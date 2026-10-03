"""Backup uses disposable databases and never touches the application's data."""
import importlib.util
from pathlib import Path
import sqlite3
import tarfile

import pytest

ROOT = Path(__file__).resolve().parents[2]


def load_backup():
    spec = importlib.util.spec_from_file_location("assistant_backup", ROOT / "deploy/backup_data.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.backup


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
