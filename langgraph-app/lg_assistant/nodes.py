"""图节点。

设计原则：**每个节点只做一件有名字的事，并且都可单测。**
这是用 LangGraph 的核心理由——assistant-lite 里这些步骤藏在一个 240 行的
``handle()`` 里（读状态、路由、执行、降级、归档、记遥测），改一处要读全篇；
现在它们是图上可快照、可单独调用的节点。

节点清单：

===================  ====================================================
``device_gate``      端侧判定归一化（零 token，纯函数）
``route``            五级权威路由（零 token 优先）
``telemetry``        落库端侧判对率
``meta_command``     stop_playback / switch_scene 这类不产生内容的指令
``calc_quick``       纯算术快路径（零 token 确定性工具）
``dify_scene``       Dify 场景 Agent 执行（可选后端，失败回退 local_llm）
``local_llm``        本地场景回复
``postprocess``      播报语 + 归档
===================  ====================================================
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import config, dify_backend, llm, search, store, transcribe, vision
from .ported import calc as calc_tool
from .ported import speech as speech_tool
from .routing import (
    device_context,
    infer_meeting_action,
    route as route_decision,
)
from .state import AssistantState
from .tools import audio as audio_tool

#: 可送视觉模型的图片扩展名
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff"}

#: 可送 ASR 的音频扩展名（与 tools/audio.py 的处理范围一致）
AUDIO_EXT = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".amr", ".wma", ".webm", ".mp4"}

#: 判「这题是否有时效性」时，从视觉观察记录里取多长一段当题干。
#: 记录开头就是题型与题干，取前 600 字足够；模型后面那段自由发挥
#: （「现在看第三列…」）不算题干，见 ``exam_vision`` 里的说明。
QUESTION_HEAD_CHARS = 600

try:  # LangGraph >= 0.2.30
    from langgraph.types import Command
except Exception:  # pragma: no cover - 仅在极旧版本上触发
    Command = None  # type: ignore[assignment]

#: `calc_quick` 命中时写入的标记，作为「本次命中」的显式信号
CALC_NOTE = "由确定性算术工具求值，未调用模型"

# --------------------------------------------------------------------------- #
# 场景提示词
# --------------------------------------------------------------------------- #
SCENE_PROMPTS: dict[str, str] = {
    "meeting": (
        "你是会议纪要 Agent。把用户提供的会议转写整理成结构化纪要："
        "主题、覆盖范围、讨论要点、明确决策、行动项、待确认。\n"
        "严格约束：只能依据原文，禁止补全原文没有的时间期限、流程步骤或责任人，"
        "原文没说的字段写「未明确」。不要编造参会人姓名。"
    ),
    "resource": (
        "你是资料检索 Agent。真正可用的检索与导出指令是："
        "「我有哪些资料」「导出最新一份成 Word」「导出 <ID前缀> 成 PDF」。\n"
        "在本次纯文本上下文中你无法直接访问资料库——明确说明这一点，"
        "不要编造任何资料标题、ID 或内容。"
    ),
    "general": (
        "你是本地智能助手，支持会议纪要、拍照解题及资料检索。"
        "日常问题简洁作答，不啰嗦。不宣称未实际执行的动作，"
        "不确定的信息要说明不确定，不编造。"
    ),
}

#: 纯算术快路径：「计算 1+2*3」
_ARITH_PREFIX = ("计算", "算一下", "算算", "求")
_ARITH_OK_CHARS = set("0123456789+-*/%(). \t")


# --------------------------------------------------------------------------- #
# 1. 端侧门控
# --------------------------------------------------------------------------- #
def device_gate(state: AssistantState) -> dict[str, Any]:
    """把 ``event`` 里的端侧判定归一化。

    未知枚举静默忽略——设备多发一个字段绝不能影响既有路由。
    """
    return {"device": dict(device_context(state.get("event")))}


# --------------------------------------------------------------------------- #
# 2. 权威路由
# --------------------------------------------------------------------------- #
def sticky_flags(state: AssistantState) -> dict[str, bool]:
    """Only meeting collection has a sticky conversation state."""
    return {"meeting": (state.get("meeting") or {}).get("status") in ("collecting", "paused")}


def route_node(state: AssistantState) -> dict[str, Any]:
    text = state.get("text") or ""
    decision = route_decision(text=text, event=state.get("event") or {},
        scene_hint=state.get("scene_hint"), sticky=sticky_flags(state),
        llm_router=_router_callable(), files=state.get("files") or [])
    if not decision["action"] and decision["scene"] == "meeting":
        decision = dict(decision)
        decision["action"] = infer_meeting_action(text)
    return {"routing": decision}


def _router_callable():
    """模型兜底开关。测试里通过 ``set_router`` 注入桩，避免真实调用。"""
    return _ROUTER


_ROUTER: Any = llm.llm_router


def set_router(fn: Any) -> None:
    """注入路由兜底实现（测试用）。传 None 表示禁用模型兜底。"""
    global _ROUTER
    _ROUTER = fn


# --------------------------------------------------------------------------- #
# 3. 遥测
# --------------------------------------------------------------------------- #
def telemetry_node(state: AssistantState) -> dict[str, Any]:
    """落库端侧判对率。**横切关注点：任何异常都不该打断主链路。**"""
    device = state.get("device") or {}
    rt = state.get("routing") or {}
    try:
        store.record_routing(
            owner=state.get("owner") or "local",
            session_id=state.get("session_id") or "default",
            thread_id=_thread_id(state),
            request_id=state.get("request_id") or "",
            scene=rt.get("scene") or "general",
            action=rt.get("action") or "",
            source=rt.get("source") or "unknown",
            device_intent=device.get("intent") or "",
            device_confidence=device.get("confidence"),
            device_scene=device.get("scene") or "",
            device_trusted=device.get("trusted"),
        )
    except Exception as e:
        return {"notes": [f"遥测写入失败（已忽略）：{type(e).__name__}: {e}"]}
    return {}


def _thread_id(state: AssistantState) -> str:
    """LangGraph 的会话标识：owner + session 一起构成一条可续跑的线程。"""
    return f"{state.get('owner') or 'local'}:{state.get('session_id') or 'default'}"


# --------------------------------------------------------------------------- #
# 4. 指令类动作（不产生内容）
# --------------------------------------------------------------------------- #
def meta_command(state: AssistantState) -> dict[str, Any]:
    """stop_playback / switch_scene 这类动作直接返回，不落到场景执行。"""
    action = (state.get("routing") or {}).get("action") or ""
    if action == "stop_playback":
        return {
            "result": {
                "text": "已停止播报。",
                "speech": "",
                "backend": "local",
            }
        }
    return {
        "result": {
            "text": f"已切换到「{(state.get('routing') or {}).get('scene') or 'general'}」场景。",
            "backend": "local",
        }
    }


# --------------------------------------------------------------------------- #
# 5. 纯算术快路径（零 token）
# --------------------------------------------------------------------------- #
def _arith_expr(text: str) -> str | None:
    """从「计算 (18+24)*3」里取出纯算式；不是纯算术请求返回 None。"""
    t = (text or "").strip()
    for prefix in _ARITH_PREFIX:
        if not t.startswith(prefix):
            continue
        expr = t[len(prefix):].strip().lstrip(":：").strip().rstrip("？?。.=").strip()
        if expr and not (set(expr) - _ARITH_OK_CHARS):
            return expr
    return None


def is_pure_arith(text: str) -> bool:
    """这句是不是「计算 1+1」这类纯算术请求。

    路由层要复用这个判据：会话里留着上一轮的题目图片时，
    「计算 (18+24)*3」不能被当成「接着看那张图」而去跑视觉链。
    """
    return _arith_expr(text) is not None


def calc_quick(state: AssistantState) -> dict[str, Any]:
    """纯算术快路径（零 token）。

    **控制流走 ``calc_hit`` 这个显式标记，不走数据值。**
    实测踩过两次坑：

    1. 用「``result.text`` 是否为空」做条件边 → 未命中时条件边读到检查点里
       上一次的 ``result``，误判「已完成」，``local_llm`` 从不执行，
       用户拿到上一条请求的答案。
    2. 用 ``Command(goto=...)`` 走捷径 → 它与静态边**并存**，两条路都执行，
       产生竞态（后写入者覆盖前者）。

    正确做法：命中与否都写 ``calc_hit``（每轮必刷新，永不陈旧），
    由条件边只读这一个字段决定去向。
    """
    text = (state.get("text") or "").strip()
    expr = None if state.get("files") else _arith_expr(text)
    if expr:
        try:
            value = calc_tool.calculate(expr)
        except calc_tool.CalcError:
            value = None
        if value is not None:
            return {
                "calc_hit": True,
                "result": {
                    "text": f"{expr} = {value}",
                    "backend": "local",
                    "note": CALC_NOTE,
                },
            }
    # 未命中也要写：这个字段必须每轮刷新，否则条件边会读到上一轮的值
    return {"calc_hit": False}


# --------------------------------------------------------------------------- #
# 6. Dify 场景执行（可选后端）
# --------------------------------------------------------------------------- #
def dify_scene(state: AssistantState) -> dict[str, Any]:
    """转发给 Dify 工作流。失败回退本地（除非显式关闭回退）。"""
    rt = state.get("routing") or {}
    device = state.get("device") or {}
    scene = rt.get("scene") or "general"

    try:
        text = dify_backend.run_scene(
            text=state.get("text") or "",
            scene=scene,
            device_intent=device.get("intent") or "",
            device_confidence=device.get("confidence"),
            user=state.get("owner") or "local",
        )
    except dify_backend.DifyError as e:
        if not config.DIFY_FALLBACK_LOCAL:
            return {
                "result": {
                    "text": f"Dify 后端执行失败：{e}",
                    "backend": "dify",
                    "note": "未回退（DIFY_FALLBACK_LOCAL=0）",
                }
            }
        fallback = local_llm(state)
        res = dict(fallback.get("result") or {})
        res["text"] = f"{res.get('text', '')}\n\n（Dify 后端不可用，已回退本地：{e}）"
        res["backend"] = "local_fallback"
        return {"result": res}

    return {"result": {"text": text, "backend": "dify"}}


# --------------------------------------------------------------------------- #
# 6b. 拍照解题：本地视觉链（真读图）
# --------------------------------------------------------------------------- #
def exam_vision(state: AssistantState) -> dict[str, Any]:
    """Compatibility entry point; production registers the compiled exam subgraph."""
    from .exam_batch import ORIGINAL_BATCH_GRAPH, JEV_BATCH_GRAPH
    EXAM_GRAPH = JEV_BATCH_GRAPH if state.get("exam_backend") == "jev" else ORIGINAL_BATCH_GRAPH
    out = EXAM_GRAPH.invoke(state)
    return {key: out[key] for key in ("result", "last_images") if key in out}


# --------------------------------------------------------------------------- #
# 6c. 会议录音：转写 + 说话人分离 + 纪要
# --------------------------------------------------------------------------- #
#: 会议转写的整理提示。纪要链路与「人工改判后重算」**共用这一份**——
#: 两处各写一遍，改了一处忘了另一处，重算出来的纪要就会和第一次不一致。
MEETING_INSTRUCTION = (
    "以下是会议录音的转写，每行以 [时间] 发言人N：开头。"
    "请依据它整理纪要；引用观点时指明是哪位发言人。"
    "标注了「短促片段·归属存疑」的行，说话人归属不可靠，"
    "不要把它的内容算成某位发言人的观点；转写用词可能有同音错字，"
    "拿不准的用词照原样保留，不要替它改成另一个说法。"
)


def summarize_transcript(transcript: str, *, instruction: str, system: str) -> str:
    """把转写整理成纪要/可读文本。失败时抛 ``llm.LLMError``，由调用方决定怎么降级。"""
    return llm.chat(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": f"{instruction}\n\n{transcript}"},
        ],
        temperature=0.3,
    )


def meeting_body(summary: str, report_line: str, transcript: str) -> str:
    """会议产出的版式：纪要 + 完整文字记录。会议链路与「改判后重算」共用。"""
    return f"{summary}\n\n---\n\n## 会议文字记录 {report_line}\n\n{transcript}"


def resummarize_meeting(transcript: str) -> str:
    """人工改判后用**同一套提示词**重新生成纪要（否则两份纪要会漂移）。"""
    from .meeting_graph import summarize_text, render_summary
    result = summarize_text(transcript)
    if result.get("error"):
        raise llm.LLMError(result["error"])
    return render_summary(result["summary"], result["issues"])


def meeting_audio(state: AssistantState) -> dict[str, Any]:
    """把上传的录音转成「带发言人的文字记录」并整理成纪要。

    **为什么需要这个节点**：图片有 `exam_vision` 专门处理，音频却没有任何节点接手——
    带音频的请求会直接落到 `local_llm`，而那里只拿得到用户那句「会议纪要整理」，
    于是模型只能回「请发送会议转写文本」，把用户明明已经用音频给过的内容再要一遍。
    这条缺口实测复现：上传 1.9MB 录音，4 秒返回一句要文本的提示。

    **两条转写通路，按需选择**：

    ==================  ==========================  ============================
    通路                 能力                        代价
    ==================  ==========================  ============================
    paraformer-v2       说话人分离 + 句级时间戳      需上传到百炼临时存储换取 URL
    qwen3-asr-flash     纯文本                       本地文件直接送，快
    ==================  ==========================  ============================

    首选前者：用户明确要「区分不同声线的参会人」，而 qwen3-asr-flash **不支持**——
    实测传 diarization_enabled 返回逐字节相同的结果，参数被静默忽略。
    前者失败时降级到后者，转写内容仍然拿得到，只是没有发言人区分。
    """
    files = state.get("files") or []
    audios = [f for f in files if Path(f).suffix.lower() in AUDIO_EXT]
    if not audios:
        return {
            "result": {
                "text": "没有找到可转写的音频。请上传录音文件（mp3/wav/m4a 等）。",
                "backend": "local",
                "note": "附件里没有音频",
            }
        }

    warnings: list[str] = []
    utterances: list[dict] = []
    transcript = ""

    from .tools.audio import normalize_browser_recording
    try:
        audios = [str(normalize_browser_recording(f)) for f in audios]
    except RuntimeError as exc:
        return {"result": {"text": str(exc), "backend": "local", "note": "录音转换失败"}}

    # --- 首选：paraformer-v2，带说话人分离 ---
    # **整段提交而不是分片**：分片各自 diarize 会让 speaker_id 每片重新从 0 开始，
    # 同一人在第 2 片可能变成 0 号，拼起来等于把一个人拆成多个。
    #
    # 同时挂上定制热词：领域词（具身智能、ChatGPT…）听错是解码器缺先验，
    # 热词表正好补这一块。热词表建失败不影响转写，只记一条 warning。
    vocabulary_id = ""
    try:
        vocabulary_id = transcribe.ensure_vocabulary()
    except Exception as e:  # noqa: BLE001
        warnings.append(f"热词表未生效（{e}），领域词可能被听错")

    diar_ok = False
    for f in audios:
        p = Path(f)
        try:
            data = transcribe.transcribe_with_speakers(p, vocabulary_id=vocabulary_id or None)
        except Exception as e:  # noqa: BLE001 - 分离失败要能降级，不能炸链路
            warnings.append(f"{p.name}：说话人分离失败（{e}），已降级为纯文本转写")
            continue
        if not data["utterances"]:
            warnings.append(f"{p.name}：未识别到语音内容")
            continue
        utterances.extend(data["utterances"])
        diar_ok = True

    if diar_ok:
        transcript = transcribe.format_transcript(utterances)
        # 分离质量如实报告：共几位、每人几段、哪些片段归属存疑。
        # 不替服务改判（改判=可能把别人的话安在别人头上），只说清楚它分出了什么、
        # 以及哪里不能全信。逐段结构另存一份供界面人工改判（见 postprocess）。
        report = transcribe.diarization_report(utterances)
        body_extra = transcribe.describe_diarization(report)
    else:
        # --- 降级：qwen3-asr-flash，长音频分片 ---
        parts: list[str] = []
        for f in audios:
            p = Path(f)
            try:
                chunks, note = audio_tool.split(str(p))
            except Exception as e:  # noqa: BLE001
                chunks, note = [p], f"分片失败（{type(e).__name__}: {e}），已整段提交"
            if note:
                warnings.append(f"{p.name}：{note}")
            try:
                for chunk in chunks:
                    text = llm.asr(chunk)
                    if text.strip():
                        parts.append(text.strip())
                    else:
                        warnings.append(f"{p.name}：这一片没有识别到语音")
            except llm.LLMError as e:
                warnings.append(f"{p.name} 转写失败：{e}")
            finally:
                try:
                    audio_tool.cleanup(chunks, p)
                except Exception:  # noqa: BLE001
                    pass
        transcript = "\n".join(parts).strip()
        body_extra = "（未能区分发言人，以下为纯文本转写）"

    if not transcript:
        detail = ("\n\n未处理的部分：\n" + "\n".join(f"· {w}" for w in warnings)) if warnings else ""
        return {
            "result": {
                "text": f"没能从音频里识别到语音内容。{detail}",
                "backend": "local",
                "note": "ASR 未产出文本",
            }
        }

    # 转写拿到了，按**场景**决定整理成什么。
    #
    # 为什么不写死成会议纪要：dispatch 对音频一律放行（场景是靠文字关键词猜的，
    # 用户上传录音时那句文字常常很短，判断不可靠）。所以这里必须按场景分流，
    # 否则一段「口述的数学题」录音会被硬套成会议纪要格式。
    scene = (state.get("routing") or {}).get("scene") or "meeting"
    if scene == "meeting":
        system = SCENE_PROMPTS["meeting"]
        instruction = MEETING_INSTRUCTION
    elif scene == "exam":
        system = (
            "你在处理一段口述题目的录音转写。请先忠实还原题面与条件，"
            "再作答并说明依据。转写可能有错别字或漏字，不确定处明确说明，不要臆测。"
        )
        instruction = "以下是录音转写，内容是口述的题目："
    else:
        system = (
            "你在处理一段录音转写。请忠实整理成可读文本（分段、补标点、"
            "保留发言人与关键信息）。**不要编造转写里没有的内容**；"
            "听不清或缺失的地方标注出来。"
        )
        instruction = "以下是录音转写："

    try:
        body_raw = summarize_transcript(transcript, instruction=instruction, system=system)
    except llm.LLMError as e:
        # 转写成功但整理失败：把转写交出去，别让用户的录音白转一遍
        return {
            "result": {
                "text": f"录音已转写，但整理纪要失败：{e}\n\n**转写原文**\n{transcript}",
                "backend": "local",
                "note": "转写成功、整理失败",
            }
        }

    body, spoken = speech_tool.resolve(body_raw)
    # 正文 = 纪要 + 完整文字记录。用户要的就是这两样，一次都给全。
    body = meeting_body(body, body_extra, transcript)
    if warnings:
        body += "\n\n未处理的部分：\n" + "\n".join(f"· {w}" for w in warnings)

    artifacts = [
        {
            "kind": "transcript",
            "files": [Path(f).name for f in audios],
            "chars": len(transcript),
            "speakers": len(transcribe.speaker_stats(utterances)) if diar_ok else 0,
            "diarized": diar_ok,
            # 逐段结构交给后处理落库（见 postprocess）：用户可以在界面上人工改判
            # 「谁说的是谁」。渲染好的正文给不识字的人看，逐段结构给改判用。
            "record": transcribe.new_record(utterances, summary=body) if diar_ok else None,
        }
    ]
    if diar_ok:
        artifacts.append({"kind": "speaker_stats", "stats": transcribe.speaker_stats(utterances)})

    return {
        "result": {
            "text": body,
            "speech": spoken,
            "backend": "local",
            "artifacts": artifacts,
        }
    }


# --------------------------------------------------------------------------- #
# 7. 本地场景回复
# --------------------------------------------------------------------------- #
def local_llm(state: AssistantState) -> dict[str, Any]:
    """本地场景回复。有时效性的问题先联网核对，再作答。"""
    rt = state.get("routing") or {}
    scene = rt.get("scene") or "general"
    system = SCENE_PROMPTS.get(scene, SCENE_PROMPTS["general"])
    question = (state.get("text") or "").strip()

    # ---- 会议暂停闸：暂停/恢复类指令不能送去写纪要 ----
    #
    # 「暂停会议」这四个字本身会被会议提示词当成转写正文，模型于是硬凑一份
    # 纪要出来（正文里就写着「暂停会议」）。状态由 next_meeting 落库，
    # 这里只负责给固定文案，并且明确说出「刚才那段没有保存」。
    meeting = state.get("meeting") or {}
    m_action = str(rt.get("action") or "")
    if scene == "meeting" and m_action not in ("stop", "summarize"):
        paused_now = (meeting.get("status") or "idle") == "paused"
        kept = len(meeting.get("transcript") or "")
        if m_action == "pause":
            text = (
                f"已暂停会议记录，已保留 {kept} 字符转写。\n\n"
                "说「继续会议」恢复采集；说「生成会议纪要」可以直接汇总已有内容。"
            )
        elif m_action == "resume":
            text = f"已恢复会议记录（当前 {kept} 字符）。继续提交转写内容即可。"
        elif m_action == "start" and paused_now:
            text = (
                "当前有一场已暂停的会议记录，**没有新建**。\n\n"
                "说「继续会议」接着记；要重新开始，先说「结束会议」再新建。"
            )
        elif paused_now:
            text = (
                "会议记录处于暂停状态，**这段内容没有保存**。\n\n"
                "说「继续会议」恢复后再提交；要结束就说「结束会议」。"
            )
        else:
            text = ""
        if text:
            body, spoken = speech_tool.resolve(text)
            return {
                "result": {
                    "text": body,
                    "speech": spoken,
                    "backend": "local",
                    "note": "会议暂停状态下的确定性回复",
                }
            }

    # 时效性问题必须先联网：模型的知识有截止时间，问「某会是否召开」
    # 它会给过期结论（实测三次把三中全会答成「尚未召开」）。
    # 提示词治不好这个病，只能让它去查。
    if question and search.needs_search(question):
        try:
            found = search.search_answer(question, system=system)
        except search.SearchError as e:
            # 检索失败不能静默降级成"凭记忆回答"——那正是原来出错的方式。
            # 明确告诉用户这次没查到，结论可能过期。
            fallback_system = (
                system
                + "\n\n【重要】本次联网检索失败，你只能凭既有知识作答。"
                "你的知识有截止时间，涉及「某个会议/文件/政策当前状态」时"
                "**必须明确说明可能已过时、请以官方最新发布为准**，"
                "不得给出确定结论。"
            )
            try:
                text = llm.chat(
                    [
                        {"role": "system", "content": fallback_system},
                        {"role": "user", "content": question},
                    ],
                    temperature=0.3,
                )
            except llm.LLMError as e2:
                return {
                    "result": {
                        "text": f"回答失败：{e2}",
                        "backend": "local",
                        "note": "模型调用失败",
                    }
                }
            body, spoken = speech_tool.resolve(text)
            body += f"\n\n> ⚠️ 本次联网检索未能完成（{e}），以上为模型既有知识，可能已过时。"
            return {
                "result": {
                    "text": body,
                    "speech": spoken,
                    "backend": "local",
                    "note": "时效性问题，联网检索失败",
                }
            }

        body = found["answer"] + search.format_sources(found["sources"])
        _, spoken = speech_tool.resolve(found["answer"])
        return {
            "result": {
                "text": body,
                "speech": spoken,
                "backend": "local",
                "artifacts": [
                    {
                        "kind": "search",
                        "sources": found["sources"][:6],
                        "count": len(found["sources"]),
                    }
                ],
            }
        }

    user = (
        f"用户输入：{question}\n"
        f"场景：{scene}／动作：{rt.get('action') or 'answer'}\n"
        f"端侧判定：{json.dumps(state.get('device') or {}, ensure_ascii=False)}"
    )
    try:
        text = llm.chat(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.3,
        )
    except llm.LLMError as e:
        return {
            "result": {
                "text": f"回答失败：{e}",
                "backend": "local",
                "note": "模型调用失败",
            }
        }

    body, spoken = speech_tool.resolve(text)
    return {"result": {"text": body, "speech": spoken, "backend": "local"}}


# --------------------------------------------------------------------------- #
# 8. 后处理：播报语 + 归档
# --------------------------------------------------------------------------- #
def next_meeting(state: AssistantState) -> dict[str, Any] | None:
    """按本轮路由动作推进会议状态；非会议场景返回 None。

    Web 层复用基线的 UI，MEETING 卡片按
    ``status == "collecting"`` 显示 LISTENING、``paused`` 显示 PAUSED，
    转写区读的是 ``/api/state`` 的 ``meeting`` 字段，迁移到图之后同样没有来源。
    """
    routing = state.get("routing") or {}
    if routing.get("scene") != "meeting":
        return None

    action = str(routing.get("action") or "")
    cur = dict(state.get("meeting") or {})
    transcript = cur.get("transcript") or ""
    text = (state.get("text") or "").strip()

    if action == "start":
        if cur.get("status") == "paused":
            # 暂停中的会话不能被「开始会议」静默清空
            return cur
        # 本轮这句就是会议的第一段转写
        return {"status": "collecting", "transcript": text}
    if action == "pause":
        cur["status"] = "paused"
        return cur
    if action == "resume":
        cur["status"] = "collecting"
        return cur
    if action == "append":
        if cur.get("status") == "paused":
            # 暂停期间不收内容：既不追加，也不偷偷改回 collecting
            return cur
        joined = f"{transcript}\n{text}".strip() if transcript else text
        return {"status": "collecting", "transcript": joined}
    if action in ("summarize", "stop"):
        cur["status"] = "ended"
        return cur
    return cur or {"status": "idle"}


def postprocess(state: AssistantState) -> dict[str, Any]:
    """补齐播报语（不额外调模型）、给时效性内容打标、按场景归档。"""
    res = dict(state.get("result") or {})
    text = res.get("text") or ""
    out: dict[str, Any] = {}
    unfinished_practice = res.get("error") or res.get("status") in ("error", "needs_selection")
    unresolved_batch = any(
        isinstance(art, dict) and art.get("kind") == "exam_batch"
        and art.get("answered_count") == 0 and art.get("unresolved_count", 0) > 0
        for art in res.get("artifacts") or []
    )

    speech = res.get("speech") or ""
    # 播报语含时效性断言（年份、「尚未召开」这类）时**不能直接采用**。
    # 语音是一次性、不可回看的：说错一个年份，听的人没有机会核对。
    # 实测踩过：模型把「党的二十届三中全会尚未召开」放进了播报语，
    # 而该会 2024 年 7 月就已召开（它用的是过期知识）。
    #
    # 注意：不能只退回去压缩正文——正文里往往含同一句断言，
    # 那样等于没拦（第一版就是这样，被测试抓出来了）。
    # 必须逐句剔除含时效断言的句子。
    if speech and speech_tool.looks_like_stale_claim(speech):
        speech = speech_tool.strip_stale_claims(text)
        res["speech_note"] = "播报语含时效性断言，已剔除相关语句"
    if not speech:
        speech = speech_tool.truncate(speech_tool.to_plain(text))
    res["speech"] = speech
    out["result"] = res
    out["speech"] = speech

    # 时效性内容打标：**这是知识截止导致的硬限制，提示词治不好**。
    # 实测把「知识可能已过时」写进提示词后，模型照样三次断言
    # 「党的二十届三中全会尚未召开」（该会 2024 年 7 月已召开）。
    # 原因是它的训练数据截止在 2024 年 6 月前后，它是**真心**那么认为的，
    # 无法从内部判断自己过时。既然改不了它的判断，至少不能让结论显得确定。
    #
    # 直接把提示拼进正文，而不是交给前端渲染：这样无论前端怎么改版都必然可见，
    # 也不需要在 app.js 里加分支（那份文件当时正被另一处改动大改，不宜并发编辑）。
    if not unfinished_practice and not unresolved_batch and speech_tool.looks_like_stale_claim(text):
        warning = (
            "\n\n---\n\n⚠️ **这条回答含时效性判断**"
            "（涉及某会议/文件/政策的当前状态）。"
            "模型的知识存在截止时间，**该判断可能已过时，请以官方最新发布为准**。"
        )
        res["text"] = text + warning
        res["stale_warning"] = warning.strip()
        out["notes"] = list(out.get("notes") or []) + ["回答含时效性断言，已加提示"]

    # 推进会议状态
    meeting = next_meeting(state)
    if meeting is not None:
        out["meeting"] = meeting

    scene = (state.get("routing") or {}).get("scene") or "general"
    if unfinished_practice:
        out["archived_id"] = ""
    if text and scene in ("meeting", "exam") and not unfinished_practice:
        try:
            rid = store.archive(
                owner=state.get("owner") or "local",
                scene=scene,
                title=f"{scene}·产出",
                request_id=state.get("request_id") or "",
                content=text,
                source=state.get("text") or "",
            )
            out["archived_id"] = rid
            # 会议转写：逐段结构落库，供界面上人工改判说话人。
            # 落库后把 record 从返回值里摘掉——前端要的是「能不能改」和 id，
            # 整份逐段结构（可能上千段）没必要塞进每一条聊天响应里。
            for art in res.get("artifacts") or []:
                if art.get("kind") == "transcript" and art.get("record"):
                    store.save_transcript(
                        state.get("owner") or "local", rid, scene=scene, record=art["record"]
                    )
                    art.pop("record", None)
                    art["id"] = rid
                    art["editable"] = True
        except Exception as e:
            out["notes"] = [f"归档失败（已忽略）：{type(e).__name__}: {e}"]
    out["result"] = res

    return out
