"""The original workbench remains the default even with a Next.js export."""
from pathlib import Path
import sys
import threading

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lg_assistant import config, graph, store
from lg_assistant.web.app import create_app

TOKEN = "test-only-access-token-0123456789"


@pytest.fixture
def setup_app(tmp_path, monkeypatch):
    for name, value in {
        "DATA_DIR": tmp_path / "data", "DB_PATH": tmp_path / "data/app.sqlite3",
        "CHECKPOINT_DB": tmp_path / "data/checkpoints.sqlite3",
        "UPLOAD_DIR": tmp_path / "data/uploads", "EXPORT_DIR": tmp_path / "data/exports",
        "ACCESS_TOKEN": TOKEN,
    }.items():
        monkeypatch.setattr(config, name, value)
    monkeypatch.setattr(store, "_local", threading.local())
    monkeypatch.setattr(store, "_initialised", False)
    front = tmp_path / "frontend"
    (front / "_next/static").mkdir(parents=True)
    (front / "index.html").write_text("<main>眼镜视野 Next.js</main>", encoding="utf-8")
    (front / "login.html").write_text("<main>输入访问口令 Next.js</main>", encoding="utf-8")
    (front / "_next/static/test.js").write_text("window.frontendReady=true", encoding="utf-8")
    (tmp_path / "private.txt").write_text("private-test-content")
    monkeypatch.setenv("FRONTEND_DIR", str(front))
    app = create_app(graph.build_graph(None), secure_cookie=True)
    app.config["TESTING"] = True
    return app.test_client(), front


def test_json_session_login_logout_cookie(setup_app):
    c, _ = setup_app
    r = c.get("/api/session")
    assert r.status_code == 200
    assert r.json == {"authenticated": False, "auth_enabled": True}
    assert "no-store" in r.headers["Cache-Control"]
    denied = c.post("/api/login", json={"token": "wrong"})
    assert denied.status_code == 401
    assert "Set-Cookie" not in denied.headers
    accepted = c.post("/api/login", json={"token": TOKEN}, base_url="https://localhost")
    assert accepted.status_code == 200
    assert accepted.json["authenticated"]
    cookie = accepted.headers["Set-Cookie"]
    assert "HttpOnly" in cookie and "Secure" in cookie and "SameSite=Lax" in cookie
    assert TOKEN not in accepted.get_data(as_text=True)
    assert c.get("/api/session", base_url="https://localhost").json["authenticated"]
    assert c.post("/api/logout", base_url="https://localhost").status_code == 200
    assert c.get("/api/resources", base_url="https://localhost").status_code == 401


@pytest.mark.parametrize("payload", [None, [], {"token": 123}, {"token": ["wrong"]}])
def test_json_login_bad_types_do_not_crash(setup_app, payload):
    c, _ = setup_app
    assert c.post("/api/login", json=payload).status_code in (400, 401)


def test_unicode_wrong_password_is_rejected_without_server_error(setup_app):
    c, _ = setup_app
    assert c.post("/api/login", json={"token": "错误的访问口令"}).status_code == 401
    assert c.post("/login", data={"token": "错误的访问口令"}).status_code == 401
    assert c.get("/api/tasks", headers={"Authorization": "Bearer 错误的访问口令"}).status_code == 401


def test_original_login_remains_available_with_exported_frontend(setup_app):
    c, _ = setup_app
    assert c.get("/").status_code == 302
    login = c.get("/login")
    assert login.status_code == 200
    assert '<form class="card" method="post" action="/login">' in login.get_data(as_text=True)
    assert "Next.js" not in login.get_data(as_text=True)
    accepted = c.post("/login", data={"token": TOKEN}, base_url="https://localhost")
    assert accepted.status_code == 302 and accepted.headers["Location"] == "/"
    assert "HttpOnly" in accepted.headers["Set-Cookie"] and "Secure" in accepted.headers["Set-Cookie"]


def test_original_workbench_is_default_with_exported_frontend(setup_app):
    c, _ = setup_app
    asset = c.get("/_next/static/test.js")
    assert asset.status_code == 200
    assert "immutable" in asset.headers["Cache-Control"]
    c.post("/api/login", json={"token": TOKEN}, base_url="https://localhost")
    index = c.get("/", base_url="https://localhost")
    assert index.status_code == 200
    body = index.get_data(as_text=True)
    assert 'id="hardware-demo"' in body
    assert '/static/hardware.js' in body and '/static/smart-ring.js' in body
    assert "Next.js" not in body
    assert "no-store" in index.headers["Cache-Control"]
    for path in ("/static/hardware.js", "/static/smart-ring.js", "/static/hardware.css"):
        assert c.get(path, base_url="https://localhost").status_code == 200
    legacy = c.get("/legacy", base_url="https://localhost")
    assert legacy.status_code == 200 and legacy.get_data(as_text=True) == body


def test_asset_path_cannot_read_outside_build(setup_app):
    c, _ = setup_app
    for path in ("/_next/../../private.txt", "/_next/%2e%2e/%2e%2e/private.txt", "/_next/missing.js"):
        r = c.get(path)
        assert r.status_code == 404
        assert "private-test-content" not in r.get_data(as_text=True)


def test_missing_explicit_build_is_an_actionable_startup_error(tmp_path, monkeypatch):
    monkeypatch.setenv("FRONTEND_DIR", str(tmp_path / "missing"))
    from lg_assistant.web.frontend import build_directory
    with pytest.raises(RuntimeError, match="FRONTEND_DIR"):
        build_directory()


def test_request_too_large_is_json_for_frontend(setup_app):
    c, _ = setup_app
    c.post("/api/login", json={"token": TOKEN}, base_url="https://localhost")
    c.application.config["MAX_CONTENT_LENGTH"] = 10
    r = c.post("/api/chat/async", data={"text": "too-long-for-test"}, base_url="https://localhost")
    assert r.status_code == 413
    assert r.is_json and r.json["error"]
