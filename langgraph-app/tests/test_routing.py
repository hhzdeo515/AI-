"""路由与附件信号测试。不需要 API Key、不联网。

两个真事故驱动了这些用例（用户截图里的现象）：

1. 锻炼会话里贴了图形推理题问「这个选什么」——「选什么」一个关键词都不含、
   粘性还停在 fitness，于是被送进训练助手，回了一句
   「请告诉我具体在选什么？比如动作、器械、组数、重量，还是其他？」。
   **贴图这个动作比会话里残留的场景状态可信得多**，所以附件必须压过粘性。
2. 紧接着追问「这个题目答案是什么」——这回关键词命中了 exam，
   但本轮没有附件，视觉链回「请提供具体题目内容」。图明明上一轮刚看过。
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from contextlib import ExitStack
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lg_assistant import config, graph, nodes, routing, vision  # noqa: E402


def _fresh() -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="lgrouting-"))
    config.DATA_DIR = tmp
    config.UPLOAD_DIR = tmp / "uploads"
    return tmp


def _png(tmp: Path, name: str = "q.png") -> str:
    p = tmp / name
    p.write_bytes(b"\x89PNG\r\n\x1a\n")  # 内容不重要，路径与后缀才是被测对象
    return str(p)


# --------------------------------------------------------------------------- #
# 1) 附件类型参与路由
# --------------------------------------------------------------------------- #
def test_image_beats_sticky_meeting() -> None:
    """事故 1：贴图问「这个选什么」，不能再被锻炼粘性吃掉。"""
    r = routing.route(text="这个选什么", sticky={"meeting": True}, files=["E:/x/q.png"])
    assert r == {"scene": "exam", "action": "", "source": "attachment"}


def test_audio_beats_sticky_meeting() -> None:
    r = routing.route(text="嗯你听一下", sticky={"meeting": True}, files=["E:/x/m.mp3"])
    assert r["scene"] == "meeting" and r["source"] == "attachment"


def test_image_beats_meeting_sticky() -> None:
    r = routing.route(text="这个选什么", sticky={"meeting": True}, files=["E:/x/q.jpg"])
    assert r["scene"] == "exam"


def test_keyword_still_beats_attachment() -> None:
    """关键词优先于附件：贴图的会议纪要整理仍然是会议。"""
    r = routing.route(text="整理会议纪要", files=["E:/x/q.png"])
    assert r["scene"] == "meeting" and r["source"] == "keyword"


def test_hint_still_beats_attachment() -> None:
    r = routing.route(text="这个选什么", scene_hint="meeting", files=["E:/x/q.png"])
    assert r["scene"] == "meeting" and r["source"] == "hint"


def test_event_still_beats_attachment() -> None:
    r = routing.route(
        text="这个选什么",
        event={"semantic_action": "start_meeting"},
        files=["E:/x/q.png"],
    )
    assert r["scene"] == "meeting" and r["source"] == "event"


def test_sticky_unchanged_without_attachment() -> None:
    """回归：没有附件时粘性照旧生效（训练中不贴图不能乱跳场景）。"""
    r = routing.route(text="嗯", sticky={"meeting": True})
    assert r["scene"] == "meeting" and r["source"] == "sticky"


def test_unknown_extension_is_not_an_attachment_signal() -> None:
    r = routing.route(text="这个选什么", sticky={"meeting": True}, files=["E:/x/notes.txt"])
    assert r["scene"] == "meeting", "认不出的后缀不该被当成解题信号"


def test_llm_router_receives_real_file_count() -> None:
    seen: dict = {}

    def fake_router(text, event, file_count):
        seen["n"] = file_count
        return {"scene": "general", "action": "answer"}

    r = routing.route(text="说点什么", llm_router=fake_router, files=["E:/x/a.txt", "E:/x/b.txt"])
    assert r["scene"] == "general"
    assert seen["n"] == 2, "兜底路由器要看到真实附件数量（以前这里硬编码 0）"


def test_route_node_passes_files_through() -> None:
    """路由节点必须把附件带进五级路由，否则上面的规则永远不生效。"""
    original = nodes._ROUTER
    nodes._ROUTER = None  # 关掉模型兜底，纯规则
    try:
        out = nodes.route_node({
            "text": "这个选什么",
            "files": ["E:/x/q.png"],
            "meeting": {"status": "collecting"},
        })
    finally:
        nodes._ROUTER = original
    assert out["routing"]["scene"] == "exam"
    assert out["routing"]["source"] == "attachment"


# --------------------------------------------------------------------------- #
# 2) 追问接着看同一张图
# --------------------------------------------------------------------------- #
def _stub_vision(seen: dict):
    """把四步链路换掉，只记录「这一轮到底看了哪张图」。"""
    originals = (vision.observe, vision.solve, vision.run_tools, vision.review)
    stubs = ExitStack()
    def fake_classify(question, *, requested="auto", backend="original"):
        seen.setdefault("classification_calls", []).append({"question": question, "requested": requested, "backend": backend})
        return {"agent": "ability", "question": question, "confidence": 1,
                "reason": "离线回归中的图形推理题", "decision_model": "offline-test"}
    stubs.enter_context(patch("lg_assistant.practice_agents.classify_task", side_effect=fake_classify))
    stubs.enter_context(patch("lg_assistant.exam_batch.detect_questions", return_value={
        "questions": [{"number": "1", "preview": "", "complete": True, "missing": [],
                       "regions": [{"page": 1, "bbox": [0, 0, 1000, 1000]}], "context_regions": []}],
        "notes": []}))
    stubs.enter_context(patch("lg_assistant.exam_batch._crop_images",
        side_effect=lambda images, question, crop_dir: list(images)))
    stubs.enter_context(patch("lg_assistant.vision.independent_solution",
        return_value={"answerable": True, "answer": "B"}))

    def fake_observe(text, images):
        seen["observe_images"] = list(images)
        seen.setdefault("observe_calls", []).append(list(images))
        return "题面：九宫格图形推理，选下一个。"

    def fake_solve(text, observation, images, web_context="", specialist=""):
        seen["solve_images"] = list(images)
        seen.setdefault("solve_calls", []).append(list(images))
        return {"answerable": True, "answer": "B", "explanation": "对角一致。"}

    def fake_review(text, draft, tool, images, web_context="", **kwargs):
        return dict(draft)

    vision.observe = fake_observe
    vision.solve = fake_solve
    vision.run_tools = lambda draft: {}
    vision.review = fake_review
    return originals, stubs


def _restore(originals) -> None:
    originals, stubs = originals
    vision.observe, vision.solve, vision.run_tools, vision.review = originals
    stubs.close()


def test_followup_reuses_last_image() -> None:
    """事故 2：不带附件的追问，要接着看上一轮那张图。"""
    tmp = _fresh()
    img = _png(tmp)
    seen: dict = {}
    originals = _stub_vision(seen)
    try:
        out = nodes.exam_vision({
            "text": "这个题目答案是什么",
            "files": [],
            "last_images": [img],
            "routing": {"scene": "exam"},
        })
    finally:
        _restore(originals)

    assert seen["observe_images"] == [img], "复用的图必须真的进视觉链"
    assert seen["solve_images"] == [img]
    assert "沿用上一轮的题目照片" in out["result"]["text"], "要明说这一轮看的是旧图"
    assert out["result"]["artifacts"][0]["reused_image"] is True
    assert next(a for a in out["result"]["artifacts"] if a["kind"] == "exam_batch")["reused_image"] is True
    assert out["last_images"] == [img], "记下来，下一轮还能接着用"


def test_fresh_image_is_not_marked_reused() -> None:
    tmp = _fresh()
    old, new = _png(tmp, "old.png"), _png(tmp, "new.png")
    seen: dict = {}
    originals = _stub_vision(seen)
    try:
        out = nodes.exam_vision({
            "text": "这个选什么",
            "files": [new],
            "last_images": [old],
            "routing": {"scene": "exam"},
        })
    finally:
        _restore(originals)

    assert seen["observe_images"] == [new], "本轮带了图就用本轮的图"
    assert "沿用上一轮" not in out["result"]["text"]
    assert out["result"]["artifacts"][0]["reused_image"] is False
    assert next(a for a in out["result"]["artifacts"] if a["kind"] == "exam_batch")["reused_image"] is False
    assert out["last_images"] == [new]


def test_missing_last_image_is_not_reused() -> None:
    """上一轮的图片文件被清掉了（data/uploads 会被清理）→ 老实说没有图片。"""
    tmp = _fresh()
    seen: dict = {}
    originals = _stub_vision(seen)
    try:
        out = nodes.exam_vision({
            "text": "这个题目答案是什么",
            "files": [],
            "last_images": [str(tmp / "gone.png")],
            "routing": {"scene": "exam"},
        })
    finally:
        _restore(originals)

    assert "没有找到可识别的图片" in out["result"]["text"]
    assert "observe_images" not in seen, "文件都不在了，不能进视觉链"


def test_audio_only_request_never_reuses_image() -> None:
    """传了录音却拿旧题图去解题是最糟的错位——只有 exam 场景才复用。"""
    tmp = _fresh()
    img = _png(tmp)
    seen: dict = {}
    originals = _stub_vision(seen)
    try:
        out = nodes.exam_vision({
            "text": "这段录音整理一下",
            "files": [str(tmp / "m.mp3")],
            "last_images": [img],
            "routing": {"scene": "exam"},
        })
    finally:
        _restore(originals)
    # 走到这里说明只带了音频：视觉链读不到图 → 明确报「没有图片」，而不是偷偷用旧图
    assert "没有找到可识别的图片" in out["result"]["text"]
    assert "observe_images" not in seen


# --------------------------------------------------------------------------- #
# 3) 条件边：什么时候算「视觉请求」
# --------------------------------------------------------------------------- #
def test_dispatch_sends_image_exam_to_vision() -> None:
    from lg_assistant import graph

    tmp = _fresh()
    img = _png(tmp)
    assert graph.dispatch({"files": [img], "routing": {"scene": "exam"}}) == "photo_practice"


def test_dispatch_sends_followup_with_last_image_to_vision() -> None:
    """事故 2 的关键一步：没有附件、但会话里留着上一轮的图 → 仍然走视觉链。"""
    from lg_assistant import graph

    tmp = _fresh()
    img = _png(tmp)
    assert graph.dispatch({
        "files": [], "text": "这个题目答案是什么",
        "routing": {"scene": "exam"}, "last_images": [img],
    }) == "photo_practice"


def test_dispatch_keeps_arith_on_fast_path() -> None:
    """会话里留着旧图时，「计算 (18+24)*3」不能被拿去解那张图。"""
    from lg_assistant import graph

    tmp = _fresh()
    img = _png(tmp)
    assert graph.dispatch({
        "files": [], "text": "计算 (18+24)*3",
        "routing": {"scene": "exam"}, "last_images": [img],
    }) == "local"
    assert nodes.is_pure_arith("计算 (18+24)*3") is True
    assert nodes.is_pure_arith("这个题目答案是什么") is False


def test_dispatch_ignores_missing_last_image() -> None:
    from lg_assistant import graph

    tmp = _fresh()
    assert graph.dispatch({
        "files": [], "text": "这个呢",
        "routing": {"scene": "exam"}, "last_images": [str(tmp / "gone.png")],
    }) == "local"


def test_dispatch_audio_still_goes_to_transcribe() -> None:
    from lg_assistant import graph

    tmp = _fresh()
    img = _png(tmp)
    assert graph.dispatch({
        "files": [str(tmp / "m.mp3")], "text": "这个呢",
        "routing": {"scene": "exam"}, "last_images": [img],
    }) == "audio"


def test_followup_reaches_vision_across_turns_in_one_thread() -> None:
    """整图跑一遍：第一轮贴图，第二轮追问——第二轮必须复用第一轮那张图。

    这条是用户截图里那串对话的完整回归：状态要真的穿过检查点活到下一轮。
    """
    from lg_assistant import graph, search

    tmp = _fresh()
    config.CHECKPOINT_DB = tmp / "ck.sqlite3"
    img = _png(tmp)
    seen: dict = {}
    calls: list = []
    originals = _stub_vision(seen)
    original_search = search.needs_search
    original_router = nodes._ROUTER
    search.needs_search = lambda q: False          # 别联网
    nodes._ROUTER = lambda text, event, n: {"scene": "exam", "action": ""}
    try:
        app = graph.build_graph(graph.open_checkpointer(tmp / "ck.sqlite3"))
        cfg = graph.run_config("u", "s")
        first = app.invoke({"text": "这个选什么", "owner": "u", "session_id": "s",
                    "files": [img], "event": {}, "notes": []}, cfg)
        calls.append(list(seen.get("observe_images") or []))
        out = app.invoke({"text": "这个题目答案是什么", "owner": "u", "session_id": "s",
                          "files": [], "event": {}, "notes": []}, cfg)
        calls.append(list(seen.get("observe_images") or []))
    finally:
        _restore(originals)
        search.needs_search = original_search
        nodes._ROUTER = original_router

    assert calls[0] == [img], "第一轮用本轮的图"
    assert calls[1] == [img], "第二轮没有附件，要接着看上一轮那张"
    assert seen["solve_calls"] == [[img], [img]], "两轮都必须真实经过单题视觉解答，不能读到上一轮遗留的调用记录"
    assert len(seen["classification_calls"]) == 1, "第一轮经过题型分类，第二轮沿用保存的职业能力题型"
    assert seen["classification_calls"][0]["requested"] == "auto"
    assert next(a for a in first["result"]["artifacts"] if a["kind"] == "practice_route")["agent"] == "ability"
    assert next(a for a in out["result"]["artifacts"] if a["kind"] == "practice_route")["decision_model"] == "previous"
    assert out["scene" if False else "result"], out
    assert "沿用上一轮的题目照片" in out["result"]["text"]
    assert out["last_images"] == [img]


# --------------------------------------------------------------------------- #
# 会议暂停 / 恢复
# --------------------------------------------------------------------------- #
def test_meeting_pause_resume_words() -> None:
    """暂停/恢复只认明确短语，裸词只在整句就是它时才认。"""
    f = routing.infer_meeting_action
    assert f("暂停会议") == "pause"
    assert f("暂停") == "pause"
    assert f("继续会议") == "resume"
    assert f("恢复记录") == "resume"
    # 会议正文里的裸词不能被误判成指令
    assert f("李红：三季度恢复产能，需要两周。") == "append"
    assert f("项目暂停两周") == "append"


def test_paused_meeting_keeps_sticky() -> None:
    """暂停也要粘住：否则暂停时贴的转写会被静默转给别的场景。"""
    assert nodes.sticky_flags({"meeting": {"status": "paused"}})["meeting"] is True
    assert nodes.sticky_flags({"meeting": {"status": "collecting"}})["meeting"] is True
    assert nodes.sticky_flags({"meeting": {"status": "ended"}})["meeting"] is False
    assert nodes.sticky_flags({"meeting": {"status": "idle"}})["meeting"] is False


def test_paused_meeting_stays_local_in_dispatch() -> None:
    """暂停中的会议是本地状态机，不能转给 Dify（Dify 侧没有这个状态）。"""
    assert graph.dispatch({
        "text": "李红：预算要重批。",
        "files": [],
        "event": {},
        "routing": {"scene": "meeting", "action": "append", "source": "sticky"},
        "meeting": {"status": "paused"},
    }) == "local"


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
