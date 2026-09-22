"""后台任务：把长耗时请求从「同步阻塞」改成「提交 → 轮询」。

为什么需要：一次拍题要 ~27 秒（三次视觉调用）。
网页上用户能等，戴在脸上 27 秒没有任何反馈是不能接受的。
设备端应该「提交后立刻拿到 task_id，处理完再取结果」。

实现取舍：**进程内线程池 + 内存登记表**。
够单机本地用，不引入 Celery / Redis —— 那与「轻量」的目标相悖。
如果将来要跨进程或重启不丢，再换持久化队列。
"""

from __future__ import annotations

import threading
import time
import uuid
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

_LOCK = threading.RLock()
_MAX_RECORDS = 200
_TTL_SECONDS = 3600

_RECORDS: "OrderedDict[str, dict[str, Any]]" = OrderedDict()
_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="alite-task")


def submit(fn: Callable[[], Any]) -> str:
    """把 fn 丢进线程池，立刻返回 task_id。"""
    tid = uuid.uuid4().hex[:12]
    with _LOCK:
        _RECORDS[tid] = {
            "id": tid,
            "status": "pending",  # pending | running | done | error
            "created": time.time(),
            "result": None,
            "error": "",
        }
        _evict()

    def run() -> None:
        with _LOCK:
            rec = _RECORDS.get(tid)
            if rec is None:
                return
            rec["status"] = "running"
        try:
            value = fn()
        except Exception as e:  # 任务内部异常要落到记录里，不能凭空消失
            with _LOCK:
                rec = _RECORDS.get(tid)
                if rec is not None:
                    rec["status"] = "error"
                    rec["error"] = f"{type(e).__name__}: {e}"
                    rec["done_at"] = time.time()
            return
        with _LOCK:
            rec = _RECORDS.get(tid)
            if rec is not None:
                rec["status"] = "done"
                rec["result"] = value
                rec["done_at"] = time.time()

    _POOL.submit(run)
    return tid


def get(task_id: str) -> dict[str, Any] | None:
    if not task_id:
        return None
    with _LOCK:
        rec = _RECORDS.get(task_id)
        return dict(rec) if rec else None


def _evict() -> None:
    """按数量上限与 TTL 清理。调用方需持有 _LOCK。"""
    now = time.time()
    expired = [
        tid
        for tid, r in _RECORDS.items()
        if r["status"] in ("done", "error")
        and now - r.get("done_at", now) > _TTL_SECONDS
    ]
    for tid in expired:
        _RECORDS.pop(tid, None)
    while len(_RECORDS) > _MAX_RECORDS:
        _RECORDS.popitem(last=False)


def stats() -> dict[str, int]:
    """给健康检查用。"""
    with _LOCK:
        counts = {"pending": 0, "running": 0, "done": 0, "error": 0}
        for r in _RECORDS.values():
            counts[r["status"]] = counts.get(r["status"], 0) + 1
        return counts


def clear() -> None:
    """仅供测试。"""
    with _LOCK:
        _RECORDS.clear()
