"""本地 Web 页：Flask 服务，支持图片与音频上传。

只绑定 127.0.0.1，不做多租户鉴权——面向单机个人使用。
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from flask import Flask, jsonify, make_response, render_template, request, send_file

from .. import config, llm, progress, session, tasks
from ..orchestrator import Orchestrator
from ..schemas import Task
from ..tools import export

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff"}
AUDIO_EXT = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".amr", ".wma"}
ALLOWED_EXT = IMAGE_EXT | AUDIO_EXT

MAX_UPLOAD_MB = 32


def _save_uploads(files) -> tuple[list[str], list[str]]:
    """把上传文件落到 data/uploads/，返回 (本地路径列表, 被拒绝的文件说明)。"""
    saved: list[str] = []
    rejected: list[str] = []
    config.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    for f in files or []:
        if not f or not f.filename:
            continue
        ext = Path(f.filename).suffix.lower()
        if ext not in ALLOWED_EXT:
            rejected.append(f"{f.filename}（不支持的格式 {ext or '无扩展名'}）")
            continue
        dest = config.UPLOAD_DIR / f"{uuid.uuid4().hex}{ext}"
        f.save(dest)
        saved.append(str(dest))
    return saved, rejected


def create_app() -> Flask:
    config.ensure_dirs()
    session.init()

    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024
    app.config["JSON_AS_ASCII"] = False
    # 本地工具：静态资源每次都要回源校验，否则改了 app.js/app.css 浏览器还拿旧的
    app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0
    orch = Orchestrator()

    # ------------------------------------------------------------------ #
    @app.get("/")
    def index():
        return render_template("index.html", scenes=config.SCENES)

    @app.get("/health")
    def health():
        return jsonify(
            {
                "ok": True,
                "scenes": list(config.SCENES),
                "model_text": config.MODEL_TEXT,
                "model_vision": config.MODEL_VISION,
                "model_asr": config.MODEL_ASR,
                "api_key_configured": bool(config.DASHSCOPE_API_KEY),
                "data_dir": str(config.DATA_DIR),
                "tasks": tasks.stats(),
            }
        )

    # ------------------------------------------------------------------ #
    # 对话：同步与异步共用同一套解析与执行，避免两份逻辑漂移
    # ------------------------------------------------------------------ #
    def _parse_chat_form():
        """解析表单。返回 (参数 dict, 错误响应 或 None)。"""
        owner = (request.form.get("owner") or "local").strip() or "local"
        sid = (request.form.get("session_id") or "web").strip() or "web"
        text = request.form.get("text") or ""
        scene = (request.form.get("scene") or "").strip()
        if scene in ("", "auto"):
            scene = None
        rid = (request.form.get("request_id") or "").strip() or uuid.uuid4().hex[:12]

        files, rejected = _save_uploads(request.files.getlist("files"))
        if not text.strip() and not files:
            return None, (jsonify({"error": "请提供文字，或上传图片/音频"}), 400)

        # 结构化事件（模拟眼镜按键/语音）：前端以 JSON 字符串提交
        event: dict = {}
        raw_event = (request.form.get("event") or "").strip()
        if raw_event:
            try:
                parsed = json.loads(raw_event)
                if isinstance(parsed, dict):
                    event = parsed
            except json.JSONDecodeError:
                return None, (jsonify({"error": "event 不是合法 JSON"}), 400)

        return {
            "owner": owner,
            "session_id": sid,
            "text": text,
            "scene": scene,
            "request_id": rid,
            "files": files,
            "event": event,
            "rejected": rejected,
        }, None

    def _run_chat(p: dict) -> dict:
        reply = orch.handle(
            Task(
                text=p["text"],
                owner=p["owner"],
                session_id=p["session_id"],
                request_id=p["request_id"],
                files=p["files"],
                scene_hint=p["scene"],
                event=p["event"],
            )
        )
        return {
            "text": reply.text,
            # 眼镜端播报用：短句。屏幕看 text，耳朵听 speech。
            "speech": reply.speech,
            "scene": reply.scene,
            "action": reply.action,
            "status": reply.status,
            "artifacts": reply.artifacts,
            "rejected_files": p["rejected"],
            "request_id": p["request_id"],
        }

    @app.post("/api/chat")
    def api_chat():
        p, err = _parse_chat_form()
        if err:
            return err
        try:
            return jsonify(_run_chat(p))
        except Exception as e:  # 兜底，避免把栈回溯吐给前端
            return jsonify({"error": f"{type(e).__name__}: {e}"}), 500

    @app.post("/api/chat/async")
    def api_chat_async():
        """提交后立刻返回 task_id，结果用 /api/task 轮询。

        为长耗时场景准备：一次拍题要 ~27 秒（三次视觉调用），
        设备端不能干等。同步接口 /api/chat 保留，供短请求使用。
        """
        p, err = _parse_chat_form()
        if err:
            return err
        tid = tasks.submit(lambda: _run_chat(p))
        return jsonify(
            {"task_id": tid, "request_id": p["request_id"], "status": "pending"}
        ), 202

    @app.get("/api/task")
    def api_task():
        """查询异步任务状态。done 时 result 里是完整的回复载荷。"""
        tid = (request.args.get("task_id") or "").strip()
        rec = tasks.get(tid)
        if not rec:
            return jsonify({"error": "找不到该任务（可能已过期）"}), 404
        return jsonify(rec)

    @app.get("/api/resources")
    def api_resources():
        owner = (request.args.get("owner") or "local").strip() or "local"
        scene = (request.args.get("scene") or "").strip() or None
        if scene not in config.SCENES:
            scene = None
        return jsonify({"resources": session.list_resources(owner, scene, limit=50)})

    @app.get("/api/export")
    def api_export():
        """导出已归档资料。不给 id 则导出最新一份。"""
        owner = (request.args.get("owner") or "local").strip() or "local"
        rid = (request.args.get("id") or "").strip()
        fmt = (request.args.get("format") or "md").strip().lower()
        scene = (request.args.get("scene") or "").strip() or None
        if scene not in config.SCENES:
            scene = None
        try:
            if rid:
                path = export.export_resource(owner, rid, fmt)
            else:
                rows = session.list_full(owner, scene, limit=1)
                path = export.export_rows(rows, fmt)
        except export.ExportError as e:
            return jsonify({"error": str(e)}), 400
        return send_file(
            str(path), as_attachment=True, download_name=f"{path.stem[:8]}.{path.suffix.lstrip('.')}"
        )

    @app.get("/api/speak")
    def api_speak():
        """把播报语合成为音频，供设备端播放。

        响应头 `X-Playback-Generation` 是当前播报代际号：
        设备播放前/播放中比对它，不一致说明已被打断，应丢弃并停播。
        这是「播报打断」的服务端一半——设备端只需实现比对与停播。
        """
        owner = (request.args.get("owner") or "local").strip() or "local"
        sid = (request.args.get("session_id") or "web").strip() or "web"
        text = (request.args.get("text") or "").strip()
        if not text:
            return jsonify({"error": "缺少 text"}), 400
        if len(text) > config.SPEECH_MAX_CHARS:
            return jsonify(
                {"error": f"播报语过长（上限 {config.SPEECH_MAX_CHARS} 字）；"
                          "长内容应走 text，不要念出来"}
            ), 400

        state = session.load_state(owner, sid)
        gen = int((state.get("playback") or {}).get("generation", 0))
        voice = (request.args.get("voice") or "").strip() or None
        try:
            audio = llm.tts(text, voice=voice)
        except llm.LLMError as e:
            return jsonify({"error": str(e)}), 502

        resp = make_response(audio)
        resp.headers["Content-Type"] = "audio/mpeg"
        resp.headers["X-Playback-Generation"] = str(gen)
        resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.get("/api/playback")
    def api_playback():
        """查询当前播报代际号，设备端轮询它判断是否该停播。"""
        owner = (request.args.get("owner") or "local").strip() or "local"
        sid = (request.args.get("session_id") or "web").strip() or "web"
        state = session.load_state(owner, sid)
        return jsonify(
            {"generation": int((state.get("playback") or {}).get("generation", 0))}
        )

    @app.get("/api/state")
    def api_state():
        owner = (request.args.get("owner") or "local").strip() or "local"
        sid = (request.args.get("session_id") or "web").strip() or "web"
        return jsonify(session.load_state(owner, sid))

    @app.get("/api/routing-stats")
    def api_routing_stats():
        """端侧判对率：端侧意图与云端权威路由的一致率（设计文档 §8 指标）。

        路由留在本地才有这张表——这是「方案 B」的直接产出。
        不给 owner 则统计全部。
        """
        owner = (request.args.get("owner") or "").strip() or None
        return jsonify(session.routing_stats(owner))

    @app.get("/api/progress")
    def api_progress():
        """请求级进度快照，供前端的 pipeline 显示真实执行阶段。

        没有记录时返回空 steps —— 前端应回退到"整体处理中"，不要编造阶段。
        """
        rid = (request.args.get("request_id") or "").strip()
        snap = progress.snapshot(rid)
        return jsonify(snap or {"scene": "", "steps": [], "finished": True})

    @app.post("/api/reset")
    def api_reset():
        """清空某个会话的上下文（会议记录、训练状态、建档草稿）。"""
        owner = (request.form.get("owner") or "local").strip() or "local"
        sid = (request.form.get("session_id") or "web").strip() or "web"
        session.save_state(owner, sid, session.default_state())
        return jsonify({"ok": True})

    @app.get("/api/resource")
    def api_resource():
        """取单条资料的完整内容，用于 Memory 详情。"""
        owner = (request.args.get("owner") or "local").strip() or "local"
        rid = (request.args.get("id") or "").strip()
        row = session.get_resource(owner, rid) if rid else None
        if not row:
            return jsonify({"error": "找不到该资料"}), 404
        return jsonify(row)

    @app.get("/api/profile")
    def api_profile():
        """读取健康档案（存在 data/profiles/<owner>.json）。"""
        from ..agents.fitness import profile as prof

        owner = (request.args.get("owner") or "local").strip() or "local"
        data = prof.load(owner)
        return jsonify(
            {
                "profile": data,
                "summary": prof.summarize(data),
                "bmi": prof.bmi(data),
                "risk": prof.risk_notes(data),
                "fields": [
                    {"key": k, "label": label, "kind": kind, "options": list(opts)}
                    for k, label, kind, opts in prof.FIELDS
                ],
            }
        )

    return app


def run(host: str = "127.0.0.1", port: int = 8801) -> None:
    app = create_app()
    print(f"assistant-lite Web 已启动： http://{host}:{port}")
    if not config.DASHSCOPE_API_KEY:
        print("  [警告] 未配置 DASHSCOPE_API_KEY，需要模型的场景会失败。")
    app.run(host=host, port=port, threaded=True)
