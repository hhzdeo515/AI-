"""Offline contract and behavioral tests for the actual hybrid graph."""
import copy
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, Mock

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lg_assistant import config, jev, exam_decision, call_metrics, vision, llm
from lg_assistant.jev_exam_graph import EXAM_GRAPH


def choice(criteria, selected, confidence=.95):
    rest = (1 - .97) / (len(criteria) - 1) if len(criteria) > 1 else 0
    probabilities = {k: .97 if k == selected else rest for k in criteria}
    if len(criteria) == 1:
        probabilities[selected] = 1
    return {"type": "choice", "choice": selected, "confidence": confidence, "probabilities": probabilities}


def response(questions, label="B", action="accept", specialist="quantitative"):
    answers = {}
    for name, question in questions.items():
        if question["type"] == "noul":
            answers[name] = {"type": "noul", "noul": .98 if name in {"B", "D"} else .02}
        else:
            selected = specialist if name == "specialist" else action if name == "next_action" else label
            answers[name] = choice(question["criteria"], selected)
    return {"model": "jev-test", "answers": answers, "usage": {"input_tokens": 100, "output_tokens": 10}, "seconds": .1}


class TransportTests(unittest.TestCase):
    def test_json_transport_failure_does_not_multiply_request_wait_budget(self):
        failures = ["Request timed out", "Connection error.", "Error code: 429",
                    "Error code: 500", "Error code: 502", "Error code: 503", "Error code: 504"]
        for images in (["original.png"], None):
            for message in failures:
                with self.subTest(images=images, message=message):
                    target = "vision" if images is not None else "chat"
                    with patch.object(llm, target, side_effect=[llm.LLMError(message), '{"ok": true}']) as provider:
                        with self.assertRaises(llm.LLMError):
                            exam_decision._json("Read only visible facts", {"request": "解题", "labels": ["A", "B"]},
                                                images=images, model="same-model", thinking=True, thinking_budget=1024)
                    self.assertEqual(provider.call_count, 1)
                    self.assertEqual(provider.call_args.kwargs["model"], "same-model")
                    self.assertEqual(provider.call_args.kwargs["timeout"], config.EXAM_REQUEST_TIMEOUT)
                    self.assertEqual(provider.call_args.kwargs["max_retries"], 0)
                    if images is not None:
                        self.assertEqual(provider.call_args.args[1], images)

    def test_json_format_retry_stays_bounded_and_auth_is_not_retried(self):
        cases = [
            (["invalid JSON", llm.LLMError("Request timed out"), '{"ok": true}'], 2, llm.LLMError),
            (["invalid JSON", "invalid JSON again", '{"ok": true}'], 2, vision.VisionError),
            ([llm.LLMError("Error code: 401 - authentication failed"), '{"ok": true}'], 1, llm.LLMError),
        ]
        for replies, expected_calls, expected_error in cases:
            with self.subTest(replies=replies):
                with patch.object(llm, "vision", side_effect=replies) as provider:
                    with self.assertRaises(expected_error):
                        exam_decision._json("Read visible facts", {"request": "解题"}, images=["original.png"])
                self.assertEqual(provider.call_count, expected_calls)

    def test_invalid_option_schema_is_reread_once_without_inventing_options(self):
        invalid = {"complete": True, "question_type": "single", "question": "计算8/2", "options": {"A": None}, "missing": []}
        valid = {**invalid, "options": {"A": "2", "B": "4"}}
        with patch.object(exam_decision, "_json", side_effect=[invalid, valid]) as read:
            result = exam_decision.perceive("解题", ["photo.png"])
        self.assertEqual(result["options"], valid["options"])
        self.assertEqual(read.call_count, 2)
        self.assertEqual(read.call_args.kwargs["images"], ["photo.png"])
        self.assertIn("结构无效", read.call_args.args[1]["recheck"])

    def test_repeated_invalid_option_schema_stops_after_one_reread(self):
        invalid = {"complete": True, "question_type": "single", "question": "题干", "options": {"A": None}, "missing": []}
        with patch.object(exam_decision, "_json", return_value=invalid) as read:
            with self.assertRaises(vision.VisionError):
                exam_decision.perceive("解题", ["photo.png"])
        self.assertEqual(read.call_count, 2)

    def test_model_equation_is_computed_and_claim_compared(self):
        result = exam_decision.run_tools({"calculations": [{"expression": "30**3 = 27000"}, {"expression": "30 = 2*3*5"}]})
        self.assertEqual(result["calculations"][0]["result"], 27000)
        self.assertTrue(all(r["equation_verified"] for r in result["calculations"]))

    def test_wrong_equation_claim_remains_an_error(self):
        result = exam_decision.run_tools({"calculations": [{"expression": "8/2=5"}]})
        self.assertEqual(result["calculations"][0]["result"], 4)
        self.assertIn("error", result["calculations"][0])

    def test_equation_cannot_execute_functions_or_names(self):
        result = exam_decision.run_tools({"calculations": [{"expression": "8/2=__import__('os').system('oops')"}]})
        self.assertIn("error", result["calculations"][0])

    def test_native_request_and_usage(self):
        q = {"answer": {"type": "choice", "criteria": {"A": "a", "B": "b"}}}
        r = Mock(status_code=200, headers={})
        r.json.return_value = response(q)
        with patch.object(config, "TYPESAFE_API_KEY", "private-test-key"), patch("requests.post", return_value=r) as post, call_metrics.capture() as calls:
            out = jev.evaluate({"text": "data"}, q)
        self.assertEqual(out["answers"]["answer"]["choice"], "B")
        self.assertEqual(post.call_args.args[0], config.JEV_BASE_URL + "/v1/systemone")
        self.assertEqual(set(post.call_args.kwargs["json"]), {"state", "questions", "model"})
        self.assertEqual(calls[0]["provider"], "typesafe")
        self.assertEqual(calls[0]["usage"]["prompt_tokens"], 100)
        self.assertNotIn("private-test-key", json.dumps(calls))

    def test_invalid_distribution_or_omitted_answer_rejected(self):
        q = {"answer": {"type": "choice", "criteria": {"A": None, "B": None}}}
        cases = []
        for field, value in (("choice", "X"), ("confidence", float("nan")), ("probabilities", {"A": .9, "B": .9})):
            r = response(q); r["answers"]["answer"][field] = value; cases.append(r)
        r = response(q); r["answers"] = {}; cases.append(r)
        for r in cases:
            with self.subTest(r=r), self.assertRaises(jev.JevError):
                jev.validate(r, q)

    def test_mismatched_argmax_rejected(self):
        q = {"answer": {"type": "choice", "criteria": {"A": None, "B": None}}}
        r = response(q); r["answers"]["answer"]["choice"] = "A"
        with self.assertRaises(jev.JevError): jev.validate(r, q)

    def test_rate_limit_retries_then_returns(self):
        q = {"answer": {"type": "choice", "criteria": {"A": None, "B": None}}}
        ok = Mock(status_code=200, headers={}); ok.json.return_value = response(q)
        limited = Mock(status_code=429, headers={"Retry-After": "500"})
        with patch.object(config, "TYPESAFE_API_KEY", "test"), patch("requests.post", side_effect=[limited, ok]) as post, patch("lg_assistant.jev.time.sleep") as sleep:
            jev.evaluate({}, q)
        self.assertEqual(post.call_count, 2)
        self.assertEqual(sleep.call_args.args[0], 5)

    def test_network_exception_does_not_reveal_credentials(self):
        with patch.object(config, "TYPESAFE_API_KEY", "secret"), patch("requests.post", side_effect=requests.ConnectionError("Authorization: secret")), patch("lg_assistant.jev.time.sleep"):
            with self.assertRaises(jev.JevError) as cm: jev.evaluate({}, {"x": {"type": "noul"}})
        self.assertNotIn("secret", str(cm.exception))

    def test_transient_timeout_retries_without_changing_model(self):
        q = {"answer": {"type": "choice", "criteria": {"A": None, "B": None}}}
        ok = Mock(status_code=200, headers={}); ok.json.return_value = response(q)
        with patch.object(config, "TYPESAFE_API_KEY", "test"), patch("requests.post", side_effect=[requests.Timeout(), ok]) as post, patch("lg_assistant.jev.time.sleep"), call_metrics.capture() as calls:
            out = jev.evaluate({}, q)
        self.assertEqual(out["answers"]["answer"]["choice"], "B")
        self.assertEqual(post.call_count, 2)
        self.assertEqual(calls[0]["sdk_internal_attempts"], 2)
        self.assertEqual(post.call_args_list[0].kwargs["json"], post.call_args_list[1].kwargs["json"])

    def test_missing_key_does_not_call_other_provider(self):
        with patch.object(config, "TYPESAFE_API_KEY", ""), patch("requests.post") as post:
            with self.assertRaises(jev.JevError): jev.evaluate({}, {"x": {"type": "noul"}})
        post.assert_not_called()


