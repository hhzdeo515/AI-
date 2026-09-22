"""锻炼场景测试：建档问卷 + 器械知识库。不需要 API Key。

（器械"结合档案个性化润色"那一步要调模型，这里不覆盖；无档案时走纯知识库路径。）
"""

from __future__ import annotations

import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from assistant_lite import config, session  # noqa: E402
from assistant_lite.agents.fitness import equipment as EQ  # noqa: E402
from assistant_lite.agents.fitness import profile as P  # noqa: E402
from assistant_lite.orchestrator import Orchestrator  # noqa: E402
from assistant_lite.schemas import STATUS_NEED_INPUT, STATUS_OK, Task  # noqa: E402


def _fresh_db() -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="alite-fit-"))
    config.DATA_DIR = tmp
    config.DB_PATH = tmp / "t.sqlite3"
    config.PROFILE_DIR = tmp / "profiles"
    config.UPLOAD_DIR = tmp / "uploads"
    config.EXPORT_DIR = tmp / "exports"
    session._local = threading.local()
    session._initialised = False
    return tmp


# --------------------------------------------------------------------------- #
# 档案解析
# --------------------------------------------------------------------------- #
def test_parse_int_fields() -> None:
    assert P.parse_answer("age", "35岁")[0] == 35
    assert P.parse_answer("days_per_week", "每周 3 天")[0] == 3
    # 越界
    assert P.parse_answer("age", "300")[1] != ""
    assert P.parse_answer("days_per_week", "9")[1] != ""
    # 非数字
    assert P.parse_answer("age", "不知道")[1] != ""


def test_parse_float_fields() -> None:
    assert P.parse_answer("height_cm", "175")[0] == 175.0
    assert P.parse_answer("weight_kg", "70.5")[0] == 70.5
    assert P.parse_answer("height_cm", "30")[1] != "", "身高越界应被拒"
    assert P.parse_answer("weight_kg", "500")[1] != "", "体重越界应被拒"


def test_parse_choice_fields() -> None:
    assert P.parse_answer("goal", "想增肌")[0] == "增肌"
    assert P.parse_answer("level", "偶尔运动")[0] == "偶尔运动"
    # 别名
    assert P.parse_answer("goal", "我想减肥")[0] == "减脂"
    # 无法识别
    assert P.parse_answer("goal", "随便")[1] != ""


def test_parse_list_fields() -> None:
    assert P.parse_answer("injuries", "膝盖、腰部")[0] == ["膝盖", "腰部"]
    assert P.parse_answer("injuries", "无")[0] == ["无"]
    # "无"与其它项互斥
    assert P.parse_answer("injuries", "无、膝盖")[0] == ["膝盖"]
    assert P.parse_answer("injuries", "说不清")[1] != ""


def test_parse_empty() -> None:
    for key in ("age", "goal", "injuries"):
        assert P.parse_answer(key, "   ")[1] != ""


# --------------------------------------------------------------------------- #
# 档案存取
# --------------------------------------------------------------------------- #
def test_profile_save_load_roundtrip() -> None:
    _fresh_db()
    data = {
        "age": 35,
        "height_cm": 175.0,
        "weight_kg": 70.0,
        "goal": "增肌",
        "level": "偶尔运动",
        "injuries": ["无"],
        "conditions": ["无"],
        "days_per_week": 3,
        "equipment": ["哑铃"],
    }
    path = P.save("alice", data)
    assert path.is_file()
    loaded = P.load("alice")
    assert loaded == data
    assert P.load("nobody") == {}, "不存在的档案应返回空字典"


def test_owner_filename_safety() -> None:
    """owner 来自外部，不能用来做路径穿越。"""
    _fresh_db()
    P.save("../../evil", {"age": 30})
    files = list(config.PROFILE_DIR.glob("*.json"))
    assert len(files) == 1
    assert ".." not in files[0].name
    assert files[0].parent == config.PROFILE_DIR


def test_profile_summary_and_risk() -> None:
    data = {
        "age": 40,
        "height_cm": 170.0,
        "weight_kg": 85.0,
        "injuries": ["膝盖"],
        "conditions": ["高血压"],
    }
    s = P.summarize(data)
    assert "年龄：40" in s
    assert "膝盖" in s
    assert P.bmi(data) == 29.4
    r = P.risk_notes(data)
    assert "膝盖" in r and "高血压" in r
    # "无"不应被当成风险
    assert P.risk_notes({"injuries": ["无"], "conditions": ["无"]}) == ""


def test_missing_fields_and_question() -> None:
    assert P.missing_fields({}) == [f[0] for f in P.FIELDS]
    partial = {"age": 30, "height_cm": 175.0}
    missing = P.missing_fields(partial)
    assert "age" not in missing and "height_cm" not in missing
    assert "体重" in P.question_for("weight_kg")
    assert "可选" in P.question_for("goal")


