"""Deterministic tests for the Acquisition Card (presentation + intent guards).

Run:  python3 -m unittest discover -s infra/acquisition/tests -v

No I/O, no provider call, no spend. Proves the invariants the operator card
must hold: state-dependent actions, approve-is-free, acquire-is-the-only-spend,
duplicate-click prevention, and provenance hygiene.
"""

import json
import sys
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
for d in ("acquisition", "discord-review"):
    sys.path.insert(0, str(_REPO_ROOT / "infra" / d))

import acquisition_card as card  # noqa: E402
import reaction_library  # noqa: E402

SIGNED = ("https://scontent.cdninstagram.com/v/t51.2885-15/reel.mp4"
          "?sig=SECRET&_nc_ht=scontent.cdninstagram.com&oh=XYZ")

KNOWN_SHA = "9b22f887b2ff6a3b78d91529c1d9c3105497af2a4298ba9241394686239ae096"


def vm(state, **over):
    kw = dict(
        ref="DdZS1QRhrXE", platform="instagram", state=state,
        content_id="DdZS1QRhrXE",
        canonical_url="https://www.instagram.com/reel/DdZS1QRhrXE/",
        creator="@somecreator", caption="A vertical Reel.",
        duration_seconds=55.260635, width=720, height=1280,
        size_bytes=23721338, video_codec="h264", audio_codec="aac", has_audio=True,
        estimated_cost_micro=2600, ceiling_micro=1_000_000, remaining_acquisitions=9,
        provenance={"actor": "instagram-reel-scraper", "run_id": "0DPOKk2bEwxqVTS0i"},
        tenant_id="dreemzy", profile_id="acquisition-card-proof",
    )
    kw.update(over)
    return card.build_view_model(**kw)


class TestStates(unittest.TestCase):
    def test_every_state_renders_title_and_color(self):
        for st in card.CARD_STATES:
            emb = card.format_acquisition_embed(vm(st))
            self.assertEqual(emb["title"], card.STATE_TITLES[st])
            self.assertEqual(emb["color"], card.STATE_COLORS[st])
            self.assertIn("footer", emb)
            self.assertLessEqual(len(emb["fields"]), 25)

    def test_action_matrix_by_state(self):
        self.assertEqual(card.actions_for_state(card.STATE_REVIEW),
                         ("approve", "deny", "hold"))
        self.assertEqual(card.actions_for_state(card.STATE_APPROVED),
                         ("acquire", "hold", "deny"))
        self.assertEqual(card.actions_for_state(card.STATE_ACQUIRING),
                         ("acquire", "hold"))
        self.assertEqual(card.actions_for_state(card.STATE_ACQUIRED),
                         ("play", "library", "hold"))
        self.assertEqual(card.actions_for_state(card.STATE_FAILED),
                         ("retry", "hold", "error"))

    def test_button_styles_are_intent_differentiated(self):
        rows = card.format_acquisition_buttons(vm(card.STATE_REVIEW))
        by_label = {b["label"]: b for b in rows[0]["components"]}
        self.assertEqual(by_label["Approve"]["style"], card.STYLE_SUCCESS)
        self.assertEqual(by_label["Deny"]["style"], card.STYLE_DANGER)
        self.assertEqual(by_label["Hold"]["style"], card.STYLE_SECONDARY)
        self.assertEqual(by_label["View Source"]["style"], card.STYLE_LINK)
        failed = card.format_acquisition_buttons(vm(card.STATE_FAILED))[0]["components"]
        retry = [b for b in failed if b["label"].endswith("Retry Manually")][0]
        self.assertEqual(retry["style"], card.STYLE_PRIMARY)

    def test_acquiring_locks_all_interactive_buttons(self):
        rows = card.format_acquisition_buttons(vm(card.STATE_ACQUIRING))
        interactive = [b for b in rows[0]["components"] if b["style"] != card.STYLE_LINK]
        self.assertTrue(interactive)
        self.assertTrue(all(b["disabled"] for b in interactive))

    def test_other_states_are_enabled(self):
        for st in (card.STATE_REVIEW, card.STATE_APPROVED, card.STATE_ACQUIRED, card.STATE_FAILED):
            rows = card.format_acquisition_buttons(vm(st))
            interactive = [b for b in rows[0]["components"] if b["style"] != card.STYLE_LINK]
            self.assertTrue(all(not b["disabled"] for b in interactive), st)

    def test_view_source_only_where_specified(self):
        for st in (card.STATE_REVIEW, card.STATE_APPROVED, card.STATE_FAILED):
            labels = [b["label"] for b in card.format_acquisition_buttons(vm(st))[0]["components"]]
            self.assertIn("View Source", labels)
        for st in (card.STATE_ACQUIRING, card.STATE_ACQUIRED):
            labels = [b["label"] for b in card.format_acquisition_buttons(vm(st))[0]["components"]]
            self.assertNotIn("View Source", labels)


