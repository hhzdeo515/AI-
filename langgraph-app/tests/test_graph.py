"""图结构与端侧契约测试：不需要 Dify、不需要 API Key、不联网。

策略与 assistant-lite 一致：**需要模型的路径一律打桩**，
所以无论有没有配 Key，回归结果都一样。

直接运行：python tests/test_graph.py
"""

from __future__ import annotations

import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lg_assistant import config, graph, llm, nodes, routing, store  # noqa: E402
from lg_assistant.ported import calc, grids, speech  # noqa: E402
from lg_assistant.routing import device_context  # noqa: E402


def _fresh() -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="lgassist-"))
    config.DATA_DIR = tmp
    config.DB_PATH = tmp / "app.sqlite3"
    config.CHECKPOINT_DB = tmp / "checkpoints.sqlite3"
    store._local = threading.local()
    store._initialised = False
    return tmp


def _run(**state):
    """跑图，不落检查点（单测不需要续跑）。"""
    app = graph.build_graph(None)
    base = {
        "text": "",
        "owner": "u",
        "session_id": "s",
        "request_id": "r",
        "event": {},
        "notes": [],
    }
    base.update(state)
    return app.invoke(base, graph.run_config(base["owner"], base["session_id"]))


def _stub_llm(text: str = "桩回复"):
    """把模型调用换成桩，返回 (restore 函数, 调用记录)。"""
    calls: list = []
    original_chat = llm.chat

    def fake_chat(messages, **kw):
        calls.append(messages)
        return text

    llm.chat = fake_chat
    return (lambda: setattr(llm, "chat", original_chat)), calls


def _no_router():
    original = nodes._ROUTER
    nodes.set_router(None)
    return lambda: nodes.set_router(original)


# --------------------------------------------------------------------------- #
# 图结构
# --------------------------------------------------------------------------- #
def test_graph_compiles_and_has_expected_nodes() -> None:
    app = graph.build_graph(None)
    names = set(app.get_graph().nodes)
    for expected in (
        "__start__", "device_gate", "route", "telemetry",
        "meta_command", "calc_quick", "dify_scene", "local_llm", "postprocess", "__end__",
    ):
        assert expected in names, f"缺少节点 {expected}；实际 {sorted(names)}"


def test_dispatch_rules_are_structural() -> None:
    """这些守卫在 assistant-lite 里是 if，现在是图上的条件边。逐条验证。"""
    assert graph.dispatch({"routing": {"action": "stop_playback"}}) == "meta"
    assert graph.dispatch({"routing": {"action": "switch_scene"}}) == "meta"
    # 带附件必须留在本地（需先经 ASR/VLM 预处理）
    assert graph.dispatch({"text": "x", "files": ["a.png"], "event": {}}) == "local"
    # 粘性会话是本地状态机，Dify 侧没有这些状态
    assert graph.dispatch({"text": "x", "files": [], "event": {"_sticky": {"meeting": True}}}) == "local"

    original = config.EXEC_BACKEND
    config.EXEC_BACKEND = "dify"
    try:
        assert graph.dispatch({"text": "x", "files": [], "event": {}}) == "dify"
    finally:
        config.EXEC_BACKEND = original


def test_meta_action_short_circuits() -> None:
    out = _run(text="停止", event={"semantic_action": "stop_playback"})
    assert out["routing"]["action"] == "stop_playback"
    assert out["routing"]["source"] == "event"
    assert "停止播报" in out["result"]["text"]


# --------------------------------------------------------------------------- #
# 端侧契约（与 dify-multiagent 的 normalize 节点同源）
# --------------------------------------------------------------------------- #
def test_device_gate_trusts_high_confidence() -> None:
    d = device_context({"device": {"intent": "solve", "confidence": 0.91}})
    assert d["scene"] == "exam" and d["trusted"] is True


def test_device_gate_distrusts_low_confidence() -> None:
    d = device_context({"device": {"intent": "solve", "confidence": 0.30}})
    assert d["scene"] == "exam", "映射仍要算出来，供遥测使用"
    assert d["trusted"] is False


def test_device_gate_boundary() -> None:
    assert device_context({"device": {"intent": "solve", "confidence": 0.75}})["trusted"] is True
    assert device_context({"device": {"intent": "solve", "confidence": 0.74}})["trusted"] is False


def test_device_gate_never_downgrades_pain() -> None:
    d = device_context({"device": {"intent": "train_pain", "confidence": 0.01}})
    assert d["scene"] == "fitness" and d["trusted"] is True


def test_device_gate_ignores_unknown_intent() -> None:
    d = device_context({"device": {"intent": "made_up", "confidence": 0.99}})
    assert d["scene"] == "" and d["trusted"] is False


def test_device_gate_accepts_flat_form_and_bad_confidence() -> None:
    assert device_context({"device_intent": "train_done", "device_confidence": "0.9"})["scene"] == "fitness"
    assert device_context({"device": {"intent": "solve", "confidence": "abc"}})["trusted"] is False


