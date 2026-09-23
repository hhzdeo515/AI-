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
``profile_flow``     健康档案问卷（零 token 确定性状态机）
``dify_scene``       Dify 场景 Agent 执行（可选后端，失败回退 local_llm）
``local_llm``        本地场景回复
``postprocess``      播报语 + 归档
===================  ====================================================
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import config, dify_backend, llm, profile, search, store, transcribe, vision
from .ported import calc as calc_tool
from .ported import speech as speech_tool
from .routing import (
    device_context,
    infer_fitness_action,
    infer_meeting_action,
    route as route_decision,
)
from .state import AssistantState
from .tools import audio as audio_tool

#: 可送视觉模型的图片扩展名
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff"}

#: 可送 ASR 的音频扩展名（与 tools/audio.py 的处理范围一致）
AUDIO_EXT = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".amr", ".wma", ".mp4"}

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
    "fitness": (
        "你是锻炼指导 Agent。用户在运动中双手被占用，回答要短、可执行。\n"
        "只依据用户自述的信息给建议，没有的信息不要假设。\n"
        "重要：本设备没有心率等生理传感器，禁止给出心率区间、训练负荷、"
        "恢复度这类需要生理数据才能得出的结论；卡路里只能粗估并标注误差。\n"
        "用户报告疼痛/不适时：立即让其停止该动作，给处置建议，并说明何种情况必须就医；"
        "同时声明你不是医生。"
    ),
    "resource": (
        "你是资料检索 Agent。真正可用的检索与导出指令是："
        "「我有哪些资料」「导出最新一份成 Word」「导出 <ID前缀> 成 PDF」。\n"
        "在本次纯文本上下文中你无法直接访问资料库——明确说明这一点，"
        "不要编造任何资料标题、ID 或内容。"
    ),
    "general": (
        "你是本地智能助手，支持会议纪要、拍照解题、锻炼指导、资料检索四个场景。"
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
    """从**真实会话状态**推导「进行中的多轮流程」。

    为什么必须在这里推导：原来读的是 ``event["_sticky"]``，而那个键
    **没有任何地方写入过**——route_node 读它、dispatch 读它，两条路径
    都是死代码。后果实测过：用户说「继续」想从暂停恢复训练，
    因为命不中关键词、粘性又失效，被路由到 general，训练卡在暂停态出不来。

    来源是 checkpointer 里的 state（会话状态），不是 event（单次请求的输入）。
    """
    fitness = state.get("fitness") or {}
    workout = fitness.get("workout") or {}
    return {
        "meeting": bool((state.get("meeting") or {}).get("status") == "collecting"),
        "fitness": bool(
            fitness.get("awaiting")
            or workout.get("status") in ("active", "paused")
        ),
    }


def route_node(state: AssistantState) -> dict[str, Any]:
    """五级路由。传入 ``llm_router=None`` 时纯规则，永不发起模型调用。

    模型兜底通过 ``ROUTER`` 环境开关控制，默认开启但在测试里注入桩函数。
    """
    device = state.get("device") or {}
    text = state.get("text") or ""

    # 训练进行中要**跳过模型兜底**：这时候用户说的每句话几乎都是训练事件
    # （「做完一组」「歇好了」「膝盖疼」），而它们大多不含关键词——
    # 交给模型兜底既慢（每次多一次调用）又不可靠（可能被判到 general）。
    # 先做确定性动作推断，判得出动作就说明该留在 fitness。
    #
    # 建档问卷同理，而且更严格：用户答的是「28」「减脂」「无」这类碎片，
    # 一个关键词都不含。不粘住就会掉到 general 被当闲聊——而问卷还停在
    # 原地等一个用户以为已经答过的字段。
    fit_state = state.get("fitness") or {}
    in_profile = bool(fit_state.get("awaiting")) or profile.wants_profile(text)
    workout_status = (fit_state.get("workout") or {}).get("status")
    workout_action = ""
    if in_profile:
        workout_action = "profile"
    elif workout_status in ("active", "paused"):
        workout_action = infer_fitness_action(text)
        if not workout_action:
            # 判不出动作时**不粘**：避免「帮我算个数」在训练中被吞掉
            # （这正是基线踩过的坑，路由顺序才定成关键词优先于粘性）。
            workout_status = None

    sticky_flags_now = sticky_flags(state)
    if workout_action:
        sticky_flags_now["fitness"] = True

    ev = state.get("event") or {}
    router = _router_callable() if not workout_action else None
    decision = route_decision(
        text=text,
        event=ev,
        scene_hint=state.get("scene_hint"),
        sticky=sticky_flags_now,
        llm_router=router,
    )

    # 场景内的动作推断：路由只决定场景，动作要在这里补
    if not decision["action"] and decision["scene"] in ("meeting", "fitness"):
        decision = dict(decision)
        decision["action"] = (
            infer_meeting_action(text)
            if decision["scene"] == "meeting"
            else infer_fitness_action(text)
        )

    # 建档问卷：**动作必须显式写回结论**。
    #
    # 实测踩到的坑：粘性只恢复「场景」，不恢复「动作」。问卷第二轮用户答
    # 「28」，五级路由给出 ``{scene: fitness, action: "", source: sticky}``，
    # 而 `graph.dispatch` 判的是 ``action == "profile"`` —— 于是答题被送去
    # local_llm，问卷停在原地，用户以为答过了。上面那段补动作的逻辑也救不了：
    # 它只在 ``action`` 为空且场景是 meeting/fitness 时补，而 `infer_fitness_action`
    # 对「28」判不出任何动作，仍然是空。
    #
    # 所以这里不看路由结论，只看「问卷是不是正在进行」。
    if in_profile:
        decision = {
            "scene": "fitness",
            "action": "profile",
            "source": decision.get("source") or "sticky",
        }

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
    if not state.get("files"):
        for prefix in _ARITH_PREFIX:
            if not text.startswith(prefix):
                continue
            expr = text[len(prefix):].strip().lstrip(":：").strip().rstrip("？?。.=").strip()
            if not expr or set(expr) - _ARITH_OK_CHARS:
                break
            try:
                value = calc_tool.calculate(expr)
            except calc_tool.CalcError:
                break
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
    """带图片的解题请求走本地四步视觉链。

    为什么**不**转发 Dify：Dify 工作流的 ``start`` 目前只收文本。要么在本地做完
    VLM 精读再把文本带过去（会丢掉「终审重新看原图」这条关键设计），
    要么在本地图里完整跑完链路。这里选后者——图形推理题一旦只靠文字转述，
    行列位置与黑白关系就没了，题就废了。

    四步：精读（只记录）→ 初解（重看原图）→ 工具校验（零 token）→ 终审（再重看原图）。
    """
    images = [f for f in (state.get("files") or []) if Path(f).suffix.lower() in IMAGE_EXT]
    if not images:
        return {
            "result": {
                "text": "没有找到可识别的图片。请上传题目照片（png/jpg/webp）。",
                "backend": "local",
                "note": "附件里没有图片",
            }
        }

    text = state.get("text") or ""
    try:
        observation = vision.observe(text, images)
    except (vision.VisionError, llm.LLMError) as e:
        return {
            "result": {
                "text": f"图片识别失败：{e}",
                "backend": "local",
                "note": "视觉链路失败",
            }
        }

    # 时政类题目先联网核实。**必须用转写出来的题面去查，不能用用户那句
    # 「解这道题」**——后者不含任何可检索的关键词。
    #
    # 为什么非查不可：模型知识有截止时间，实测把「党的二十届三中全会」
    # 答成「尚未召开」（该会 2024 年 7 月已召开），而且提示词治不好。
    web_context = ""
    sources: list[dict] = []
    search_note = ""
    query = f"{text}\n{observation}".strip()
    if query and search.needs_search(query):
        try:
            found = search.search_answer(
                f"请核实以下题目涉及的事实与当前最新状态，并给出准确信息：\n\n{query}"
            )
            web_context = found["answer"]
            sources = found["sources"]
        except search.SearchError as e:
            # 检索失败要让用户知道，否则他们会以为这是核实过的答案
            search_note = f"本题涉及时效性信息，但联网核实未成功（{e}）"

    try:
        draft = vision.solve(text, observation, images, web_context=web_context)
        tool_result = vision.run_tools(draft)
        final = vision.review(
            text, draft, tool_result, images, web_context=web_context
        )
    except (vision.VisionError, llm.LLMError) as e:
        return {
            "result": {
                "text": f"图片识别失败：{e}",
                "backend": "local",
                "note": "视觉链路失败",
            }
        }

    body = vision.render(final, tool_result)
    if sources:
        body += search.format_sources(sources)
    if search_note:
        body += f"\n\n> ⚠️ {search_note}，以上结论可能已过时，请以官方最新发布为准。"
    return {
        "result": {
            "text": body,
            "speech": vision.speech_of(final),
            "backend": "local",
            "note": "" if final.get("answerable") else "模型判定无法确定作答",
            "artifacts": [
                {
                    "kind": "vision",
                    "module": final.get("module", ""),
                    "subtype": final.get("subtype", ""),
                    "answerable": bool(final.get("answerable")),
                    "tools_run": sorted(tool_result.keys()),
                }
            ],
        }
    }


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
    raw = summarize_transcript(
        transcript, instruction=MEETING_INSTRUCTION, system=SCENE_PROMPTS["meeting"]
    )
    body, _ = speech_tool.resolve(raw)
    return body


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
# 6. 建档问卷（零 token 确定性流程）
# --------------------------------------------------------------------------- #
#: 建档问卷里允许「顺手带上」的字段，按此顺序尝试推断。
#:
#: 只放**单位唯一**的字段：用户说「178」几乎必然是身高（厘米）。
#: ``age`` 也在这里，但必须带「岁」——裸数字「28」既可能是年龄也可能是
#: 每周天数，猜错就是把用户档案写错，而这种错误用户自己不会发现。
#:
#: 顺序即优先级：身高、体重在前，它们把句子里的数字「消费」掉，
#: 后面的字段就不会拿同一个数字去填（见 :func:`_numbers_by_unit`）。
_UNIT_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("height_cm", ("身高", "厘米", "cm")),
    ("weight_kg", ("体重", "公斤", "kg", "千克")),
    ("age", ("年龄",)),
    ("days_per_week", ("每周", "一周", "天练", "练天")),
)

