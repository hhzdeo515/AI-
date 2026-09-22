"""会议场景单元测试：纯逻辑，不需要 API Key。

直接运行：python tests/test_meeting.py
或用 pytest：pytest tests/ -v
"""

from __future__ import annotations

import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from assistant_lite import config, session  # noqa: E402
from assistant_lite.agents.meeting import state as st  # noqa: E402
from assistant_lite.orchestrator import Orchestrator  # noqa: E402
from assistant_lite.schemas import (  # noqa: E402
    STATUS_DUPLICATE,
    STATUS_NEED_INPUT,
    STATUS_OK,
    Task,
)


def _fresh_db() -> Path:
    """把存储指到临时目录，避免污染真实 data/。"""
    tmp = Path(tempfile.mkdtemp(prefix="alite-test-"))
    config.DATA_DIR = tmp
    config.DB_PATH = tmp / "t.sqlite3"
    config.PROFILE_DIR = tmp / "profiles"
    config.UPLOAD_DIR = tmp / "uploads"
    config.EXPORT_DIR = tmp / "exports"
    session._local = threading.local()
    session._initialised = False
    return tmp


# --------------------------------------------------------------------------- #
# 状态机
# --------------------------------------------------------------------------- #
def test_state_machine_flow() -> None:
    s: dict = {}
    text, status = st.start(s)
    assert status == STATUS_OK, text
    assert s["meeting"]["status"] == "collecting"
    mid = s["meeting"]["id"]
    assert mid, "start 应生成会议 id"

    # 重复 start 不应重置已有会话
    st.start(s)
    assert s["meeting"]["id"] == mid, "重复 start 不应换 id"

    text, status = st.append(s, "张三：我负责接口文档，周五前提交。")
    assert status == STATUS_OK, text
    assert "张三" in s["meeting"]["transcript"]

    text, status = st.stop(s)
    assert s["meeting"]["status"] == "ended", text

    ok, msg = st.ready_to_summarize(s)
    assert ok, msg


def test_empty_meeting_cannot_summarize() -> None:
    s: dict = {}
    ok, msg = st.ready_to_summarize(s)
    assert not ok
    assert "没有会议内容" in msg

    text, status = st.stop(s)
    assert status == STATUS_NEED_INPUT


def test_char_limit_48000() -> None:
    s: dict = {}
    st.start(s)
    limit = config.MEETING_CHAR_LIMIT
    assert limit == 48000

    big = "字" * (limit - 10)
    text, status = st.append(s, big)
    assert status == STATUS_OK, text
    assert len(s["meeting"]["transcript"]) == limit - 10

    # 再加 20 字必然超限（limit-10+20 > limit）
    text, status = st.append(s, "字" * 20)
    assert status == STATUS_NEED_INPUT, "超限应被拒绝"
    assert "上限" in text
    # 被拒绝的内容不能写进去
    assert len(s["meeting"]["transcript"]) == limit - 10


def test_append_empty_rejected() -> None:
    s: dict = {}
    st.start(s)
    text, status = st.append(s, "   ")
    assert status == STATUS_NEED_INPUT


def test_looks_like_meta() -> None:
    assert st.looks_like_meta("继续记录")
    assert st.looks_like_meta("开始会议")
    assert st.looks_like_meta("然后呢")
    # 真实会议内容不应被当成指令
    assert not st.looks_like_meta("张三说周五前提交接口文档。")
    assert not st.looks_like_meta("会议内容：张三负责接口，李四负责前端。")
    assert not st.looks_like_meta("")