# --------------------------------------------------------------------------- #
# 路由（五级 + 顺序）
# --------------------------------------------------------------------------- #
def test_route_event_wins() -> None:
    r = routing.route(text="随便", event={"semantic_action": "start_meeting"})
    assert r == {"scene": "meeting", "action": "start", "source": "event"}


def test_route_hint_wins_over_keyword() -> None:
    r = routing.route(text="这道题怎么做", scene_hint="fitness")
    assert r["scene"] == "fitness" and r["source"] == "hint"


def test_route_keyword_beats_sticky() -> None:
    """回归：会议进行中一句「计算 (18+24)*3」曾被当成会议内容吞掉。

    顺序必须是 事件 → 显式 → **关键词** → 粘性 → LLM。
    """
    r = routing.route(text="计算 (18+24)*3", sticky={"meeting": True})
    assert r["scene"] == "exam", r
    assert r["source"] == "keyword"


def test_route_sticky_used_when_no_keyword() -> None:
    r = routing.route(text="嗯嗯接着说", sticky={"meeting": True})
    assert r["scene"] == "meeting" and r["source"] == "sticky"


def test_route_export_word_beats_other_keywords() -> None:
    """「导出刚才的纪要成 Word」同时含 meeting 的「纪要」与 resource 的「导出」。"""
    scores = routing.keyword_scores("导出刚才的会议纪要成 Word")
    assert scores["resource"] == 0.95
    assert routing.route(text="导出刚才的会议纪要成 Word")["scene"] == "resource"


def test_route_llm_fallback_and_failure() -> None:
    r = routing.route(text="今天天气怎么样", llm_router=lambda *a: {"scene": "general", "action": "answer"})
    assert r["scene"] == "general" and r["source"] == "llm"

    def boom(*a):
        raise RuntimeError("模型挂了")

    r2 = routing.route(text="今天天气怎么样", llm_router=boom)
    assert r2["scene"] == "general" and r2["source"] == "llm_failed"


def test_route_never_calls_llm_when_rules_hit() -> None:
    """零 token 路由：规则命中时绝不允许发起模型调用。"""
    called = []
    routing.route(text="帮我生成会议纪要", llm_router=lambda *a: called.append(1) or {})
    routing.route(text="计算 1+1", llm_router=lambda *a: called.append(1) or {})
    routing.route(text="x", event={"semantic_action": "set_done"}, llm_router=lambda *a: called.append(1) or {})
    assert called == [], "规则命中时不应走模型兜底"


# --------------------------------------------------------------------------- #
# 零 token 算术快路径
# --------------------------------------------------------------------------- #
def test_calc_quick_hit_uses_no_model() -> None:
    restore, calls = _stub_llm()
    drop_router = _no_router()
    try:
        out = _run(text="计算 (18+24)*3")
    finally:
        drop_router()
        restore()
    assert "126" in out["result"]["text"]
    assert out["result"]["backend"] == "local"
    assert calls == [], "算术快路径绝不能调模型"


def test_calc_quick_miss_falls_through_to_llm() -> None:
    restore, calls = _stub_llm("这是模型回答")
    drop_router = _no_router()
    try:
        out = _run(text="计算 x+1")
    finally:
        drop_router()
        restore()
    assert out["result"]["text"] == "这是模型回答"
    assert len(calls) == 1, "未命中快路径时应落到模型"


def test_calc_quick_rejects_unsafe_expression() -> None:
    """AST 安全求值：危险表达式必须落到模型而不是被当成算术求值。"""
    out = nodes.calc_quick({"text": "计算 __import__('os').system('ls')", "files": []})
    assert out.get("calc_hit") is False
    assert "result" not in out, "危险表达式不得产出 result"

    for bad in ["计算 1+", "计算 x+1", "计算 2**100", "计算 1/0"]:
        assert nodes.calc_quick({"text": bad, "files": []}).get("calc_hit") is False, bad


# --------------------------------------------------------------------------- #
# 播报语与归档
# --------------------------------------------------------------------------- #
def test_postprocess_fills_speech_without_extra_model_call() -> None:
    restore, calls = _stub_llm("## 标题\n\n**结论**：一切正常。" * 12)
    drop_router = _no_router()
    try:
        out = _run(text="随便问问")
    finally:
        drop_router()
        restore()
    assert len(calls) == 1, "播报语必须由生成正文的同一次调用顺带产出，不得二次调用"
    assert out["speech"], "播报语不能为空"
    assert "**" not in out["speech"], "播报语必须去 Markdown"
    assert len(out["speech"]) <= speech.MAX_CHARS + 1


def test_postprocess_archives_scene_output() -> None:
    _fresh()
    restore, _ = _stub_llm("纪要正文")
    drop_router = _no_router()
    try:
        out = _run(text="帮我生成会议纪要")
    finally:
        drop_router()
        restore()
    assert out["routing"]["scene"] == "meeting"
    assert out.get("archived_id"), "会议产出必须归档"
    rows = store.list_resources("u")
    assert len(rows) == 1 and rows[0]["scene"] == "meeting"


