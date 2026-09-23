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

from lg_assistant import config, graph, llm, nodes, profile as profile_module, routing, store  # noqa: E402
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


def test_meeting_state_accumulates_transcript() -> None:
    """会议卡片的 LISTENING 态与转写区读 /api/state 的 meeting 字段。"""
    start = nodes.postprocess({
        "text": "开始会议",
        "routing": {"scene": "meeting", "action": "start"},
    })
    assert start["meeting"]["status"] == "collecting"
    assert "开始会议" in start["meeting"]["transcript"]

    more = nodes.postprocess({
        "text": "张明说下周交付",
        "routing": {"scene": "meeting", "action": "append"},
        "meeting": start["meeting"],
    })
    assert more["meeting"]["status"] == "collecting"
    assert "张明说下周交付" in more["meeting"]["transcript"]
    assert "开始会议" in more["meeting"]["transcript"], "追加不能丢掉前面的转写"

    ended = nodes.postprocess({
        "text": "结束会议",
        "routing": {"scene": "meeting", "action": "stop"},
        "meeting": more["meeting"],
    })
    assert ended["meeting"]["status"] == "ended"
    assert ended["meeting"]["transcript"] == more["meeting"]["transcript"]


def test_meeting_untouched_for_other_scenes() -> None:
    """非会议场景不得写 meeting，避免污染卡片状态。"""
    out = nodes.postprocess({
        "text": "你好",
        "routing": {"scene": "general", "action": "answer"},
    })
    assert "meeting" not in out


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

    def fake_solve(text, observation, images, **kw):
        order.append("solve")
        return {
            "module": "数量关系",
            "subtype": "一元一次方程",
            "answerable": True,
            "candidate": "B",
            "calculations": [{"label": "x", "expression": "8/2"}],
            "binary_grid": None,
        }

    def fake_review(text, draft, tool_result, images, **kw):
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


def test_vision_drops_tautological_calculations() -> None:
    """同义反复的「算式」必须被丢弃，且不得留下「程序计算校验」标题。

    实测截图里出现过：::

        程序计算校验
          13.34 = 13.34
          10.66 = 10.66

    政治理论/常识类题本来就无物可算，而提示词要求「尽量提供关键算式」，
    模型便把「选项里的数 = 资料里的数」写成算式交差。这比不显示更糟——
    **它让没做过的校验看起来像做过了**。
    """
    from lg_assistant import vision

    # 全部是同义反复 → 不产生 calculations，但留下计数痕迹
    tools = vision.run_tools(
        {"calculations": [{"expression": "13.34 = 13.34"}, {"expression": "10.66"}]}
    )
    assert "calculations" not in tools, "同义反复不该进计算校验"
    assert tools["calculations_skipped"] == 2

    # 渲染时不得出现「程序计算校验」标题，但要如实交代没做校验
    body = vision.render({"answerable": True, "answer": "B"}, tools)
    assert "程序计算校验" not in body
    assert "无需计算校验" in body

    # 真假混杂 → 只保留真算式
    mixed = vision.run_tools(
        {"calculations": [{"expression": "13.34 = 13.34"}, {"expression": "8/2"}]}
    )
    assert [c["expression"] for c in mixed["calculations"]] == ["8/2"]
    assert mixed["calculations"][0]["result"] == 4.0
    assert mixed["calculations_skipped"] == 1


def test_is_meaningful_calc_keeps_real_arithmetic() -> None:
    """判定器本身：真算式一律保留，纯等值声明一律丢弃。"""
    from lg_assistant.vision import is_meaningful_calc

    for good in ["8/2", "2**10", "120*(1+0.2)", "18+24", "100%7", "2+3=5"]:
        assert is_meaningful_calc(good), f"{good} 是有效算式，不该被丢"
    for bad in ["13.34 = 13.34", "10.66", "C", "  ", "", "选项B"]:
        assert not is_meaningful_calc(bad), f"{bad!r} 不是算式，必须丢弃"


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
    """链路必须真的转写，并把转写交给 LLM 整理，且正文含文字记录。"""
    tmp = _fresh()
    src = tmp / "meeting.mp3"
    src.write_bytes(b"fake-audio")

    from lg_assistant import llm, transcribe

    calls = {"diar": 0, "chat": 0}
    orig_diar, orig_chat = transcribe.transcribe_with_speakers, llm.chat

    def fake_diar(path, **kw):
        calls["diar"] += 1
        return {
            "utterances": [
                {"speaker": 0, "begin_ms": 1000, "end_ms": 3000, "text": "数据获取范式要转型。"},
                {"speaker": 1, "begin_ms": 3000, "end_ms": 5000, "text": "我同意这个判断。"},
            ],
            "speakers": [0, 1],
            "sentences": [],
            "duration_ms": 5000,
            "text": "x",
        }

    def fake_chat(messages, **kw):
        calls["chat"] += 1
        assert "数据获取范式要转型" in str(messages), "转写未进入整理提示词"
        return "**会议主题** 具身智能卡点\n**明确决策** 未明确"

    transcribe.transcribe_with_speakers = fake_diar
    llm.chat = fake_chat
    try:
        out = nodes.meeting_audio({"files": [str(src)], "routing": {"scene": "meeting"}})
    finally:
        transcribe.transcribe_with_speakers, llm.chat = orig_diar, orig_chat

    text = out["result"]["text"]
    assert calls["diar"] == 1, "必须调用带说话人分离的转写"
    assert calls["chat"] == 1, "转写后必须调用模型整理"
    assert "会议主题" in text
    # 纪要 + 文字记录都要在，用户要的是两样
    assert "会议文字记录" in text, "正文必须包含完整文字记录"
    assert "发言人1" in text and "发言人2" in text, "文字记录要区分发言人"


