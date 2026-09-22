"""全项目唯一数据契约：Task（进）/ Reply（出）。

编排层、各场景 Agent、Web 层都只认这两个结构。
把"问答型"和"主动提醒型"统一成同一条 event -> orchestrator -> agent -> Reply 事件流，
区别只在 Agent 内部有没有状态机。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

SCENE_GENERAL = "general"
SCENE_MEETING = "meeting"
SCENE_EXAM = "exam"
SCENE_FITNESS = "fitness"

# Reply.status 取值
STATUS_OK = "ok"  # 正常完成
STATUS_NEED_INPUT = "need_input"  # 需要用户补充信息（如健康档案未建）
STATUS_DUPLICATE = "duplicate"  # request_id 命中，已去重
STATUS_ERROR = "error"  # 出错


@dataclass
class Task:
    """一次请求的完整输入。"""

    text: str = ""
    owner: str = "local"
    session_id: str = "default"
    request_id: str = ""  # 非空时参与去重
    files: list[str] = field(default_factory=list)  # 本地文件绝对路径（图片/音频）
    event: dict[str, Any] = field(default_factory=dict)  # 结构化事件，如 {"semantic_action": "set_done"}
    scene_hint: str | None = None  # 用户显式指定的场景，路由时最高优先


@dataclass
class Reply:
    """一次请求的完整输出。"""

    text: str = ""
    scene: str = SCENE_GENERAL
    action: str = ""
    status: str = STATUS_OK
    artifacts: list[dict[str, Any]] = field(default_factory=list)  # 产出文件描述
    state_delta: dict[str, Any] = field(default_factory=dict)  # 需要写回会话状态的增量
    archive: bool = False  # 是否值得归档进资料库


class Agent(Protocol):
    """场景 Agent 协议。所有 Agent 都实现这两个方法。"""

    scene: str

    def can_handle(self, task: Task) -> float:
        """返回 0~1 的置信度；0 表示不处理。"""
        ...

    def handle(self, task: Task, state: dict[str, Any]) -> Reply:
        """处理请求并返回 Reply。state 是当前会话状态（只读，改动通过 state_delta 返回）。"""
        ...
