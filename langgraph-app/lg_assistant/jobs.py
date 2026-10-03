"""Single-process durable task queue backed by the application's SQLite database.

Workers claim persisted inputs rather than closures. A restart never silently
replays a running external operation: only pending work is automatically run.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import threading
import time
from typing import Any, Callable
import uuid


_SCHEMA = """
CREATE TABLE IF NOT EXISTS web_tasks (
    id TEXT PRIMARY KEY, owner TEXT NOT NULL, session_id TEXT NOT NULL,
    request_id TEXT NOT NULL, status TEXT NOT NULL, payload TEXT NOT NULL,
    result TEXT, error TEXT NOT NULL DEFAULT '', progress TEXT,
    attempts INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL, updated REAL NOT NULL,
    UNIQUE(owner, request_id)
);
CREATE INDEX IF NOT EXISTS web_tasks_queue ON web_tasks(status, created);
CREATE INDEX IF NOT EXISTS web_tasks_owner ON web_tasks(owner, created DESC);
CREATE INDEX IF NOT EXISTS web_tasks_session ON web_tasks(owner, session_id, status);
"""
_PRIVATE_FIELDS = {"path", "paths", "files", "file_path", "local_path", "payload", "parameters",
                   "params", "request_key", "api_key", "access_token", "authorization"}
_ABSOLUTE_PATH = re.compile(r"(?<![\w:])(?:[A-Za-z]:[\\/]|/(?:tmp|home|Users|var|app|data|mnt)/)[^\s\"'<>，。；]+")


class TaskConflictError(RuntimeError):
    """An old request must not overwrite a more recent session checkpoint."""
    MESSAGE = "会话已有新请求，请新建会话后重新提交该任务。"

    def __init__(self):
        super().__init__(self.MESSAGE)


def public_value(value: Any, private_paths: list[str] = ()) -> Any:
    """Remove implementation paths/fields from otherwise compatible result JSON."""
    if isinstance(value, dict):
        return {k: public_value(v, private_paths) for k, v in value.items() if k.lower() not in _PRIVATE_FIELDS}
    if isinstance(value, list):
        return [public_value(v, private_paths) for v in value]
    if isinstance(value, str):
        for path in private_paths:
            if path:
                value = value.replace(path, "[上传文件]")
        return _ABSOLUTE_PATH.sub("[本地文件]", value)
    return value


class TaskManager:
    def __init__(self, db_path: Path, execute: Callable[[dict], dict], *, max_workers: int = 2,
                 is_stale: Callable[[dict], bool] | None = None):
        if not 1 <= max_workers <= 16:
            raise ValueError("Task worker count must be between 1 and 16")
        self.db_path = Path(db_path)
        self.execute = execute
        self.max_workers = max_workers
        self.is_stale = is_stale
        self._condition = threading.Condition(threading.RLock())
        self._session_locks = [threading.RLock() for _ in range(128)]
        self._workers: list[threading.Thread] = []
        self._started = False
        self._closing = False
        self._active_sync = 0
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.executescript(_SCHEMA)

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.db_path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def session_lock(self, owner: str, session_id: str):
        key = hashlib.sha256(json.dumps([owner, session_id]).encode()).digest()
        return self._session_locks[int.from_bytes(key[:4], "big") % len(self._session_locks)]

    def start(self) -> None:
        """Recover once, then start a fixed number of workers; safe to call again."""
        with self._condition:
            if self._started:
                return
            if self._closing:
                raise RuntimeError("Task manager has been shut down")
            with self._db() as db:
                db.execute("UPDATE web_tasks SET status='interrupted', error=?, updated=? WHERE status='running'",
                           ("服务重启，处理已中断。请继续原任务以恢复处理。", time.time()))
            self._started = True
            for number in range(self.max_workers):
                thread = threading.Thread(target=self._work, name=f"assistant-worker-{number}", daemon=True)
                self._workers.append(thread)
                thread.start()

    def shutdown(self, wait: bool = True) -> None:
        """Stop accepting work and drain started workers before a safe shutdown."""
        with self._condition:
            self._closing = True
            self._condition.notify_all()
        if wait:
            for worker in self._workers:
                worker.join()
            with self._condition:
                while self._active_sync:
                    self._condition.wait()

    @contextmanager
    def activity(self):
        """Include synchronous graph/model work in health and graceful draining."""
        with self._condition:
            if self._closing:
                raise RuntimeError("Task manager is shutting down")
            self._active_sync += 1
        try:
            yield
        finally:
            with self._condition:
                self._active_sync -= 1
                self._condition.notify_all()

    def submit(self, payload: dict) -> dict:
        owner, rid = payload["owner"], payload["request_id"]
        now = time.time()
        with self._condition:
            if self._closing:
                raise RuntimeError("Task manager is shutting down")
            with self._db() as db:
                db.execute("""INSERT OR IGNORE INTO web_tasks
                    (id,owner,session_id,request_id,status,payload,created,updated)
                    VALUES(?,?,?,?, 'pending', ?,?,?)""",
                           (uuid.uuid4().hex, owner, payload["session_id"], rid,
                            json.dumps(payload, ensure_ascii=False), now, now))
                row = db.execute("SELECT * FROM web_tasks WHERE owner=? AND request_id=?", (owner, rid)).fetchone()
            self._condition.notify_all()
        return self._public(row)

    def retry(self, task_id: str, owner: str = "local") -> dict | None:
        with self._condition:
            if self._closing:
                raise RuntimeError("Task manager is shutting down")
            with self._db() as db:
                db.execute("""UPDATE web_tasks SET status='pending', error='', attempts=MAX(attempts,1), updated=?
                    WHERE id=? AND owner=? AND status IN ('interrupted','error')""", (time.time(), task_id, owner))
                row = db.execute("SELECT * FROM web_tasks WHERE id=? AND owner=?", (task_id, owner)).fetchone()
            self._condition.notify_all()
        return self._public(row) if row else None

    def mark_conflict(self, task_id: str, owner: str = "local") -> None:
        """Retire an unrecoverable checkpoint barrier without replaying any work."""
        with self._db() as db:
            db.execute("""UPDATE web_tasks SET status='error', error=?, updated=?
                WHERE id=? AND owner=? AND status IN ('interrupted','error')""",
                       (TaskConflictError.MESSAGE, time.time(), task_id, owner))
        with self._condition:
            self._condition.notify_all()

    def get(self, task_id: str, owner: str = "local") -> dict | None:
        with self._db() as db:
            row = db.execute("SELECT * FROM web_tasks WHERE id=? AND owner=?", (task_id, owner)).fetchone()
        return self._public(row) if row else None

    def get_by_request(self, owner: str, request_id: str) -> dict | None:
        with self._db() as db:
            row = db.execute("SELECT * FROM web_tasks WHERE owner=? AND request_id=?", (owner, request_id)).fetchone()
        return self._public(row) if row else None

    def get_payload(self, task_id: str, owner: str = "local") -> dict | None:
        """Private server-side input access for authorized media delivery only."""
        with self._db() as db:
            row = db.execute("SELECT payload FROM web_tasks WHERE id=? AND owner=?", (task_id, owner)).fetchone()
        return json.loads(row["payload"]) if row else None

    def list(self, owner: str = "local", limit: int = 50) -> list[dict]:
        with self._db() as db:
            rows = db.execute("SELECT * FROM web_tasks WHERE owner=? ORDER BY created DESC LIMIT ?",
                              (owner, min(200, max(1, limit)))).fetchall()
        return [self._public(row) for row in rows]

    def active_count(self) -> int:
        with self._db() as db:
            queued = db.execute("SELECT COUNT(*) FROM web_tasks WHERE status IN ('pending','running')").fetchone()[0]
        with self._condition:
            return queued + self._active_sync

    def save_progress(self, owner: str, request_id: str, value: dict | None) -> None:
        with self._db() as db:
            db.execute("UPDATE web_tasks SET progress=?, updated=? WHERE owner=? AND request_id=?",
                       (json.dumps(value, ensure_ascii=False), time.time(), owner, request_id))

    @staticmethod
    def _public(row) -> dict:
        payload = json.loads(row["payload"])
        return {"id": row["id"], "status": row["status"], "request_id": row["request_id"],
                "scene": payload.get("scene") or "",
                "session_id": row["session_id"], "created": row["created"], "updated": row["updated"],
                "result": public_value(json.loads(row["result"]) if row["result"] else None, payload.get("files", [])),
                "error": row["error"], "progress": json.loads(row["progress"]) if row["progress"] else None}

    def _claim(self):
        # Do not spend an entire worker waiting on one session when another can run.
        with self._db() as db:
            candidates = db.execute("""SELECT * FROM web_tasks t WHERE status='pending' AND NOT EXISTS
                (SELECT 1 FROM web_tasks r WHERE r.owner=t.owner AND r.session_id=t.session_id AND r.status='running')
                ORDER BY t.rowid""").fetchall()
        for row in candidates:
            lock = self.session_lock(row["owner"], row["session_id"])
            if not lock.acquire(blocking=False):
                continue
            claimed = False
            try:
                with self._db() as db:
                    interrupted = db.execute("""SELECT * FROM web_tasks WHERE owner=? AND session_id=?
                        AND status='interrupted' AND rowid<(SELECT rowid FROM web_tasks WHERE id=?)
                        ORDER BY rowid""", (row["owner"], row["session_id"], row["id"])).fetchall()
                blocked = False
                for predecessor in interrupted:
                    # A queued successor cannot overwrite an unfinished checkpoint.
                    # Only a read-only checkpoint comparison may retire a stale barrier.
                    try:
                        stale = self.is_stale and self.is_stale(json.loads(predecessor["payload"]))
                    except Exception:
                        stale = False  # Unable to inspect the checkpoint: preserve it.
                    if stale:
                        self.mark_conflict(predecessor["id"], predecessor["owner"])
                    else:
                        blocked = True
                        break
                if blocked:
                    continue
                with self._db() as db:
                    claimed = bool(db.execute("""UPDATE web_tasks SET status='running', attempts=attempts+1, updated=?
                        WHERE id=? AND status='pending'""", (time.time(), row["id"])).rowcount)
                if claimed:
                    return row, lock
            finally:
                if not claimed:
                    lock.release()
        return None

    def _has_running_work(self) -> bool:
        with self._db() as db:
            running = db.execute("SELECT 1 FROM web_tasks WHERE status='running' LIMIT 1").fetchone()
        return bool(running or self._active_sync)

    def _work(self) -> None:
        while True:
            with self._condition:
                claimed = self._claim()
                if not claimed:
                    # Interrupted predecessors need human retry. Preserve their
                    # pending successors on disk instead of deadlocking shutdown.
                    if self._closing and not self._has_running_work():
                        return
                    self._condition.wait(.2)
                    continue
            row, session_lock = claimed
            try:
                payload = json.loads(row["payload"])
                if row["attempts"]:
                    payload["_retry"] = True
                result = self.execute(payload)
                status = "interrupted" if result.get("status") == "pending" else \
                    "error" if result.get("status") in {"error", "failed"} else "done"
                error = "外部任务尚未完成，请继续原任务获取结果。" if status == "interrupted" else \
                    "任务处理失败，可继续原任务重试。" if status == "error" else ""
                with self._db() as db:
                    db.execute("UPDATE web_tasks SET status=?, result=?, error=?, updated=? WHERE id=?",
                               (status, json.dumps(result, ensure_ascii=False), error, time.time(), row["id"]))
            except Exception as exc:
                # Never disclose provider credentials, input text or local paths in exceptions.
                status = "error"
                error = TaskConflictError.MESSAGE if isinstance(exc, TaskConflictError) else \
                    f"{type(exc).__name__}：任务处理失败，可继续原任务重试。"
                with self._db() as db:
                    db.execute("UPDATE web_tasks SET status=?, error=?, updated=? WHERE id=?",
                               (status, error, time.time(), row["id"]))
            finally:
                session_lock.release()
                with self._condition:
                    self._condition.notify_all()
