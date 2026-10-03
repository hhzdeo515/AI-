"""Production startup and authentication use isolated storage and no model calls."""

from __future__ import annotations

import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lg_assistant import config, graph, store  # noqa: E402
from lg_assistant.web.app import create_app  # noqa: E402

TOKEN = "production-test-token-0123456789"


@pytest.fixture(autouse=True)
def isolated_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "app.sqlite3")
    monkeypatch.setattr(config, "CHECKPOINT_DB", tmp_path / "checkpoints.sqlite3")
    monkeypatch.setattr(config, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(config, "EXPORT_DIR", tmp_path / "exports")
    monkeypatch.setattr(config, "DASHSCOPE_API_KEY", "")
    monkeypatch.setattr(config, "TYPESAFE_API_KEY", "")
    monkeypatch.setattr(config, "ACCESS_TOKEN", TOKEN)
    monkeypatch.setattr(store, "_local", threading.local())
    monkeypatch.setattr(store, "_initialised", False)
    monkeypatch.delenv("PUBLIC_SCHEME", raising=False)
    monkeypatch.delenv("PORT", raising=False)
    yield
    connection = getattr(store._local, "conn", None)
    if connection is not None:
        connection.close()


def test_factory_can_issue_secure_login_cookie():
    client = create_app(graph.build_graph(None), secure_cookie=True).test_client()
    response = client.post("/login", data={"token": TOKEN})
    assert response.status_code == 302
    cookie = response.headers["Set-Cookie"]
    assert "; Secure;" in cookie
    assert "; HttpOnly;" in cookie
    assert "SameSite=Lax" in cookie


@pytest.mark.parametrize("token", ["", "  ", "short-secret", "x" * 23])
def test_production_refuses_missing_or_short_access_token(token, monkeypatch, capsys):
    import production

    monkeypatch.setattr(config, "ACCESS_TOKEN", token)
    with pytest.raises(RuntimeError, match="ACCESS_TOKEN") as error:
        production.create_production_app()
    if token.strip():
        assert token not in str(error.value)
        captured = capsys.readouterr()
        assert token not in captured.out + captured.err


def test_production_requires_login_and_secures_cookie_by_default():
    import production

    client = production.create_production_app().test_client()
    assert client.get("/health").status_code == 200
    assert client.get("/").status_code == 302
    assert client.get("/api/resources").status_code == 401
    rejected = client.post("/login", data={"token": "wrong"})
    assert rejected.status_code == 401
    assert "Set-Cookie" not in rejected.headers

    response = client.post("/login", data={"token": TOKEN})
    assert response.status_code == 302
    assert "; Secure;" in response.headers["Set-Cookie"]
    assert client.get("/", base_url="https://localhost").status_code == 200


def test_explicit_http_deployment_allows_local_cookie(monkeypatch):
    import production

    monkeypatch.setenv("PUBLIC_SCHEME", "http")
    response = production.create_production_app().test_client().post("/login", data={"token": TOKEN})
    assert response.status_code == 302
    assert "; Secure;" not in response.headers["Set-Cookie"]


def test_invalid_public_scheme_is_rejected(monkeypatch):
    import production

    monkeypatch.setenv("PUBLIC_SCHEME", "ftp")
    with pytest.raises(ValueError, match="PUBLIC_SCHEME"):
        production.create_production_app()


@pytest.mark.parametrize("port", ["zero", "0", "65536"])
def test_invalid_port_is_rejected_before_server_start(port, monkeypatch):
    import production

    monkeypatch.setenv("PORT", port)
    with pytest.raises(ValueError, match="PORT"):
        production.main()
