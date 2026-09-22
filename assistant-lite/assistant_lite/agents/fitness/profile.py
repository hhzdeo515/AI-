"""健康档案：每 owner 一个 JSON 文件，人可读、可手工编辑。

设计原则：
- 存储与逻辑分离，纯函数便于单测
- 档案文件放 data/profiles/<owner>.json，已被 .gitignore 排除
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ... import config

#: 档案字段定义：(键, 中文名, 类型, 选项/说明)
FIELDS: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    ("age", "年龄", "int", ()),
    ("height_cm", "身高（厘米）", "float", ()),
    ("weight_kg", "体重（公斤）", "float", ()),
    (
        "goal",
        "运动目标",
        "choice",
        ("减脂", "增肌", "保持体能", "康复", "提升运动表现"),
    ),
    (
        "level",
        "运动基础",
        "choice",
        ("几乎不运动", "偶尔运动", "规律运动1年以内", "规律运动1年以上"),
    ),
    ("injuries", "伤病情况", "list", ("无", "膝盖", "腰部", "肩部", "手腕", "脚踝")),
    ("conditions", "慢性病", "list", ("无", "高血压", "糖尿病", "心脏病", "哮喘")),
    ("days_per_week", "每周可训练天数", "int", ()),
    ("equipment", "可用器械", "list", ("健身房全套", "哑铃", "杠铃", "弹力带", "自重")),
)

FIELD_MAP: dict[str, tuple[str, str, tuple[str, ...]]] = {
    k: (label, kind, opts) for k, label, kind, opts in FIELDS
}

#: 档案里出现这些，后续所有建议都要带上安全提示
RISK_KEYS = ("injuries", "conditions")


def _safe_name(owner: str) -> str:
    """owner 可能来自外部，转成安全文件名。"""
    cleaned = re.sub(r"[^0-9A-Za-z_\u4e00-\u9fff-]", "_", owner or "local")
    return (cleaned or "local")[:64]


def path_for(owner: str) -> Path:
    return config.PROFILE_DIR / f"{_safe_name(owner)}.json"


# --------------------------------------------------------------------------- #
# 读写
# --------------------------------------------------------------------------- #
def load(owner: str) -> dict[str, Any]:
    """读档案；不存在或损坏时返回空档案。"""
    p = path_for(owner)
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def save(owner: str, data: dict[str, Any]) -> Path:
    config.PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    p = path_for(owner)
    p.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return p


def exists(owner: str) -> bool:
    return bool(load(owner))


# --------------------------------------------------------------------------- #
# 问卷
# --------------------------------------------------------------------------- #
def missing_fields(data: dict[str, Any]) -> list[str]:
    """返回还缺哪些字段，按 FIELDS 顺序。"""
    out = []
    for key, _label, _kind, _opts in FIELDS:
        v = data.get(key)
        if v is None or v == "" or v == []:
            out.append(key)
    return out


def question_for(key: str) -> str:
    label, kind, opts = FIELD_MAP[key]
    if kind == "choice":
        return f"{label}？可选：{'、'.join(opts)}。请直接回复其中一项。"
    if kind == "list":
        return f"{label}？可多选，用顿号分隔。可选：{'、'.join(opts)}。"
    if kind == "int":
        return f"{label}？（请填数字）"
    if kind == "float":
        return f"{label}？（请填数字，可带小数）"
    return f"{label}？"


def parse_answer(key: str, text: str) -> tuple[Any, str]:
    """把用户回答解析成该字段的值。返回 (值, 错误说明)。"""
    label, kind, opts = FIELD_MAP[key]
    t = (text or "").strip()
    if not t:
        return None, f"没有读到内容，请重新填写{label}。"

    if kind == "int":
        m = re.search(r"\d+", t)
        if not m:
            return None, f"{label}需要是数字，请重新填写。"
        v = int(m.group())
        if key == "age" and not 5 <= v <= 120:
            return None, "年龄看起来不合理（应在 5–120 之间），请重新填写。"
        if key == "days_per_week" and not 0 <= v <= 7:
            return None, "每周训练天数应在 0–7 之间，请重新填写。"
        return v, ""

    if kind == "float":
        m = re.search(r"\d+(?:\.\d+)?", t)
        if not m:
            return None, f"{label}需要是数字，请重新填写。"
        v = float(m.group())
        if key == "height_cm" and not 80 <= v <= 250:
            return None, "身高看起来不合理（应在 80–250 厘米之间），请重新填写。"
        if key == "weight_kg" and not 20 <= v <= 300:
            return None, "体重看起来不合理（应在 20–300 公斤之间），请重新填写。"
        return v, ""

    if kind == "choice":
        for o in opts:
            if o in t:
                return o, ""
        # 允许部分匹配（如"减脂"写成"减肥"）
        alias = {"减肥": "减脂", "增重": "增肌", "健身": "保持体能"}
        for k, v in alias.items():
            if k in t:
                return v, ""
        return None, f"没识别出{label}，请从这些里选一个：{'、'.join(opts)}。"

    if kind == "list":
        parts = [p.strip() for p in re.split(r"[、,，;；/\s]+", t) if p.strip()]
        picked = []
        for o in opts:
            if any(o in p for p in parts) or any(p in o for p in parts):
                picked.append(o)
        if not picked:
            return None, f"没识别出{label}，请从这些里选：{'、'.join(opts)}。"
        # "无"与其它项互斥
        if "无" in picked and len(picked) > 1:
            picked = [p for p in picked if p != "无"]
        return picked, ""

    return t, ""


# --------------------------------------------------------------------------- #
# 展示
# --------------------------------------------------------------------------- #
def summarize(data: dict[str, Any]) -> str:
    """把档案渲染成可读文本，供注入提示词与回显。"""
    if not data:
        return "（尚未建立健康档案）"
    lines = []
    for key, label, kind, _opts in FIELDS:
        v = data.get(key)
        if v is None or v == "" or v == []:
            continue
        lines.append(f"- {label}：{'、'.join(v) if isinstance(v, list) else v}")
    return "\n".join(lines) if lines else "（尚未建立健康档案）"


def risk_notes(data: dict[str, Any]) -> str:
    """从档案里提取需要注意的安全事项。"""
    notes = []
    injuries = data.get("injuries") or []
    conditions = data.get("conditions") or []
    injuries = [i for i in injuries if i != "无"]
    conditions = [c for c in conditions if c != "无"]
    if injuries:
        notes.append(f"有{ '、'.join(injuries) }伤病")
    if conditions:
        notes.append(f"有{ '、'.join(conditions) }")
    return "；".join(notes)


def bmi(data: dict[str, Any]) -> float | None:
    h = data.get("height_cm")
    w = data.get("weight_kg")
    if not isinstance(h, (int, float)) or not isinstance(w, (int, float)) or h <= 0:
        return None
    return round(w / ((h / 100) ** 2), 1)