# --------------------------------------------------------------------------- #
# 编排层：路由 / 去重 / 隔离
# --------------------------------------------------------------------------- #
def test_event_routing_is_deterministic() -> None:
    _fresh_db()
    o = Orchestrator()

    scene, action, source = o.route(
        Task(text="随便什么", event={"semantic_action": "start_meeting"})
    )
    assert (scene, action, source) == ("meeting", "start", "event")

    scene, action, source = o.route(
        Task(text="x", event={"semantic_action": "switch_scene", "scene": "exam"})
    )
    assert (scene, action) == ("exam", "switch_scene")

    # 非法场景回落到 general
    scene, action, _ = o.route(
        Task(text="x", event={"semantic_action": "switch_scene", "scene": "navigation"})
    )
    assert scene == "general"


def test_scene_hint_wins() -> None:
    _fresh_db()
    o = Orchestrator()
    scene, _, source = o.route(Task(text="随便什么内容", scene_hint="meeting"))
    assert (scene, source) == ("meeting", "hint")


def test_keyword_routing_no_api_needed() -> None:
    _fresh_db()
    o = Orchestrator()
    scene, _, source = o.route(Task(text="帮我生成会议纪要"))
    assert scene == "meeting"
    assert source in ("keyword", "sticky")


def test_request_id_dedup() -> None:
    _fresh_db()
    o = Orchestrator()
    t1 = Task(text="开始会议记录", owner="alice", session_id="s1", request_id="req-1")
    r1 = o.handle(t1)
    assert r1.scene == "meeting"
    assert r1.action == "start"
    assert r1.status == STATUS_OK

    t2 = Task(text="开始会议记录", owner="alice", session_id="s1", request_id="req-1")
    r2 = o.handle(t2)
    assert r2.status == STATUS_DUPLICATE, "同 request_id 应命中去重"
    assert r2.text == r1.text


def test_owner_isolation() -> None:
    _fresh_db()
    o = Orchestrator()
    o.handle(Task(text="开始会议记录", owner="alice", session_id="s1", request_id="a1"))
    o.handle(Task(text="开始会议记录", owner="bob", session_id="s1", request_id="b1"))

    a = session.load_state("alice", "s1")
    b = session.load_state("bob", "s1")
    assert a["meeting"]["id"] != b["meeting"]["id"], "不同 owner 的会议必须隔离"

    # alice 追加内容不应影响 bob
    o.handle(
        Task(
            text="张三：周五前提交接口文档。",
            owner="alice",
            session_id="s1",
            request_id="a2",
        )
    )
    a2 = session.load_state("alice", "s1")
    b2 = session.load_state("bob", "s1")
    assert "张三" in a2["meeting"]["transcript"]
    assert "张三" not in b2["meeting"]["transcript"]


def test_sticky_meeting_route_and_persist() -> None:
    """会议进行中，不含"会议"二字的转写也应归到会议场景。"""
    _fresh_db()
    o = Orchestrator()
    o.handle(Task(text="开始会议记录", owner="u", session_id="s", request_id="r1"))

    r = o.handle(
        Task(
            text="李四：我负责前端，下周三给联调版本。",
            owner="u",
            session_id="s",
            request_id="r2",
        )
    )
    assert r.scene == "meeting", "粘性场景应命中会议"
    assert r.action == "append"

    state = session.load_state("u", "s")
    assert "李四" in state["meeting"]["transcript"]
    assert state["meeting"]["status"] == "collecting"

    # 说"结束会议"应退出粘性
    r = o.handle(Task(text="结束会议", owner="u", session_id="s", request_id="r3"))
    assert r.action == "stop"
    assert session.load_state("u", "s")["meeting"]["status"] == "ended"


def test_sticky_does_not_swallow_other_scenes() -> None:
    """回归：会议进行中，明确属于其它场景的输入不能被粘性规则吞掉。"""
    _fresh_db()
    o = Orchestrator()
    o.handle(Task(text="开始会议记录", owner="u", session_id="s", request_id="k1"))

    # 此时 meeting 处于 collecting，但这句话明显是解题
    scene, _, source = o.route(
        Task(text="计算 (18+24)*3"), session.load_state("u", "s")
    )
    assert scene == "exam", f"应路由到 exam，实际 {scene}（source={source}）"

    # 会议指令本身仍应归会议
    scene, _, _ = o.route(Task(text="生成会议纪要"), session.load_state("u", "s"))
    assert scene == "meeting"

    # 真正的转写内容（无任何关键词）才走粘性
    scene, _, source = o.route(
        Task(text="李四：我负责前端，下周三给联调版本。"), session.load_state("u", "s")
    )
    assert (scene, source) == ("meeting", "sticky")