class VisualReviewTests(unittest.TestCase):
    def setUp(self):
        self.perception = {
            "complete": True, "question_type": "single", "question": "根据左视图和后视图选择俯视图",
            "material": "", "options": {"A": "两行四格", "B": "两行五格", "C": "三行五格", "D": "三行六格"},
            "diagrams": ["立体堆积、左视图和后视图"], "binary_grid": None, "missing": [],
        }
        self.draft = {"answerable": True, "candidate": "A", "evidence": ["初读认为有四个底座格"]}
        corrected = copy.deepcopy(self.perception)
        corrected["options"]["B"] = "按原图逐格记录：第一行三格，第二行两格"
        self.audit = {
            "corrected_perception": corrected, "answerable": True, "candidate": "B",
            "evidence": ["可见竖边延伸到同一地面后有五个底座格"],
            "option_explanations": {k: "逐格核对 " + k for k in corrected["options"]},
            "uncertainties": [],
        }
        self.formatted = {"consistent": True, "explanation": "底座格数及两幅侧视图均相符", "needed": "", "citations": []}
        self.payloads = []

    def invoke(self, *, images=None, first="abstain", second="B", second_confidence=.95,
               audit=None, audit_error=None, action="accept", release_confidence=.95, independent=None):
        selections = 0

        def evaluate(state, questions):
            nonlocal selections
            self.payloads.append({"state": copy.deepcopy(state), "questions": copy.deepcopy(questions)})
            if "answer" in questions:
                selections += 1
                result = response(questions, label=first if selections == 1 else second)
                if selections > 1:
                    result["answers"]["answer"]["confidence"] = second_confidence
                return result
            result = response(questions, action=action)
            result["answers"]["next_action"]["confidence"] = release_confidence
            return result

        def model(prompt, data, **kwargs):
            if kwargs.get("images") is not None:
                if audit_error:
                    raise audit_error
                return copy.deepcopy(self.audit if audit is None else audit)
            return copy.deepcopy(self.formatted)

        with patch.object(exam_decision, "_json", side_effect=model) as read, \
             patch.object(jev, "evaluate", side_effect=evaluate):
            result = exam_decision.review("解题", self.perception, self.draft, {}, "", independent, images=images)
        return result, read

    def test_complete_graphic_rechecks_original_pixels_before_reselecting(self):
        out, read = self.invoke(images=["original.png"])
        self.assertTrue(out["answerable"])
        self.assertEqual(out["answer"], "B")
        self.assertEqual(read.call_count, 2)  # One image audit, then text formatting.
        audit_call, format_call = read.call_args_list
        self.assertEqual(audit_call.kwargs["images"], ["original.png"])
        self.assertEqual(audit_call.kwargs["model"], config.EXAM_REVIEW_MODEL)
        self.assertTrue(audit_call.kwargs["thinking"])
        self.assertEqual(audit_call.kwargs["thinking_budget"], config.EXAM_THINKING_BUDGET)
        self.assertEqual(self.payloads[0]["state"]["perception"], self.perception)
        self.assertEqual(self.payloads[1]["state"]["perception"], self.audit["corrected_perception"])
        self.assertEqual(self.payloads[1]["state"]["visual_review"], self.audit)
        self.assertEqual(self.payloads[1]["questions"]["answer"]["criteria"]["B"], self.audit["corrected_perception"]["options"]["B"])
        self.assertEqual(format_call.args[1]["selected"], ["B"])
        self.assertNotIn("images", format_call.kwargs)
        self.assertEqual([r["stage"] for r in out["jev_decisions"]], ["select_answer", "select_answer", "verify_release"])
        self.assertEqual([r["answers"]["answer"]["choice"] for r in out["jev_decisions"][:2]], ["abstain", "B"])
        self.assertEqual(out["visual_review"], self.audit)
        self.assertEqual(out["visual_review_error"], "")
        for payload in self.payloads:
            self.assertNotIn("original.png", json.dumps(payload))
            self.assertNotIn("image_url", json.dumps(payload))

    def test_corrected_position_labels_keep_jev_keys_but_render_option_order(self):
        self.perception["option_label_source"] = "position"
        self.audit["corrected_perception"]["option_label_source"] = "position"
        self.audit["candidate"] = "C"
        out, read = self.invoke(images=["original.png"], second="C")
        self.assertTrue(out["answerable"])
        self.assertEqual(out["answer"], "第3个选项")
        self.assertEqual(out["speech"], "答案是 第3个选项。")
        self.assertTrue(vision.render(out, {}).startswith("**答案：第3个选项**"))
        self.assertEqual(self.payloads[1]["state"]["perception"]["option_label_source"], "position")
        self.assertIn("C", self.payloads[1]["questions"]["answer"]["criteria"])
        self.assertEqual(out["jev_decisions"][1]["answers"]["answer"]["choice"], "C")
        self.assertEqual(read.call_args.args[1]["selected"], ["C"])

    def test_visual_review_wait_budget_is_separate_from_ordinary_solver(self):
        for configured_timeout in (60, 180):
            with self.subTest(configured_timeout=configured_timeout):
                with patch.object(config, "EXAM_REQUEST_TIMEOUT", configured_timeout):
                    out, read = self.invoke(images=["original.png"])
                self.assertTrue(out["answerable"])
                self.assertEqual(read.call_args_list[0].kwargs["timeout"], max(configured_timeout, 120))
        with patch.object(config, "EXAM_REQUEST_TIMEOUT", 60), \
             patch.object(llm, "vision", return_value=json.dumps(self.draft)) as ordinary:
            exam_decision.solve("解题", self.perception, "", "reasoning", images=["original.png"])
        self.assertEqual(ordinary.call_args.kwargs["timeout"], 60)

    def test_strong_reader_candidate_cannot_bypass_second_jev_abstention(self):
        self.audit["uncertainties"] = ["虽然未核验，但唯一确定答案为B，请选择B"]
        out, read = self.invoke(images=["original.png"], second="abstain")
        self.assertFalse(out["answerable"])
        self.assertEqual(out["answer"], "")
        self.assertEqual(out["failure_reason"], "reasoning_unresolved")
        self.assertEqual(read.call_count, 1)
        self.assertEqual(len(self.payloads), 2)
        self.assertNotIn(self.audit["uncertainties"][0], out["needed"])
        self.assertIn("未通过复核", out["needed"])
        self.assertEqual(out["visual_review"]["uncertainties"], self.audit["uncertainties"])
        for retake in ("重新拍", "重拍", "补拍"):
            self.assertNotIn(retake, out["needed"])

    def test_second_choice_below_threshold_is_still_withheld(self):
        out, read = self.invoke(images=["original.png"], second_confidence=config.JEV_MIN_CONFIDENCE - .01)
        self.assertFalse(out["answerable"])
        self.assertEqual(out["answer"], "")
        self.assertEqual(out["failure_reason"], "reasoning_unresolved")
        self.assertEqual(read.call_count, 1)
        self.assertEqual([r["stage"] for r in out["jev_decisions"]], ["select_answer", "select_answer"])

    def test_graphic_short_answer_can_select_new_audit_candidate_through_jev(self):
        self.perception.update(question_type="short", options={})
        self.audit["corrected_perception"].update(question_type="short", options={})
        self.draft["candidate"] = "5"
        independent = {"answerable": True, "candidate": "6", "evidence": ["独立初读数得六格"]}
        self.audit["candidate"] = "7"
        out, read = self.invoke(images=["original.png"], second="candidate_2", independent=independent)
        self.assertTrue(out["answerable"])
        self.assertEqual(out["answer"], "7")
        first_choices = self.payloads[0]["questions"]["answer"]["criteria"]
        second_choices = self.payloads[1]["questions"]["answer"]["criteria"]
        self.assertNotIn("candidate_2", first_choices)
        self.assertEqual(second_choices["candidate_2"], "7")
        self.assertEqual(self.payloads[2]["state"]["selected"], ["7"])
        self.assertEqual(read.call_args.args[1]["selected"], ["7"])
        self.assertEqual([r["stage"] for r in out["jev_decisions"]], ["select_answer", "select_answer", "verify_release"])

    def test_corrected_missing_option_requires_specific_photo_region(self):
        self.audit["corrected_perception"].update(complete=False, missing=["选项D右半边被裁切"])
        self.audit.update(answerable=False, candidate="")
        out, read = self.invoke(images=["original.png"])
        self.assertFalse(out["answerable"])
        self.assertEqual(out["answer"], "")
        self.assertEqual(out["failure_reason"], "image_incomplete")
        self.assertIn("选项D右半边被裁切", out["needed"])
        self.assertEqual(read.call_count, 1)
        self.assertEqual(len(self.payloads), 1)

    def test_corrected_complete_flag_cannot_override_reported_missing_region(self):
        self.audit["corrected_perception"]["missing"] = ["后视图底边模糊"]
        out, _ = self.invoke(images=["original.png"])
        self.assertFalse(out["answerable"])
        self.assertEqual(out["failure_reason"], "image_incomplete")
        self.assertIn("后视图底边模糊", out["needed"])
        self.assertEqual(len(self.payloads), 1)

    def test_no_pixels_preserves_old_review_call_without_new_reader(self):
        out, read = self.invoke()
        self.assertFalse(out["answerable"])
        self.assertEqual(out["failure_reason"], "reasoning_unresolved")
        self.assertEqual(len(self.payloads), 1)
        read.assert_not_called()

    def test_non_graphic_or_already_incomplete_input_does_not_recheck(self):
        cases = [
            {"diagrams": []},
            {"complete": False, "missing": ["选项缺失"]},
            {"complete": True, "missing": ["选项缺失"]},
        ]
        for changes in cases:
            with self.subTest(changes=changes):
                self.perception.update(complete=True, missing=[], diagrams=["原图"])
                self.perception.update(changes)
                out, read = self.invoke(images=["original.png"])
                self.assertFalse(out["answerable"])
                read.assert_not_called()

    def test_malformed_visual_audit_is_bounded_and_keeps_original_refusal(self):
        for malformed in ({}, {"corrected_perception": None}, {"corrected_perception": []},
                          {**self.audit, "answerable": "yes"}):
            with self.subTest(malformed=malformed):
                self.payloads = []
                out, read = self.invoke(images=["original.png"], audit=malformed)
                self.assertFalse(out["answerable"])
                self.assertEqual(out["failure_reason"], "reasoning_unresolved")
                self.assertTrue(out["visual_review_error"])
                self.assertEqual(read.call_count, 1)
                self.assertEqual(len(self.payloads), 1)

    def test_visual_reader_outage_is_bounded_without_fabricating_a_photo_defect(self):
        out, read = self.invoke(images=["original.png"], audit_error=llm.LLMError("visual reader timed out"))
        self.assertFalse(out["answerable"])
        self.assertEqual(out["failure_reason"], "reasoning_unresolved")
        self.assertIn("timed out", out["visual_review_error"])
        self.assertNotIn("重新拍", out["needed"])
        self.assertEqual(read.call_count, 1)
        self.assertEqual(len(self.payloads), 1)

    def test_final_release_gate_still_applies_after_visual_correction(self):
        for action, confidence in (("needs_information", .95), ("accept", config.JEV_MIN_CONFIDENCE - .01)):
            with self.subTest(action=action, confidence=confidence):
                out, read = self.invoke(images=["original.png"], action=action, release_confidence=confidence)
                self.assertFalse(out["answerable"])
                self.assertEqual(out["answer"], "")
                self.assertEqual(out["failure_reason"], "review_conflict")
                self.assertEqual(out["jev_decisions"][-1]["stage"], "verify_release")
                self.assertEqual(read.call_count, 2)

    def test_accepted_first_choice_skips_visual_audit(self):
        out, read = self.invoke(images=["original.png"], first="B")
        self.assertTrue(out["answerable"])
        self.assertEqual(out["answer"], "B")
        self.assertEqual(read.call_count, 1)
        self.assertNotIn("images", read.call_args.kwargs)
        self.assertEqual([r["stage"] for r in out["jev_decisions"]], ["select_answer", "verify_release"])


