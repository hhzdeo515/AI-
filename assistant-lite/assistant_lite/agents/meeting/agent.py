"""会议纪要 Agent。

动作解析顺序：编排层给出的 action > 事件 semantic_action > 文本关键词 > 默认 append。
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from ... import llm
from ...schemas import (
    SCENE_MEETING,
    STATUS_ERROR,
    STATUS_NEED_INPUT,
    STATUS_OK,
    Reply,
    Task,
)
from ..base import BaseAgent
from . import prompts, state as st

AUDIO_EXT = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".amr", ".wma", ".mp4"}
#: 会议现场照片（白板 / 投屏 / 手写笔记）——走视觉模型忠实记录，不是 OCR
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff"}

#: 超过这个长度就分段滚动摘要，避免把整段转写塞进一次调用
ROLL_THRESHOLD = 20000
CHUNK_SIZE = 12000


class MeetingAgent(BaseAgent):
    scene = SCENE_MEETING
    keywords = ("会议", "纪要", "开会", "会议记录", "转写", "录音")

    # ------------------------------------------------------------------ #
    # 动作解析
    # ------------------------------------------------------------------ #
    def _resolve_action(self, task: Task) -> str:
        explicit = (task.action or "").strip()
        if explicit:
            return explicit
        sa = str((task.event or {}).get("semantic_action", "")).strip()
        if sa in ("start_meeting", "append_meeting", "summarize_meeting", "stop_meeting"):
            return sa.replace("_meeting", "")
        return self._infer_action(task.text or "")

    @staticmethod
    def _infer_action(text: str) -> str:
        t = text or ""
        if any(w in t for w in ("结束会议", "停止记录", "会议结束", "停止会议")):
            return "stop"
        if any(w in t for w in ("生成纪要", "生成会议纪要", "总结会议", "整理纪要", "纪要")):
            return "summarize"
        if any(w in t for w in ("开始会议", "开始记录", "新建会议", "开始开会")):
            return "start"
        return "append"

    # ------------------------------------------------------------------ #
    # 转写内容提取
    # ------------------------------------------------------------------ #
    def _extract_transcript(self, task: Task) -> tuple[str, str, list[str]]:
        """返回 (转写文本, 致命错误, 警告列表)。

        优先级：事件里的 transcript > 附件 > 正文。
        - 音频附件走 ASR 转写
        - 图片附件走视觉模型做**忠实记录**（白板/投屏/手写笔记），不是 OCR

        附件里只要有任意一个成功，就不算失败；失败的那些作为警告返回，
        既不阻断流程，也不静默吞掉。
        """
        ev = task.event or {}
        if isinstance(ev.get("transcript"), str) and ev["transcript"].strip():
            return ev["transcript"].strip(), "", []

        parts: list[str] = []
        warnings: list[str] = []

        for f in task.files:
            p = Path(f)
            ext = p.suffix.lower()
            if ext in AUDIO_EXT:
                try:
                    got = llm.asr(f).strip()
                    if got:
                        parts.append(got)
                    else:
                        warnings.append(f"{p.name}：没有识别到语音内容")
                except llm.LLMError as e:
                    warnings.append(f"{p.name} 转写失败：{e}")
            elif ext in IMAGE_EXT:
                try:
                    got = llm.vision(
                        "请记录这张会议现场图片里的全部内容。",
                        [f],
                        system=prompts.WHITEBOARD,
                        temperature=0.1,
                    ).strip()
                    if got:
                        parts.append(f"【白板 / 现场照片：{p.name}】\n{got}")
                    else:
                        warnings.append(f"{p.name}：没有识别到内容")
                except llm.LLMError as e:
                    warnings.append(f"{p.name} 识图失败：{e}")

        if parts:
            return "\n\n".join(parts), "", warnings
        if warnings:
            return "", "附件处理失败：" + "；".join(warnings), []
        if task.files:
            return (
                "",
                "没能从附件里提取到内容。支持音频（自动转写）与图片（白板 / 笔记识别）。",
                [],
            )

        text = (task.text or "").strip()
        if st.looks_like_meta(text):
            return (
                "",
                "这条像是操作指令而不是会议内容。请把转写文本粘贴进来，或上传录音 / 白板图片。",
                [],
            )
        return text, "", []

    # ------------------------------------------------------------------ #
    # 摘要
    # ------------------------------------------------------------------ #
    @staticmethod
    def _summarize(transcript: str) -> str:
        """生成纪要。超长转写先分段滚动摘要，再汇总。"""
        if len(transcript) <= ROLL_THRESHOLD:
            return llm.chat(
                [
                    {"role": "system", "content": prompts.MEETING},
                    {"role": "user", "content": f"会议转写内容：\n\n{transcript}"},
                ],
                temperature=0.2,
            )

        chunks = [
            transcript[i : i + CHUNK_SIZE]
            for i in range(0, len(transcript), CHUNK_SIZE)
        ]
        partials: list[str] = []
        for i, chunk in enumerate(chunks, 1):
            part = llm.chat(
                [
                    {"role": "system", "content": prompts.ROLLING_SUMMARY},
                    {
                        "role": "user",
                        "content": f"第 {i}/{len(chunks)} 段转写：\n\n{chunk}",
                    },
                ],
                temperature=0.2,
            )
            partials.append(f"【第 {i} 段】\n{part}")

        merged = "\n\n".join(partials)
        return llm.chat(
            [
                {"role": "system", "content": prompts.MEETING},
                {
                    "role": "user",
                    "content": (
                        "以下是同一场会议的分段摘要，请合并成一份完整纪要：\n\n" + merged
                    ),
                },
            ],
            temperature=0.2,
        )

    # ------------------------------------------------------------------ #
    # 主入口
    # ------------------------------------------------------------------ #
    def handle(self, task: Task, state: dict[str, Any]) -> Reply:
        working = copy.deepcopy(state)
        st.ensure(working)
        action = self._resolve_action(task)

        if action == "start":
            text, status = st.start(working)
            return self._reply(text, status, action, working)

        if action == "stop":
            text, status = st.stop(working)
            return self._reply(text, status, action, working)

        if action == "summarize":
            ok, msg = st.ready_to_summarize(working)
            if not ok:
                return self._reply(msg, STATUS_NEED_INPUT, action, working)
            transcript = working["meeting"]["transcript"]
            try:
                summary = self._summarize(transcript)
            except llm.LLMError as e:
                return self._reply(f"生成纪要失败：{e}", STATUS_ERROR, action, working)
            return self._reply(
                summary,
                STATUS_OK,
                action,
                working,
                archive=True,
                archive_title="会议纪要",
                archive_source=transcript,
            )

        # 默认 append
        transcript, err, warnings = self._extract_transcript(task)
        if err:
            return self._reply(err, STATUS_NEED_INPUT, "append", working)
        text, status = st.append(working, transcript)
        if warnings:
            text += "\n\n未处理的部分：\n" + "\n".join(f"· {w}" for w in warnings)
        return self._reply(text, status, "append", working)

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
    ) -> Reply:
        delta: dict[str, Any] = {
            "meeting": working["meeting"],
            "active_scene": SCENE_MEETING,
        }
        if archive and archive_title:
            delta["_archive"] = {
                "scene": SCENE_MEETING,
                "title": archive_title,
                "content": text,
                "source": archive_source,
            }
        return Reply(
            text=text,
            scene=SCENE_MEETING,
            action=action,
            status=status,
            state_delta=delta,
            archive=archive,
        )