class TestSpendGuards(unittest.TestCase):
    def test_approved_state_carries_acquire_button(self):
        labels = [b["label"] for b in card.format_acquisition_buttons(vm(card.STATE_APPROVED))[0]["components"]]
        self.assertIn("Acquire for Reaction", labels)

    def test_approve_is_free_and_authorization_only(self):
        self.assertFalse(card.requires_spend(card.ACT_APPROVE))
        self.assertFalse(card.requires_authorization(card.ACT_APPROVE))
        self.assertTrue(card.is_safe_without_spend(card.ACT_APPROVE))

    def test_only_spend_actions_cost_money(self):
        spending = sorted(a for a in card.ACTION_CODES if card.requires_spend(a))
        self.assertEqual(spending, sorted([card.ACT_ACQUIRE, card.ACT_RETRY]))
        for a in card.ACTION_CODES:
            if a not in card.SPEND_ACTIONS:
                self.assertTrue(card.is_safe_without_spend(a), a)

    def test_retry_requires_explicit_authorization(self):
        self.assertTrue(card.requires_spend(card.ACT_RETRY))
        self.assertTrue(card.requires_authorization(card.ACT_RETRY))

    def test_duplicate_click_suppressed(self):
        self.assertEqual(
            card.guard_action(card.STATE_APPROVED, card.ACT_ACQUIRE, ref="r",
                              last_action=card.ACT_ACQUIRE, last_ref="r",
                              last_action_at=100.0, now=105.0),
            card.GUARD_DUPLICATE)

    def test_duplicate_click_allowed_after_window(self):
        self.assertEqual(
            card.guard_action(card.STATE_APPROVED, card.ACT_ACQUIRE, ref="r",
                              last_action=card.ACT_ACQUIRE, last_ref="r",
                              last_action_at=100.0,
                              now=100.0 + card.DUPLICATE_WINDOW_SECONDS + 1),
            card.GUARD_OK)

    def test_acquire_blocked_in_flight_and_when_acquired(self):
        self.assertEqual(
            card.guard_action(card.STATE_APPROVED, card.ACT_ACQUIRE, in_flight=True),
            card.GUARD_IN_FLIGHT)
        self.assertEqual(
            card.guard_action(card.STATE_ACQUIRED, card.ACT_ACQUIRE),
            card.GUARD_ALREADY_ACQUIRED)

    def test_illegal_actions_rejected(self):
        self.assertEqual(card.guard_action(card.STATE_REVIEW, card.ACT_ACQUIRE),
                         card.GUARD_NOT_ALLOWED)
        self.assertEqual(card.guard_action(card.STATE_REVIEW, card.ACT_PLAY),
                         card.GUARD_NOT_ALLOWED)
        self.assertEqual(card.guard_action(card.STATE_ACQUIRED, card.ACT_APPROVE),
                         card.GUARD_NOT_ALLOWED)

    def test_transitions(self):
        self.assertEqual(card.next_state(card.STATE_REVIEW, card.ACT_APPROVE), card.STATE_APPROVED)
        self.assertEqual(card.next_state(card.STATE_APPROVED, card.ACT_ACQUIRE), card.STATE_ACQUIRING)
        self.assertEqual(card.next_state(card.STATE_ACQUIRING, card.ACT_ACQUIRE, succeeded=True),
                         card.STATE_ACQUIRED)
        self.assertEqual(card.next_state(card.STATE_ACQUIRING, card.ACT_ACQUIRE, succeeded=False),
                         card.STATE_FAILED)
        self.assertEqual(card.next_state(card.STATE_FAILED, card.ACT_RETRY), card.STATE_ACQUIRING)
        self.assertEqual(card.next_state(card.STATE_REVIEW, card.ACT_DENY), card.STATE_DENIED)
        self.assertEqual(card.next_state(card.STATE_REVIEW, card.ACT_HOLD), card.STATE_REVIEW)

    def test_acknowledgements_present(self):
        self.assertIn("no spend", card.acknowledgement(card.ACT_APPROVE, card.STATE_APPROVED).lower())
        self.assertTrue(card.acknowledgement(card.ACT_HOLD, card.STATE_REVIEW))


