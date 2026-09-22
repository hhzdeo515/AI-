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
