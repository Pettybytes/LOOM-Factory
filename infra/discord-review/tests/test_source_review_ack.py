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
            h._handle_button(interaction)

        self.assertEqual(h.sent[0], (200, {"type": 6}),
                         "first response must be the deferred acknowledgement")
        self.assertTrue(started, "work must be handed to a worker thread")
        self.assertEqual(started[0]["target"], "_source_button_worker")
        self.assertTrue(started[0]["daemon"], "worker must be a daemon thread")


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
            return {"decision": payload["decision"]}, True

        with mock.patch.object(source_review, "fetch_candidate_by_fingerprint",
                               lambda *a, **k: cand), \
                mock.patch.object(source_review, "source_decision_payload",
                                  lambda c, d, r: {"decision": d, "reviewer": r}), \
                mock.patch.object(source_review, "persist_decision", fake_persist), \
                mock.patch.object(source_review, "decided_embed",
                                  lambda d, r, c: {"title": "decided"}):
            h._source_button_worker({"application_id": "1", "token": "t"},
                                    {"fingerprint": "f" * 64, "decision": "reject"})

        self.assertEqual(persisted and persisted[0]["decision"], "reject")
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

    def test_edit_does_not_persist_or_approve(self):
        h = _handler()
        launched = []
        h._launch_source_editor = lambda *a, **k: launched.append(a)
        cand = {"duplicate_fingerprint": "f" * 64, "tenant_id": "dreemzy"}
        with mock.patch.object(source_review, "fetch_candidate_by_fingerprint", return_value=cand), \
             mock.patch.object(source_review, "persist_decision") as persist, \
             mock.patch.object(h, "_source_approve_handoff") as handoff:
            h._source_button_worker(
                {"application_id": "1", "token": "t"},
                {"fingerprint": "f" * 64, "decision": source_review.DECISION_EDIT})
        persist.assert_not_called()
        handoff.assert_not_called()
        self.assertEqual(len(launched), 1)
        self.assertIs(launched[0][1], cand)

    def test_launch_binds_the_candidate_and_does_not_download_or_decide(self):
        h = _handler()
        posted = []
        h._source_editor_followup = lambda *a, **k: posted.append(k)
        h._restore_source_card = lambda *a, **k: posted.append({"restored": True})
        cand = {
            "duplicate_fingerprint": "a" * 64,
            "tenant_id": "dreemzy",
            "canonical_url": "https://www.tiktok.com/video/1",
            "moment_evidence": {"lineage": {
                "origin": "manual_media_intake",
                "media_reference": "https://assets.cdn.filesafe.space/file.mp4",
            }},
            "boundary_plan": {"recommended_clip_start": 0.0, "recommended_clip_end": 11.1},
        }
        with mock.patch.object(adapter, "register_editor_token", return_value=(201, [{"id": "s"}])) as reg, \
             mock.patch.object(adapter, "EDITOR_PUBLIC_URL", "https://app.loomz.stream"), \
             mock.patch.object(source_review, "persist_decision") as persist, \
             mock.patch.object(adapter, "urlopen", side_effect=AssertionError("download")):
            h._launch_source_editor(
                {"guild_id": "g", "channel_id": "c", "member": {"user": {"id": "9", "username": "bryan"}}},
                cand, "app", "tok")
        persist.assert_not_called()
        identity = reg.call_args.args[1]
        self.assertEqual(identity["tenant_id"], "dreemzy")
        self.assertEqual(identity["clip_id"], "src-" + "a" * 64 + ":0")
        self.assertEqual(identity["asset_version"], "source-1.0")
        self.assertTrue(posted[0]["url"].startswith("https://app.loomz.stream/edit?token="))
        self.assertEqual(posted[-1], {"restored": True})


if __name__ == "__main__":
    unittest.main()
