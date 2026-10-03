"""Create a non-destructive data backup. Stop the application before running."""
from __future__ import annotations

import argparse
from contextlib import closing
from pathlib import Path
import sqlite3
import tarfile
import tempfile


def backup(data_dir: Path, destination: Path) -> None:
    data_dir, destination = data_dir.resolve(), destination.resolve()
    if not data_dir.is_dir():
        raise ValueError("数据目录不存在")
    if destination.is_relative_to(data_dir):
        raise ValueError("备份必须保存在数据目录之外")
    if destination.exists():
        raise ValueError("备份文件已存在，不覆盖已有备份")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="assistant-backup-") as temp:
        staged = Path(temp)
        with tarfile.open(destination, "x:gz") as archive:
            for path in sorted(data_dir.rglob("*")):
                if not path.is_file() or path.is_symlink():
                    continue
                relative = path.relative_to(data_dir)
                if path.name.endswith(("-wal", "-shm", "-journal")):
                    continue
                if path.suffix in {".sqlite3", ".db"}:
                    copy = staged / relative
                    copy.parent.mkdir(parents=True, exist_ok=True)
                    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as source:
                        with closing(sqlite3.connect(copy)) as target:
                            source.backup(target)
                    archive.add(copy, arcname=str(relative), recursive=False)
                else:
                    archive.add(path, arcname=str(relative), recursive=False)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    backup(args.data_dir, args.output)
    print("备份已写入指定文件；原数据未修改。")
