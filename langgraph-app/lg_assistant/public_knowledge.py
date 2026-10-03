"""Curated public concepts only. No question bank or evaluation answers live here."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from collections import Counter
from pathlib import Path

from . import config
from .exam_knowledge import terms

BUNDLE_DIR = Path(__file__).parent / "knowledge"
_BUNDLE_LOCK = threading.RLock()
_BUNDLE_CACHE: dict[tuple[str, str], str] = {}


def connect():
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(config.DB_PATH, timeout=30)
    db.row_factory = sqlite3.Row
    db.executescript("""
    CREATE TABLE IF NOT EXISTS public_concepts(
      id TEXT PRIMARY KEY, title TEXT NOT NULL, content TEXT NOT NULL,
      source_url TEXT NOT NULL, version TEXT NOT NULL, specialist TEXT NOT NULL,
      tags TEXT NOT NULL, digest TEXT NOT NULL);
    CREATE VIRTUAL TABLE IF NOT EXISTS public_concepts_fts USING fts5(id UNINDEXED, tokens);
    CREATE TABLE IF NOT EXISTS public_concept_bundles(path TEXT PRIMARY KEY, digest TEXT NOT NULL, ids TEXT NOT NULL);
    """)
    if "metadata" not in {r[1] for r in db.execute("PRAGMA table_info(public_concepts)")}:
        try:
            db.execute("ALTER TABLE public_concepts ADD COLUMN metadata TEXT NOT NULL DEFAULT '{}'")
            db.commit()
        except sqlite3.OperationalError:
            if "metadata" not in {r[1] for r in db.execute("PRAGMA table_info(public_concepts)")}:
                db.close()
                raise
    return db


def import_concepts(path: Path) -> int:
    rows = json.loads(Path(path).read_text(encoding="utf-8"))
    db = connect()
    try:
        with db:
            for row in rows:
                if any(k in row for k in ("answer", "answers", "expected", "rubric", "rubrics", "question", "gold")):
                    raise ValueError("公共知识库不能导入逐题答案或评分标准")
                if not row.get("source_url", "").startswith("https://"):
                    raise ValueError("知识点必须包含来源")
                content = row["content"]
                digest = hashlib.sha256(content.encode()).hexdigest()
                metadata = {k: row[k] for k in ("module", "aliases", "source_title", "source_kind", "reviewed_at", "version_label", "risk") if k in row}
                db.execute("INSERT OR REPLACE INTO public_concepts(id,title,content,source_url,version,specialist,tags,digest,metadata) VALUES(?,?,?,?,?,?,?,?,?)",
                           (row["id"], row["title"], content, row["source_url"], row.get("version", "stable"),
                            row.get("specialist", "knowledge"), json.dumps(row.get("tags", []), ensure_ascii=False), digest,
                            json.dumps(metadata, ensure_ascii=False)))
                db.execute("DELETE FROM public_concepts_fts WHERE id=?", (row["id"],))
                db.execute("INSERT INTO public_concepts_fts VALUES(?,?)", (row["id"], " ".join(terms(row["title"] + " " + content + " " + " ".join(row.get("tags", [])) + " " + " ".join(row.get("aliases", []))))))
        return len(rows)
    finally:
        db.close()


def ensure_bundled() -> int:
    """Install the packaged concepts in this DB once, including upgrades.

    Runs for production and direct graph callers. No evaluation folder is read.
    Ownership is tracked so removed bundle entries do not leave stale facts.
    """
    path = BUNDLE_DIR / "exam_concepts.json"
    if not path.is_file():
        raise ValueError("考试知识库文件缺失")
    key = (str(config.DB_PATH.resolve()), str(path.resolve()))
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    with _BUNDLE_LOCK:
        if _BUNDLE_CACHE.get(key) == digest and config.DB_PATH.is_file():
            return 0
        db = connect()
        try:
            previous = db.execute("SELECT digest,ids FROM public_concept_bundles WHERE path=?", (key[1],)).fetchone()
            if previous and previous["digest"] == digest:
                _BUNDLE_CACHE[key] = digest
                return 0
            rows = json.loads(path.read_text(encoding="utf-8"))
            ids = [r["id"] for r in rows]
            if len(ids) != len(set(ids)) or not all(i.startswith("cs-") for i in ids):
                raise ValueError("考试知识库条目编号无效或重复")
            count = import_concepts(path)
            with db:
                for cid in set(json.loads(previous["ids"]) if previous else []) - set(ids):
                    db.execute("DELETE FROM public_concepts WHERE id=?", (cid,))
                    db.execute("DELETE FROM public_concepts_fts WHERE id=?", (cid,))
                db.execute("INSERT OR REPLACE INTO public_concept_bundles VALUES(?,?,?)", (key[1], digest, json.dumps(ids)))
            _BUNDLE_CACHE[key] = digest
            return count
        finally:
            db.close()


def bundled_catalog() -> dict:
    """User-visible scope and provenance; no private documents or answer keys."""
    rows = json.loads((BUNDLE_DIR / "exam_concepts.json").read_text(encoding="utf-8"))
    sources = json.loads((BUNDLE_DIR / "sources.json").read_text(encoding="utf-8"))
    return {"count": len(rows), "modules": dict(Counter(r["module"] for r in rows)),
            "concepts": [{k: r.get(k, "") for k in ("id", "module", "title", "content", "version_label", "source_kind")}
                         for r in rows],
            "sources": sources, "updated": "2026-09-30",
            "scope": "主要公共考试科目与岗位基础知识；不是所有地区和专业的完整教材。",
            "limits": "法律、政策与时政须按题干年份和适用版本核实；方法知识不替代原图和题目条件。"}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="导入通用知识点；禁止导入试题或评测答案")
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    print(f"Imported {import_concepts(args.path)} public concepts")


def retrieve(query: str, specialist: str, version: str = "", tags: list[str] | None = None, job_role: str = "") -> list[dict]:
    tokens = terms(query)[:180]
    if not tokens:
        return []
    db = connect()
    try:
        rows = db.execute("""SELECT c.* FROM public_concepts_fts f
          JOIN public_concepts c ON c.id=f.id WHERE public_concepts_fts MATCH ?
          AND c.specialist IN (?, 'all') AND (c.version='stable' OR c.version=?)
          ORDER BY bm25(public_concepts_fts) LIMIT 48""",
                          (" OR ".join('"' + t + '"' for t in tokens), specialist, version)).fetchall()
        result = []
        for row in rows:
            r = dict(row)
            r.update(json.loads(r.pop("metadata")))
            r["tags"] = json.loads(r["tags"])
            if tags and not set(tags).intersection(r["tags"]):
                continue
            if job_role and job_role not in r["tags"] and "通用" not in r["tags"]:
                continue
            if len(set(terms(r["title"] + r["content"])) & set(tokens)) < min(2, len(tokens)):
                continue
            # Packaged methods need a concrete topic match. Broad phrases such
            # as "选择正确答案" must not fill the context with irrelevant notes.
            aliases = r.get("aliases", [])
            hits = sum(len(a) for a in aliases if len(a) >= 2 and a.lower() in query.lower())
            if aliases and not hits:
                continue
            r["relevance"] = hits
            result.append({**r, "ordinal": 1, "document_id": r["id"], "kind": "public_concept"})
        result.sort(key=lambda r: r["relevance"], reverse=True)
        return result[:4]
    finally:
        db.close()
