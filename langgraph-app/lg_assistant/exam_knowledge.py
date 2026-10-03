"""Local, owner-scoped exam references. FTS indexes Chinese bigrams, no embedding service."""
from __future__ import annotations

import hashlib
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

from . import config

MAX_CHARS = 200_000


def terms(text: str) -> list[str]:
    result = []
    for token in re.findall(r"[\u4e00-\u9fff]+|[a-zA-Z0-9_]+", text.lower()):
        if re.fullmatch(r"[\u4e00-\u9fff]+", token):
            result.extend(token[i:i + 2] for i in range(len(token) - 1))
        elif len(token) > 1:
            result.append(token)
    return list(dict.fromkeys(result))


@contextmanager
def connection():
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(config.DB_PATH, timeout=30)
    db.row_factory = sqlite3.Row
    try:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS exam_documents(
          id TEXT PRIMARY KEY, owner TEXT NOT NULL, title TEXT NOT NULL,
          version TEXT NOT NULL, content TEXT NOT NULL, digest TEXT NOT NULL,
          created TEXT NOT NULL, UNIQUE(owner, title, version, digest));
        CREATE TABLE IF NOT EXISTS exam_chunks(
          id TEXT PRIMARY KEY, document_id TEXT NOT NULL, ordinal INTEGER NOT NULL,
          content TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS exam_chunks_document ON exam_chunks(document_id);
        CREATE VIRTUAL TABLE IF NOT EXISTS exam_fts USING fts5(chunk_id UNINDEXED, tokens);
        """)
        with db:
            yield db
    finally:
        db.close()


def add(owner: str, title: str, content: str, version: str = "") -> dict:
    if not all(isinstance(x, str) for x in (owner, title, content, version)):
        raise ValueError("资料字段必须是文本")
    title, content, version = title.strip(), content.strip(), version.strip()
    if not title or len(title) > 160 or not content or len(content) > MAX_CHARS or len(version) > 80:
        raise ValueError("请填写标题（最多160字）和正文（最多20万字），版本最多80字")
    digest = hashlib.sha256(content.encode()).hexdigest()
    with connection() as db:
        db.execute("BEGIN IMMEDIATE")
        existing = db.execute("SELECT id FROM exam_documents WHERE owner=? AND title=? AND version=? AND digest=?",
                              (owner, title, version, digest)).fetchone()
        if existing:
            return {"id": existing["id"], "duplicate": True}
        doc = uuid.uuid4().hex
        db.execute("INSERT INTO exam_documents VALUES(?,?,?,?,?,?,?)",
                   (doc, owner, title, version, content, digest, datetime.now(timezone.utc).isoformat()))
        for number, start in enumerate(range(0, len(content), 800), 1):
            chunk = content[start:start + 1000]
            cid = f"{doc}:{number}"
            db.execute("INSERT INTO exam_chunks VALUES(?,?,?,?)", (cid, doc, number, chunk))
            db.execute("INSERT INTO exam_fts VALUES(?,?)", (cid, " ".join(terms(chunk))))
        return {"id": doc, "duplicate": False}


def documents(owner: str) -> list[dict]:
    with connection() as db:
        return [dict(row) for row in db.execute(
            "SELECT id,title,version,created,length(content) AS chars FROM exam_documents WHERE owner=? ORDER BY created DESC", (owner,))]


def remove(owner: str, document_id: str) -> bool:
    with connection() as db:
        db.execute("BEGIN IMMEDIATE")
        if not db.execute("SELECT 1 FROM exam_documents WHERE owner=? AND id=?", (owner, document_id)).fetchone():
            return False
        db.execute("DELETE FROM exam_fts WHERE chunk_id IN (SELECT id FROM exam_chunks WHERE document_id=?)", (document_id,))
        db.execute("DELETE FROM exam_chunks WHERE document_id=?", (document_id,))
        db.execute("DELETE FROM exam_documents WHERE id=? AND owner=?", (document_id, owner))
        return True


def document(owner: str, document_id: str) -> dict | None:
    with connection() as db:
        row = db.execute("SELECT id,title,version,content FROM exam_documents WHERE owner=? AND id=?", (owner, document_id)).fetchone()
        return dict(row) if row else None


def retrieve(owner: str, query: str, document_ids: list[str]) -> list[dict]:
    """Only explicitly selected documents are searched; absent/stale selections fail closed."""
    if not document_ids:
        return []
    tokens = terms(query)[:180]
    if not tokens:
        return []
    with connection() as db:
        placeholders = ",".join("?" for _ in document_ids)
        visible = {r[0] for r in db.execute(f"SELECT id FROM exam_documents WHERE owner=? AND id IN ({placeholders})", [owner, *document_ids])}
        if visible != set(document_ids):
            raise ValueError("所选资料已删除或不属于当前用户，请重新选择")
        rows = db.execute(f"""SELECT c.id,c.document_id,c.ordinal,c.content,d.title,d.version
          FROM exam_fts f JOIN exam_chunks c ON c.id=f.chunk_id
          JOIN exam_documents d ON d.id=c.document_id
          WHERE exam_fts MATCH ? AND d.owner=? AND d.id IN ({placeholders})
          ORDER BY bm25(exam_fts) LIMIT 12""", [" OR ".join('"' + t + '"' for t in tokens), owner, *document_ids]).fetchall()
        # One common Chinese bigram is not enough evidence of relevance.
        return [dict(r) for r in rows if len(set(terms(r["content"])) & set(tokens)) >= min(2, len(tokens))][:4]
