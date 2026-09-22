"""Web 层测试：Flask test client，不需要 API Key。

覆盖：页面渲染、健康检查、文字对话、附件上传（图片/音频/非法格式）、空输入拦截。
"""

from __future__ import annotations

import io
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from assistant_lite import config, session  # noqa: E402


def _fresh() -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="alite-web-"))
    config.DATA_DIR = tmp
    config.DB_PATH = tmp / "t.sqlite3"
    config.PROFILE_DIR = tmp / "profiles"
    config.UPLOAD_DIR = tmp / "uploads"
    config.EXPORT_DIR = tmp / "exports"
    session._local = threading.local()
    session._initialised = False
    return tmp


def _client():
    from assistant_lite.web.app import create_app

    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


def _png(name: str = "q.png"):
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (16, 16), "white").save(buf, format="PNG")
    buf.seek(0)
    return (buf, name)


# --------------------------------------------------------------------------- #
def test_index_renders() -> None:
    _fresh()
    c = _client()
    r = c.get("/")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "assistant-lite" in body
    assert "会议纪要" in body and "拍照解题" in body and "锻炼指导" in body


def test_health() -> None:
    _fresh()
    c = _client()
    r = c.get("/health")
    assert r.status_code == 200
    j = r.get_json()
    assert j["ok"] is True
    assert set(j["scenes"]) == {"meeting", "exam", "fitness", "resource", "general"}
    assert j["model_text"] and j["model_vision"] and j["model_asr"]


def test_chat_text_meeting_start() -> None:
    """文字对话走零 token 路径，无 Key 也能成功。"""
    _fresh()
    c = _client()
    r = c.post(
        "/api/chat",
        data={"text": "开始会议记录", "session_id": "s1", "owner": "u"},
    )
    assert r.status_code == 200, r.get_data(as_text=True)
    j = r.get_json()
    assert j["scene"] == "meeting"
    assert j["action"] == "start"
    assert j["status"] == "ok"
    assert "会议" in j["text"]


def test_chat_empty_rejected() -> None:
    _fresh()
    c = _client()
    r = c.post("/api/chat", data={"text": "   ", "session_id": "s1"})
    assert r.status_code == 400
    assert "error" in r.get_json()


def test_chat_scene_hint() -> None:
    _fresh()
    c = _client()
    r = c.post("/api/chat", data={"text": "计算 (18+24)*3", "scene": "exam", "session_id": "s1"})
    j = r.get_json()
    assert j["scene"] == "exam"
    assert "126" in j["text"]


def test_upload_image_saved() -> None:
    _fresh()
    c = _client()
    r = c.post(
        "/api/chat",
        data={
            "text": "开始会议记录",
            "session_id": "s1",
            "files": _png("题目.png"),
        },
        content_type="multipart/form-data",
    )
    assert r.status_code == 200, r.get_data(as_text=True)
    saved = list(config.UPLOAD_DIR.glob("*.png"))
    assert len(saved) == 1, f"上传文件应落到 uploads/，实际 {list(config.UPLOAD_DIR.iterdir())}"
    # 文件名被重命名为 uuid，避免路径穿越与重名覆盖
    assert saved[0].name != "题目.png"
    assert len(saved[0].stem) == 32


def test_upload_audio_accepted() -> None:
    _fresh()
    c = _client()
    r = c.post(
        "/api/chat",
        data={
            "text": "开始会议记录",
            "session_id": "s1",
            "files": (io.BytesIO(b"RIFF0000WAVEfmt "), "meeting.wav"),
        },
        content_type="multipart/form-data",
    )
    assert r.status_code == 200
    assert len(list(config.UPLOAD_DIR.glob("*.wav"))) == 1


def test_upload_bad_extension_rejected() -> None:
    _fresh()
    c = _client()
    r = c.post(
        "/api/chat",
        data={
            "text": "开始会议记录",
            "session_id": "s1",
            "files": (io.BytesIO(b"#!/bin/sh"), "evil.sh"),
        },
        content_type="multipart/form-data",
    )
    assert r.status_code == 200, "应忽略非法附件而不是整单失败"
    j = r.get_json()
    assert j["rejected_files"], j
    assert "evil.sh" in j["rejected_files"][0]
    assert not list(config.UPLOAD_DIR.glob("*")), "非法附件不能落盘"