def test_general_scene_is_not_archived() -> None:
    _fresh()
    restore, _ = _stub_llm("日常回复")
    drop_router = _no_router()
    try:
        out = _run(text="你好")
    finally:
        drop_router()
        restore()
    assert out["routing"]["scene"] == "general"
    assert not out.get("archived_id")
    assert store.list_resources("u") == []


# --------------------------------------------------------------------------- #
# 遥测：端侧判对率
# --------------------------------------------------------------------------- #
def test_telemetry_records_device_misjudgement() -> None:
    _fresh()
    restore, _ = _stub_llm("处理完毕")
    drop_router = _no_router()
    try:
        # 端侧说 exam，但文本命中 fitness 关键词 -> 云端 reroute
        out = _run(
            text="我膝盖有点疼，练不下去了",
            event={"device": {"intent": "solve", "confidence": 0.95}},
        )
    finally:
        drop_router()
        restore()
    assert out["routing"]["scene"] == "fitness", out["routing"]
    stats = store.routing_stats("u")
    assert stats["samples"] == 1, stats
    assert stats["device_correct"] == 0, stats
    assert stats["accuracy"] == 0.0, stats


def test_telemetry_ignores_text_only_requests() -> None:
    _fresh()
    restore, _ = _stub_llm("回复")
    drop_router = _no_router()
    try:
        _run(text="你好")
    finally:
        drop_router()
        restore()
    assert store.routing_stats("u")["samples"] == 0, "无端侧意图不应产生样本"


def test_telemetry_counts_sources() -> None:
    _fresh()
    restore, _ = _stub_llm("回复")
    drop_router = _no_router()
    try:
        _run(
            text="计算 1+1",
            event={"device": {"intent": "solve", "confidence": 0.9}},
        )
        _run(
            text="这道题怎么做",
            request_id="r2",
            event={"device": {"intent": "solve", "confidence": 0.9}},
        )
    finally:
        drop_router()
        restore()
    stats = store.routing_stats("u")
    assert stats["samples"] == 2, stats
    assert stats["by_source"].get("keyword", 0) >= 1, stats
    assert stats["device_correct"] == 2, "两条端侧都判 exam，应全部判对"


# --------------------------------------------------------------------------- #
# Dify 后端（打桩，不联网）
# --------------------------------------------------------------------------- #
def test_dify_backend_used_when_configured() -> None:
    _fresh()
    from lg_assistant import dify_backend

    original_run = dify_backend.run_scene
    original_backend = config.EXEC_BACKEND
    config.EXEC_BACKEND = "dify"
    seen = {}

    def fake_run_scene(**kw):
        seen.update(kw)
        return "来自 Dify 的正文"

    dify_backend.run_scene = fake_run_scene
    drop_router = _no_router()
    try:
        out = _run(text="这道题怎么做")
    finally:
        drop_router()
        dify_backend.run_scene = original_run
        config.EXEC_BACKEND = original_backend

    assert out["result"]["backend"] == "dify"
    assert out["result"]["text"] == "来自 Dify 的正文"
    assert seen["scene"] == "exam", "本地权威路由结论必须传给 Dify"


def test_dify_failure_falls_back_to_local_with_explicit_note() -> None:
    _fresh()
    from lg_assistant import dify_backend

    original_run = dify_backend.run_scene
    original_backend, original_fb = config.EXEC_BACKEND, config.DIFY_FALLBACK_LOCAL
    config.EXEC_BACKEND, config.DIFY_FALLBACK_LOCAL = "dify", True

    def boom(**kw):
        raise dify_backend.DifyError("模拟失败")

    dify_backend.run_scene = boom
    restore, _ = _stub_llm("本地回答")
    drop_router = _no_router()
    try:
        out = _run(text="这道题怎么做")
    finally:
        drop_router()
        restore()
        dify_backend.run_scene = original_run
        config.EXEC_BACKEND, config.DIFY_FALLBACK_LOCAL = original_backend, original_fb

    assert out["result"]["backend"] == "local_fallback"
    assert "回退本地" in out["result"]["text"], "回退必须显式说明，不能静默"


def test_dify_not_used_for_attachments_or_sticky() -> None:
    """带附件与粘性会话都在 dispatch 就被拦下，不会走到 dify_scene。"""
    assert graph.dispatch({"files": ["a.png"], "event": {}, "routing": {}}) == "local"
    assert graph.dispatch({"files": [], "event": {"_sticky": {"fitness": True}}, "routing": {}}) == "local"


