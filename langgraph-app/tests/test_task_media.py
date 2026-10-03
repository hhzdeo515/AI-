import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from flask import Flask
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lg_assistant import config
from lg_assistant.web import auth


@pytest.fixture
def client(tmp_path, monkeypatch):
    from lg_assistant.web import media
    upload = tmp_path / "uploads"
    upload.mkdir()
    photo = upload / "photo.png"
    photo.write_bytes(b"test-image-bytes")
    private = tmp_path / "private.png"
    private.write_bytes(b"private")
    monkeypatch.setattr(config, "UPLOAD_DIR", upload)
    manager = SimpleNamespace(get_payload=Mock(side_effect=lambda tid, owner: {
        "ok": {"files": [str(photo)]}, "outside": {"files": [str(private)]},
    }.get(tid) if owner == "local" else None))
    app = Flask(__name__)
    app.extensions["task_manager"] = manager
    auth.install(app, "test-token")
    media.install(app)
    return app.test_client()


def test_media_requires_login_and_owner(client):
    path = "/api/task/media?task_id=ok&index=0"
    assert client.get(path).status_code == 401
    headers = {"Authorization": "Bearer test-token"}
    assert client.get(path, headers=headers).data == b"test-image-bytes"
    assert client.get(path + "&owner=other", headers=headers).status_code == 404


def test_media_bounds_and_filesystem_escape(client):
    headers = {"Authorization": "Bearer test-token"}
    for query in ("task_id=outside&index=0", "task_id=ok&index=-1", "task_id=ok&index=9",
                  "task_id=ok&index=bad", "task_id=missing&index=0"):
        assert client.get("/api/task/media?" + query, headers=headers).status_code == 404


def test_media_metadata_only_contains_authenticated_urls(client):
    from lg_assistant.web import media
    with client.application.test_request_context():
        items = media.describe("ok")
        assert len(items) == 1
        assert items[0]["type"] == "image" and items[0]["index"] == 0
        assert items[0]["url"].startswith("/api/task/media?")
        assert "uploads" not in str(items)
        assert media.describe("outside") == []