def test_meeting_path_only_uses_transcribe_functions_that_exist() -> None:
    """会议链路只许调用 ``transcribe`` 里真实存在的函数——本用例不打桩 transcribe。

    **为什么要有这一条**：这个 bug 真的发生过。某次提交只带上了 ``nodes.py``
    的改动，而它调用的 ``transcribe.diarization_report`` /
    ``describe_diarization`` 还留在别人的工作区里没提交 —— 于是仓库里
    ``nodes.py`` 引用着不存在的函数，**克隆下来跑到会议转写就 AttributeError**，
    而当时所有单测都是绿的。

    绿的原因很具体：``test_audio_node_transcribes_then_summarises`` 只打了
    ``transcribe_with_speakers`` 的桩，其余仍走真模块；而它断言的是
    「会议主题」「转写交给模型」这些**上游**行为，从没检查 ``body_extra``
    里那句发言人统计。这里补上这一块，且**一个桩都不打在 transcribe 上**——
    凡是 ``nodes`` 与 ``transcribe`` 的接口对不上，本用例必然红。
    """
    tmp = _fresh()
    src = tmp / "m.mp3"
    src.write_bytes(b"fake-audio")

    from lg_assistant import llm, transcribe

    orig_diar, orig_chat = transcribe.transcribe_with_speakers, llm.chat

    def fake_diar(path, **kw):
        # 真实形状的 utterances：字段与线上 paraformer-v2 返回一致
        return {
            "utterances": [
                {"speaker": 0, "begin_ms": 1000, "end_ms": 4000, "text": "先看数据获取。"},
                {"speaker": 1, "begin_ms": 4000, "end_ms": 9000, "text": "我同意。"},
                {"speaker": 0, "begin_ms": 9000, "end_ms": 11000, "text": "那就这么定。"},
            ],
            "speakers": [0, 1],
            "sentences": [],
            "duration_ms": 11000,
            "text": "x",
        }

    transcribe.transcribe_with_speakers = fake_diar
    llm.chat = lambda messages, **kw: "**会议主题** 数据获取范式"
    try:
        out = nodes.meeting_audio({"files": [str(src)], "routing": {"scene": "meeting"}})
    finally:
        transcribe.transcribe_with_speakers, llm.chat = orig_diar, orig_chat

    text = out["result"]["text"]
    # 发言人统计真的渲染出来了（这正是此前没人断言的那一块）
    assert "共识别出 2 位发言人" in text, f"缺少发言人统计：{text[:400]}"
    assert "发言人1 2 段" in text and "发言人2 1 段" in text, "段数统计不对"
    # 转写正文照旧
    assert "会议文字记录" in text
    assert "先看数据获取" in text and "那就这么定" in text, "正文丢了转写内容"


# --------------------------------------------------------------------------- #
# 健康档案（建档问卷）
#
# 为什么这些用例必须走**真 checkpointer 的多轮会话**：
# 实测踩到的头号 bug 就是「粘性只恢复场景、不恢复动作」——第二轮用户答
# 「28」，路由给出 scene=fitness/action=""，而 dispatch 判的是
# action=="profile"，于是答题被送去 local_llm，问卷停在原地。
# 单轮调用永远看不到这个 bug：它只在第二轮出现。
# --------------------------------------------------------------------------- #
def _session(owner: str = "u", session: str = "p"):
    """开一个**带检查点**的会话，返回 (invoke, app, cfg)。"""
    tmp = _fresh()
    app = graph.build_graph(graph.open_checkpointer(config.CHECKPOINT_DB))
    cfg = graph.run_config(owner, session)

    def turn(text: str, **extra):
        base = {
            "text": text,
            "owner": owner,
            "session_id": session,
            "request_id": "r",
            "files": [],
            "event": {},
            "notes": [],
        }
        base.update(extra)
        return app.invoke(base, cfg)

    return turn, app, cfg


def test_profile_trigger_routes_to_fitness_profile() -> None:
    """「建立健康档案」必须零 token 命中 fitness/profile。

    修复前它一个关键词都不命中（KEYWORDS["fitness"] 里没有「建档」「健康档案」），
    掉到 LLM 兜底被判成 general，拿通用提示词即兴回答——用户以为在填档案，
    其实什么都没记。
    """
    assert routing.keyword_scores("建立健康档案") == {"fitness": 0.8}
    assert routing.infer_fitness_action("建立健康档案") == "profile"
    assert routing.infer_fitness_action("建档") == "profile"
    assert graph.dispatch({"routing": {"action": "profile"}}) == "profile"