def test_dify_payload_carries_authoritative_scene() -> None:
    from lg_assistant import dify_backend

    p = dify_backend.build_payload(
        text="膝盖疼", scene="fitness", device_intent="solve", device_confidence=0.95
    )
    assert p["inputs"]["scene_hint"] == "fitness"
    assert p["inputs"]["device_intent"] == "solve"
    assert p["inputs"]["device_confidence"] == "0.95"
    assert dify_backend.build_payload(text="x", scene="bogus")["inputs"]["scene_hint"] == ""


def test_dify_extract_text_rejects_empty_outputs() -> None:
    """空 outputs = 图静默停止，必须显式暴露而不是当成模型没说话。"""
    from lg_assistant import dify_backend

    for bad in (
        {"data": {"status": "succeeded", "outputs": {}}},
        {"data": {"status": "failed", "outputs": {}}},
        {"data": {"outputs": "oops"}},
        {},
        [],
    ):
        try:
            dify_backend.extract_text(bad)
        except dify_backend.DifyError:
            continue
        raise AssertionError(f"应抛 DifyError：{bad!r}")


# --------------------------------------------------------------------------- #
# 断点续跑（本次迁移最实质的能力）
# --------------------------------------------------------------------------- #
def test_checkpointer_persists_thread_state() -> None:
    tmp = _fresh()
    cp_path = tmp / "ck.sqlite3"

    restore, _ = _stub_llm("第一次回答")
    drop_router = _no_router()
    try:
        app1 = graph.build_graph(graph.open_checkpointer(cp_path))
        cfg = graph.run_config("u", "s1")
        out1 = app1.invoke(
            {"text": "你好", "owner": "u", "session_id": "s1", "event": {}, "notes": []}, cfg
        )
    finally:
        drop_router()
        restore()

    assert out1["result"]["text"] == "第一次回答"

    # 新进程/新图实例，同一个 thread_id 应能读回状态
    app2 = graph.build_graph(graph.open_checkpointer(cp_path))
    snap = app2.get_state(graph.run_config("u", "s1"))
    assert snap.values, "检查点必须能跨图实例读回"
    assert snap.values["result"]["text"] == "第一次回答"
    assert snap.values["routing"]["scene"] == "general"


def test_checkpointer_isolates_threads() -> None:
    tmp = _fresh()
    cp_path = tmp / "ck.sqlite3"
    app = graph.build_graph(graph.open_checkpointer(cp_path))

    restore, _ = _stub_llm("A 的回答")
    drop_router = _no_router()
    try:
        app.invoke({"text": "你好", "owner": "u", "session_id": "a", "event": {}, "notes": []},
                   graph.run_config("u", "a"))
    finally:
        drop_router()
        restore()

    # 另一条线程不应看到上一条的内容
    snap_b = app.get_state(graph.run_config("u", "b"))
    assert not snap_b.values, "不同 thread_id 之间必须隔离"


def test_interrupt_then_resume_does_not_redo_decided_work() -> None:
    """眼镜断连后从任意步骤恢复——本次迁移最实质的能力。

    assistant-lite 的异步任务是进程内线程池 + 内存表，重启即丢。
    这里在 ``local_llm`` 之前中断（路由已决定、正文尚未生成），
    用同一个 thread_id 恢复后，只有未完成的那一步真正执行。
    """
    tmp = _fresh()
    app = graph.build_graph(graph.open_checkpointer(tmp / "ck.sqlite3"))
    cfg = graph.run_config("u", "resume1")

    calls: list = []
    original_chat = llm.chat

    def counting_chat(messages, **kw):
        calls.append(messages)
        return "恢复之后才生成的回答"

    llm.chat = counting_chat
    drop_router = _no_router()
    try:
        interrupted = app.invoke(
            {
                "text": "hello", "owner": "u", "session_id": "resume1",
                "request_id": "r1", "files": [], "event": {}, "notes": [],
            },
            cfg,
            interrupt_before=["local_llm"],
        )
        assert calls == [], "中断点之前不应发起模型调用"
        assert (interrupted.get("routing") or {}).get("scene") == "general", (
            "路由应当在中断之前就已完成"
        )
        assert not (interrupted.get("result") or {}).get("text")

        snap = app.get_state(cfg)
        assert snap.next == ("local_llm",), f"应停在 local_llm，实际 {snap.next}"

        resumed = app.invoke(None, cfg)
    finally:
        drop_router()
        llm.chat = original_chat

    assert len(calls) == 1, "恢复时只应执行未完成的那一步，不重跑已完成的节点"
    assert resumed["result"]["text"] == "恢复之后才生成的回答"
    assert resumed["speech"], "恢复后后处理也要跑（补播报语）"


def test_thread_id_shape() -> None:
    assert graph.thread_id("dev1", "s2") == "dev1:s2"
    assert graph.thread_id("", "") == "local:default"


