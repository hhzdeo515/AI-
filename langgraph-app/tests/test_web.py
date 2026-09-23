"""Web 层测试：Flask test client + 桩图，不需要 API Key、不联网、不起端口。

策略与基线一致：需要模型的路径一律打桩；用 `test_client` 比真起服务再 curl 可靠得多。
另外全图只编译一次并复用（create_app 默认会自行编译带 checkpointer 的图，
每个测试都编译会拖慢且互相污染检查点）。
"""

from __future__ import annotations

import io
import json
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lg_assistant import config, graph, llm, nodes, store  # noqa: E402

_APP = None


def _fresh() -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="lgweb-"))
    config.DATA_DIR = tmp
    config.DB_PATH = tmp / "app.sqlite3"
    config.CHECKPOINT_DB = tmp / "ck.sqlite3"
    config.EXPORT_DIR = tmp / "exports"
    config.UPLOAD_DIR = tmp / "uploads"
    store._local = threading.local()
    store._initialised = False
    return tmp


_ORIGINAL_TOKEN: str | None = None


def _client(tmp: Path | None = None):
    """每个测试一套独立的库与图。

    **显式关闭鉴权**：本文件测的是业务接口，不是鉴权（那是 test_auth.py 的事）。
    `.env` 里设了 ACCESS_TOKEN 时，不关就会所有请求都被重定向到登录页，
    表现为大面积莫名其妙的失败（实测：27 项里只剩 2 项通过）。

    **必须带 checkpointer**：``/api/state`` 与 ``/api/resume`` 依赖它，
    无检查点的图调 ``get_state`` 会直接抛 ``No checkpointer set``。
    """
    global _ORIGINAL_TOKEN
    from lg_assistant.web.app import create_app

    _ORIGINAL_TOKEN = config.ACCESS_TOKEN
    config.ACCESS_TOKEN = ""

    cp = graph.open_checkpointer((tmp or config.DATA_DIR) / "ck.sqlite3")
    app = create_app(graph.build_graph(cp))
    app.config["TESTING"] = True
    return app.test_client()


def _restore_token() -> None:
    global _ORIGINAL_TOKEN
    if _ORIGINAL_TOKEN is not None:
        config.ACCESS_TOKEN = _ORIGINAL_TOKEN


def _stub_llm(text: str = "桩回复"):
    original = llm.chat
    llm.chat = lambda messages, **kw: text
    original_router = nodes._ROUTER
    nodes.set_router(None)
    return lambda: (setattr(llm, "chat", original), nodes.set_router(original_router))


def _png(name: str = "q.png"):
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (8, 8), "white").save(buf, format="PNG")
    buf.seek(0)
    return (buf, name)


# --------------------------------------------------------------------------- #
# 页面与健康检查
# --------------------------------------------------------------------------- #
def test_index_renders_reused_frontend() -> None:
    _fresh()
    r = _client().get("/")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "assistant-lite" in body or "Companion" in body
    # 复用的前端要能区分三个场景
    assert "会议纪要" in body and "拍照解题" in body and "锻炼指导" in body


def test_static_assets_served_and_not_cached() -> None:
    _fresh()
    c = _client()
    for name in ("app.css", "app.js"):
        r = c.get(f"/static/{name}")
        assert r.status_code == 200, name
        assert "no-cache" in r.headers.get("Cache-Control", "") or r.headers.get(
            "Cache-Control"
        ) in ("no-store", "no-cache"), f"{name} 不应被浏览器缓存"


def test_health_reports_engine() -> None:
    _fresh()
    body = _client().get("/health").get_json()
    assert body["ok"] is True
    assert body["engine"] == "langgraph"
    assert "meeting" in body["scenes"]


# --------------------------------------------------------------------------- #
# 对话
# --------------------------------------------------------------------------- #
def test_chat_runs_graph_and_returns_payload() -> None:
    _fresh()
    c = _client()
    r = c.post("/api/chat", data={"text": "计算 (18+24)*3", "owner": "u", "session_id": "s"})
    assert r.status_code == 200
    body = r.get_json()
    assert body["scene"] == "exam"
    assert body["source"] == "keyword"
    assert "126" in body["text"]
    assert body["speech"], "播报语必须随响应返回（眼镜端要用）"


def test_chat_empty_input_rejected() -> None:
    _fresh()
    r = _client().post("/api/chat", data={"text": "", "owner": "u"})
    assert r.status_code == 400
    assert "error" in r.get_json()


