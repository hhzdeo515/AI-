"""本地 Web 页：Flask 服务，支持图片与音频上传。

只绑定 127.0.0.1，不做多租户鉴权——面向单机个人使用。
"""

from __future__ import annotations

import uuid
from pathlib import Path

from flask import Flask, jsonify, render_template, request, send_file

from .. import config, progress, session
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
            }
        )

    @app.post("/api/chat")
    def api_chat():
        owner = (request.form.get("owner") or "local").strip() or "local"
        sid = (request.form.get("session_id") or "web").strip() or "web"
        text = request.form.get("text") or ""
        scene = (request.form.get("scene") or "").strip()
        if scene in ("", "auto"):
            scene = None
        rid = (request.form.get("request_id") or "").strip() or uuid.uuid4().hex[:12]

        files, rejected = _save_uploads(request.files.getlist("files"))
        if not text.strip() and not files:
            return jsonify({"error": "请提供文字，或上传图片/音频"}), 400

        task = Task(
            text=text,
            owner=owner,
            session_id=sid,
            request_id=rid,
            files=files,
            scene_hint=scene,
        )
        try:
            reply = orch.handle(task)
        except Exception as e:  # 兜底，避免把栈回溯吐给前端
            return jsonify({"error": f"{type(e).__name__}: {e}"}), 500

        return jsonify(
            {
                "text": reply.text,
                "scene": reply.scene,
                "action": reply.action,
                "status": reply.status,
                "artifacts": reply.artifacts,
                "rejected_files": rejected,
                "request_id": rid,
            }
        )

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

    @app.get("/api/state")
    def api_state():
        owner = (request.args.get("owner") or "local").strip() or "local"
        sid = (request.args.get("session_id") or "web").strip() or "web"
        return jsonify(session.load_state(owner, sid))

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