def test_transcript_from_event() -> None:
    _fresh_db()
    o = Orchestrator()
    o.handle(Task(text="开始会议", owner="u", session_id="s", request_id="e1"))
    r = o.handle(
        Task(
            text="",
            owner="u",
            session_id="s",
            request_id="e2",
            event={"semantic_action": "append_meeting", "transcript": "王五：预算 30 万。"},
        )
    )
    assert r.action == "append"
    assert "王五" in session.load_state("u", "s")["meeting"]["transcript"]


def test_meeting_accepts_whiteboard_image() -> None:
    """回归：会议场景曾完全忽略图片附件。

    旧版 dify 的提示词是「根据 transcript 与白板文字生成纪要」，
    新版 _extract_transcript 只认 AUDIO_EXT，白板照片会一路落到最后，
    回一句「没有收到会议内容」——明明刚传了文件，这个提示很误导。
    """
    from PIL import Image

    from assistant_lite import llm as L

    tmp = _fresh_db()
    img = tmp / "board.png"
    Image.new("RGB", (8, 8), "white").save(img)

    seen: dict = {}
    original = L.vision

    def fake_vision(prompt, images, system="", **kw):
        seen["system"] = system
        seen["images"] = list(images)
        return "白板内容：\n1. 一期目标：本机版\n2. 负责人：张三"

    L.vision = fake_vision
    try:
        o = Orchestrator()
        r = o.handle(
            Task(text="", owner="u", session_id="s", request_id="wb1",
                 files=[str(img)], scene_hint="meeting")
        )
    finally:
        L.vision = original

    assert r.status == STATUS_OK, r.text
    transcript = session.load_state("u", "s")["meeting"]["transcript"]
    assert "白板内容" in transcript, transcript
    assert "张三" in transcript
    # 必须用「忠实记录」的提示词，不能套考题提示词
    assert "白板" in seen["system"] and "忠实" in seen["system"], seen["system"]
    assert "不要猜测" in seen["system"]
    assert seen["images"] == [str(img)]


def test_meeting_accepts_audio_via_asr() -> None:
    from assistant_lite import llm as L

    tmp = _fresh_db()
    wav = tmp / "m.wav"
    wav.write_bytes(b"RIFF0000WAVEfmt ")

    original = L.asr
    L.asr = lambda path, model=None: "张三：我负责接口文档，周五前提交。"
    try:
        o = Orchestrator()
        r = o.handle(
            Task(text="", owner="u", session_id="s", request_id="au1",
                 files=[str(wav)], scene_hint="meeting")
        )
    finally:
        L.asr = original

    assert r.status == STATUS_OK, r.text
    assert "张三" in session.load_state("u", "s")["meeting"]["transcript"]


def test_meeting_combines_audio_and_image() -> None:
    """录音 + 白板照片同时上传，两份内容都要进转写。"""
    from PIL import Image

    from assistant_lite import llm as L

    tmp = _fresh_db()
    wav = tmp / "m.wav"
    wav.write_bytes(b"RIFF0000WAVEfmt ")
    img = tmp / "b.png"
    Image.new("RGB", (8, 8), "white").save(img)

    o_asr, o_vis = L.asr, L.vision
    L.asr = lambda path, model=None: "李四：我负责前端。"
    L.vision = lambda *a, **kw: "白板：下周三联调"
    try:
        o = Orchestrator()
        r = o.handle(
            Task(text="", owner="u", session_id="s", request_id="mix1",
                 files=[str(wav), str(img)], scene_hint="meeting")
        )
    finally:
        L.asr, L.vision = o_asr, o_vis

    assert r.status == STATUS_OK, r.text
    transcript = session.load_state("u", "s")["meeting"]["transcript"]
    assert "李四" in transcript, transcript
    assert "下周三联调" in transcript, transcript