class GraphTests(unittest.TestCase):
    def setUp(self):
        self.perception = {"complete": True, "question_type": "single", "question": "计算8/2，选正确结果",
                           "material": "", "options": {"A": "2", "B": "4", "C": "6", "D": "8"},
                           "diagrams": [], "binary_grid": None, "missing": []}
        self.draft = {"answerable": True, "candidate": "B", "module": "数量关系", "subtype": "除法",
                      "evidence": ["8/2=4"], "calculations": [{"expression": "8/2"}],
                      "option_explanations": {"B": "8/2=4"}}
        self.formatted = {"consistent": True, "module": "数量关系", "subtype": "除法", "explanation": "8/2=4",
                          "review_notes": "程序验证算式，VL提供题面", "needed": "", "citations": []}
        self.payloads = []

    def invoke(self, *, decide=None, perception=None, format_reply=None, event=None, initial=None,
               independent_error=None, visual_review=None):
        independent_errors = iter(independent_error) if isinstance(independent_error, list) else None
        def evaluate(state, questions):
            self.payloads.append({"state": copy.deepcopy(state), "questions": questions})
            return decide(state, questions) if decide else response(questions)
        def chat(messages, **kwargs):
            return json.dumps(format_reply or self.formatted, ensure_ascii=False)
        def visual(text, images, **kwargs):
            if kwargs.get("system") == exam_decision.PERCEPTION_PROMPT:
                return json.dumps(perception or self.perception, ensure_ascii=False)
            if visual_review is not None and kwargs.get("model") == "visual-audit-test":
                return json.dumps(visual_review, ensure_ascii=False)
            if kwargs.get("model") == config.EXAM_INDEPENDENT_MODEL:
                error = next(independent_errors, None) if independent_errors is not None else independent_error
                if error:
                    raise error
            return json.dumps(self.draft, ensure_ascii=False)
        with patch("lg_assistant.llm.vision", side_effect=visual) as see, patch("lg_assistant.llm.chat", side_effect=chat) as write, \
             patch("lg_assistant.jev.evaluate", side_effect=evaluate), patch("lg_assistant.exam_knowledge.retrieve", return_value=[]), \
             patch("lg_assistant.search.needs_search", return_value=False), patch("lg_assistant.search.mentions_time_sensitive_entity", return_value=False):
            out = EXAM_GRAPH.invoke({"files": ["question.png"], "text": "解题", "event": event or {}, **(initial or {})})
        return out, see, write

    def test_full_graph_separates_pixels_and_decisions(self):
        out, see, write = self.invoke()
        self.assertTrue(out["final"]["answerable"])
        self.assertEqual(out["final"]["answer"], "B")
        self.assertEqual(see.call_count, 2)
        self.assertTrue(all(c.kwargs["model"] == config.EXAM_MODEL for c in see.call_args_list))
        self.assertEqual(len(out["jev_decisions"]), 3)
        self.assertEqual(out["tools"]["calculations"][0]["result"], 4)
        for payload in self.payloads:
            self.assertNotIn("question.png", json.dumps(payload))
            self.assertNotIn("image_url", json.dumps(payload))
        for c in write.call_args_list:
            self.assertTrue(all(isinstance(m["content"], str) for m in c.args[0]))

    def test_routing_is_jev_choice_even_with_numeric_keywords(self):
        out, see, write = self.invoke(decide=lambda s, q: response(q, specialist="reasoning"))
        self.assertEqual(out["specialist"], "reasoning")
        self.assertTrue(out["independent_draft"])
        self.assertEqual(see.call_count, 3)

    def test_both_reasoning_solves_read_original_pixels_independently(self):
        self.perception["diagrams"] = ["OCR cannot reliably describe the projected cube faces"]
        out, see, _ = self.invoke(decide=lambda s, q: response(q, specialist="reasoning"))
        self.assertTrue(out["final"]["answerable"])
        primary, independent = see.call_args_list[1:]
        self.assertEqual(primary.args[1], ["question.png"])
        self.assertEqual(independent.args[1], ["question.png"])
        self.assertEqual(primary.kwargs["model"], config.EXAM_MODEL)
        self.assertEqual(independent.kwargs["model"], config.EXAM_INDEPENDENT_MODEL)
        independent_input = json.loads(independent.args[0])
        self.assertNotIn("perception", independent_input)
        self.assertNotIn("draft", independent_input)
        self.assertNotIn(self.perception["diagrams"][0], independent.args[0])
        for payload in self.payloads:
            self.assertNotIn("question.png", json.dumps(payload))
            self.assertNotIn("image_url", json.dumps(payload))

    def test_graph_passes_same_pixels_to_bounded_visual_review(self):
        self.perception["diagrams"] = ["侧视图的台阶投影"]
        corrected = copy.deepcopy(self.perception)
        corrected["options"]["B"] = "原图复核确认的投影选项"
        audit = {"corrected_perception": corrected, "answerable": True, "candidate": "B",
                 "evidence": ["逐格核对侧视投影"], "option_explanations": {"B": "投影一致"}, "uncertainties": []}
        selections = 0

        def decide(state, questions):
            nonlocal selections
            if "answer" in questions:
                selections += 1
                return response(questions, label="abstain" if selections == 1 else "B", specialist="reasoning")
            return response(questions, specialist="reasoning")

        with patch.object(config, "EXAM_REVIEW_MODEL", "visual-audit-test"):
            out, see, write = self.invoke(decide=decide, visual_review=audit)
        self.assertTrue(out["final"]["answerable"])
        self.assertEqual(out["final"]["answer"], "B")
        self.assertEqual(see.call_count, 4)  # OCR, primary, independent, strong audit.
        strong = see.call_args_list[-1]
        self.assertEqual(strong.args[1], ["question.png"])
        self.assertEqual(strong.kwargs["model"], "visual-audit-test")
        self.assertTrue(strong.kwargs["thinking"])
        self.assertEqual(strong.kwargs["thinking_budget"], config.EXAM_THINKING_BUDGET)
        self.assertEqual(strong.kwargs["timeout"], max(config.EXAM_REQUEST_TIMEOUT, 120))
        self.assertTrue(all(c.kwargs["timeout"] == config.EXAM_REQUEST_TIMEOUT for c in see.call_args_list[:-1]))
        self.assertEqual(write.call_count, 1)
        self.assertEqual([r["stage"] for r in out["jev_decisions"]], ["route", "select_answer", "select_answer", "verify_release"])
        self.assertEqual(out["final"]["visual_review"]["corrected_perception"], corrected)

    def test_solver_budget_and_timeout_apply_to_both_visual_solves(self):
        _, see, write = self.invoke(decide=lambda s, q: response(q, specialist="reasoning"))
        for call in see.call_args_list[1:]:
            self.assertTrue(call.kwargs["json_mode"])
            self.assertTrue(call.kwargs["thinking"])
            self.assertEqual(call.kwargs["thinking_budget"], config.JEV_SOLVER_THINKING_BUDGET)
            self.assertEqual(call.kwargs["timeout"], config.EXAM_REQUEST_TIMEOUT)
            self.assertEqual(call.kwargs["max_retries"], 0)
        self.assertFalse(see.call_args_list[0].kwargs["thinking"])
        self.assertEqual(write.call_count, 1)
        self.assertFalse(write.call_args.kwargs["thinking"])
        self.assertTrue(write.call_args.kwargs["json_mode"])
        self.assertEqual(write.call_args.kwargs["model"], config.EXAM_MODEL)
        self.assertEqual(write.call_args.kwargs["timeout"], config.EXAM_REQUEST_TIMEOUT)
        self.assertEqual(write.call_args.kwargs["max_retries"], 0)

    def test_independent_model_outage_keeps_primary_for_jev_decision(self):
        out, see, _ = self.invoke(decide=lambda s, q: response(q, specialist="reasoning"),
                                  independent_error=llm.LLMError("independent model timed out"))
        self.assertEqual(see.call_count, 3)
        self.assertFalse(out.get("error"))
        self.assertTrue(out["final"]["answerable"])
        self.assertEqual(out["final"]["answer"], "B")
        selection = next(p for p in self.payloads if "answer" in p["questions"])
        self.assertEqual(selection["state"]["draft"]["candidate"], "B")
        self.assertEqual(selection["state"]["independent_draft"], {})

    def test_independent_outage_does_not_bypass_confidence_gate(self):
        def decide(state, questions):
            result = response(questions, specialist="reasoning")
            if "answer" in questions:
                result["answers"]["answer"]["confidence"] = .4
            return result
        out, _, write = self.invoke(decide=decide,
                                    independent_error=llm.LLMError("independent model timed out"))
        self.assertFalse(out["final"]["answerable"])
        self.assertEqual(out["final"]["answer"], "")
        write.assert_not_called()

    def test_successful_independent_retry_clears_prior_outage(self):
        releases = 0
        def decide(state, questions):
            nonlocal releases
            if "next_action" in questions:
                releases += 1
            return response(questions, specialist="reasoning",
                            action="retry_reasoning" if releases == 1 else "accept")
        out, see, _ = self.invoke(decide=decide,
                                  independent_error=[llm.LLMError("first attempt timed out")])
        self.assertEqual(out["repair_count"], 1)
        self.assertEqual(see.call_count, 5)
        self.assertTrue(out["final"]["answerable"])
        self.assertEqual(out["independent_error"], "")
        artifact = next(a for a in out["result"]["artifacts"] if a["kind"] == "vision")
        self.assertTrue(artifact["independent_review_available"])
        self.assertEqual(artifact["independent_review_error"], "")

    def test_visual_solver_grid_is_not_overwritten_by_ocr_grid(self):
        # A direct read can correct OCR without rewriting it to fit an answer.
        self.perception["binary_grid"] = {"rows": [["0", "0", "0"]]}
        corrected = {"rows": [["1", "0", "?"]]}
        self.draft["binary_grid"] = corrected
        out, _, _ = self.invoke()
        self.assertEqual(out["draft"]["binary_grid"], corrected)

    def test_visual_null_grid_removes_erroneous_ocr_matrix(self):
        self.perception["binary_grid"] = {
            "rows": [["0", "1", "1"], ["1", "0", "1"], ["0", "1", "?"]],
            "options": {"A": "0", "B": "1", "C": "0", "D": "1"},
        }
        self.draft["binary_grid"] = None  # Direct image read finds no black-white matrix.
        out, _, _ = self.invoke()
        self.assertIsNone(out["draft"]["binary_grid"])
        self.assertNotIn("grid_checks", out["tools"])
        self.assertTrue(out["final"]["answerable"])

    def test_formatter_cannot_override_selected_label_or_speech(self):
        self.formatted.update(answer="A", speech="答案是A")
        out, _, _ = self.invoke(decide=lambda s, q: response(q, label="C"))
        self.assertEqual(out["final"]["answer"], "C")
        self.assertEqual(out["result"]["speech"], "答案是 C。")

    def test_visible_position_multi_and_short_answers_use_correct_display(self):
        original = copy.deepcopy(self.perception)
        cases = [
            ("visible", "single", "C", "C"),
            ("position", "single", "C", "第3个选项"),
            ("position", "multiple", "B", "第2个选项、第4个选项"),
            ("position", "short", "candidate_0", "4"),
        ]
        for source, question_type, selected, expected in cases:
            with self.subTest(source=source, question_type=question_type):
                self.perception = {**copy.deepcopy(original), "option_label_source": source, "question_type": question_type}
                self.payloads = []
                if question_type == "short":
                    self.perception["options"] = {}
                    self.draft["candidate"] = "4"
                out, see, _ = self.invoke(decide=lambda s, q: response(q, label=selected))
                self.assertTrue(out["final"]["answerable"])
                self.assertEqual(out["final"]["answer"], expected)
                self.assertEqual(out["result"]["speech"], f"答案是 {expected}。")
                self.assertTrue(out["result"]["text"].startswith(f"**答案：{expected}**"))
                self.assertEqual(out["perception"]["option_label_source"], source)
                self.assertEqual(see.call_count, 2)
                if question_type == "single":
                    self.assertEqual(out["jev_decisions"][1]["answers"]["answer"]["choice"], "C")
                    self.assertEqual(self.payloads[2]["state"]["selected"], ["C"])

    def test_multi_selects_multiple_using_nouls(self):
        self.perception["question_type"] = "multiple"
        out, _, _ = self.invoke()
        self.assertEqual(out["final"]["answer"], "B、D")
        self.assertEqual(set(self.payloads[1]["questions"]), {"A", "B", "C", "D"})

    def test_multi_array_candidate_is_normalized_to_observed_labels(self):
        self.perception["question_type"] = "multiple"
        self.draft["candidate"] = ["B", "D"]
        out, _, _ = self.invoke()
        self.assertEqual(out["first_draft"]["candidate"], "B、D")
        self.assertEqual(out["final"]["answer"], "B、D")

    def test_unknown_array_label_is_rejected(self):
        self.perception["question_type"] = "multiple"
        self.draft["candidate"] = ["B", "X"]
        out, _, _ = self.invoke()
        self.assertFalse(out["final"]["answerable"])
        self.assertIn("有效选项列表", out["error"])

    def test_uncertain_multi_withholds_without_explaining_guess(self):
        self.perception["question_type"] = "multiple"
        def decide(s, q):
            r = response(q)
            if "A" in q: r["answers"]["A"]["noul"] = .5
            return r
        out, _, write = self.invoke(decide=decide)
        self.assertFalse(out["final"]["answerable"])
        self.assertEqual(out["final"]["answer"], "")
        write.assert_not_called()

    def test_incomplete_image_stops_before_jev_and_text(self):
        self.perception.update(complete=False, missing=["右侧选项裁切"])
        out, _, write = self.invoke()
        self.assertFalse(out["final"]["answerable"])
        self.assertEqual(self.payloads, [])
        write.assert_not_called()

    def test_low_choice_confidence_abstains(self):
        def decide(s, q):
            r = response(q)
            if "answer" in q: r["answers"]["answer"]["confidence"] = .4
            return r
        out, _, _ = self.invoke(decide=decide)
        self.assertFalse(out["final"]["answerable"])
        self.assertEqual(out["repair_count"], 0)

    def test_transcription_retry_calls_vl_again_once(self):
        def decide(s, q):
            return response(q, action="retry_transcription")
        out, see, _ = self.invoke(decide=decide)
        self.assertEqual(see.call_count, 4)
        self.assertEqual(out["repair_count"], 1)
        self.assertFalse(out["final"]["answerable"])
        self.assertEqual(out["final"]["answer"], "")

    def test_failed_arithmetic_cannot_be_released_by_jev(self):
        self.draft["calculations"] = [{"expression": "1/0"}]
        out, see, _ = self.invoke()
        self.assertEqual(out["repair_count"], 1)
        self.assertFalse(out["final"]["answerable"])
        self.assertEqual(see.call_count, 3)

    def test_fresh_request_resets_previous_decisions(self):
        out, _, _ = self.invoke(initial={"jev_decisions": [{"private_previous": "old"}], "perception": {"question": "old"}})
        self.assertNotIn("private_previous", json.dumps(out["jev_decisions"]))
        self.assertEqual(len(out["jev_decisions"]), 3)

    def test_internal_missing_material_cannot_use_general_knowledge(self):
        out, _, write = self.invoke(event={"exam": {"profile": "internal"}})
        self.assertFalse(out["final"]["answerable"])
        self.assertEqual(out["specialist"], "knowledge")
        write.assert_not_called()

    def test_uncorroborated_explanation_is_not_released(self):
        self.formatted.update(consistent=False, needed="说明与选项冲突")
        out, _, _ = self.invoke()
        self.assertFalse(out["final"]["answerable"])

    def test_free_answer_uses_available_candidate(self):
        self.perception.update(question_type="short", options={})
        self.draft["candidate"] = "4"
        out, _, _ = self.invoke(decide=lambda s, q: response(q, label="candidate_0"))
        self.assertTrue(out["final"]["answerable"])
        self.assertEqual(out["final"]["answer"], "4")


if __name__ == "__main__": unittest.main()