#: 「28岁」这种数字带单位的写法。`\d` 与单位之间允许几个字：
#: 「178厘米」「178 厘米」「178的厘米」都要认。
_NUM_UNIT = r"(\d+(?:\.\d+)?)\s*(?:的)?\s*"


def _numbers_by_unit(text: str) -> dict[str, float]:
    """按单位就近取数，**每个数字只能用一次**。

    为什么不能对每个字段都拿整句去解析：``parse_field`` 里的 ``_as_float``
    取的是「句子里第一个数字」。实测「我178厘米，70公斤」会把身高和体重
    都填成 178，于是 BMI 算出 56.2 —— 用户看到一个荒谬的数字，却不知道
    是系统记错了体重。**这正是档案最不能有的错误**：它会被后续所有
    负荷建议继承。

    做法：先按单位抓出「数字+单位」的配对（如 ``70公斤``→70），
    返回 ``{单位词: 数值}``；取用后该数字从可用集合里划掉。
    """
    import re

    found: dict[str, float] = {}
    for _key, words in _UNIT_HINTS:
        for w in words:
            for m in re.finditer(_NUM_UNIT + re.escape(w), text):
                found.setdefault(w, float(m.group(1)))
            # 单位在前：「体重70」也常见
            for m in re.finditer(
                re.escape(w) + r"\s*(?:是|为|：|:)?\s*(\d+(?:\.\d+)?)", text
            ):
                found.setdefault(w, float(m.group(1)))
    return found


