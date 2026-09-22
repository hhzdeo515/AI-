"""锻炼指导 Agent：健康档案 + 器械识别 + 训练状态机。

P3：问卷建档、器械识别（照片 / 文字）。
P4：训练状态机与事件提醒（start_exercise / set_done / pain_report / end_workout）。

动作解析：编排层 action > 建档流程中（awaiting）> 文本事件识别 > 关键词 > 默认 advice。
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from ... import llm, speech
from ...schemas import (
    SCENE_FITNESS,
    STATUS_ERROR,
    STATUS_NEED_INPUT,
    STATUS_OK,
    Reply,
    Task,
)
from ..base import BaseAgent
from . import equipment, profile, prompts
from . import state as wk

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff"}

PROFILE_TRIGGERS = ("建档", "健康档案", "我的档案", "更新档案", "重新填写", "建立档案", "完善档案")
CANCEL_TRIGGERS = ("取消建档", "不建了", "退出建档", "算了", "不填了")
EQUIPMENT_TRIGGERS = ("器械", "器材", "怎么用", "用法", "这台", "这个机器", "这个设备", "怎么练")

#: 训练状态机处理的四个动作
WORKOUT_ACTIONS = frozenset(
    {"start_exercise", "set_done", "pain_report", "end_workout"}
)


def default_fitness() -> dict[str, Any]:
    """锻炼场景的会话状态。"""
    return {"awaiting": "", "draft": {}, "workout": wk.default_workout()}


class FitnessAgent(BaseAgent):
    scene = SCENE_FITNESS
    keywords = (
        "锻炼",
        "健身",
        "运动",
        "器械",
        "器材",
        "深蹲",
        "卧推",
        "硬拉",
        "跑步",
        "训练",
        "增肌",
        "减脂",
        "热身",
        "拉伸",
        "几组",
        "健康档案",
        "建档",
        "做完",
        "练完",
    )

    # ------------------------------------------------------------------ #
    def can_handle(self, task: Task) -> float:
        """除关键词外，命中内置器械名也算高置信度——器械名太多，不适合堆进关键词表。"""
        if task.scene_hint == self.scene:
            return 1.0
        if equipment.find(task.text or "")[0]:
            return 0.9
        return super().can_handle(task)

    # ------------------------------------------------------------------ #
    def handle(self, task: Task, state: dict[str, Any]) -> Reply:
        working = copy.deepcopy(state)
        fit = working.setdefault("fitness", default_fitness())
        if not isinstance(fit, dict):
            fit = default_fitness()
            working["fitness"] = fit

        action = self._infer_action(task, fit)
        try:
            if action == "profile":
                return self._profile_flow(task, working, fit)
            if action in WORKOUT_ACTIONS:
                return self._workout(task, working, fit, action)
            if action == "equipment":
                return self._equipment(task, working, fit)
            return self._advice(task, working, fit)
        except llm.LLMError as e:
            return self._reply(f"处理失败：{e}", STATUS_ERROR, action, working)

    # ------------------------------------------------------------------ #
    @staticmethod
    def _infer_action(task: Task, fit: dict[str, Any]) -> str:
        if task.action:
            return task.action
        text = task.text or ""

        # 建档进行中，所有输入都当作问卷答案（取消也走 profile 分支处理）
        if fit.get("awaiting"):
            return "profile"
        if any(w in text for w in CANCEL_TRIGGERS):
            return "profile"
        if any(w in text for w in PROFILE_TRIGGERS):
            return "profile"

        # 训练事件（文本形式；结构化事件由编排层直接给出 action）
        if wk.is_end(text):
            return "end_workout"
        if wk.is_pain_report(text):
            return "pain_report"
        if wk.is_set_done(text):
            return "set_done"

        started = wk.detect_start(text)
        if started and wk.is_active(fit):
            return "start_exercise"

        if task.files:
            return "equipment"
        if any(w in text for w in EQUIPMENT_TRIGGERS):
            return "equipment"
        if started:
            return "start_exercise"
        return "advice"

    # ------------------------------------------------------------------ #
    # 建档
    # ------------------------------------------------------------------ #
    def _profile_flow(
        self, task: Task, working: dict[str, Any], fit: dict[str, Any]
    ) -> Reply:
        text = (task.text or "").strip()
        draft = fit.setdefault("draft", {})
        if not isinstance(draft, dict):
            draft = {}
            fit["draft"] = draft

        if any(w in text for w in CANCEL_TRIGGERS):
            fit["awaiting"] = ""
            fit["draft"] = {}
            return self._reply(
                "已退出建档流程，之前填的内容没保存。随时说「建立健康档案」重新开始。",
                STATUS_OK,
                "profile",
                working,
            )

        awaiting = fit.get("awaiting", "")
        if awaiting:
            value, err = profile.parse_answer(awaiting, text)
            if err:
                return self._reply(err, STATUS_NEED_INPUT, "profile", working)
            draft[awaiting] = value
            fit["awaiting"] = ""

        keys = [f[0] for f in profile.FIELDS]
        missing = [k for k in keys if k not in draft]

        if not missing:
            path = profile.save(task.owner, draft)
            fit["draft"] = {}
            body = "健康档案已建立，之后所有训练建议都会结合它。\n\n" + profile.summarize(draft)
            bmi = profile.bmi(draft)
            if bmi:
                body += f"\n\nBMI：{bmi}"
            risk = profile.risk_notes(draft)
            if risk:
                body += (
                    f"\n\n注意：{risk}。有这些情况的训练安排请先咨询医生或线下教练，"
                    "我会在建议里避开高风险动作。"
                )
            body += f"\n\n档案文件：{path.name}"
            return self._reply(body, STATUS_OK, "profile", working)

        nxt = missing[0]
        fit["awaiting"] = nxt
        total = len(keys)
        done = total - len(missing)
        prefix = ""
        if not awaiting:
            prefix = (
                "我们先把健康档案建起来，之后所有建议都会结合它。"
                "中途想退出就说「取消建档」。\n\n"
            )
        return self._reply(
            f"{prefix}（{done + 1}/{total}）{profile.question_for(nxt)}",
            STATUS_OK,
            "profile",
            working,
        )

    # ------------------------------------------------------------------ #
    # 训练状态机
    # ------------------------------------------------------------------ #
    def _workout(
        self, task: Task, working: dict[str, Any], fit: dict[str, Any], action: str
    ) -> Reply:
        text = (task.text or "").strip()

        if action == "start_exercise":
            body, status = wk.start(fit, text)
            return self._reply(body, status, action, working)

        if action == "set_done":
            body, status = wk.set_done(fit, text)
            return self._reply(body, status, action, working)

        if action == "pain_report":
            body, status = wk.pain_report(fit, text)
            # 安全相关的内容必须念清楚，不能靠通用截断——截掉就医提示就麻烦了
            where = "未指明部位"
            pains = (fit.get("workout") or {}).get("pain") or []
            if pains:
                where = pains[-1].get("where") or where
            spoken = (
                f"已暂停训练。{where}不适已记录，请立即停止该动作，观察十到十五分钟。"
                "如果出现肿胀、变形、麻木或疼痛加重，请尽快就医。"
            )
            return self._reply(body, status, action, working, spoken=spoken)

        # end_workout：生成总结 + 下一步计划并归档
        facts, err = wk.end(fit)
        if err:
            return self._reply(err, STATUS_NEED_INPUT, action, working)
        raw = self._write_summary(facts, task.owner)
        body, spoken = speech.resolve(raw)
        return self._reply(
            body,
            STATUS_OK,
            action,
            working,
            archive=True,
            archive_title="训练总结",
            archive_source=wk.render_facts(facts),
            spoken=spoken,
        )

    @staticmethod
    def _write_summary(facts: dict[str, Any], owner: str) -> str:
        """让模型基于结构化事实 + 健康档案写总结与下一步计划；模型不可用时降级为事实清单。"""
        prof = profile.load(owner)
        user = (
            f"本次训练记录：\n{wk.render_facts(facts)}\n\n"
            f"用户健康档案：\n{profile.summarize(prof)}"
        )
        try:
            return llm.chat(
                [
                    {"role": "system", "content": prompts.WORKOUT_SUMMARY},
                    {"role": "user", "content": user},
                ],
                temperature=0.4,
            )
        except llm.LLMError:
            # 不编造建议，只给事实
            return (
                "（模型暂时不可用，先给你本次训练的事实记录）\n\n"
                + wk.render_facts(facts)
                + "\n\n（模型恢复后可以说「结束训练」重新生成总结与下一步计划。）"
            )

    # ------------------------------------------------------------------ #
    # 器械
    # ------------------------------------------------------------------ #
    def _equipment(
        self, task: Task, working: dict[str, Any], fit: dict[str, Any]
    ) -> Reply:
        images = [f for f in task.files if Path(f).suffix.lower() in IMAGE_EXT]
        prof = profile.load(task.owner)
        name: str | None = None
        info: dict[str, Any] | None = None
        prefix = ""

        if images:
            try:
                data = llm.vision_json(
                    prompts.VISION_EQUIPMENT, images, "请识别这张照片里的健身器械。"
                )
            except llm.LLMError as e:
                return self._reply(f"照片识别失败：{e}", STATUS_ERROR, "equipment", working)

            raw_name = str(data.get("name") or "").strip()
            note = str(data.get("note") or "").strip()
            if not raw_name:
                return self._reply(
                    "没能从照片里确认这是什么器械。"
                    + (f"\n{note}" if note else "")
                    + "\n\n可以直接告诉我器械名字，或者换个角度补拍一张。",
                    STATUS_NEED_INPUT,
                    "equipment",
                    working,
                )
            name, info = equipment.find(raw_name)
            if info is None:
                # 模型认出来了但不在内置知识库里：交给模型自己讲
                return self._reply(
                    self._free_equipment(raw_name, data, prof), STATUS_OK, "equipment", working
                )
            if str(data.get("confidence") or "").strip().lower() == "low":
                prefix = f"（照片不太清晰，我按「{name}」判断；如果不是，请直接告诉我器械名。）\n\n"
        else:
            name, info = equipment.find(task.text or "")
            if info is None:
                return self._reply(
                    "没识别出具体器械。可以告诉我器械名字，或者拍一张器械照片。\n\n"
                    "我目前内置了这些："
                    + "、".join(equipment.names()),
                    STATUS_NEED_INPUT,
                    "equipment",
                    working,
                )

        base = equipment.render(name, info)  # type: ignore[arg-type]
        if prof:
            try:
                body = llm.chat(
                    [
                        {"role": "system", "content": prompts.EQUIPMENT_PERSONALIZE},
                        {
                            "role": "user",
                            "content": (
                                f"器械：{name}\n\n通用资料：\n{base}\n\n"
                                f"用户健康档案：\n{profile.summarize(prof)}"
                            ),
                        },
                    ],
                    temperature=0.3,
                )
            except llm.LLMError:
                body = (
                    base
                    + "\n\n（个性化润色暂时不可用，下面是你的档案，请自行对照。）\n\n"
                    + profile.summarize(prof)
                )
        else:
            body = (
                base
                + "\n\n（你还没有健康档案。说「建立健康档案」，"
                "我可以按你的年龄、基础和目标调整重量与组数。）"
            )
        return self._reply(prefix + body, STATUS_OK, "equipment", working)

    @staticmethod
    def _free_equipment(raw_name: str, data: dict[str, Any], prof: dict[str, Any]) -> str:
        """模型识别出的器械不在知识库里时，让它自己结合档案作答。"""
        user = (
            f"照片里的器械，模型判断是「{raw_name}」。"
            f"照片中可见特征：{data.get('visible', '')}\n"
            f"不确定之处：{data.get('note', '')}\n"
        )
        if prof:
            user += f"\n用户健康档案：\n{profile.summarize(prof)}"
        try:
            return llm.chat(
                [
                    {"role": "system", "content": prompts.EQUIPMENT_ADVICE},
                    {"role": "user", "content": user},
                ],
                temperature=0.3,
            )
        except llm.LLMError as e:
            return f"识别到器械「{raw_name}」，但生成指导失败：{e}"

    # ------------------------------------------------------------------ #
    # 一般训练咨询
    # ------------------------------------------------------------------ #
    def _advice(
        self, task: Task, working: dict[str, Any], fit: dict[str, Any]
    ) -> Reply:
        prof = profile.load(task.owner)
        risk = profile.risk_notes(prof)
        user = f"用户健康档案：\n{profile.summarize(prof)}\n"
        if risk:
            user += f"\n特别注意：{risk}。\n"
        user += f"\n用户问题：{task.text}"

        body = llm.chat(
            [
                {"role": "system", "content": prompts.FITNESS_ADVICE},
                {"role": "user", "content": user},
            ],
            temperature=0.4,
        )
        if not prof:
            body += "\n\n（提示：说「建立健康档案」，我能给出更贴合你情况的建议。）"
        return self._reply(body, STATUS_OK, "advice", working)

    # ------------------------------------------------------------------ #
    @staticmethod
    def _reply(
        text: str,
        status: str,
        action: str,
        working: dict[str, Any],
        archive: bool = False,
        archive_title: str = "",
        archive_source: str = "",
        spoken: str = "",
    ) -> Reply:
        delta: dict[str, Any] = {
            "fitness": working.get("fitness", default_fitness()),
            "active_scene": SCENE_FITNESS,
        }
        if archive and archive_title:
            delta["_archive"] = {
                "scene": SCENE_FITNESS,
                "title": archive_title,
                "content": text,
                "source": archive_source,
            }
        return Reply(
            text=text,
            scene=SCENE_FITNESS,
            action=action,
            status=status,
            speech=spoken,
            state_delta=delta,
            archive=archive,
        )
