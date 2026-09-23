"""语音播报文本。

眼镜是听觉优先的设备：屏幕上能显示 600 字，耳朵里不行。
同一份内容，眼睛看和耳朵听需要的长度、结构完全不同。
这里把「给屏幕看的长文本」压成「适合念出来的短句」。

三条路径，优先级从高到低：
1. 模型直接给的播报语（用 `<<<SPEECH>>>` 分隔，或结构化输出里的 speech 字段）
2. 规则截断（永远可用，不调模型、不依赖网络）
3. 短文本直接复用

设计约束：**本模块绝不发起额外的模型调用**。
长内容的播报语由生成它的那一次调用顺带产出，避免二次调用带来的时延与成本。
"""

from __future__ import annotations

import re

#: 模型输出里用于分隔「正文」与「播报语」的标记
SPEECH_MARK = "<<<SPEECH>>>"

#: 播报语的目标上限（字符）。超过就认为不适合直接念
MAX_CHARS = 80

_MD_PATTERNS = (
    (re.compile(r"```.*?```", re.S), " "),      # 代码块
    # 标题：**不能锚定行首**。原实现用 ^\s{0,3}#{1,6}\s*，在 re.M 下只匹配
    # 第一个标题——重复串里后续的 "## 标题" 前面是空格而非行首，会原样送进 TTS，
    # 用户听到「井号井号 标题」。改为不锚定，逐个去掉标记本身（不吞空格）。
    (re.compile(r"#{1,6}"), ""),
    (re.compile(r"\*\*(.+?)\*\*"), r"\1"),      # 粗体
    (re.compile(r"(?<!\*)\*([^*\n]+)\*(?!\*)"), r"\1"),  # 斜体
    (re.compile(r"`([^`]+)`"), r"\1"),          # 行内代码
    (re.compile(r"^\s*[-*+]\s+", re.M), ""),    # 无序列表
    (re.compile(r"^\s*\d+[.)]\s+", re.M), ""),  # 有序列表
    (re.compile(r"^\s*>\s?", re.M), ""),        # 引用
    (re.compile(r"^\s*\|.*\|\s*$", re.M), " "),  # 表格行
    (re.compile(r"^[-*_]{3,}\s*$", re.M), " "),  # 分隔线
)

_SENT_END = "。！？!?；;"


def to_plain(text: str) -> str:
    """去掉 Markdown 标记，压掉多余空白，得到可直接朗读的纯文本。"""
    s = text or ""
    for pat, rep in _MD_PATTERNS:
        s = pat.sub(rep, s)
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n{2,}", "\n", s)
    return s.strip()


#: 播报语/正文里不该当作确定结论的「时间断言」。
#: 语音是一次性、不可回看的——说错一个年份，听的人没有机会核对。
#: 实测踩过：模型把「党的二十届三中全会尚未召开」放进了播报语，
#: 而该会议 2024 年 7 月就已召开（模型用的是过期知识）。
#
# **必须收窄，否则误伤一片**（前两版都因此被测试抓出来）：
#   - 见年份就拦 -> 「会议于2022年10月23日召开」是稳定史实，被误伤
#   - 只匹配「第N届/次」 -> 「第一次」「第三组」全中招
# 只认真正表达「当前进展到哪一步」的句式：明确的时效副词，
# 或「第N届」与时效词同现。
_TIME_ASSERTION = re.compile(
    # 明确的时效副词——这些词本身就在陈述「现在还没发生」
    r"尚未|还未|还没|未召开|未发布|未生效|未举行|未出台|未通过"
    # 对未来的预测：同样不该在语音里说得像事实
    # （实测语料里就有「预计2028年出货量激增」这类，念出来像已确定）
    r"|预计\s*(?:(?:19|20)\d{2}\s*年|未来|明年|后年)"
    r"|即将(?:召开|发布|生效|举行|出台)"
    r"|截至\s*(?:(?:19|20)\d{2}\s*年|\d{1,2}\s*月|目前|现在|今)"
    r"|截至目前|迄今为止|目前尚未|目前还未"
    # 「第N届」必须与时效词同现，才是在判断最新进展
    r"|第[一二三四五六七八九十]+届[^。；;\n]{0,30}(?:尚未|还未|还没|已经|已|未)"
)


def looks_like_stale_claim(text: str) -> bool:
    """播报语是否含时效性断言（年份、「尚未召开」这类）。"""
    return bool(_TIME_ASSERTION.search(text or ""))


def strip_stale_claims(text: str, limit: int = MAX_CHARS) -> str:
    """逐句剔除含时效性断言的句子，再压缩成播报语。

    为什么不直接「退回去压缩正文」：正文里往往含同一句断言，
    压缩等于没拦——第一版就是这么写的，被测试抓出来了。
    这里的威胁模型是「不让听众听到过期断言」，所以必须在句子级别摘掉它。
    """
    plain = to_plain(text)
    kept: list[str] = []
    for chunk in re.split(r"(?<=[。！？!?；;\n])", plain):
        s = chunk.strip()
        if not s:
            continue
        if looks_like_stale_claim(s):
            continue
        kept.append(s)

    if not kept:
        # 整篇都是时效性内容：宁可只给一句保守提示，也不念过期判断
        return "该回答涉及时效性信息，请以官方最新发布为准。"
    return truncate("".join(kept), limit)


def safe_speech(text: str, limit: int = MAX_CHARS) -> str:
    """产出可安全朗读的播报语。

    含时效性断言时不直接采用模型给的播报语，改为按规则压缩正文——
    规则压缩只会摘取原句，不会新增判断，风险更低。
    """
    body, sp = resolve(text, limit)
    if sp and looks_like_stale_claim(sp):
        return truncate(body, limit)
    return sp


def truncate(text: str, limit: int = MAX_CHARS) -> str:
    """按句子边界截断到 limit 以内；实在没有句读就硬截。

    先经 :func:`to_plain` 去 Markdown 再判断长度——否则短文本分支会把
    「## 标题」这类标记原样送去朗读（屏幕上是标题，耳朵里是「井号井号」）。
    """
    s = to_plain(text).replace("\n", " ")
    s = re.sub(r"\s{2,}", " ", s).strip()
    if len(s) <= limit:
        return s

    cut = -1
    for i, ch in enumerate(s[: limit + 1]):
        if ch in _SENT_END:
            cut = i
    if cut >= limit // 3:          # 找到足够靠后的句末才用，否则句子太短没信息量
        return s[: cut + 1]
    return s[:limit].rstrip() + "…"


def split_speech(text: str) -> tuple[str, str]:
    """把模型输出拆成 (正文, 播报语)。没有分隔标记时播报语为空串。"""
    if SPEECH_MARK in (text or ""):
        body, _, sp = text.partition(SPEECH_MARK)
        return body.strip(), to_plain(sp)
    return (text or "").strip(), ""


def resolve(text: str, limit: int = MAX_CHARS) -> tuple[str, str]:
    """返回 (正文, 播报语)。播报语一定有值——模型没给就用规则截断。"""
    body, sp = split_speech(text)
    if not sp:
        sp = truncate(body, limit)
    return body, sp
