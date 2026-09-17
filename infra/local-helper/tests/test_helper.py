"""Deterministic tests for local-helper HTTP request validation.

Run:  python3 -m unittest discover -s infra/local-helper/tests -v
"""

import json
import sys
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO_ROOT / "infra" / "local-helper"))

import helper  # noqa: E402


class TestValidateRequest(unittest.TestCase):
    def test_missing_token_configured(self):
        self.assertEqual(helper.validate_request(None, None, b"{}", ""), "helper token not configured")

    def test_missing_auth(self):
        self.assertEqual(helper.validate_request(None, "chrome-extension://x", b"{}", "tok"),
                         "unauthorized")

    def test_wrong_token(self):
        self.assertEqual(helper.validate_request("Bearer wrong", "chrome-extension://x", b"{}", "tok"),
                         "unauthorized")

    def test_valid(self):
        self.assertIsNone(helper.validate_request("Bearer tok", "chrome-extension://x", b"{}", "tok"))

    def test_bad_origin(self):
        self.assertEqual(helper.validate_request("Bearer tok", "https://evil.com", b"{}", "tok"),
                         "invalid origin")

    def test_body_too_large(self):
        big = b"x" * (helper.MAX_BODY + 1)
        self.assertEqual(helper.validate_request("Bearer tok", "chrome-extension://x", big, "tok"),
                         "request body too large")


class TestParseBody(unittest.TestCase):
    def test_valid(self):
        body = json.dumps({"canonical_url": "https://www.instagram.com/reel/x",
                           "candidate_url": "https://scontent.cdninstagram.com/v/a.mp4"}).encode()
        payload, err = helper.parse_body(body)
        self.assertIsNone(err)
        self.assertEqual(payload["canonical_url"], "https://www.instagram.com/reel/x")

    def test_invalid_json(self):
        payload, err = helper.parse_body(b"not json")
        self.assertEqual(err, "invalid JSON")

    def test_missing_canonical(self):
        body = json.dumps({"candidate_url": "https://x"}).encode()
        payload, err = helper.parse_body(body)
        self.assertEqual(err, "missing canonical_url")

    def test_missing_candidate(self):
        body = json.dumps({"canonical_url": "https://x"}).encode()
        payload, err = helper.parse_body(body)
        self.assertEqual(err, "missing candidate_url")

    def test_default_actor(self):
        body = json.dumps({"canonical_url": "https://x", "candidate_url": "https://y"}).encode()
        payload, _ = helper.parse_body(body)
        self.assertEqual(payload["actor"], "bryan")


if __name__ == "__main__":
    unittest.main(verbosity=2)