def test_profile_questionnaire_multiturn_without_model() -> None:
    """九项问卷全程零 token 走完，且落库的是**正确的**数值。

    这里钉住两个实测 bug：
    1. 粘性不恢复动作 → 第二轮就掉出问卷（见上面注释）
    2. 「我178厘米，70公斤」曾把身高体重都填成 178，BMI 算出 56.2
    """
    turn, _app, _cfg = _session()
    restore, calls = _stub_llm("不该被调用")
    try:
        out = turn("建立健康档案")
        assert out["routing"]["scene"] == "fitness"
        assert out["routing"]["action"] == "profile"
        assert out["fitness"]["awaiting"] == "age"
        assert "年龄" in out["result"]["text"]

        # 第二轮：裸数字必须被当作年龄答案 —— 这正是粘性 bug 的暴露点
        out = turn("28")
        assert out["routing"]["action"] == "profile", "第二轮掉出了建档问卷"
        assert out["fitness"]["draft"]["age"] == 28

        # 一句话答两项：按单位就近取数，**不能复用同一个数字**
        out = turn("我178厘米，70公斤")
        draft = out["fitness"]["draft"]
        assert draft["height_cm"] == 178.0
        assert draft["weight_kg"] == 70.0, f"体重被填错：{draft}"
        assert draft["height_cm"] != draft["weight_kg"], "身高体重不该是同一个数"

        # 别名要映射到标准选项
        out = turn("减肥")
        assert out["fitness"]["draft"]["goal"] == "减脂"

        # 答得不合规 → 原地重问，不推进、不丢
        out = turn("999")
        assert out["fitness"]["awaiting"] == "level"
        assert "没识别出" in out["result"]["text"]

        turn("偶尔运动")
        out = turn("膝盖、腰")
        assert out["fitness"]["draft"]["injuries"] == ["膝盖", "腰部"]
        turn("无")
        turn("3")
        out = turn("哑铃、弹力带")

        # 填满 → 落库 + 回显
        assert out["fitness"]["awaiting"] == ""
        assert out["fitness"]["draft"] == {}
        assert "健康档案已建立" in out["result"]["text"]
    finally:
        restore()

    data = store.load_profile("u")
    assert store.profile_meta("u")["source"] == "glasses"
    assert data["age"] == 28
    assert data["height_cm"] == 178.0 and data["weight_kg"] == 70.0
    assert data["conditions"] == ["无"]
    assert abs(profile_module.bmi(data) - 22.1) < 0.05, "BMI 必须按正确身高体重算"
    assert calls == [], f"问卷不该调用模型，实际调用 {len(calls)} 次"


def test_profile_repeat_trigger_restarts_instead_of_erroring() -> None:
    """问卷进行中再说一次「建立健康档案」＝重新开始，不是把这句话当答案。

    实测：问到年龄时它被拿去解析成年龄，回「年龄需要是数字，请重新填写」——
    用户只是重复了最初的请求，完全看不懂这句错误。
    """
    turn, _app, _cfg = _session()
    turn("建立健康档案")
    out = turn("建立健康档案")
    assert out["fitness"]["awaiting"] == "age"
    assert "年龄" in out["result"]["text"]
    assert "需要是数字" not in out["result"]["text"]


def test_profile_cancel_removes_draft_but_keeps_saved_profile() -> None:
    """「取消建档」＝这次不填了，**不是删除我的健康档案**。

    这个区分很要紧：用户一句「算了」不该把已经建好的档案弄没。
    """
    turn, _app, _cfg = _session()
    store.save_profile("u", {"age": 30, "height_cm": 175.0}, source="phone")

    turn("建立健康档案")
    out = turn("取消建档")
    assert out["fitness"]["awaiting"] == ""
    assert out["fitness"]["draft"] == {}
    assert "已退出建档" in out["result"]["text"]
    # 库里那份不能少
    assert store.load_profile("u")["age"] == 30


def test_profile_is_injected_into_fitness_prompt_only() -> None:
    """档案的唯一用途：把建议落到用户身上。**只进健身场景。**

    档案含伤病与慢性病，是敏感信息，没有理由出现在会议纪要或解题的提示词里。
    """
    store.save_profile(
        "u",
        {
            "age": 28, "height_cm": 178.0, "weight_kg": 70.0,
            "goal": "减脂", "level": "偶尔运动",
            "injuries": ["膝盖"], "conditions": ["无"],
            "days_per_week": 3, "equipment": ["哑铃"],
        },
        source="phone",
    )

    captured: dict[str, str] = {}

    def spy(messages, **kw):
        for m in messages:
            if m.get("role") == "system":
                captured["system"] = m.get("content", "")
        return "桩回复"

    original = llm.chat
    llm.chat = spy
    try:
        # 健身场景 → 必须带上档案与风险提示
        _run(text="我今天练什么", scene_hint="fitness")
        sysmsg = captured.get("system", "")
        assert "【用户健康档案】" in sysmsg, "健身建议没读档案"
        assert "年龄：28" in sysmsg and "减脂" in sysmsg
        assert "膝盖" in sysmsg, "伤病必须带进提示词——这正是安全相关的部分"
        assert "先咨询医生" in sysmsg

        # 其它场景 → 一点都不许出现
        captured.clear()
        _run(text="帮我整理这段会议内容", scene_hint="meeting")
        assert "健康档案" not in captured.get("system", ""), "档案泄露到了非健身场景"
    finally:
        llm.chat = original


def test_profile_absent_is_stated_not_invented() -> None:
    """没建档时要明说「尚未建立」，**绝不假设默认值**。

    「假设用户 30 岁、无伤病」会让建议看起来个性化，实际全是凭空来的，
    而这恰恰是安全相关的字段。
    """
    captured: dict[str, str] = {}

    def spy(messages, **kw):
        for m in messages:
            if m.get("role") == "system":
                captured["system"] = m.get("content", "")
        return "桩回复"

    original = llm.chat
    llm.chat = spy
    try:
        _run(text="我今天练什么", scene_hint="fitness")
    finally:
        llm.chat = original
    sysmsg = captured.get("system", "")
    assert "尚未建立" in sysmsg
    assert "不要假设" in sysmsg