def test_resources_and_state_endpoints() -> None:
    _fresh()
    c = _client()
    r = c.get("/api/resources?owner=u")
    assert r.status_code == 200
    assert r.get_json()["resources"] == []

    c.post("/api/chat", data={"text": "开始会议记录", "session_id": "s9", "owner": "u"})
    r = c.get("/api/state?owner=u&session_id=s9")
    assert r.status_code == 200
    st = r.get_json()
    assert st["meeting"]["status"] == "collecting"


def test_session_isolation_across_sessions() -> None:
    _fresh()
    c = _client()
    c.post("/api/chat", data={"text": "开始会议记录", "session_id": "A", "owner": "u"})
    c.post("/api/chat", data={"text": "开始会议记录", "session_id": "B", "owner": "u"})
    a = c.get("/api/state?owner=u&session_id=A").get_json()
    b = c.get("/api/state?owner=u&session_id=B").get_json()
    assert a["meeting"]["id"] != b["meeting"]["id"]


def test_progress_endpoint_without_record() -> None:
    """没有进度记录时返回空 steps —— 前端据此回退，而不是编造阶段。"""
    _fresh()
    c = _client()
    r = c.get("/api/progress?request_id=nope")
    assert r.status_code == 200
    j = r.get_json()
    assert j["steps"] == []
    assert j["finished"] is True


def test_progress_registry_marks_real_states() -> None:
    from assistant_lite import progress

    progress.clear()
    progress.begin("r1", "exam")
    progress.report("r1", "recognize")
    snap = progress.snapshot("r1")
    states = {s["id"]: s["state"] for s in snap["steps"]}
    assert states["capture"] == "done", states
    assert states["recognize"] == "active", states
    assert states["solve"] == "pending", states
    assert states["verify"] == "pending", states

    progress.report("r1", "verify")
    progress.finish("r1")
    snap = progress.snapshot("r1")
    states = {s["id"]: s["state"] for s in snap["steps"]}
    assert states["verify"] == "done", states
    assert snap["finished"] is True

    # 未知 request_id 返回 None，而不是伪造进度
    assert progress.snapshot("unknown") is None
    progress.clear()


def test_profile_endpoint() -> None:
    _fresh()
    c = _client()
    j = c.get("/api/profile?owner=u").get_json()
    assert j["profile"] == {}

    from assistant_lite.agents.fitness import profile as P

    P.save("u", {"age": 30, "goal": "增肌", "injuries": ["膝盖"]})
    j = c.get("/api/profile?owner=u").get_json()
    assert j["profile"]["age"] == 30
    assert "年龄" in j["summary"]
    assert "膝盖" in j["risk"]
    # 字段定义也要返回，前端据此渲染
    assert any(f["key"] == "goal" for f in j["fields"])


def test_resource_endpoint_and_isolation() -> None:
    _fresh()
    c = _client()
    rid = session.archive("u", "meeting", "会议纪要", "# 标题\n\n正文内容")
    r = c.get("/api/resource?owner=u&id=" + rid)
    assert r.status_code == 200
    assert "正文内容" in r.get_json()["content"]

    assert c.get("/api/resource?owner=u&id=nope").status_code == 404
    # 跨 owner 取不到
    assert c.get("/api/resource?owner=other&id=" + rid).status_code == 404
    # 不带 id
    assert c.get("/api/resource?owner=u").status_code == 404


def test_reset_endpoint_clears_session() -> None:
    _fresh()
    c = _client()
    c.post("/api/chat", data={"text": "开始会议记录", "session_id": "s1", "owner": "u"})
    assert c.get("/api/state?owner=u&session_id=s1").get_json()["meeting"]["status"] == "collecting"

    r = c.post("/api/reset", data={"owner": "u", "session_id": "s1"})
    assert r.status_code == 200
    st = c.get("/api/state?owner=u&session_id=s1").get_json()
    assert st["meeting"]["status"] == "idle"
    assert st["meeting"]["transcript"] == ""


def test_static_assets_served() -> None:
    _fresh()
    c = _client()
    css = c.get("/static/app.css")
    js = c.get("/static/app.js")
    assert css.status_code == 200, "app.css 应可访问"
    assert b"--accent" in css.data
    assert js.status_code == 200, "app.js 应可访问"
    assert b"Glasses Companion" in js.data


