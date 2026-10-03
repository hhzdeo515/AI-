"""Batch progress remains truthful through completion, failure and thread handoff."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lg_assistant import progress, exam_batch


class BatchProgressTests(unittest.TestCase):
    def setUp(self):
        self.rid = "batch-progress-" + uuid4().hex
        progress.unbind()

    def tearDown(self):
        progress.unbind()

    def test_bound_request_reports_completed_questions_without_changing_phase(self):
        progress.begin(self.rid, "exam", "recognize")
        progress.bind(self.rid)
        progress.batch(3, 0)
        progress.mark("solve")
        progress.batch(3, 2)
        snapshot = progress.snapshot(self.rid)
        self.assertEqual(snapshot["batch"], {"total": 3, "done": 2})
        self.assertEqual([step["id"] for step in snapshot["steps"] if step["state"] == "active"], ["solve"])
        self.assertFalse(snapshot["finished"])

    def test_begin_clears_previous_batch_for_the_same_request_id(self):
        progress.begin(self.rid, "exam", "solve")
        progress.batch(8, 8, self.rid)
        progress.finish(self.rid)
        progress.begin(self.rid, "exam", "recognize")
        snapshot = progress.snapshot(self.rid)
        self.assertIsNone(snapshot["batch"])
        self.assertFalse(snapshot["finished"])
        self.assertFalse(snapshot["error"])
        self.assertEqual(len(snapshot["timings"]["steps"]), 1)

    def test_finish_never_invents_missing_completions(self):
        for error in (False, True):
            with self.subTest(error=error):
                progress.begin(self.rid, "exam", "solve")
                progress.batch(5, 2, self.rid)
                progress.finish(self.rid, error=error)
                snapshot = progress.snapshot(self.rid)
                self.assertTrue(snapshot["finished"])
                self.assertEqual(snapshot["error"], error)
                self.assertEqual(snapshot["batch"], {"total": 5, "done": 2})
                progress.batch(5, 5, self.rid)
                self.assertEqual(progress.snapshot(self.rid)["batch"], {"total": 5, "done": 2})

    def test_completed_batch_and_empty_batch_keep_exact_counts(self):
        for total in (0, 4):
            with self.subTest(total=total):
                progress.begin(self.rid, "exam", "verify")
                progress.batch(total, total, self.rid)
                progress.finish(self.rid)
                self.assertEqual(progress.snapshot(self.rid)["batch"], {"total": total, "done": total})

    def test_batch_updates_do_not_add_timing_marks(self):
        with patch("lg_assistant.progress.time.monotonic", side_effect=[10.0, 12.0, 16.0, 20.0]):
            progress.begin(self.rid, "exam", "recognize")
            progress.batch(2, 0, self.rid)
            progress.mark("solve", self.rid)
            progress.batch(2, 1, self.rid)
            progress.mark("verify", self.rid)
            progress.batch(2, 2, self.rid)
            progress.finish(self.rid)
        self.assertEqual(progress.timings(self.rid), {
            "steps": [{"id": "recognize", "ms": 2000}, {"id": "solve", "ms": 4000},
                      {"id": "verify", "ms": 4000}], "total_ms": 10000})

    def test_snapshot_batch_cannot_mutate_the_record(self):
        progress.begin(self.rid, "exam", "solve")
        progress.batch(3, 1, self.rid)
        progress.snapshot(self.rid)["batch"]["done"] = 3
        self.assertEqual(progress.snapshot(self.rid)["batch"]["done"], 1)

    def test_unbound_unknown_and_unstarted_requests_are_noops(self):
        progress.batch(3, 1)
        progress.batch(3, 1, self.rid)
        self.assertIsNone(progress.snapshot(self.rid))
        progress.bind(self.rid)
        progress.batch(3, 1)
        self.assertIsNone(progress.snapshot(self.rid))

    def test_thread_binding_does_not_leak_to_worker_and_explicit_id_works(self):
        progress.begin(self.rid, "exam", "solve")
        progress.bind(self.rid)
        progress.batch(2, 0)
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(progress.batch, 2, 1).result()
            self.assertEqual(progress.snapshot(self.rid)["batch"]["done"], 0)
            pool.submit(progress.batch, 2, 1, self.rid).result()
        self.assertEqual(progress.snapshot(self.rid)["batch"]["done"], 1)

    def test_invalid_counts_are_rejected_without_corrupting_previous_report(self):
        progress.begin(self.rid, "exam", "solve")
        progress.batch(3, 1, self.rid)
        for total, done in ((-1, 0), (3, -1), (3, 4), (3, 1.5), (3, True), ("3", 1)):
            with self.subTest(total=total, done=done), self.assertRaises(ValueError):
                progress.batch(total, done, self.rid)
        self.assertEqual(progress.snapshot(self.rid)["batch"], {"total": 3, "done": 1})

    def test_single_question_worker_reports_real_phase_and_clears_binding(self):
        progress.begin(self.rid, "exam", "solve")
        question = {"complete": True}
        state = {"request_id": self.rid, "questions": [question]}
        def solve(*args):
            self.assertEqual(progress.current(), self.rid)
            progress.mark("verify")
            return {"answer": "B"}
        with patch.object(exam_batch, "solve_question", side_effect=solve):
            row, calls = exam_batch._solve_isolated(state, question, "jev", "unused")
        self.assertEqual(row["answer"], "B")
        self.assertEqual(progress.current(), "")
        self.assertEqual([s["id"] for s in progress.snapshot(self.rid)["steps"] if s["state"] == "active"], ["verify"])

    def test_parallel_siblings_do_not_overwrite_aggregate_phase(self):
        progress.begin(self.rid, "exam", "solve")
        question = {"complete": True}
        state = {"request_id": self.rid, "questions": [question, question]}
        def solve(*args):
            self.assertEqual(progress.current(), "")
            progress.mark("verify")
            return {"answer": "B"}
        with patch.object(exam_batch, "solve_question", side_effect=solve):
            exam_batch._solve_isolated(state, question, "jev", "unused")
        self.assertEqual([s["id"] for s in progress.snapshot(self.rid)["steps"] if s["state"] == "active"], ["solve"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
