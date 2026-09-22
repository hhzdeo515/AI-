"""训练状态机与事件提醒测试：不需要 API Key。

（end_workout 会让模型写总结；无 Key 时自动降级为事实清单，这里覆盖的就是降级路径。）
"""

from __future__ import annotations

import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from assistant_lite import config, session  # noqa: E402
from assistant_lite.agents.fitness import state as W  # noqa: E402
from assistant_lite.orchestrator import Orchestrator  # noqa: E402
from assistant_lite.schemas import STATUS_NEED_INPUT, STATUS_OK, Task  # noqa: E402


def _fresh_db() -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="alite-wo-"))
    config.DATA_DIR = tmp
    config.DB_PATH = tmp / "t.sqlite3"
    config.PROFILE_DIR = tmp / "profiles"
    config.UPLOAD_DIR = tmp / "uploads"
    config.EXPORT_DIR = tmp / "exports"
    session._local = threading.local()
    session._initialised = False
    return tmp


def _fit() -> dict:
    return {"awaiting": "", "draft": {}, "workout": W.default_workout()}


# --------------------------------------------------------------------------- #
# 识别器
# --------------------------------------------------------------------------- #
def test_detect_start() -> None:
    assert W.detect_start("开始深蹲") == "深蹲"
    assert W.detect_start("开始卧推 4组10次") == "卧推"
    assert W.detect_start("练一下硬拉") == "硬拉"
    assert W.detect_start("卧推 4组10次") == "卧推"
    # 泛称不能当动作
    assert W.detect_start("我想练腿") is None
    assert W.detect_start("开始练胸") is None
    # 不是开始
    assert W.detect_start("") is None
    assert W.detect_start("做完一组") is None
    assert W.detect_start("膝盖疼") is None
    assert W.detect_start("结束训练") is None


def test_is_set_done() -> None:
    assert W.is_set_done("做完一组")
    assert W.is_set_done("这组完成了")
    assert W.is_set_done("再来一组")
    assert not W.is_set_done("深蹲怎么做")
    assert not W.is_set_done("结束训练")
    assert not W.is_set_done("膝盖疼")
    assert not W.is_set_done("")


def test_is_pain_report() -> None:
    assert W.is_pain_report("膝盖疼")
    assert W.is_pain_report("腰有点痛")
    assert W.is_pain_report("肩膀不适")
    # 提问不算伤情报告
    assert not W.is_pain_report("深蹲膝盖疼怎么办")
    assert not W.is_pain_report("练完为什么会酸痛")
    # 没有疼痛词
    assert not W.is_pain_report("有点累")


def test_is_end() -> None:
    assert W.is_end("结束训练")
    assert W.is_end("练完了")
    assert W.is_end("不练了")
    assert not W.is_end("开始训练")


def test_parse_quantities() -> None:
    assert W.parse_sets("4组") == 4
    assert W.parse_reps("10次") == 10
    assert W.parse_reps("12个") == 12
    assert W.parse_weight("60公斤") == 60.0
    assert W.parse_weight("62.5kg") == 62.5
    assert W.parse_sets("随便") is None


# --------------------------------------------------------------------------- #
# 状态机
# --------------------------------------------------------------------------- #
def test_start_requires_exercise() -> None:
    f = _fit()
    text, status = W.start(f, "开始")
    assert status == STATUS_NEED_INPUT
    assert f["workout"]["status"] == W.STATUS_IDLE


def test_start_and_accumulate_sets() -> None:
    f = _fit()
    text, status = W.start(f, "开始卧推 3组10次 60公斤")
    assert status == STATUS_OK
    w = f["workout"]
    assert w["status"] == W.STATUS_ACTIVE
    assert w["current"] == "卧推"
    assert w["current_reps"] == 10
    assert w["current_weight"] == 60.0
    assert "3 组" in text

    text, _ = W.set_done(f, "做完一组")
    assert w["sets_done"] == 1 and w["total_sets"] == 1
    assert "第 1 组" in text
    assert "60–90 秒" in text, text

    W.set_done(f, "做完一组")
    assert w["sets_done"] == 2
    # 第 3 组应给动作提示
    text, _ = W.set_done(f, "做完一组")
    assert w["sets_done"] == 3
    assert "动作提示" in text or "累计 3 组" in text, text
    # 达标提示
    assert "3 组已完成" in text, text


