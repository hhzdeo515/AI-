"""锻炼指导场景：健康档案 + 器械识别。

P3 范围：问卷建档、器械识别（照片 / 文字）。
P4 会在此基础上加训练状态机与事件提醒（start_exercise / set_done / pain_report / end_workout）。
"""

from .agent import FitnessAgent

__all__ = ["FitnessAgent"]
