"""Existing OpenShorts Auto Edit: credential transport and resolved-state reopen.

No network, provider calls, storage mutations or media acquisition.
"""
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import adapter
import source_review


class Response:
    status = 200
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def read(self):
        return b'{"success":true}'


class AutoEditGatewayTests(unittest.TestCase):
    def test_configured_key_reaches_only_the_existing_edit_endpoint(self):
        handler = SimpleNamespace(headers={"Content-Type": "application/json",
                                            "X-Gemini-Key": "unit-test-key",
                                            "Authorization": "Bearer editor-session"})
        for method, path, expected in [("POST", "/api/edit", "unit-test-key"),
                                        ("POST", "/api/clip/rerender", None),
                                        ("GET", "/api/edit", None)]:
            with self.subTest(method=method, path=path), \
                 mock.patch.object(adapter, "urlopen", return_value=Response()) as opener:
                adapter.Handler._backend_request(handler, method, path, b'{}')
                req = opener.call_args.args[0]
                headers = {k.lower(): v for k, v in req.header_items()}
                self.assertEqual(headers.get("x-gemini-key"), expected)
                self.assertNotIn("authorization", headers)
                self.assertEqual(req.full_url, "http://127.0.0.1:8000" + path)
                self.assertNotIn(b"unit-test-key", req.data)

    def test_auto_edit_keeps_the_existing_job_and_clip_ownership_guard(self):
        for job, clip in [("someone-else", 0), ("ours", 1)]:
            target = adapter.Handler._authorize_editor_path(
                SimpleNamespace(), "POST", "/api/edit",
                json.dumps({"job_id": job, "clip_index": clip}).encode(),
                "ours", "0", "server-owned-workspace")
            self.assertIsNone(target)


class GeneratedStateReopenTests(unittest.TestCase):
    def bootstrap(self, metadata):
        return source_review.edited_version_bootstrap(
            {"tenant_id": "test", "title": "source"},
            {"clean_source_url": "https://example.test/generated.mp4",
             "duration_seconds": 26, "asset_version": "render-1.2"},
            {"trim_start": 0, "trim_end": 26, "edl_metadata": metadata},
            "render-1.2")

    def test_generated_plan_survives_registration_correction_to_fresh_bootstrap(self):
        state = {"status": "completed", "provider": "gemini",
                 "captions": [{"text": "hello", "startMs": 0, "endMs": 500}],
                 "generated_copy": {"title": "Generated title", "hook": "Generated hook"},
                 "edit_plan": {"edits": [{"type": "punch_in", "start": 1, "end": 3}]}}
        payload, reason = self.bootstrap({"auto_edit": state})
        self.assertEqual(reason, "ok")
        self.assertEqual(payload["auto_edit"], state)
        self.assertEqual(payload["captions"], state["captions"])
        self.assertEqual(payload["caption_text"], "hello")
        self.assertEqual(payload["title"], "Generated title")
        self.assertEqual(payload["hook"], "Generated hook")
        self.assertEqual(payload["media_url"], "https://example.test/generated.mp4")
        self.assertEqual(payload["asset_version"], "render-1.2")

    def test_legacy_render_and_auto_framing_do_not_claim_gemini_analysis(self):
        for metadata in ({"framing": "auto"}, None, "legacy"):
            with self.subTest(metadata=metadata):
                payload, reason = self.bootstrap(metadata)
                self.assertEqual(reason, "ok")
                self.assertIsNone(payload["auto_edit"])


if __name__ == "__main__":
    unittest.main()