def test_chat_rejects_bad_event_json() -> None:
    _fresh()
    r = _client().post("/api/chat", data={"text": "x", "event": "{oops"})
    assert r.status_code == 400
    assert "event" in r.get_json()["error"]


def test_chat_accepts_structured_event() -> None:
    """端侧事件是眼镜的主入口，必须被采纳。"""
    _fresh()
    r = _client().post(
        "/api/chat",
        data={
            "text": "停止",
            "session_id": "s1",
            "event": json.dumps({"semantic_action": "stop_playback"}),
        },
    )
    body = r.get_json()
    assert body["action"] == "stop_playback"
    assert body["source"] == "event"


def test_chat_is_idempotent_by_request_id() -> None:
    """断连补传的地基：同 request_id 重放不得重复执行。"""
    _fresh()
    c = _client()
    data = {"text": "计算 1+1", "owner": "u", "session_id": "s", "request_id": "dup1"}
    first = c.post("/api/chat", data=data).get_json()
    second = c.post("/api/chat", data=data).get_json()
    assert first["status"] == "ok"
    assert second["status"] == "duplicate", second
    assert second["text"] == first["text"]


def test_chat_scene_hint_wins() -> None:
    _fresh()
    restore = _stub_llm("健身回答")
    try:
        r = _client().post(
            "/api/chat", data={"text": "这道题怎么做", "scene": "fitness", "owner": "u"}
        )
    finally:
        restore()
    assert r.get_json()["scene"] == "fitness"


# --------------------------------------------------------------------------- #
# 附件
# --------------------------------------------------------------------------- #
def test_upload_image_saved_for_vision() -> None:
    _fresh()
    restore = _stub_llm("桩")
    try:
        r = _client().post(
            "/api/chat",
            data={"text": "解这道题", "owner": "u", "files": _png()},
            content_type="multipart/form-data",
        )
    finally:
        restore()
    assert r.status_code == 200
    assert r.get_json()["scene"] == "exam"
    saved = list(config.UPLOAD_DIR.glob("*.png"))
    assert saved, "上传的图片必须落盘（视觉链要读本地文件）"


def test_upload_bad_extension_rejected() -> None:
    _fresh()
    r = _client().post(
        "/api/chat",
        data={"text": "x", "files": (io.BytesIO(b"MZ"), "evil.exe")},
        content_type="multipart/form-data",
    )
    assert r.status_code == 200
    assert r.get_json()["rejected_files"], "非法扩展名必须被拒绝并说明"


# --------------------------------------------------------------------------- #
# 异步
# --------------------------------------------------------------------------- #
def test_async_chat_returns_task_and_result() -> None:
    _fresh()
    c = _client()
    started = c.post("/api/chat/async", data={"text": "计算 2+2", "owner": "u", "session_id": "s"})
    assert started.status_code == 202
    tid = started.get_json()["task_id"]

    import time

    for _ in range(100):
        rec = c.get(f"/api/task?task_id={tid}").get_json()
        if rec["status"] in ("done", "error"):
            break
        time.sleep(0.05)
    assert rec["status"] == "done", rec
    assert "4" in rec["result"]["text"]


def test_async_unknown_task_404() -> None:
    _fresh()
    assert _client().get("/api/task?task_id=nope").status_code == 404


def test_async_rejects_bad_input_like_sync() -> None:
    _fresh()
    r = _client().post("/api/chat/async", data={"text": "", "event": "{oops"})
    assert r.status_code == 400


# --------------------------------------------------------------------------- #
# 状态 / 续跑 / 判对率
# --------------------------------------------------------------------------- #
def test_state_endpoint_reports_thread() -> None:
    _fresh()
    c = _client()
    c.post("/api/chat", data={"text": "计算 1+1", "owner": "u", "session_id": "s"})
    body = c.get("/api/state?owner=u&session_id=s").get_json()
    assert body["thread_id"] == "u:s"
    assert body["has_checkpoint"] is True


def test_resume_404_without_checkpoint() -> None:
    _fresh()
    assert _client().get("/api/resume?owner=u&session_id=none").status_code == 404


def test_resume_reports_already_finished() -> None:
    _fresh()
    c = _client()
    c.post("/api/chat", data={"text": "计算 1+1", "owner": "u", "session_id": "s"})
    body = c.get("/api/resume?owner=u&session_id=s").get_json()
    assert body["resumed"] is False
    assert body["reason"]


