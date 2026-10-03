"""Durable ASR jobs. A submission with unknown outcome is never auto-resubmitted."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from . import config, llm, transcribe
from .tools import audio

_locks = [threading.RLock() for _ in range(64)]


@contextmanager
def connection():
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(config.DB_PATH, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("""CREATE TABLE IF NOT EXISTS meeting_jobs(
      id TEXT PRIMARY KEY, owner TEXT NOT NULL, request_id TEXT NOT NULL, path TEXT NOT NULL,
      status TEXT NOT NULL, task_id TEXT NOT NULL DEFAULT '', result TEXT NOT NULL DEFAULT '{}',
      error TEXT NOT NULL DEFAULT '', updated REAL NOT NULL)""")
    db.commit()
    try:
        with db:
            yield db
    finally:
        db.close()


def get(key):
    with connection() as db:
        row = dict(db.execute("SELECT * FROM meeting_jobs WHERE id=?", (key,)).fetchone())
    row["result"] = json.loads(row["result"])
    return row


def update(key, **fields):
    if not set(fields) <= {"status", "task_id", "result", "error"}:
        raise ValueError("Invalid ASR job update")
    fields["updated"] = time.time()
    if "result" in fields:
        fields["result"] = json.dumps(fields["result"], ensure_ascii=False)
    with connection() as db:
        db.execute("UPDATE meeting_jobs SET " + ",".join(k + "=?" for k in fields) + " WHERE id=?", [*fields.values(), key])


def prepare(owner, request_id, path, vocabulary_id=""):
    digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    settings = [transcribe.MODEL_DIAR, config.MODEL_ASR, vocabulary_id, config.ASR_CHUNK_SECONDS]
    key = hashlib.sha256(json.dumps([owner, request_id, digest, settings]).encode()).hexdigest()
    with connection() as db:
        db.execute("INSERT OR IGNORE INTO meeting_jobs(id,owner,request_id,path,status,updated) VALUES(?,?,?,?,?,?)",
                   (key, owner, request_id, str(Path(path).resolve()), "prepared", time.time()))
    return key


def submit_job(key, vocabulary_id=""):
    # A striped mutex also makes simultaneous requests in this worker share the result.
    with _locks[int(key[:8], 16) % len(_locks)]:
        row = get(key)
        if row["status"] != "prepared":
            return row
        try:
            normalized = audio.normalize_browser_recording(row["path"])
            url = transcribe.upload_for_temp_url(normalized)
        except Exception as exc:
            update(key, status="fallback", error=f"上传或转换失败：{exc}")
            return get(key)
        # Claim before the external side effect, including across processes.
        with connection() as db:
            claimed = db.execute("UPDATE meeting_jobs SET status='submitting',updated=? WHERE id=? AND status='prepared'", (time.time(), key)).rowcount
        if not claimed:
            return get(key)
        try:
            task = transcribe.submit(url, vocabulary_id=vocabulary_id or None)
        except Exception as exc:
            update(key, status="uncertain", error=f"提交结果不确定，未自动重新提交：{exc}")
        else:
            update(key, status="submitted", task_id=task)
        return get(key)


def collect_job(key):
    with _locks[int(key[:8], 16) % len(_locks)]:
        row = get(key)
        if row["status"] in ("submitting", "fallback_running"):
            return {**row, "error": "外部处理尚在进行或完成状态不确定；保留任务，不自动重复提交。"}
        if row["status"] == "submitted":
            try:
                result = transcribe.parse_result(transcribe.fetch_result(row["task_id"]))
                if not result.get("utterances"):
                    raise ValueError("未识别到语音内容")
                update(key, status="done", result=result, error="")
            except Exception as exc:
                detail = str(exc)
                # Poll/network errors leave the same task available for the next request.
                terminal = "任务失败" in detail or "未识别到语音" in detail
                update(key, status="fallback" if terminal else "submitted", error=detail)
            row = get(key)
        if row["status"] == "fallback":
            with connection() as db:
                claimed = db.execute("UPDATE meeting_jobs SET status='fallback_running',updated=? WHERE id=? AND status='fallback'", (time.time(), key)).rowcount
            if not claimed:
                return get(key)
            chunks = []
            original = Path(row["path"])
            try:
                chunks, _ = audio.split(str(original))
                parts = [llm.asr(p) for p in chunks]
                text = "\n".join(p for p in parts if p.strip())
                if not text:
                    raise ValueError("未识别到语音内容")
                update(key, status="done", result={"text": text, "utterances": [], "diarized": False}, error=row["error"])
            except Exception as exc:
                update(key, status="failed", error=str(exc))
            finally:
                audio.cleanup(chunks, original)
        return get(key)
