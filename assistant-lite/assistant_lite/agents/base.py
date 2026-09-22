"""Agent 基类与协议再导出。

各场景 Agent 继承 BaseAgent，只需要实现 can_handle / handle 两个方法。
"""

from __future__ import annotations

from typing import Any

from ..schemas import Reply, Task


class BaseAgent:
    """所有场景 Agent 的基类。"""

    scene: str = "general"
    #: 关键词表，命中即给高置信度（供 orchestrator 的规则层使用）
    keywords: tuple[str, ...] = ()

    def can_handle(self, task: Task) -> float:
        """默认实现：关键词命中 0.8，否则 0。子类可覆盖。"""
        if task.scene_hint == self.scene:
            return 1.0
        text = task.text or ""
        if self.keywords and any(k in text for k in self.keywords):
            return 0.8
        return 0.0

    def handle(self, task: Task, state: dict[str, Any]) -> Reply:
        raise NotImplementedError
