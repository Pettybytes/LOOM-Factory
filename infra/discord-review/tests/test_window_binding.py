"""Range binding + post-persistence card update + visible PATCH failure.

Run:  python3 -m unittest discover -s infra/discord-review/tests -v
"""

import json
import sys
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
for d in ("acquisition", "discord-review"):
    sys.path.insert(0, str(_REPO / "infra" / d))

import acquisition_card as card  # noqa: E402
import acquisition_ops as ops  # noqa: E402
import approved_lane as lane  # noqa: E402

VID = "ClhBXVygWbc"
URL = f"https://www.youtube.com/watch?v={VID}"
NEW_FP = "fad6787e957e17c4c7730f0e0a1b2c3d"
OLD_FP = "839c64395f568fda0000000000000000"


class _Store:
    def __init__(self, fingerprint=NEW_FP):
        self.fingerprint = fingerprint
        self.jobs = {}
        self.writes = []

    def source_context(self, ref):
        import source_context as sc
        rows = [{"platform": "youtube", "source_url": URL,
                 "duplicate_fingerprint": "f" * 64,
                 "reviewer_note": json.dumps(
                     {"window": {"start_ms": 65_000, "end_ms": 95_000,
                                 "clip_duration_ms": 30_000,
                                 "range_fingerprint": self.fingerprint},
                      "active_origin": "operator_adjusted"}),
                 "time_start_s": 65.0, "time_end_s": 95.0}]
        r = sc.resolve_context(ref, get=lambda _p: rows)
        return r.as_dict() if r else None

    def update_source_window(self, ref, *, start_s, end_s, provenance=None):
        self.writes.append((ref, start_s, end_s))
        return {"submission": [{}], "job": []}

    def pending_job(self, ref):
        return None

    def authorized_job(self, ref):
        return None

    def library_rows(self):
        return []


def ctx_for(store):
    return ops.context_for({"ref": VID}, store=store)


class TestRangeBinding(unittest.TestCase):
    def test_custom_id_round_trips_the_fingerprint(self):
        cid = card.make_custom_id(card.ACT_APPROVE_ACQUIRE, VID, fingerprint=NEW_FP)
        self.assertEqual(cid, f"AQ:aa:{VID}#{NEW_FP[:16]}")
        self.assertEqual(card.parse_custom_id(cid),
                         {"action": "approve_acquire", "ref": VID,
                          "fingerprint": NEW_FP[:16]})
        plain = card.make_custom_id(card.ACT_HOLD, VID)
        self.assertNotIn("fingerprint", card.parse_custom_id(plain))

    def test_stale_click_is_refused_and_creates_nothing(self):
        store = _Store()
        ran = {"n": 0}

        def never(**_kw):
            ran["n"] += 1
            raise AssertionError("a stale approval must never reach the runner")

        before = len(store.jobs)
        with self.assertRaises(lane.LaneError) as e:
            ops.one_click_approve_acquire(
                deps=store, ctx=ctx_for(store), actor="bryan",
                transport=ops.RecordingTransport(), runner=never,
                required_fingerprint=OLD_FP[:16])
        self.assertEqual(e.exception.code, "RANGE_STALE")
        self.assertEqual(ran["n"], 0, "the runner must not be invoked")
        self.assertEqual(len(store.jobs), before, "nothing may be created")
        self.assertEqual(store.writes, [], "nothing may be persisted")

    def test_current_binding_matches_the_durable_window(self):
        store = _Store()
        window = ctx_for(store).window
        self.assertTrue(window["range_fingerprint"].startswith(NEW_FP[:16]))


class TestPostPersistenceCardUpdate(unittest.TestCase):
    def _handler(self):
        class H:
            def __init__(self):
                self.sent = []

            def _send_json(self, code, obj):
                self.sent.append((code, obj))
        return H()

    def _modal(self, bot_token="bot-token"):
        cid = ops.window_modal_payload(VID, message_id="1550657694665412730")["data"]["custom_id"]
        interaction = {"type": 5, "application_id": "app", "token": "tok",
                       "channel": {"id": "1550207989569421444"},
                       "member": {"user": {"id": "1", "username": "bryan"}},
                       "data": {"custom_id": cid, "components": [
                           {"components": [{"custom_id": "window_start", "value": "01:05"}]},
                           {"components": [{"custom_id": "window_end", "value": "01:35"}]}]}}
        return interaction

    def test_the_card_patch_uses_token_positionally_and_body_by_keyword(self):
        calls = []

        def http_json(method, url, token=None, body=None, timeout=None, **kw):
            calls.append({"method": method, "url": url, "token": token, "body": body})
            return 200, {"id": "1"}

        store = _Store()
        out = ops.handle_window_modal_adapter(self._modal(),
                                              (VID, "1550657694665412730"),
                                              http_json=http_json, store=store,
                                              actor="bryan", bot_token="bot-token")
        self.assertTrue(out["card_updated"]["ok"])
        patch = [c for c in calls if c["method"] == "PATCH"][0]
        self.assertEqual(patch["token"], "bot-token",
                         "the BOT token must travel in the token parameter")
        self.assertIsInstance(patch["body"], dict,
                              "the card must travel as the body, not as a token")
        self.assertEqual(patch["body"]["embeds"][0]["title"], card.STATE_TITLES[card.STATE_REVIEW])

    def test_a_failed_card_patch_is_reported_not_hidden(self):
        follows = []

        def http_json(method, url, token=None, body=None, timeout=None, **kw):
            if method == "PATCH":
                raise RuntimeError("HTTP 401 Unauthorized")
            follows.append(body)
            return 200, {}

        store = _Store()
        out = ops.handle_window_modal_adapter(self._modal(),
                                              (VID, "1550657694665412730"),
                                              http_json=http_json, store=store,
                                              actor="bryan", bot_token="bot-token")
        self.assertFalse(out["card_updated"]["ok"])
        text = json.dumps(follows)
        self.assertIn("could NOT be updated", text,
                      "the operator must be told the card did not change")
        self.assertNotIn("Press Approve & Acquire", text,
                         "a failed patch must never read as success")
        # the durable window is authoritative even when the card lags behind it
        self.assertEqual([(w[0], w[1], w[2]) for w in store.writes], [(VID, 65.0, 95.0)])


if __name__ == "__main__":
    unittest.main()
