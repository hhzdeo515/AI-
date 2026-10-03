"""Offline checks for camera-model budgets and bounded format recovery."""
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lg_assistant import config, exam_decision, llm, vision


class RequestBudgetTests(unittest.TestCase):
    def setUp(self):
        self.caller = Mock()
        self.caller.with_options.return_value = self.caller
        self.caller.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=' {"ok":true} '))],
            usage=None,
        )

    def test_visual_request_applies_sdk_deadline_and_bounded_reasoning(self):
        with patch.object(llm, "client", return_value=self.caller), \
             patch.object(llm, "to_data_url", return_value="data:image/png;base64,test"):
            output = llm.vision("read the original image", ["photo.png"], model="qwen3-test",
                                json_mode=True, thinking=True, thinking_budget=1024,
                                timeout=60, max_retries=0)
        self.assertEqual(json.loads(output), {"ok": True})
        self.caller.with_options.assert_called_once_with(timeout=60, max_retries=0)
        kwargs = self.caller.chat.completions.create.call_args.kwargs
        self.assertEqual(kwargs["extra_body"], {"enable_thinking": True, "thinking_budget": 1024})
        self.assertEqual(kwargs["response_format"], {"type": "json_object"})
        self.assertNotIn("max_tokens", kwargs)  # Complete JSON must not be truncated.
        self.assertNotIn("timeout", kwargs)
        self.assertNotIn("max_retries", kwargs)
        content = kwargs["messages"][-1]["content"]
        self.assertEqual(content[-1]["image_url"]["url"], "data:image/png;base64,test")

    def test_formatter_disables_thinking_without_changing_chat_defaults(self):
        with patch.object(llm, "client", return_value=self.caller):
            llm.chat([{"role": "user", "content": "format the selected answer"}],
                     model="qwen3-test", json_mode=True, thinking=False,
                     thinking_budget=1024, timeout=60, max_retries=0)
        kwargs = self.caller.chat.completions.create.call_args.kwargs
        self.assertEqual(kwargs["extra_body"], {"enable_thinking": False})
        self.assertEqual(kwargs["response_format"], {"type": "json_object"})
        self.caller.with_options.assert_called_once_with(timeout=60, max_retries=0)
        self.assertNotIn("max_tokens", kwargs)

        self.caller.reset_mock()
        with patch.object(llm, "client", return_value=self.caller):
            llm.chat([{"role": "user", "content": "ordinary chat"}], model="qwen-plus")
        self.caller.with_options.assert_not_called()
        ordinary = self.caller.chat.completions.create.call_args.kwargs
        self.assertNotIn("extra_body", ordinary)
        self.assertNotIn("response_format", ordinary)


class FormatRecoveryTests(unittest.TestCase):
    def test_invalid_json_retries_once_with_same_original_image_and_budget(self):
        with patch.object(llm, "vision", side_effect=["incomplete JSON", '{"ok":true}']) as see:
            result = exam_decision._json("read pixels", {"request": "solve"}, images=["photo.png"],
                                         model="qwen3-test", thinking=True, thinking_budget=1024)
        self.assertEqual(result, {"ok": True})
        self.assertEqual(see.call_count, 2)
        for call in see.call_args_list:
            self.assertEqual(call.args[1], ["photo.png"])
            self.assertEqual(call.kwargs["thinking_budget"], 1024)
            self.assertEqual(call.kwargs["timeout"], config.EXAM_REQUEST_TIMEOUT)
            self.assertEqual(call.kwargs["max_retries"], 0)
        self.assertTrue(see.call_args_list[0].kwargs["json_mode"])
        self.assertFalse(see.call_args_list[1].kwargs["json_mode"])

    def test_provider_json_generation_failure_can_recover_without_extra_thinking(self):
        failure = llm.LLMError("output became abnormal under JSON generation")
        with patch.object(llm, "chat", side_effect=[failure, '{"consistent":true}']) as write:
            result = exam_decision._json("format selected answer", {"selected": ["B"]})
        self.assertEqual(result, {"consistent": True})
        self.assertEqual(write.call_count, 2)
        self.assertFalse(write.call_args.kwargs["thinking"])
        self.assertFalse(write.call_args.kwargs["json_mode"])
        self.assertEqual(write.call_args.kwargs["max_retries"], 0)

    def test_network_timeout_is_not_multiplied_by_json_recovery(self):
        with patch.object(llm, "vision", side_effect=llm.LLMError("model request timed out")) as see:
            with self.assertRaises(llm.LLMError):
                exam_decision._json("read pixels", {}, images=["photo.png"])
        see.assert_called_once()


class SharedVisionBudgetTests(unittest.TestCase):
    def test_shared_json_paths_forward_deadline_without_sdk_retries(self):
        for images, method in ((["photo.png"], "vision"), ([], "chat")):
            with self.subTest(method=method), patch.object(llm, method, return_value='{"ok":true}') as call:
                result = vision._json_call("read question", "solve", images)
            self.assertEqual(result, {"ok": True})
            call.assert_called_once()
            self.assertEqual(call.call_args.kwargs["timeout"], config.EXAM_REQUEST_TIMEOUT)
            self.assertEqual(call.call_args.kwargs["max_retries"], 0)

    def test_observation_forwards_deadline_and_preserves_output_limit(self):
        with patch.object(llm, "vision", return_value="complete observed question") as see:
            result = vision.observe("read this question", ["photo.png"])
        self.assertEqual(result, "complete observed question")
        self.assertEqual(see.call_args.kwargs["timeout"], config.EXAM_REQUEST_TIMEOUT)
        self.assertEqual(see.call_args.kwargs["max_retries"], 0)
        self.assertEqual(see.call_args.kwargs["max_tokens"], vision.OBSERVE_MAX_TOKENS)
        self.assertFalse(see.call_args.kwargs["thinking"])

    def test_shared_application_retry_remains_bounded_at_two_calls(self):
        for failure in ("not JSON", llm.LLMError("model request timed out")):
            with self.subTest(failure=failure), patch.object(llm, "vision", side_effect=[failure, '{"ok":true}']) as see:
                result = vision._json_call("read question", "solve", ["photo.png"])
            self.assertEqual(result, {"ok": True})
            self.assertEqual(see.call_count, 2)
            for call in see.call_args_list:
                self.assertEqual(call.kwargs["timeout"], config.EXAM_REQUEST_TIMEOUT)
                self.assertEqual(call.kwargs["max_retries"], 0)


if __name__ == "__main__":
    unittest.main()