# --------------------------------------------------------------------------- #
# 移植资产的回归（确保搬运没有改行为）
# --------------------------------------------------------------------------- #
def test_ported_calc_still_safe() -> None:
    assert calc.calculate("1+2") == 3
    assert calc.calculate("(18+24)*3") == 126
    assert abs(calc.calculate("120/(1+0.2)") - 100.0) < 1e-9
    for bad in ["__import__('os')", "1+", "abc", "1/0", "2**100", "open('x')"]:
        try:
            calc.calculate(bad)
        except calc.CalcError:
            continue
        raise AssertionError(f"应拒绝：{bad!r}")


def test_ported_grids_still_detects_xor() -> None:
    out = grids.check_grids(
        {
            "rows": [["01", "10", "11"], ["11", "01", "10"], ["10", "11", "?"]],
            "options": {"A": "01", "B": "10", "C": "00", "D": "11"},
        }
    )
    assert out["status"] == "checked"
    assert any("xor" in c["rule"] for c in out["candidates"]), out


def test_ported_speech_still_compresses() -> None:
    """resolve 只对**播报语**去 Markdown；正文保留原 Markdown 给屏幕看。"""
    body, spoken = speech.resolve("**结论**：一切正常。" * 20)
    assert "**" in body, "正文应保留 Markdown（屏幕上要渲染）"
    assert "**" not in spoken, "播报语必须去 Markdown"
    assert len(spoken) <= speech.MAX_CHARS + 1


def test_ported_speech_strips_all_headings_not_just_first() -> None:
    """回归：标题正则原本锚定行首（``^\\s{0,3}#{1,6}\\s*`` + re.M），
    在重复串里只会匹配**第一个**标题——后续的 "## 标题" 前面是空格而非行首，
    于是「井号井号」被原样送进 TTS。短文本分支尤其明显，因为没有截断兜底。
    """
    text = "## 标题\n\n**结论**：一切正常。" * 12
    spoken = speech.resolve(text)[1]
    assert "#" not in spoken, f"播报语仍残留 Markdown 标题标记：{spoken[:60]!r}"

    short = "## 标题\n正文。"
    spoken_short = speech.truncate(short)
    assert "#" not in spoken_short, f"短文本分支必须同样清理：{spoken_short!r}"


def test_postprocess_speech_is_plain_text() -> None:
    """端到端：图输出的播报语必须是可直接朗读的纯文本。"""
    restore, _ = _stub_llm("## 标题\n\n**结论**：一切正常。" * 12)
    drop_router = _no_router()
    try:
        out = _run(text="随便问问")
    finally:
        drop_router()
        restore()
    assert "**" not in out["speech"]
    assert "#" not in out["speech"]


# --------------------------------------------------------------------------- #
# 训练状态：Web 层 WORKOUT 卡片读 /api/state 的 fitness.workout
# 迁移到状态图后该字段一度没有来源，卡片恒显示 IDLE。
# --------------------------------------------------------------------------- #

def test_workout_start_sets_active_with_exercise_name() -> None:
    """开始训练后卡片要能看到动作名，而不是恒为 IDLE。"""
    out = nodes.postprocess({
        "text": "开始训练，做深蹲",
        "routing": {"scene": "fitness", "action": "start_exercise"},
    })
    w = out["fitness"]["workout"]
    assert w["status"] == "active"
    assert w["current"] == "深蹲"
    assert w["total_sets"] == 0


def test_workout_set_done_counts_only_while_active() -> None:
    """训练中「做完一组」+1；没开始训练时不能凭空冒出组数。"""
    out = nodes.postprocess({
        "text": "这组做完了",
        "routing": {"scene": "fitness", "action": "set_done"},
        "fitness": {"workout": {"status": "active", "current": "深蹲", "total_sets": 2}},
    })
    assert out["fitness"]["workout"]["total_sets"] == 3

    idle = nodes.postprocess({
        "text": "这组做完了",
        "routing": {"scene": "fitness", "action": "set_done"},
    })
    assert idle["fitness"]["workout"]["total_sets"] == 0


def test_workout_pain_pauses_without_losing_sets() -> None:
    """不适暂停保留已完成组数——安全相关状态不该被清掉。"""
    out = nodes.postprocess({
        "text": "膝盖有点疼",
        "routing": {"scene": "fitness", "action": "pain_report"},
        "fitness": {"workout": {"status": "active", "current": "深蹲", "total_sets": 3}},
    })
    assert out["fitness"]["workout"]["status"] == "paused"
    assert out["fitness"]["workout"]["total_sets"] == 3


def test_workout_end_resets_status() -> None:
    out = nodes.postprocess({
        "text": "结束训练",
        "routing": {"scene": "fitness", "action": "end_workout"},
        "fitness": {"workout": {"status": "paused", "current": "深蹲", "total_sets": 3}},
    })
    assert out["fitness"]["workout"]["status"] == "idle"


def test_workout_untouched_for_other_scenes() -> None:
    """非锻炼场景不得写 fitness，避免污染卡片状态。"""
    out = nodes.postprocess({
        "text": "你好",
        "routing": {"scene": "general", "action": "answer"},
    })
    assert "fitness" not in out


