"""Authenticated original-media access by task ID, never by client file path."""
from __future__ import annotations

from pathlib import Path
from urllib.parse import urlencode

from flask import abort, current_app, request, send_file
from .. import config

IMAGES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff"}
AUDIO = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".amr", ".wma", ".webm"}


def _files(task_id: str, owner: str) -> list[str]:
    manager = current_app.extensions.get("task_manager")
    payload = manager.get_payload(task_id, owner) if manager else None
    return payload.get("files", []) if isinstance(payload, dict) else []


def _safe_path(value: str) -> Path | None:
    root = current_app.extensions["media_upload_root"]
    try:
        path = Path(value).resolve(strict=True)
        if path.is_relative_to(root) and path.is_file() and path.suffix.lower() in IMAGES | AUDIO:
            return path
    except (ValueError, TypeError, OSError):
        pass
    return None


def describe(task_id: str, owner: str = "local") -> list[dict]:
    items = []
    for index, value in enumerate(_files(task_id, owner)):
        path = _safe_path(value)
        if path:
            kind = "image" if path.suffix.lower() in IMAGES else "audio"
            items.append({"index": index, "type": kind,
                          "name": ("照片" if kind == "image" else "录音") + str(index + 1) + path.suffix.lower(),
                          "url": "/api/task/media?" + urlencode({"task_id": task_id, "owner": owner, "index": index})})
    return items


def install(app):
    app.extensions["media_upload_root"] = config.UPLOAD_DIR.resolve()

    @app.get("/api/task/media")
    def task_media():
        task_id = (request.args.get("task_id") or "").strip()
        owner = (request.args.get("owner") or "local").strip() or "local"
        try:
            index = int(request.args.get("index", "0"))
        except ValueError:
            abort(404)
        files = _files(task_id, owner)
        if index < 0 or index >= len(files):
            abort(404)
        path = _safe_path(files[index])
        if path is None:
            abort(404)
        response = send_file(path, conditional=True)
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response
