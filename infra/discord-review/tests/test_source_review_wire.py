"""Signed local HTTP proof of source-review ACK, durable contracts and recovery.

Only database/Discord transports are replaced. The real signature verifier,
router, worker, decision writer, pending-job planner and card PATCH run together.
No real outbound request or paid provider can be reached by these tests.
"""

import contextlib
import io
import json
import sys
import threading
import time
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = Path(__file__).resolve().parents[3]
for directory in ("acquisition", "discord-review"):
    sys.path.insert(0, str(ROOT / "infra" / directory))

import acquisition_ops
import approved_lane
import adapter
import source_review

FP = "ab" * 32


class SourceReviewWireTests(unittest.TestCase):
    def setUp(self):
        self.signer = Ed25519PrivateKey.generate()
        public_key = self.signer.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(mock.patch.object(adapter, "DISCORD_PUBLIC_KEY", public_key))
        self.stack.enter_context(mock.patch.object(adapter, "SUPABASE_URL", "https://store.invalid"))
        self.stack.enter_context(mock.patch.object(adapter, "SUPABASE_KEY", "fixture-only"))
        self.reviews = {}
        self.store = acquisition_ops.MemoryLaneStore()
        self.lock = threading.Lock()
        self.release_lookup = threading.Event()
        self.lookup_started = threading.Event()
        self.patched = threading.Event()
        self.outbound = []
        self.steps = []
        self.candidate = {
            "tenant_id": "tenant-a", "profile_id": "segment5-wire",
            "duplicate_fingerprint": FP, "platform": "youtube",
            "platform_content_id": "ZaIDxlEuKpc", "title": "Wire fixture, not acceptance",
            "canonical_url": "https://www.youtube.com/watch?v=ZaIDxlEuKpc",
            "boundary_plan": {"event_timestamp": 2.75, "recommended_clip_start": 0.125,
                              "recommended_clip_end": 8.625, "start_anchors": []},
        }

        def get(_url, _key, path):
            self.steps.append("read")
            if path.startswith("loom_source_candidates?"):
                self.lookup_started.set()
                if not self.release_lookup.wait(5):
                    raise RuntimeError("test did not release the lookup")
                return [dict(self.candidate)]
            if path.startswith("loom_source_reviews?"):
                return [dict(self.reviews[FP])] if FP in self.reviews else []
            raise AssertionError("unexpected store read: " + path)

        def post(_url, _key, table, body, *, on_conflict, ignore_duplicates):
            self.assertEqual((table, on_conflict, ignore_duplicates),
                             ("loom_source_reviews", "duplicate_fingerprint", True))
            self.steps.append("persist")
            with self.lock:
                if body["duplicate_fingerprint"] in self.reviews:
                    return 201, []
                self.reviews[body["duplicate_fingerprint"]] = dict(body)
                return 201, [dict(body)]

        def outbound(request, timeout=None):
            self.assertEqual(request.get_method(), "PATCH")
            self.assertIn("/webhooks/", request.full_url)
            self.outbound.append(json.loads(request.data))
            self.steps.append("patch")
            self.patched.set()
            return contextlib.closing(io.BytesIO(b"{}"))

        self.stack.enter_context(mock.patch.object(source_review, "_get", side_effect=get))
        self.stack.enter_context(mock.patch.object(source_review, "_post", side_effect=post))
        self.stack.enter_context(mock.patch.object(adapter, "_acquisition_store", return_value=self.store))
        self.stack.enter_context(mock.patch.object(adapter, "urlopen", side_effect=outbound))
        self.stack.enter_context(mock.patch.object(adapter, "http_json", side_effect=AssertionError("unexpected outbound")))
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), adapter.Handler)
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()
        self.addCleanup(self.close_server)

    def close_server(self):
        self.release_lookup.set()
        self.server.shutdown()
        self.server.server_close()
        self.server_thread.join(2)

    def post_interaction(self, custom_id, member=None, component_type=2, values=None):
        payload = {"type": 3, "application_id": "fixture-app", "token": "fixture-token",
                   "member": member or {"nick": "Original reviewer", "user": {"id": "1", "username": "reviewer"}},
                   "data": {"custom_id": custom_id, "component_type": component_type}}
        if values is not None:
            payload["data"]["values"] = values
        body = json.dumps(payload).encode()
        timestamp = str(int(time.time()))
        signature = self.signer.sign(timestamp.encode() + body).hex()
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.server.server_port}/discord/interactions", data=body,
            headers={"Content-Type": "application/json", "X-Signature-Ed25519": signature,
                     "X-Signature-Timestamp": timestamp}, method="POST")
        started = time.monotonic()
        with urllib.request.urlopen(request, timeout=2) as response:
            result = response.status, json.loads(response.read())
        self.assertLess(time.monotonic() - started, 1.5)
        return result

    def test_valid_sha256_ack_precedes_io_and_duplicate_decisions_keep_one_pending_job(self):
        approve = source_review.make_source_custom_id(source_review.DECISION_APPROVE, FP)
        self.assertEqual(self.post_interaction(approve), (200, {"type": 6}))
        self.assertFalse(self.release_lookup.is_set(), "wire ACK must not wait for lookup")
        self.assertEqual(self.reviews, {})
        self.release_lookup.set()
        self.assertTrue(self.patched.wait(3), "worker must persist and update after ACK")
        self.assertLess(self.steps.index("persist"), self.steps.index("patch"))
        self.assertEqual(len(self.store.jobs), 1)
        job = next(iter(self.store.jobs.values()))
        self.assertEqual(job["authorization_type"], "approved_for_acquisition")
        self.assertEqual(job["status_reason"], approved_lane.REASON_PENDING_AUTHORIZATION)
        self.assertEqual((job["requested_start_s"], job["requested_end_s"]), (0.125, 8.625))
        self.assertEqual(job["correlation_id"], FP)
        lineage = json.loads(job["rights_usage_note"])
        self.assertEqual((lineage["tenant_id"], lineage["profile_id"]), ("tenant-a", "segment5-wire"))
        self.assertEqual(lineage["boundary_plan"], self.candidate["boundary_plan"])

        # A repeated approval AND a losing reject must keep the original winner.
        # Reject is two-step: the button renders the reason select (type 7);
        # only the select submission (component_type 3 + chosen value) reaches
        # the worker and can become the losing attempted decision.
        for decision, ctype, values in (
            (source_review.DECISION_APPROVE, 2, None),
            (source_review.DECISION_REJECT, 3, ["weak_moment"]),
        ):
            self.patched.clear()
            cid = source_review.make_source_custom_id(decision, FP)
            self.assertEqual(self.post_interaction(cid, {"user": {"username": "second reviewer"}},
                                                   component_type=ctype, values=values),
                             (200, {"type": 6}))
            self.assertTrue(self.patched.wait(3))
        self.assertEqual(len(self.reviews), 1)
        self.assertEqual(len(self.store.jobs), 1)
        self.assertEqual(self.reviews[FP]["decision"], source_review.DECISION_APPROVE)
        self.assertEqual(self.reviews[FP]["reviewer"], "Original reviewer")
        self.assertIn("Original reviewer", json.dumps(self.outbound[-1]))
        self.assertIn("00:00.125", json.dumps(self.outbound[-1]))
        self.assertIn("00:08.625", json.dumps(self.outbound[-1]))
        self.assertEqual(self.store.library, [])

    def test_reject_button_renders_reason_select_then_reason_persists_with_no_job(self):
        self.release_lookup.set()
        reject = source_review.make_source_custom_id(source_review.DECISION_REJECT, FP)

        # Step 1: the Reject button renders the structured reason select on the
        # card (type 7) and persists nothing — no review, no phantom job.
        status, reply = self.post_interaction(reject)
        self.assertEqual(reply["type"], 7)
        select = reply["data"]["components"][0]["components"][0]
        self.assertEqual(select["type"], 3)
        self.assertEqual(select["custom_id"], reject)
        self.assertEqual(len(select["options"]), 3)
        self.assertEqual(self.reviews, {})
        self.assertEqual(self.store.jobs, {})

        # Step 2: the chosen reason reaches the worker; the decision persists
        # with rejection_category, renders the reason on the card, and creates
        # NO acquisition job (reject never queues work).
        self.assertFalse(self.lookup_started.is_set())
        self.assertEqual(self.post_interaction(reject, values=["off_theme"], component_type=3),
                         (200, {"type": 6}))
        self.assertTrue(self.patched.wait(3))
        self.assertEqual(self.reviews[FP]["decision"], source_review.DECISION_REJECT)
        self.assertEqual(self.reviews[FP]["rejection_category"], "off_theme")
        self.assertEqual(self.store.jobs, {})
        self.assertIn(source_review.SOURCE_REJECT_REASONS["off_theme"],
                      json.dumps(self.outbound[-1]))

    def test_reject_select_with_unknown_reason_is_refused_without_persistence(self):
        self.release_lookup.set()
        reject = source_review.make_source_custom_id(source_review.DECISION_REJECT, FP)
        status, reply = self.post_interaction(reject, values=["bogus"], component_type=3)
        self.assertEqual(reply, {"type": 4, "data": {"content": "Unknown reject reason.", "flags": 64}})
        self.assertEqual(self.reviews, {})
        self.assertEqual(self.store.jobs, {})

    def test_legacy_eleven_character_source_id_is_private_rejection_not_worker_ack(self):
        status, reply = self.post_interaction("sa_ZaIDxlEuKpc")
        self.assertEqual(status, 200)
        self.assertEqual(reply, {"type": 4, "data": {"content": "Invalid button.", "flags": 64}})
        self.assertFalse(self.lookup_started.is_set())
        self.assertEqual((self.reviews, self.store.jobs, self.outbound), ({}, {}, []))

    def test_pending_job_exception_is_observable_and_keeps_retry_controls(self):
        self.release_lookup.set()
        log = io.StringIO()
        with mock.patch.object(self.store, "create_job", side_effect=RuntimeError("secret-do-not-log")), \
             contextlib.redirect_stdout(log):
            cid = source_review.make_source_custom_id(source_review.DECISION_APPROVE, FP)
            self.assertEqual(self.post_interaction(cid), (200, {"type": 6}))
            self.assertTrue(self.patched.wait(3))
        self.assertEqual(len(self.reviews), 1)
        self.assertEqual(self.store.jobs, {})
        self.assertIn("Decision saved", self.outbound[-1]["content"])
        self.assertIn("pending_job", self.outbound[-1]["content"])
        self.assertNotIn("components", self.outbound[-1], "failure must not remove Retry controls")
        self.assertIn("stage=pending_job", log.getvalue())
        self.assertIn("frames=", log.getvalue())
        self.assertNotIn("secret-do-not-log", log.getvalue())

    def test_malformed_identity_is_inside_worker_error_boundary(self):
        log = io.StringIO()
        with contextlib.redirect_stdout(log):
            cid = source_review.make_source_custom_id(source_review.DECISION_APPROVE, FP)
            self.assertEqual(self.post_interaction(cid, {"user": ["not-a-user-object"]}),
                             (200, {"type": 6}))
            self.assertTrue(self.patched.wait(3))
        self.assertEqual(self.reviews, {})
        self.assertIn("identity", self.outbound[-1]["content"])
        self.assertNotIn("components", self.outbound[-1])
        self.assertIn("stage=identity", log.getvalue())

    def test_reporting_transport_failure_cannot_escape_worker(self):
        handler = adapter.Handler.__new__(adapter.Handler)
        handler._reviewer_name = mock.Mock(side_effect=ValueError("private-identity-value"))
        handler._patch_original = mock.Mock(side_effect=RuntimeError("private-transport-value"))
        log = io.StringIO()
        with contextlib.redirect_stdout(log):
            handler._source_button_worker(
                {"application_id": "fixture-app", "token": "fixture-token"},
                {"fingerprint": FP, "decision": source_review.DECISION_APPROVE})
        self.assertIn("stage=identity", log.getvalue())
        self.assertIn("card_update_failed", log.getvalue())
        self.assertNotIn("private-identity-value", log.getvalue())
        self.assertNotIn("private-transport-value", log.getvalue())
        self.assertNotIn("fixture-token", log.getvalue())


if __name__ == "__main__":
    unittest.main()
