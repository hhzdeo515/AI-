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