class TestHygiene(unittest.TestCase):
    def test_signed_url_never_survives_render(self):
        v = vm(card.STATE_ACQUIRED,
               preview={"kind": "video_url", "hosted_url": SIGNED})
        payload = json.dumps(card.format_acquisition_card(v))
        for marker in ("sig=", "oh=", "scontent", "SECRET"):
            self.assertNotIn(marker, payload)
        self.assertNotIn("video", card.format_acquisition_embed(v))

    def test_query_string_stripped_from_canonical_url(self):
        v = vm(card.STATE_REVIEW,
               canonical_url="https://www.instagram.com/reel/DdZS1QRhrXE/?utm_source=x&stkn=abc")
        self.assertEqual(v["canonical_url"], "https://www.instagram.com/reel/DdZS1QRhrXE/")
        self.assertNotIn("stkn", json.dumps(card.format_acquisition_card(v)))

    def test_provenance_line_is_compact(self):
        line = card.provenance_line(vm(card.STATE_ACQUIRED))
        self.assertIn("DdZS1QRhrXE", line)
        self.assertIn("instagram-reel-scraper", line)
        self.assertNotIn("http", line)

    def test_unsafe_ref_rejected(self):
        with self.assertRaises(ValueError):
            card.build_view_model(ref="bad ref!", platform="instagram", state=card.STATE_REVIEW)
        with self.assertRaises(card.CardIdError):
            card.make_custom_id(card.ACT_ACQUIRE, "bad ref!")

    def test_unknown_state_and_action_rejected(self):
        with self.assertRaises(ValueError):
            card.build_view_model(ref="ok", platform="instagram", state="mystery")
        with self.assertRaises(card.CardIdError):
            card.make_custom_id("explode", "ok")

    def test_custom_id_round_trip(self):
        cid = card.make_custom_id(card.ACT_ACQUIRE, "DdZS1QRhrXE")
        self.assertLessEqual(len(cid), 100)
        parsed = card.parse_custom_id(cid)
        self.assertEqual(parsed, {"action": card.ACT_ACQUIRE, "ref": "DdZS1QRhrXE"})
        self.assertIsNone(card.parse_custom_id("mi:submit:abc"))
        self.assertIsNone(card.parse_custom_id("AQ:zz:ref"))