def test_profile_save_failure_is_reported_not_swallowed() -> None:
    """落库失败必须说出来。

    用户以为建好了、眼镜端却读不到，这种「以为存上了」的静默失败最难查。
    """
    turn, _app, _cfg = _session()
    turn("建立健康档案")
    turn("28")
    turn("我178厘米，70公斤")
    turn("减脂")
    turn("偶尔运动")
    turn("无")
    turn("无")
    turn("3")

    original = store.save_profile

    def boom(*a, **kw):
        raise OSError("磁盘满了")

    store.save_profile = boom
    try:
        out = turn("哑铃")
    finally:
        store.save_profile = original

    text = out["result"]["text"]
    assert "保存" in text and "失败" in text, f"落库失败被吞掉了：{text}"
    assert store.load_profile("u") == {}, "没存上就是没存上"
    # 草稿要留着，用户重试时不用从头再答一遍
    assert out["fitness"]["draft"]["age"] == 28


def test_audio_node_falls_back_to_plain_asr() -> None:
    """分离失败要降级到纯文本转写，不能炸链路，也不能丢掉录音。"""
    tmp = _fresh()
    src = tmp / "m.mp3"
    src.write_bytes(b"x")

    from lg_assistant import llm, transcribe
    from lg_assistant.tools import audio as audio_tool

    orig = (transcribe.transcribe_with_speakers, llm.asr, llm.chat, audio_tool.split)

    def boom(path, **kw):
        raise transcribe.TranscriptionError("模拟分离失败")

    transcribe.transcribe_with_speakers = boom
    llm.asr = lambda p, **kw: "降级后的纯文本转写。"
    llm.chat = lambda messages, **kw: "**会议主题** 降级"
    audio_tool.split = lambda p, **kw: ([Path(p)], "")

    try:
        out = nodes.meeting_audio({"files": [str(src)], "routing": {"scene": "meeting"}})
    finally:
        (transcribe.transcribe_with_speakers, llm.asr, llm.chat, audio_tool.split) = orig

    text = out["result"]["text"]
    assert "降级" in text
    assert "降级后的纯文本转写" in text, "降级后转写内容仍要交出来"
    assert "说话人分离失败" in text, "降级原因必须写明"
    assert "未能区分发言人" in text


def test_transcribe_parse_groups_consecutive_same_speaker() -> None:
    """同一说话人的连续句子要合并成一段发言——逐句罗列读起来太碎。"""
    from lg_assistant import transcribe

    data = {
        "properties": {"original_duration_in_milliseconds": 10000},
        "transcripts": [
            {
                "sentences": [
                    {"speaker_id": 0, "begin_time": 0, "end_time": 1000, "text": "第一句。"},
                    {"speaker_id": 0, "begin_time": 1000, "end_time": 2000, "text": "第二句。"},
                    {"speaker_id": 1, "begin_time": 2000, "end_time": 3000, "text": "换人。"},
                    {"speaker_id": 0, "begin_time": 3000, "end_time": 4000, "text": "又回来。"},
                ]
            }
        ],
    }
    r = transcribe.parse_result(data)
    assert len(r["sentences"]) == 4
    assert len(r["utterances"]) == 3, r["utterances"]
    assert r["utterances"][0]["text"] == "第一句。第二句。"
    assert r["utterances"][0]["end_ms"] == 2000
    assert r["speakers"] == [0, 1]


def test_transcribe_parse_skips_empty_and_sorts_by_time() -> None:
    from lg_assistant import transcribe

    data = {
        "properties": {},
        "transcripts": [
            {
                "sentences": [
                    {"speaker_id": 1, "begin_time": 5000, "end_time": 6000, "text": "后说的"},
                    {"speaker_id": 0, "begin_time": 1000, "end_time": 2000, "text": "   "},
                    {"speaker_id": 0, "begin_time": 2000, "end_time": 3000, "text": "先说的"},
                ]
            }
        ],
    }
    r = transcribe.parse_result(data)
    assert [s["text"] for s in r["sentences"]] == ["先说的", "后说的"], "要按时间排序且丢掉空句"


def test_speaker_label_never_invents_names() -> None:
    """声纹只能区分「是不是同一个人」，不知道他是谁。

    从内容猜姓名再冠上去，猜错就是把别人的话安在别人头上——
    会议纪要里这是严重错误。所以只输出「发言人N」。
    """
    from lg_assistant import transcribe

    assert transcribe.speaker_label(0) == "发言人1"
    assert transcribe.speaker_label(5) == "发言人6"
    assert transcribe.speaker_label(None) == "发言人"


def test_speaker_stats_counts_turns_and_duration() -> None:
    from lg_assistant import transcribe

    utts = [
        {"speaker": 0, "begin_ms": 0, "end_ms": 1000, "text": "a"},
        {"speaker": 1, "begin_ms": 1000, "end_ms": 4000, "text": "b"},
        {"speaker": 0, "begin_ms": 4000, "end_ms": 5000, "text": "c"},
    ]
    stats = transcribe.speaker_stats(utts)
    assert stats[0]["label"] == "发言人2", "按时长降序，说话多的排前面"
    assert stats[0]["turns"] == 1 and stats[0]["seconds"] == 3.0
    by = {s["speaker"]: s for s in stats}
    assert by[0]["turns"] == 2 and by[0]["seconds"] == 2.0


