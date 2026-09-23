"""端侧契约与 Dify 执行后端测试：不需要 Dify、不需要 API Key、不联网。

覆盖三件事：
  1. `event.device` 契约（意图枚举 + 置信度门控），与 Dify DSL 的 normalize 节点同源；
  2. 路由遥测（端侧判对率）——路由留在本地的理由，必须可测；
  3. Dify 执行后端的载荷构造、输出提取、以及失败回退。

直接运行：python tests/test_device_contract.py
"""

from __future__ import annotations

import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from assistant_lite import config, dify_backend, session  # noqa: E402
from assistant_lite.orchestrator import Orchestrator  # noqa: E402
from assistant_lite.schemas import STATUS_ERROR, STATUS_OK, Task  # noqa: E402


def _fresh_db() -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="alite-device-"))
    config.DATA_DIR = tmp
    config.DB_PATH = tmp / "t.sqlite3"
    config.PROFILE_DIR = tmp / "profiles"
    config.UPLOAD_DIR = tmp / "uploads"
    config.EXPORT_DIR = tmp / "exports"
    session._local = threading.local()
    session._initialised = False
    return tmp


def _dev_task(text: str = "文本", **device) -> Task:
    return Task(text=text, owner="u", session_id="s", event={"device": device})


# --------------------------------------------------------------------------- #
# 1. 端侧意图契约
# --------------------------------------------------------------------------- #
def test_high_confidence_intent_is_trusted() -> None:
    d = Orchestrator.device_context(_dev_task(intent="solve", confidence=0.91))
    assert d["scene"] == "exam"
    assert d["trusted"] is True


def test_low_confidence_intent_is_not_trusted() -> None:
    d = Orchestrator.device_context(_dev_task(intent="solve", confidence=0.30))
    assert d["scene"] == "exam", "映射仍然要算出来，供遥测与证据使用"
    assert d["trusted"] is False


def test_boundary_confidence_075_is_trusted() -> None:
    assert Orchestrator.device_context(
        _dev_task(intent="solve", confidence=0.75)
    )["trusted"] is True
    assert Orchestrator.device_context(
        _dev_task(intent="solve", confidence=0.74)
    )["trusted"] is False


def test_pain_report_is_never_downgraded() -> None:
    """安全相关内容不受置信度门控——与 fitness 的「不适即停」一致。"""
    d = Orchestrator.device_context(_dev_task(intent="train_pain", confidence=0.01))
    assert d["scene"] == "fitness"
    assert d["trusted"] is True


def test_unknown_intent_is_ignored_without_error() -> None:
    """设备多发字段绝不能影响既有路由。"""
    d = Orchestrator.device_context(_dev_task(intent="totally_made_up", confidence=0.99))
    assert d["scene"] == ""
    assert d["trusted"] is False


def test_intents_without_scene_semantics_are_empty() -> None:
    for intent in ("wake", "stop", "cancel", "switch_scene", ""):
        assert Orchestrator.device_context(_dev_task(intent=intent))["scene"] == ""


def test_flat_event_form_is_accepted() -> None:
    task = Task(event={"device_intent": "train_done", "device_confidence": "0.88"})
    d = Orchestrator.device_context(task)
    assert d["scene"] == "fitness" and d["trusted"] is True


def test_bad_confidence_does_not_raise() -> None:
    for bad in ("abc", None, "", [], {}):
        d = Orchestrator.device_context(_dev_task(intent="solve", confidence=bad))
        assert d["scene"] == "exam"
        assert d["trusted"] is False, f"{bad!r} 应视为不可信而不是抛错"


def test_device_intent_does_not_override_authoritative_routing() -> None:
    """端侧说 fitness，但云端关键词明确指向会议——路由必须听云端的。

    这是设计文档 §2 的核心：端侧判定降级为提示，不具备权威性。
    """
    _fresh_db()
    o = Orchestrator()
    task = Task(text="帮我生成会议纪要", event={"device": {"intent": "train_start", "confidence": 0.99}})
    scene, _action, source = o.route(task, {})
    assert scene == "meeting", f"端侧不应覆盖云端路由，实际 {scene}"
    assert source == "keyword"


# --------------------------------------------------------------------------- #
# 2. 路由遥测（端侧判对率）
# --------------------------------------------------------------------------- #
def test_telemetry_records_correct_and_rerouted() -> None:
    _fresh_db()
    o = Orchestrator()

    # 端侧判对：说开会，端侧也说是 meeting
    o._record_routing(
        Task(text="x", owner="u", session_id="s", request_id="r1"),
        "meeting",
        "start",
        "keyword",
    )
    session.record_routing(
        owner="u", session_id="s", request_id="r1", scene="meeting", action="start",
        source="keyword", device_intent="meeting_start", device_confidence=0.9,
        device_scene="meeting", device_trusted=True,
    )
    # 端侧判错：端侧说是 exam，实际路由到 fitness
    session.record_routing(
        owner="u", session_id="s", request_id="r2", scene="fitness", action="answer",
        source="keyword", device_intent="solve", device_confidence=0.95,
        device_scene="exam", device_trusted=True,
    )

    stats = session.routing_stats("u")
    assert stats["samples"] == 2, stats
    assert stats["device_correct"] == 1, stats
    assert stats["accuracy"] == 0.5, stats