def test_fitness_action_inference_puts_safety_first() -> None:
    """关键词路由只给场景，动作靠推断；不适必须优先于开始。"""
    assert routing.infer_fitness_action("开始训练，做深蹲") == "start_exercise"
    assert routing.infer_fitness_action("这组做完了，下一组") == "set_done"
    assert routing.infer_fitness_action("膝盖有点疼") == "pain_report"
    assert routing.infer_fitness_action("结束训练") == "end_workout"
    # 同时含「疼」与「练」：安全优先，绝不能启动训练
    assert routing.infer_fitness_action("膝盖疼，今天不练了") == "pain_report"
    assert routing.infer_fitness_action("今天天气不错") == ""


def test_same_thread_second_request_does_not_reuse_first_result() -> None:
    """回归：检查点里的旧状态污染了下一条请求。

    实测两个连续的坑：

    1. ``calc_quick`` 未命中时返回 ``None``，LangGraph 用检查点恢复的**上一次**
       ``result`` 去求值条件边 → 误判「已完成」→ ``local_llm`` 从不执行，
       第二条请求直接返回第一条的答案。
    2. 改用 ``Command(goto=...)`` 后，它与静态边**并存**，两条路都执行并竞态。

    现在条件边只读 ``calc_hit``（每轮必刷新）。这个测试锁住的是行为本身：
    同一 thread 上第二条请求必须拿到自己的答案。
    """
    tmp = _fresh()
    app = graph.build_graph(graph.open_checkpointer(tmp / "ck.sqlite3"))
    cfg = graph.run_config("u", "same")

    def call(text, rid):
        return app.invoke(
            {"text": text, "owner": "u", "session_id": "same", "request_id": rid,
             "files": [], "event": {}, "notes": []},
            cfg,
        )

    first = call("计算 (18+24)*3", "c1")
    assert "126" in first["result"]["text"]

    restore, _ = _stub_llm("这是第二条请求的回答")
    drop_router = _no_router()
    try:
        second = call("hello", "c2")
    finally:
        drop_router()
        restore()

    assert second["result"]["text"] == "这是第二条请求的回答", (
        f"第二条请求拿到了陈旧结果：{second['result']['text']!r}"
    )
    assert "126" not in second["result"]["text"]


def test_calc_quick_sets_fresh_hit_flag_every_turn() -> None:
    """``calc_hit`` 必须在命中与未命中时都被写，否则条件边会读到上一轮的值。"""
    hit = nodes.calc_quick({"text": "计算 1+1", "files": []})
    assert hit.get("calc_hit") is True
    miss = nodes.calc_quick({"text": "你好", "files": []})
    assert miss.get("calc_hit") is False
    assert "result" not in miss, "未命中时不应写 result（避免留下陈旧正文）"


# --------------------------------------------------------------------------- #
# 视觉链路（打桩模型，不联网）
# --------------------------------------------------------------------------- #
def _png(tmp: Path, name: str = "q.png") -> str:
    from PIL import Image

    p = tmp / name
    Image.new("RGB", (8, 8), "white").save(p)
    return str(p)


def _stub_vision_chain():
    """打桩 vision.observe / solve / review，并记录调用顺序。"""
    from lg_assistant import vision

    order: list[str] = []
    originals = (vision.observe, vision.solve, vision.review)

    def fake_observe(text, images):
        order.append("observe")
        return "题面：2x+6=14，选项 A)2 B)4 C)6 D)8"

    def fake_solve(text, observation, images):
        order.append("solve")
        return {
            "module": "数量关系",
            "subtype": "一元一次方程",
            "answerable": True,
            "candidate": "B",
            "calculations": [{"label": "x", "expression": "8/2"}],
            "binary_grid": None,
        }

    def fake_review(text, draft, tool_result, images):
        order.append("review")
        return {
            "module": "数量关系",
            "subtype": "一元一次方程",
            "answerable": True,
            "answer": "B) 4",
            "explanation": "2x=8，x=4",
            "review_notes": "已回看原图核对",
            "speech": "答案是 B，4。",
        }

    vision.observe, vision.solve, vision.review = fake_observe, fake_solve, fake_review
    return order, lambda: (
        setattr(vision, "observe", originals[0]),
        setattr(vision, "solve", originals[1]),
        setattr(vision, "review", originals[2]),
    )


def test_dispatch_routes_image_exam_to_vision() -> None:
    """带图片的解题请求必须走本地视觉链，不能盲目转发 Dify。"""
    assert (
        graph.dispatch({"files": ["a.png"], "routing": {"scene": "exam"}, "event": {}})
        == "vision"
    )
    # 图片但不是解题场景 → 留本地（会议白板照片等）
    assert (
        graph.dispatch({"files": ["a.png"], "routing": {"scene": "meeting"}, "event": {}})
        == "local"
    )
    # 音频附件 → 转写节点（**不是** local）。
    # 这条断言原本写的是 "local"，那是音频节点还不存在时的行为：
    # 录音落到 local_llm 只会回一句「请发送会议转写文本」。
    # 现在音频一律先转写，整理成什么由节点按场景决定。
    assert (
        graph.dispatch({"files": ["a.wav"], "routing": {"scene": "exam"}, "event": {}})
        == "audio"
    )