def test_audio_node_reports_empty_transcript_instead_of_pretending() -> None:
    """转写不出内容时要如实说，并把原因带出来，不能编一份纪要。"""
    tmp = _fresh()
    src = tmp / "silent.mp3"
    src.write_bytes(b"x")

    from lg_assistant import llm, transcribe
    from lg_assistant.tools import audio as audio_tool

    orig = (transcribe.transcribe_with_speakers, llm.asr, audio_tool.split)

    def boom(path, **kw):
        raise transcribe.TranscriptionError("分离不可用")

    transcribe.transcribe_with_speakers = boom
    llm.asr = lambda p, **kw: ""
    audio_tool.split = lambda p, **kw: ([Path(p)], "未找到 ffmpeg")

    try:
        out = nodes.meeting_audio({"files": [str(src)], "routing": {"scene": "meeting"}})
    finally:
        (transcribe.transcribe_with_speakers, llm.asr, audio_tool.split) = orig

    text = out["result"]["text"]
    assert "没能从音频里识别到语音" in text
    assert "未找到 ffmpeg" in text, "分片降级原因必须带出来"
    assert out["result"]["note"] == "ASR 未产出文本"


def test_transcribe_upload_uses_get_for_policy() -> None:
    """回归：取上传凭证必须用 GET + 查询参数，用 POST 会 405。

    这条踩过：文档示例是 `requests.get(..., params={"action":"getPolicy", ...})`，
    我先写成 POST 直接 405，白排查一轮。
    """
    from lg_assistant import transcribe

    seen: dict = {}

    class FakeResp:
        status_code = 200
        text = "{}"

        def json(self):
            return {
                "data": {
                    "upload_host": "https://oss.example/up",
                    "upload_dir": "d",
                    "oss_access_key_id": "k",
                    "policy": "p",
                    "signature": "s",
                }
            }

    class FakeRequests:
        @staticmethod
        def get(url, **kw):
            seen["method"] = "GET"
            seen["params"] = kw.get("params")
            return FakeResp()

        @staticmethod
        def post(url, **kw):
            seen["post_url"] = url
            return FakeResp()

    orig_req, orig_key = transcribe._requests, config.DASHSCOPE_API_KEY
    transcribe._requests = lambda: FakeRequests
    config.DASHSCOPE_API_KEY = "sk-test"

    tmp = Path(tempfile.mkdtemp(prefix="lgup-"))
    f = tmp / "a.mp3"
    f.write_bytes(b"x")
    try:
        url = transcribe.upload_for_temp_url(f)
    finally:
        transcribe._requests, config.DASHSCOPE_API_KEY = orig_req, orig_key

    assert seen["method"] == "GET", "取凭证必须 GET"
    assert seen["params"]["action"] == "getPolicy"
    assert seen["params"]["model"] == transcribe.MODEL_DIAR, "文件与模型绑定，模型名必须带上"
    assert url.startswith("oss://"), url


def test_transcribe_resolves_oss_url_with_required_header() -> None:
    """用 oss:// 临时 URL 调模型必须带 X-DashScope-OssResourceResolve: enable。

    缺这个头服务端解析不了 oss:// 链接，会失败——文档明确警告过，
    这里锁住，避免以后重构时把请求头弄丢。
    """
    from lg_assistant import transcribe

    seen: dict = {}

    class FakeResp:
        status_code = 200
        text = "{}"

        def json(self):
            return {"output": {"task_id": "t1"}}

    class FakeRequests:
        @staticmethod
        def post(url, **kw):
            seen["headers"] = kw.get("headers") or {}
            return FakeResp()

    orig_req, orig_key = transcribe._requests, config.DASHSCOPE_API_KEY
    transcribe._requests = lambda: FakeRequests
    config.DASHSCOPE_API_KEY = "sk-test"
    try:
        task = transcribe.submit("oss://bucket/x.mp3", speaker_count=3)
    finally:
        transcribe._requests, config.DASHSCOPE_API_KEY = orig_req, orig_key

    assert task == "t1"
    assert seen["headers"].get("X-DashScope-OssResourceResolve") == "enable"
    assert seen["headers"].get("X-DashScope-Async") == "enable"


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
# 播报语安全（时效性断言）
# --------------------------------------------------------------------------- #
def test_stale_claim_detector_blocks_time_assertions() -> None:
    """回归：模型把「二十届三中全会尚未召开」放进了播报语。

    真实事故：该会 2024 年 7 月已召开，模型用的是过期知识，
    却用了确定语气，还被单独高亮成一条「播报」——等于把错误信息
    说得很确定。语音是一次性、不可回看的，说错没有机会核对。

    这里只拦「时效性断言」，正常的播报语必须照常放行，
    否则会把所有播报都退化成无用的正文摘录。
    """
    import importlib

    import lg_assistant.ported.speech as speech_mod

    speech_mod = importlib.reload(speech_mod)

    blocked = [
        "答案是D，因为二十届三中全会尚未召开，相关文件不存在。",
        "截至2024年6月，该文件未发布。",
        "该会议还未召开，因此D项错误。",
        "预计2028年出货量激增。",
    ]
    for s in blocked:
        assert speech_mod.looks_like_stale_claim(s) is True, s

    allowed = [
        "答案是B，因为5乘7加3等于38，符合方程。",
        "已暂停训练。膝盖不适已记录，请立即停止该动作。",
        "这道题选C，解析见下。",
        "共找到3份资料。",
    ]
    for s in allowed:
        assert speech_mod.looks_like_stale_claim(s) is False, s


