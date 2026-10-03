"""Backup uses disposable databases and never touches the application's data."""
import importlib.util
from pathlib import Path
import sqlite3
import tarfile
import threading

import pytest

ROOT = Path(__file__).resolve().parents[2]


def load_backup():
    spec = importlib.util.spec_from_file_location("assistant_backup", ROOT / "deploy/backup_data.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.backup


def load_online_check():
    spec = importlib.util.spec_from_file_location("assistant_check_online", ROOT / "deploy/check_online.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.check


@pytest.fixture
def original_site():
    from flask import Flask, jsonify, render_template
    from lg_assistant.web import auth
    from werkzeug.serving import make_server

    web = ROOT / "langgraph-app/lg_assistant/web"
    app = Flask(__name__, template_folder=str(web / "templates"), static_folder=str(web / "static"))
    token = "local-online-check-test-token"
    auth.install(app, token)

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/health")
    def health():
        return jsonify({"ok": True, "auth_enabled": True, "active_tasks": 0})

    @app.get("/api/resources")
    def resources():
        return jsonify({"resources": []})

    @app.get("/api/tasks")
    def tasks():
        return jsonify({"tasks": []})

    server = make_server("127.0.0.1", 0, app)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", token, app
    finally:
        server.shutdown()
        worker.join(timeout=5)


def test_online_check_accepts_original_workbench_and_real_static_assets(original_site):
    url, token, _ = original_site
    load_online_check()(url, token, allow_http=True)


def test_online_check_rejects_missing_ring_asset(original_site):
    from flask import request

    url, token, app = original_site

    @app.before_request
    def unavailable_ring():
        if request.path == "/static/smart-ring.js":
            return "missing ring", 404

    with pytest.raises(RuntimeError, match="static asset"):
        load_online_check()(url, token, allow_http=True)


def test_backup_preserves_records_files_and_original(tmp_path):
    data = tmp_path / "data"
    (data / "uploads").mkdir(parents=True)
    (data / "uploads/photo.jpg").write_bytes(b"synthetic-photo")
    database = data / "app.sqlite3"
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE example(value TEXT)")
        db.execute("INSERT INTO example VALUES('retained')")
    output = tmp_path / "private/backup.tar.gz"
    load_backup()(data, output)
    with tarfile.open(output) as archive:
        assert set(archive.getnames()) == {"app.sqlite3", "uploads/photo.jpg"}
        assert archive.extractfile("uploads/photo.jpg").read() == b"synthetic-photo"
        restored = tmp_path / "restored.sqlite3"
        restored.write_bytes(archive.extractfile("app.sqlite3").read())
    with sqlite3.connect(restored) as db:
        assert db.execute("SELECT value FROM example").fetchone()[0] == "retained"
    assert database.is_file() and (data / "uploads/photo.jpg").read_bytes() == b"synthetic-photo"
    with pytest.raises(ValueError, match="不覆盖"):
        load_backup()(data, output)
    with pytest.raises(ValueError, match="之外"):
        load_backup()(data, data / "backup.tar.gz")