def test_vision_chain_runs_all_four_steps_in_order() -> None:
    """四步链：精读 → 初解 → 工具校验 → 终审。初解与终审都要重新看原图。"""
    tmp = _fresh()
    order, restore = _stub_vision_chain()
    drop_router = _no_router()
    try:
        out = _run(text="解这道题", files=[_png(tmp)])
    finally:
        drop_router()
        restore()

    assert order == ["observe", "solve", "review"], f"链路顺序错误：{order}"
    assert out["routing"]["scene"] == "exam"
    assert out["result"]["backend"] == "local"
    assert "B) 4" in out["result"]["text"]
    assert "8/2 = 4" in out["result"]["text"], "程序计算校验必须渲染出来"
    assert out["speech"] == "答案是 B，4。"


def test_vision_chain_runs_deterministic_grid_check() -> None:
    """黑白格题必须真的执行逐格运算校验，而不是把模型输出原样带过。"""
    from lg_assistant import vision

    draft = {
        "binary_grid": {
            "rows": [["01", "10", "11"], ["11", "01", "10"], ["10", "11", "?"]],
            "options": {"A": "01", "B": "10", "C": "00", "D": "11"},
        },
        "calculations": [{"expression": "120/(1+0.2)"}],
    }
    tools = vision.run_tools(draft)
    assert tools["grid_checks"]["status"] == "checked"
    assert any("xor" in c["rule"] for c in tools["grid_checks"]["candidates"])
    assert abs(tools["calculations"][0]["result"] - 100.0) < 1e-9


def test_vision_render_refuses_to_assert_when_not_answerable() -> None:
    """模型判定无法作答时，正文必须明确说「无法确定」，不得偷渡猜测。"""
    from lg_assistant import vision

    body = vision.render(
        {"answerable": False, "module": "图形推理", "needed": "请补拍右下角"},
        {},
    )
    assert "无法确定作答" in body
    assert "请补拍右下角" in body
    assert "答案：" not in body


def test_vision_node_rejects_non_image_attachments() -> None:
    out = nodes.exam_vision({"files": ["a.wav"], "text": "x"})
    assert "没有找到可识别的图片" in out["result"]["text"]


def test_vision_failure_is_reported_not_swallowed() -> None:
    """视觉链路失败要显式暴露，不能静默返回空。"""
    from lg_assistant import vision

    original = vision.observe

    def boom(*a, **kw):
        raise vision.VisionError("模拟识别失败")

    vision.observe = boom
    try:
        out = nodes.exam_vision({"files": ["a.png"], "text": "解这道题"})
    finally:
        vision.observe = original
    assert "图片识别失败" in out["result"]["text"]
    assert out["result"]["note"]


# --------------------------------------------------------------------------- #
# 音频链路（会议录音转写）—— 此前完全缺失
# --------------------------------------------------------------------------- #
def test_dispatch_routes_audio_to_meeting_audio() -> None:
    """回归：音频曾经没有任何节点接手。

    带录音的请求会直接落到 ``local_llm``，那里只拿得到用户那句「会议纪要整理」，
    于是模型回「请发送会议转写文本」——把用户已经用音频给过的内容再要一遍。
    实测：上传 1.9MB 录音，4 秒返回一句要文本的提示。
    """
    for ext in (".mp3", ".wav", ".m4a", ".flac"):
        assert graph.dispatch(
            {"files": [f"a{ext}"], "routing": {"scene": "meeting"}, "event": {}}
        ) == "audio", ext
    # 图片走视觉链，不受影响
    assert graph.dispatch(
        {"files": ["a.png"], "routing": {"scene": "exam"}, "event": {}}
    ) == "vision"
    # 无附件的会议请求仍走本地 LLM
    assert graph.dispatch(
        {"files": [], "routing": {"scene": "meeting"}, "event": {}}
    ) == "local"


def test_audio_node_transcribes_then_summarises() -> None:
    """链路必须真的调用 ASR，并把转写交给 LLM 整理。"""
    tmp = _fresh()
    src = tmp / "meeting.mp3"
    src.write_bytes(b"fake-audio")

    from lg_assistant import llm
    from lg_assistant.tools import audio as audio_tool

    calls = {"asr": 0, "chat": 0}
    orig_asr, orig_chat, orig_split = llm.asr, llm.chat, audio_tool.split

    def fake_asr(p, **kw):
        calls["asr"] += 1
        return "王浩：数据获取范式要转型。"

    def fake_chat(messages, **kw):
        calls["chat"] += 1
        # 转写必须真的被送进模型，否则等于白转
        assert "数据获取范式要转型" in str(messages), "转写未进入整理提示词"
        return "**会议主题** 具身智能卡点\n**明确决策** 未明确"

    llm.asr = fake_asr
    llm.chat = fake_chat
    audio_tool.split = lambda p, **kw: ([Path(p)], "")

    try:
        out = nodes.meeting_audio({"files": [str(src)], "routing": {"scene": "meeting"}})
    finally:
        llm.asr, llm.chat, audio_tool.split = orig_asr, orig_chat, orig_split

    assert calls["asr"] == 1, "必须调用 ASR"
    assert calls["chat"] == 1, "转写后必须调用模型整理"
    assert "会议主题" in out["result"]["text"]
    assert out["result"]["artifacts"][0]["kind"] == "audio"