def test_routing_stats_endpoint() -> None:
    _fresh()
    c = _client()
    c.post(
        "/api/chat",
        data={
            "text": "我膝盖有点疼，练不下去了",
            "owner": "u",
            "session_id": "s",
            "event": json.dumps({"device": {"intent": "solve", "confidence": 0.95}}),
        },
    )
    body = c.get("/api/routing-stats?owner=u").get_json()
    assert body["samples"] == 1, body
    assert body["device_correct"] == 0, body
    assert body["accuracy"] == 0.0


# --------------------------------------------------------------------------- #
# 资料与导出
# --------------------------------------------------------------------------- #
def test_resources_and_resource_detail() -> None:
    _fresh()
    c = _client()
    restore = _stub_llm("会议纪要正文")
    try:
        c.post("/api/chat", data={"text": "帮我生成会议纪要", "owner": "u", "session_id": "s"})
    finally:
        restore()

    rows = c.get("/api/resources?owner=u").get_json()["resources"]
    assert rows, "会议产出应归档"
    rid = rows[0]["id"]

    detail = c.get(f"/api/resource?owner=u&id={rid}")
    assert detail.status_code == 200
    assert "会议纪要正文" in detail.get_json()["content"]

    assert c.get("/api/resource?owner=u&id=nope").status_code == 404
    # 跨 owner 必须拿不到
    assert c.get(f"/api/resource?owner=other&id={rid}").status_code == 404


def test_export_all_six_formats() -> None:
    _fresh()
    c = _client()
    store.archive("u", "meeting", "测试纪要", "## 主题\n内容", "src")
    for fmt in ("md", "txt", "json", "csv", "docx", "pdf"):
        r = c.get(f"/api/export?owner=u&format={fmt}")
        assert r.status_code == 200, (fmt, r.status_code)
        assert len(r.data) > 0, fmt


def test_export_bad_format_rejected() -> None:
    _fresh()
    c = _client()
    store.archive("u", "meeting", "t", "c", "")
    assert c.get("/api/export?owner=u&format=exe").status_code == 400


def test_export_without_resources_rejected() -> None:
    _fresh()
    assert _client().get("/api/export?owner=nobody").status_code == 400


# --------------------------------------------------------------------------- #
# 语音与进度
# --------------------------------------------------------------------------- #
def test_speak_rejects_empty_and_too_long() -> None:
    _fresh()
    c = _client()
    assert c.get("/api/speak?text=").status_code == 400
    assert c.get("/api/speak?text=" + "长" * 400).status_code == 400


def test_speak_reports_tts_failure() -> None:
    """TTS 失败要显式报 502，不能返回空音频让设备静默。"""
    _fresh()
    original = llm.tts
    llm.tts = lambda *a, **kw: (_ for _ in ()).throw(llm.LLMError("模拟合成失败"))
    try:
        r = _client().get("/api/speak?text=你好")
    finally:
        llm.tts = original
    assert r.status_code == 502
    assert "模拟合成失败" in r.get_json()["error"]


def test_speak_returns_audio() -> None:
    _fresh()
    original = llm.tts
    llm.tts = lambda *a, **kw: b"ID3fake-mp3-bytes"
    try:
        r = _client().get("/api/speak?text=你好")
    finally:
        llm.tts = original
    assert r.status_code == 200
    assert r.headers["Content-Type"] == "audio/mpeg"
    assert r.data == b"ID3fake-mp3-bytes"


def test_progress_endpoint_without_record() -> None:
    """没有进度记录时必须返回空 steps，前端据此回退，不编造阶段。"""
    _fresh()
    body = _client().get("/api/progress?request_id=nope").get_json()
    assert body["steps"] == []
    assert body["finished"] is True


def test_profile_empty_is_honest_not_invented() -> None:
    """没建档时返回空档案，但**字段清单要给全** —— 前端靠它画表单。"""
    _fresh()
    body = _client().get("/api/profile").get_json()
    assert body["profile"] == {}
    assert body["complete"] is False
    assert body["note"], "没建档要说明"
    # fields 是服务端下发的，前端不硬编码字段
    keys = [f["key"] for f in body["fields"]]
    assert keys == [
        "age", "height_cm", "weight_kg", "goal", "level",
        "injuries", "conditions", "days_per_week", "equipment",
    ]
    assert body["fields"][0]["label"] == "年龄"
    assert body["fields"][3]["options"], "选项类字段要带选项"