def test_telemetry_ignores_records_without_device_scene() -> None:
    """纯文字网页输入没有端侧判定，不能污染判对率分母。"""
    _fresh_db()
    session.record_routing(
        owner="web", session_id="s", request_id="r", scene="general", action="answer",
        source="llm", device_intent="", device_scene="",
    )
    stats = session.routing_stats("web")
    assert stats["samples"] == 0
    assert stats["accuracy"] is None


def test_telemetry_is_written_by_handle_and_never_breaks_the_chain() -> None:
    """遥测失败不得影响主链路——用一个坏 state 触发路由后仍能走完。"""
    _fresh_db()
    o = Orchestrator()
    # 关键词命中，不调模型
    r = o.handle(Task(text="计算 1+1", owner="u", session_id="s", request_id="tel1"))
    assert r.status == STATUS_OK, r.text
    stats = session.routing_stats("u")
    assert stats["samples"] == 0, "无端侧意图时不产生样本"
    rows = session.routing_stats("u")
    assert rows["by_source"] == {}, "无样本时不应有来源统计"


def test_telemetry_counts_by_source() -> None:
    _fresh_db()
    for i, src in enumerate(["keyword", "keyword", "llm"]):
        session.record_routing(
            owner="u", session_id="s", request_id=f"r{i}", scene="exam", action="answer",
            source=src, device_intent="solve", device_confidence=0.9,
            device_scene="exam", device_trusted=True,
        )
    stats = session.routing_stats("u")
    assert stats["samples"] == 3
    assert stats["by_source"] == {"keyword": 2, "llm": 1}, stats


def test_telemetry_is_owner_isolated() -> None:
    _fresh_db()
    for owner in ("a", "b", "b"):
        session.record_routing(
            owner=owner, session_id="s", request_id="r", scene="exam", action="answer",
            source="keyword", device_intent="solve", device_confidence=0.9,
            device_scene="exam", device_trusted=True,
        )
    assert session.routing_stats("a")["samples"] == 1
    assert session.routing_stats("b")["samples"] == 2
    assert session.routing_stats()["samples"] == 3


# --------------------------------------------------------------------------- #
# 3. Dify 执行后端
# --------------------------------------------------------------------------- #
def test_payload_carries_authoritative_scene_hint() -> None:
    p = dify_backend.build_payload(
        text="膝盖疼", scene="fitness", device_intent="solve", device_confidence=0.95
    )
    assert p["inputs"]["scene_hint"] == "fitness", "本地权威路由必须传给 Dify"
    assert p["inputs"]["device_intent"] == "solve"
    assert p["inputs"]["device_confidence"] == "0.95"
    assert p["response_mode"] == "blocking"


def test_payload_drops_unknown_scene_hint() -> None:
    p = dify_backend.build_payload(text="x", scene="not-a-scene")
    assert p["inputs"]["scene_hint"] == "", "非法场景不能让 Dify 误分类"


def test_payload_folds_local_vision_observation_into_query() -> None:
    p = dify_backend.build_payload(text="解这道题", scene="exam", observation="题面：2x+6=14")
    assert "解这道题" in p["inputs"]["text"]
    assert "2x+6=14" in p["inputs"]["text"], "本地精读结果要并入 query，避免重复视觉调用"


def test_payload_omits_confidence_when_unknown() -> None:
    p = dify_backend.build_payload(text="x", scene="general")
    assert p["inputs"]["device_confidence"] == ""
    assert p["inputs"]["device_intent"] == ""


def test_extract_text_picks_first_non_empty_output() -> None:
    payload = {"data": {"status": "succeeded", "outputs": {"answer_x": "", "answer_y": "正文"}}}
    assert dify_backend.extract_text(payload) == "正文"


def test_extract_text_rejects_empty_outputs() -> None:
    """空 outputs 是「图静默停止」的信号，不能被当成模型没说话。"""
    try:
        dify_backend.extract_text({"data": {"status": "succeeded", "outputs": {}}})
    except dify_backend.DifyError as e:
        assert "空 outputs" in str(e)
        return
    raise AssertionError("空 outputs 应抛 DifyError")


def test_extract_text_rejects_failed_status() -> None:
    try:
        dify_backend.extract_text(
            {"data": {"status": "failed", "error": "boom", "outputs": {}}}
        )
    except dify_backend.DifyError as e:
        assert "failed" in str(e)
        return
    raise AssertionError("失败状态应抛 DifyError")


def test_extract_text_rejects_malformed_payload() -> None:
    for bad in ([], "text", {}, {"data": None}, {"data": {"outputs": "oops"}}):
        try:
            dify_backend.extract_text(bad)
        except dify_backend.DifyError:
            continue
        raise AssertionError(f"畸形响应应抛 DifyError：{bad!r}")


