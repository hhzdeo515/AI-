"""联网检索：给时政类问题接上真实信息。

**为什么需要**：模型的知识有截止时间，实测三次把「党的二十届三中全会」
答成「尚未召开」（该会 2024 年 7 月已召开）。**提示词治不好**——它是真心
那么认为的，无法从内部判断自己过时。唯一的正解是让它能查到最新信息。

**必须走 DashScope 原生接口，不能用 OpenAI 兼容层**（实测对比）：

    OpenAI 兼容 + extra_body.enable_search   -> 「尚未召开」  参数被忽略，没联网
    dashscope.Generation.call(enable_search) -> 「2024年7月15日至18日」  真联网

同一个问题、同一个模型，结论完全相反。所以联网这条路径固定用 dashscope SDK。

返回结构里 ``output.search_info.search_results`` 带标题/URL/站点，
可以给出来源，让用户自己核对——比只给一个结论可信得多。
"""

from __future__ import annotations

import json
import re
from typing import Any

from . import config

#: 「一出现就说明在问当前状态」的措辞。
#: 这些措辞不会作为连接词出现在普通叙述里，所以单独命中即可判定。
STRONG_FRESH_PATTERNS = (
    r"是否(?:已经|已)?(?:召开|发布|生效|出台|举行|通过|上市)"
    r"|什么(?:时候|时间)(?:召开|发布|举行)"
    r"|截至目前|截至|最新版|现行|修订"
    r"|新(?:政策|规|法|条例|办法)",
)

#: 可能出现在普通叙述里的措辞（「现在看第三列」「目前无规律」）。
#: **单独出现不算信号**，必须与下面的时效性名词同时出现。
#:
#: 为什么要这么分：解题链的题目内容由模型转写，而模型写的观察记录是散文。
#: 实测同一张九宫格题图，一次触发检索（白等 30 秒）、下一次不触发——
#: 同一张图两次结果不同，说明触发源是模型的措辞，不是题目本身。
WEAK_FRESH_PATTERNS = (
    r"最新|最近|近期|当前|目前|现在|如今|有无|有没有",
)

#: 时效性名词：只有谈这些东西才可能「随时间变化」。
#: 刻意**不收录**「办法/方案/文件/通知」——太泛，
#: 「有没有什么办法」会被误判成时政提问。
FRESH_NOUNS = (
    r"政策|法规|条例|会议|全会|两会|人事|任职|任命|当选|选举"
    r"|主席|总书记|总理|部长|生效|实施|出台|发布|现行|版本",
)

#: 时政**实体**：具体届次 / 会议名。与上面那组的区别是——
#: 这些词不可能作为普通连接词被写出来，一旦出现就说明这题真的涉及时政，
#: 所以它可以安全地用来扫模型的整段输出（见 ``mentions_time_sensitive_entity``）。
ENTITY_PATTERNS = (
    r"第[一二三四五六七八九十百]+届[一二三四五六七八九十]+中全会"
    r"|三中全会|四中全会|五中全会|中央经济工作会议|全国两会|政治局会议"
    r"|二十大|十九届|二十届",
)

_STRONG_RE = re.compile("|".join(f"(?:{p})" for p in STRONG_FRESH_PATTERNS))
_WEAK_RE = re.compile("|".join(f"(?:{p})" for p in WEAK_FRESH_PATTERNS))
_NOUN_RE = re.compile("|".join(f"(?:{p})" for p in FRESH_NOUNS))
_ENTITY_RE = re.compile("|".join(f"(?:{p})" for p in ENTITY_PATTERNS))


class SearchError(RuntimeError):
    """联网检索失败。"""