def test_postprocess_replaces_unsafe_speech() -> None:
    """含时效断言的播报语要被换掉，且换成正则压缩的结果。"""
    _fresh()
    out = nodes.postprocess(
        {
            "routing": {"scene": "general"},
            "result": {
                "text": "这道题选D。党的二十届三中全会尚未召开，相关文件不存在。",
                "speech": "答案是D，因为二十届三中全会尚未召开。",
            },
            "owner": "u",
        }
    )
    speech = out["result"]["speech"]
    assert "尚未召开" not in speech, f"危险播报语未被替换：{speech!r}"
    assert out["result"].get("speech_note"), "替换行为要留痕，便于排查"
    assert speech, "替换后仍要有播报语"


def test_postprocess_keeps_safe_speech_untouched() -> None:
    """正常播报语不能被误伤——否则等于把播报功能废掉。"""
    _fresh()
    original = "答案是B，因为5乘7加3等于38。"
    out = nodes.postprocess(
        {
            "routing": {"scene": "general"},
            "result": {"text": "答案：B\n解析…", "speech": original},
            "owner": "u",
        }
    )
    assert out["result"]["speech"] == original
    assert not out["result"].get("speech_note")


def test_exam_prompt_has_time_anchor() -> None:
    """提示词必须明确「知识有截止时间」，不能拿它当当下。

    原来的提示词只说「不得凭记忆确认现行政策」，没有给任何时间锚点，
    模型于是真心以为二十届三中全会还没开——它并不知道自己过时了。
    """
    from lg_assistant import vision_prompts

    assert "时间锚点" in vision_prompts.METHODS
    assert "知识存在截止时间" in vision_prompts.METHODS
    # 反例要写进去，光说「注意时效」模型抓不住
    assert "二十届三中全会" in vision_prompts.METHODS
    assert "尚未召开" in vision_prompts.METHODS


def test_postprocess_appends_stale_warning_to_text() -> None:
    """含时效性断言时，警告要直接拼进正文。

    为什么不交给前端渲染：一是前端当时正被另一处改动大改，不宜并发编辑；
    二是拼进正文后无论前端怎么改版都必然可见——这条提示不能因为 UI 改版而丢。
    """
    _fresh()
    out = nodes.postprocess(
        {
            "routing": {"scene": "general"},
            "result": {"text": "截至2024年6月，党的二十届三中全会尚未召开。"},
            "owner": "u",
        }
    )
    text = out["result"]["text"]
    assert "请以官方最新发布为准" in text, "警告必须出现在正文里"
    assert "可能已过时" in text
    assert out["result"].get("stale_warning")
    assert any("时效性" in n for n in (out.get("notes") or []))


def test_postprocess_does_not_warn_on_ordinary_answers() -> None:
    """普通回答不能被误标——否则警告会变成噪声，用户就不再看了。"""
    _fresh()
    for text in (
        "答案是B，因为5乘7加3等于38。",
        "已暂停训练，膝盖不适已记录。",
        "共找到3份资料。",
    ):
        out = nodes.postprocess(
            {"routing": {"scene": "general"}, "result": {"text": text}, "owner": "u"}
        )
        assert "请以官方最新发布为准" not in out["result"]["text"], text
        assert not out["result"].get("stale_warning"), text


# --------------------------------------------------------------------------- #
# 联网检索（时政类问题的正解）
# --------------------------------------------------------------------------- #
def test_search_needed_only_for_time_sensitive_questions() -> None:
    """检索有延迟和成本，不能每道题都查；但宁可多查也不能漏查。

    漏查的后果就是原来那个 bug：把「二十届三中全会」答成「尚未召开」。
    """
    from lg_assistant import search

    must = [
        "党的二十届三中全会是否已经召开？",
        "最近一次中央经济工作会议有什么重点？",
        "某政策目前是否生效",
        "最新版的条例是什么",
        "第二十届中央委员会第三次全体会议什么时间召开",
    ]
    for q in must:
        assert search.needs_search(q) is True, q

    must_not = [
        "这道题选什么",
        "计算 (18+24)*3",
        "膝盖有点疼",
        "你好",
        "帮我生成会议纪要",
    ]
    for q in must_not:
        assert search.needs_search(q) is False, q


def test_search_uses_native_dashscope_with_search_enabled() -> None:
    """**必须走 dashscope 原生接口**——OpenAI 兼容层会忽略 enable_search。

    实测对比（同一个问题、同一个模型）：
      OpenAI 兼容 + extra_body.enable_search -> 「尚未召开」（没联网）
      dashscope.Generation.call(enable_search) -> 「2024年7月15日至18日」（真联网）
    这条断言锁住走哪条路，避免以后"顺手"改成 OpenAI 兼容层把联网弄丢。
    """
    from lg_assistant import search

    seen: dict = {}

    class FakeMessage:
        content = "答案"

    class FakeChoice:
        message = FakeMessage

    class FakeOutput:
        choices = [FakeChoice]
        search_info = {
            "search_results": [
                {"title": "T1", "url": "https://a.example/1", "site_name": "S1"},
                {"title": "T2", "url": "https://a.example/1", "site_name": "S1"},  # 重复
                {"title": "T3", "url": "https://b.example/2", "site_name": "S2"},
            ]
        }

    class FakeResp:
        status_code = 200
        output = FakeOutput

    class FakeGeneration:
        @staticmethod
        def call(**kw):  # 生产代码走 dashscope.Generation.call(...)
            seen.update(kw)
            return FakeResp

    class FakeDashscope:
        api_key = ""
        Generation = FakeGeneration

    import lg_assistant.search as search_mod

    orig, orig_key = search_mod._dashscope, config.DASHSCOPE_API_KEY
    search_mod._dashscope = lambda: FakeDashscope
    config.DASHSCOPE_API_KEY = "sk-test"
    try:
        r = search.search_answer("问题", system="你是助手")
    finally:
        search_mod._dashscope = orig
        config.DASHSCOPE_API_KEY = orig_key

    assert seen.get("enable_search") is True, "必须开 enable_search"
    assert seen.get("search_options", {}).get("enable_source") is True
    assert any(m["role"] == "system" for m in seen["messages"]), "system 要透传"
    assert r["searched"] is True and r["answer"] == "答案"
    urls = [s["url"] for s in r["sources"]]
    assert urls == ["https://a.example/1", "https://b.example/2"], urls


