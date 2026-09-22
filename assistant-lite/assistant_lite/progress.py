"""请求级进度登记。

用途：让前端的 pipeline 显示**真实的执行阶段**，而不是假动画。
Agent 在关键步骤调用 `report(request_id, step)`，Web 层用 `/api/progress` 轮询。

设计取舍：
- 只保留最近 N 条，避免内存无限增长
- 没有登记时返回 None，前端据此回退到"整体处理中"而不是编造阶段
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Any

_LOCK = threading.RLock()
_MAX_RECORDS = 200
_RECORDS: "OrderedDict[str, dict[str, Any]]" = OrderedDict()

#: 各场景的 pipeline 定义：步骤 id -> 展示文案（顺序即展示顺序）
PIPELINES: dict[str, list[tuple[str, str]]] = {
    "meeting": [
        ("captured", "Audio captured"),
        ("transcript", "Transcript ready"),
        ("structuring", "Structuring meeting"),
        ("summary", "Summary ready"),
    ],
    "exam": [
        ("capture", "Capture"),
        ("recognize", "Recognize"),
        ("solve", "Solve"),
        ("verify", "Verify"),
    ],
}


def begin(request_id: str, scene: str) -> None:
    """开始记录一次请求的进度。"""
    if not request_id:
        return
    with _LOCK:
        _RECORDS[request_id] = {
            "scene": scene,
            "step": "",
            "finished": False,
            "error": False,
            "at": time.time(),
        }
        _RECORDS.move_to_end(request_id)
        while len(_RECORDS) > _MAX_RECORDS:
            _RECORDS.popitem(last=False)


def report(request_id: str, step: str) -> None:
    """登记"正在执行 step"。未登记的 request_id 静默忽略。"""
    if not request_id:
        return
    with _LOCK:
        rec = _RECORDS.get(request_id)
        if rec is None or rec["finished"]:
            return
        rec["step"] = step
        rec["at"] = time.time()


def finish(request_id: str, error: bool = False) -> None:
    if not request_id:
        return
    with _LOCK:
        rec = _RECORDS.get(request_id)
        if rec is None:
            return
        rec["finished"] = True
        rec["error"] = error
        rec["at"] = time.time()


def snapshot(request_id: str) -> dict[str, Any] | None:
    """返回该请求的进度快照；没有记录则 None。"""
    if not request_id:
        return None
    with _LOCK:
        rec = _RECORDS.get(request_id)
        if rec is None:
            return None
        rec = dict(rec)

    pipeline = PIPELINES.get(rec["scene"], [])
    if not pipeline:
        return None

    order = [k for k, _ in pipeline]
    current = rec["step"]
    idx = order.index(current) if current in order else -1

    steps = []
    for i, (sid, label) in enumerate(pipeline):
        if rec["finished"]:
            if i < idx or (i == idx and not rec["error"]):
                state = "done"
            elif i == idx and rec["error"]:
                state = "error"
            else:
                state = "pending"
        elif i < idx:
            state = "done"
        elif i == idx:
            state = "active"
        else:
            state = "pending"
        steps.append({"id": sid, "label": label, "state": state})

    return {
        "scene": rec["scene"],
        "steps": steps,
        "finished": rec["finished"],
        "error": rec["error"],
    }


def clear() -> None:
    """仅供测试使用。"""
    with _LOCK:
        _RECORDS.clear()
