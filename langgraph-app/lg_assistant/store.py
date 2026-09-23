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
CREATE TABLE IF NOT EXISTS transcripts(
    id          TEXT PRIMARY KEY,
    owner       TEXT NOT NULL,
    scene       TEXT NOT NULL,
    summary     TEXT NOT NULL DEFAULT '',
    names       TEXT NOT NULL DEFAULT '{}',
    utterances  TEXT NOT NULL,
    original    TEXT NOT NULL DEFAULT '[]',
    next_speaker INTEGER NOT NULL DEFAULT 0,
    created     TEXT NOT NULL,
    updated     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_transcripts_owner ON transcripts(owner, updated DESC);
CREATE TABLE IF NOT EXISTS profiles(
    owner      TEXT PRIMARY KEY,
    data       TEXT NOT NULL,
    source     TEXT NOT NULL DEFAULT '',
    created    TEXT NOT NULL,
    updated    TEXT NOT NULL
);
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


def update_resource(owner: str, rid: str, content: str) -> bool:
    """更新归档正文（人工改判转写后，纪要+正文要跟着变）。"""
    init()
    with _LOCK:
        conn = _connect()
        cur = conn.execute(
            "UPDATE resources SET content=? WHERE owner=? AND id=?", (content, owner, rid)
        )
        conn.commit()
    return cur.rowcount > 0


# --------------------------------------------------------------------------- #
# 转写记录：人工改判说话人的落点
#
# 为什么单独一张表而不是塞进 resources.content：改判要反复读写「逐段结构」，
# 正文只是它的渲染结果。id 就用归档 id——会议产出与转写记录是同一个东西的两面。
# --------------------------------------------------------------------------- #
def save_transcript(
    owner: str, rid: str, *, scene: str, record: dict[str, Any]
) -> None:
    init()
    now = _now()
    with _LOCK:
        conn = _connect()
        conn.execute(
            "INSERT INTO transcripts(id, owner, scene, summary, names, utterances,"
            " original, next_speaker, created, updated)"
            " VALUES(?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(id) DO UPDATE SET summary=excluded.summary, names=excluded.names,"
            " utterances=excluded.utterances, original=excluded.original,"
            " next_speaker=excluded.next_speaker, updated=excluded.updated",
            (
                rid,
                owner,
                scene,
                str(record.get("summary") or ""),
                json.dumps(record.get("names") or {}, ensure_ascii=False),
                json.dumps(record.get("utterances") or [], ensure_ascii=False),
                json.dumps(record.get("original") or [], ensure_ascii=False),
                int(record.get("next_speaker") or 0),
                now,
                now,
            ),
        )
        conn.commit()


def get_transcript(owner: str, rid: str) -> dict[str, Any] | None:
    init()
    with _LOCK:
        row = _connect().execute(
            "SELECT * FROM transcripts WHERE owner=? AND id=?", (owner, rid)
        ).fetchone()
    if not row:
        return None
    rec = dict(row)
    for field, fallback in (("names", {}), ("utterances", []), ("original", [])):
        try:
            rec[field] = json.loads(rec[field])
        except (TypeError, json.JSONDecodeError):
            rec[field] = fallback
    return {
        "id": rec["id"],
        "scene": rec["scene"],
        "summary": rec["summary"],
        "names": rec["names"],
        "utterances": rec["utterances"],
        "original": rec["original"],
        "next_speaker": rec["next_speaker"],
        "updated": rec["updated"],
    }


# --------------------------------------------------------------------------- #
# 健康档案（按 owner 一份，手机端写入、眼镜端只读）
#
# 为什么与资料库同库而不是沿用基线的 `data/profiles/<owner>.json`：
# 线上是单库 SQLite，档案和资料一起备份、一起按 owner 隔离；文件方式还要
# 自己处理 owner 名清洗和多进程并发写。
#
# ``source`` 记这份档案是哪来的（``phone`` / ``glasses``），排查「为什么
# 眼镜读到的档案和我手机上填的不一样」时，第一眼要看的就是它。
# --------------------------------------------------------------------------- #
def save_profile(owner: str, data: dict[str, Any], source: str = "") -> None:
    """覆盖式写入健康档案。``created`` 只在首次写入时设置。"""
    init()
    now = _now()
    payload = json.dumps(data or {}, ensure_ascii=False)
    with _LOCK:
        conn = _connect()
        row = conn.execute("SELECT created FROM profiles WHERE owner=?", (owner,)).fetchone()
        created = row["created"] if row else now
        conn.execute(
            "INSERT INTO profiles(owner, data, source, created, updated) VALUES(?,?,?,?,?) "
            "ON CONFLICT(owner) DO UPDATE SET data=excluded.data, source=excluded.source, "
            "updated=excluded.updated",
            (owner, payload, source, created, now),
        )
        conn.commit()


def load_profile(owner: str) -> dict[str, Any]:
    """读健康档案。没有或损坏都返回 ``{}`` —— **不抛异常**。

    档案是可选输入：读不到就该按「尚未建档」继续，而不是让整条链路失败。
    """
    init()
    with _LOCK:
        row = _connect().execute(
            "SELECT * FROM profiles WHERE owner=?", (owner,)
        ).fetchone()
    if not row:
        return {}
    try:
        data = json.loads(row["data"])
    except (TypeError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def profile_meta(owner: str) -> dict[str, Any]:
    """档案的元信息（来源与时间）。给界面显示「手机上什么时候填的」。"""
    init()
    with _LOCK:
        row = _connect().execute(
            "SELECT source, created, updated FROM profiles WHERE owner=?", (owner,)
        ).fetchone()
    return dict(row) if row else {}


def delete_profile(owner: str) -> bool:
    init()
    with _LOCK:
        conn = _connect()
        cur = conn.execute("DELETE FROM profiles WHERE owner=?", (owner,))
        conn.commit()
    return cur.rowcount > 0


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
