"""SQLite 存储：路由遥测 + 资料归档 + 请求幂等去重。

移植自 `assistant-lite/assistant_lite/session.py`，只保留图需要的那部分：

- ``routing_telemetry``  端侧意图 vs 云端权威路由 → **端侧判对率**（设计文档 §8）
- ``resources``          场景产出归档
- ``receipts``           同 ``request_id`` 幂等去重 → 断连补传不产生孤儿归档

**为什么这些和 LangGraph 不冲突**：LangGraph 的 checkpointer 存的是「图的状态」，
这里存的是「业务事实」。断连补传要的是后者——设备重放同一 request_id 时，
图可以重新跑，但归档绝不能重复。
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from typing import Any

from . import config

_LOCK = threading.RLock()
_local = threading.local()
_initialised = False

_SCHEMA = """
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
    created  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_resources_owner ON resources(owner, created DESC);
CREATE TABLE IF NOT EXISTS routing_telemetry(
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    owner             TEXT NOT NULL,
    session_id        TEXT NOT NULL,
    thread_id         TEXT,
    request_id        TEXT,
    device_intent     TEXT,
    device_confidence REAL,
    device_scene      TEXT,
    device_trusted    TEXT,
    scene             TEXT NOT NULL,
    action            TEXT,
    source            TEXT NOT NULL,
    device_correct    TEXT,
    rerouted          TEXT,
    created           TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_routing_owner ON routing_telemetry(owner, created DESC);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect() -> sqlite3.Connection:
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
    """建表。幂等。"""
    global _initialised
    with _LOCK:
        if _initialised:
            return
        conn = _connect()
        conn.executescript(_SCHEMA)
        conn.commit()
        _initialised = True


# --------------------------------------------------------------------------- #
# 幂等去重（断连补传的地基）
# --------------------------------------------------------------------------- #
def receipt(owner: str, request_id: str, stage: str) -> dict[str, Any] | None:
    if not request_id:
        return None
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


def remember(owner: str, request_id: str, stage: str, payload: dict[str, Any]) -> None:
    if not request_id:
        return
    init()
    with _LOCK:
        conn = _connect()
        conn.execute(
            "INSERT OR REPLACE INTO receipts(owner, request_id, stage, response) VALUES(?,?,?,?)",
            (owner, request_id, stage, json.dumps(payload, ensure_ascii=False)),
        )
        conn.commit()


# --------------------------------------------------------------------------- #
# 资料归档
# --------------------------------------------------------------------------- #
def archive(owner: str, scene: str, title: str, content: str, source: str = "") -> str:
    init()
    rid = uuid.uuid4().hex
    with _LOCK:
        conn = _connect()
        conn.execute(
            "INSERT INTO resources(id, owner, scene, title, content, source, created)"
            " VALUES(?,?,?,?,?,?,?)",
            (rid, owner, scene, title, content, source, _now()),
        )
        conn.commit()
    return rid


def list_full(owner: str, limit: int = 20) -> list[dict]:
    """取完整资料（含 content），用于导出。"""
    init()
    with _LOCK:
        rows = _connect().execute(
            "SELECT * FROM resources WHERE owner=? ORDER BY created DESC LIMIT ?",
            (owner, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def get_resource(owner: str, rid: str) -> dict | None:
    init()
    with _LOCK:
        row = _connect().execute(
            "SELECT * FROM resources WHERE owner=? AND id=?", (owner, rid)
        ).fetchone()
    return dict(row) if row else None


def list_resources(owner: str, limit: int = 20) -> list[dict]:
    init()
    with _LOCK:
        rows = _connect().execute(
            "SELECT id, scene, title, created, length(content) AS size FROM resources"
            " WHERE owner=? ORDER BY created DESC LIMIT ?",
            (owner, limit),
        ).fetchall()
    return [dict(r) for r in rows]


# --------------------------------------------------------------------------- #
# 路由遥测（端侧判对率）
# --------------------------------------------------------------------------- #
def record_routing(
    *,
    owner: str,
    session_id: str,
    thread_id: str,
    request_id: str,
    scene: str,
    action: str,
    source: str,
    device_intent: str = "",
    device_confidence: float | None = None,
    device_scene: str = "",
    device_trusted: bool | None = None,
) -> None:
    """记录一次路由判定。

    ``device_correct`` / ``rerouted`` 只在端侧给出了场景映射时才计算；
    端侧没给意图（例如纯文字网页输入）时留空，不污染统计分母。
    """
    if device_scene:
        correct = "yes" if device_scene == scene else "no"
        rerouted = "no" if device_scene == scene else "yes"
    else:
        correct = ""
        rerouted = ""

    init()
    with _LOCK:
        conn = _connect()
        conn.execute(
            "INSERT INTO routing_telemetry("
            "owner, session_id, thread_id, request_id, device_intent, device_confidence,"
            " device_scene, device_trusted, scene, action, source,"
            " device_correct, rerouted, created"
            ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                owner, session_id, thread_id, request_id, device_intent, device_confidence,
                device_scene,
                "" if device_trusted is None else ("yes" if device_trusted else "no"),
                scene, action, source, correct, rerouted, _now(),
            ),
        )
        conn.commit()


def routing_stats(owner: str | None = None) -> dict[str, Any]:
    """端侧判对率汇总。样本 = 端侧给出了场景映射的记录。"""
    init()
    where = "WHERE device_scene != ''"
    args: list[Any] = []
    if owner:
        where += " AND owner=?"
        args.append(owner)

    with _LOCK:
        conn = _connect()
        total = conn.execute(
            f"SELECT COUNT(*) AS n FROM routing_telemetry {where}", args
        ).fetchone()["n"]
        correct = conn.execute(
            f"SELECT COUNT(*) AS n FROM routing_telemetry {where} AND device_correct='yes'",
            args,
        ).fetchone()["n"]
        by_source = conn.execute(
            f"SELECT source, COUNT(*) AS n FROM routing_telemetry {where}"
            " GROUP BY source ORDER BY n DESC",
            args,
        ).fetchall()

    return {
        "samples": total,
        "device_correct": correct,
        "accuracy": round(correct / total, 4) if total else None,
        "by_source": {r["source"]: r["n"] for r in by_source},
    }
