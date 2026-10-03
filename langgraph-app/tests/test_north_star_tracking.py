"""Contract tests: synthetic telemetry only, no model or network execution."""

from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from evaluation.north_star_tracking import compare_expected, evaluate_case
from scripts.build_north_star_event_cases import build_items, event, session


class NorthStarEventContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.items = build_items()
        cls.by_id = {item["id"]: item for item in cls.items}

    def test_all_hand_authored_expected_outputs(self) -> None:
        self.assertGreaterEqual(len(self.items), 24)
        self.assertEqual(len(self.items), len(self.by_id))
        for item in self.items:
            with self.subTest(case_id=item["id"]):
                actual = evaluate_case(item["input"])
                self.assertEqual(compare_expected(actual, item["expectedOutput"]), [])
                json.dumps(actual, allow_nan=False, sort_keys=True)

    def test_synthetic_scope_is_explicit_in_every_item(self) -> None:
        for item in self.items:
            with self.subTest(case_id=item["id"]):
                self.assertEqual(item["metadata"]["data_origin"], "synthetic_event_fixture")
                self.assertEqual(item["metadata"]["score_scope"], "event_contract")
                self.assertTrue(item["metadata"]["not_real_user_data"])
                for window in item["input"]["sessions"]:
                    for tracked_event in window["events"]:
                        self.assertEqual(tracked_event["source"], "synthetic_fixture")

    def test_zero_attempts_are_participants_not_missing_data(self) -> None:
        result = evaluate_case(self.by_id["event-zero-attempts"]["input"])
        self.assertEqual(result["groups"]["product"]["M"], 1)
        self.assertEqual(result["groups"]["product"]["C"], 0)
        self.assertIsNone(result["groups"]["product"]["adoption_rate"])
        self.assertEqual(result["delta"], 0)

    def test_pooled_metrics_algebra(self) -> None:
        result = evaluate_case(self.by_id["event-pooled-adoption-rate"]["input"])
        p, o = result["groups"]["product"], result["groups"]["original"]
        self.assertEqual(p["adoption_rate"], 0.25)
        self.assertNotEqual(p["adoption_rate"], 0.5)
        self.assertEqual(result["delta"], p["C"] / p["M"] - o["C"] / o["M"])
        self.assertEqual(result["delta"], p["per_user_attempts"] * p["adoption_rate"]
                         - o["per_user_attempts"] * o["adoption_rate"])

    def test_missing_pair_does_not_create_zero_original(self) -> None:
        result = evaluate_case(self.by_id["event-missing-original-method"]["input"])
        self.assertEqual(result["status"], "pending")
        self.assertIsNone(result["groups"]["original"]["C"])
        self.assertIsNone(result["delta"])

    def test_duplicate_window_rejected_before_aggregation(self) -> None:
        result = evaluate_case(self.by_id["event-duplicate-participant-window"]["input"])
        self.assertEqual(result["status"], "invalid")
        self.assertEqual(result["groups"]["product"]["M"], 1)
        self.assertIsNone(result["groups"]["product"]["Q"])
        self.assertIsNone(result["delta"])

    def test_deadline_uses_occurrence_time_only(self) -> None:
        late_receive = evaluate_case(self.by_id["event-late-reception-in-time"]["input"])
        late_occurrence = evaluate_case(self.by_id["event-confirm-after-deadline"]["input"])
        self.assertEqual(late_receive["groups"]["product"]["C"], 1)
        self.assertEqual(late_occurrence["groups"]["product"]["C"], 0)
        self.assertEqual(late_occurrence["sessions"][0]["after_deadline_events"], 1)

    def test_retries_do_not_create_extra_questions(self) -> None:
        result = evaluate_case(self.by_id["event-same-question-retry"]["input"])
        self.assertEqual(result["groups"]["product"]["Q"], 1)
        self.assertEqual(result["groups"]["product"]["C"], 1)

    def test_revisions_require_current_answer_confirmation(self) -> None:
        revised = evaluate_case(self.by_id["event-revise-without-reconfirm"]["input"])
        confirmed = evaluate_case(self.by_id["event-revise-and-reconfirm"]["input"])
        self.assertEqual(revised["sessions"][0]["questions"]["question-1"]["adoption_status"], "unconfirmed")
        self.assertEqual(revised["groups"]["product"]["C"], 0)
        self.assertEqual(confirmed["groups"]["product"]["C"], 1)

    def test_deduplication_transport_metadata(self) -> None:
        result = evaluate_case(self.by_id["event-retransmission-new-receive-time"]["input"])
        self.assertEqual(result["sessions"][0]["duplicate_events"], 1)
        self.assertEqual(result["groups"]["product"]["C"], 1)

    def test_input_is_not_mutated(self) -> None:
        item = copy.deepcopy(self.by_id["event-out-of-order-delivery"]["input"])
        before = json.dumps(item, sort_keys=True)
        evaluate_case(item)
        self.assertEqual(json.dumps(item, sort_keys=True), before)

    def test_evaluation_is_stable(self) -> None:
        for item in self.items:
            self.assertEqual(json.dumps(evaluate_case(item["input"]), sort_keys=True),
                             json.dumps(evaluate_case(item["input"]), sort_keys=True))

    def test_unknown_does_not_become_non_adopted(self) -> None:
        result = evaluate_case(self.by_id["event-missing-adoption-state"]["input"])
        self.assertEqual(result["status"], "pending")
        self.assertIsNone(result["groups"]["product"]["C"])
        self.assertEqual(result["sessions"][0]["questions"]["question-1"]["adoption_status"], "unknown")

    def test_original_method_has_no_presentation_requirement(self) -> None:
        result = evaluate_case(self.by_id["event-original-self-answer-without-presentation"]["input"])
        original_events = self.by_id["event-original-self-answer-without-presentation"]["input"]["sessions"][1]["events"]
        self.assertFalse(any(e["event_name"] == "exam_result_presented" for e in original_events))
        self.assertEqual(result["groups"]["original"]["C"], 1)

    def test_invalid_top_level_and_empty_data(self) -> None:
        self.assertEqual(evaluate_case(None)["status"], "invalid")
        self.assertIsNone(evaluate_case({"sessions": []})["delta"])
        self.assertEqual(evaluate_case({"sessions": []})["status"], "pending")

    def test_malformed_participant_ids_are_invalid_without_crashing(self) -> None:
        product = session("product")
        product["user_id"] = {"unexpected": "object"}
        result = evaluate_case({"sessions": [product, session("original")]})
        self.assertEqual(result["status"], "invalid")
        self.assertIn("missing_user_id", result["issues"])
        self.assertEqual(result["groups"]["product"]["M"], 0)

    def test_sequence_time_disagreement_is_pending(self) -> None:
        product = session("product")
        product["events"][-2]["elapsed_ms"] = 115
        result = evaluate_case({"sessions": [product, session("original")]})
        self.assertEqual(result["status"], "pending")
        self.assertIn("state_time_conflict", result["issues"])
        self.assertIn("adoption_answer_mismatch", result["issues"])

    def test_non_adopted_snapshot_can_close_known_failure(self) -> None:
        result = evaluate_case(self.by_id["event-end-snapshot-known-non-adoption"]["input"])
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["groups"]["product"]["Q"], 1)
        self.assertEqual(result["groups"]["product"]["C"], 0)

    def test_adopted_snapshot_cannot_fabricate_confirmation(self) -> None:
        product = session("product")
        product["events"] = [e for e in product["events"] if e["event_name"] != "exam_adoption_changed"]
        product["events"][-1]["question_states"] = [{"question_id": "question-1",
                                                     "answer_version": "answer-1", "adoption_status": "adopted"}]
        result = evaluate_case({"sessions": [product, session("original")]})
        self.assertEqual(result["status"], "pending")
        self.assertIn("missing_adoption_confirmation", result["issues"])

    def test_boolean_is_not_valid_elapsed_time(self) -> None:
        product = session("product")
        product["events"].append(event("bad-time", "exam_action_recorded", True, "question-1", action="verify"))
        result = evaluate_case({"sessions": [product, session("original")]})
        self.assertEqual(result["status"], "invalid")
        self.assertIn("invalid_elapsed_ms", result["issues"])

    def test_exit_is_terminal_but_budget_is_not_shortened(self) -> None:
        exited = evaluate_case(self.by_id["event-answer-after-early-exit"]["input"])
        self.assertEqual(exited["status"], "invalid")
        self.assertEqual(exited["sessions"][0]["after_exit_events"], 3)
        self.assertEqual(exited["sessions"][0]["observed_C"], 0)
        self.assertEqual(exited["sessions"][0]["budget_ms"], 1000)
        known_exit = evaluate_case(self.by_id["event-early-exit-keeps-fixed-budget"]["input"])
        self.assertEqual(known_exit["status"], "complete")
        self.assertEqual(known_exit["groups"]["product"]["M"], 1)

    def test_clock_basis_must_be_explicit(self) -> None:
        unknown = evaluate_case(self.by_id["event-unknown-clock-basis"]["input"])
        declared = evaluate_case(self.by_id["event-declared-single-session-clock"]["input"])
        self.assertEqual(unknown["status"], "pending")
        self.assertEqual(declared["status"], "complete")

    def test_each_user_has_one_pair_per_period(self) -> None:
        result = evaluate_case(self.by_id["event-same-user-multiple-pairs"]["input"])
        self.assertEqual(result["status"], "invalid")
        self.assertEqual(result["groups"]["product"]["M"], 1)
        self.assertEqual(result["groups"]["original"]["M"], 1)
        self.assertIsNone(result["delta"])


if __name__ == "__main__":
    unittest.main()
