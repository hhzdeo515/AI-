"""健康档案：字段定义 + 问卷状态机 + 校验。**纯函数，只依赖 store 落库。**

## 为什么建档不在眼镜上做

眼镜端只有麦克风，没有屏幕：这套问卷有 9 个字段（含多选），靠语音一轮一轮问
「年龄？」「身高？」… 用户既看不到还剩几项，也改不了上一项填错的内容。
所以建档的**主入口是手机端**（有屏幕、能改能确认），眼镜端只读档案、
把它带进训练建议里。

## 两条写入路径，一套字段

- **手机端**：一次性提交整份档案（``save``），服务端按 owner 覆盖。
- **眼镜端**：语音建档（:func:`answer` 驱动状态机），逐步填。

两条路径的字段名、取值范围、校验规则**必须是同一套** —— 否则同一份档案
在手机改过之后，眼镜读到的含义会漂移。所以校验只写在这里一份，
手机端的写入接口也复用它（:func:`validate`）。

## 存储

走 ``store.py`` 的 SQLite（``profiles`` 表），不再用 ``data/profiles/<owner>.json``。
理由：线上是单库 SQLite，档案与其它资料同库便于备份和 owner 隔离；
文件方式还要自己处理 owner 名清洗与并发写。
"""

from __future__ import annotations

import re
from typing import Any

#: 档案字段定义：(键, 中文名, 类型, 选项/取值范围说明)
#:
#: 类型语义：``int`` / ``float`` 数值；``choice`` 单选；``list`` 多选。
FIELDS: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    ("age", "年龄", "int", ()),
    ("height_cm", "身高（厘米）", "float", ()),
    ("weight_kg", "体重（公斤）", "float", ()),
    ("goal", "运动目标", "choice",
     ("减脂", "增肌", "保持体能", "康复", "提升运动表现")),
    ("level", "运动基础", "choice",
     ("几乎不运动", "偶尔运动", "规律运动1年以内", "规律运动1年以上")),
    ("injuries", "伤病情况", "list", ("无", "膝盖", "腰部", "肩部", "手腕", "脚踝")),
    ("conditions", "慢性病", "list", ("无", "高血压", "糖尿病", "心脏病", "哮喘")),
    ("days_per_week", "每周可训练天数", "int", ()),
    ("equipment", "可用器械", "list", ("健身房全套", "哑铃", "杠铃", "弹力带", "自重")),
)

FIELD_MAP: dict[str, tuple[str, str, tuple[str, ...]]] = {
    k: (label, kind, opts) for k, label, kind, opts in FIELDS
}

FIELD_KEYS: tuple[str, ...] = tuple(f[0] for f in FIELDS)

#: 数值字段的合理区间。超出即拒收，**不落库** —— 一个 0 公斤的体重会让
#: 后续所有负荷建议失真，而且用户是不会发现的。
RANGES: dict[str, tuple[float, float, str]] = {
    "age": (5, 120, "年龄"),
    "height_cm": (80, 250, "身高"),
    "weight_kg": (20, 300, "体重"),
    "days_per_week": (0, 7, "每周训练天数"),
}

#: 语音输入的口语别名 -> 标准选项。手机端不需要它，但眼镜端是主要消费者。
ALIASES: dict[str, dict[str, str]] = {
    "goal": {"减肥": "减脂", "增重": "增肌", "长肌肉": "增肌", "健身": "保持体能"},
    "level": {"不运动": "几乎不运动", "经常运动": "规律运动1年以上"},
}

#: 档案里出现这些键的非「无」值，后续所有建议都要带安全提示
RISK_KEYS = ("injuries", "conditions")


# --------------------------------------------------------------------------- #
# 读取后的规范化
# --------------------------------------------------------------------------- #
def has(data: dict[str, Any] | None) -> bool:
    return bool(data)


def filled_keys(data: dict[str, Any] | None) -> list[str]:
    """已填且非空的字段，按 FIELDS 顺序。"""
    d = data or {}
    out = []
    for key in FIELD_KEYS:
        v = d.get(key)
        if v is None or v == "" or v == []:
            continue
        out.append(key)
    return out


def missing_keys(data: dict[str, Any] | None) -> list[str]:
    """还缺哪些字段，按 FIELDS 顺序。"""
    d = data or {}
    return [k for k in FIELD_KEYS if k not in filled_keys(d)]


def is_complete(data: dict[str, Any] | None) -> bool:
    return not missing_keys(data)


def progress(data: dict[str, Any] | None) -> tuple[int, int]:
    """``(已填, 总数)``，给「（3/9）」这类进度提示用。"""
    return len(filled_keys(data)), len(FIELD_KEYS)