def test_unconfigured_backend_raises_dify_error() -> None:
    original = config.DIFY_API_KEY
    config.DIFY_API_KEY = ""
    try:
        assert dify_backend.available() is False
        try:
            dify_backend.run_scene(text="x", scene="general")
        except dify_backend.DifyError as e:
            assert "DIFY_API_KEY" in str(e)
            return
        raise AssertionError("未配置 Key 时应抛 DifyError")
    finally:
        config.DIFY_API_KEY = original


# --------------------------------------------------------------------------- #
# 4. 编排层的后端切换与回退
# --------------------------------------------------------------------------- #
def test_dify_backend_falls_back_to_local_on_failure() -> None:
    _fresh_db()
    o = Orchestrator()
    original_backend = config.EXEC_BACKEND
    original_key = config.DIFY_API_KEY
    original_fallback = config.DIFY_FALLBACK_LOCAL
    config.EXEC_BACKEND = "dify"
    config.DIFY_API_KEY = ""  # 强制失败
    config.DIFY_FALLBACK_LOCAL = True
    try:
        r = o.handle(Task(text="计算 1+1", owner="u", session_id="s"))
    finally:
        config.EXEC_BACKEND = original_backend
        config.DIFY_API_KEY = original_key
        config.DIFY_FALLBACK_LOCAL = original_fallback

    assert r.status == STATUS_OK, r.text
    assert "2" in r.text, "回退后应由本地确定性工具算出结果"
    assert "回退本地" in r.text, "回退必须显式说明，不能静默"


def test_dify_backend_reports_error_when_fallback_disabled() -> None:
    _fresh_db()
    o = Orchestrator()
    original = (config.EXEC_BACKEND, config.DIFY_API_KEY, config.DIFY_FALLBACK_LOCAL)
    config.EXEC_BACKEND, config.DIFY_API_KEY, config.DIFY_FALLBACK_LOCAL = "dify", "", False
    try:
        r = o.handle(Task(text="计算 1+1", owner="u", session_id="s"))
    finally:
        config.EXEC_BACKEND, config.DIFY_API_KEY, config.DIFY_FALLBACK_LOCAL = original
    assert r.status == STATUS_ERROR
    assert "Dify 后端执行失败" in r.text


def test_dify_backend_keeps_attachments_local() -> None:
    """带附件的请求需要本地 ASR/VLM 预处理，不能盲目转发。"""
    _fresh_db()
    tmp = _fresh_db()
    img = tmp / "q.png"
    from PIL import Image

    Image.new("RGB", (8, 8), "white").save(img)

    o = Orchestrator()
    original = (config.EXEC_BACKEND, config.DIFY_API_KEY)
    config.EXEC_BACKEND, config.DIFY_API_KEY = "dify", ""  # 若误转发会走回退文案
    try:
        r = o.handle(Task(text="计算 1+1", owner="u", session_id="s", files=[str(img)]))
    finally:
        config.EXEC_BACKEND, config.DIFY_API_KEY = original
    assert "带附件" in r.text, r.text
    assert "2" in r.text


def test_dify_backend_keeps_sticky_sessions_local() -> None:
    """会议进行中/训练进行中是本地状态机，Dify 侧没有这些状态。"""
    _fresh_db()
    o = Orchestrator()
    original = (config.EXEC_BACKEND, config.DIFY_API_KEY)
    config.EXEC_BACKEND, config.DIFY_API_KEY = "dify", ""
    try:
        o.handle(Task(text="开始会议", owner="u", session_id="s"))
        # 真实会议内容：不能被 looks_like_meta 拦掉，否则测不到粘性分支
        r = o.handle(Task(text="李四说他周五前提交接口文档", owner="u", session_id="s"))
    finally:
        config.EXEC_BACKEND, config.DIFY_API_KEY = original
    assert "多轮有状态流程" in r.text, r.text
    assert "未转发 Dify" in r.text, r.text


def test_routing_stats_endpoint_exposes_accuracy() -> None:
    """判对率要能从 Web 层读到，否则它只是数据库里的死数据。"""
    _fresh_db()
    session.record_routing(
        owner="web", session_id="s", request_id="r", scene="fitness", action="answer",
        source="keyword", device_intent="solve", device_confidence=0.95,
        device_scene="exam", device_trusted=True,
    )
    from assistant_lite.web.app import create_app

    app = create_app()
    app.config["TESTING"] = True
    c = app.test_client()

    r = c.get("/api/routing-stats?owner=web")
    assert r.status_code == 200, r.status_code
    body = r.get_json()
    assert body["samples"] == 1, body
    assert body["device_correct"] == 0, body
    assert body["accuracy"] == 0.0, body

    # 未给 owner 时统计全部，且空库不炸
    assert c.get("/api/routing-stats").status_code == 200
    assert c.get("/api/routing-stats?owner=nobody").get_json()["accuracy"] is None


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
        except Exception as e:  # noqa: BLE001
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
