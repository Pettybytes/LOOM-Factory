"""Regression: Source Review buttons must ACK inside Discord's 3-second deadline.

Observed defect (2026-09-21): Bryan pressed Reject on both real Source Review
cards and both answered "The application didn't respond in time". Cause: the
source-review branch did a Supabase READ (fetch_candidate_by_fingerprint) and a
Supabase WRITE (persist_decision) BEFORE replying, so the interaction exceeded
Discord's 3-second deadline.

Fix: acknowledge with a deferred update ({"type": 6}) first, then do the network
work on a detached worker and PATCH the original message — the same discipline
the proven acquisition lane already used.

These tests pin the ORDERING, which is the property that was wrong, and that the
worker still persists and updates the message.
"""

from __future__ import annotations

import sys
import unittest
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from unittest import mock

_SUITE = Path(__file__).resolve().parents[1]
if str(_SUITE) not in sys.path:
    sys.path.insert(0, str(_SUITE))

import adapter  # noqa: E402
import source_review  # noqa: E402

_Handler = next(
    c for c in vars(adapter).values()
    if isinstance(c, type) and issubclass(c, BaseHTTPRequestHandler)
    and c is not BaseHTTPRequestHandler
)


def _handler():
    """Real dispatch/worker methods, no socket (bypasses __init__)."""
    h = _Handler.__new__(_Handler)
    h.sent = []
    h._send_json = lambda code, obj: h.sent.append((code, obj))
    h._reviewer_name = lambda interaction: "bryan#test"
    return h


class SourceReviewAckOrderingTests(unittest.TestCase):
    def test_source_button_acknowledges_before_any_network_work(self):
        h = _handler()
        cid = source_review.make_source_custom_id(source_review.DECISION_REJECT, "f" * 64)
        interaction = {"data": {"custom_id": cid, "component_type": 2},
                       "application_id": "123", "token": "tok"}
        started: list[dict] = []

        def forbidden_fetch(*a, **k):
            raise AssertionError("network work ran BEFORE the ACK")

        class _FakeThread:
            def __init__(self, target=None, args=(), daemon=None, **kw):
                started.append({"target": getattr(target, "__name__", str(target)),
                                "args": args, "daemon": daemon})

            def start(self):
                pass

        with mock.patch.object(source_review, "fetch_candidate_by_fingerprint", forbidden_fetch), \
                mock.patch.object(adapter.threading, "Thread", _FakeThread):
            # Approve still ACKs deferred first, then hands to the worker.
            approve = source_review.make_source_custom_id(source_review.DECISION_APPROVE, "f" * 64)
            h._handle_button({"data": {"custom_id": approve, "component_type": 2},
                              "application_id": "123", "token": "tok"})
            self.assertEqual(h.sent[-1], (200, {"type": 6}),
                             "approve must be the deferred acknowledgement")
            self.assertEqual(started[-1]["target"], "_source_button_worker")
            self.assertTrue(started[-1]["daemon"], "worker must be a daemon thread")

            # Reject BUTTON never reaches the worker: it renders the
            # structured reason select (type 7) with no network work at all.
            workers_before = len(started)
            h._handle_button(interaction)
            self.assertEqual(h.sent[-1][0], 200)
            self.assertEqual(h.sent[-1][1]["type"], 7,
                             "reject button must render the reason select")
            select = h.sent[-1][1]["data"]["components"][0]["components"][0]
            self.assertEqual(select["type"], 3)
            self.assertEqual(len(select["options"]),
                             len(source_review.SOURCE_REJECT_REASONS))
            self.assertEqual(len(started), workers_before,
                             "reject button must not start a worker or persist")

            # The reason submission ACKs deferred first, then hands to the
            # worker with the chosen reason.
            reason = next(iter(source_review.SOURCE_REJECT_REASONS))
            h._handle_button({"data": {"custom_id": cid, "component_type": 3,
                                       "values": [reason]},
                              "application_id": "123", "token": "tok"})
            self.assertEqual(h.sent[-1], (200, {"type": 6}),
                             "reason submission must be the deferred acknowledgement")
            self.assertEqual(started[-1]["target"], "_source_button_worker")
            self.assertEqual(started[-1]["args"][1].get("reason"), reason,
                             "the chosen reason must ride to the worker")


