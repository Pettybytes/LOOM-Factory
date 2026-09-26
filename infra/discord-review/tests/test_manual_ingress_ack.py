"""Targeted tests: Manual Intake ingress ACK/defer seam (2026-09-26 live defect).

Observed live: a valid-media post in #manual-intake returned "didn't respond in
time", while the wrong-channel path answered fine. Two causes, both pinned here:

  1. The valid path replied with ``type: 6`` (component defer). An application
     command must defer as ``type: 5``; Discord rejects type 6 there, which the
     operator sees as a timeout. The wrong-channel path used ``type: 4`` and worked.
  2. The channel guard resolved a channel over the network BEFORE responding,
     which must happen after the defer.

Also pinned: the service runtime (run.sh) does not put infra/ on sys.path, so the
shared LOOM packages must be importable from these modules anyway; and a re-run
must not upload the same bytes twice.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

_REPO = Path(__file__).resolve().parents[3]
_DR = str(_REPO / "infra" / "discord-review")
for _p in (_DR, str(_REPO / "infra"), str(_REPO / "infra" / "acquisition")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

if not os.environ.get("LOOM_SUPABASE_URL"):
    _env_file = Path.home() / ".openclaw/.env"
    if _env_file.exists():
        for _line in _env_file.read_text().splitlines():
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))
os.environ.setdefault("LOOM_ACQUISITION_LIVE", "0")

import adapter  # noqa: E402
import manual_media_intake as mmi  # noqa: E402


def _handler():
    h = object.__new__(adapter.Handler)
    h.sent = []
    h._send_json = lambda code, obj: h.sent.append((code, obj))
    h._reviewer_name = lambda interaction: "bryan#test"
    return h


def _interaction(content="url: https://www.tiktok.com/@a/video/123", attachments=None):
    return {"data": {"type": 3, "name": "intake-media", "target_id": "m1",
                     "resolved": {"messages": {"m1": {
                         "content": content,
                         "attachments": attachments if attachments is not None else [
                             {"filename": "a.mp4", "content_type": "video/mp4", "url": "u"}],
                         "channel_id": "c1"}}}},
            "application_id": "app", "token": "tok"}


class TestIngressAck(unittest.TestCase):
    def test_defers_with_command_defer_type_and_no_network_first(self):
        h = _handler()
        started = []

        class _Thread:
            def __init__(self, target=None, args=(), daemon=None, **kw):
                started.append(args)

            def start(self):
                pass

        with mock.patch.object(adapter, "http_json",
                               side_effect=AssertionError("network work before ACK")), \
                mock.patch.object(adapter.threading, "Thread", _Thread):
            h._handle_manual_media_intake(_interaction())
        self.assertEqual(h.sent[0], (200, {"type": 5, "data": {"flags": 64}}),
                         "an application command must defer as type 5, not type 6")
        self.assertEqual(len(h.sent), 1, "the ACK must be the only pre-worker response")
        self.assertTrue(started, "durable work is handed to the worker after the defer")

    def test_missing_input_answers_immediately_without_network(self):
        h = _handler()
        with mock.patch.object(adapter, "http_json",
                               side_effect=AssertionError("network work before reply")):
            h._handle_manual_media_intake(_interaction(content="no url here", attachments=[]))
        code, body = h.sent[0]
        self.assertEqual(code, 200)
        self.assertEqual(body["type"], 4)
        self.assertIn("Cannot ingest", body["data"]["content"])

    def test_channel_guard_runs_after_the_defer(self):
        """The guard resolves a channel over the network — never before the ACK."""
        source = (_REPO / "infra/discord-review/adapter.py").read_text()
        handler_src = source.split("def _handle_manual_media_intake")[1]
        handler_src = handler_src.split("def _manual_media_worker")[0]
        self.assertNotIn("resolve_manual_intake_channel", handler_src,
                         "channel resolution must live in the worker, after the defer")
        worker_src = source.split("def _manual_media_worker")[1]
        self.assertIn("resolve_manual_intake_channel", worker_src)


class TestServiceRuntimeImports(unittest.TestCase):
    def test_modules_bootstrap_infra_for_the_service_runtime(self):
        code = ("import sys; sys.path[:0] = [%r, %r];"
                "import manual_media_intake, manual_intake_intelligence;"
                "import content_intelligence.pipeline, discovery.store_supabase;"
                "print('OK')" % (_DR, str(_REPO / "infra" / "acquisition")))
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                             cwd=_DR, timeout=120)
        self.assertIn("OK", out.stdout, out.stderr[-400:])


class TestRetryMediaReuse(unittest.TestCase):
    class _Resp:
        def __init__(self, payload):
            self._p = json.dumps(payload).encode()

        def read(self):
            return self._p

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def test_reuses_only_on_exact_byte_match(self):
        rows = [{"media_reference": {"url": "https://cdn/x.mp4", "sha256": "abc",
                                     "file_id": "f1"}}]
        with mock.patch.object(mmi.urllib.request, "urlopen",
                               return_value=self._Resp(rows)):
            ref = mmi.find_existing_media("http://sb", "k", "tiktok",
                                          "https://www.tiktok.com/@a/video/123",
                                          "dreemzy", "abc")
        self.assertEqual(ref["url"], "https://cdn/x.mp4")

    def test_different_bytes_are_not_reused(self):
        rows = [{"media_reference": {"url": "https://cdn/x.mp4", "sha256": "abc"}}]
        with mock.patch.object(mmi.urllib.request, "urlopen",
                               return_value=self._Resp(rows)):
            ref = mmi.find_existing_media("http://sb", "k", "tiktok",
                                          "https://www.tiktok.com/@a/video/123",
                                          "dreemzy", "different")
        self.assertIsNone(ref)


if __name__ == "__main__":
    unittest.main(verbosity=2)
