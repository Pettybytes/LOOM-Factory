"""ACK-first ordering: Discord's deadline must never depend on our lookups.

Run:  python3 -m unittest discover -s infra/discord-review/tests -v

The live defect this covers: Preview / Play and Open in Reaction Library timed out
because the adapter resolved the source context (Supabase reads) BEFORE
acknowledging the interaction. Once the ACK is sent, any private note has to go out
as a webhook follow-up — a second response to the same interaction is impossible.

No network: the HTTP surface is injected.
"""

import sys
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
for d in ("acquisition", "discord-review"):
    sys.path.insert(0, str(_REPO_ROOT / "infra" / d))

import acquisition_card as card  # noqa: E402
import acquisition_ops as ops  # noqa: E402

CID = "DdQE_RUS0GJ"
REEL = f"https://www.instagram.com/reel/{CID}/"


class FakeHandler:
    """Stands in for the adapter's BaseHTTPRequestHandler response path."""

    def __init__(self, log: list[str]) -> None:
        self.log = log
        self.responses: list[tuple[int, dict]] = []

    def _send_json(self, code, payload):
        self.log.append("ack")
        self.responses.append((code, payload))


class FakeHttp:
    def __init__(self, log: list[str]) -> None:
        self.log = log
        self.calls: list[tuple] = []

    def __call__(self, method, path, body=None, **kw):
        self.log.append("followup" if body and body.get("flags") == 64 else "http")
        self.calls.append((method, path, body))
        return {"ok": True}


def interaction() -> dict:
    return {"application_id": "app1", "token": "tok1", "channel_id": "c",
            "message_id": "m", "member": {"user": {"username": "bryan"}}}


def failing_ctx() -> ops.LaneContext:
    return ops.LaneContext(ref=CID, platform="instagram", content_id=CID,
                           canonical_url=REEL, state=card.STATE_FAILED,
                           error={"code": 404, "stage": "actor_start", "message": "x"},
                           preview={"kind": "none"})


class TestAckFirst(unittest.TestCase):
    def _run(self, action, provider, *, store=None):
        log: list[str] = []
        handler = FakeHandler(log)
        http = FakeHttp(log)

        def slow_provider():
            log.append("context")
            return provider()

        ops.handle_adapter_interaction(
            handler, interaction(), {"action": action, "ref": CID},
            http_json=http, context_provider=slow_provider, actor="bryan",
            store=store or ops.MemoryLaneStore(), runner=None)
        return handler, http, log

    def test_ack_is_sent_before_the_context_lookup(self):
        handler, _http, log = self._run(card.ACT_ERROR, failing_ctx)
        self.assertEqual(log[0], "ack", "the ACK must be the first thing that happens")
        self.assertEqual(log[1], "context")
        self.assertEqual(handler.responses[0], (200, {"type": 6}))

    def test_interaction_is_answered_exactly_once(self):
        handler, _http, _log = self._run(card.ACT_ERROR, failing_ctx)
        self.assertEqual(len(handler.responses), 1, "a second response is impossible")

    def test_private_note_after_the_ack_uses_the_followup_webhook(self):
        _handler, http, _log = self._run(card.ACT_ERROR, failing_ctx)
        method, path, body = http.calls[-1]
        self.assertEqual(method, "POST")
        self.assertIn("/webhooks/app1/tok1", path)
        self.assertEqual(body["flags"], 64)
        self.assertIn("404", body["content"])

    def test_state_change_after_the_ack_patches_the_original_message(self):
        store = ops.MemoryLaneStore()
        store.source_contexts[CID] = {"platform": "instagram", "canonical_url": REEL,
                                      "content_id": CID, "fingerprint": CID}
        ctx = ops.LaneContext(ref=CID, platform="instagram", content_id=CID,
                              canonical_url=REEL, state=card.STATE_REVIEW)
        handler, http, log = self._run(card.ACT_HOLD, lambda: ctx, store=store)
        self.assertEqual(log[0], "ack")
        self.assertEqual(handler.responses[0], (200, {"type": 6}))
        self.assertEqual(len(handler.responses), 1)
        patch_calls = [c for c in http.calls if c[0] == "PATCH"]
        self.assertTrue(patch_calls, "the card update must go to the original message")
        self.assertIn("/messages/@original", patch_calls[-1][1])
        self.assertTrue(store.decisions, "Hold is durable")

    def test_context_failure_still_answers_inside_the_deadline(self):
        """A failed lookup must ACK first, then refuse — never raise, never write."""
        store = ops.MemoryLaneStore()
        handler, http, log = self._run(
            card.ACT_APPROVE,
            lambda: (_ for _ in ()).throw(RuntimeError("db down")), store=store)
        self.assertEqual(handler.responses[0], (200, {"type": 6}))
        self.assertEqual(log[0], "ack")
        self.assertEqual(len(handler.responses), 1)
        self.assertEqual(store.jobs, {}, "nothing may be written on a context failure")
        self.assertEqual(store.decisions, [])
        method, path, body = http.calls[-1]
        self.assertIn("/webhooks/", path)
        self.assertIn("could not be resolved", body["content"])


if __name__ == "__main__":
    unittest.main()
