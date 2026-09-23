"""编排层：规则优先路由 + LLM 兜底 + Agent 分发。

核心思路：把"问答型"和"主动提醒型"统一成同一条
`event -> orchestrator -> agent -> Reply` 事件流。
路由尽量零 token 完成，只有规则都命中不了才问模型。
"""

from __future__ import annotations

import importlib.util
import json
from dataclasses import asdict
from typing import Any

from . import config, dify_backend, llm, progress, session, speech
from .agents.base import BaseAgent
from .schemas import (
    SCENE_FITNESS,
    SCENE_GENERAL,
    SCENE_MEETING,
    STATUS_DUPLICATE,
    STATUS_ERROR,
    Reply,
    Task,
)

#: 结构化事件的确定性映射，零 token。scene 为 None 表示取自事件的 scene 字段。
ACTION_MAP: dict[str, tuple[str | None, str]] = {
    "start_meeting": ("meeting", "start"),
    "append_meeting": ("meeting", "append"),
    "summarize_meeting": ("meeting", "summarize"),
    "stop_meeting": ("meeting", "stop"),
    "solve_captured_question": ("exam", "solve"),
    "start_exercise": ("fitness", "start_exercise"),
    "set_done": ("fitness", "set_done"),
    "pain_report": ("fitness", "pain_report"),
    "end_workout": ("fitness", "end_workout"),
    "export_resource": ("resource", "export"),
    "list_resources": ("resource", "list"),
    "stop_playback": (None, "stop_playback"),
    "switch_scene": (None, "switch_scene"),
}

#: 端侧 1–3B 意图枚举 → 场景。契约见 `docs/端侧适配设计_眼镜.md` §3.1。
#: 与 Dify DSL 里 normalize 节点的映射表保持一致——两边都改才算改契约。
DEVICE_INTENT_SCENES: dict[str, str] = {
    "meeting_start": SCENE_MEETING,
    "meeting_append": SCENE_MEETING,
    "meeting_summarize": SCENE_MEETING,
    "meeting_stop": SCENE_MEETING,
    "solve": "exam",
    "train_start": SCENE_FITNESS,
    "train_done": SCENE_FITNESS,
    "train_pain": SCENE_FITNESS,
    # 无场景语义：switch_scene 场景取自事件、stop/cancel/wake 不产生请求
    "switch_scene": "",
    "stop": "",
    "cancel": "",
    "wake": "",
}

#: 端侧判定可信阈值。低于此值的 device 意图只作证据、不作提示。
DEVICE_CONFIDENCE_FLOOR = 0.75
#: 安全相关意图永不降级（与 fitness 的「不适即停」一致）。
DEVICE_NEVER_DOWNGRADE = frozenset({"train_pain"})

ROUTER_PROMPT = """你是智能助手总控，只输出一个 JSON 对象，不要代码围栏。
字段：scene, action, reason。
scene 只能取 meeting / exam / fitness / resource / general：
- 会议记录、会议纪要、会议转写、录音整理 -> meeting
- 题目、解题、答案、讲解、计算、上传的题目图片 -> exam
- 健身、锻炼、运动、器械、动作、几组、深蹲、卧推、跑步、身体不适 -> fitness
- 导出、下载、查找历史资料、我有哪些记录、把刚才的结果转成文件 -> resource
- 其他日常问答 -> general
action 只能取 start / append / summarize / stop / solve / start_exercise / set_done /
pain_report / end_workout / export / list / answer。
拿不准时 scene 填 general、action 填 answer。
只输出 JSON，例如 {"scene":"meeting","action":"append","reason":"用户在提交会议转写"}"""

#: 已实现的场景 Agent 模块（缺失的会被自动跳过，便于分阶段开发）
_AGENT_SPECS = (
    ("meeting", "MeetingAgent"),
    ("exam", "ExamAgent"),
    ("fitness", "FitnessAgent"),
    ("resource", "ResourceAgent"),
)


def _load_agents() -> list[BaseAgent]:
    """加载已实现的场景 Agent；尚未实现的模块自动跳过。"""
    agents: list[BaseAgent] = []
    for mod, cls in _AGENT_SPECS:
        name = f"{__package__}.agents.{mod}.agent"
        try:
            spec = importlib.util.find_spec(name)
        except ModuleNotFoundError:
            # 父包（如 agents.exam）尚未创建时 find_spec 会抛异常而非返回 None
            continue
        if spec is None:
            continue
        module = importlib.import_module(name)
        agents.append(getattr(module, cls)())

    from .agents.general import GeneralAgent

    agents.append(GeneralAgent())
    return agents


