"""Real parent/checkpoint integration; providers and audio devices stay offline."""
from __future__ import annotations

import io
import json
import tempfile
import threading
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from lg_assistant import config, exam_knowledge, graph, store, photo_practice
from lg_assistant.web.app import create_app


class PhotoPracticeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.saved = {k: getattr(config, k) for k in (
            "DATA_DIR", "DB_PATH", "CHECKPOINT_DB", "UPLOAD_DIR", "EXPORT_DIR", "ACCESS_TOKEN")}
        config.DATA_DIR, config.DB_PATH = root, root / "app.sqlite3"
        config.CHECKPOINT_DB, config.UPLOAD_DIR, config.EXPORT_DIR = root / "checkpoints.sqlite3", root / "uploads", root / "exports"
        config.ACCESS_TOKEN = ""
        store._local, store._initialised = threading.local(), False
        self.cp = graph.open_checkpointer()
        self.compiled = graph.build_graph(self.cp, with_telemetry=False)
        self.client = create_app(self.compiled).test_client()
        self.ocr, self.classified = "计算8/2 A 2 B 4", "ability"
        self.calls = []
        self.stubs = ExitStack()
        self.stubs.enter_context(patch("lg_assistant.nodes._ROUTER", None))
        self.observe = self.stubs.enter_context(patch("lg_assistant.vision.observe", side_effect=lambda *a, **k: self.ocr))
        self.stubs.enter_context(patch("lg_assistant.llm.chat", side_effect=AssertionError("unexpected model call")))
        self.stubs.enter_context(patch("lg_assistant.llm.json_chat", side_effect=self.generate))
        self.stubs.enter_context(patch("lg_assistant.jev.evaluate", side_effect=lambda *a, **k: {
            "answers": {"task_type": {"choice": self.classified, "confidence": .98}}, "model": "jev-stub"}))
        self.stubs.enter_context(patch("lg_assistant.exam_batch.detect_questions", return_value={
            "questions": [{"number": str(n), "preview": "计算8/2", "complete": True, "missing": [],
                "regions": [{"page": 1, "bbox": [0, 0, 1000, 1000]}], "context_regions": []} for n in (1, 2)], "notes": []}))
        self.stubs.enter_context(patch("lg_assistant.exam_batch._crop_images", side_effect=lambda images, *a: images))
        self.original_solve = self.stubs.enter_context(patch("lg_assistant.vision.solve", return_value={"answerable": True, "candidate": "B"}))
        self.stubs.enter_context(patch("lg_assistant.vision.review", return_value={"answerable": True, "answer": "B", "explanation": "8/2=4"}))
        self.stubs.enter_context(patch("lg_assistant.exam_decision.perceive", return_value={
            "complete": True, "question_type": "single", "question": "8/2", "options": {"A": "2", "B": "4"}, "missing": []}))
        self.stubs.enter_context(patch("lg_assistant.exam_decision.route", return_value=("quantitative", {"stage": "route", "model": "jev-stub"})))
        self.jev_solve = self.stubs.enter_context(patch("lg_assistant.exam_decision.solve", return_value={"answerable": True, "candidate": "B"}))
        self.stubs.enter_context(patch("lg_assistant.exam_decision.review", return_value={"answerable": True, "answer": "B", "explanation": "8/2=4"}))
        self.stubs.enter_context(patch("lg_assistant.search.search_answer", side_effect=AssertionError("unexpected search")))

    def tearDown(self):
        self.stubs.close()
        self.cp.conn.close()
        conn = getattr(store._local, "conn", None)
        if conn:
            conn.close()
        store._local, store._initialised = threading.local(), False
        for key, value in self.saved.items():
            setattr(config, key, value)
        self.temp.cleanup()

    def generate(self, messages, **kw):
        self.calls.append((messages, kw))
        if "题型分类器" in messages[0]["content"]:
            return {"agent": self.classified, "confidence": .98, "reason": "测试题面分类"}
        context = json.loads(messages[-1]["content"])
        return {"text": "本步骤真实输出：" + context["task_data"]["question"], "citations": []}

    def submit(self, *, practice=None, upload=False, backend="original", text="", session="photo", audio=False, documents=None):
        event = {"practice": practice or {"agent": "auto", "action": "run"}, "exam": {"allow_web": False, "use_public_knowledge": False}}
        if documents is not None:
            event["exam"]["document_ids"] = documents
        data = {"owner": "alice", "session_id": session, "scene": "exam", "text": text,
                "exam_backend": backend, "event": json.dumps(event)}
        if upload or audio:
            data["files"] = (io.BytesIO(b"offline media"), "reply.wav" if audio else "question.png")
        response = self.client.post("/api/chat", data=data)
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return response.get_json()

    @staticmethod
    def artifact(result, kind):
        return next(item for item in result["artifacts"] if item["kind"] == kind)

    def snapshot(self, session="photo"):
        return self.compiled.get_state(graph.run_config("alice", session)).values

    def test_auto_ability_keeps_all_questions_and_both_real_pipelines(self):
        for backend in ("original", "jev"):
            result = self.submit(upload=True, backend=backend, session=backend)
            batch = self.artifact(result, "exam_batch")
            self.assertEqual((batch["count"], batch["answered_count"]), (2, 2))
            self.assertEqual(result["exam_backend"], backend)
            self.assertEqual(self.artifact(result, "practice_route")["agent"], "ability")
        self.assertEqual((self.original_solve.call_count, self.jev_solve.call_count), (2, 2))

    def test_explicit_ability_does_not_classify_or_fall_back(self):
        with patch("lg_assistant.practice_agents.classify_task", side_effect=AssertionError("must not classify")), \
             patch("lg_assistant.vision.observe", side_effect=AssertionError("child graph observe is stubbed below")), \
             patch("lg_assistant.exam_batch.solve_question", side_effect=lambda s, q, backend, directory: {
                 "id": q["id"], "label": q["label"], "page": q["page"], "status": "answered", "answer": "B",
                 "explanation": "8/2=4", "needed": "", "review_notes": "", "artifacts": []}):
            result = self.submit(upload=True, backend="jev", practice={"agent": "ability", "action": "run"})
        self.assertEqual(self.artifact(result, "exam_batch")["count"], 2)
        self.assertEqual(result["exam_backend"], "jev")

    def test_auto_essay_stages_and_text_critique_preserve_checkpoint(self):
        self.ocr, self.classified = "根据材料撰写策论，主题为基层治理", "essay"
        result = self.submit(upload=True)
        self.assertEqual([stage["id"] for stage in self.artifact(result, "practice")["stages"]], ["analysis", "outline", "draft"])
        image_count = self.observe.call_count
        previous = self.snapshot()["practice_state"]
        critique = self.submit(practice={"agent": "essay", "action": "critique", "draft": "我的真实治理草稿"}, backend="jev")
        self.assertEqual(self.observe.call_count, image_count)
        current = self.snapshot()["practice_state"]
        self.assertEqual(current["question"], previous["question"])
        self.assertEqual(current["user_draft"], "我的真实治理草稿")
        self.assertEqual(self.artifact(critique, "practice")["stages"][0]["id"], "critique")
        self.assertEqual(self.artifact(critique, "practice")["decision_model"], "user")
        self.assertIn("题型由用户选择", critique["text"])
        self.assertNotIn("当前选择JEV决策模式", critique["text"])
        self.assertTrue(result["archived_id"] and critique["archived_id"])

    def test_interview_text_turn_uses_real_answer_and_new_followup(self):
        self.ocr, self.classified = "面试：你如何处理群众诉求？", "interview"
        result = self.submit(upload=True)
        started = self.artifact(result, "practice")
        self.assertEqual(started["next_actions"][0]["id"], "answer")
        self.assertEqual(started["stages"][0]["text"], self.ocr)
        self.assertEqual(started["question_source"], "provided")
        self.assertEqual(started["question_input_source"], "photo")
        self.assertEqual(started["generation_model"], "")
        self.assertIn("未生成或改写题目", result["text"])
        self.assertNotIn("文字生成使用", result["text"])
        self.assertFalse(any("当前步骤" in messages[0]["content"] for messages, _ in self.calls))
        previous = self.snapshot()["practice_state"]
        self.assertEqual(previous["current_question"], self.ocr)
        self.assertEqual(previous["question_input_source"], "photo")
        observed = self.observe.call_count
        result = self.submit(practice={"agent": "auto", "action": "answer", "answer": "我会先认真倾听并核实诉求"}, backend="jev")
        current = self.snapshot()["practice_state"]
        self.assertEqual(current["question"], previous["question"])
        self.assertEqual([stage["id"] for stage in self.artifact(result, "practice")["stages"]], ["answer", "feedback", "follow_up"])
        self.assertEqual(self.observe.call_count, observed)
        self.assertEqual(self.artifact(result, "practice")["decision_model"], "previous")
        self.assertIn("沿用已保存题型", result["text"])
        self.assertNotIn("当前选择JEV决策模式", result["text"])
        self.assertEqual(current["question_source"], "provided")
        self.assertEqual(current["current_question_source"], "generated")
        self.assertEqual(self.artifact(result, "practice")["generation_model"], config.MODEL_TEXT)
        generation = [json.loads(messages[-1]["content"])["task_data"] for messages, _ in self.calls if "当前步骤" in messages[0]["content"]]
        self.assertTrue(any(item.get("user_answer") == "我会先认真倾听并核实诉求" for item in generation))
        self.assertTrue(all(item.get("active_question") == self.ocr for item in generation))

    def test_audio_answer_runs_asr_in_interview_instead_of_meeting(self):
        self.ocr, self.classified = "面试：你如何处理群众诉求？", "interview"
        self.submit(upload=True)
        count = self.observe.call_count
        with patch("lg_assistant.llm.asr", return_value="录音中实际说出的回答") as asr, \
             patch("lg_assistant.nodes.meeting_audio", side_effect=AssertionError("must not summarize a meeting")):
            result = self.submit(practice={"agent": "interview", "action": "answer"}, audio=True)
        asr.assert_called_once()
        self.assertEqual(self.observe.call_count, count)
        self.assertEqual(self.artifact(result, "practice")["transcript"], "录音中实际说出的回答")
        self.assertEqual(self.snapshot()["practice_state"]["agent"], "interview")

    def test_failed_audio_keeps_interview_and_does_not_archive_success(self):
        self.ocr, self.classified = "面试：沟通群众诉求", "interview"
        self.submit(upload=True)
        previous = self.snapshot()["practice_state"]
        with patch("lg_assistant.llm.asr", return_value=""):
            result = self.submit(practice={"agent": "interview", "action": "answer"}, audio=True)
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["archived_id"], "")
        self.assertEqual(self.snapshot()["practice_state"], previous)

    def test_mixed_unknown_keeps_whole_ocr_and_can_be_manually_corrected(self):
        self.ocr, self.classified = "第1题 A.2 B.4 C.6 D.8\n第2题：根据材料写一篇策论", "unknown"
        first = self.submit(upload=True)
        self.assertEqual(first["status"], "needs_selection")
        self.assertEqual(first["archived_id"], "")
        self.assertIn("尚未完成", first["text"])
        route = self.artifact(first, "practice_route")
        self.assertEqual(route["question"], self.ocr)
        count = self.observe.call_count
        second = self.submit(practice={"agent": "essay", "action": "analyze"})
        self.assertEqual(second["status"], "ok")
        self.assertEqual(self.artifact(second, "practice")["question"], self.ocr)
        self.assertEqual(self.observe.call_count, count)

    def test_new_photo_and_new_text_topic_reset_previous_interview(self):
        self.ocr, self.classified = "面试：第一道题", "interview"
        self.submit(upload=True)
        self.ocr, self.classified = "策论：新的材料题", "essay"
        self.submit(upload=True)
        self.assertEqual(self.snapshot()["practice_state"]["question"], self.ocr)
        self.assertNotIn("current_question", self.snapshot()["practice_state"])
        result = self.submit(practice={"agent": "interview", "action": "start", "topic": "新的沟通主题"})
        practice = self.artifact(result, "practice")
        self.assertEqual(practice["question"], "新的沟通主题")
        self.assertEqual(practice["question_source"], "generated")
        self.assertEqual(practice["question_input_source"], "topic")
        self.assertEqual(practice["generation_model"], config.MODEL_TEXT)
        self.assertEqual(self.snapshot()["last_images"], [])

    def test_explicit_complete_text_question_is_preserved_without_generation_in_jev_mode(self):
        question = "假如你在项目现场遇到两位群众对方案意见相反，你会如何核实事实、开展沟通并推进处置？"
        with patch("lg_assistant.llm.json_chat", side_effect=AssertionError("provided question must not generate")):
            result = self.submit(practice={"agent": "interview", "action": "start", "topic": question}, backend="jev")
        artifact = self.artifact(result, "practice")
        self.assertEqual(artifact["stages"][0]["text"], question)
        self.assertEqual(artifact["question_source"], "provided")
        self.assertEqual(artifact["question_input_source"], "topic")
        self.assertEqual(artifact["decision_model"], "user")
        self.assertEqual(artifact["generation_model"], "")
        self.assertIn("题型由用户选择", result["text"])
        self.assertIn("未生成或改写题目", result["text"])
        self.assertNotIn("JEV决策模式", result["text"])
        self.assertNotIn("文字生成使用", result["text"])
        # Resume from disk, rather than relying on the original in-memory graph instance.
        self.cp.conn.close()
        self.cp = graph.open_checkpointer()
        self.compiled = graph.build_graph(self.cp, with_telemetry=False)
        saved = self.snapshot()["practice_state"]
        self.assertEqual(saved["current_question"], question)
        self.assertEqual(saved["question_source"], "provided")
        self.assertEqual(saved["question_input_source"], "topic")

    def test_unknown_then_interview_keeps_complete_long_photo_question_and_source(self):
        self.ocr = "背景材料：" + "现场正在办理群众事务。" * 430 + "\n假如现场发生意见冲突，你会如何核实事实并开展沟通？"
        self.classified = "unknown"
        first = self.submit(upload=True)
        self.assertEqual(first["status"], "needs_selection")
        self.assertEqual(self.artifact(first, "practice_route")["question_input_source"], "photo")
        calls = len(self.calls)
        observed = self.observe.call_count
        result = self.submit(practice={"agent": "interview", "action": "start"}, backend="jev")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(self.artifact(result, "practice")["stages"][0]["text"], self.ocr)
        self.assertEqual(self.artifact(result, "practice")["question_input_source"], "photo")
        self.assertEqual(self.artifact(result, "practice")["question_source"], "provided")
        self.assertEqual(self.snapshot()["practice_state"]["current_question"], self.ocr)
        self.assertEqual(len(self.calls), calls)
        self.assertEqual(self.observe.call_count, observed)

    def test_unknown_then_ability_keeps_every_photo_question(self):
        self.ocr, self.classified = "无法确认类别的两道题", "unknown"
        first = self.submit(upload=True)
        original_images = self.snapshot()["last_images"]
        second = self.submit(practice={"agent": "ability", "action": "run"})
        self.assertEqual(first["status"], "needs_selection")
        self.assertEqual(self.artifact(second, "exam_batch")["count"], 2)
        self.assertEqual(self.snapshot()["last_images"], original_images)
        self.assertEqual(self.original_solve.call_count, 2)

    def test_interview_text_answer_without_reupload_or_practice_event(self):
        self.ocr, self.classified = "面试：如何处理群众诉求", "interview"
        self.submit(upload=True)
        count = self.observe.call_count
        response = self.client.post("/api/chat", data={"owner": "alice", "session_id": "photo",
            "scene": "exam", "text": "我会认真倾听并沟通", "exam_backend": "jev"})
        self.assertEqual(response.status_code, 200)
        result = response.get_json()
        self.assertEqual([item["id"] for item in self.artifact(result, "practice")["stages"]], ["answer", "feedback", "follow_up"])
        self.assertEqual(self.observe.call_count, count)

    def test_long_audio_transcript_does_not_invoke_feedback_or_discard_previous_round(self):
        self.ocr, self.classified = "面试：如何处理群众诉求", "interview"
        self.submit(upload=True)
        previous = self.snapshot()["practice_state"]
        count = len(self.calls)
        with patch("lg_assistant.llm.asr", return_value="答" * 20001):
            result = self.submit(practice={"agent": "interview", "action": "answer"}, audio=True)
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["archived_id"], "")
        self.assertEqual(self.snapshot()["practice_state"], previous)
        self.assertEqual(len(self.calls), count)

    def test_reference_source_and_archive_are_shared_across_model_switch(self):
        self.ocr, self.classified = "策论：基层治理材料", "essay"
        document = exam_knowledge.add("alice", "基层治理参考", "基层治理依靠真实群众意见，不能编造群众诉求。", "2026")
        first = self.submit(upload=True, documents=[document["id"]])
        second = self.submit(practice={"agent": "essay", "action": "outline"}, backend="jev", documents=[document["id"]])
        refs = [json.loads(messages[-1]["content"])["references"] for messages, _ in self.calls if "当前步骤" in messages[0]["content"]]
        self.assertTrue(all(any(r["title"] == "基层治理参考" for r in part) for part in refs))
        rows = self.client.get("/api/resources?owner=alice").get_json()["resources"]
        self.assertEqual({row["id"] for row in rows}, {first["archived_id"], second["archived_id"]})

    def test_generation_failure_reports_error_and_keeps_previous_valid_state(self):
        self.ocr, self.classified = "策论：基层治理材料", "essay"
        self.submit(upload=True)
        previous = self.snapshot()["practice_state"]
        with patch("lg_assistant.llm.json_chat", side_effect=ValueError("provider failed")):
            result = self.submit(practice={"agent": "essay", "action": "draft"})
        self.assertEqual(result["status"], "error")
        self.assertTrue(result["error"])
        self.assertEqual(result["archived_id"], "")
        self.assertEqual(self.snapshot()["practice_state"]["question"], previous["question"])
        self.assertEqual(self.snapshot()["practice_state"]["draft"], previous["draft"])

    def test_invalid_practice_payload_rejected_before_model_calls(self):
        for practice in ({"agent": "bogus"}, {"agent": "interview", "answer": "x" * 20001}, {"action": "invalid"}):
            response = self.client.post("/api/chat", data={"event": json.dumps({"practice": practice})})
            self.assertEqual(response.status_code, 400)
        self.observe.assert_not_called()

    def test_required_reference_miss_is_not_unreferenced_generation(self):
        self.ocr, self.classified = "策论：基层治理材料", "essay"
        document = exam_knowledge.add("alice", "不相关课程", "高等数学积分课程内容", "2026")
        result = self.submit(upload=True, documents=[document["id"]])
        self.assertEqual(result["status"], "error")
        self.assertIn("未检索到相关依据", result["text"])
        self.assertEqual(result["archived_id"], "")
        self.assertFalse(any("当前步骤" in messages[0]["content"] for messages, _ in self.calls))

    def test_provider_error_is_not_echoed_in_ocr_or_asr_result(self):
        from lg_assistant import llm, vision
        secret = "provider-detail-with-sensitive-token"
        with patch("lg_assistant.vision.observe", side_effect=vision.VisionError(secret)):
            result = self.submit(upload=True)
        self.assertEqual(result["status"], "error")
        self.assertNotIn(secret, json.dumps(result))
        self.ocr, self.classified = "面试：沟通群众诉求", "interview"
        self.submit(upload=True)
        with patch("lg_assistant.llm.asr", side_effect=llm.LLMError(secret)):
            result = self.submit(practice={"agent": "interview", "action": "answer"}, audio=True)
        self.assertEqual(result["status"], "error")
        self.assertNotIn(secret, json.dumps(result))


if __name__ == "__main__":
    unittest.main()
