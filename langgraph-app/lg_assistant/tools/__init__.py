"""移植自 assistant-lite 的工具层（无第三方依赖的部分）。

- ``audio``  长音频分片（wav 用标准库，其余走 ffmpeg）
- ``export`` Markdown / 纯文本 / JSON / CSV / Word / PDF 导出

两者都只依赖 ``config``，与编排框架无关，原样保留以确保行为一致。
"""

from . import audio, export

__all__ = ["audio", "export"]