def _infer_extra(text: str, draft: dict[str, Any]) -> dict[str, Any]:
    """从一句话里顺带认出还没填的字段。认不出的不猜。"""
    out: dict[str, Any] = {}
    by_unit = _numbers_by_unit(text)
    used: set[float] = set()

    for key, words in _UNIT_HINTS:
        if key in draft or key in out:
            continue
        # 优先用带单位的数；同一个数不能被两个字段用掉
        value = None
        for w in words:
            v = by_unit.get(w)
            if v is not None and v not in used:
                value = v
                used.add(v)
                break
        if value is None:
            continue
        parsed, err = profile.parse_field(key, value)
        if not err:
            out[key] = parsed

    # 选项类字段（目标/基础/伤病/慢性病/器械）也顺手认一下：
    # 「我想增肌，膝盖有伤」一次能填两项。它们都有固定选项，误判风险低。
    for key, _label, kind, _opts in profile.FIELDS:
        if key in draft or key in out or kind not in ("choice", "list"):
            continue
        value, err = profile.parse_field(key, text)
        if not err:
            out[key] = value
    return out


def profile_flow(state: AssistantState) -> dict[str, Any]:
    """健康档案问卷。**零 token 纯函数驱动，不调模型。**

    为什么独立成节点而不是丢给 ``local_llm``：问卷的每一步都是确定性的
    ——解析、校验、决定下一个问什么、什么时候落库。交给模型只会更慢、
    更贵，而且它会「热心」地把用户没说的信息补上（那正是档案最不能有的东西）。

    回合协议：

    - 用户说「建立健康档案」→ 开场 + 第 1 问
    - 用户答一句 → 解析进 ``draft``，问下一个；答得不合规就原地重问
    - 填满 → ``store.save_profile`` 落库，回显档案 + BMI + 风险提示
    - 任何时候说「取消建档」→ 清空草稿（**已存在库里的档案不动**）

    未填完的草稿存在会话状态里（``fitness.draft``），由 checkpointer 落盘——
    眼镜断连后重连还能接着答，这是迁移到 LangGraph 换来的。
    """
    text = (state.get("text") or "").strip()
    fit = state.get("fitness") or {}
    awaiting = str(fit.get("awaiting") or "")
    draft = fit.get("draft") if isinstance(fit.get("draft"), dict) else {}

    # 退出：连库里的档案一起清掉吗？**不清**。
    # 「取消建档」的语义是「这次不填了」，不是「删除我的健康档案」——
    # 后者必须是另一个明确的动作，否则用户一句「算了」就把档案弄没了。
    if profile.wants_cancel(text):
        return {
            "fitness": {"awaiting": "", "draft": {}},
            "result": {
                "text": profile.cancelled_line(),
                "backend": "local",
                "note": "已退出建档",
            },
        }

    # 起点：只有触发词、没有答案
    if not awaiting:
        if not profile.wants_profile(text):
            # 走到了建档分支却既不是触发词也不是答案——不该发生，
            # 但真发生时回一句「没听懂」比静默吞掉好。
            return {
                "result": {
                    "text": "想建健康档案的话，直接说「建立健康档案」就行。",
                    "backend": "local",
                    "note": "建档分支未识别输入",
                }
            }
        missing = profile.missing_keys(draft)
        if not missing:
            # 档案已完整（手机上填过了）：回显现状，不要重新问一遍
            data = store.load_profile(state.get("owner") or "local")
            return {
                "fitness": {"awaiting": "", "draft": {}},
                "result": {
                    "text": "你的健康档案已经有了：\n\n" + profile.summarize(data),
                    "backend": "local",
                    "note": "档案已存在",
                },
            }
        text = ""  # 让 answer() 走「首次提问」那条路

    # 顺手推断：用户答身高时可能把体重也说了
    extra = _infer_extra(text, draft) if text else {}
    if extra:
        draft = {**draft, **extra}

    out = profile.answer(text, awaiting=awaiting, draft=draft)

    if out["done"]:
        data = out["draft"]
        notes: list[str] = []
        try:
            store.save_profile(state.get("owner") or "local", data, source="glasses")
        except Exception as e:  # noqa: BLE001
            # 落库失败必须说出来：用户以为建好了、眼镜端却读不到，
            # 这种「以为存上了」的静默失败最难查。
            notes.append(f"档案保存失败：{type(e).__name__}: {e}")
            return {
                "fitness": {"awaiting": "", "draft": data},
                "result": {
                    "text": f"{out['message']}\n\n⚠️ 但保存到服务端失败：{e}\n"
                    "草稿已留在本次会话里，请重试或改用手机端填写。",
                    "backend": "local",
                    "note": "档案落库失败",
                },
                "notes": notes,
            }
        return {
            "fitness": {"awaiting": "", "draft": {}},
            "result": {
                "text": out["message"],
                "backend": "local",
                "note": "健康档案已保存",
            },
        }

    return {
        "fitness": {"awaiting": out["awaiting"], "draft": out["draft"]},
        "result": {
            "text": out["message"],
            "backend": "local",
            "note": f"建档问卷 {profile.progress(out['draft'])[0]}/{len(profile.FIELD_KEYS)}",
        },
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

    # ---- 安全闸：训练因不适暂停后，不再让模型自由发挥 ----
    #
    # 实测事故：报告「膝盖有点疼」后（状态机已置 paused），用户接着说
    # 「做完一组」，回答仍是「好，休息30秒，准备下一组」——**在鼓励用户
    # 带着疼痛继续练**。状态机拒绝了计数，但回答是模型生成的，它看不到
    # 训练状态，所以照常给出鼓励性回复。
    #
    # 这类安全回复不能交给模型即兴发挥：改成确定性文案，并且明确说明
    # 「为什么没记这一组」以及「怎样才算可以继续」。
    workout = ((state.get("fitness") or {}).get("workout") or {})
    if workout.get("status") == "paused":
        action = str(rt.get("action") or "")
        where = workout.get("current") or "当前动作"
        done = int(workout.get("total_sets") or 0)
        # 「结束训练」必须放行——否则用户拿不到训练总结，被卡在暂停态出不来。
        # 「继续」也要给恢复确认，而不是复述暂停提示——状态这一轮已经切回
        # active 了，再回一句「当前处于暂停状态」会自相矛盾（实测发现）。
        if action == "resume_workout":
            text = (
                f"好，已恢复记录「{where}」，本次累计 {done} 组。\n\n"
                "如果再次出现不适，立刻停下并告诉我，不要硬撑。"
            )
            body, spoken = speech_tool.resolve(text)
            return {
                "result": {
                    "text": body,
                    "speech": spoken or "已恢复记录，有不舒服立刻停下。",
                    "backend": "local",
                    "note": "从暂停恢复",
                }
            }
        if action != "end_workout":
            if action == "set_done":
                text = (
                    f"**这一组没有记录。** 你刚才报告了不适，训练已暂停在「{where}」，"
                    f"本次累计 {done} 组。\n\n"
                    "请先确认：疼痛是否已经缓解？如果还疼，不要继续这个动作——"
                    "带痛训练会让损伤加重，恢复期更长。\n\n"
                    "确认没问题后再继续，可以直接说「继续」，我会恢复记录；"
                    "要结束就说「结束训练」。"
                )
            else:
                text = (
                    f"训练当前处于暂停状态（不适待确认），停在「{where}」，"
                    f"本次累计 {done} 组。\n\n"
                    "确认身体没有不适就说「继续」；要结束就说「结束训练」。"
                )
            body, spoken = speech_tool.resolve(text)
            return {
                "result": {
                    "text": body,
                    "speech": spoken or "训练已暂停，请先确认身体状况。",
                    "backend": "local",
                    "note": "训练暂停中，已拦截继续训练类回复",
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

    # ---- 健康档案注入：档案的**唯一用途**就是把建议落到用户身上 ----
    #
    # 只有健身场景注入。档案含伤病与慢性病，是敏感信息，没有理由出现在
    # 会议纪要或解题的提示词里。
    #
    # 读不到档案就什么都不加——**不编造默认值**。「假设用户 30 岁、无伤病」
    # 会让建议看起来个性化，实际全是凭空来的，而这恰恰是安全相关的字段。
    profile_block = ""
    if scene == "fitness":
        prof = store.load_profile(state.get("owner") or "local")
        if prof:
            profile_block = (
                "\n\n【用户健康档案】\n" + profile.summarize(prof)
                + "\n以上是用户自己填的信息，请结合它调整动作与负荷；"
                "档案里没写的不要假设。"
            )
            risk = profile.risk_notes(prof)
            if risk:
                profile_block += (
                    f"\n⚠️ {risk}——避免安排冲击该部位或高强度的动作，"
                    "并提示先咨询医生或线下教练。"
                )
        else:
            profile_block = (
                "\n\n【用户健康档案】尚未建立。只依据用户这次自述的信息给建议，"
                "不要假设年龄、体重或伤病；可以提一句「说『建立健康档案』我能给得更准」。"
            )
        system = system + profile_block

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
#: 训练动作词表。从「做深蹲」这类口语里取出动作名，供卡片显示。
_EXERCISES = (
    "深蹲", "卧推", "硬拉", "引体向上", "引体", "俯卧撑", "平板支撑", "弓步",
    "划船", "推举", "弯举", "卷腹", "臀桥", "跑步", "骑行", "游泳", "跳绳",
)


def _exercise_name(text: str) -> str:
    for e in _EXERCISES:
        if e in (text or ""):
            return e
    return "—"


def next_workout(state: AssistantState) -> dict[str, Any] | None:
    """按本轮路由动作推进训练状态；非锻炼场景返回 None（不动状态）。

    背景：Web 层复用基线的 UI，WORKOUT 卡片读 ``/api/state`` 的
    ``fitness.workout``。迁移到状态图后这个字段没有来源，卡片恒显示 IDLE，
    用户看不出训练到底有没有开始。这里补一个最小状态机，动作取自
    ``routing.ACTION_MAP`` 里 fitness 的四个：
    start_exercise / set_done / pain_report / end_workout。

    纯函数、不依赖模型，可直接单测。
    """
    routing = state.get("routing") or {}
    if routing.get("scene") != "fitness":
        return None

    action = str(routing.get("action") or "")
    cur = dict((state.get("fitness") or {}).get("workout") or {})
    status = cur.get("status") or "idle"
    sets = int(cur.get("total_sets") or 0)

    if action == "start_exercise":
        return {
            "status": "active",
            "current": _exercise_name(state.get("text") or ""),
            "total_sets": 0,
        }
    if action == "set_done":
        # 没开始训练时的「做完一组」不计入，避免凭空冒出组数
        if status == "active":
            cur["total_sets"] = sets + 1
        else:
            cur.setdefault("status", "idle")
            cur.setdefault("total_sets", sets)
        return cur
    if action == "pain_report":
        cur["status"] = "paused"
        return cur
    if action == "resume_workout":
        # 只从「暂停」恢复；idle 时什么都不做，
        # 否则一句「继续」会凭空开出一场训练、组数从 0 重新算
        if status == "paused":
            cur["status"] = "active"
        return cur
    if action == "end_workout":
        cur["status"] = "idle"
        return cur
    return cur or {"status": "idle"}


def next_meeting(state: AssistantState) -> dict[str, Any] | None:
    """按本轮路由动作推进会议状态；非会议场景返回 None。

    同 ``next_workout``：Web 层复用基线的 UI，MEETING 卡片按
    ``status == "collecting"`` 显示 LISTENING 与转写区，读的是
    ``/api/state`` 的 ``meeting`` 字段，迁移到图之后同样没有来源。
    """
    routing = state.get("routing") or {}
    if routing.get("scene") != "meeting":
        return None

    action = str(routing.get("action") or "")
    cur = dict(state.get("meeting") or {})
    transcript = cur.get("transcript") or ""
    text = (state.get("text") or "").strip()

    if action == "start":
        # 本轮这句就是会议的第一段转写
        return {"status": "collecting", "transcript": text}
    if action == "append":
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
    if speech_tool.looks_like_stale_claim(text):
        warning = (
            "\n\n---\n\n⚠️ **这条回答含时效性判断**"
            "（涉及某会议/文件/政策的当前状态）。"
            "模型的知识存在截止时间，**该判断可能已过时，请以官方最新发布为准**。"
        )
        res["text"] = text + warning
        res["stale_warning"] = warning.strip()
        out["notes"] = list(out.get("notes") or []) + ["回答含时效性断言，已加提示"]

    # 推进设备形态状态：前端卡片读 /api/state 的 fitness / meeting
    workout = next_workout(state)
    if workout is not None:
        out["fitness"] = {"workout": workout}
    meeting = next_meeting(state)
    if meeting is not None:
        out["meeting"] = meeting

    scene = (state.get("routing") or {}).get("scene") or "general"
    if text and scene in ("meeting", "exam", "fitness"):
        try:
            rid = store.archive(
                owner=state.get("owner") or "local",
                scene=scene,
                title=f"{scene}·产出",
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