def test_search_failure_is_explicit_not_silent() -> None:
    """检索失败必须显式提示，**不能静默改成凭记忆作答**。

    静默降级正是原来出错的方式：用户以为答案是核实过的，其实来自过期知识。
    """
    _fresh()
    from lg_assistant import search

    orig_search, orig_chat = search.search_answer, llm.chat

    def boom(*a, **kw):
        raise search.SearchError("模拟检索不可用")

    search.search_answer = boom
    llm.chat = lambda messages, **kw: "党的二十届三中全会尚未召开。"
    try:
        out = nodes.local_llm({"text": "党的二十届三中全会是否已经召开？", "routing": {}})
    finally:
        search.search_answer, llm.chat = orig_search, orig_chat

    text = out["result"]["text"]
    assert "联网检索未能完成" in text, "必须显式说明没查到"
    assert "可能已过时" in text
    assert out["result"]["note"] == "时效性问题，联网检索失败"


def test_search_success_attaches_sources_and_skips_second_call() -> None:
    """检索成功时直接用检索结果，不再多调一次模型（省一次调用）。"""
    _fresh()
    from lg_assistant import search

    calls = {"chat": 0}
    orig_search, orig_chat = search.search_answer, llm.chat

    search.search_answer = lambda q, **kw: {
        "answer": "该会已于2024年7月15日至18日召开。",
        "sources": [{"title": "T", "url": "https://x.example/1", "site": "S"}],
        "searched": True,
    }

    def counting_chat(messages, **kw):
        calls["chat"] += 1
        return "不应被调用"

    llm.chat = counting_chat
    try:
        out = nodes.local_llm({"text": "党的二十届三中全会是否已经召开？", "routing": {}})
    finally:
        search.search_answer, llm.chat = orig_search, orig_chat

    text = out["result"]["text"]
    assert "2024年7月15日至18日" in text
    assert "检索来源" in text, "必须附来源供核对"
    assert calls["chat"] == 0, "已有检索结果时不该再调一次模型"
    assert out["result"]["artifacts"][0]["kind"] == "search"


def test_vision_path_searches_with_observation_not_just_user_text() -> None:
    """图片时政题要联网，且检索词必须含**转写出来的题面**。

    用户那句话通常只是「解这道题」，拿它去检索什么也查不到。
    """
    tmp = _fresh()
    from lg_assistant import search, vision

    seen: dict = {}
    orig = (search.search_answer, vision.observe, vision.solve, vision.review)

    def fake_search(q, **kw):
        seen["query"] = q
        return {"answer": "该会2024年7月召开", "sources": [], "searched": True}

    def fake_observe(text, images):
        return "题干：党的二十届三中全会是否已经召开？A 已召开 B 未召开"

    def fake_solve(text, observation, images, **kw):
        seen["solve_web"] = kw.get("web_context", "")
        return {"answerable": True, "answer": "A", "calculations": [], "binary_grid": None}

    def fake_review(text, draft, tool_result, images, **kw):
        seen["review_web"] = kw.get("web_context", "")
        return {"answerable": True, "answer": "A", "explanation": "x", "review_notes": "y"}

    search.search_answer = fake_search
    vision.observe = fake_observe
    vision.solve = fake_solve
    vision.review = fake_review
    try:
        nodes.exam_vision(
            {"files": [_png(tmp)], "text": "解这道题", "routing": {"scene": "exam"}}
        )
    finally:
        (
            search.search_answer,
            vision.observe,
            vision.solve,
            vision.review,
        ) = orig

    assert "二十届三中全会" in seen.get("query", ""), "检索词必须含题面"
    # 初解与终审都要拿到检索结果，否则终审会把它"纠正"回过期答案
    assert seen.get("solve_web"), "初解必须拿到检索结果"
    assert seen.get("review_web"), "终审也必须拿到检索结果"


# --------------------------------------------------------------------------- #
# 健身场景的三个真 bug（真实链路走查发现）
# --------------------------------------------------------------------------- #
def test_fitness_action_start_and_setdone_not_swapped() -> None:
    """回归一：裸动词「做」把开始与做组判定对调了。

    原词表 FITNESS_START_WORDS 里有裸动词「做」和「练」：
      - 「做完一组」命中「做」-> 判成 start_exercise
      - 「开始深蹲」命不中任何词 -> 判成空
    两者完全对调。后果：状态机被反复重置，**组数一条也记不上**，
    且训练根本没进入 active。
    """
    assert routing.infer_fitness_action("做完一组") == "set_done"
    assert routing.infer_fitness_action("再来一组") == "set_done"
    # 具体动作名要能接住——用户说的是「开始深蹲」而不是「开始训练」
    for t in ("开始深蹲", "开始深蹲 3组10次 60公斤", "深蹲 3组10次", "卧推 4组10次"):
        assert routing.infer_fitness_action(t) == "start_exercise", t
    # 不适永远优先于开始（安全顺序不可换）
    assert routing.infer_fitness_action("膝盖疼，今天不练了") == "pain_report"