# --------------------------------------------------------------------------- #
# 解析与校验（语音路径与手机端共用）
# --------------------------------------------------------------------------- #
def _as_float(raw: Any) -> float | None:
    m = re.search(r"\d+(?:\.\d+)?", str(raw))
    return float(m.group()) if m else None


def parse_field(key: str, raw: Any) -> tuple[Any, str]:
    """把某个字段的原始输入解析成标准值。返回 ``(值, 错误说明)``。

    错误说明非空时值必为 None —— 调用方据此决定是否落库。
    """
    label, kind, opts = FIELD_MAP[key]

    # 手机端可能直接提交结构化值（list / 数字），先按类型放过
    if kind == "list":
        if isinstance(raw, list):
            picked = [str(x).strip() for x in raw if str(x).strip()]
        else:
            parts = [p.strip() for p in re.split(r"[、,，;；/\s]+", str(raw or "")) if p.strip()]
            picked = []
            for o in opts:
                if any(o in p or p in o for p in parts):
                    picked.append(o)
        if not picked:
            return None, f"没识别出{label}，请从这些里选：{'、'.join(opts)}。"
        unknown = [p for p in picked if p not in opts]
        if unknown:
            return None, f"{label}里有无法识别的项：{'、'.join(unknown)}。可选：{'、'.join(opts)}。"
        # 「无」与其它项互斥：选了「无膝盖」就不该同时留着「膝盖」
        if "无" in picked and len(picked) > 1:
            picked = [p for p in picked if p != "无"]
        # 去重且保持 opts 顺序，避免手机端提交顺序影响存储
        picked = [o for o in opts if o in picked]
        return picked, ""

    text = "" if raw is None else str(raw).strip()
    if not text:
        return None, f"没有读到内容，请重新填写{label}。"

    if kind in ("int", "float"):
        v = _as_float(text)
        if v is None:
            return None, f"{label}需要是数字，请重新填写。"
        lo, hi, name = RANGES[key]
        if not lo <= v <= hi:
            return None, f"{name}看起来不合理（应在 {lo:g}–{hi:g} 之间），请重新填写。"
        return (int(v) if kind == "int" else v), ""

    if kind == "choice":
        for o in opts:
            if o in text:
                return o, ""
        for alias, target in ALIASES.get(key, {}).items():
            if alias in text:
                return target, ""
        return None, f"没识别出{label}，请从这些里选一个：{'、'.join(opts)}。"

    return text, ""


def validate(data: dict[str, Any], *, partial: bool = False) -> tuple[dict[str, Any], list[str]]:
    """校验一份档案。返回 ``(规范化后的档案, 错误列表)``。

    ``partial=True``（眼镜端语音建档）允许缺字段 —— 问卷本来就是一轮一轮填。
    ``partial=False``（手机端提交）要求 9 项齐全，缺了直接报出来，
    免得用户以为存上了、其实眼镜端读到的是一份半成品。
    """
    src = data or {}
    unknown = [k for k in src if k not in FIELD_MAP]
    errors = [f"无法识别的字段：{'、'.join(unknown)}"] if unknown else []

    out: dict[str, Any] = {}
    for key in FIELD_KEYS:
        if key not in src or src[key] in (None, "", []):
            continue
        value, err = parse_field(key, src[key])
        if err:
            errors.append(err)
        else:
            out[key] = value

    if not partial:
        missing = missing_keys(out)
        if missing:
            labels = "、".join(FIELD_MAP[k][0] for k in missing)
            errors.append(f"还缺这些字段：{labels}。")

    return out, errors


# --------------------------------------------------------------------------- #
# 展示
# --------------------------------------------------------------------------- #
def summarize(data: dict[str, Any] | None) -> str:
    """渲染成可读文本，供注入提示词与界面回显。"""
    d = data or {}
    lines = []
    for key, label, _kind, _opts in FIELDS:
        v = d.get(key)
        if v is None or v == "" or v == []:
            continue
        lines.append(f"- {label}：{'、'.join(map(str, v)) if isinstance(v, list) else v}")
    return "\n".join(lines) if lines else "（尚未建立健康档案）"


def question_for(key: str) -> str:
    """语音路径的提问文案。手机端不用它（界面自己画表单）。"""
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


def risk_notes(data: dict[str, Any] | None) -> str:
    """伤病与慢性病摘要。用于安全提示与界面上的「注意」。

    「无」是选项之一，但它不是风险 —— 必须滤掉，否则每个填了「无」的用户
    都会被告知「有伤病史」。
    """
    d = data or {}
    notes = []
    injuries = [v for v in (d.get("injuries") or []) if v != "无"]
    conditions = [v for v in (d.get("conditions") or []) if v != "无"]
    if injuries:
        notes.append(f"有{'、'.join(injuries)}伤病")
    if conditions:
        notes.append(f"有{'、'.join(conditions)}")
    return "；".join(notes)


