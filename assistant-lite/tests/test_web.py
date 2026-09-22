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
    assert set(j["scenes"]) == {"meeting", "exam", "fitness", "general"}
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
