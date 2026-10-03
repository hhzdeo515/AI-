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
                    ◇ dispatch ◇  ── meta ──▶ meta_command ────┐
                           │                                │
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
from .exam_batch import ORIGINAL_BATCH_GRAPH as EXAM_GRAPH
from .exam_batch import JEV_BATCH_GRAPH as JEV_EXAM_GRAPH
from .photo_practice import PHOTO_GRAPH
from .meeting_graph import MEETING_GRAPH
from .nodes import AUDIO_EXT, IMAGE_EXT
from .state import AssistantState

#: 不需要场景执行的指令类动作
META_ACTIONS = frozenset({"stop_playback", "switch_scene"})


def dispatch(state: AssistantState) -> str:
    """条件边：决定这条请求走哪条路。

    顺序即优先级，且每条判断都是**确定性规则**，不调模型：

    1. 指令类动作（停止播报/切场景）不产生内容
    3. **带图片的解题请求**走本地视觉链——Dify 侧 ``start`` 只收文本，
       转发会丢掉「终审重新看原图」这条关键设计
    4. **带音频的请求**走本地 ASR 链——同样是 Dify 侧做不到的预处理；
       此前这条路径缺失，带录音的请求落到 ``local_llm`` 只会回一句
       「请发送会议转写文本」，把用户已经用音频给过的内容再要一遍
    5. 其余带附件的请求留在本地
    6. 粘性会话（会议进行中）是本地状态机，
       Dify 侧没有这些状态
    7. 配置指定 dify 时转发（Dify 失败由图内回退到 local_llm）
    8. 其余走本地
    """
    action = (state.get("routing") or {}).get("action") or ""
    if action in META_ACTIONS:
        return "meta"

    files = state.get("files") or []
    scene = (state.get("routing") or {}).get("scene")
    exts = {Path(f).suffix.lower() for f in files}
    event = state.get("event") or {}
    practice = event.get("practice") if isinstance(event, dict) else None
    previous = state.get("practice_state") or {}
    requested = practice.get("agent", "auto") if isinstance(practice, dict) else "auto"
    practice_action = practice.get("action", "run") if isinstance(practice, dict) else "run"
    # Oral interview replies use ASR and critique, not the meeting audio graph.
    if exts & AUDIO_EXT and isinstance(practice, dict) and practice_action == "answer" and (
            requested == "interview" or requested == "auto" and previous.get("agent") == "interview"):
        return "photo_practice"
    if isinstance(practice, dict) and not files:
        return "photo_practice"
    if not files and previous.get("agent") in {"essay", "interview", "unknown"} and scene != "meeting" and not nodes.is_pure_arith(state.get("text") or ""):
        return "photo_practice"
    # 解题：本轮带图，**或**本轮一个附件都没带、但会话里还留着上一轮的题目图片。
    #
    # 后者是实测补的：用户贴图问「这个选什么」，接着追问「这个题目答案是什么」
    # 时不会重新上传图片。以前这里只看本轮 files，请求于是落到 local_llm，
    # 回一句「请提供具体题目内容，我才帮你解答」——图明明上一轮刚看过。
    if scene == "exam" and (exts & IMAGE_EXT or _reusable_image(state)):
        return "photo_practice"
    if files:
        if exts & AUDIO_EXT:
            # 音频一律先转写：**不按场景收窄**。
            # 「场景」是由文字关键词猜出来的，而用户上传录音时那句文字往往很短
            # （「会议纪要整理」甚至为空），靠它判断内容类型并不可靠。
            # 转写本身与场景无关，整理成什么由节点内部按场景选提示词决定。
            return "audio"
        return "local"

    if scene == "meeting" and action in ("summarize", "stop"):
        return "meeting"

    # 进行中的多轮流程（会议收集中）必须留在本地——
    # Dify 侧没有这些会话状态。
    #
    # 状态从**会话状态**读，不是从 event 读：原来读的 `event["_sticky"]`
    # 没有任何地方写入过，这条守卫一直是死代码（实测发现）。
    if _in_sticky_flow(state):
        return "local"
    if config.EXEC_BACKEND == "dify":
        return "dify"
    return "local"


def _reusable_image(state: AssistantState) -> bool:
    """本轮没带任何附件，但会话里还留着上一轮的题目图片（文件仍在）。

    三种情况一律不放行：
      · 本轮带了别的附件（例如录音）——拿旧题图去解他的话是最糟的错位；
      · 本轮是纯算术请求（「计算 (18+24)*3」）——它该走零 token 快路径，
        不是「接着看那张图」；
      · 上一轮的图片文件已经被清掉（data/uploads 会被清理）。
    """
    if state.get("files"):
        return False
    if nodes.is_pure_arith(state.get("text") or ""):
        return False
    return any(
        Path(f).suffix.lower() in IMAGE_EXT and Path(f).is_file()
        for f in (state.get("last_images") or [])
    )


def _in_sticky_flow(state: AssistantState) -> bool:
    """是否处于进行中的多轮流程。与会话状态一致，不看单次请求的 event。"""
    return (state.get("meeting") or {}).get("status") in ("collecting", "paused")


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
    g.add_node("exam_vision", EXAM_GRAPH)
    g.add_node("exam_jev", JEV_EXAM_GRAPH)
    g.add_node("photo_practice", PHOTO_GRAPH)
    g.add_node("meeting_audio", MEETING_GRAPH)
    g.add_node("audio_gate", lambda s: {})
    g.add_node("meeting_summary", MEETING_GRAPH)
    g.add_node("audio_other", nodes.meeting_audio)
    g.add_node("dify_scene", nodes.dify_scene)
    g.add_node("local_llm", nodes.local_llm)
    g.add_node("postprocess", nodes.postprocess)

    g.add_edge(START, "device_gate")
    g.add_edge("device_gate", "route")

    _routes = {
        "meta": "meta_command",
        "vision": "exam_vision",
        "vision_jev": "exam_jev",
        "photo_practice": "photo_practice",
        "audio": "audio_gate",
        "meeting": "meeting_summary",
        "audio_other": "audio_other",
        "dify": "dify_scene",
        "local": "calc_quick",
    }

    # 遥测是旁路节点：只写库，不改变状态语义
    if with_telemetry:
        g.add_edge("route", "telemetry")
        g.add_conditional_edges("telemetry", dispatch, _routes)
    else:
        g.add_conditional_edges("route", dispatch, _routes)

    # 场景执行
    g.add_edge("meta_command", "postprocess")
    g.add_edge("exam_vision", "postprocess")
    g.add_edge("exam_jev", "postprocess")
    g.add_edge("photo_practice", "postprocess")
    g.add_edge("meeting_audio", "postprocess")
    g.add_conditional_edges("audio_gate", lambda s: "meeting_audio" if (s.get("routing") or {}).get("scene") == "meeting" else "audio_other", ["meeting_audio", "audio_other"])
    g.add_edge("meeting_summary", "postprocess")
    g.add_edge("audio_other", "postprocess")
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