def test_meeting_partial_attachment_failure_is_reported() -> None:
    """一个附件失败、另一个成功时：内容要保留，失败项要明说，不能静默吞掉。"""
    from PIL import Image

    from assistant_lite import llm as L

    tmp = _fresh_db()
    ok_img = tmp / "ok.png"
    bad_img = tmp / "bad.png"
    Image.new("RGB", (8, 8), "white").save(ok_img)
    Image.new("RGB", (8, 8), "white").save(bad_img)

    original = L.vision

    def flaky(prompt, images, system="", **kw):
        if "bad" in str(images[0]):
            raise L.LLMError("模拟识图失败")
        return "白板：一期做本机版"

    L.vision = flaky
    try:
        o = Orchestrator()
        r = o.handle(
            Task(text="", owner="u", session_id="s", request_id="pf1",
                 files=[str(ok_img), str(bad_img)], scene_hint="meeting")
        )
    finally:
        L.vision = original

    assert r.status == STATUS_OK, r.text
    assert "未处理的部分" in r.text, r.text
    assert "bad.png" in r.text, r.text
    # 成功的那个附件内容应进转写（回复只报字数，不回显内容）
    transcript = session.load_state("u", "s")["meeting"]["transcript"]
    assert "一期做本机版" in transcript, transcript


def test_meeting_all_attachments_fail() -> None:
    from PIL import Image

    from assistant_lite import llm as L

    tmp = _fresh_db()
    img = tmp / "x.png"
    Image.new("RGB", (8, 8), "white").save(img)

    original = L.vision

    def boom(*a, **kw):
        raise L.LLMError("模型不可用")

    L.vision = boom
    try:
        o = Orchestrator()
        r = o.handle(
            Task(text="", owner="u", session_id="s", request_id="af1",
                 files=[str(img)], scene_hint="meeting")
        )
    finally:
        L.vision = original

    assert r.status == STATUS_NEED_INPUT
    assert "附件处理失败" in r.text, r.text


def test_meeting_unsupported_attachment_is_explicit() -> None:
    """既不是音频也不是图片的附件，要给明确说明，不能默默什么都不做。"""
    _fresh_db()
    tmp = Path(tempfile.mkdtemp(prefix="alite-mt-"))
    txt = tmp / "notes.txt"
    txt.write_text("hello", encoding="utf-8")

    o = Orchestrator()
    r = o.handle(
        Task(text="", owner="u", session_id="s", request_id="us1",
             files=[str(txt)], scene_hint="meeting")
    )
    assert r.status == STATUS_NEED_INPUT, r.text
    assert "没能从附件里提取到内容" in r.text, r.text


def test_meeting_event_transcript_still_wins() -> None:
    """事件里带 transcript 时，不应对附件再调模型。"""
    from PIL import Image

    from assistant_lite import llm as L

    tmp = _fresh_db()
    img = tmp / "b.png"
    Image.new("RGB", (8, 8), "white").save(img)

    original = L.vision

    def must_not_call(*a, **kw):
        raise AssertionError("有 event.transcript 时不应调用视觉模型")

    L.vision = must_not_call
    try:
        o = Orchestrator()
        r = o.handle(
            Task(text="", owner="u", session_id="s", request_id="ev1",
                 files=[str(img)],
                 event={"semantic_action": "append_meeting", "transcript": "王五：预算 30 万。"})
        )
    finally:
        L.vision = original

    assert r.status == STATUS_OK, r.text
    assert "王五" in session.load_state("u", "s")["meeting"]["transcript"]


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
