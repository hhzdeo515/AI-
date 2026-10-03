"""Request-local model switching keeps references, sessions, archives and exports shared."""
from __future__ import annotations

import io
import json
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lg_assistant import config, exam_knowledge, graph, store
from lg_assistant.web.app import create_app


class ModelSwitchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.saved = {key: getattr(config, key) for key in (
            "DATA_DIR", "DB_PATH", "CHECKPOINT_DB", "EXPORT_DIR", "UPLOAD_DIR", "ACCESS_TOKEN")}
        config.DATA_DIR, config.DB_PATH = root, root / "app.sqlite3"
        config.CHECKPOINT_DB = root / "checkpoints.sqlite3"
        config.EXPORT_DIR, config.UPLOAD_DIR = root / "exports", root / "uploads"
        config.ACCESS_TOKEN = ""
        store._local, store._initialised = threading.local(), False
        self.cp = graph.open_checkpointer()
        self.compiled = graph.build_graph(self.cp, with_telemetry=False)
        self.client = create_app(self.compiled).test_client()
        self.image = root / "question.png"
        self.image.write_bytes(b"image stub")
        self.document = exam_knowledge.add("alice", "除法规则", "制度规定：计算8/2，结果为4。", "2026")
        self.event = {"exam": {"document_ids": [self.document["id"]], "allow_web": False}, "practice": {"agent": "ability"}}
        self.perception = {"complete": True, "question_type": "single", "question": "制度规定，计算8/2",
                           "options": {"A": "2", "B": "4"}, "missing": []}
        self.final = {"answerable": True, "answer": "B", "explanation": "8/2=4", "repair_issues": [],
                      "citations": [{"id": self.document["id"] + ":1", "quote": "制度规定：计算8/2，结果为4。"}]}
        self.stubs = ExitStack()
        # These model-switch tests exercise request/session contracts. Batch
        # inventory and real pixels are covered separately by batch tests.
        self.stubs.enter_context(patch("lg_assistant.exam_batch.detect_questions", return_value={
            "questions": [{"number": "1", "preview": "", "complete": True, "missing": [],
                           "regions": [{"page": 1, "bbox": [0, 0, 1000, 1000]}], "context_regions": []}],
            "notes": []}))
        self.stubs.enter_context(patch("lg_assistant.exam_batch._crop_images",
            side_effect=lambda images, question, crop_dir: list(images)))
        self.stubs.enter_context(patch("lg_assistant.nodes._ROUTER", None))
        self.stubs.enter_context(patch("lg_assistant.llm.json_chat", side_effect=AssertionError("unexpected classification network call")))
        self.stubs.enter_context(patch("lg_assistant.jev.evaluate", side_effect=AssertionError("unexpected JEV network call")))
        self.stubs.enter_context(patch("lg_assistant.vision.observe", return_value="制度规定，计算8/2，A 2 B 4"))
        self.original_solve = self.stubs.enter_context(patch("lg_assistant.vision.solve", return_value={
            "answerable": True, "candidate": "B", "calculations": [{"expression": "8/2"}]}))
        self.stubs.enter_context(patch("lg_assistant.vision.review", side_effect=lambda *a, **k: dict(self.final)))
        self.stubs.enter_context(patch("lg_assistant.exam_decision.perceive", return_value=self.perception))
        self.stubs.enter_context(patch("lg_assistant.exam_decision.route", return_value=(
            "knowledge", {"stage": "route", "model": "jev-test"})))
        self.jev_solve = self.stubs.enter_context(patch("lg_assistant.exam_decision.solve", return_value={
            "answerable": True, "candidate": "B", "calculations": [{"expression": "8/2"}]}))
        self.stubs.enter_context(patch("lg_assistant.exam_decision.review", side_effect=lambda *a, **k: dict(self.final)))
        self.stubs.enter_context(patch("lg_assistant.search.search_answer", side_effect=AssertionError("unexpected web call")))

    def tearDown(self):
        self.stubs.close()
        self.cp.conn.close()
        connection = getattr(store._local, "conn", None)
        if connection:
            connection.close()
        store._local, store._initialised = threading.local(), False
        for key, value in self.saved.items():
            setattr(config, key, value)
        self.temp.cleanup()

    def submit(self, backend=None, *, upload=True, request_id=None, session="same-session"):
        data = {"text": "按资料解题", "scene": "exam", "owner": "alice", "session_id": session,
                "event": json.dumps(self.event)}
        if backend is not None:
            data["exam_backend"] = backend
        if request_id:
            data["request_id"] = request_id
        if upload:
            data["files"] = (io.BytesIO(b"image stub"), "question.png")
        response = self.client.post("/api/chat", data=data)
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return response.get_json()

    def test_both_models_use_same_documents_archive_list_and_export(self):
        original, jev = self.submit("original"), self.submit("jev")
        self.assertEqual((original["exam_backend"], jev["exam_backend"]), ("original", "jev"))
        self.assertEqual((self.original_solve.call_count, self.jev_solve.call_count), (1, 1))
        for result in (original, jev):
            self.assertEqual(result["note"], "")
            self.assertIn("除法规则", result["text"])
            details = self.client.get("/api/resource", query_string={"owner": "alice", "id": result["archived_id"]})
            self.assertEqual(details.status_code, 200)
            exported = self.client.get("/api/export", query_string={"owner": "alice", "id": result["archived_id"], "format": "md"})
            self.assertEqual(exported.status_code, 200)
            self.assertIn("除法规则", exported.get_data(as_text=True))
        rows = self.client.get("/api/resources?owner=alice").get_json()["resources"]
        self.assertEqual({row["id"] for row in rows}, {original["archived_id"], jev["archived_id"]})
        documents = self.client.get("/api/exam/knowledge?owner=alice").get_json()["documents"]
        self.assertEqual([doc["id"] for doc in documents], [self.document["id"]])

    def test_switch_reuses_last_image_and_unspecified_request_defaults_to_original(self):
        self.submit("jev")
        followup = self.submit(upload=False)
        self.assertEqual(followup["exam_backend"], "original")
        self.assertIn("沿用上一轮", followup["text"])
        snap = self.compiled.get_state(graph.run_config("alice", "same-session"))
        self.assertEqual(snap.values["exam_backend"], "original")

    def test_idempotent_replay_reports_executed_model(self):
        first = self.submit("jev", request_id="fixed-id")
        replay = self.submit("original", request_id="fixed-id")
        self.assertEqual(replay["status"], "duplicate")
        self.assertEqual(replay["exam_backend"], "jev")
        self.assertEqual(replay["archived_id"], first["archived_id"])
        self.assertEqual(self.jev_solve.call_count, 1)
        self.original_solve.assert_not_called()

    def test_parallel_requests_do_not_change_global_model_selection(self):
        model = config.EXAM_MODEL
        barrier = threading.Barrier(2)
        original_fake, jev_fake = self.original_solve.side_effect, self.jev_solve.side_effect
        def solve(*a, **k):
            barrier.wait(timeout=5)
            return {"answerable": True, "candidate": "B", "calculations": []}
        self.original_solve.side_effect = self.jev_solve.side_effect = solve
        def run(backend):
            try:
                return self.compiled.invoke({"owner": "alice", "session_id": backend, "request_id": backend,
                    "text": "按资料解题", "files": [str(self.image)], "scene_hint": "exam", "exam_backend": backend,
                    "event": self.event, "notes": []}, graph.run_config("alice", backend))
            finally:
                connection = getattr(store._local, "conn", None)
                if connection:
                    connection.close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            outputs = list(pool.map(run, ("original", "jev")))
        self.assertEqual([output["result"]["exam_backend"] for output in outputs], ["original", "jev"])
        self.assertEqual(config.EXAM_MODEL, model)
        self.original_solve.side_effect, self.jev_solve.side_effect = original_fake, jev_fake

    def test_pure_calculation_does_not_claim_jev_execution(self):
        response = self.client.post("/api/chat", data={"text": "计算8/2", "owner": "alice", "exam_backend": "jev"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["exam_backend"], "")
        self.jev_solve.assert_not_called()

    def test_invalid_model_is_rejected(self):
        response = self.client.post("/api/chat", data={"text": "计算8/2", "exam_backend": "unknown"})
        self.assertEqual(response.status_code, 400)

    def test_async_submission_retains_request_model(self):
        captured = []
        def submit(fn, request_key=""):
            captured.append(fn())
            return "task-test"
        with patch("lg_assistant.web.app._task_submit", side_effect=submit):
            response = self.client.post("/api/chat/async", data={
                "text": "按资料解题", "scene": "exam", "owner": "alice", "exam_backend": "jev",
                "event": json.dumps(self.event), "files": (io.BytesIO(b"image stub"), "question.png")})
        self.assertEqual(response.status_code, 202)
        self.assertEqual(captured[0]["exam_backend"], "jev")
        self.original_solve.assert_not_called()

    def test_checkpoint_resume_retains_jev_node(self):
        cfg = graph.run_config("alice", "interrupted")
        self.compiled.invoke({"owner": "alice", "session_id": "interrupted", "request_id": "resume-test",
            "text": "按资料解题", "files": [str(self.image)], "scene_hint": "exam", "exam_backend": "jev",
            "event": self.event, "notes": []}, cfg, interrupt_before=["photo_practice"])
        self.assertEqual(self.compiled.get_state(cfg).next, ("photo_practice",))
        self.assertEqual(self.compiled.get_state(cfg).values["exam_backend"], "jev")
        response = self.client.get("/api/resume?owner=alice&session_id=interrupted")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["resumed"])
        self.assertEqual(response.get_json()["exam_backend"], "jev")
        self.assertEqual(self.jev_solve.call_count, 1)
        self.original_solve.assert_not_called()

    def test_meeting_dispatch_is_unchanged_by_model_choice(self):
        for backend in ("original", "jev"):
            self.assertEqual(graph.dispatch({"exam_backend": backend,
                "routing": {"scene": "meeting", "action": "summarize"}, "files": []}), "meeting")

    def test_health_and_version_list_expose_readiness_without_credentials(self):
        with patch.object(config, "TYPESAFE_API_KEY", "secret-test-key"), patch.object(config, "DASHSCOPE_API_KEY", "secret-other-key"):
            health = self.client.get("/health").get_json()
            versions = self.client.get("/api/versions").get_json()
        self.assertTrue(health["jev_ready"])
        self.assertEqual([version["id"] for version in versions["versions"]], ["original", "jev"])
        self.assertNotIn("secret-", json.dumps([health, versions]))


if __name__ == "__main__":
    unittest.main()
