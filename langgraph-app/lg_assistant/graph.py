"""把编排建模成显式状态图。

```
                    ┌──────────────┐
   START ──────────▶│ device_gate  │  端侧判定归一化（零 token）
                    └──────┬───────┘
                           ▼
                    ┌──────────────┐
                    │    route     │  五级权威路由（零 token 优先）
                    └──────┬───────┘
                           ▼
                    ┌──────────────┐
                    │  telemetry   │  端侧判对率落库
                    └──────┬───────┘
                           ▼
                    ◇ dispatch ◇  ── meta ──▶ meta_command ─┐
                           │                                │
                           ├── calc ──▶ calc_quick ──────────┤
                           │                                │
                           ├── dify ──▶ dify_scene ──────────┤
                           │                                │
                           └─ local ──▶ local_llm ───────────┤
                                                            ▼
                                                   ┌────────────────┐
                                                   │  postprocess   │ 播报语 + 归档
                                                   └───────┬────────┘
                                                           ▼
                                                          END
```

**与 assistant-lite 的关键差异**：那条 `telemetry` 分支在 assistant-lite 里是
`handle()` 中间的三行副作用；在这里它是一个有名字、可快照、可单独测试的节点。
同样，`_execute_dify` 的「带附件/粘性会话不转发」两条守卫变成了 `dispatch` 的
条件边——**规则从代码里的 if 变成了图上的结构**，这是本迁移最实质的收益。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph

from . import config, nodes
from .nodes import IMAGE_EXT
from .state import AssistantState

#: 不需要场景执行的指令类动作
META_ACTIONS = frozenset({"stop_playback", "switch_scene"})


def dispatch(state: AssistantState) -> str:
    """条件边：决定这条请求走哪条路。

    顺序即优先级，且每条判断都是**确定性规则**，不调模型：

    1. 指令类动作（停止播报/切场景）不产生内容
    2. **带图片的解题请求**走本地视觉链——Dify 侧 ``start`` 只收文本，
       转发会丢掉「终审重新看原图」这条关键设计
    3. 其余带附件的请求留在本地——需先经本地 ASR 预处理
    4. 粘性会话（会议进行中 / 训练进行中）是本地状态机，Dify 侧没有这些状态
    5. 配置指定 dify 时转发（Dify 失败由图内回退到 local_llm）
    6. 其余走本地
    """
    action = (state.get("routing") or {}).get("action") or ""
    if action in META_ACTIONS:
        return "meta"

    files = state.get("files") or []
    if files:
        has_image = any(Path(f).suffix.lower() in IMAGE_EXT for f in files)
        if has_image and (state.get("routing") or {}).get("scene") == "exam":
            return "vision"
        return "local"

    if (state.get("event") or {}).get("_sticky"):
        return "local"
    if config.EXEC_BACKEND == "dify":
        return "dify"
    return "local"


def build_graph(checkpointer: Any = None, *, with_telemetry: bool = True):
    """构造并编译状态图。

    ``checkpointer`` 传 None 时图**不可续跑**——只适合单测。
    生产路径用 :func:`open_checkpointer` 拿 SqliteSaver。
    """
    g = StateGraph(AssistantState)

    g.add_node("device_gate", nodes.device_gate)
    g.add_node("route", nodes.route_node)
    if with_telemetry:
        g.add_node("telemetry", nodes.telemetry_node)

    g.add_node("meta_command", nodes.meta_command)
    g.add_node("calc_quick", nodes.calc_quick)
    g.add_node("exam_vision", nodes.exam_vision)
    g.add_node("dify_scene", nodes.dify_scene)
    g.add_node("local_llm", nodes.local_llm)
    g.add_node("postprocess", nodes.postprocess)

    g.add_edge(START, "device_gate")
    g.add_edge("device_gate", "route")

    # 遥测是旁路节点：只写库，不改变状态语义
    if with_telemetry:
        g.add_edge("route", "telemetry")
        g.add_conditional_edges(
            "telemetry",
            dispatch,
            {
                "meta": "meta_command",
                "vision": "exam_vision",
                "dify": "dify_scene",
                "local": "calc_quick",
            },
        )
    else:
        g.add_conditional_edges(
            "route",
            dispatch,
            {
                "meta": "meta_command",
                "vision": "exam_vision",
                "dify": "dify_scene",
                "local": "calc_quick",
            },
        )

    # 场景执行
    g.add_edge("meta_command", "postprocess")
    g.add_edge("exam_vision", "postprocess")
    g.add_edge("dify_scene", "postprocess")

    # 本地路径：先试零 token 的算术快路径，未命中再落到模型。
    # 条件边**只读 `calc_hit`**——它每轮必刷新，不会残留上一轮的值。
    # （用 `result.text` 判断会踩检查点的陈旧状态；用 Command 会与静态边竞态。）
    g.add_conditional_edges(
        "calc_quick",
        lambda s: "done" if s.get("calc_hit") else "llm",
        {"done": "postprocess", "llm": "local_llm"},
    )
    g.add_edge("local_llm", "postprocess")
    g.add_edge("postprocess", END)

    return g.compile(checkpointer=checkpointer, name="glasses-assistant")


# --------------------------------------------------------------------------- #
# 持久化
# --------------------------------------------------------------------------- #
def open_checkpointer(db_path: Path | None = None) -> SqliteSaver:
    """打开 SQLite checkpointer。

    **这是本次迁移最实质的能力**：assistant-lite 的异步任务是进程内线程池 + 内存表，
    重启即丢；眼镜必然断连，断连后必须能从任意步骤恢复。
    checkpointer 让「线程 + 检查点」落盘，重连后用同一个 thread_id 即可续跑。
    """
    config.ensure_dirs()
    path = Path(db_path) if db_path else config.CHECKPOINT_DB
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30, check_same_thread=False)
    return SqliteSaver(conn)


def thread_id(owner: str, session_id: str) -> str:
    """一条可续跑的线程标识。设备端重连后必须用同一个值。"""
    return f"{owner or 'local'}:{session_id or 'default'}"


def run_config(owner: str, session_id: str, request_id: str = "") -> dict[str, Any]:
    """invoke/stream 的 config。``thread_id`` 决定断点归属。"""
    cfg: dict[str, Any] = {"configurable": {"thread_id": thread_id(owner, session_id)}}
    if request_id:
        cfg.setdefault("metadata", {})["request_id"] = request_id
    return cfg
