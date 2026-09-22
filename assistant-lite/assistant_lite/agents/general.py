"""通用问答 Agent：所有场景都不匹配时的兜底。"""

from __future__ import annotations

from typing import Any

from .. import llm
from ..schemas import (
    SCENE_GENERAL,
    STATUS_ERROR,
    STATUS_OK,
    Reply,
    Task,
)
from .base import BaseAgent

PROMPT = (
    "你是本地智能助手，支持三个场景：会议纪要、拍照解题、锻炼指导。"
    "日常问题简洁作答，不啰嗦。不宣称未实际执行的动作，"
    "不确定的信息要说明不确定，不编造。"
)


class GeneralAgent(BaseAgent):
    scene = SCENE_GENERAL

    def can_handle(self, task: Task) -> float:
        # 永远给一个低分兜底，让路由层的 keyword 阈值（0.8）不会选中它
        return 0.1

    def handle(self, task: Task, state: dict[str, Any]) -> Reply:
        try:
            text = llm.chat(
                [
                    {"role": "system", "content": PROMPT},
                    {"role": "user", "content": task.text or "你好"},
                ],
                temperature=0.5,
            )
        except llm.LLMError as e:
            return Reply(
                text=f"回答失败：{e}",
                scene=SCENE_GENERAL,
                action="answer",
                status=STATUS_ERROR,
            )
        return Reply(
            text=text,
            scene=SCENE_GENERAL,
            action="answer",
            status=STATUS_OK,
            state_delta={"active_scene": SCENE_GENERAL},
        )