class SourceReviewWorkerTests(unittest.TestCase):
    def test_reviewer_exception_is_observable_not_a_silent_thread_death(self):
        h = _handler()
        patches = []
        h._patch_original = lambda *args, **kw: patches.append(kw)
        h._reviewer_name = mock.Mock(side_effect=ValueError("malformed identity"))
        with mock.patch.object(source_review, "persist_decision") as persist:
            h._source_button_worker({"application_id": "1", "token": "t"},
                                    {"fingerprint": "f" * 64, "decision": "hold"})
        persist.assert_not_called()
        self.assertIn("identity", patches[0]["content"])

    def test_stored_approval_recovers_missing_dispatch_and_keeps_winning_reviewer(self):
        h = _handler()
        h._patch_original = mock.Mock()
        h._source_approve_handoff = mock.Mock(return_value="pending")
        cand = {"duplicate_fingerprint": "f" * 64}
        winner = {"decision": source_review.DECISION_APPROVE, "reviewer": "original operator"}
        with mock.patch.object(source_review, "fetch_candidate_by_fingerprint", return_value=cand), \
             mock.patch.object(source_review, "persist_decision", return_value=(winner, False)):
            h._source_button_worker({"application_id": "1", "token": "t"},
                                   {"fingerprint": "f" * 64, "decision": "hold"})
        h._source_approve_handoff.assert_called_once_with(cand, "original operator")
        self.assertIn("original operator", str(h._patch_original.call_args))

    def test_failure_to_patch_does_not_claim_decision_was_not_saved(self):
        h = _handler()
        h._patch_original = mock.Mock(side_effect=[RuntimeError("patch failed"), None])
        with mock.patch.object(source_review, "fetch_candidate_by_fingerprint",
                               return_value={"duplicate_fingerprint": "f" * 64}), \
             mock.patch.object(source_review, "persist_decision", return_value=({"decision": "hold"}, True)):
            h._source_button_worker({"application_id": "1", "token": "t"},
                                   {"fingerprint": "f" * 64, "decision": "hold"})
        self.assertIn("Decision saved", h._patch_original.call_args.kwargs["content"])

    def test_worker_persists_decision_then_patches_original(self):
        h = _handler()
        patches: list[dict] = []
        h._patch_original = lambda app, tok, **kw: patches.append(kw)
        cand = {"duplicate_fingerprint": "f" * 64, "title": "proof"}
        persisted: list[dict] = []

        def fake_persist(url, key, payload):
            persisted.append(payload)
            return dict(payload), True

        def fake_payload(c, d, r, category=None, instructions=None):
            return {"decision": d, "reviewer": r, "rejection_category": category}

        with mock.patch.object(source_review, "fetch_candidate_by_fingerprint",
                               lambda *a, **k: cand), \
                mock.patch.object(source_review, "source_decision_payload", fake_payload), \
                mock.patch.object(source_review, "persist_decision", fake_persist), \
                mock.patch.object(source_review, "decided_embed",
                                  lambda d, r, c, category=None: {"title": "decided"}):
            h._source_button_worker({"application_id": "1", "token": "t"},
                                    {"fingerprint": "f" * 64,
                                     "decision": source_review.DECISION_REJECT,
                                     "reason": "off_theme"})

        self.assertEqual(persisted[0]["decision"], source_review.DECISION_REJECT)
        self.assertEqual(persisted[0]["rejection_category"], "off_theme",
                         "the chosen reason must persist as rejection_category")
        self.assertTrue(patches, "the original message must be updated")
        self.assertIn("embed", patches[0])

    def test_worker_never_raises_and_reports_failure(self):
        h = _handler()
        patches: list[dict] = []
        h._patch_original = lambda app, tok, **kw: patches.append(kw)
        with mock.patch.object(source_review, "fetch_candidate_by_fingerprint",
                               side_effect=RuntimeError("supabase down")):
            h._source_button_worker({"application_id": "1", "token": "t"},
                                    {"fingerprint": "f" * 64, "decision": "hold"})
        self.assertTrue(patches and "content" in patches[0])


if __name__ == "__main__":
    unittest.main()
