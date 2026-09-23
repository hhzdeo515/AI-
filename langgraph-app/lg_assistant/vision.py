"""视觉解题链：精读 → 初解 → 工具校验 → 终审 → 渲染。

移植自 `assistant-lite/assistant_lite/agents/exam/agent.py` 的确定性链路，
但用 LangGraph 的节点表达，并把「初解/终审各自重新查看原图」这条关键设计保留：

    初解与终审是**两次独立的模型调用，且都重新查看原图**，
    不是一次调用的自我复述——转述会丢信息，图形题尤其致命。

四个步骤分别对应图上的四个节点，因此每一步都可单独测试、单独重放。
纯函数（``run_tools`` / ``render``）不碰网络，可直接单测。
"""

from __future__ import annotations

import json
from typing import Any

from . import llm
from .ported import calc as calc_tool
from .ported import grids as grids_tool
from .vision_prompts import REVIEWER, SOLVER, VISION

#: 非考试图片（白板/路牌等）忠实记录，不强行套用考题结构
VISION_EXTRA = "\n非考试图片（白板/路牌等）忠实记录实际内容，不强行套用考题结构。"


class VisionError(RuntimeError):
    """视觉链路失败（模型调用或返回结构异常）。"""


def _json_call(
    prompt: str,
    user_text: str,
    images: list[str],
    temperature: float = 0.1,
    retries: int = 1,
) -> dict[str, Any]:
    """要求模型输出 JSON；解析失败追加纠正消息重试一次。"""
    text = user_text
    last: Exception | None = None
    for attempt in range(retries + 1):
        if images:
            raw = llm.vision(text, images, system=prompt, temperature=temperature)
        else:
            raw = llm.chat(
                [
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": text},
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
            text = (
                text
                + "\n\n【上次输出不是合法 JSON】请只输出一个 JSON 对象，"
                "不要任何解释文字、不要 Markdown 围栏。"
            )
    raise VisionError(f"模型未返回合法 JSON：{last}")


# --------------------------------------------------------------------------- #
# 链路各步（每步对应一个图节点）
# --------------------------------------------------------------------------- #
def observe(text: str, images: list[str]) -> str:
    """第一步：视觉精读。只记录，不解题。"""
    if not images:
        return ""
    return llm.vision(
        text or "请识别这道题的全部题干与选项。",
        images,
        system=VISION + VISION_EXTRA,
        temperature=0.1,
    )


def solve(text: str, observation: str, images: list[str]) -> dict[str, Any]:
    """第二步：初解。**重新查看原图**，不只读观察记录。"""
    user = f"用户请求：{text or '请解答这道题'}\n\n图片观察记录：\n{observation or '（无图片）'}"
    return _json_call(SOLVER, user, images, temperature=0.1)


def review(
    text: str, draft: dict[str, Any], tool_result: dict[str, Any], images: list[str]
) -> dict[str, Any]:
    """第四步：终审。**再次重新查看原图**，允许推翻初解。"""
    user = (
        f"用户请求：{text or '请解答这道题'}\n\n"
        f"初解结果：\n{json.dumps(draft, ensure_ascii=False, indent=2)}\n\n"
        f"程序工具校验结果：\n{json.dumps(tool_result, ensure_ascii=False, indent=2)}\n\n"
        "请重新直接查看原图核对，并给出终审结论。"
    )
    return _json_call(REVIEWER, user, images, temperature=0.1)


# --------------------------------------------------------------------------- #
# 工具校验（纯函数，零 token）
# --------------------------------------------------------------------------- #
def run_tools(draft: dict[str, Any]) -> dict[str, Any]:
    """把初解给出的算式与黑白格交给确定性工具执行。

    模型结果只作参考：算式由 AST 安全求值，黑白格由逐格运算校验。
    「程序算式成立」不等于「看图正确」——终审仍须回看原图。
    """
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


def render(final: dict[str, Any], tool_result: dict[str, Any]) -> str:
    """第五步：渲染成屏幕上的正文。"""
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


def speech_of(final: dict[str, Any]) -> str:
    """播报语：优先用终审给的；没有就至少把答案念出来。"""
    spoken = str(final.get("speech") or "").strip()
    if spoken:
        return spoken
    if final.get("answerable"):
        ans = str(final.get("answer") or "").strip()
        if ans:
            return f"答案是 {ans}。"
    return ""