def test_audio_node_reports_empty_transcript_instead_of_pretending() -> None:
    """转写不出内容时要如实说，并把原因带出来，不能编一份纪要。"""
    tmp = _fresh()
    src = tmp / "silent.mp3"
    src.write_bytes(b"x")

    from lg_assistant import llm
    from lg_assistant.tools import audio as audio_tool

    orig_asr, orig_split = llm.asr, audio_tool.split
    llm.asr = lambda p, **kw: ""
    audio_tool.split = lambda p, **kw: ([Path(p)], "未找到 ffmpeg")

    try:
        out = nodes.meeting_audio({"files": [str(src)], "routing": {"scene": "meeting"}})
    finally:
        llm.asr, audio_tool.split = orig_asr, orig_split

    text = out["result"]["text"]
    assert "没能从音频里识别到语音" in text
    assert "未找到 ffmpeg" in text, "分片降级原因必须带出来"
    assert out["result"]["note"] == "ASR 未产出文本"


def test_audio_node_without_audio_answers_clearly() -> None:
    out = nodes.meeting_audio({"files": ["a.png"], "routing": {"scene": "meeting"}})
    assert "没有找到可转写的音频" in out["result"]["text"]


def test_split_ffmpeg_falls_back_to_reencode() -> None:
    """回归：`-c copy` 在容器与编码不匹配时会失败。

    实测：用户上传的文件扩展名是 .mp3，实际内容是 AAC（16kHz 单声道，10分45秒）。
    把 AAC 流直接写进 mp3 容器时 ffmpeg 报
    "Exactly one MP3 audio stream is required"，退出码 -22。
    分片失败 → 整段送 ASR → "The audio is too long"，用户的录音完全转不出来。

    这里不依赖真实 ffmpeg：检查失败后会**再次尝试**（带重编码参数），
    而不是直接放弃并留下 0 字节残片。
    """
    tmp = Path(tempfile.mkdtemp(prefix="lgaud-"))
    src = tmp / "a.mp3"
    src.write_bytes(b"x")

    from lg_assistant.tools import audio as audio_tool

    attempts: list[list[str]] = []
    orig_run, orig_find = audio_tool.subprocess.run, audio_tool.find_ffmpeg

    class R:
        returncode = 1
        stderr = "Invalid audio stream. Exactly one MP3 audio stream is required."

    audio_tool.find_ffmpeg = lambda: "ffmpeg"
    audio_tool.subprocess.run = lambda cmd, **kw: (attempts.append(list(cmd)), R())[1]

    try:
        try:
            audio_tool._split_ffmpeg(src, 240, tmp)
        except RuntimeError as e:
            assert "分片失败" in str(e)
        else:
            raise AssertionError("两次都失败时应抛 RuntimeError")
    finally:
        audio_tool.subprocess.run, audio_tool.find_ffmpeg = orig_run, orig_find

    assert len(attempts) == 2, f"应尝试 copy 与重编码两次，实际 {len(attempts)}"
    assert "copy" in attempts[0]
    assert "libmp3lame" in " ".join(attempts[1]), "第二次必须重编码"


def test_split_ffmpeg_purges_zero_byte_leftovers() -> None:
    """失败留下的 0 字节分片不能被当成有效分片返回。"""
    tmp = Path(tempfile.mkdtemp(prefix="lgpurge-"))
    src = tmp / "a.mp3"
    src.write_bytes(b"x")
    leftover = tmp / "a_part000.mp3"
    leftover.write_bytes(b"")     # 伪造上一次失败留下的残片

    from lg_assistant.tools import audio as audio_tool

    orig_run, orig_find = audio_tool.subprocess.run, audio_tool.find_ffmpeg
    audio_tool.find_ffmpeg = lambda: "ffmpeg"
    audio_tool.subprocess.run = lambda cmd, **kw: type(
        "R", (), {"returncode": 1, "stderr": "boom"}
    )()
    try:
        try:
            audio_tool._split_ffmpeg(src, 240, tmp)
        except RuntimeError:
            pass
    finally:
        audio_tool.subprocess.run, audio_tool.find_ffmpeg = orig_run, orig_find

    assert not leftover.exists(), "0 字节残片必须被清理，否则会被当成有效分片"


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
