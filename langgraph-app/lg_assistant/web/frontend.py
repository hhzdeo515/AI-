"""Serve a Next.js static export alongside the Python API on one origin."""
from __future__ import annotations

import os
from pathlib import Path

from flask import current_app, send_file, send_from_directory


def build_directory() -> Path | None:
    raw = os.getenv("FRONTEND_DIR", "").strip()
    if not raw:
        return None
    directory = Path(raw).resolve()
    if not (directory / "index.html").is_file():
        raise RuntimeError("FRONTEND_DIR 缺少 index.html，请先在 frontend 运行 npm ci 和 npm run build。")
    return directory


def page(name: str):
    directory = current_app.extensions.get("frontend_directory")
    if not directory:
        return None
    for candidate in (directory / (name + ".html"), directory / name / "index.html"):
        if candidate.is_file():
            response = send_file(candidate)
            response.headers["Cache-Control"] = "no-store"
            return response
    return None


def install(app) -> None:
    directory = build_directory()
    app.extensions["frontend_directory"] = directory

    @app.get("/_next/<path:filename>")
    def next_asset(filename):
        if directory is None:
            from flask import abort
            abort(404)
        response = send_from_directory(directory / "_next", filename)
        if filename.startswith("static/"):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response

    @app.after_request
    def private_responses(response):
        from flask import request
        if request.path.startswith("/api/") or request.path in {"/login", "/logout", "/", "/legacy"}:
            response.headers["Cache-Control"] = "no-store"
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        return response

    @app.errorhandler(413)
    def upload_too_large(_error):
        from flask import jsonify
        return jsonify({"error": "文件超过上传大小限制，请压缩或拆分后再上传。", "code": "upload_too_large"}), 413