def test_page_has_no_leftover_placeholders() -> None:
    """页面不应残留未替换的模板占位符或 TODO。"""
    _fresh()
    c = _client()
    body = c.get("/").get_data(as_text=True)
    for bad in ("{{", "}}", "TODO", "FIXME", "undefined"):
        assert bad not in body, f"页面残留 {bad!r}"


def test_chat_accepts_structured_event() -> None:
    """前端提交的 event 必须被采纳——这是眼镜按键/语音事件的入口。

    回归：/api/chat 曾经只读 text/scene，把 event 整个丢掉，
    导致 set_done 这类不带关键词的事件只能靠 LLM 兜底路由。
    """
    _fresh()
    c = _client()
    c.post("/api/chat", data={
        "text": "开始深蹲", "session_id": "s1", "owner": "u",
        "event": '{"semantic_action": "start_exercise"}',
    })
    # 文本本身不含任何关键词，只能靠 event 路由
    r = c.post("/api/chat", data={
        "text": "嗯", "session_id": "s1", "owner": "u",
        "event": '{"semantic_action": "set_done"}',
    })
    j = r.get_json()
    assert j["scene"] == "fitness", j
    assert j["action"] == "set_done", j
    assert "第 1 组" in j["text"], j["text"]


def test_chat_rejects_bad_event_json() -> None:
    _fresh()
    c = _client()
    r = c.post("/api/chat", data={"text": "开始深蹲", "session_id": "s1", "event": "{oops"})
    assert r.status_code == 400
    assert "event" in r.get_json()["error"]


def test_static_assets_not_cached() -> None:
    """本地工具改了 app.js/app.css 要能立刻生效，不能被浏览器缓存住。"""
    _fresh()
    c = _client()
    r = c.get("/static/app.js")
    assert r.status_code == 200
    cc = (r.headers.get("Cache-Control") or "").lower()
    assert "no-cache" in cc or "max-age=0" in cc or "no-store" in cc, cc


def test_async_chat_returns_task_and_result() -> None:
    """异步接口：提交后立刻拿 task_id，结果用 /api/task 轮询。"""
    import time

    _fresh()
    c = _client()
    r = c.post(
        "/api/chat/async",
        data={"text": "开始会议记录", "session_id": "s1", "owner": "u"},
    )
    assert r.status_code == 202, r.get_data(as_text=True)
    j = r.get_json()
    assert j["task_id"], j
    assert j["request_id"], j
    tid = j["task_id"]

    rec = None
    for _ in range(80):
        rec = c.get("/api/task?task_id=" + tid).get_json()
        if rec["status"] in ("done", "error"):
            break
        time.sleep(0.1)

    assert rec["status"] == "done", rec
    assert rec["result"]["scene"] == "meeting", rec
    assert rec["result"]["speech"], "异步结果也要带播报语"


def test_async_task_unknown_id() -> None:
    _fresh()
    c = _client()
    r = c.get("/api/task?task_id=nope")
    assert r.status_code == 404
    assert "找不到" in r.get_json()["error"]


def test_async_rejects_bad_input_like_sync() -> None:
    _fresh()
    c = _client()
    assert c.post("/api/chat/async", data={"text": "   ", "session_id": "s1"}).status_code == 400
    assert (
        c.post("/api/chat/async", data={"text": "x", "session_id": "s1", "event": "{oops"}).status_code
        == 400
    )


def test_health_reports_task_stats() -> None:
    _fresh()
    c = _client()
    j = c.get("/health").get_json()
    assert "tasks" in j
    for key in ("pending", "running", "done", "error"):
        assert key in j["tasks"]


def test_tasks_module_lifecycle() -> None:
    from assistant_lite import tasks

    tasks.clear()
    tid = tasks.submit(lambda: {"ok": 1})
    assert tasks.get(tid)["status"] in ("pending", "running")

    import time

    for _ in range(50):
        rec = tasks.get(tid)
        if rec["status"] == "done":
            break
        time.sleep(0.05)
    assert rec["status"] == "done"
    assert rec["result"] == {"ok": 1}

    # 任务内部异常要落到记录里，不能凭空消失
    bad = tasks.submit(lambda: (_ for _ in ()).throw(ValueError("boom")))
    for _ in range(50):
        rec = tasks.get(bad)
        if rec["status"] == "error":
            break
        time.sleep(0.05)
    assert rec["status"] == "error"
    assert "boom" in rec["error"]

    assert tasks.get("不存在") is None
    tasks.clear()


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
