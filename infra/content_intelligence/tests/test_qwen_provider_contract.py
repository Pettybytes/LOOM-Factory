"""Regression coverage for the established Qwen provider request contract.

Why this exists: the pinned `qwen/qwen3.5-9b` is a reasoning model. With thinking
left on, it consumed the entire output budget before emitting any answer
(`finish_reason="length"`, empty completion) and surfaced as `INVALID_RESPONSE` —
failing 100% of observed calls at the provider default, and still unreliable at
6000/16000. Non-thinking + constrained JSON measured 337-665 output tokens for the
SAME prompt.

These tests pin the fix so the request can never silently regress to
thinking-on with a reasoning-sized budget, while leaving genuine reasoning work
opt-in.

Run from inside this directory:  python3 -m unittest discover -s tests -v
"""

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

_SUITE_DIR = Path(__file__).resolve().parents[1]
if str(_SUITE_DIR) not in sys.path:
    sys.path.insert(0, str(_SUITE_DIR))

from providers import (  # noqa: E402
    QWEN_PINNED_MODEL,
    QWEN_RESPONSE_SCHEMA,
    ProviderError,
    QwenProvider,
    _build_prompt,
)


class _FakeResponse:
    def __init__(self, payload: dict):
        self._payload = json.dumps(payload).encode()

    def read(self) -> bytes:
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


VALID_PAYLOAD = {
    "choices": [{"message": {"content": json.dumps({
        "scores": {"hook": 80, "emotion": 70},
        "content_worthiness_score": 75,
        "candidate_timestamps": [{"start_s": 1.0, "end_s": 9.0, "basis": "audio_spike"}],
        "recommended_content_format": "reaction_single",
        "recommended_angle": "the reveal",
        "reasons": [{"code": "STRONG_HOOK", "signal": "hook", "message": "opens hot"}],
    })}}],
    "usage": {"prompt_tokens": 300, "completion_tokens": 350},
    "model": QWEN_PINNED_MODEL,
}


class _Brief:
    topics = ["reaction"]
    keywords = ["moment"]
    entities = []


def _call_provider(**kw):
    """Invoke the provider and return the captured request body."""
    captured: dict = {}

    def fake_urlopen(req, timeout=None):
        captured["body"] = json.loads(req.data.decode())
        captured["timeout"] = timeout
        captured["headers"] = dict(req.headers)
        return _FakeResponse(VALID_PAYLOAD)

    with mock.patch("urllib.request.urlopen", fake_urlopen):
        parsed = QwenProvider("test-key-not-a-secret", **kw).assess(
            _Evidence(), _Brief())
    return captured, parsed


class _Evidence:
    transcript_text = "Nobody doing shit about killing kids right now."
    signal_windows = None

    def level(self):
        return "high"


class ThinkingDisabledTests(unittest.TestCase):
    def test_request_disables_thinking_explicitly(self):
        captured, _ = _call_provider()
        self.assertIn("reasoning", captured["body"],
                      "request must always state its reasoning mode")
        self.assertEqual(captured["body"]["reasoning"], {"enabled": False})

    def test_thinking_is_opt_in_only(self):
        """Scoping requirement: genuine reasoning tasks may opt in explicitly."""
        captured, _ = _call_provider(reasoning_enabled=True)
        self.assertEqual(captured["body"]["reasoning"], {"enabled": True})

    def test_reasoning_key_is_never_absent(self):
        """Proves the code path cannot OMIT the flag and fall back to the
        provider's reasoning-on default."""
        for kw in ({}, {"reasoning_enabled": False}, {"reasoning_enabled": True}):
            captured, _ = _call_provider(**kw)
            self.assertIn("reasoning", captured["body"])


class StructuredOutputTests(unittest.TestCase):
    def test_json_schema_is_the_default_constraint(self):
        captured, _ = _call_provider()
        rf = captured["body"]["response_format"]
        self.assertEqual(rf["type"], "json_schema")
        self.assertEqual(rf["json_schema"], QWEN_RESPONSE_SCHEMA)

    def test_schema_matches_the_documented_response_contract(self):
        """The schema must mirror what _build_prompt already demanded, so the
        scoring contract is unchanged."""
        props = set(QWEN_RESPONSE_SCHEMA["schema"]["properties"])
        self.assertEqual(props, {"scores", "content_worthiness_score",
                                 "candidate_timestamps", "recommended_content_format",
                                 "recommended_angle", "reasons"})
        self.assertEqual(set(QWEN_RESPONSE_SCHEMA["schema"]["required"]),
                         {"scores", "content_worthiness_score",
                          "recommended_content_format", "recommended_angle", "reasons"})
        self.assertFalse(QWEN_RESPONSE_SCHEMA["schema"]["additionalProperties"])

    def test_json_object_mode_is_available(self):
        captured, _ = _call_provider(structured_output="json_object")
        self.assertEqual(captured["body"]["response_format"], {"type": "json_object"})

    def test_unsupported_structured_output_is_rejected(self):
        captured_holder = {}

        def fake_urlopen(req, timeout=None):
            captured_holder["body"] = json.loads(req.data.decode())
            return _FakeResponse(VALID_PAYLOAD)

        with mock.patch("urllib.request.urlopen", fake_urlopen):
            with self.assertRaises(ProviderError) as ctx:
                QwenProvider("test-key-not-a-secret",
                             structured_output="yaml_ish").assess(_Evidence(), _Brief())
        self.assertEqual(ctx.exception.code, "BAD_CONFIG")
        self.assertNotIn("body", captured_holder, "must fail before any HTTP call")


class BoundedBudgetTests(unittest.TestCase):
    def test_output_budget_is_bounded_not_reasoning_sized(self):
        """Measured need for this prompt with thinking off: 337-665 tokens."""
        captured, _ = _call_provider()
        self.assertEqual(captured["body"]["max_tokens"], 1200)
        self.assertLessEqual(captured["body"]["max_tokens"], 2000,
                             "budget must fit the schema, not compensate for reasoning")

    def test_default_budget_unchanged_from_contract(self):
        self.assertEqual(QwenProvider("k").max_output_tokens, 1200)


class ContractPreservationTests(unittest.TestCase):
    def test_prompt_is_unchanged(self):
        captured, _ = _call_provider()
        expected = _build_prompt(_Evidence.transcript_text, 12000, None)
        self.assertEqual(captured["body"]["messages"][0]["content"], expected)

    def test_valid_payload_still_parses_with_usage_and_identity(self):
        _, parsed = _call_provider()
        self.assertEqual(parsed["content_worthiness_score"], 75)
        self.assertEqual(parsed["usage"]["input_tokens"], 300)
        self.assertEqual(parsed["usage"]["output_tokens"], 350)
        self.assertIn("cost_usd", parsed["usage"])
        self.assertEqual(parsed["model_identity"]["provider"], "qwen")

    def test_invalid_json_still_raises_invalid_response(self):
        payload = {"choices": [{"message": {"content": ""}}],
                   "usage": {}, "model": QWEN_PINNED_MODEL}
        with mock.patch("urllib.request.urlopen",
                        lambda req, timeout=None: _FakeResponse(payload)):
            with self.assertRaises(ProviderError) as ctx:
                QwenProvider("test-key-not-a-secret").assess(_Evidence(), _Brief())
        self.assertEqual(ctx.exception.code, "INVALID_RESPONSE")

    def test_missing_key_still_raises_auth(self):
        with self.assertRaises(ProviderError) as ctx:
            QwenProvider("").assess(_Evidence(), _Brief())
        self.assertEqual(ctx.exception.code, "AUTH")


if __name__ == "__main__":
    unittest.main(verbosity=2)
