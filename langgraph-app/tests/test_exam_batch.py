"""Offline regression coverage for multi-question photos and per-question isolation.

No real model, network, production database, or evaluation answer key is used.
The photo fixtures contain geometric bands so crop boundaries can be checked.
"""
from __future__ import annotations

import copy
from contextvars import ContextVar
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lg_assistant import call_metrics, config, exam_batch as batch


def region(page=1, bbox=None):
    return {"page": page, "bbox": bbox or [0, 0, 1000, 300]}


def visible(number="1", *, page=1, complete=True, missing=None, bbox=None,
            preview="计算 8/2", context=None):
    return {"number": number, "preview": preview, "complete": complete,
            "missing": list(missing or []), "regions": [region(page, bbox)],
            "context_regions": list(context or [])}


class PhotoBatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data_patch = patch.object(config, "DATA_DIR", self.root / "data")
        self.data_patch.start()
        self.real_recheck = batch.recheck_incomplete_questions
        self.recheck_patch = patch.object(batch, "recheck_incomplete_questions", return_value={"questions": []})
        self.recheck = self.recheck_patch.start()
        self.first = self.root / "page-one.png"
        self.second = self.root / "page-two.png"
        self.new = self.root / "new.png"
        self.non_image = self.root / "voice.wav"
        (self.root / "crops").mkdir()
        # Top and bottom halves make sibling pixels easy to detect.
        image = Image.new("RGB", (600, 1000), "red")
        image.paste("blue", (0, 500, 600, 1000))
        image.save(self.first)
        Image.new("RGB", (500, 700), "green").save(self.second)
        Image.new("RGB", (300, 400), "yellow").save(self.new)
        self.non_image.write_bytes(b"test audio")

    def tearDown(self):
        self.recheck_patch.stop()
        self.data_patch.stop()
        self.temp.cleanup()

    def initial(self, **overrides):
        return {"owner": "alice", "files": [str(self.first)], "text": "解答照片中的全部题目",
                "event": {"exam": {"use_public_knowledge": False, "allow_web": False}},
                **overrides}

    def inventoried(self, questions, **overrides):
        state = self.initial(**overrides)
        state.update(batch.prepare(state))
        with patch.object(batch, "detect_questions", return_value={"questions": questions, "notes": []}):
            state.update(batch.inventory(state))
        return state

    @staticmethod
    def answered(question, answer="B"):
        return {"id": question["id"], "label": question["label"], "page": question["page"],
                "status": "answered", "answer": answer, "explanation": "8/2=4",
                "needed": "", "review_notes": "已核对算式", "artifacts": [{"kind": "vision", "answerable": True}]}

    @staticmethod
    def summary(output):
        return next(a for a in output["result"]["artifacts"] if a["kind"] == "exam_batch")

    def test_followup_keeps_every_existing_original_photo(self):
        paths = [str(self.first), str(self.second)]
        out = batch.prepare(self.initial(files=[], last_images=paths))
        self.assertEqual(out["images"], paths)
        self.assertTrue(out["reused"])

    def test_fresh_photo_replaces_previous_batch_and_private_state(self):
        out = batch.prepare(self.initial(files=[str(self.new)], last_images=[str(self.first)],
            questions=[{"preview": "previous private question"}],
            references=[{"content": "previous private material"}],
            result={"text": "previous answer"}))
        self.assertEqual(out["images"], [str(self.new)])
        self.assertFalse(out["reused"])
        self.assertEqual(out["questions"], [])
        self.assertNotIn("previous private", json.dumps(out))
        self.assertNotIn("previous answer", json.dumps(out))

    def test_non_image_attachment_does_not_reuse_old_question_photo(self):
        out = batch.prepare(self.initial(files=[str(self.non_image)], last_images=[str(self.first)]))
        self.assertEqual(out["images"], [])
        self.assertTrue(out.get("error"))

    def test_invalid_exam_options_stop_before_inventory_or_models(self):
        with patch.object(batch, "detect_questions") as detect, patch.object(batch, "_single_graph") as child:
            output = batch.ORIGINAL_BATCH_GRAPH.invoke(self.initial(event={"exam": {"profile": []}}))
        detect.assert_not_called()
        child.assert_not_called()
        self.assertEqual(self.summary(output)["count"], 0)
        self.assertTrue(self.summary(output)["error"])

    def test_followup_skips_missing_files_without_losing_other_photos(self):
        out = batch.prepare(self.initial(files=[], last_images=[str(self.first), str(self.root / "removed.png"), str(self.second)]))
        self.assertEqual(out["images"], [str(self.first), str(self.second)])

    def test_inventory_retains_twelve_questions_without_small_fixed_cap(self):
        questions = [visible(str(i + 1), bbox=[0, i * 80, 1000, (i + 1) * 80]) for i in range(12)]
        out = self.inventoried(questions)
        self.assertEqual(len(out["questions"]), 12)
        self.assertEqual(len({q["id"] for q in out["questions"]}), 12)
        self.assertTrue(all(q["complete"] for q in out["questions"]))
        self.recheck.assert_not_called()

    def test_independent_inventory_recheck_clears_false_option_content_crop(self):
        first = visible("7", complete=False, missing=["右侧选项图形不可见"], bbox=[0, 0, 1000, 400])
        second = {"index": 1, "complete": True, "missing": [],
                  "regions": [region(bbox=[0, 0, 1000, 700])], "context_regions": []}
        with patch.object(batch, "recheck_incomplete_questions", return_value={"questions": [second]}) as review:
            out = self.inventoried([first])
        review.assert_called_once()
        question = out["questions"][0]
        self.assertEqual(question["id"], "p1-q1")
        self.assertEqual(question["label"], "第7题")
        self.assertTrue(question["complete"])
        self.assertEqual(question["missing"], [])
        self.assertEqual(question["failure_reason"], "")
        self.assertEqual(question["regions"][0]["bbox"], [0, 0, 1000, 700], "the verified full option region replaces the narrower first box")

    def test_independent_inventory_recheck_preserves_real_missing_content(self):
        first = visible("1", complete=False, missing=["底部未拍全"])
        second = {"index": 1, "complete": False, "missing": ["底部选项的右半幅图形确实未进入画面"],
                  "regions": [region()], "context_regions": []}
        with patch.object(batch, "recheck_incomplete_questions", return_value={"questions": [second]}):
            state = self.inventoried([first])
        self.assertFalse(state["questions"][0]["complete"])
        self.assertEqual(state["questions"][0]["missing"], second["missing"])
        with patch.object(batch, "_single_graph") as child:
            result = self.summary(batch.solve_all(state))
        child.assert_not_called()
        self.assertEqual(result["questions"][0]["status"], "needs_photo")

    def test_inventory_rechecks_partial_items_in_one_batch_without_reordering_or_deleting(self):
        first = [visible("9"), visible("10", complete=False, missing=["缺左边图形"]),
                 visible("11", complete=False, missing=["缺底部选项"])]
        reviewed = [{"index": 3, "complete": False, "missing": ["底部确实没有选项内容"], "regions": [region()]},
                    {"index": 2, "complete": True, "missing": [], "regions": [region()]},
                    {"index": 1, "complete": False, "missing": ["不应改完整题"], "regions": [region()]}]
        with patch.object(batch, "recheck_incomplete_questions", return_value={"questions": reviewed}) as review:
            out = self.inventoried(first)
        review.assert_called_once()
        self.assertEqual([q["index"] for q in review.call_args.args[2]], [2, 3], "complete items do not participate in the second read")
        self.assertEqual([q["id"] for q in out["questions"]], ["p1-q1", "p1-q2", "p1-q3"])
        self.assertEqual([q["label"] for q in out["questions"]], ["第9题", "第10题", "第11题"])
        self.assertEqual([q["complete"] for q in out["questions"]], [True, True, False])

    def test_inventory_recheck_receives_original_photos_and_independent_model_once(self):
        first = visible("1", complete=False, missing=["首次误判为缺图"])
        reply = {"questions": [{"index": 1, "complete": True, "missing": [], "regions": [region()]}]}
        photos = [str(self.first), str(self.second)]
        with patch.object(batch, "recheck_incomplete_questions", wraps=self.real_recheck), \
             patch.object(batch.vision, "_json_call", return_value=reply) as call:
            out = self.inventoried([first], files=photos)
        call.assert_called_once()
        self.assertEqual(call.call_args.args[2], photos, "the second reader gets full source photos, never question crops")
        self.assertEqual(call.call_args.kwargs["model"], config.EXAM_INDEPENDENT_MODEL)
        self.assertEqual(call.call_args.kwargs["retries"], 0, "inventory review permits one generation for the entire partial batch")
        payload = json.loads(call.call_args.args[1])
        self.assertEqual(payload["questions"][0]["index"], 1)
        self.assertEqual(payload["questions"][0]["regions"], first["regions"])
        self.assertNotIn("首次误判为缺图", call.call_args.args[1], "initial missing claims do not bias the independent read")
        self.assertTrue(out["questions"][0]["complete"])

    def test_invalid_inventory_recheck_cannot_release_or_remove_a_partial_question(self):
        valid = {"index": 1, "complete": True, "missing": [], "regions": [region()]}
        invalid = [None, {"questions": None}, {"questions": []},
                   {"questions": [{**valid, "index": True}]}, {"questions": [{**valid, "index": 2}]},
                   {"questions": [{**valid, "complete": "true"}]}, {"questions": [{**valid, "missing": "none"}]},
                   {"questions": [{**valid, "missing": [17]}]}, {"questions": [{**valid, "missing": ["缺少图形"]}]},
                   {"questions": [{**valid, "complete": False}]}, {"questions": [{**valid, "regions": []}]},
                   {"questions": [{**valid, "regions": [region(page=2)]}]},
                   {"questions": [{**valid, "regions": [region(bbox=[0, 0, 1500, 1000])]}]},
                   {"questions": [{**valid, "context_regions": "invalid"}]}, {"questions": [valid, valid]}]
        for reply in invalid:
            with self.subTest(reply=reply), patch.object(batch, "recheck_incomplete_questions", return_value=reply) as review:
                out = self.inventoried([visible("1", complete=False, missing=["原始残缺提示"] )])
                review.assert_called_once()
                self.assertEqual(len(out["questions"]), 1)
                self.assertEqual(out["questions"][0]["id"], "p1-q1")
                self.assertFalse(out["questions"][0]["complete"])
                self.assertEqual(out["questions"][0]["missing"], ["原始残缺提示"])

    def test_inventory_recheck_outage_keeps_original_partial_without_batch_error(self):
        with patch.object(batch, "recheck_incomplete_questions", side_effect=batch.llm.LLMError("connection failed")) as review:
            out = self.inventoried([visible("1", complete=False, missing=["原始补拍部位"])])
        review.assert_called_once()
        self.assertFalse(out["questions"][0]["complete"])
        self.assertEqual(out["questions"][0]["missing"], ["原始补拍部位"])
        self.assertEqual(out["error"], "")

    def test_repeated_printed_numbers_on_different_pages_remain_distinct(self):
        out = self.inventoried([visible("1"), visible("1", page=2)], files=[str(self.first), str(self.second)])
        self.assertEqual(len(out["questions"]), 2)
        self.assertNotEqual(out["questions"][0]["id"], out["questions"][1]["id"])
        self.assertEqual([q["page"] for q in out["questions"]], [1, 2])

    def test_duplicate_numbers_include_photo_number_in_text_and_speech(self):
        state = self.inventoried([visible("1"), visible("1", page=2)], files=[str(self.first), str(self.second)])
        with patch.object(batch, "solve_question", side_effect=lambda s,q,b,d:self.answered(q)):
            output = batch.solve_all(state)
        for field in ("text", "speech"):
            self.assertIn("第1张照片", output["result"][field])
            self.assertIn("第2张照片", output["result"][field])
            self.assertIn("第1题", output["result"][field])

    def test_missing_printed_number_gets_a_stable_display_label(self):
        out = self.inventoried([visible("")])
        self.assertTrue(out["questions"][0]["label"].startswith("第1题"))
        self.assertTrue(out["questions"][0]["id"])

    def test_clipped_question_keeps_specific_retake_location(self):
        out = self.inventoried([visible("1"), visible("2", complete=False,
            missing=["照片底部的 C、D 选项没有拍全"], bbox=[0, 600, 1000, 1000])])
        question = out["questions"][1]
        self.assertFalse(question["complete"])
        self.assertIn("C、D", "；".join(question["missing"]))

    def test_reported_missing_content_prevents_complete_flag(self):
        out = self.inventoried([visible("1", complete=True, missing=["题干左侧被裁切"])])
        self.assertFalse(out["questions"][0]["complete"])

    def test_invalid_missing_field_stays_partial_and_keeps_specific_retake_hint(self):
        hint = "照片右侧的 D 选项被裁切"
        for malformed in (hint, [hint, 17]):
            with self.subTest(missing=malformed):
                question = visible("1", complete=True)
                question["missing"] = malformed
                state = self.inventoried([question])
                self.assertFalse(state["questions"][0]["complete"])
                with patch.object(batch, "solve_question") as solve, patch.object(batch, "_single_graph") as graph:
                    output = batch.solve_all(state)
                solve.assert_not_called()
                graph.assert_not_called()
                row = self.summary(output)["questions"][0]
                self.assertEqual(row["status"], "needs_photo")
                self.assertFalse(row["answer"])
                self.assertIn(hint, row["needed"])

    def test_nonboolean_completeness_is_not_treated_as_true(self):
        question = visible("1")
        question["complete"] = "false"
        out = self.inventoried([question])
        self.assertFalse(out["questions"][0]["complete"])

    def test_empty_crop_rectangle_cannot_be_solved_as_a_complete_question(self):
        out = self.inventoried([visible("1", bbox=[300, 200, 300, 800])])
        if out.get("questions"):
            self.assertFalse(out["questions"][0]["complete"])
        else:
            self.assertTrue(out.get("error") or out.get("inventory_notes"))

    def test_question_requires_a_visible_location_to_avoid_whole_page_fallback(self):
        question = visible("1")
        question["regions"] = []
        out = self.inventoried([question])
        self.assertFalse(out["questions"][0]["complete"])

    def test_context_region_is_kept_separate_from_question_region(self):
        context = [region(1, [0, 0, 1000, 200])]
        out = self.inventoried([visible("3", bbox=[0, 500, 1000, 800], context=context)])
        question = out["questions"][0]
        self.assertEqual(question["context_regions"], context)
        self.assertNotEqual(question["regions"], context)

    def test_unknown_page_cannot_silently_read_another_photo(self):
        out = self.inventoried([visible("1", page=3)])
        if out.get("questions"):
            self.assertFalse(out["questions"][0]["complete"])
        else:
            self.assertTrue(out.get("error") or out.get("inventory_notes"))

    def test_zero_visible_questions_do_not_invent_a_question(self):
        out = self.inventoried([])
        self.assertEqual(out["questions"], [])
        with patch.object(batch, "solve_question") as solve:
            final = batch.solve_all(out)
        solve.assert_not_called()
        artifact = next(a for a in final["result"]["artifacts"] if a["kind"] == "exam_batch")
        self.assertEqual(artifact["count"], 0)
        self.assertEqual(artifact["questions"], [])

    def test_mixed_photo_solves_complete_questions_and_marks_only_clipped_one(self):
        state = self.inventoried([visible("1"), visible("2", complete=False,
            missing=["右下角的 D 选项缺失"], bbox=[0, 700, 1000, 1000]),
            visible("3", bbox=[0, 400, 1000, 650])])
        original_solve = batch.solve_question
        def solve(s, question, backend, crop_dir):
            if not question["complete"]:
                return original_solve(s, question, backend, crop_dir)
            return self.answered(question, "A" if question["label"] == "第1题" else "C")
        with patch.object(batch, "solve_question", side_effect=solve) as mocked:
            output = batch.solve_all(state)
        artifact = self.summary(output)
        self.assertEqual((artifact["count"], artifact["answered_count"], artifact["needs_photo_count"]), (3, 2, 1))
        self.assertEqual(sum(call.args[1]["complete"] for call in mocked.call_args_list), 2)
        clipped = next(q for q in artifact["questions"] if q["label"] == "第2题")
        self.assertEqual(clipped["status"], "needs_photo")
        self.assertFalse(clipped["answer"])
        self.assertIn("D", clipped["needed"])
        self.assertIn("第1题", output["result"]["text"])
        self.assertIn("第2题", output["result"]["text"])
        self.assertIn("第3题", output["result"]["text"])
        self.assertEqual(output["last_images"], [str(self.first)])

    def test_all_clipped_questions_skip_both_solving_backends(self):
        state = self.inventoried([visible("1", complete=False, missing=["缺少题干上半部"]),
            visible("2", complete=False, missing=["缺少表格右半部"])])
        with patch.object(batch, "_single_graph") as solve:
            output = batch.solve_all(state)
        solve.assert_not_called()
        artifact = self.summary(output)
        self.assertEqual(artifact["answered_count"], 0)
        self.assertEqual(artifact["needs_photo_count"], 2)
        self.assertTrue(all(q["status"] == "needs_photo" and not q["answer"] for q in artifact["questions"]))

    def test_one_question_failure_does_not_cancel_other_answers(self):
        state = self.inventoried([visible("1"), visible("2", bbox=[0, 400, 1000, 600]),
            visible("3", bbox=[0, 700, 1000, 1000])])
        def solve(s, question, backend, crop_dir):
            if question["label"] == "第2题":
                raise RuntimeError("simulated one-question failure")
            return self.answered(question)
        with patch.object(batch, "solve_question", side_effect=solve) as solve_mock:
            output = batch.solve_all(state)
        artifact = self.summary(output)
        self.assertEqual(solve_mock.call_count, 3)
        self.assertEqual(artifact["answered_count"], 2)
        self.assertEqual([q["status"] for q in artifact["questions"]], ["answered", "error", "answered"])
        self.assertFalse(artifact["questions"][1]["answer"])

    def test_unresolved_question_is_not_claimed_as_answered_or_clipped(self):
        state = self.inventoried([visible("1"), visible("2", bbox=[0, 600, 1000, 1000])])
        def solve(s, question, backend, crop_dir):
            row = self.answered(question)
            if question["label"] == "第2题":
                row.update(status="unresolved", answer="", needed="两个选项依据仍有冲突")
            return row
        with patch.object(batch, "solve_question", side_effect=solve):
            artifact = self.summary(batch.solve_all(state))
        self.assertEqual(artifact["answered_count"], 1)
        self.assertEqual(artifact["needs_photo_count"], 0)
        self.assertEqual(artifact["questions"][1]["status"], "unresolved")

    def test_solve_all_keeps_every_original_page_for_later_followup(self):
        original = [str(self.first), str(self.second)]
        state = self.inventoried([visible("1"), visible("2", page=2)], files=original)
        with patch.object(batch, "solve_question", side_effect=lambda s,q,b,d:self.answered(q)):
            output = batch.solve_all(state)
        self.assertEqual(output["last_images"], original)
        self.assertTrue(all(Path(p).is_file() for p in original))

    def test_temporary_question_crops_are_removed_after_batch(self):
        state = self.inventoried([visible("1"), visible("2", bbox=[0, 700, 1000, 1000])])
        directories = []
        def solve(s, question, backend, crop_dir):
            directories.append(Path(crop_dir))
            self.assertTrue(Path(crop_dir).is_dir())
            return self.answered(question)
        with patch.object(batch, "solve_question", side_effect=solve):
            output = batch.solve_all(state)
        self.assertTrue(directories)
        self.assertTrue(all(not path.exists() for path in directories))
        self.assertTrue(self.first.is_file())
        self.assertEqual(output["last_images"], [str(self.first)])

    def test_actual_worker_call_measurements_are_merged_without_duplicates(self):
        state = self.inventoried([visible("1"), visible("2", bbox=[0, 400, 1000, 600]),
            visible("3", bbox=[0, 700, 1000, 1000])])
        def solve(s, question, backend, crop_dir):
            call_metrics.record("worker-" + question["id"], .01, {"usage": {"input_tokens": 7, "output_tokens": 3}})
            return self.answered(question)
        with call_metrics.capture() as calls:
            call_metrics.record("inventory-call", .01)
            with patch.object(batch, "solve_question", side_effect=solve):
                batch.solve_all(state)
        self.assertEqual(len(calls), 4)
        self.assertCountEqual([row["model"] for row in calls], ["inventory-call", *["worker-" + q["id"] for q in state["questions"]]])
        self.assertTrue(all(row["usage"]["prompt_tokens"] == 7 for row in calls if row["model"].startswith("worker-")))

    def test_failed_worker_does_not_drop_its_already_measured_call(self):
        state = self.inventoried([visible("1"), visible("2", bbox=[0, 700, 1000, 1000])])
        def solve(s, question, backend, crop_dir):
            call_metrics.record("worker-" + question["id"], .01, error=question["label"] == "第2题")
            if question["label"] == "第2题":
                raise RuntimeError("failure after an actual call was measured")
            return self.answered(question)
        with call_metrics.capture() as calls, patch.object(batch, "solve_question", side_effect=solve):
            output = batch.solve_all(state)
        self.assertEqual(len(calls), 2)
        self.assertEqual(sum(row["status"] == "error" for row in calls), 1)
        self.assertEqual([q["status"] for q in self.summary(output)["questions"]], ["answered", "error"])

    def test_workers_do_not_inherit_langgraph_or_custom_context(self):
        from langchain_core.runnables.config import var_child_runnable_config
        custom = ContextVar("batch_test_private_context", default=None)
        parent_custom = custom.set("parent-private-context")
        parent_graph = var_child_runnable_config.set({"configurable": {"thread_id": "parent-private-thread"}})
        state = self.inventoried([visible("1"), visible("2", bbox=[0, 700, 1000, 1000])])
        observed = []
        def solve(s, question, backend, crop_dir):
            observed.append((custom.get(), var_child_runnable_config.get()))
            # Even when the executor reuses one OS thread, its next task must
            # not inherit context assigned by this sibling question.
            custom.set("current-question-context")
            var_child_runnable_config.set({"configurable": {"thread_id": question["id"]}})
            return self.answered(question)
        try:
            with patch.object(config, "EXAM_BATCH_WORKERS", 1), patch.object(batch, "solve_question", side_effect=solve):
                batch.solve_all(state)
            self.assertEqual(observed, [(None, None), (None, None)])
            self.assertEqual(custom.get(), "parent-private-context")
            self.assertEqual(var_child_runnable_config.get()["configurable"]["thread_id"], "parent-private-thread")
        finally:
            var_child_runnable_config.reset(parent_graph)
            custom.reset(parent_custom)

    def test_original_and_jev_batch_graphs_use_requested_child_backend(self):
        for backend, compiled in (("original", batch.ORIGINAL_BATCH_GRAPH), ("jev", batch.JEV_BATCH_GRAPH)):
            with self.subTest(backend=backend):
                original_solve = batch.solve_question
                def solve_question(s, question, selected_backend, crop_dir):
                    if not question["complete"]:
                        return original_solve(s, question, selected_backend, crop_dir)
                    return self.answered(question)
                with patch.object(batch, "detect_questions", return_value={"questions": [visible("1"), visible("2", complete=False, missing=["底部被裁切"])], "notes": []}), \
                     patch.object(batch, "solve_question", side_effect=solve_question) as solve:
                    output = compiled.invoke(self.initial(exam_backend=backend))
                self.assertEqual(output["result"]["exam_backend"], backend)
                self.assertEqual(sum(call.args[1]["complete"] for call in solve.call_args_list), 1)
                self.assertTrue(all(call.args[2] == backend for call in solve.call_args_list))
                self.assertEqual(self.summary(output)["needs_photo_count"], 1)

    def test_actual_child_sees_current_question_crop_and_shared_context_only(self):
        state = self.inventoried([visible("1", bbox=[0, 0, 1000, 300],
            context=[region(2, [0, 0, 1000, 200])]),
            visible("2", bbox=[0, 700, 1000, 1000])], files=[str(self.first), str(self.second)])
        state.update(references=[{"content": "previous private evidence"}],
                     draft={"candidate": "previous answer"}, final={"answer": "previous final"},
                     perception={"question": "previous OCR"})
        captured = []
        def invoke(child):
            captured.append(copy.deepcopy(child))
            colors = []
            for path in child["files"]:
                with Image.open(path) as image:
                    colors.append(image.convert("RGB").getpixel((image.width//2, image.height//2)))
                self.assertNotIn(path, [str(self.first), str(self.second)])
            self.assertCountEqual(colors, [(255, 0, 0), (0, 128, 0)])
            return {"final": {"answerable": True, "answer": "B", "explanation": "该题计算为 4", "review_notes": "核对当前题"},
                    "result": {"text": "答案 B", "speech": "答案 B", "artifacts": [{"kind": "vision", "answerable": True}]}}
        child_graph = Mock()
        child_graph.invoke.side_effect = invoke
        with patch.object(batch, "_single_graph", return_value=child_graph) as select:
            row = batch.solve_question(state, state["questions"][0], "original", self.root / "crops")
        self.assertEqual(row["status"], "answered")
        self.assertEqual(row["answer"], "B")
        self.assertEqual(row["id"], state["questions"][0]["id"])
        select.assert_called_once_with("original")
        self.assertEqual(captured[0]["owner"], "alice")
        self.assertEqual(captured[0]["event"], state["event"])
        self.assertNotIn("previous private", json.dumps(captured))
        self.assertNotIn("previous answer", json.dumps(captured))
        self.assertNotIn("previous final", json.dumps(captured))
        self.assertNotIn("previous OCR", json.dumps(captured))
        self.assertNotIn("questions", captured[0])

    def test_child_nonanswer_cannot_leak_a_candidate_answer(self):
        state = self.inventoried([visible("1")])
        child_graph = Mock()
        child_graph.invoke.return_value = {"final": {"answerable": False, "answer": "B", "explanation": "未确定", "needed": "缺少依据"},
            "result": {"text": "无法作答", "artifacts": []}}
        with patch.object(batch, "_single_graph", return_value=child_graph):
            row = batch.solve_question(state, state["questions"][0], "jev", self.root / "crops")
        self.assertEqual(row["status"], "unresolved")
        self.assertFalse(row["answer"])
        self.assertIn("缺少依据", row["needed"])

    def test_typed_reasoning_failure_does_not_turn_retake_words_into_photo_failure(self):
        state = self.inventoried([visible("1")])
        for failure_reason in ("reasoning_unresolved", "evidence_missing", "review_conflict"):
            with self.subTest(failure_reason=failure_reason):
                child_graph = Mock()
                child_graph.invoke.return_value = {"final": {"answerable": False, "answer": "B",
                    "failure_reason": failure_reason, "needed": "原图并非模糊，不需要补拍，但解题依据仍有冲突。"},
                    "result": {"artifacts": []}}
                with patch.object(batch, "_single_graph", return_value=child_graph):
                    row = batch.solve_question(state, state["questions"][0], "jev", self.root / "crops")
                self.assertEqual(row["status"], "unresolved")
                self.assertEqual(row["failure_reason"], failure_reason)
                self.assertFalse(row["answer"])

    def test_typed_photo_failure_does_not_require_retake_keywords(self):
        state = self.inventoried([visible("1")])
        for failure_reason in ("image_incomplete", "image_unreadable"):
            with self.subTest(failure_reason=failure_reason):
                child_graph = Mock()
                child_graph.invoke.return_value = {"final": {"answerable": False, "answer": "",
                    "failure_reason": failure_reason, "needed": "右侧 D 选项下半部分无法辨认。"},
                    "result": {"artifacts": []}}
                with patch.object(batch, "_single_graph", return_value=child_graph):
                    row = batch.solve_question(state, state["questions"][0], "jev", self.root / "crops")
                self.assertEqual(row["status"], "needs_photo")
                self.assertEqual(row["failure_reason"], failure_reason)
                self.assertIn("D", row["needed"])

    def test_incomplete_inventory_preserves_typed_reason_in_question_and_batch_row(self):
        state = self.inventoried([visible("1", complete=False, missing=["右侧 D 选项未进入画面"])])
        self.assertEqual(state["questions"][0]["failure_reason"], "image_incomplete")
        with patch.object(batch, "_single_graph") as child:
            output = batch.solve_all(state, "jev")
        child.assert_not_called()
        row = self.summary(output)["questions"][0]
        self.assertEqual(row["status"], "needs_photo")
        self.assertEqual(row["failure_reason"], "image_incomplete")
        self.assertIn("D", row["needed"])

    def test_typed_second_read_image_failure_is_not_reported_as_service_error(self):
        state = self.inventoried([visible("1")])
        child_graph = Mock()
        child_graph.invoke.return_value = {"error": "题面不完整", "final": {"answerable": False,
            "failure_reason": "image_unreadable", "needed": "第四个符号无法辨认。"}, "result": {"artifacts": []}}
        with patch.object(batch, "_single_graph", return_value=child_graph):
            row = batch.solve_question(state, state["questions"][0], "jev", self.root / "crops")
        self.assertEqual(row["status"], "needs_photo")
        self.assertEqual(row["failure_reason"], "image_unreadable")

    def test_other_typed_error_is_not_guessed_from_retake_wording(self):
        state = self.inventoried([visible("1")])
        child_graph = Mock()
        child_graph.invoke.return_value = {"error": "连接中断", "final": {"answerable": False,
            "failure_reason": "upstream_failure", "needed": "服务建议补拍重试。"}, "result": {"artifacts": []}}
        with patch.object(batch, "_single_graph", return_value=child_graph):
            row = batch.solve_question(state, state["questions"][0], "jev", self.root / "crops")
        self.assertEqual(row["status"], "error")
        self.assertEqual(row["failure_reason"], "upstream_failure")

    def test_second_read_can_mark_a_previously_complete_question_for_retake(self):
        state = self.inventoried([visible("1")])
        child_graph = Mock()
        child_graph.invoke.return_value = {"final": {"answerable": False, "answer": "", "needed": "右边图形未拍全，请补拍第 1 题右侧"},
            "result": {"artifacts": []}}
        with patch.object(batch, "_single_graph", return_value=child_graph):
            row = batch.solve_question(state, state["questions"][0], "original", self.root / "crops")
        self.assertEqual(row["status"], "needs_photo")
        self.assertFalse(row["answer"])
        self.assertIn("右侧", row["needed"])
        self.assertEqual(row["failure_reason"], "", "legacy results still use retake wording without inventing a typed reason")

    def test_crop_coordinates_follow_exif_upright_orientation(self):
        sideways = self.root / "sideways.jpg"
        image = Image.new("RGB", (100, 200), "red")
        image.paste("blue", (0, 100, 100, 200))
        exif = Image.Exif()
        exif[274] = 6
        image.save(sideways, quality=95, exif=exif)
        # The upright image is 200x100 with blue left / red right.
        state = self.inventoried([visible("1", bbox=[500, 0, 1000, 1000])], files=[str(sideways)])
        def invoke(child):
            with Image.open(child["files"][0]) as crop:
                red, green, blue = crop.convert("RGB").getpixel((crop.width//2, crop.height//2))
                self.assertGreater(red, 200)
                self.assertLess(green, 50)
                self.assertLess(blue, 50)
            return {"final": {"answerable": True, "answer": "B", "explanation": "核对当前题"}, "result": {"artifacts": []}}
        child_graph = Mock()
        child_graph.invoke.side_effect = invoke
        with patch.object(batch, "_single_graph", return_value=child_graph):
            row = batch.solve_question(state, state["questions"][0], "original", self.root / "crops")
        self.assertEqual(row["status"], "answered")

    def test_child_files_are_different_for_sibling_questions(self):
        state = self.inventoried([visible("1", bbox=[0, 0, 1000, 300]),
            visible("2", bbox=[0, 700, 1000, 1000])])
        payloads = []
        colors = []
        def invoke(child):
            payloads.append(copy.deepcopy(child))
            with Image.open(child["files"][0]) as image:
                colors.append(image.convert("RGB").getpixel((image.width//2, image.height//2)))
            return {"final": {"answerable": True, "answer": "B", "explanation": "计算结果为 4"},
                    "result": {"artifacts": []}}
        child_graph = Mock()
        child_graph.invoke.side_effect = invoke
        with patch.object(batch, "_single_graph", return_value=child_graph):
            for question in state["questions"]:
                batch.solve_question(state, question, "original", self.root / "crops")
        self.assertNotEqual(payloads[0]["files"], payloads[1]["files"])
        self.assertEqual(colors, [(255, 0, 0), (0, 0, 255)])


if __name__ == "__main__":
    unittest.main()