def test_paused_workout_blocks_set_done_with_explanation() -> None:
    """回归二：疼痛暂停后仍回「好，休息30秒，准备下一组」。

    实测事故：报告「膝盖有点疼」后状态机已置 paused，用户接着说「做完一组」，
    回答仍在鼓励他继续练。状态机拒绝了计数，但回答由模型生成、它看不到状态。
    现在改成确定性安全文案，并且要说明「为什么没记」与「怎样算可以继续」。
    """
    _fresh()
    out = nodes.local_llm(
        {
            "text": "做完一组",
            "routing": {"scene": "fitness", "action": "set_done"},
            "fitness": {"workout": {"status": "paused", "current": "深蹲", "total_sets": 2}},
        }
    )
    text = out["result"]["text"]
    assert "没有记录" in text, "必须明确说这一组没被计入"
    assert "休息30秒" not in text, "不能再出现鼓励继续练的话"
    assert "继续" in text and "结束训练" in text, "要给出恢复与结束两条出路"
    assert out["result"]["note"]


def test_paused_workout_still_allows_ending() -> None:
    """回归三：「结束训练」不能被安全闸拦住。

    闸门做完后实测发现，暂停态下发「结束训练」也只会回一句
    「训练当前处于暂停状态」——用户拿不到训练总结，被卡在暂停态出不来。
    """
    _fresh()
    out = nodes.local_llm(
        {
            "text": "结束训练",
            "routing": {"scene": "fitness", "action": "end_workout"},
            "fitness": {"workout": {"status": "paused", "current": "深蹲", "total_sets": 2}},
        }
    )
    assert "暂停状态" not in out["result"]["text"], "结束训练不应被安全闸拦截"


def test_sticky_flags_come_from_session_state_not_event() -> None:
    """回归四：``event["_sticky"]`` 从来没有被写入过，粘性是死代码。

    后果：用户说「继续」想从暂停恢复，命不中关键词、粘性又失效，
    被路由到 general，**训练卡在暂停态出不来**。
    现在从会话状态推导。
    """
    assert nodes.sticky_flags({}) == {"meeting": False, "fitness": False}
    assert nodes.sticky_flags({"fitness": {"workout": {"status": "active"}}})["fitness"] is True
    assert nodes.sticky_flags({"fitness": {"workout": {"status": "paused"}}})["fitness"] is True
    assert nodes.sticky_flags({"fitness": {"awaiting": "age"}})["fitness"] is True
    assert nodes.sticky_flags({"meeting": {"status": "collecting"}})["meeting"] is True
    # 已结束的训练不该继续粘住
    assert nodes.sticky_flags({"fitness": {"workout": {"status": "idle"}}})["fitness"] is False


def test_training_skips_llm_router_and_stays_in_fitness() -> None:
    """训练进行中要跳过模型兜底：那些话大多不含关键词，交给模型既慢又不准。"""
    _fresh()
    calls: list = []
    orig = nodes._ROUTER
    nodes.set_router(lambda *a, **kw: (calls.append(1), {"scene": "general"})[1])
    try:
        # 训练进行中 + 判得出动作 -> 不调模型兜底
        out = nodes.route_node(
            {
                "text": "歇好了",
                "fitness": {"workout": {"status": "paused"}},
                "device": {},
            }
        )
        assert out["routing"]["scene"] == "fitness", out["routing"]
        # 「歇好了」命不中动作，但其本身不是训练事件时会走兜底——
        # 这里只断言「判得出动作时确实没调模型」
        calls.clear()
        out2 = nodes.route_node(
            {
                "text": "做完一组",
                "fitness": {"workout": {"status": "active"}},
                "device": {},
            }
        )
        assert out2["routing"]["action"] == "set_done"
        assert calls == [], "判得出动作时不该调模型兜底"
    finally:
        nodes.set_router(orig)


def test_training_does_not_swallow_unrelated_requests() -> None:
    """训练中也要能问别的：判不出动作时不粘，避免「帮我算个数」被吞掉。

    这正是基线踩过的坑，路由顺序才定成关键词优先于粘性。
    """
    _fresh()
    out = nodes.route_node(
        {"text": "计算 (18+24)*3", "fitness": {"workout": {"status": "active"}}, "device": {}}
    )
    assert out["routing"]["scene"] == "exam", out["routing"]


def test_resume_only_works_from_paused() -> None:
    """「继续」只能从暂停恢复；idle 时不该凭空开出一场训练。"""
    _fresh()
    paused = nodes.next_workout(
        {
            "routing": {"scene": "fitness", "action": "resume_workout"},
            "fitness": {"workout": {"status": "paused", "total_sets": 2}},
        }
    )
    assert paused["status"] == "active" and paused["total_sets"] == 2

    idle = nodes.next_workout(
        {
            "routing": {"scene": "fitness", "action": "resume_workout"},
            "fitness": {"workout": {"status": "idle", "total_sets": 0}},
        }
    )
    assert idle["status"] == "idle", "idle 时说「继续」不应开始训练"


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
