"""资料查询与导出场景：把已归档的会议纪要 / 题解 / 训练总结找出来或导出成文件。

支持自然语言，例如：
- 「我有哪些资料」「查找会议纪要」            -> 列表
- 「导出刚才的纪要成 Word」「导出成 PDF」       -> 导出最新一份
- 「导出 6e13c22b 成 pdf」                   -> 按 ID 前缀导出指定资料

格式解析、目标筛选、ID 识别全部走规则，零 token，不调模型。
"""

from __future__ import annotations

import re
from typing import Any

from ... import session
from ...schemas import (
    SCENE_RESOURCE,
    STATUS_NEED_INPUT,
    STATUS_OK,
    Reply,
    Task,
)
from ...tools import export
from ..base import BaseAgent

#: 导出意图词——命中即给最高置信度，压过其它场景的关键词
EXPORT_WORDS = ("导出", "下载", "转成", "存成", "生成文件", "给我文件", "打包")

#: 格式词 -> 导出格式
FORMAT_WORDS: dict[str, str] = {
    "word": "docx",
    "docx": "docx",
    "doc": "docx",
    "文档": "docx",
    "pdf": "pdf",
    "markdown": "md",
    "md": "md",
    "txt": "txt",
    "纯文本": "txt",
    "文本": "txt",
    "json": "json",
    "csv": "csv",
    "表格": "csv",
    "excel": "csv",
}

#: 目标资料筛选词 -> 场景
TARGET_WORDS: dict[str, str] = {
    "会议": "meeting",
    "纪要": "meeting",
    "题解": "exam",
    "题目": "exam",
    "解题": "exam",
    "训练": "fitness",
    "锻炼": "fitness",
    "健身": "fitness",
}

#: 指向"最新一份"的词
LATEST_WORDS = ("刚才", "刚刚", "上面", "这份", "那份", "最新", "最近", "上一份", "这个")

_ID_RE = re.compile(r"\b([0-9a-fA-F]{6,32})\b")

SCENE_LABEL = {
    "meeting": "会议纪要",
    "exam": "题解",
    "fitness": "训练总结",
    "general": "资料",
}


def parse_format(text: str) -> str:
    """从文本里解析导出格式；没写则返回空字符串。"""
    low = (text or "").lower()
    # 长的先匹配，避免 "docx" 被 "doc" 抢先
    for word in sorted(FORMAT_WORDS, key=len, reverse=True):
        if word in low:
            return FORMAT_WORDS[word]
    return ""


def parse_target(text: str) -> str | None:
    """从文本里解析要导出的资料场景；没指定返回 None。"""
    t = text or ""
    for word in sorted(TARGET_WORDS, key=len, reverse=True):
        if word in t:
            return TARGET_WORDS[word]
    return None


def parse_id_prefix(text: str) -> str:
    """从文本里解析资料 ID 前缀（用户通常只看到前 8 位）。"""
    for m in _ID_RE.finditer(text or ""):
        token = m.group(1).lower()
        # 纯数字的多半是组数/年份之类，不是资料 ID
        if token.isdigit():
            continue
        return token
    return ""


def wants_export(text: str) -> bool:
    t = text or ""
    return any(w in t for w in EXPORT_WORDS)


class ResourceAgent(BaseAgent):
    scene = SCENE_RESOURCE
    keywords = ("资料", "记录", "历史", "查找", "找一下", "有哪些")

    def can_handle(self, task: Task) -> float:
        if task.scene_hint == self.scene:
            return 1.0
        text = task.text or ""
        # 导出意图优先于其它场景的关键词：
        # 「导出刚才的纪要成 Word」同时含 meeting 的"纪要"，必须让 resource 胜出
        if wants_export(text):
            return 0.95
        return super().can_handle(task)

    # ------------------------------------------------------------------ #
    def handle(self, task: Task, state: dict[str, Any]) -> Reply:
        text = (task.text or "").strip()
        action = task.action or ("export" if wants_export(text) else "list")
        if action == "export":
            return self._export(task, text)
        return self._list(task, text)

    # ------------------------------------------------------------------ #
    def _export(self, task: Task, text: str) -> Reply:
        fmt = parse_format(text) or "md"
        target = parse_target(text)
        prefix = parse_id_prefix(text)

        rows = session.list_full(task.owner, target, limit=50)
        if not rows:
            scope = f"（{SCENE_LABEL.get(target or '', '全部')}）" if target else ""
            return self._need(
                f"没有找到可导出的资料{scope}。先生成会议纪要、题解或训练总结再导出。"
            )

        if prefix:
            rows = [r for r in rows if r["id"].lower().startswith(prefix)]
            if not rows:
                return self._need(f"没有找到 ID 以 {prefix} 开头的资料。")
        elif not any(w in text for w in LATEST_WORDS) and len(rows) > 1 and not target:
            # 有多个候选又没说清要哪个 -> 让用户挑
            lines = [f"找到 {len(rows)} 份资料，请指定要导出哪一份（回复 ID 前缀即可）："]
            lines += [f"- {r['title']}（{r['scene']}，`{r['id'][:8]}`）" for r in rows[:10]]
            return self._need("\n".join(lines))

        row = rows[0]
        try:
            path = export.export_rows([row], fmt)
        except export.ExportError as e:
            return self._need(f"导出失败：{e}")

        label = SCENE_LABEL.get(row.get("scene", ""), "资料")
        return Reply(
            text=(
                f"已导出{label}《{row['title']}》为 {fmt.upper()}。\n\n"
                f"文件：{path}\n\n"
                "（本机文件；Web 页也可用 /api/export?format=%s 直接下载。）" % fmt
            ),
            scene=SCENE_RESOURCE,
            action="export",
            status=STATUS_OK,
            artifacts=[
                {
                    "kind": "file",
                    "label": f"{label}·{fmt.upper()}",
                    "path": str(path),
                    "id": row["id"],
                }
            ],
            state_delta={"active_scene": SCENE_RESOURCE},
        )

    # ------------------------------------------------------------------ #
    def _list(self, task: Task, text: str) -> Reply:
        target = parse_target(text)
        rows = session.list_resources(task.owner, target, limit=20)
        if not rows:
            scope = f"（{SCENE_LABEL.get(target or '', '全部')}）" if target else ""
            return self._need(f"还没有归档资料{scope}。", action="list")

        lines = [f"共找到 {len(rows)} 份资料："]
        for r in rows:
            label = SCENE_LABEL.get(r.get("scene", ""), "资料")
            lines.append(
                f"- **{r['title']}**（{label}，`{r['id'][:8]}`，{r['created'][:10]}）"
            )
        lines.append(
            "\n导出：说「导出最新一份成 Word」或「导出 " + rows[0]["id"][:8] + " 成 PDF」。"
        )
        return Reply(
            text="\n".join(lines),
            scene=SCENE_RESOURCE,
            action="list",
            status=STATUS_OK,
            state_delta={"active_scene": SCENE_RESOURCE},
        )

    # ------------------------------------------------------------------ #
    @staticmethod
    def _need(msg: str, action: str = "export") -> Reply:
        return Reply(
            text=msg,
            scene=SCENE_RESOURCE,
            action=action,
            status=STATUS_NEED_INPUT,
        )
