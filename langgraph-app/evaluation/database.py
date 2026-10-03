"""Explicit, isolated evaluation DB. Input views never return answer fields."""
import hashlib
import json
import sqlite3
from pathlib import Path


class EvaluationDB:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS inputs(id TEXT PRIMARY KEY, scene TEXT NOT NULL,
          split TEXT NOT NULL, group_id TEXT NOT NULL, payload TEXT NOT NULL, digest TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS gold(id TEXT PRIMARY KEY, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS runs(version TEXT NOT NULL, id TEXT NOT NULL,
          result TEXT NOT NULL, PRIMARY KEY(version,id));
        """)
        if "question_digest" not in {r[1] for r in self.db.execute("PRAGMA table_info(inputs)")}:
            self.db.execute("ALTER TABLE inputs ADD COLUMN question_digest TEXT NOT NULL DEFAULT ''")
            for r in self.db.execute("SELECT id,payload FROM inputs").fetchall():
                self.db.execute("UPDATE inputs SET question_digest=? WHERE id=?", (self.fingerprint(json.loads(r["payload"])), r["id"]))
            self.db.commit()

    @staticmethod
    def fingerprint(payload):
        return payload.get("image_sha256") or payload.get("sha256") or hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()

    def import_cases(self, rows):
        groups = {r["group_id"]: r["split"] for r in self.db.execute("SELECT group_id,split FROM inputs")}
        fingerprints = {r["question_digest"]: r["split"] for r in self.db.execute("SELECT question_digest,split FROM inputs")}
        def has_gold(value):
            if isinstance(value, dict):
                return any(k in ("answer", "answers", "expected", "rubric", "rubrics", "gold") or has_gold(v) for k, v in value.items())
            return isinstance(value, list) and any(has_gold(v) for v in value)
        for row in rows:
            if row["split"] not in ("dev", "holdout"):
                raise ValueError("Invalid split")
            key = row["group_id"]
            if key in groups and groups[key] != row["split"]:
                raise ValueError("同材料/同卷跨集合")
            groups[key] = row["split"]
            digest = row.get("question_digest") or self.fingerprint(row["input"])
            if digest in fingerprints and fingerprints[digest] != row["split"]:
                raise ValueError("重复题跨集合")
            fingerprints[digest] = row["split"]
            if has_gold(row["input"]):
                raise ValueError("输入含评分信息")
        with self.db:
            for row in rows:
                payload = json.dumps(row["input"], ensure_ascii=False, sort_keys=True)
                digest = hashlib.sha256(payload.encode()).hexdigest()
                existing = self.db.execute("SELECT * FROM inputs WHERE id=?", (row["id"],)).fetchone()
                if existing and (existing["digest"] != digest or existing["split"] != row["split"] or existing["group_id"] != row["group_id"]):
                    raise ValueError("已冻结样本不可覆盖；请创建新版本")
                gold = json.dumps(row["gold"], ensure_ascii=False, sort_keys=True)
                old_gold = self.db.execute("SELECT payload FROM gold WHERE id=?", (row["id"],)).fetchone()
                if old_gold and old_gold[0] != gold:
                    raise ValueError("评分标准已冻结")
                self.db.execute("INSERT OR IGNORE INTO inputs(id,scene,split,group_id,payload,digest,question_digest) VALUES(?,?,?,?,?,?,?)", (row["id"], row["scene"], row["split"], row["group_id"], payload, digest, row.get("question_digest") or self.fingerprint(row["input"])))
                self.db.execute("INSERT OR IGNORE INTO gold VALUES(?,?)", (row["id"], gold))

    def inputs(self, scene=None):
        rows = self.db.execute("SELECT id,scene,split,group_id,payload FROM inputs WHERE (? IS NULL OR scene=?) ORDER BY id", (scene, scene))
        return [{**{k: r[k] for k in ("id", "scene", "split", "group_id")}, "input": json.loads(r["payload"])} for r in rows]

    def gold(self, key):
        return json.loads(self.db.execute("SELECT payload FROM gold WHERE id=?", (key,)).fetchone()[0])

    def save_run(self, version, key, result):
        with self.db:
            self.db.execute("INSERT INTO runs VALUES(?,?,?)", (version, key, json.dumps(result, ensure_ascii=False)))

    def run(self, version, key):
        row = self.db.execute("SELECT result FROM runs WHERE version=? AND id=?", (version, key)).fetchone()
        return json.loads(row[0]) if row else None

    def close(self):
        self.db.close()
