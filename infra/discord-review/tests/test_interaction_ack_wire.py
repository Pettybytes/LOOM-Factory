"""WIRE-LEVEL proof that a modal submit is ACKed before any slow work.

Real HTTP against the adapter's own request handler on a real socket: the response
must come back while a deliberately slow source-context call is still running.
A call-order assertion inside the handler would not prove this.

Run:  python3 -m unittest discover -s infra/discord-review/tests -v
"""

import json
import socket
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

_REPO = Path(__file__).resolve().parents[3]
for d in ("acquisition", "discord-review"):
    sys.path.insert(0, str(_REPO / "infra" / d))

import acquisition_ops as ops  # noqa: E402
import adapter as ad  # noqa: E402

VID = "ClhBXVygWbc"
SLOW_SECONDS = 4.0          # comfortably past Discord's 3-second deadline
DEADLINE = 1.5              # what the wire must beat


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _SlowStore:
    """A store whose durable reads are slow; writes are recorded."""

    def __init__(self, *, sleep=SLOW_SECONDS, fail=False):
        self.sleep = sleep
        self.fail = fail
        self.reads: list[str] = []
        self.writes: list[tuple] = []
        self.jobs: dict[str, dict] = {}
        self.read_started_at: float | None = None
        self.read_finished_at: float | None = None

    def source_context(self, ref):
        self.reads.append(ref)
        self.read_started_at = time.time()
        time.sleep(self.sleep)                      # the slow Supabase call
        self.read_finished_at = time.time()
        if self.fail:
            raise RuntimeError("supabase unavailable")
        if not isinstance(ref, str):
            raise TypeError("ref must be str")
        import source_context as sc
        rows = [{"platform": "youtube",
                 "source_url": f"https://www.youtube.com/watch?v={VID}",
                 "duplicate_fingerprint": "f" * 64,
                 "reviewer_note": json.dumps(
                     {"window": {"start_ms": 60_000, "end_ms": 90_000,
                                 "range_fingerprint": "a" * 64},
                      "suggestion_origin": "automated_source_engine"}),
                 "time_start_s": 60.0, "time_end_s": 90.0}]
        resolved = sc.resolve_context(ref, get=lambda _p: rows)
        return resolved.as_dict() if resolved else None

    def update_source_window(self, ref, *, start_s, end_s, provenance=None):
        self.writes.append((ref, start_s, end_s, provenance))
        return {"submission": [{"time_start_s": start_s}], "job": []}

    def pending_job(self, ref):
        return self.jobs.get(ref)


class WireTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.port = _free_port()
        cls.server = ThreadingHTTPServer(("127.0.0.1", cls.port), ad.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        # Signature verification is the ONLY thing stubbed: no private key exists
        # in a test environment, and the wire behaviour is what we are proving.
        patcher = None
        for name in ("_verify_signature", "verify_signature", "_valid_signature"):
            if hasattr(ad.Handler, name):
                patcher = mock.patch.object(ad.Handler, name, lambda *a, **k: True)
                break
        if patcher is None:
            patcher = mock.patch.object(ad, "verify_signature", lambda *a, **k: True)
        patcher.start()
        self.addCleanup(patcher.stop)

        self.http_calls: list[tuple] = []

        # mirror the adapter's REAL signature: (method, url, token=None, body=None,
        # timeout=15). A PATCH card edit passes the bot token positionally.
        def fake_http_json(method, url, token=None, body=None, timeout=None, **kw):
            self.http_calls.append((method, url, body, token))
            return 200, {"id": "1"}

        hp = mock.patch.object(ad, "http_json", side_effect=fake_http_json)
        hp.start()
        self.addCleanup(hp.stop)
        op = mock.patch.object(ops, "http_json", side_effect=fake_http_json, create=True)
        op.start()
        self.addCleanup(op.stop)

    def _post(self, payload: dict, *, timeout=10):
        req = urllib.request.Request(
            f"{self.base}/discord/interactions",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json",
                     "X-Signature-Ed25519": "stub", "X-Signature-Timestamp": "1"},
            method="POST")
        started = time.time()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = r.read().decode()
                code = r.status
        except urllib.error.HTTPError as e:
            body, code = e.read().decode(), e.code
        return code, body, time.time() - started

    def _modal(self, start="01:05", end="01:35"):
        cid = ops.window_modal_payload(VID, message_id="1550657694665412730")["data"]["custom_id"]
        return {"id": "1", "type": 5, "application_id": "app", "token": "tok",
                "channel": {"id": "1550207989569421444"},
                "member": {"user": {"id": "1", "username": "bryan"}},
                "user": {"id": "1", "username": "bryan"},
                "data": {"custom_id": cid, "components": [
                    {"components": [{"custom_id": "window_start", "value": start}]},
                    {"components": [{"custom_id": "window_end", "value": end}]}]}}

    def test_response_is_flushed_before_a_slow_source_context_completes(self):
        store = _SlowStore()
        with mock.patch.object(ad, "_acquisition_store", lambda: store):
            code, body, elapsed = self._post(self._modal())
            t_response = time.time()
            self.assertEqual(code, 200)
            self.assertEqual(json.loads(body).get("type"), 6,
                             "the custom_id ACK must be type 6")
            self.assertLess(elapsed, DEADLINE,
                            f"ACK took {elapsed:.2f}s — slow work blocked the wire")
            # The wire proof: the response came back WHILE the slow durable read was
            # still in flight, i.e. it never waited for it to finish.
            # If the slow read had already finished, the ACK waited for it. It must
            # not have. (If it hasn't started yet, the ACK was even earlier.)
            if store.read_finished_at is not None:
                self.assertLessEqual(t_response, store.read_finished_at,
                                     "the ACK waited for the slow durable read")
            # the health endpoint stays responsive while the worker is busy
            code2, _, elapsed2 = self._post({"type": 1})  # PING
            self.assertLess(elapsed2, DEADLINE)
            self.assertEqual(code2, 200)

            deadline = time.time() + SLOW_SECONDS + 8
            while time.time() < deadline:
                if store.writes and any(c[0] == "PATCH" for c in self.http_calls):
                    break
                time.sleep(0.05)
        self.assertTrue(store.writes, "persistence must happen AFTER the ACK")
        ref, start_s, end_s, prov = store.writes[0]
        self.assertEqual((ref, start_s, end_s), (VID, 65.0, 95.0))
        self.assertEqual(prov["active_origin"], "operator_adjusted")
        patched = [c for c in self.http_calls if c[0] == "PATCH"]
        self.assertTrue(patched, "the card must be PATCHed after the ACK")

    def test_worker_exception_is_private_and_never_kills_the_adapter(self):
        store = _SlowStore(sleep=0.1, fail=True)
        with mock.patch.object(ad, "_acquisition_store", lambda: store):
            code, body, elapsed = self._post(self._modal())
            self.assertEqual((code, json.loads(body).get("type")), (200, 6))
            time.sleep(1.0)
            code2, body2, _ = self._post({"type": 1})
        self.assertEqual(code2, 200, "the adapter must survive a worker exception")
        follows = [c for c in self.http_calls if "webhooks" in str(c[1])]
        self.assertTrue(follows, "a failed worker must send a private follow-up")

    def test_duplicate_submissions_are_idempotent(self):
        store = _SlowStore(sleep=0.1)
        with mock.patch.object(ad, "_acquisition_store", lambda: store):
            first = self._post(self._modal())
            second = self._post(self._modal())
            self.assertEqual(json.loads(first[1]).get("type"), 6)
            self.assertEqual(json.loads(second[1]).get("type"), 6,
                             "a duplicate submission is ACKed the same way")
            deadline = time.time() + 4
            while time.time() < deadline and len(store.writes) < 2:
                time.sleep(0.1)
        self.assertEqual([(w[1], w[2]) for w in store.writes], [(65.0, 95.0)] * 2,
                         "the same window is written the same way — no divergence")


if __name__ == "__main__":
    unittest.main()
