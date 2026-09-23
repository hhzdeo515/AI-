"""图状态定义。

设计取舍：状态是**显式字段**而不是一个自由 `dict`。这是用 LangGraph 的核心收益——
每个字段都能被 checkpointer 快照、被测试断言、被时间旅行回放。
assistant-lite 里状态散布在 `Task` / `Reply` / `session state` 三处，
迁移到图之后它们统一成一份可持久化的 State。
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict


class DeviceJudgement(TypedDict, total=False):
    """端侧 1–3B 的判定结果（契约见 docs/端侧适配设计_眼镜.md §3.1）。"""

    intent: str
    confidence: float | None
    scene: str
    trusted: bool | None


class Routing(TypedDict, total=False):
    """权威路由结论。**路由永远在本地**，不交给任何外部框架。"""

    scene: str
    action: str
    #: event / hint / keyword / sticky / llm
    source: str


class SceneResult(TypedDict, total=False):
    """场景执行产出。"""

    text: str
    #: 由生成正文的那次调用顺带产出的播报语（可能为空）
    speech: str
    #: python / dify / local_fallback
    backend: str
    #: 非空表示这条结果不是正常产出，需要显式暴露给用户
    note: str
    artifacts: list[dict[str, Any]]


def _merge_dict(left: dict, right: dict) -> dict:
    """并行分支合并：后写入的键覆盖先写入的。"""
    out = dict(left or {})
    out.update(right or {})
    return out


def _replace(left: Any, right: Any) -> Any:
    """整体替换 reducer。

    **必需**：LangGraph 对 TypedDict 结构的字段默认做**合并**。也就是说节点只写
    ``{"result": {"backend": "local", "text": "新回答"}}`` 时，上一次请求留下的
    ``result.text`` 会残留下来——同一会话的第二条请求会拿到上一条的正文。

    实测复现：同一 thread 先问「计算 (18+24)*3」再问「hello」，
    路由已经正确切到 general/llm，但 ``result.text`` 仍是 ``(18+24)*3 = 126``。

    语义上这也更对齐 assistant-lite 的 ``state_delta`` 约定：
    一次写入就是一次「本次请求的结论」，不是对上次结论的补丁。
    """
    return right


class AssistantState(TypedDict, total=False):
    """整张图共享的状态。

    `total=False`：节点只返回自己改动的键，LangGraph 按 reducer 合并，
    不需要每个节点都读改写整个状态（assistant-lite 的 `state_delta` 约定同理）。
    """

    # ---- 输入（一次请求的事实，不可变语义）----
    text: str
    owner: str
    session_id: str
    request_id: str
    files: list[str]
    event: dict[str, Any]
    scene_hint: str | None

    # ---- 端侧判定与路由 ----
    device: DeviceJudgement
    routing: Annotated[Routing, _replace]

    # ---- 场景执行 ----
    result: Annotated[SceneResult, _replace]
    #: `calc_quick` 本轮是否命中纯算术。**每轮必刷新**，条件边只读它。
    calc_hit: bool

    # ---- 后处理 ----
    speech: str
    archived_id: str
    #: 追加式：`operator.add` 让并行分支各自 append 而不互相覆盖
    notes: Annotated[list[str], operator.add]

    # ---- 设备形态状态（前端卡片读它）----
    #: 训练状态：``{"workout": {"status", "current", "total_sets"}}``。
    #: 前端 Web 层复用基线的 UI，WORKOUT 卡片读这个字段；迁移到图之后
    #: 一度没有来源，卡片恒显示 IDLE。由 ``nodes.next_workout`` 补齐。
    #:
    #: 另含建档问卷的两个键（``nodes.profile_flow`` 读写）：
    #: ``awaiting``（正在等哪个字段）与 ``draft``（已填部分）。
    #: 它们放这里而不是新开字段，是因为 ``_in_sticky_flow`` 与
    #: ``sticky_flags`` 已经在看 ``fitness.awaiting``——问卷期间必须粘住
    #: fitness 场景，否则用户答「28」这类没有任何关键词的内容会掉到 general。
    fitness: Annotated[dict[str, Any], _merge_dict]
    #: 会议状态：``{"status": "collecting|ended", "transcript": "..."}``。
    #: 同上，MEETING 卡片的 LISTENING 态与转写区读它。
    #: 由 ``nodes.next_meeting`` 补齐。
    meeting: Annotated[dict[str, Any], _merge_dict]
