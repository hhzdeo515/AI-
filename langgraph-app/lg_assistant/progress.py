"""请求级进度：Web 层轮询用。

**为什么单独一个模块**：链路（``vision.py``）是在**执行线程**里知道自己走到哪一步的，
而 ``GET /api/progress`` 是**另一个线程**在轮询。两边靠 ``request_id`` 对上：

    Web 层  _run_chat()  →  bind(request_id)  把 rid 绑到执行线程上
    链路    vision.solve() →  mark("solve")   从线程上取回 rid，写进共享记录
    Web 层  /api/progress →  snapshot(rid)    另一个线程读同一条记录

这样链路不必把 request_id 一路透传下去，``mark()`` 在没有绑定时是**空操作**——
单测直接调 ``vision.observe/solve/...`` 不会因为缺少请求上下文而报错。

步骤只由**真正执行的那一步**上报，不按时间猜：没上报的步骤就停在 pending。
"""

from __future__ import annotations

import threading
import time
from typing import Any

#: 保留多少条记录（超出按插入顺序淘汰，与 Web 层的 LRU 口径一致）
MAX_RECORDS = 200

#: 各场景的步骤链：``(步骤 id, 显示名)``。Web 层只认这张表，不自己编步骤。
#:
#: 只登记**真有上报者**的场景。会议链的进度由 nodes.meeting_audio 内部掌握，
#: 目前没有上报点，就不放进来——宁可前端只显示「正在处理」，
#: 也不要一条永远停在第一步的假进度条。
PIPELINES: dict[str, list[tuple[str, str]]] = {
    "exam": [
        ("capture", "Capture"),
        ("recognize", "Recognize"),
        ("solve", "Solve"),
        ("verify", "Verify"),
    ],
}

_lock = threading.RLock()
_records: dict[str, dict[str, Any]] = {}
_current = threading.local()


# --------------------------------------------------------------------------- #
# 线程绑定：链路侧用
# --------------------------------------------------------------------------- #
def bind(request_id: str) -> None:
    """把 request_id 绑到当前线程；之后的 ``mark()`` 都写这条记录。"""
    _current.request_id = request_id or ""


def unbind() -> None:
    _current.request_id = ""


def current() -> str:
    return getattr(_current, "request_id", "") or ""


# --------------------------------------------------------------------------- #
# 记录：Web 层用
# --------------------------------------------------------------------------- #
def begin(request_id: str, scene: str = "", step: str = "") -> None:
    """开始记录。``scene`` 为空表示这次请求没有步骤链（前端只显示等待态）。

    ``step`` 允许调用方声明「已经有哪一步完成了」——例如图片在上传阶段就落盘，
    所以解题请求一开始 ``capture`` 就是 done，不必等模型。
    """
    if not request_id:
        return
    now = time.monotonic()
    with _lock:
        _records[request_id] = {
            "scene": scene or "",
            "step": step or "",
            "finished": False,
            "error": False,
            "batch": None,
            # 计时：每步只记「什么时候开始的」，时长在读取时相减得到。
            # 记录耗时是为了**能回答「时间花在哪一步」**——解题链总时长
            # 主要由模型输出长度决定，没有分解就只能靠猜（实测踩过：
            # 精读一步 104 秒，而当时的判断是「网络慢」）。
            "t0": now,
            "t1": None,
            "marks": [(step, now)] if step else [],
        }
        while len(_records) > MAX_RECORDS:
            _records.pop(next(iter(_records)))


def mark(step: str, request_id: str = "") -> None:
    """上报「现在开始做哪一步」。没有绑定也没有显式 rid 时是空操作。"""
    rid = request_id or current()
    if not rid or not step:
        return
    with _lock:
        rec = _records.get(rid)
        if rec and not rec["finished"]:
            rec["step"] = step
            rec["marks"].append((step, time.monotonic()))


def batch(total: int, done: int, request_id: str = "") -> None:
    """上报本批次实际处理完成的题数；不会修改步骤或推算完成数。

    无绑定、请求尚未开始或已经结束时为空操作。显式 request_id 可供
    其他线程报告同一批次，默认只使用当前执行线程绑定的请求。
    """
    if type(total) is not int or type(done) is not int or not 0 <= done <= total:
        raise ValueError("批次进度必须满足整数 0 <= done <= total")
    rid = request_id or current()
    if not rid:
        return
    with _lock:
        rec = _records.get(rid)
        if rec and not rec["finished"]:
            rec["batch"] = {"total": total, "done": done}


def finish(request_id: str, error: bool = False) -> None:
    """结束请求并冻结计时；批次 done 保留最后实际报告的完成数。"""
    with _lock:
        rec = _records.get(request_id)
        if rec:
            rec["finished"] = True
            rec["error"] = bool(error)
            rec["t1"] = time.monotonic()


def timings(request_id: str) -> dict[str, Any]:
    """各步耗时（毫秒）。没有记录或没有步骤链时返回空 dict。

    ``total_ms`` 是端到端耗时；``steps`` 按上报顺序给出每步实际占用。
    注意「步骤耗时」不含首次上报之前的等待（例如上传/排队）。
    """
    with _lock:
        rec = _records.get(request_id)
        if not rec:
            return {}
        rec = dict(rec)
        marks = list(rec["marks"])
        t0, t1 = rec["t0"], rec["t1"]

    if not marks:
        return {}
    end = t1 if t1 is not None else time.monotonic()
    steps = []
    for i, (sid, ts) in enumerate(marks):
        nxt = marks[i + 1][1] if i + 1 < len(marks) else end
        steps.append({"id": sid, "ms": int((nxt - ts) * 1000)})
    return {
        "steps": steps,
        "total_ms": int((end - t0) * 1000),
    }


def snapshot(request_id: str) -> dict[str, Any] | None:
    """给前端的步骤、计时与批进度（未开始识题时 batch 为 None）。

    没有任何步骤链的场景返回 ``None``（前端看到的是「正在处理」，而不是假进度条）。
    """
    with _lock:
        rec = _records.get(request_id)
        if not rec:
            return None
        rec = dict(rec)

    pipeline = PIPELINES.get(rec["scene"] or "")
    if not pipeline:
        return None

    ids = [sid for sid, _ in pipeline]
    pos = ids.index(rec["step"]) if rec["step"] in ids else 0

    steps = []
    for i, (sid, label) in enumerate(pipeline):
        if rec["error"] and i == pos:
            state = "error"
        elif rec["finished"] or i < pos:
            state = "done"
        elif i == pos:
            state = "active"
        else:
            state = "pending"
        steps.append({"id": sid, "label": label, "state": state})

    return {
        "scene": rec["scene"],
        "steps": steps,
        "finished": rec["finished"],
        "error": rec["error"],
        "batch": dict(rec["batch"]) if rec.get("batch") is not None else None,
        # 各步耗时，供前端显示「这一步花了多久」与事后排查瓶颈。
        # 加成字段，不影响原有键。
        "timings": timings(request_id),
    }
