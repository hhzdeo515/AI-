"""路由：五级规则优先 + LLM 兜底。**纯函数，不碰图、不碰网络以外的状态。**

移植自 `assistant-lite/assistant_lite/orchestrator.py`，行为保持一致：
顺序必须是 事件 → 显式指定 → **关键词** → 粘性 → LLM 兜底。

关键词必须排在粘性之前——否则会议进行中一句「计算 (18+24)*3」
会被当成会议内容吞掉（assistant-lite 踩过这个坑，见其工作日志）。

放进 LangGraph 的收益：路由成了图里一个可单测、可快照、可回放的节点，
而不是散在 `handle()` 里的分支。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import config
from .state import DeviceJudgement, Routing

# --------------------------------------------------------------------------- #
# 结构化事件 -> (场景, 动作)：零 token 确定性映射
# --------------------------------------------------------------------------- #
#: scene 为 None 表示取自事件的 scene 字段（switch_scene）
ACTION_MAP: dict[str, tuple[str | None, str]] = {
    "start_meeting": ("meeting", "start"),
    "append_meeting": ("meeting", "append"),
    "summarize_meeting": ("meeting", "summarize"),
    "stop_meeting": ("meeting", "stop"),
    "solve_captured_question": ("exam", "solve"),
    "export_resource": ("resource", "export"),
    "list_resources": ("resource", "list"),
    "stop_playback": (None, "stop_playback"),
    "switch_scene": (None, "switch_scene"),
}

#: 端侧 1–3B 意图枚举 -> 场景。与 dify-multiagent 的 normalize 节点同源契约。
DEVICE_INTENT_SCENES: dict[str, str] = {
    "meeting_start": "meeting",
    "meeting_append": "meeting",
    "meeting_summarize": "meeting",
    "meeting_stop": "meeting",
    "solve": "exam",
    "switch_scene": "",
    "stop": "",
    "cancel": "",
    "wake": "",
}

#: 关键词表：场景 -> 词元。命中即 0.8 置信度（与 assistant-lite 一致）。
KEYWORDS: dict[str, tuple[str, ...]] = {
    "meeting": ("会议", "纪要", "开会", "会议记录", "转写", "录音"),
    "exam": (
        "这道题", "解题", "做题", "答案", "讲解", "计算", "题目", "题干",
        "选项", "公考", "行测", "图形推理", "资料分析", "数量关系",
        "判断推理", "言语理解",
    ),
    "resource": ("资料", "记录", "历史", "查找", "找一下", "有哪些"),
}

#: 导出意图词：给 0.95 以压过其它场景的关键词
#: （「导出刚才的纪要成 Word」同时含 meeting 的「纪要」和 resource 的「导出」）
EXPORT_WORDS = ("导出", "下载", "转成", "存成", "生成文件", "给我文件", "打包")

#: 命中这些词说明还在进行中的多轮流程里，后续输入默认仍归该场景
STICKY_SUMMARIZE_WORDS = ("生成纪要", "生成会议纪要", "总结会议", "整理纪要", "纪要")
STICKY_START_WORDS = ("开始会议", "开始记录", "新建会议", "开始开会")
STICKY_STOP_WORDS = ("结束会议", "停止记录", "会议结束", "停止会议")

#: 暂停 / 恢复。**只认明确短语，不放裸「暂停」「恢复」**——
#: 会议正文里「三季度恢复产能」「项目暂停两周」会被裸词误判成指令。
STICKY_PAUSE_WORDS = ("暂停会议", "暂停记录", "暂停采集")
STICKY_RESUME_WORDS = ("继续会议", "恢复会议", "继续记录", "恢复记录", "继续采集", "恢复采集")
#: 整句就是一个裸动词时才认（用户只打了「暂停」两个字的情形）
BARE_PAUSE_WORDS = ("暂停", "暂停一下", "先暂停")
BARE_RESUME_WORDS = ("继续", "恢复", "继续吧", "恢复吧")


# --------------------------------------------------------------------------- #
# 端侧判定
# --------------------------------------------------------------------------- #
def device_context(event: dict[str, Any] | None) -> DeviceJudgement:
    """从 event 读端侧 1–3B 的判定。未知枚举一律静默忽略。

    契约：``event.device = {intent, confidence}``，也接受扁平写法。
    绝不因为设备多发了个字段就影响既有路由。
    """
    ev = event or {}
    dev = ev.get("device")
    dev = dev if isinstance(dev, dict) else {}

    intent = str(dev.get("intent") or ev.get("device_intent") or "").strip()
    raw = dev.get("confidence", ev.get("device_confidence"))
    try:
        confidence: float | None = None if raw is None or raw == "" else float(raw)
    except (TypeError, ValueError):
        confidence = None

    scene = DEVICE_INTENT_SCENES.get(intent, "")
    if not scene:
        trusted: bool | None = False if intent else None
    elif intent in config.DEVICE_NEVER_DOWNGRADE:
        trusted = True
    else:
        trusted = (confidence or 0.0) >= config.DEVICE_CONFIDENCE_FLOOR

    return {"intent": intent, "confidence": confidence, "scene": scene, "trusted": trusted}


# --------------------------------------------------------------------------- #
# 关键词打分
# --------------------------------------------------------------------------- #
def keyword_scores(text: str) -> dict[str, float]:
    """返回各场景的关键词置信度。0 表示不命中。"""
    t = text or ""
    scores: dict[str, float] = {}

    if any(w in t for w in EXPORT_WORDS):
        # 导出意图优先于其它场景关键词
        scores["resource"] = 0.95
    else:
        for scene, words in KEYWORDS.items():
            if any(w in t for w in words):
                scores[scene] = 0.8
    return scores


def preprocess_text(text: str) -> str:
    """真正请求里的文本原样返回；这里只是为测试与扩展留一个显式接缝。"""
    return (text or "").strip()


# --------------------------------------------------------------------------- #
# 五级路由
# --------------------------------------------------------------------------- #
def route(
    *,
    text: str,
    event: dict[str, Any] | None = None,
    scene_hint: str | None = None,
    sticky: dict[str, bool] | None = None,
    llm_router: Any = None,
    files: list[str] | None = None,
) -> Routing:
    """返回 ``{"scene", "action", "source"}``。

    ``sticky`` 由图的会话状态推导（见 ``session.sticky_flags``）；
    ``llm_router`` 是一个可调用对象，签名 ``(text, event, file_count) -> dict``，
    为 None 或抛异常时回落 general。**把模型调用作为参数注入，是为了让本函数可单测。**
    ``files`` 是本次请求的附件路径——**附件类型本身就是最强的意图信号**，见第 4 步。
    """
    ev = event or {}

    # 1) 结构化事件：确定性映射，零 token
    sa = str(ev.get("semantic_action", "")).strip()
    if sa in ACTION_MAP:
        scene, action = ACTION_MAP[sa]
        if scene is None:  # switch_scene / stop_playback：场景取自事件
            scene = str(ev.get("scene") or "general").strip()
            if scene not in config.SCENES:
                scene = "general"
        return {"scene": scene, "action": action, "source": "event"}

    # 2) 用户显式指定场景
    if scene_hint and scene_hint in config.SCENES:
        return {"scene": scene_hint, "action": "", "source": "hint"}

    # 3) 关键词命中。必须排在粘性之前（见模块 docstring）
    scores = keyword_scores(text)
    if scores:
        scene = max(scores, key=lambda k: scores[k])
        if scores[scene] >= 0.8:
            return {"scene": scene, "action": "", "source": "keyword"}

    # 4) 附件类型：贴了题目图片 / 传了录音，就是最明确的请求类型，
    #    图片白传了。用户贴图这个动作，比会话里残留的场景状态可信得多。
    paths = [str(f) for f in (files or [])]
    if any(Path(p).suffix.lower() in config.IMAGE_EXT for p in paths):
        return {"scene": "exam", "action": "", "source": "attachment"}
    if any(Path(p).suffix.lower() in config.AUDIO_EXT for p in paths):
        return {"scene": "meeting", "action": "", "source": "attachment"}

    # 5) 粘性场景：进行中的多轮流程
    st = sticky or {}
    if st.get("meeting"):
        return {"scene": "meeting", "action": "", "source": "sticky"}

    # 6) LLM 兜底
    if llm_router is not None:
        try:
            data = llm_router(text, ev, len(paths))
        except Exception:
            return {"scene": "general", "action": "answer", "source": "llm_failed"}
        scene = str(data.get("scene", "")).strip()
        action = str(data.get("action", "")).strip()
        if scene not in config.SCENES:
            scene = "general"
        return {"scene": scene, "action": action, "source": "llm"}

    return {"scene": "general", "action": "answer", "source": "llm_failed"}


def infer_meeting_action(text: str) -> str:
    """会议动作推断：与 assistant-lite 的 ``_infer_action`` 一致。"""
    t = text or ""
    bare = t.strip()
    if bare in BARE_PAUSE_WORDS or any(w in t for w in STICKY_PAUSE_WORDS):
        return "pause"
    if bare in BARE_RESUME_WORDS or any(w in t for w in STICKY_RESUME_WORDS):
        return "resume"
    if any(w in t for w in STICKY_STOP_WORDS):
        return "stop"
    if any(w in t for w in STICKY_SUMMARIZE_WORDS):
        return "summarize"
    if any(w in t for w in STICKY_START_WORDS):
        return "start"
    return "append"


ROUTER_PROMPT = """你是智能助手总控，只输出一个 JSON 对象，不要代码围栏。
字段：scene, action, reason。
scene 只能取 meeting / exam / resource / general：
- 会议记录、会议纪要、会议转写、录音整理 -> meeting
- 题目、解题、答案、讲解、计算、上传的题目图片 -> exam
- 导出、下载、查找历史资料、我有哪些记录、把刚才的结果转成文件 -> resource
- 其他日常问答 -> general
action 只能取 start / append / summarize / stop / solve / export / list / answer。
拿不准时 scene 填 general、action 填 answer。
只输出 JSON，例如 {"scene":"meeting","action":"append","reason":"用户在提交会议转写"}"""
