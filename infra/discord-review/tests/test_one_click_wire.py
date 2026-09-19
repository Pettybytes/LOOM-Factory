"""Production-shaped wire proof for Approve & Acquire.

The inbound response socket is deliberately poisoned after the ACK: any further use
of the request handler raises OSError Errno 9 — exactly what a closed descriptor
does. The background one-click path must still complete, because everything after
the ACK travels on independent outbound requests.

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
URL = f"https://www.youtube.com/watch?v={VID}"
FP = "fad6787e957e17c4c7730f0e0a1b2c3d"
CHANNEL = "1550207989569421444"
MESSAGE = "1550657694665412730"
PROV = {"window": {"start_ms": 65_000, "end_ms": 95_000, "clip_duration_ms": 30_000,
                   "origin": "operator_adjusted", "range_fingerprint": FP},
        "active_origin": "operator_adjusted",
        "original_suggestion": {"start_ms": 60_000, "end_ms": 90_000}}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class WireOneClickTests(unittest.TestCase):
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
        # Signature verification is the ONLY thing stubbed (no private key exists in
        # a test environment). It lives at module level, not on the handler, so fall
        # back to patching the module function when no handler hook exists.
        patcher = None
        for name in ("_verify_signature", "verify_signature", "_valid_signature"):
            if hasattr(ad.Handler, name):
                patcher = mock.patch.object(ad.Handler, name, lambda *a, **k: True)
                break
        if patcher is None:
            patcher = mock.patch.object(ad, "verify_signature", lambda *a, **k: True)
        patcher.start()
        self.addCleanup(patcher.stop)

        self.outbound = []
        self.acks = {"n": 0}
        self.real_send = ad.Handler._send_json

        def recording_http_json(method, url, token="", body=None, timeout=None, **kw):
            self.outbound.append({"method": method, "url": url, "token": token,
                                  "body": body})
            return 200, {"id": "1"}

        hp = mock.patch.object(ad, "http_json", side_effect=recording_http_json)
        hp.start(); self.addCleanup(hp.stop)

    def _seed_store(self):
        store = ops.MemoryLaneStore()
        store.source_contexts[VID] = {
            "platform": "youtube", "canonical_url": URL, "content_id": VID,
            "requested_start_s": 65.0, "requested_end_s": 95.0,
            "card_ref": json.dumps(PROV)}
        return store

    def _poisoned_send_json(self, allow=1):
        """Let the ACK through; every later write acts like a closed descriptor."""
        def sender(handler_self, status_code, obj):
            self.acks["n"] += 1
            if self.acks["n"] > allow:
                raise OSError(9, "Bad file descriptor")
            return self.real_send(handler_self, status_code, obj)
        return sender

    def _post(self, payload):
        req = urllib.request.Request(
            f"{self.base}/discord/interactions",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json",
                     "X-Signature-Ed25519": "stub", "X-Signature-Timestamp": "1"},
            method="POST")
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, json.loads(r.read().decode())

    def _interaction(self):
        return {"id": "1", "type": 3, "application_id": "app", "token": "***",
                "channel": {"id": CHANNEL},
                "message": {"id": MESSAGE},
                "member": {"user": {"id": "1", "username": "bryan"}},
                "data": {"custom_id": f"AQ:aa:{VID}#{FP[:16]}", "component_type": 2}}

    def test_ack_closes_the_socket_and_the_worker_still_completes(self):
        store = self._seed_store()
        ran = {"n": 0}

        def fake_runner(**_kw):
            ran["n"] += 1
            return {"ok": True, "status": "acquired", "local": True,
                    "library": {"asset_filename": f"youtube__{VID}__65-95__abc.mp4",
                                "asset_hash": "a" * 64},
                    "job_patch": {"status": "acquired"},
                    "provenance": {"adapter": "local:yt-dlp",
                                   "provider_cost_micro": 0}}

        with mock.patch.object(ad, "_acquisition_store", lambda: store), \
             mock.patch.object(ad, "_acquisition_live_runner", lambda: fake_runner), \
             mock.patch.object(ad.Handler, "_send_json", self._poisoned_send_json()):
            status, body = self._post(self._interaction())
            self.assertEqual(status, 200)
            self.assertEqual(body.get("type"), 6, "the wire ACK must be type 6")

            deadline = time.time() + 10
            while time.time() < deadline and ran["n"] == 0:
                time.sleep(0.05)
            time.sleep(0.6)                       # let the terminal update flush

        self.assertEqual(ran["n"], 1, "the acquisition must have run exactly once")

        # every post-ACK card change is an INDEPENDENT outbound request
        patches = [c for c in self.outbound if c["method"] == "PATCH"]
        self.assertGreaterEqual(len(patches), 2,
                                "ACQUIRING and the terminal update must both be PATCHed")
        for call in patches:
            self.assertIn(f"/channels/{CHANNEL}/messages/{MESSAGE}", call["url"])
            self.assertTrue(call["token"], "the bot token must travel on the outbound call")
            self.assertIsInstance(call["body"], dict)

        notes = [c for c in self.outbound if "webhooks" in c["url"]]
        blob = json.dumps(notes)
        self.assertNotIn("Errno 9", blob, "a Bad file descriptor must not occur")
        self.assertNotIn("Bad file descriptor", blob)

    def test_the_worker_never_writes_to_the_inbound_connection(self):
        store = self._seed_store()
        done = {"n": 0}

        def fake_runner(**_kw):
            done["n"] += 1
            return {"ok": True, "status": "acquired", "local": True,
                    "library": {"asset_filename": "x.mp4", "asset_hash": "a" * 64},
                    "job_patch": {"status": "acquired"},
                    "provenance": {"adapter": "local:yt-dlp"}}

        with mock.patch.object(ad, "_acquisition_store", lambda: store), \
             mock.patch.object(ad, "_acquisition_live_runner", lambda: fake_runner), \
             mock.patch.object(ad.Handler, "_send_json", self._poisoned_send_json()):
            status, body = self._post(self._interaction())
            deadline = time.time() + 10
            while time.time() < deadline and done["n"] == 0:
                time.sleep(0.05)
            time.sleep(0.4)

        self.assertEqual(self.acks["n"], 1,
                         f"the handler was used {self.acks['n']} times; only the ACK "
                         "may touch the inbound response")
        self.assertEqual(done["n"], 1)

    def test_stale_fingerprint_is_refused_on_the_real_route(self):
        store = self._seed_store()
        ran = {"n": 0}

        def fake_runner(**_kw):
            ran["n"] += 1
            return {"ok": True, "status": "acquired", "job_patch": {}}

        stale = {"id": "1", "type": 3, "application_id": "app", "token": "***",
                 "channel": {"id": CHANNEL}, "message": {"id": MESSAGE},
                 "member": {"user": {"id": "1", "username": "bryan"}},
                 "data": {"custom_id": f"AQ:aa:{VID}#839c64395f568fda",
                          "component_type": 2}}
        with mock.patch.object(ad, "_acquisition_store", lambda: store), \
             mock.patch.object(ad, "_acquisition_live_runner", lambda: fake_runner):
            status, body = self._post(stale)
            time.sleep(0.8)
        self.assertEqual((status, body.get("type")), (200, 6))
        self.assertEqual(ran["n"], 0, "a stale range must not reach the runner")


if __name__ == "__main__":
    unittest.main()
