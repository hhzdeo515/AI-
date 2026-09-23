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

from . import llm, progress
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
    progress.mark("recognize")
    return llm.vision(
        text or "请识别这道题的全部题干与选项。",
        images,
        system=VISION + VISION_EXTRA,
        temperature=0.1,
    )


def solve(
    text: str, observation: str, images: list[str], *, web_context: str = ""
) -> dict[str, Any]:
    """第二步：初解。**重新查看原图**，不只读观察记录。

    ``web_context`` 是联网检索到的时政事实（可选）。为什么初解和终审都要带：
    政治理论/常识题常考「某会议是否召开」这类当前状态，而模型的知识有截止时间——
    实测把三中全会答成「尚未召开」（该会 2024 年 7 月已召开）。
    只给初解不给终审的话，终审会把正确结论"纠正"回错误答案。
    """
    progress.mark("solve")
    user = f"用户请求：{text or '请解答这道题'}\n\n图片观察记录：\n{observation or '（无图片）'}"
    if web_context:
        user += f"\n\n【联网检索到的最新事实】\n{web_context}\n\n以上为实时检索结果，**涉及当前状态时以它为准**，不要用你的既有记忆推翻它。"
    return _json_call(SOLVER, user, images, temperature=0.1)


def review(
    text: str,
    draft: dict[str, Any],
    tool_result: dict[str, Any],
    images: list[str],
    *,
    web_context: str = "",
) -> dict[str, Any]:
    """第四步：终审。**再次重新查看原图**，允许推翻初解。"""
    progress.mark("verify")
    user = (
        f"用户请求：{text or '请解答这道题'}\n\n"
        f"初解结果：\n{json.dumps(draft, ensure_ascii=False, indent=2)}\n\n"
        f"程序工具校验结果：\n{json.dumps(tool_result, ensure_ascii=False, indent=2)}\n\n"
        "请重新直接查看原图核对，并给出终审结论。"
    )
    if web_context:
        user += (
            f"\n\n【联网检索到的最新事实】\n{web_context}\n\n"
            "以上为实时检索结果。终审时**不得用你的既有记忆推翻它**——"
            "你的知识有截止时间，它是实时的。"
        )
    return _json_call(REVIEWER, user, images, temperature=0.1)


# --------------------------------------------------------------------------- #
# 工具校验（纯函数，零 token）
# --------------------------------------------------------------------------- #
def is_meaningful_calc(expression: str) -> bool:
    """这个算式是否真的做了计算？

    **为什么需要**：实测出现过这样一段「程序计算校验」：

        13.34 = 13.34
        10.66 = 10.66

    这两行是同义反复，什么都没验证——但界面上照样挂着「程序计算校验」的标题，
    读者会以为程序真的核对过那两个数字。比不显示更糟：**它让没做的校验
    看起来像做过了**。

    成因：政治理论/常识类题是事实核对，本来就没有可算的东西，而提示词要求
    「尽量提供关键算式」，模型于是把「选项里的数 = 资料里的数」写成算式来交差。

    判定：没有任何运算符的表达式不构成计算。
    """
    s = (expression or "").strip()
    if not s:
        return False
    # 去掉等号两侧的写法（2+3=5）只保留表达式部分
    s = s.split("=", 1)[0]
    return any(ch in s for ch in "+-*/%") or "**" in s


def run_tools(draft: dict[str, Any]) -> dict[str, Any]:
    """把初解给出的算式与黑白格交给确定性工具执行。

    模型结果只作参考：算式由 AST 安全求值，黑白格由逐格运算校验。
    「程序算式成立」不等于「看图正确」——终审仍须回看原图。

    **只保留真正做了计算的算式**：同义反复（``13.34 = 13.34``）会被丢弃，
    否则界面上会出现一个空有标题的「程序计算校验」段落。
    """
    result: dict[str, Any] = {}
    # 工具校验（零 token）与终审同属 verify 这一步：模型初解已经出来了，
    # 接下来是「用程序核对算式/黑白格」+「再回看原图复核」。
    progress.mark("verify")
    calcs = draft.get("calculations")
    if isinstance(calcs, list) and calcs:
        wanted = [
            c.get("expression", "")
            for c in calcs
            if isinstance(c, dict) and c.get("expression")
        ]
        kept = [e for e in wanted if is_meaningful_calc(e)]
        dropped = len(wanted) - len(kept)
        if dropped:
            # 留痕便于排查：为什么这次没有计算校验
            result["calculations_skipped"] = dropped
        if kept:
            result["calculations"] = calc_tool.calculate_many(kept)

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
    elif tool_result.get("calculations_skipped"):
        # 说明为什么没有计算校验：模型交上来的全是同义反复。
        # 不静默隐藏——用户看到「这次没做计算校验」比看到一个空的
        # 「程序计算校验」标题更诚实。
        parts.append(
            f"\n（本题无需计算校验：模型给出的 {tool_result['calculations_skipped']} "
            "个「算式」都是同义反复，未做程序核对。）"
        )

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