def test_cue_not_repeated() -> None:
    """同一档位的提示只给一次。"""
    f = _fit()
    W.start(f, "开始深蹲")
    for _ in range(2):
        W.set_done(f, "做完一组")
    t3, _ = W.set_done(f, "做完一组")
    assert "累计 3 组" in t3
    # 第 4、5 组不给提示
    W.set_done(f, "做完一组")
    t5, _ = W.set_done(f, "做完一组")
    assert "累计 3 组" not in t5


def test_set_done_without_workout() -> None:
    f = _fit()
    text, status = W.set_done(f, "做完一组")
    assert status == STATUS_NEED_INPUT
    assert "没有进行中的训练" in text


def test_pain_pauses_and_advises() -> None:
    f = _fit()
    W.start(f, "开始深蹲")
    W.set_done(f, "做完一组")
    text, status = W.pain_report(f, "膝盖有点疼")
    assert status == STATUS_OK
    w = f["workout"]
    assert w["status"] == W.STATUS_PAUSED
    assert w["pain"][0]["where"] == "膝盖"
    assert "停下来" in text
    assert "就医" in text
    assert "我不是医生" in text


def test_set_done_blocked_while_paused() -> None:
    f = _fit()
    W.start(f, "开始深蹲")
    W.set_done(f, "做完一组")
    W.pain_report(f, "膝盖疼")
    text, status = W.set_done(f, "做完一组")
    assert status == STATUS_NEED_INPUT
    assert "已暂停" in text
    assert f["workout"]["sets_done"] == 1, "暂停期间不应继续记组"


def test_switch_exercise_mid_workout() -> None:
    f = _fit()
    W.start(f, "开始卧推")
    W.set_done(f, "做完一组")
    text, _ = W.start(f, "开始深蹲")
    assert f["workout"]["current"] == "深蹲"
    assert f["workout"]["sets_done"] == 0, "换动作后组数应归零"
    assert f["workout"]["total_sets"] == 1, "总组数应保留"
    assert "卧推" in text


def test_end_without_log() -> None:
    f = _fit()
    facts, err = W.end(f)
    assert not facts
    assert "没有记录" in err


def test_end_produces_facts() -> None:
    f = _fit()
    W.start(f, "开始卧推 2组10次 60公斤")
    W.set_done(f, "做完一组 10次")
    W.set_done(f, "做完一组 10次")
    W.start(f, "开始深蹲 2组8次")
    W.set_done(f, "做完一组 8次")
    W.pain_report(f, "膝盖疼")

    facts, err = W.end(f)
    assert not err, err
    assert facts["total_sets"] == 3
    assert facts["ended_early"] is True
    names = [e["exercise"] for e in facts["exercises"]]
    assert names == ["卧推", "深蹲"], names
    bench = facts["exercises"][0]
    assert bench["sets"] == 2
    assert bench["reps"] == [10, 10]
    assert bench["weights"] == [60.0, 60.0]
    assert bench["volume"] == 1200

    rendered = W.render_facts(facts)
    assert "卧推" in rendered and "总组数：3" in rendered
    assert "不适记录" in rendered


def test_ensure_fills_missing_keys() -> None:
    """老会话状态里没有 workout 字段时不应崩。"""
    fit = {"awaiting": ""}
    w = W.ensure(fit)
    assert w["status"] == W.STATUS_IDLE
    assert w["log"] == []
    # 结构被写坏的情况
    fit2 = {"workout": "坏掉了"}
    w2 = W.ensure(fit2)
    assert isinstance(w2, dict) and w2["status"] == W.STATUS_IDLE


# --------------------------------------------------------------------------- #
# 编排层：事件路由与粘性
# --------------------------------------------------------------------------- #
def test_workout_events_route_deterministically() -> None:
    _fresh_db()
    o = Orchestrator()
    for sa, want in [
        ("start_exercise", "start_exercise"),
        ("set_done", "set_done"),
        ("pain_report", "pain_report"),
        ("end_workout", "end_workout"),
    ]:
        scene, action, source = o.route(Task(text="x", event={"semantic_action": sa}))
        assert scene == "fitness", f"{sa} 应路由到 fitness，实际 {scene}"
        assert action == want
        assert source == "event"


