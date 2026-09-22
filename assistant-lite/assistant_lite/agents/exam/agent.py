"""拍照解题 Agent：视觉精读 -> 初解 -> 工具校验 -> 终审 -> 渲染。

提示词直接复制自旧 dify-assistant/exam_prompts.py（已在本机验证过公考六类题）。
链路改写自旧 Dify Chatflow 的 homework 分支，但去掉了 Dify 依赖，改为进程内顺序调用。

注意：初解与终审是两次独立的模型调用，且都会重新查看原图，不是一次调用的自我复述。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ... import llm, progress
from ...schemas import (
    SCENE_EXAM,
    STATUS_ERROR,
    STATUS_NEED_INPUT,
    STATUS_OK,
    Reply,
    Task,
)
from ...tools import calc as calc_tool
from ...tools import grids as grids_tool
from ..base import BaseAgent
from . import prompts

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff"}

VISION_EXTRA = "\n非考试图片（白板/路牌等）忠实记录实际内容，不强行套用考题结构。"

#: 「计算 1+2*3」这类纯算术请求，直接走确定性工具，不调模型
_ARITH_RE = re.compile(r"^(?:计算|算一下|算算|求)\s*[:：]?\s*(.+)$")
_ARITH_OK = re.compile(r"^[0-9+\-*/%().\s]+$")


class ExamAgent(BaseAgent):
    scene = SCENE_EXAM
    keywords = (
        "这道题",
        "解题",
        "做题",
        "答案",
        "讲解",
        "计算",
        "题目",
        "题干",
        "选项",
        "公考",
        "行测",
        "图形推理",
        "资料分析",
        "数量关系",
        "判断推理",
        "言语理解",
    )

    # ------------------------------------------------------------------ #
    # 主入口
    # ------------------------------------------------------------------ #
    def handle(self, task: Task, state: dict[str, Any]) -> Reply:
        images = [f for f in task.files if Path(f).suffix.lower() in IMAGE_EXT]
        text = (task.text or "").strip()

        # 纯算术：零 token，直接用确定性工具
        pure = self._pure_arithmetic(text, images)
        if pure is not None:
            return pure

        if not images and not text:
            return self._fail("请提供题目文字，或上传题目图片。", STATUS_NEED_INPUT)

        progress.report(task.request_id, "capture")

        # 1) 视觉精读（有图才做）
        observation = ""
        if images:
            progress.report(task.request_id, "recognize")
            try:
                observation = llm.vision(
                    text or "请识别这道题的全部题干与选项。",
                    images,
                    system=prompts.VISION + VISION_EXTRA,
                    temperature=0.1,
                )
            except llm.LLMError as e:
                return self._fail(f"图片识别失败：{e}")

        # 2) 初解
        progress.report(task.request_id, "solve")
        try:
            draft = self._solve(text, observation, images)
        except llm.LLMError as e:
            return self._fail(f"初解失败：{e}")

        # 3) 工具校验（确定性，模型结果只作参考）
        tool_result = self._run_tools(draft)

        # 4) 终审：重新查看原图
        progress.report(task.request_id, "verify")
        try:
            final = self._review(text, draft, tool_result, images)
        except llm.LLMError as e:
            return self._fail(f"终审失败：{e}")

        # 5) 渲染
        body = self._render(final, tool_result)
        answerable = bool(final.get("answerable"))

        delta: dict[str, Any] = {
            "active_scene": SCENE_EXAM,
            "last_question": text or observation[:500],
            "last_answer": body,
        }
        if answerable:
            # 与 meeting / fitness 用同一套归档约定：编排层看到 _archive 才写资料库。
            # 之前这里只塞了个假的 resource artifact，结果题解根本没落库。
            delta["_archive"] = {
                "scene": SCENE_EXAM,
                "title": f"题解·{final.get('module') or '题目'}",
                "content": body,
                "source": observation or text,
            }

        return Reply(
            text=body,
            scene=SCENE_EXAM,
            action="solve",
            status=STATUS_OK,
            state_delta=delta,
            archive=answerable,
        )

    # ------------------------------------------------------------------ #
    # 纯算术快路径
    # ------------------------------------------------------------------ #
    @staticmethod
    def _pure_arithmetic(text: str, images: list[str]) -> Reply | None:
        if images or not text:
            return None
        m = _ARITH_RE.match(text)
        if not m:
            return None
        expr = m.group(1).strip().rstrip("？?。.=").strip()
        if not expr or not _ARITH_OK.match(expr):
            return None
        try:
            value = calc_tool.calculate(expr)
        except calc_tool.CalcError:
            return None
        return Reply(
            text=f"{expr} = {value}",
            scene=SCENE_EXAM,
            action="calculate",
            status=STATUS_OK,
            state_delta={"active_scene": SCENE_EXAM},
        )

    # ------------------------------------------------------------------ #
    # 模型调用
    # ------------------------------------------------------------------ #
    @staticmethod
    def _json_call(
        prompt: str,
        user_text: str,
        images: list[str],
        temperature: float = 0.1,
        retries: int = 1,
    ) -> dict:
        """要求模型输出 JSON；解析失败追加纠正消息重试一次。"""
        user_text = user_text
        last: Exception | None = None
        for attempt in range(retries + 1):
            if images:
                raw = llm.vision(user_text, images, system=prompt, temperature=temperature)
            else:
                raw = llm.chat(
                    [
                        {"role": "system", "content": prompt},
                        {"role": "user", "content": user_text},
                    ],
                    temperature=temperature,
                )
            try:
                data = json.loads(llm.strip_fences(raw))
                if isinstance(data, dict):
                    return data
                last = llm.LLMError("期望 JSON 对象")
            except json.JSONDecodeError as e:
                last = e
            if attempt < retries:
                user_text = (
                    user_text
                    + "\n\n【上次输出不是合法 JSON】请只输出一个 JSON 对象，"
                    "不要任何解释文字、不要 Markdown 围栏。"
                )
        raise llm.LLMError(f"模型未返回合法 JSON：{last}")

    def _solve(self, text: str, observation: str, images: list[str]) -> dict:
        user = f"用户请求：{text or '请解答这道题'}\n\n图片观察记录：\n{observation or '（无图片）'}"
        return self._json_call(prompts.SOLVER, user, images, temperature=0.1)

    def _review(
        self, text: str, draft: dict, tool_result: dict, images: list[str]
    ) -> dict:
        user = (
            f"用户请求：{text or '请解答这道题'}\n\n"
            f"初解结果：\n{json.dumps(draft, ensure_ascii=False, indent=2)}\n\n"
            f"程序工具校验结果：\n{json.dumps(tool_result, ensure_ascii=False, indent=2)}\n\n"
            "请重新直接查看原图核对，并给出终审结论。"
        )
        return self._json_call(prompts.REVIEWER, user, images, temperature=0.1)

    # ------------------------------------------------------------------ #
    # 工具校验
    # ------------------------------------------------------------------ #
    @staticmethod
    def _run_tools(draft: dict) -> dict:
        """把初解里给出的算式与黑白格交给确定性工具执行。"""
        result: dict[str, Any] = {}
        calcs = draft.get("calculations")
        if isinstance(calcs, list) and calcs:
            exprs = [
                c.get("expression", "")
                for c in calcs
                if isinstance(c, dict) and c.get("expression")
            ]
            if exprs:
                result["calculations"] = calc_tool.calculate_many(exprs)

        grid = draft.get("binary_grid")
        if isinstance(grid, dict) and grid:
            result["grid_checks"] = grids_tool.check_grids(grid)
        return result

    # ------------------------------------------------------------------ #
    # 渲染
    # ------------------------------------------------------------------ #
    @staticmethod
    def _render(final: dict, tool_result: dict) -> str:
        module = final.get("module", "")
        subtype = final.get("subtype", "")

        if not final.get("answerable"):
            parts = ["**这道题暂时无法确定作答。**"]
            if module or subtype:
                parts.append(f"分类：{module} / {subtype}")
            if final.get("needed"):
                parts.append(f"\n**需要补充**\n{final['needed']}")
            if final.get("review_notes"):
                parts.append(f"\n**已完成的核对**\n{final['review_notes']}")
            return "\n".join(parts)

        parts = [f"**答案：{final.get('answer', '')}**"]
        if module or subtype:
            parts.append(f"分类：{module} / {subtype}")
        if final.get("explanation"):
            parts.append(f"\n**解析**\n{final['explanation']}")

        calcs = tool_result.get("calculations")
        if calcs:
            lines = []
            for c in calcs:
                if "result" in c:
                    lines.append(f"  {c['expression']} = {c['result']}")
                else:
                    lines.append(f"  {c['expression']} -> 计算失败：{c.get('error', '')}")
            parts.append("\n**程序计算校验**\n" + "\n".join(lines))

        grid = tool_result.get("grid_checks")
        if grid and grid.get("status") == "checked":
            cands = grid.get("candidates") or []
            if cands:
                lines = [
                    f"  [{c['direction']}] {c['rule']} -> 预测 {c['prediction']}，"
                    f"匹配选项 {c['matching_options'] or '无'}"
                    for c in cands
                ]
                parts.append("\n**黑白格逐格运算校验**\n" + "\n".join(lines))
            else:
                parts.append("\n**黑白格校验**：没有任何候选运算能解释已知行列，需回看原图另找规律。")

        if final.get("review_notes"):
            parts.append(f"\n**核对说明**\n{final['review_notes']}")
        return "\n".join(parts)

    # ------------------------------------------------------------------ #
    @staticmethod
    def _fail(msg: str, status: str = STATUS_ERROR) -> Reply:
        return Reply(text=msg, scene=SCENE_EXAM, action="solve", status=status)
