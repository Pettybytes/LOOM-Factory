"""State/action acceptance matrix: every displayed button must do something real.

Run:  python3 -m unittest discover -s infra/acquisition/tests -v

For every state the card can render, each button must be provably one of:
  * a genuine interaction (custom_id that the router parses, that the card allows
    in that state, and that the operator surface actually handles), or
  * a valid link (http(s) URL, no custom_id — so it can never invoke a handler), or
  * correctly disabled.

Also guards the specific defects that were reported live: an ACQUIRED card must
not advertise Preview / Play, and must not show a Reaction Library button unless
there is a real destination for it.
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
LIB_URL = "http://127.0.0.1:8787/library/" + "a" * 64

# Actions the operator surface routes (anything else would fall through).
ROUTED_ACTIONS = frozenset({
    card.ACT_APPROVE, card.ACT_DENY, card.ACT_HOLD, card.ACT_ACQUIRE,
    card.ACT_RETRY, card.ACT_PLAY, card.ACT_LIBRARY, card.ACT_SOURCE,
    card.ACT_ERROR,
})

STATES = (card.STATE_REVIEW, card.STATE_APPROVED, card.STATE_ACQUIRING,
          card.STATE_ACQUIRED, card.STATE_FAILED)


def _vm(state, **over):
    kw = dict(ref=CID, platform="instagram", state=state, content_id=CID,
              canonical_url=REEL, estimated_cost_micro=2600, ceiling_micro=1_000_000,
              remaining_acquisitions=8, tenant_id="dreemzy",
              profile_id="acquisition-live-proof")
    kw.update(over)
    return card.build_view_model(**kw)


def _buttons(vm, **kw):
    rows = card.format_acquisition_buttons(vm, **kw)
    return [b for row in rows for b in row.get("components", [])]


class TestEveryButtonIsReal(unittest.TestCase):
    def test_every_button_is_interactive_xor_a_link(self):
        for state in STATES:
            for vm in (_vm(state), _vm(state, library_url=LIB_URL)):
                for b in _buttons(vm):
                    has_custom = "custom_id" in b
                    has_url = "url" in b
                    self.assertNotEqual(has_custom, has_url,
                                        f"{state}: button {b.get('label')!r} must be either "
                                        f"interactive or a link, not both/neither")

    def test_interactive_buttons_route_and_are_legal_in_their_state(self):
        """An ENABLED interactive button must be legal; a locked one must be disabled.

        ACQUIRING intentionally renders the in-flight action greyed out, so the
        rule is: enabled => permitted, disabled => visibly locked (never a silently
        dead but clickable button).
        """
        for state in STATES:
            for b in _buttons(_vm(state)):
                if "custom_id" not in b:
                    continue
                parsed = card.parse_custom_id(b["custom_id"])
                self.assertIsNotNone(parsed, f"{state}: unparseable custom_id")
                self.assertEqual(parsed["ref"], CID)
                self.assertIn(parsed["action"], ROUTED_ACTIONS,
                              f"{state}: {parsed['action']!r} is not handled anywhere")
                guard = card.guard_action(state, parsed["action"], ref=CID)
                if b.get("disabled"):
                    self.assertTrue(b["disabled"], f"{state}: locked button must say so")
                else:
                    self.assertEqual(guard, card.GUARD_OK,
                                     f"{state}: enabled {parsed['action']!r} is not "
                                     f"permitted ({guard})")

    def test_link_buttons_are_valid_and_never_invoke_handlers(self):
        for state in STATES:
            for b in _buttons(_vm(state, library_url=LIB_URL)):
                if "url" not in b:
                    continue
                self.assertTrue(b["url"].startswith(("http://", "https://")),
                                f"{state}: link {b['url']!r} is not http(s)")
                self.assertNotIn("custom_id", b)

    def test_acquiring_locks_every_interactive_button(self):
        for b in _buttons(_vm(card.STATE_ACQUIRING)):
            if "custom_id" in b:
                self.assertTrue(b["disabled"], f"{b['label']!r} must be locked mid-flight")

    def test_every_state_exposes_a_usable_action_or_is_locked(self):
        """Non-transient states must offer something usable; ACQUIRING is locked."""
        for state in STATES:
            buttons = _buttons(_vm(state))
            usable = [b for b in buttons if ("url" in b) or not b.get("disabled")]
            if state in card.LOCKED_STATES:
                self.assertEqual(usable, [], f"{state}: a locked state must lock everything")
                self.assertTrue(buttons, f"{state}: still shows what is happening")
            else:
                self.assertTrue(usable, f"{state}: no usable action")


class TestAcquiredCardHasNoDeadActions(unittest.TestCase):
    def test_no_preview_play_button_when_acquired(self):
        labels = [b.get("label") for b in _buttons(_vm(card.STATE_ACQUIRED))]
        self.assertNotIn("Preview / Play", labels)

    def test_library_button_is_a_link_when_a_route_exists(self):
        buttons = _buttons(_vm(card.STATE_ACQUIRED, library_url=LIB_URL))
        lib = next(b for b in buttons if b.get("label") == "Open in Reaction Library")
        self.assertEqual(lib["url"], LIB_URL)
        self.assertNotIn("custom_id", lib)

    def test_library_button_is_omitted_when_no_route_exists(self):
        labels = [b.get("label") for b in _buttons(_vm(card.STATE_ACQUIRED))]
        self.assertNotIn("Open in Reaction Library", labels)
        self.assertEqual(labels, ["Hold"])           # only a real action remains

    def test_preview_field_never_claims_unavailable_playback(self):
        vm = _vm(card.STATE_ACQUIRED, preview={"kind": "local_asset",
                                               "local_filename": "a.mp4",
                                               "attach_reason": "15.9 MiB > 10.0 MiB limit"})
        text = card.preview_field(vm)
        self.assertIn("Video not attached", text)
        self.assertIn("15.9 MiB > 10.0 MiB limit", text)
        self.assertIn("Reaction Library", text)
        self.assertNotIn("Playable in this message", text)

    def test_preview_field_claims_playback_only_when_attached(self):
        vm = _vm(card.STATE_ACQUIRED, preview={"kind": "video_attachment"})
        self.assertIn("Playable in this message", card.preview_field(vm))


class TestHoldIsDurableAndSafe(unittest.TestCase):
    def test_hold_on_acquired_persists_and_never_deletes_the_asset(self):
        store = ops.MemoryLaneStore()
        store.library.append({"platform_content_id": CID, "asset_filename": "a.mp4",
                              "asset_hash": "f" * 64})
        store.source_contexts[CID] = {"platform": "instagram", "canonical_url": REEL,
                                      "content_id": CID, "fingerprint": CID}
        transport = ops.RecordingTransport()
        ctx = ops.LaneContext(ref=CID, platform="instagram", content_id=CID,
                              canonical_url=REEL, state=card.STATE_ACQUIRED,
                              library={"status": "acquired", "asset_filename": "a.mp4"})
        out = ops.handle({"action": card.ACT_HOLD, "ref": CID}, deps=store,
                         transport=transport, actor="bryan", ctx=ctx, now=1.0)
        self.assertTrue(out["ok"])
        self.assertEqual(out["effects"]["decision"], "hold")
        self.assertTrue(store.decisions)                       # durable
        self.assertTrue(store.library)                          # asset untouched
        self.assertTrue(transport.updates)                      # card updated in place


if __name__ == "__main__":
    unittest.main()