def needs_search(text: str) -> bool:
    """问题是否需要联网核对。纯规则，零成本。

    判定分三档（精度递减、召回递增）：
    1. **强措辞**（是否已召开 / 最新版 / 截至…）——命中即判定；
    2. **时政实体**（三中全会 / 中央经济工作会议…）——命中即判定；
    3. **弱措辞**（现在 / 目前 / 最近…）**必须与时效性名词共现**才判定。

    第 3 档的共现要求是必须的：弱措辞会出现在普通叙述里（「现在看第三列」
    「目前无规律」），单看它们等于随机触发一次 30 秒的联网检索。
    收窄后**宁可多查也不能漏查**这条原则仍然成立——时政题一定带实体或强措辞。
    """
    t = text or ""
    if _STRONG_RE.search(t) or _ENTITY_RE.search(t):
        return True
    return bool(_WEAK_RE.search(t) and _NOUN_RE.search(t))


def mentions_time_sensitive_entity(text: str) -> bool:
    """文本里是否出现时政实体（届次 / 会议名）。

    **这组可以安全地扫模型的整段输出**：普通推理不会写出「三中全会」
    「中央经济工作会议」这类词，所以误报率远低于问法措辞。
    用途：解题链里题目内容由模型转写，题干区以外的部分也要能触发检索。
    """
    return bool(_ENTITY_RE.search(text or ""))


def _dashscope():
    try:
        import dashscope
    except ImportError as e:  # pragma: no cover
        raise SearchError("未安装 dashscope，无法联网检索：pip install dashscope") from e
    dashscope.api_key = config.DASHSCOPE_API_KEY
    return dashscope


def search_answer(
    question: str,
    *,
    model: str | None = None,
    system: str = "",
) -> dict[str, Any]:
    """带着联网检索回答一个问题。

    返回::

        {"answer": "…", "sources": [{"title","url","site"}], "searched": True}

    失败抛 :class:`SearchError`，由调用方决定是降级还是提示——**不静默**。
    """
    if not (question or "").strip():
        raise SearchError("问题为空")
    if not config.DASHSCOPE_API_KEY:
        raise SearchError("未配置 DASHSCOPE_API_KEY")

    dashscope = _dashscope()
    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": question})

    try:
        resp = dashscope.Generation.call(
            model=model or config.MODEL_TEXT,
            messages=messages,
            result_format="message",
            # 联网开关。**必须走原生接口**——OpenAI 兼容层会忽略它。
            enable_search=True,
            search_options={"enable_source": True, "enable_citation": True},
        )
    except Exception as e:  # noqa: BLE001
        raise SearchError(f"联网检索调用失败：{type(e).__name__}: {e}") from e

    code = getattr(resp, "status_code", 200)
    if code != 200:
        raise SearchError(
            f"联网检索返回错误 {code}："
            f"{getattr(resp, 'code', '')} {getattr(resp, 'message', '')}"
        )

    try:
        answer = (resp.output.choices[0].message.content or "").strip()
    except (AttributeError, IndexError, TypeError) as e:
        raise SearchError(f"联网检索返回结构异常：{e}") from e

    return {
        "answer": answer,
        "sources": extract_sources(resp),
        "searched": True,
    }


def extract_sources(resp: Any) -> list[dict[str, str]]:
    """从响应里取检索来源。取不到就返回空列表，不影响主流程。"""
    try:
        info = getattr(resp.output, "search_info", None)
    except Exception:  # noqa: BLE001
        return []
    if not info:
        return []

    if not isinstance(info, dict):
        try:
            info = json.loads(json.dumps(info, default=lambda o: getattr(o, "__dict__", {})))
        except Exception:  # noqa: BLE001
            return []

    rows = info.get("search_results") or []
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for r in rows:
        if not isinstance(r, dict):
            continue
        url = str(r.get("url") or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        out.append(
            {
                "title": str(r.get("title") or "").strip(),
                "url": url,
                "site": str(r.get("site_name") or "").strip(),
            }
        )
    return out


def format_sources(sources: list[dict[str, str]], limit: int = 6) -> str:
    """把来源渲染成 Markdown 列表，附在回答尾部供核对。"""
    if not sources:
        return ""
    lines = ["", "**检索来源**（可自行核对）", ""]
    for i, s in enumerate(sources[:limit], 1):
        title = s.get("title") or s.get("url")
        site = f"（{s['site']}）" if s.get("site") else ""
        lines.append(f"{i}. [{title}]({s['url']}){site}")
    return "\n".join(lines)
