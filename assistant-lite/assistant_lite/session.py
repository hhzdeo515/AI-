"""会话状态与去重存储：SQLite + WAL，按 owner 隔离。

移植自旧 service.py 的 load_state / save_state / receipt / remember，
但去掉了 HTTP 包装，改为进程内直接调用，并加了 WAL 以支持并发。
"""

from __future__ import annotations

import json
import sqlite3
import threading
from typing import Any

from . import config

_LOCK = threading.RLock()
_local = threading.local()
_initialised = False

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions(
    owner   TEXT NOT NULL,
    id      TEXT NOT NULL,
    state   TEXT NOT NULL,
    updated TEXT NOT NULL,
    PRIMARY KEY(owner, id)
);
CREATE TABLE IF NOT EXISTS receipts(
    owner      TEXT NOT NULL,
    request_id TEXT NOT NULL,
    stage      TEXT NOT NULL,
    response   TEXT NOT NULL,
    PRIMARY KEY(owner, request_id, stage)
);
CREATE TABLE IF NOT EXISTS resources(
    id       TEXT PRIMARY KEY,
    owner    TEXT NOT NULL,
    scene    TEXT NOT NULL,
    title    TEXT NOT NULL,
    content  TEXT NOT NULL,
    source   TEXT,
    files    TEXT,
    created  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_resources_owner ON resources(owner, created DESC);
"""


def _connect() -> sqlite3.Connection:
    """每线程一个连接（Flask 开发服务器是多线程的）。"""
    conn: sqlite3.Connection | None = getattr(_local, "conn", None)
    if conn is None:
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(config.DB_PATH, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        _local.conn = conn
    return conn


def init() -> None:
    """建表。幂等，可重复调用。"""
    global _initialised
    with _LOCK:
        if _initialised:
            return
        conn = _connect()
        conn.executescript(_SCHEMA)
        conn.commit()
        _initialised = True


# --------------------------------------------------------------------------- #
# 会话状态
# --------------------------------------------------------------------------- #
def default_state() -> dict[str, Any]:
    return {
        "active_scene": "general",
        "meeting": {"id": "", "status": "idle", "transcript": ""},
        "fitness": {"awaiting": "", "draft": {}},
        #: 播报代际号。设备端播放前比对：代际变了说明这条音频已作废，直接丢弃。
        #: 这是"播报打断"的服务端一半——设备端只需实现"比对并停播"。
        "playback": {"generation": 0},
        "last_question": "",
        "last_answer": "",
    }


def load_state(owner: str, session_id: str) -> dict[str, Any]:
    """读会话状态；不存在时返回默认状态（不写库）。"""
    init()
    with _LOCK:
        row = _connect().execute(
            "SELECT state FROM sessions WHERE owner=? AND id=?", (owner, session_id)
        ).fetchone()
    if not row:
        return default_state()
    try:
        data = json.loads(row["state"])
    except json.JSONDecodeError:
        return default_state()
    if not isinstance(data, dict):
        return default_state()
    # 补齐缺失键，兼容后续版本新增字段
    base = default_state()
    base.update(data)
    return base


def save_state(owner: str, session_id: str, state: dict[str, Any]) -> None:
    init()
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    with _LOCK:
        conn = _connect()
        conn.execute(
            "INSERT OR REPLACE INTO sessions(owner, id, state, updated) VALUES(?,?,?,?)",
            (owner, session_id, json.dumps(state, ensure_ascii=False), now),
        )
        conn.commit()


# --------------------------------------------------------------------------- #
# 请求级去重（幂等）
# --------------------------------------------------------------------------- #
def receipt(owner: str, request_id: str, stage: str) -> dict[str, Any] | None:
    """取已完成的同 request_id + stage 的结果；没有则 None。"""
    init()
    with _LOCK:
        row = _connect().execute(
            "SELECT response FROM receipts WHERE owner=? AND request_id=? AND stage=?",
            (owner, request_id, stage),
        ).fetchone()
    if not row:
        return None
    try:
        return json.loads(row["response"])
    except json.JSONDecodeError:
        return None


def remember(
    owner: str, request_id: str, stage: str, result: dict[str, Any]
) -> dict[str, Any]:
    """记录某 request_id + stage 的结果，供重复请求直接返回。"""
    init()
    with _LOCK:
        conn = _connect()
        conn.execute(
            "INSERT OR REPLACE INTO receipts(owner, request_id, stage, response) VALUES(?,?,?,?)",
            (owner, request_id, stage, json.dumps(result, ensure_ascii=False)),
        )
        conn.commit()
    return result


# --------------------------------------------------------------------------- #
# 资料库
# --------------------------------------------------------------------------- #
def archive(
    owner: str,
    scene: str,
    title: str,
    content: str,
    source: str = "",
    files: list[str] | None = None,
) -> str:
    """归档一份资料，返回资料 ID。"""
    import uuid
    from datetime import datetime, timezone

    init()
    rid = uuid.uuid4().hex
    created = datetime.now(timezone.utc).isoformat()
    with _LOCK:
        conn = _connect()
        conn.execute(
            "INSERT INTO resources(id, owner, scene, title, content, source, files, created)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (
                rid,
                owner,
                scene,
                title,
                content,
                source,
                json.dumps(files or [], ensure_ascii=False),
                created,
            ),
        )
        conn.commit()
    return rid


def list_resources(owner: str, scene: str | None = None, limit: int = 20) -> list[dict]:
    init()
    sql = "SELECT id, scene, title, created, length(content) AS size FROM resources WHERE owner=?"
    args: list[Any] = [owner]
    if scene:
        sql += " AND scene=?"
        args.append(scene)
    sql += " ORDER BY created DESC LIMIT ?"
    args.append(limit)
    with _LOCK:
        rows = _connect().execute(sql, args).fetchall()
    return [dict(r) for r in rows]


def list_full(owner: str, scene: str | None = None, limit: int = 20) -> list[dict]:
    """取完整资料（含 content），用于导出。"""
    init()
    sql = "SELECT * FROM resources WHERE owner=?"
    args: list[Any] = [owner]
    if scene:
        sql += " AND scene=?"
        args.append(scene)
    sql += " ORDER BY created DESC LIMIT ?"
    args.append(limit)
    with _LOCK:
        rows = _connect().execute(sql, args).fetchall()
    return [dict(r) for r in rows]


def get_resource(owner: str, rid: str) -> dict | None:
    init()
    with _LOCK:
        row = _connect().execute(
            "SELECT * FROM resources WHERE owner=? AND id=?", (owner, rid)
        ).fetchone()
    return dict(row) if row else None