class Orchestrator:
    """全局唯一的编排入口。"""

    def __init__(self) -> None:
        config.ensure_dirs()
        session.init()
        self.agents = _load_agents()
        self._fallback = next(a for a in self.agents if a.scene == SCENE_GENERAL)

    # ------------------------------------------------------------------ #
    # 路由
    # ------------------------------------------------------------------ #
    def route(self, task: Task, state: dict[str, Any] | None = None) -> tuple[str, str, str]:
        """返回 (scene, action, 来源)。来源用于调试：event / hint / sticky / keyword / llm。"""
        ev = task.event or {}

        # 1) 结构化事件：确定性映射，零 token
        sa = str(ev.get("semantic_action", "")).strip()
        if sa in ACTION_MAP:
            scene, action = ACTION_MAP[sa]
            if scene is None:  # switch_scene：场景取自事件
                scene = str(ev.get("scene") or SCENE_GENERAL).strip()
                if scene not in config.SCENES:
                    scene = SCENE_GENERAL
            return scene, action, "event"

        # 2) 用户显式指定场景
        if task.scene_hint and task.scene_hint in config.SCENES:
            return task.scene_hint, "", "hint"

        # 3) 关键词命中。必须排在粘性场景之前：
        #    否则会议进行中一句"计算 (18+24)*3"会被当成会议内容吞掉。
        best: BaseAgent | None = None
        best_score = 0.0
        for a in self.agents:
            score = a.can_handle(task)
            if score > best_score:
                best, best_score = a, score
        if best is not None and best_score >= 0.8:
            return best.scene, "", "keyword"

        # 4) 粘性场景：进行中的多轮流程，后续输入默认仍归该场景。
        #    会议记录：全部输入当会议内容；说"结束会议"退出。
        #    锻炼建档：全部输入当问卷答案；说"取消建档"退出。
        #    训练进行中：全部输入当训练事件；说"结束训练"退出。
        if state:
            m = state.get("meeting")
            if isinstance(m, dict) and m.get("status") == "collecting":
                return SCENE_MEETING, "", "sticky"
            f = state.get("fitness")
            if isinstance(f, dict):
                if f.get("awaiting"):
                    return SCENE_FITNESS, "", "sticky"
                w = f.get("workout")
                if isinstance(w, dict) and w.get("status") in ("active", "paused"):
                    return SCENE_FITNESS, "", "sticky"

        # 5) LLM 兜底
        return self._llm_route(task)

    def _llm_route(self, task: Task) -> tuple[str, str, str]:
        payload = (
            f"当前请求：{task.text}\n"
            f"事件：{json.dumps(task.event or {}, ensure_ascii=False)}\n"
            f"附带文件数：{len(task.files)}"
        )
        try:
            data = llm.json_chat(
                [
                    {"role": "system", "content": ROUTER_PROMPT},
                    {"role": "user", "content": payload},
                ]
            )
        except llm.LLMError:
            return SCENE_GENERAL, "answer", "llm_failed"

        scene = str(data.get("scene", "")).strip()
        action = str(data.get("action", "")).strip()
        if scene not in config.SCENES:
            scene = SCENE_GENERAL
        return scene, action, "llm"

    # ------------------------------------------------------------------ #
    # 分发
    # ------------------------------------------------------------------ #
    def _agent_for(self, scene: str) -> BaseAgent:
        for a in self.agents:
            if a.scene == scene:
                return a
        return self._fallback

    def handle(self, task: Task) -> Reply:
        config.ensure_dirs()
        session.init()

        # 请求级去重：同 request_id 直接返回上次结果
        if task.request_id:
            cached = session.receipt(task.owner, task.request_id, "handle")
            if cached:
                data = dict(cached)
                data["status"] = STATUS_DUPLICATE
                return Reply(**data)

        state = session.load_state(task.owner, task.session_id)
        scene, action, source = self.route(task, state)
        task.action = action

        # 路由遥测：端侧判对率的数据来源。必须在执行之前记录，
        # 这样即使执行失败也留下了路由判定的事实。
        self._record_routing(task, scene, action, source)

        agent = self._agent_for(scene)
        progress.begin(task.request_id, scene)
        reply = self._execute(task, state, agent, scene, action, source)
        progress.finish(task.request_id, error=(reply.status == STATUS_ERROR))

        # 语音播报兜底：Agent 没给就用规则压缩（不调模型，测试里也安全）
        if not reply.speech:
            reply.speech = speech.truncate(speech.to_plain(reply.text))

        self._apply(task, state, reply)

        if task.request_id:
            session.remember(task.owner, task.request_id, "handle", asdict(reply))
        return reply

    # ------------------------------------------------------------------ #
    # 端侧上下文
    # ------------------------------------------------------------------ #
    @staticmethod
    def device_context(task: Task) -> dict[str, Any]:
        """从 event 里读端侧 1–3B 的判定结果。

        契约：`event.device = {intent, confidence}`（见设计文档 §4.2）。
        也接受扁平的 `event.device_intent`，便于设备端少一层嵌套。
        未知枚举一律静默忽略——绝不能因为设备多发了个字段就影响既有路由。
        """
        ev = task.event or {}
        dev = ev.get("device")
        dev = dev if isinstance(dev, dict) else {}

        intent = str(dev.get("intent") or ev.get("device_intent") or "").strip()
        raw_conf = dev.get("confidence", ev.get("device_confidence"))
        confidence: float | None
        try:
            confidence = None if raw_conf is None or raw_conf == "" else float(raw_conf)
        except (TypeError, ValueError):
            confidence = None

        device_scene = DEVICE_INTENT_SCENES.get(intent, "")
        if not device_scene:
            trusted: bool | None = False if intent else None
        elif intent in DEVICE_NEVER_DOWNGRADE:
            trusted = True
        else:
            trusted = (confidence or 0.0) >= DEVICE_CONFIDENCE_FLOOR

        return {
            "intent": intent,
            "confidence": confidence,
            "scene": device_scene,
            "trusted": trusted,
        }

    def _record_routing(self, task: Task, scene: str, action: str, source: str) -> None:
        try:
            d = self.device_context(task)
            session.record_routing(
                owner=task.owner,
                session_id=task.session_id,
                request_id=task.request_id,
                scene=scene,
                action=action,
                source=source,
                device_intent=d["intent"],
                device_confidence=d["confidence"],
                device_scene=d["scene"],
                device_trusted=d["trusted"],
            )
        except Exception:
            # 遥测是横切关注点，任何异常都不该影响主链路
            pass

    # ------------------------------------------------------------------ #
    # 执行：本地 Agent 或 Dify 后端
    # ------------------------------------------------------------------ #
    def _execute(
        self,
        task: Task,
        state: dict[str, Any],
        agent: BaseAgent,
        scene: str,
        action: str,
        source: str,
    ) -> Reply:
        """按 `EXEC_BACKEND` 决定场景 Agent 在哪执行。路由永远已经完成。"""
        if config.EXEC_BACKEND == "dify":
            return self._execute_dify(task, state, agent, scene, action, source)
        return self._execute_local(task, state, agent, scene, action)

    def _execute_local(
        self,
        task: Task,
        state: dict[str, Any],
        agent: BaseAgent,
        scene: str,
        action: str,
    ) -> Reply:
        try:
            return agent.handle(task, state)
        except Exception as e:  # Agent 内部异常不应炸掉整个服务
            return Reply(
                text=f"处理失败：{type(e).__name__}: {e}",
                scene=scene,
                action=action,
                status=STATUS_ERROR,
            )

    def _execute_dify(
        self,
        task: Task,
        state: dict[str, Any],
        agent: BaseAgent,
        scene: str,
        action: str,
        source: str,
    ) -> Reply:
        """把场景执行转发给 Dify 工作流。

        刻意只在「纯文本、无附件」时转发：图片/音频需要先经本地
        ASR / VLM 预处理，而那条路径涉及会话状态与归档，绕开它会破坏
        既有约定。带附件时保持本地执行，行为可预期。

        另外：本地有状态机的场景（会议收集中、训练进行中、建档问卷）
        必须留在本地——Dify 侧没有这些状态。粘性路由（source="sticky"）
        正是这些情况的标志。
        """
        d = self.device_context(task)
        reason = ""
        if task.files:
            reason = "本次请求带附件，需本地 ASR/VLM 预处理"
        elif source == "sticky":
            reason = "处于多轮有状态流程中，Dify 侧无此会话状态"

        if reason:
            reply = self._execute_local(task, state, agent, scene, action)
            reply.text = f"{reply.text}\n\n（未转发 Dify：{reason}。）" if reply.text else reply.text
            return reply

        try:
            text = dify_backend.run_scene(
                text=task.text,
                scene=scene,
                device_intent=d["intent"],
                device_confidence=d["confidence"],
                user=task.owner,
            )
        except dify_backend.DifyError as e:
            if not config.DIFY_FALLBACK_LOCAL:
                return Reply(
                    text=f"Dify 后端执行失败：{e}",
                    scene=scene,
                    action=action,
                    status=STATUS_ERROR,
                )
            reply = self._execute_local(task, state, agent, scene, action)
            reply.text = f"{reply.text}\n\n（Dify 后端不可用，已回退本地：{e}）"
            return reply

        return Reply(
            text=text,
            scene=scene,
            action=action or "answer",
            speech="",  # 由编排层统一规则压缩，避免 Dify 侧再产生一次差异
            state_delta={"active_scene": scene},
        )

    # ------------------------------------------------------------------ #
    @staticmethod
    def _apply(task: Task, state: dict[str, Any], reply: Reply) -> None:
        """把 Reply 的 state_delta 写回会话，并处理归档。"""
        delta = dict(reply.state_delta or {})
        archive = delta.pop("_archive", None)

        if delta:
            state.update(delta)
            session.save_state(task.owner, task.session_id, state)

        if archive:
            rid = session.archive(
                task.owner,
                archive.get("scene", reply.scene),
                archive.get("title", "资料"),
                archive.get("content", ""),
                archive.get("source", ""),
            )
            reply.artifacts.append(
                {"kind": "resource", "label": archive.get("title", "资料"), "id": rid}
            )
