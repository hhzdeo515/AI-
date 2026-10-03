"""Isolation, bounded evidence repair and durable ASR failure scenarios. No network."""
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lg_assistant import config, meeting_jobs as jobs, meeting_graph as mg, public_knowledge as pk, store
from lg_assistant.exam_graph import options, retrieve
from evaluation.database import EvaluationDB
from evaluation.scoring import choices, error_rate, grade_exam, diarization_error


class IterationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.old_db = config.DB_PATH
        config.DB_PATH = self.root / "app.sqlite3"
        self.file = self.root / "test.wav"
        self.file.write_bytes(b"audio mock")
        self.summary = {"topic": "交付讨论", "items": [{"id": "i1", "kind": "action", "text": "提交报告", "owner": "未明确", "deadline": "未明确", "evidence": [{"id": "s1", "quote": "提交报告"}]}]}

    def tearDown(self):
        config.DB_PATH = self.old_db
        self.tmp.cleanup()

    def run_summary(self, responses):
        with patch.object(mg.llm, "chat", side_effect=[json.dumps(x, ensure_ascii=False) for x in responses]):
            return mg.summarize_text("提交报告，负责人还未确定。")

    def test_reviewed_summary_keeps_unknown_owner(self):
        out = self.run_summary([self.summary, {"checked": True, "issues": []}])
        self.assertIn("负责人：未明确", out["result"]["text"])
        self.assertEqual(out["repair_count"], 0)

    def test_forged_evidence_cannot_be_passed_by_reviewer(self):
        self.summary["items"][0]["evidence"][0]["quote"] = "编造"
        out = self.run_summary([self.summary, {"checked": True, "issues": []}]*2)
        self.assertEqual(out["repair_count"], 1)
        self.assertEqual(out["result"]["artifacts"][0]["verification"]["status"], "needs_review")
        self.assertIn("[待确认]", out["result"]["text"])

    def test_suggestion_as_decision_repaired_once(self):
        issues = [{"item_id": "i1", "detail": "只是建议，不是已确定决策", "repairable": True}]
        out = self.run_summary([self.summary, {"checked": True, "issues": issues}, self.summary, {"checked": True, "issues": []}])
        self.assertEqual(out["repair_count"], 1)
        self.assertFalse(out["issues"])

    def test_missing_evidence_does_not_loop(self):
        out = self.run_summary([self.summary, {"checked": True, "issues": [{"item_id": "i1", "detail": "缺少明确责任人", "repairable": False}]}])
        self.assertEqual(out["repair_count"], 0)

    def test_reviewer_unknown_id_fails_closed(self):
        out = self.run_summary([self.summary, {"checked": True, "issues": [{"item_id": "nonexistent", "detail": "错误", "repairable": True}]}])
        self.assertIn("[待确认]", out["result"]["text"])
        self.assertEqual(out["repair_count"], 0)

    def test_missing_audio_is_recorded_beside_success(self):
        with patch.object(mg.transcribe, "ensure_vocabulary", return_value=""):
            out = mg.prepare({"files": [str(self.file), str(self.root / "missing.wav")]})
        self.assertEqual(len(out["jobs"]), 1)
        self.assertEqual(out["file_results"][0]["status"], "failed")

    def test_consecutive_meeting_requests_reset_state(self):
        from langgraph.checkpoint.memory import InMemorySaver
        g = mg.build_meeting_graph(InMemorySaver())
        cfg = {"configurable": {"thread_id": "sequential"}}
        with patch.object(mg.llm, "chat", side_effect=[json.dumps(self.summary), '{"checked":true,"issues":[]}']):
            g.invoke({"text": "提交报告，负责人还未确定。"}, cfg)
        with patch.object(mg.llm, "chat", return_value='{"topic":"second","items":[]}'):
            out = g.invoke({"text": "另一个会议"}, cfg)
        self.assertEqual(out["transcript"], "另一个会议")
        self.assertNotIn("提交报告", out["result"]["text"])

    def test_transcript_old_schema_migration_and_verification(self):
        import sqlite3
        old_local, old_init = store._local, store._initialised
        store._local, store._initialised = threading.local(), False
        try:
            store.init()
            conn = store._connect()
            conn.execute("ALTER TABLE transcripts DROP COLUMN verification")
            conn.commit()
            store._initialised = False
            store.init()
            record = mg.transcribe.new_record([{"speaker":0,"begin_ms":0,"end_ms":1000,"text":"报告"}], summary="old")
            store.save_transcript("a", "r1", scene="meeting", record=record)
            self.assertEqual(store.get_transcript("a", "r1")["verification"], {})
            record["verification"] = {"status":"reviewed"}
            store.save_transcript("a", "r1", scene="meeting", record=record)
            self.assertEqual(store.get_transcript("a", "r1")["verification"]["status"], "reviewed")
            self.assertIsNone(store.get_transcript("b", "r1"))
        finally:
            if hasattr(store._local, "conn"): store._local.conn.close()
            store._local, store._initialised = old_local, old_init

    def test_asr_inflight_outcome_is_explicit(self):
        key = jobs.prepare("a", "1", self.file)
        jobs.update(key, status="submitting")
        self.assertIn("不确定", jobs.collect_job(key)["error"])

    def test_bad_model_structure_preserves_transcript(self):
        out = self.run_summary([{"topic": "错误", "items": "bad"}])
        self.assertIn("负责人还未确定", out["result"]["text"])
        self.assertTrue(out["result"]["retryable"])

    def test_concurrent_submission_once(self):
        key = jobs.prepare("alice", "req1", self.file)
        with patch.object(jobs.audio, "normalize_browser_recording", return_value=self.file), patch.object(jobs.transcribe, "upload_for_temp_url", return_value="oss://file"), patch.object(jobs.transcribe, "submit", return_value="task1") as submit:
            with ThreadPoolExecutor(2) as pool:
                list(pool.map(jobs.submit_job, [key, key]))
            self.assertEqual(submit.call_count, 1)
            self.assertEqual(jobs.get(key)["task_id"], "task1")

    def test_uncertain_submission_not_retried(self):
        key = jobs.prepare("alice", "req1", self.file)
        with patch.object(jobs.audio, "normalize_browser_recording", return_value=self.file), patch.object(jobs.transcribe, "upload_for_temp_url", return_value="oss://file"), patch.object(jobs.transcribe, "submit", side_effect=TimeoutError("unknown")) as submit:
            jobs.submit_job(key); jobs.submit_job(key)
            self.assertEqual(submit.call_count, 1)
            self.assertEqual(jobs.get(key)["status"], "uncertain")

    def test_completed_asr_reused(self):
        key = jobs.prepare("alice", "req1", self.file)
        jobs.update(key, status="done", result={"text": "已经完成"})
        with patch.object(jobs.transcribe, "fetch_result", side_effect=AssertionError("no duplicate")), patch.object(jobs.transcribe, "submit", side_effect=AssertionError("no duplicate")):
            self.assertEqual(jobs.collect_job(key)["result"]["text"], "已经完成")
            self.assertEqual(jobs.submit_job(key)["status"], "done")

    def test_poll_failure_retains_task(self):
        key = jobs.prepare("alice", "req1", self.file)
        jobs.update(key, status="submitted", task_id="t")
        with patch.object(jobs.transcribe, "fetch_result", side_effect=TimeoutError("poll timeout")):
            self.assertEqual(jobs.collect_job(key)["status"], "submitted")
        self.assertEqual(jobs.get(key)["task_id"], "t")

    def test_jobs_isolate_owners_and_requests(self):
        self.assertEqual(len({jobs.prepare(o, r, self.file) for o, r in [("a", "1"), ("a", "2"), ("b", "1")]}), 3)

    def test_partial_files_and_speaker_namespace(self):
        keys = [jobs.prepare("a", str(i), self.file) for i in range(3)]
        for key in keys[:2]:
            jobs.update(key, status="done", result={"utterances": [{"speaker": 0, "text": "发言", "begin_ms": 0, "end_ms": 1000}]})
        jobs.update(keys[2], status="failed", error="bad audio")
        out = mg.collect({"jobs": keys, "warnings": []})
        self.assertEqual(len(set(x["speaker"] for x in out["utterances"])), 2)
        self.assertIn("bad audio", str(out["warnings"]))
        self.assertEqual(len(out["file_results"]), 3)

    def test_plain_fallback_persisted(self):
        key = jobs.prepare("a", "1", self.file)
        jobs.update(key, status="fallback", error="转写任务失败")
        with patch.object(jobs.audio, "split", return_value=([self.file], "")), patch.object(jobs.audio, "cleanup"), patch.object(jobs.llm, "asr", return_value="完整文本"):
            self.assertEqual(jobs.collect_job(key)["status"], "done")
        self.assertFalse(jobs.get(key)["result"]["diarized"])

    def test_subgraph_resume_after_asr(self):
        from langgraph.checkpoint.memory import InMemorySaver
        with patch.object(mg, "collect", wraps=mg.collect) as collect:
            g = mg.build_meeting_graph(InMemorySaver())
            cfg = {"configurable": {"thread_id": "meeting"}}
            g.invoke({"text": "提交报告，负责人还未确定。"}, cfg, interrupt_before=["draft"])
            self.assertEqual(collect.call_count, 1)
            with patch.object(mg.llm, "chat", side_effect=[json.dumps(self.summary), '{"checked":true,"issues":[]}']):
                out = g.invoke(None, cfg)
            self.assertEqual(collect.call_count, 1)
        self.assertEqual(out["summary"]["topic"], "交付讨论")

    def test_sqlite_restart_after_audio_before_summary(self):
        from langgraph.checkpoint.sqlite import SqliteSaver
        cfg = {"configurable": {"thread_id": "persisted-audio"}}
        cp_path = str(self.root / "checkpoints.sqlite3")
        with SqliteSaver.from_conn_string(cp_path) as saver:
            g = mg.build_meeting_graph(saver)
            with patch.object(mg.transcribe, "ensure_vocabulary", return_value=""), patch.object(jobs.audio, "normalize_browser_recording", return_value=self.file), patch.object(jobs.transcribe, "upload_for_temp_url", return_value="oss://clip"), patch.object(jobs.transcribe, "submit", return_value="task"), patch.object(jobs.transcribe, "fetch_result", return_value={"transcripts":[{"sentences":[{"speaker_id":0,"begin_time":0,"end_time":2000,"text":"提交报告"}]}]}):
                g.invoke({"owner":"a","request_id":"r","files":[str(self.file)]}, cfg, interrupt_before=["draft"])
        self.summary["items"][0]["evidence"][0]["id"] = "f1s1"
        with SqliteSaver.from_conn_string(cp_path) as saver:
            g = mg.build_meeting_graph(saver)
            with patch.object(jobs.transcribe, "submit", side_effect=AssertionError("duplicate submission")), patch.object(jobs.transcribe, "fetch_result", side_effect=AssertionError("duplicate ASR")), patch.object(mg.llm, "chat", side_effect=[json.dumps(self.summary), '{"checked":true,"issues":[]}']):
                out = g.invoke(None, cfg)
        self.assertEqual(out["file_results"][0]["task_id"], "task")
        self.assertEqual(out["result"]["artifacts"][0]["verification"]["status"], "reviewed")

    def test_public_selection_never_overrides_private(self):
        for event in [{"profile": "internal", "use_public_knowledge": True}, {"document_ids": ["a"*32], "use_public_knowledge": True}]:
            self.assertFalse(options({"exam": event})["use_public_knowledge"])
        self.assertTrue(options({})["use_public_knowledge"])
        self.assertFalse(options({"exam": {"use_public_knowledge": False}})["use_public_knowledge"])

    def test_public_import_idempotent_version_filter(self):
        path = self.root / "concepts.json"
        path.write_text(json.dumps([{"id": "bank", "title": "银行知识", "content": "商业银行吸收存款，发放贷款", "source_url": "https://example.org/knowledge", "version": "2024"}]), encoding="utf-8")
        pk.import_concepts(path); pk.import_concepts(path)
        self.assertFalse(pk.retrieve("银行贷款", "knowledge", "2025"))
        self.assertEqual(len(pk.retrieve("银行贷款", "knowledge", "2024")), 1)

    def test_public_rejects_answer_keys(self):
        path = self.root / "leak.json"
        path.write_text(json.dumps([{"answer": "A"}]), encoding="utf-8")
        with self.assertRaises(ValueError): pk.import_concepts(path)
        path.write_text(json.dumps([{"answers": ["A"]}]), encoding="utf-8")
        with self.assertRaises(ValueError): pk.import_concepts(path)

    def test_public_job_filter_and_online_cannot_find_gold(self):
        data = self.root / "concepts.json"
        data.write_text(json.dumps([{"id":"one","title":"银行存款","content":"银行存款与贷款","source_url":"https://example.org/a","tags":["银行"],"specialist":"knowledge"}]), encoding="utf-8")
        pk.import_concepts(data)
        self.assertEqual(len(pk.retrieve("银行存款", "knowledge", job_role="银行")), 1)
        self.assertFalse(pk.retrieve("银行存款", "knowledge", job_role="计算机"))
        db = EvaluationDB(self.root / "eval.sqlite3")
        db.import_cases([{"id":"secret","scene":"exam","split":"dev","group_id":"p","input":{"text":"question"},"gold":{"answer":"isolatedsecret"}}])
        db.close()
        self.assertFalse(pk.retrieve("isolatedsecret", "knowledge"))

    def test_gold_not_in_input_and_frozen(self):
        db = EvaluationDB(self.root / "eval.sqlite3")
        try:
            row = {"id": "q1", "scene": "exam", "split": "dev", "group_id": "paper", "input": {"text": "Question"}, "gold": {"answer": "SECRET"}}
            db.import_cases([row]); db.import_cases([row])
            self.assertNotIn("SECRET", json.dumps(db.inputs()))
            with self.assertRaises(ValueError): db.import_cases([{**row, "gold": {"answer": "changed"}}])
            with self.assertRaises(ValueError): db.import_cases([row, {**row, "id": "q2", "split": "holdout"}])
            with self.assertRaises(ValueError): db.import_cases([{**row, "id": "q2", "split": "holdout"}])
            with self.assertRaises(ValueError): db.import_cases([{**row, "input": {"extra": {"gold": "secret"}}}])
        finally: db.close()

    def test_scoring_multiple_not_partial_and_no_explanation_letters(self):
        self.assertEqual(choices("答案：A、C，因为B不成立"), ["A", "C"])
        self.assertEqual(choices("无法确定，可能是A"), [])
        r = grade_exam({"answerable": True, "answer": "A"}, {"status": "verified", "type": "multiple", "answers": ["A", "C"]})
        self.assertFalse(r["correct"])

    def test_asr_metrics_and_permutation(self):
        self.assertEqual(error_rate("你好，世界", "你好世界", "zh"), 0)
        self.assertEqual(error_rate("hello world", "hello", "en"), .5)
        ref = [{"speaker": "a", "begin_ms": 0, "end_ms": 1000}]
        hyp = [{"speaker": "99", "begin_ms": 0, "end_ms": 1000}]
        self.assertEqual(diarization_error(ref, hyp, 1), 0)


if __name__ == "__main__":
    unittest.main()