def test_profile_write_then_read_back() -> None:
    """手机端提交 → 眼镜端读到同一份。这是「云端存档案」的完整往返。"""
    _fresh()
    client = _client()
    payload = {
        "age": 28, "height_cm": 178, "weight_kg": 70,
        "goal": "减脂", "level": "偶尔运动",
        "injuries": ["膝盖"], "conditions": ["无"],
        "days_per_week": 3, "equipment": ["哑铃", "弹力带"],
    }
    body = client.post(
        "/api/profile", data={"owner": "u", "profile": json.dumps(payload, ensure_ascii=False)}
    ).get_json()
    assert body["complete"] is True
    assert body["missing"] == []
    assert abs(body["bmi"] - 22.1) < 0.05
    assert "膝盖" in body["risk"]
    assert "无" not in body["risk"], "「无」不是风险，不该出现在注意里"
    assert body["meta"]["source"] == "phone"

    # 再读一次：真的落库了，不是只回显
    again = client.get("/api/profile?owner=u").get_json()
    assert again["profile"]["age"] == 28
    assert again["profile"]["height_cm"] == 178.0


def test_profile_write_rejects_bad_values() -> None:
    """校验不通过要 400 并说清哪里错，**不能悄悄存下半成品**。"""
    _fresh()
    client = _client()

    # 年龄超范围
    r = client.post(
        "/api/profile",
        data={"owner": "u", "profile": json.dumps({"age": 999}, ensure_ascii=False)},
    )
    assert r.status_code == 400
    assert "年龄" in r.get_json()["error"]

    # 缺字段（默认要求齐全）
    r = client.post(
        "/api/profile",
        data={"owner": "u", "profile": json.dumps({"age": 30}, ensure_ascii=False)},
    )
    assert r.status_code == 400
    assert "还缺" in r.get_json()["error"]

    # 非法 JSON
    r = client.post("/api/profile", data={"owner": "u", "profile": "{不是json"})
    assert r.status_code == 400

    assert client.get("/api/profile?owner=u").get_json()["profile"] == {}


def test_profile_partial_draft_allowed_when_asked() -> None:
    """``partial=1`` 才允许存草稿 —— 默认严格，免得用户以为存上了。"""
    _fresh()
    client = _client()
    r = client.post(
        "/api/profile",
        data={"owner": "u", "profile": json.dumps({"age": 30}), "partial": "1"},
    )
    assert r.status_code == 200
    body = r.get_json()
    assert body["profile"]["age"] == 30
    assert body["complete"] is False
    assert "height_cm" in body["missing"]


def test_profile_delete() -> None:
    """删档案要真的删掉，且删不存在的档案不报错。"""
    _fresh()
    client = _client()
    client.post(
        "/api/profile",
        data={"owner": "u", "profile": json.dumps({"age": 30}), "partial": "1"},
    )
    assert client.delete("/api/profile?owner=u").get_json()["ok"] is True
    assert client.get("/api/profile?owner=u").get_json()["profile"] == {}
    assert client.delete("/api/profile?owner=u").get_json()["ok"] is False


def test_profile_is_isolated_per_owner() -> None:
    """档案按 owner 隔离：别人的档案一个字都不该看到。"""
    _fresh()
    client = _client()
    client.post(
        "/api/profile",
        data={
            "owner": "alice",
            "profile": json.dumps({"age": 30, "height_cm": 165}, ensure_ascii=False),
            "partial": "1",
        },
    )
    assert client.get("/api/profile?owner=bob").get_json()["profile"] == {}
    assert client.get("/api/profile?owner=alice").get_json()["profile"]["age"] == 30


def test_state_exposes_meeting_and_fitness_on_top_level() -> None:
    """/api/state 必须在顶层给出 meeting / fitness。

    前端是从 assistant-lite 原样复用的，状态卡片直接读 ``st.fitness``；
    数据只塞在 ``values`` 里的话卡片读不到，WORKOUT 会恒显示 IDLE。
    """
    _fresh()
    body = _client().get("/api/state?owner=u&session_id=s").get_json()
    assert "values" in body
    assert "meeting" in body, "顶层缺 meeting，基线 UI 的卡片读不到"
    assert "fitness" in body, "顶层缺 fitness，WORKOUT 卡片会恒为 IDLE"
    assert isinstance(body["fitness"], dict)
    assert isinstance(body["meeting"], dict)


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
        finally:
            _restore_token()
    print(f"\n{len(tests) - failed}/{len(tests)} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass
    raise SystemExit(_run_all())
