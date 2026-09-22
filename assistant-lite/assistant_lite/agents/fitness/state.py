"""训练状态机与规则引擎：纯逻辑，不做 I/O，便于单测。

事件（由 orchestrator.ACTION_MAP 路由过来）：
- start_exercise  开始练一个动作
- set_done        完成一组
- pain_report     报告疼痛/不适
- end_workout     结束本次训练

规则（来自方案约定）：
- 组间休息 60–90 秒
- 每 3 组给一次动作提示
- 报告疼痛立即停止该动作并给出处置建议
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any

#: 建议组间休息区间（秒）
REST_MIN, REST_MAX = 60, 90
#: 每完成多少组给一次动作提示
CUE_EVERY_SETS = 3

STATUS_IDLE = "idle"
STATUS_ACTIVE = "active"
STATUS_PAUSED = "paused"
STATUS_DONE = "done"

# --------------------------------------------------------------------------- #
# 文本解析
# --------------------------------------------------------------------------- #
_SETS_RE = re.compile(r"(\d+)\s*组")
_REPS_RE = re.compile(r"(\d+)\s*(?:次|个|下|reps?)", re.IGNORECASE)
_WEIGHT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:kg|KG|公斤|千克)")
_START_RE = re.compile(r"(?:开始|练|做|来|去|想)\s*([^\d，,。.；;、]{1,14})")
_EXERCISE_TAIL = re.compile(r"(训练|练习|动作|模式|了|吧|呗)+$")
#: 提取出的名称里残留的动词/量词前缀，如"练一下硬拉"->"一下硬拉"->"硬拉"
_NAME_PREFIX = re.compile(r"^(?:开始|练|做|来|去|想|一下|一个|个|点|些)+")

#: 这些是部位/泛称而不是具体动作，不能当成训练动作开始记录
GENERIC_WORDS = frozenset(
    {
        "腿", "胸", "背", "肩", "手臂", "核心", "全身", "有氧", "力量",
        "训练", "运动", "健身", "器械", "肌肉", "身体",
    }
)

BODY_PARTS = (
    "膝盖", "膝关节", "腰", "腰椎", "肩", "肩膀", "手腕", "脚踝", "踝",
    "肘", "手肘", "颈", "脖子", "背", "髋", "大腿", "小腿", "胸",
)
PAIN_WORDS = ("疼", "痛", "酸", "不适", "难受", "拉伤", "扭到", "刺痛", "胀")
#: 带这些词说明是在问问题，不是在报告伤情
QUESTION_WORDS = ("怎么办", "如何", "为什么", "怎么", "什么原因", "正常吗", "要不要")

SET_DONE_RE = re.compile(r"(做完|完成|做了|已做|再来|下一组|结束这组)")
END_WORDS = ("结束训练", "结束锻炼", "练完了", "不练了", "收工", "结束本次")


def now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _elapsed_min(started: str) -> int | None:
    if not started:
        return None
    try:
        t0 = datetime.fromisoformat(started)
    except ValueError:
        return None
    return max(0, int((now() - t0).total_seconds() // 60))


# --------------------------------------------------------------------------- #
# 识别器
# --------------------------------------------------------------------------- #
def _clean_name(raw: str) -> str:
    """把"练一下硬拉训练"这类残留清成"硬拉"。"""
    name = _EXERCISE_TAIL.sub("", (raw or "").strip())
    name = _NAME_PREFIX.sub("", name).strip()
    return name.strip("的 　：:，,。.、")


def detect_start(text: str) -> str | None:
    """返回动作名；不像"开始训练"则返回 None。"""
    t = (text or "").strip()
    if not t or is_pain_report(t) or is_set_done(t) or is_end(t):
        return None
    m = _START_RE.search(t)
    if m:
        name = _clean_name(m.group(1))
        if name in GENERIC_WORDS:
            return None
        if name and not any(w in name for w in QUESTION_WORDS):
            return name
    # "卧推 4组10次" 这种没有动词但带组数的
    if _SETS_RE.search(t) and _REPS_RE.search(t):
        name = _clean_name(_SETS_RE.split(t)[0])
        if name and 1 <= len(name) <= 14 and name not in GENERIC_WORDS:
            return name
    return None


def is_set_done(text: str) -> bool:
    t = (text or "").strip()
    if not t or is_end(t):
        return False
    if is_pain_report(t):
        return False
    if any(w in t for w in QUESTION_WORDS):
        return False
    return bool(SET_DONE_RE.search(t))


def is_pain_report(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    if not any(w in t for w in PAIN_WORDS):
        return False
    # "膝盖疼怎么办" 是在问问题，不是报告伤情
    if any(w in t for w in QUESTION_WORDS):
        return False
    return any(p in t for p in BODY_PARTS) or len(t) <= 10


def is_end(text: str) -> bool:
    t = (text or "").strip()
    return any(w in t for w in END_WORDS)


def pain_location(text: str) -> str:
    for p in BODY_PARTS:
        if p in text:
            return p
    return ""


def parse_sets(text: str) -> int | None:
    m = _SETS_RE.search(text or "")
    return int(m.group(1)) if m else None


def parse_reps(text: str) -> int | None:
    m = _REPS_RE.search(text or "")
    return int(m.group(1)) if m else None


def parse_weight(text: str) -> float | None:
    m = _WEIGHT_RE.search(text or "")
    return float(m.group(1)) if m else None


# --------------------------------------------------------------------------- #
# 状态
# --------------------------------------------------------------------------- #
def default_workout() -> dict[str, Any]:
    return {
        "id": "",
        "status": STATUS_IDLE,
        "started": "",
        "ended": "",
        "plan": [],  # [{name, sets, reps, weight}]
        "current": "",
        "current_reps": None,
        "current_weight": None,
        "sets_done": 0,
        "total_sets": 0,
        "log": [],  # [{exercise, set, reps, weight, at}]
        "pain": [],  # [{where, note, at}]
        "cues": [],  # 已经给过的提示，避免重复
    }


def ensure(fit: dict[str, Any]) -> dict[str, Any]:
    """保证 fit["workout"] 结构完整。"""
    w = fit.setdefault("workout", default_workout())
    if not isinstance(w, dict):
        w = default_workout()
        fit["workout"] = w
    base = default_workout()
    for k, v in base.items():
        w.setdefault(k, v)
    for key in ("plan", "log", "pain", "cues"):
        if not isinstance(w.get(key), list):
            w[key] = []
    return w


def is_active(fit: dict[str, Any]) -> bool:
    w = fit.get("workout")
    return isinstance(w, dict) and w.get("status") in (STATUS_ACTIVE, STATUS_PAUSED)


# --------------------------------------------------------------------------- #
# 动作
# --------------------------------------------------------------------------- #
def start(fit: dict[str, Any], text: str, exercise: str | None = None) -> tuple[str, str]:
    from ...schemas import STATUS_NEED_INPUT, STATUS_OK

    w = ensure(fit)
    name = (exercise or detect_start(text) or "").strip()
    if not name:
        return (
            "没听清要练什么动作。可以这样说：「开始深蹲」「开始卧推 4组10次」。",
            STATUS_NEED_INPUT,
        )

    if w["status"] in (STATUS_ACTIVE, STATUS_PAUSED):
        # 训练中切换动作
        prev = w.get("current") or ""
        w["current"] = name
        w["sets_done"] = 0
        w["current_reps"] = parse_reps(text)
        w["current_weight"] = parse_weight(text)
        if w["status"] == STATUS_PAUSED:
            w["status"] = STATUS_ACTIVE
        return (
            f"已切换到「{name}」（{prev} 已记 {_sets_of(w, prev)} 组）。"
            f"做完一组告诉我「做完一组」。",
            STATUS_OK,
        )

    w.update(
        id=uuid.uuid4().hex,
        status=STATUS_ACTIVE,
        started=_iso(now()),
        ended="",
        current=name,
        sets_done=0,
        total_sets=0,
        current_reps=parse_reps(text),
        current_weight=parse_weight(text),
        log=[],
        pain=[],
        cues=[],
    )
    sets = parse_sets(text)
    w["plan"] = [
        {
            "name": name,
            "sets": sets,
            "reps": parse_reps(text),
            "weight": parse_weight(text),
        }
    ]

    target = ""
    if sets or w["current_reps"] or w["current_weight"]:
        bits = []
        if sets:
            bits.append(f"{sets} 组")
        if w["current_reps"]:
            bits.append(f"{w['current_reps']} 次")
        if w["current_weight"]:
            bits.append(f"{w['current_weight']} 公斤")
        target = "目标：" + " × ".join(bits) + "。\n"

    return (
        f"开始记录「{name}」。{target}"
        f"每做完一组说「做完一组」，我会帮你记组数并提醒休息。"
        f"身体有不舒服随时说，比如「膝盖疼」。",
        STATUS_OK,
    )


def set_done(fit: dict[str, Any], text: str = "") -> tuple[str, str]:
    from ...schemas import STATUS_NEED_INPUT, STATUS_OK

    w = ensure(fit)
    if w["status"] not in (STATUS_ACTIVE, STATUS_PAUSED):
        return (
            "现在没有进行中的训练。先说「开始深蹲」之类的，我就开始记录。",
            STATUS_NEED_INPUT,
        )
    if w["status"] == STATUS_PAUSED:
        return (
            f"当前因为不适已暂停「{w['current']}」。"
            f"确认没问题可以说「继续」；要结束就说「结束训练」。",
            STATUS_NEED_INPUT,
        )

    reps = parse_reps(text) or w.get("current_reps")
    weight = parse_weight(text) or w.get("current_weight")
    w["sets_done"] += 1
    w["total_sets"] += 1
    w["log"].append(
        {
            "exercise": w["current"],
            "set": w["sets_done"],
            "reps": reps,
            "weight": weight,
            "at": _iso(now()),
        }
    )

    plan_sets = None
    for p in w["plan"]:
        if p.get("name") == w["current"]:
            plan_sets = p.get("sets")
            break

    lines = [f"「{w['current']}」第 {w['sets_done']} 组已记录" + (f"（{reps} 次）" if reps else "") + "。"]

    if plan_sets and w["sets_done"] >= plan_sets:
        lines.append(f"这个动作的 {plan_sets} 组已完成，可以换下一个动作，或说「结束训练」。")
    else:
        remaining = (plan_sets - w["sets_done"]) if plan_sets else None
        lines.append(
            f"休息 {REST_MIN}–{REST_MAX} 秒再开始下一组"
            + (f"，还剩 {remaining} 组。" if remaining else "。")
        )

    cue = _cue_if_due(w)
    if cue:
        lines.append(cue)
    return "\n".join(lines), STATUS_OK


def _sets_of(w: dict[str, Any], exercise: str) -> int:
    return sum(1 for e in w["log"] if e.get("exercise") == exercise)


def _cue_if_due(w: dict[str, Any]) -> str:
    """每 CUE_EVERY_SETS 组给一次动作提示，同一动作同一档位不重复给。"""
    if w["sets_done"] == 0 or w["sets_done"] % CUE_EVERY_SETS != 0:
        return ""
    key = f"{w['current']}#{w['sets_done']}"
    if key in w["cues"]:
        return ""
    w["cues"].append(key)

    tips: list[str] = []
    try:
        from . import equipment

        _name, info = equipment.find(w["current"])
        if info:
            tips = list(info.get("mistakes") or [])[:2]
    except Exception:
        tips = []

    if tips:
        return f"累计 {w['sets_done']} 组。动作提示：注意 " + "；".join(tips) + "。"
    return f"累计 {w['sets_done']} 组。留意动作是否变形，宁可减重量也别将就。"


def pain_report(fit: dict[str, Any], text: str) -> tuple[str, str]:
    from ...schemas import STATUS_OK

    w = ensure(fit)
    where = pain_location(text) or "未指明部位"
    w["pain"].append({"where": where, "note": (text or "").strip(), "at": _iso(now())})

    lines = [f"收到，先停下来。已记录：{where}不适。"]
    if w["status"] == STATUS_ACTIVE:
        w["status"] = STATUS_PAUSED
        lines.append(f"已暂停「{w['current']}」，本次训练累计 {w['total_sets']} 组。")

    lines.append(
        "\n**现在这样做**\n"
        "  1. 立刻停止引起疼痛的动作，不要「忍一忍做完」。\n"
        "  2. 停止活动、保持舒适体位，观察 10–15 分钟看是否缓解。\n"
        "  3. 急性期（48 小时内）不要热敷和按摩，也不要强行拉伸。\n"
        "  4. 可以继续练不牵涉该部位的其它动作，但强度要降下来。"
    )
    lines.append(
        "\n**这些情况请尽快就医**\n"
        "  - 关节肿胀、变形、无法承重或活动受限\n"
        "  - 疼痛持续加重，或伴随麻木、无力、放射性疼痛\n"
        "  - 胸痛、胸闷、头晕、呼吸困难（立即就医，不要继续训练）"
    )
    lines.append(
        "\n我不是医生，以上只是训练安全建议。"
        "接下来可以说「继续」（换不痛的部位练）或「结束训练」做总结。"
    )
    return "\n".join(lines), STATUS_OK


def end(fit: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """结束训练。返回 (事实数据, 错误说明)。事实数据交给模型写总结与下一步计划。"""
    w = ensure(fit)
    if w["status"] == STATUS_IDLE or not w["log"]:
        return {}, "本次还没有记录到任何组数，无需总结。先说「开始深蹲」开始记录吧。"

    w["status"] = STATUS_DONE
    w["ended"] = _iso(now())
    return facts(w), ""


def facts(w: dict[str, Any]) -> dict[str, Any]:
    """把训练记录整理成结构化事实，供模型撰写总结。"""
    by_exercise: dict[str, list[dict[str, Any]]] = {}
    for e in w["log"]:
        by_exercise.setdefault(e["exercise"], []).append(e)

    detail = []
    for name, entries in by_exercise.items():
        reps = [x["reps"] for x in entries if x.get("reps")]
        weights = [x["weight"] for x in entries if x.get("weight")]
        detail.append(
            {
                "exercise": name,
                "sets": len(entries),
                "reps": reps,
                "weights": weights,
                "total_reps": sum(reps) if reps else None,
                "volume": (sum(r * (weights[0] if weights else 0) for r in reps) or None)
                if reps and weights
                else None,
            }
        )

    return {
        "duration_min": _elapsed_min(w.get("started", "")),
        "total_sets": w["total_sets"],
        "exercises": detail,
        "pain": list(w["pain"]),
        "ended_early": bool(w["pain"]),
    }


def render_facts(f: dict[str, Any]) -> str:
    """把事实渲染成文本，供提示词注入与无模型时兜底。"""
    if not f:
        return "（无训练记录）"
    lines = []
    if f.get("duration_min") is not None:
        lines.append(f"- 训练时长：约 {f['duration_min']} 分钟")
    lines.append(f"- 总组数：{f.get('total_sets', 0)}")
    for e in f.get("exercises", []):
        bit = f"- {e['exercise']}：{e['sets']} 组"
        if e.get("reps"):
            bit += f"，次数 {'/'.join(str(r) for r in e['reps'])}"
        if e.get("weights"):
            bit += f"，重量 {'/'.join(str(x) for x in e['weights'])} 公斤"
        if e.get("volume"):
            bit += f"，总容量约 {round(e['volume'])} 公斤"
        lines.append(bit)
    if f.get("pain"):
        pains = "、".join(f"{p['where']}（{p['note']}）" for p in f["pain"])
        lines.append(f"- 不适记录：{pains}")
    return "\n".join(lines)