# --------------------------------------------------------------------------- #
# 器械知识库
# --------------------------------------------------------------------------- #
def test_equipment_find() -> None:
    name, info = EQ.find("史密斯机怎么用")
    assert name == "史密斯机" and info is not None

    name, _ = EQ.find("高位下拉")
    assert name == "高位下拉"

    # 别名
    assert EQ.find("lat pulldown")[0] == "高位下拉"
    assert EQ.find("treadmill")[0] == "跑步机"

    # 找不到
    assert EQ.find("这个怎么用") == (None, None)
    assert EQ.find("") == (None, None)


def test_equipment_render() -> None:
    name, info = EQ.find("深蹲")
    assert name and info
    text = EQ.render(name, info)
    assert name in text
    assert "用法要点" in text
    assert "常见错误" in text


def test_equipment_no_duplicate_alias() -> None:
    """别名索引不应互相覆盖导致查错。"""
    for n in EQ.names():
        assert EQ.find(n)[0] == n, f"{n} 自查找应命中自己"


# --------------------------------------------------------------------------- #
# Agent / 编排
# --------------------------------------------------------------------------- #
ANSWERS = [
    ("35", "年龄"),
    ("175", "身高"),
    ("70", "体重"),
    ("想增肌", "目标"),
    ("偶尔运动", "基础"),
    ("无", "伤病"),
    ("无", "慢性病"),
    ("3", "天数"),
    ("哑铃", "器械"),
]


def test_questionnaire_full_flow_no_api() -> None:
    """完整走一遍建档问卷：除了最后一次保存，全程不需要 API。"""
    _fresh_db()
    o = Orchestrator()

    r = o.handle(Task(text="建立健康档案", owner="u", session_id="s", request_id="p0"))
    assert r.scene == "fitness"
    assert r.action == "profile"
    assert "年龄" in r.text, r.text
    assert "1/9" in r.text, r.text

    for i, (answer, _label) in enumerate(ANSWERS, start=1):
        r = o.handle(
            Task(text=answer, owner="u", session_id="s", request_id=f"p{i}")
        )
        assert r.scene == "fitness", f"第{i}步应留在锻炼场景，实际 {r.scene}"
        assert r.action == "profile"

    # 最后一步应完成建档
    assert "已建立" in r.text, r.text
    saved = P.load("u")
    assert saved.get("age") == 35
    assert saved.get("goal") == "增肌"
    assert saved.get("equipment") == ["哑铃"]
    assert not P.missing_fields(saved), f"档案仍缺字段：{P.missing_fields(saved)}"

    # 建档完成后粘性解除
    state = session.load_state("u", "s")
    assert not state["fitness"]["awaiting"]


def test_questionnaire_rejects_bad_answer() -> None:
    _fresh_db()
    o = Orchestrator()
    o.handle(Task(text="建立健康档案", owner="u", session_id="s", request_id="q0"))
    r = o.handle(Task(text="很大", owner="u", session_id="s", request_id="q1"))
    assert r.status == STATUS_NEED_INPUT
    assert "数字" in r.text
    # 还在等年龄，不能前进
    assert session.load_state("u", "s")["fitness"]["awaiting"] == "age"


def test_questionnaire_cancel() -> None:
    _fresh_db()
    o = Orchestrator()
    o.handle(Task(text="建立健康档案", owner="u", session_id="s", request_id="c0"))
    r = o.handle(Task(text="取消建档", owner="u", session_id="s", request_id="c1"))
    assert "已退出" in r.text
    assert session.load_state("u", "s")["fitness"]["awaiting"] == ""


def test_equipment_text_lookup_no_profile_no_api() -> None:
    """没有档案时不调模型，纯知识库作答。"""
    _fresh_db()
    o = Orchestrator()
    r = o.handle(Task(text="史密斯机怎么用", owner="nobody", session_id="s", request_id="e1"))
    assert r.scene == "fitness"
    assert r.action == "equipment"
    assert r.status == STATUS_OK
    assert "史密斯机" in r.text
    assert "用法要点" in r.text
    assert "还没有健康档案" in r.text


def test_equipment_unknown_asks_back() -> None:
    _fresh_db()
    o = Orchestrator()
    r = o.handle(Task(text="这个器械怎么用", owner="u", session_id="s", request_id="e2"))
    assert r.status == STATUS_NEED_INPUT
    assert "史密斯机" in r.text, "应给出内置器械清单"


def test_fitness_routing_by_keyword() -> None:
    _fresh_db()
    o = Orchestrator()
    for text in ["我想增肌怎么练", "深蹲膝盖疼怎么办", "帮我建立健康档案"]:
        scene, _, source = o.route(Task(text=text))
        assert scene == "fitness", f"{text!r} 应路由到 fitness，实际 {scene}"
        assert source == "keyword"


# --------------------------------------------------------------------------- #
def _run_all() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL  {fn.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"  ERROR {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass
    raise SystemExit(_run_all())
