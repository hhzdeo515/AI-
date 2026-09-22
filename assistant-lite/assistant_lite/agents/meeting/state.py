"""会议状态机：纯逻辑，不做 I/O，便于单测。

规则移植自旧 service.py 的 prepare() 会议分支（已在本机验证）：
- 单场会议转写上限 48000 字符
- start / append / stop / summarize 四态流转
- append 的重复提交由 session 层的 request_id 去重负责，这里不管

本模块只改传入的 state 字典，不做任何网络或磁盘操作。
"""

from __future__ import annotations

import uuid
from typing import Any

from ... import config
from ...schemas import STATUS_NEED_INPUT, STATUS_OK

CHAR_LIMIT = config.MEETING_CHAR_LIMIT

STATUS_IDLE = "idle"
STATUS_COLLECTING = "collecting"
STATUS_ENDED = "ended"

#: 看起来像"操作指令"而不是会议内容的短语
_META_WORDS = ("会议", "记录", "纪要", "继续", "接着", "然后", "开始", "结束", "停止")


def default_meeting() -> dict[str, Any]:
    return {"id": "", "status": STATUS_IDLE, "transcript": ""}


def ensure(state: dict[str, Any]) -> dict[str, Any]:
    """保证 state["meeting"] 结构完整，返回该子字典。"""
    m = state.get("meeting")
    if not isinstance(m, dict):
        m = default_meeting()
        state["meeting"] = m
    m.setdefault("id", "")
    m.setdefault("status", STATUS_IDLE)
    m.setdefault("transcript", "")
    return m


def looks_like_meta(text: str) -> bool:
    """判断一段短文本是不是"操作指令"而非会议内容。

    用于避免把"继续记录"这类短语当成会议正文存进去。
    """
    t = (text or "").strip()
    if not t or len(t) > 14 or "\n" in t:
        return False
    if any(p in t for p in "。！？.!?，,"):
        return False
    return any(w in t for w in _META_WORDS)


def start(state: dict[str, Any]) -> tuple[str, str]:
    """开始一场会议。返回 (回复文本, status)。"""
    m = ensure(state)
    if m["status"] == STATUS_COLLECTING:
        return (
            "当前已有进行中的会议记录，继续提交转写内容即可；"
            "如需重新开始，请先说“结束会议”。",
            STATUS_OK,
        )
    m.update(id=uuid.uuid4().hex, status=STATUS_COLLECTING, transcript="")
    return (
        "已创建会议记录会话。请提交会议转写内容（粘贴文字，或上传录音文件），"
        "我会据此生成纪要。说“生成会议纪要”即可汇总。",
        STATUS_OK,
    )


def append(state: dict[str, Any], transcript: str) -> tuple[str, str]:
    """追加一段转写。返回 (回复文本, status)。"""
    m = ensure(state)
    t = (transcript or "").strip()
    if not t:
        return "没有收到会议内容，请粘贴转写文本或上传录音文件。", STATUS_NEED_INPUT

    used = len(m["transcript"])
    if used + len(t) > CHAR_LIMIT:
        return (
            f"单场会议文本上限为 {CHAR_LIMIT} 字符（当前已 {used} 字符）。"
            "请先生成纪要并结束当前会议，再新建一场。",
            STATUS_NEED_INPUT,
        )

    if not m["id"]:
        m["id"] = uuid.uuid4().hex
    m["transcript"] = (m["transcript"] + "\n" + t).strip()
    m["status"] = STATUS_COLLECTING
    return (
        f"已保存这段会议内容（累计 {len(m['transcript'])} 字符）。"
        "继续说“生成会议纪要”即可汇总。",
        STATUS_OK,
    )


def stop(state: dict[str, Any]) -> tuple[str, str]:
    """结束当前会议记录。返回 (回复文本, status)。"""
    m = ensure(state)
    if not m["transcript"]:
        return "当前没有会议内容，无需结束。", STATUS_NEED_INPUT
    m["status"] = STATUS_ENDED
    return (
        f"已结束本次会议记录（共 {len(m['transcript'])} 字符）。"
        "可随时说“生成会议纪要”。",
        STATUS_OK,
    )


def ready_to_summarize(state: dict[str, Any]) -> tuple[bool, str]:
    """检查能否生成纪要。返回 (是否可生成, 不可生成时的说明)。"""
    m = ensure(state)
    if not m["transcript"].strip():
        return False, "还没有会议内容。请先提交转写文本或上传录音文件。"
    return True, ""