class TestPresentation(unittest.TestCase):
    def test_media_summary_facts(self):
        s = card.media_summary(vm(card.STATE_REVIEW))
        self.assertIn("0:55", s)
        self.assertIn("720×1280", s)
        self.assertIn("9:16 vertical", s)
        self.assertIn("22.6 MB", s)
        self.assertIn("Audio present (AAC)", s)

    def test_no_audio_and_unknown_audio(self):
        self.assertIn("No audio", card.media_summary(vm(card.STATE_REVIEW, has_audio=False)))
        self.assertIn("Audio unknown", card.media_summary(vm(card.STATE_REVIEW, has_audio=None)))

    def test_failed_state_cost_is_not_claimed_unspent(self):
        line = card.cost_line(vm(card.STATE_FAILED))
        self.assertIn("actual not reconciled", line)
        self.assertNotIn("not yet spent", line)

    def test_cost_line_before_and_after(self):
        before = card.cost_line(vm(card.STATE_APPROVED))
        self.assertIn("$0.0026", before)
        self.assertIn("not yet spent", before)
        self.assertIn("ceiling $1.0000", before)
        after = card.cost_line(vm(card.STATE_ACQUIRED, actual_cost_micro=2600))
        self.assertIn("Actual **$0.0026**", after)

    def test_library_line_states(self):
        self.assertEqual(card.library_line(vm(card.STATE_REVIEW)), "Not acquired")
        lib = card.library_line(vm(card.STATE_ACQUIRED, library={
            "asset_filename": "instagram__DdZS1QRhrXE__9b22f887b2ff.mp4",
            "sha256": KNOWN_SHA, "status": "acquired"}))
        self.assertIn("Acquired", lib)
        self.assertIn("instagram__DdZS1QRhrXE__9b22f887b2ff.mp4", lib)
        self.assertIn("9b22f887b2ff", lib)

    def test_preview_fallback_states(self):
        self.assertIn("Playable locally",
                      card.preview_field(vm(card.STATE_ACQUIRED,
                                            preview={"kind": "local_asset",
                                                     "local_filename": "a.mp4"})))
        self.assertIn("cannot embed a local file",
                      card.preview_field(vm(card.STATE_ACQUIRED,
                                            preview={"kind": "local_asset",
                                                     "local_filename": "a.mp4"})))
        self.assertIn("thumbnail", card.preview_field(vm(card.STATE_REVIEW,
                                                         preview={"kind": "thumbnail"})))
        self.assertIn("No preview yet", card.preview_field(vm(card.STATE_REVIEW)))

    def test_attachment_poster_is_allowed_and_rendered(self):
        v = vm(card.STATE_ACQUIRED, preview={"kind": "thumbnail",
                                            "thumbnail_url": "attachment://poster.jpg"})
        emb = card.format_acquisition_embed(v)
        self.assertEqual(emb["thumbnail"]["url"], "attachment://poster.jpg")
        self.assertIn("Poster frame attached", card.preview_field(v))

    def test_attachment_reference_cannot_smuggle_a_credential(self):
        for bad in ("attachment://../etc/passwd", "attachment://a b.jpg",
                    "file:///etc/passwd", "http://x/y?sig=1"):
            self.assertEqual(card.sanitize_display_url(bad), "")

    def test_no_preview_never_invents_an_image(self):
        emb = card.format_acquisition_embed(vm(card.STATE_REVIEW))
        self.assertNotIn("thumbnail", emb)
        self.assertNotIn("video", emb)

    def test_failure_state_shows_error_summary(self):
        emb = card.format_acquisition_embed(vm(
            card.STATE_FAILED, error={"code": "INSUFFICIENT_FMP4_SET",
                                      "message": "Provider returned no usable video URL."}))
        names = [f["name"] for f in emb["fields"]]
        self.assertIn("Failure", names)
        val = [f["value"] for f in emb["fields"] if f["name"] == "Failure"][0]
        self.assertIn("no usable video URL", val)

    def test_platform_agnostic_view_model(self):
        v = vm(card.STATE_REVIEW, platform="tiktok", ref="7412345678901234567",
               content_id="7412345678901234567")
        self.assertEqual(v["platform_label"], "TikTok")
        emb = card.format_acquisition_embed(v)
        self.assertIn("ACQUISITION", emb["title"])
        self.assertIn("TikTok", emb["footer"]["text"])

    def test_footer_carries_controls_and_fixture_marker(self):
        text = card.format_acquisition_embed(vm(card.STATE_REVIEW, fixture=True))["footer"]["text"]
        self.assertIn("1 URL · 1 item · ≤5 min · ≤100 MiB · breaker $1.00 / 10", text)
        self.assertIn("FIXTURE", text)

    def test_caption_trimmed(self):
        emb = card.format_acquisition_embed(vm(card.STATE_REVIEW, caption="x" * 5000))
        self.assertLessEqual(len(emb["description"]), 400)
        for f in emb["fields"]:
            self.assertLessEqual(len(f["value"]), 1024)
            self.assertLessEqual(len(f["name"]), 256)


class TestFixtureAndLibraryIntegration(unittest.TestCase):
    def test_fixture_states(self):
        review = card.fixture_view_model(card.STATE_REVIEW)
        self.assertIsNone(review["actual_cost_micro"])
        self.assertEqual(review["library"], {})
        self.assertEqual(review["remaining_acquisitions"], 9)
        acquired = card.fixture_view_model(card.STATE_ACQUIRED, local_path="/tmp/x.mp4")
        self.assertEqual(acquired["actual_cost_micro"], 2600)
        self.assertEqual(acquired["remaining_acquisitions"], 8)
        self.assertEqual(acquired["library"]["sha256"], KNOWN_SHA)

    def test_fixture_local_asset_preview_is_honest(self):
        acquired = card.fixture_view_model(card.STATE_ACQUIRED, local_path="/tmp/asset.mp4")
        self.assertEqual(acquired["preview"]["kind"], "local_asset")
        emb = card.format_acquisition_embed(acquired)
        self.assertNotIn("video", emb)  # local file cannot be embedded by Discord

    def test_library_record_matches_real_shape(self):
        rec = reaction_library.plan_library_entry(
            duplicate_fingerprint="f" * 64,
            platform="instagram",
            platform_content_id="DdZS1QRhrXE",
            canonical_url="https://www.instagram.com/reel/DdZS1QRhrXE/",
            acquisition_job_id="job-1",
            filename="instagram__DdZS1QRhrXE__9b22f887b2ff.mp4",
            digest=KNOWN_SHA,
            size_bytes=23721338,
            duration_seconds=55.260635,
        )
        line = card.library_line(vm(card.STATE_ACQUIRED, library={
            "asset_filename": rec["asset_filename"], "sha256": rec["asset_hash"],
            "status": "acquired"}))
        self.assertIn(rec["asset_filename"], line)
        self.assertIn(rec["asset_hash"][:12], line)


if __name__ == "__main__":
    unittest.main()