def bmi(data: dict[str, Any] | None) -> float | None:
    d = data or {}
    h, w = d.get("height_cm"), d.get("weight_kg")
    if not isinstance(h, (int, float)) or not isinstance(w, (int, float)) or h <= 0:
        return None
    return round(w / ((h / 100) ** 2), 1)


def field_spec() -> list[dict[str, Any]]:
    """字段清单，给手机端渲染表单用。**前端不自己硬编码字段**，
    否则加一个字段要改两处，迟早对不上。"""
    return [
        {"key": k, "label": label, "kind": kind, "options": list(opts)}
        for k, label, kind, opts in FIELDS
    ]


# --------------------------------------------------------------------------- #
# 对话框状态机（眼镜端语音建档）
#
# 状态只有两个键，挂在会话状态 ``fitness`` 里（见 state.py）：
#   awaiting —— 正在等哪个字段的回答
#   draft    —— 已经填好的部分
#
# 与 assistant-lite 的行为保持一致，含「建档进行中，所有输入都当问卷答案」：
# 这条是必要的，因为用户在答「28」的时候，那句话里没有任何关键词，
# 不粘住就会掉到 general 被当闲聊。
# --------------------------------------------------------------------------- #
#: 触发建档。注意「建档」两字要能单独命中——用户口语里常说「建个档」。
START_WORDS = (
    "建立健康档案", "健康档案", "我的档案", "更新档案", "重新填写",
    "建立档案", "完善档案", "建档", "建个档", "填档案",
)

#: 退出建档
CANCEL_WORDS = ("取消建档", "不建了", "退出建档", "算了", "不填了", "退出")


def wants_profile(text: str) -> bool:
    """这句话是不是要开始/继续建档。"""
    t = text or ""
    return any(w in t for w in START_WORDS)


def wants_cancel(text: str) -> bool:
    t = text or ""
    return any(w in t for w in CANCEL_WORDS)


def opening_line() -> str:
    return (
        "我们先把健康档案建起来，之后所有建议都会结合它。"
        "中途想退出就说「取消建档」。"
    )


def finished_line(data: dict[str, Any]) -> str:
    body = "健康档案已建立，之后所有训练建议都会结合它。\n\n" + summarize(data)
    b = bmi(data)
    if b is not None:
        body += f"\n\nBMI：{b}"
    risk = risk_notes(data)
    if risk:
        body += (
            f"\n\n注意：{risk}。有这些情况的训练安排请先咨询医生或线下教练，"
            "我会在建议里避开高风险动作。"
        )
    return body


def cancelled_line() -> str:
    return "已退出建档流程，之前填的内容没保存。随时说「建立健康档案」重新开始。"


def answer(
    text: str,
    *,
    awaiting: str = "",
    draft: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """推进一轮问卷。**纯函数**：不落库、不调模型。

    返回 ``{awaiting, draft, done, message}``：

    - ``done=True`` 表示这轮填满了，调用方负责落库（``store.save_profile``）
    - ``message`` 是要回给用户的话
    - 出错时 ``awaiting`` 不变，用户重答同一个字段

    为什么不在这里落库：纯函数好单测，且落库是副作用、该由节点统一做
    （节点才知道 owner）。
    """
    draft = dict(draft or {})
    t = (text or "").strip()

    if wants_cancel(t):
        return {"awaiting": "", "draft": {}, "done": False, "message": cancelled_line()}

    # 问卷进行中又说了一次「建立健康档案」→ 当成重新开始，而不是答案。
    #
    # 实测：问到年龄时用户说了句「建立健康档案」，它被拿去解析成年龄，
    # 回一句「年龄需要是数字，请重新填写」——用户完全看不懂，因为他只是在
    # 重复最初的请求（可能没听见第一句提问）。语义上这是明确的「重来」。
    if awaiting and wants_profile(t):
        draft = {}
        nxt = missing_keys(draft)[0]
        return {
            "awaiting": nxt,
            "draft": draft,
            "done": False,
            "message": f"{opening_line()}\n\n（1/{len(FIELD_KEYS)}）{question_for(nxt)}",
        }

    if awaiting and awaiting in FIELD_MAP:
        value, err = parse_field(awaiting, t)
        if err:
            # 答得不对就停在原地，不推进 —— 否则会悄悄丢掉这一项
            return {"awaiting": awaiting, "draft": draft, "done": False, "message": err}
        draft[awaiting] = value

    missing = missing_keys(draft)
    if not missing:
        return {"awaiting": "", "draft": draft, "done": True, "message": finished_line(draft)}

    nxt = missing[0]
    n, total = progress(draft)
    prefix = f"{opening_line()}\n\n" if not awaiting else ""
    return {
        "awaiting": nxt,
        "draft": draft,
        "done": False,
        "message": f"{prefix}（{n + 1}/{total}）{question_for(nxt)}",
    }
