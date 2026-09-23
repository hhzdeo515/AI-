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

from . import config, dify_backend, llm, store, vision
from .ported import calc as calc_tool
from .ported import speech as speech_tool
from .routing import device_context, infer_meeting_action, route as route_decision
from .state import AssistantState

#: 可送视觉模型的图片扩展名
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff"}

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
def route_node(state: AssistantState) -> dict[str, Any]:
    """五级路由。传入 ``llm_router=None`` 时纯规则，永不发起模型调用。

    模型兜底通过 ``ROUTER`` 环境开关控制，默认开启但在测试里注入桩函数。
    """
    ev = state.get("event") or {}
    device = state.get("device") or {}

    # 粘性流程来自图自身的会话状态（由 checkpointer 提供）
    sticky = ev.get("_sticky") or {}
    sticky_flags = {
        "meeting": bool(sticky.get("meeting")),
        "fitness": bool(sticky.get("fitness")),
    }

    router = _router_callable()
    decision = route_decision(
        text=state.get("text") or "",
        event=ev,
        scene_hint=state.get("scene_hint"),
        sticky=sticky_flags,
        llm_router=router,
    )

    # 会议场景补一个动作推断（路由只决定场景）
    if decision["scene"] == "meeting" and not decision["action"]:
        decision = dict(decision)
        decision["action"] = infer_meeting_action(state.get("text") or "")

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
        draft = vision.solve(text, observation, images)
        tool_result = vision.run_tools(draft)
        final = vision.review(text, draft, tool_result, images)
    except (vision.VisionError, llm.LLMError) as e:
        return {
            "result": {
                "text": f"图片识别失败：{e}",
                "backend": "local",
                "note": "视觉链路失败",
            }
        }

    body = vision.render(final, tool_result)
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
# 7. 本地场景回复
# --------------------------------------------------------------------------- #
def local_llm(state: AssistantState) -> dict[str, Any]:
    """本地场景回复。纯文本上下文，不假装已实现视觉/检索链路。"""
    rt = state.get("routing") or {}
    scene = rt.get("scene") or "general"
    system = SCENE_PROMPTS.get(scene, SCENE_PROMPTS["general"])

    user = (
        f"用户输入：{state.get('text') or ''}\n"
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
def postprocess(state: AssistantState) -> dict[str, Any]:
    """补齐播报语（不额外调模型）并按场景归档。"""
    res = dict(state.get("result") or {})
    text = res.get("text") or ""
    out: dict[str, Any] = {}

    if not res.get("speech"):
        res["speech"] = speech_tool.truncate(speech_tool.to_plain(text))
    out["result"] = res
    out["speech"] = res["speech"]

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
        except Exception as e:
            out["notes"] = [f"归档失败（已忽略）：{type(e).__name__}: {e}"]

    return out