def test_full_workout_session_via_events() -> None:
    """完整走一遍：开始 -> 两组 -> 膝痛 -> 结束。"""
    _fresh_db()
    o = Orchestrator()
    common = {"owner": "u", "session_id": "s"}

    r = o.handle(Task(text="开始卧推 3组10次 60公斤",
                      event={"semantic_action": "start_exercise"},
                      request_id="w1", **common))
    assert r.action == "start_exercise"
    assert "卧推" in r.text

    for i in (2, 3):
        r = o.handle(Task(text="做完一组", event={"semantic_action": "set_done"},
                          request_id=f"w{i}", **common))
        assert r.action == "set_done"
    assert "第 2 组" in r.text

    r = o.handle(Task(text="膝盖有点疼", event={"semantic_action": "pain_report"},
                      request_id="w4", **common))
    assert r.action == "pain_report"
    assert "已暂停" in r.text

    # 结束：给模型打桩，保证测试结果与「是否配置 API Key」无关
    from assistant_lite import llm as L

    original = L.chat
    L.chat = lambda *a, **kw: "【本次总结】完成卧推 2 组，因膝部不适提前结束。\n【下次计划】改用臀桥。"
    try:
        r = o.handle(Task(text="结束训练", event={"semantic_action": "end_workout"},
                          request_id="w5", **common))
    finally:
        L.chat = original

    assert r.action == "end_workout"
    assert r.status == STATUS_OK
    assert r.archive is True, "训练总结应归档"
    assert "本次总结" in r.text
    assert any(a.get("kind") == "resource" for a in r.artifacts), r.artifacts
    # 归档产出必须带真实资料 ID，不能是空壳
    assert all(a.get("id") for a in r.artifacts if a.get("kind") == "resource"), r.artifacts


def test_workout_summary_degrades_without_model() -> None:
    """模型不可用时降级为事实清单：不报错、不编造。"""
    _fresh_db()
    from assistant_lite import llm as L

    o = Orchestrator()
    common = {"owner": "u", "session_id": "s"}
    o.handle(Task(text="开始深蹲", event={"semantic_action": "start_exercise"},
                  request_id="d1", **common))
    o.handle(Task(text="做完一组", event={"semantic_action": "set_done"},
                  request_id="d2", **common))

    original = L.chat

    def boom(*a, **kw):
        raise L.LLMError("模拟模型不可用")

    L.chat = boom
    try:
        r = o.handle(Task(text="结束训练", event={"semantic_action": "end_workout"},
                          request_id="d3", **common))
    finally:
        L.chat = original

    assert r.status == STATUS_OK, r.text
    assert "模型暂时不可用" in r.text
    assert "总组数：1" in r.text, r.text


def test_workout_sticky_routing_by_text() -> None:
    """训练进行中，不含关键词的短句也应归锻炼场景。"""
    _fresh_db()
    o = Orchestrator()
    common = {"owner": "u", "session_id": "s"}

    o.handle(Task(text="开始深蹲", event={"semantic_action": "start_exercise"},
                  request_id="s1", **common))
    # 纯文本，无事件、无关键词
    r = o.handle(Task(text="做完一组", request_id="s2", **common))
    assert r.scene == "fitness", r.scene
    assert r.action == "set_done"

    # 结束后粘性解除
    o.handle(Task(text="结束训练", event={"semantic_action": "end_workout"},
                  request_id="s3", **common))
    state = session.load_state("u", "s")
    assert state["fitness"]["workout"]["status"] == W.STATUS_DONE


def test_workout_sticky_does_not_swallow_other_scenes() -> None:
    """回归：训练中一句解题请求不能被锻炼粘性吞掉。"""
    _fresh_db()
    o = Orchestrator()
    common = {"owner": "u", "session_id": "s"}
    o.handle(Task(text="开始深蹲", event={"semantic_action": "start_exercise"},
                  request_id="t1", **common))

    scene, _, _ = o.route(Task(text="计算 (18+24)*3"), session.load_state("u", "s"))
    assert scene == "exam", f"应路由到 exam，实际 {scene}"


def test_generic_body_part_is_not_a_workout() -> None:
    """'我想练腿' 不该开始一次训练记录。"""
    _fresh_db()
    o = Orchestrator()
    state = {"fitness": {"awaiting": "", "draft": {}, "workout": W.default_workout()}}
    action = o._agent_for("fitness")._infer_action(Task(text="我想练腿"), state["fitness"])
    assert action == "advice", action


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
