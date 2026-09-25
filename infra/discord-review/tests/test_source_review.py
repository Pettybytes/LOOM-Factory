"""Deterministic tests for the Source Review Inbox logic.

Run:  python3 -m unittest discover -s infra/discord-review/tests -v
"""

import sys
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO_ROOT / "infra" / "discord-review"))

from source_review import (  # noqa: E402
    COMMAND_NAME,
    DECISION_APPROVE,
    DECISION_HOLD,
    DECISION_REJECT,
    DECISION_CODES,
    make_source_custom_id,
    parse_source_custom_id,
    format_source_embed,
    format_source_buttons,
    source_decision_payload,
    decided_embed,
    class_color,
    _format_timestamps,
    _fmt_seconds,
    fetch_pending_candidates,
    persist_decision,
    _is_non_production,
)


def _candidate(**overrides):
    c = {
        "duplicate_fingerprint": "a" * 64,
        "tenant_id": "dreemzy",
        "profile_id": "bryan-face-reaction",
        "platform": "youtube",
        "platform_content_id": "aaaaaaaaaaa",
        "title": "Puppy rescue goes viral",
        "canonical_url": "https://www.youtube.com/watch?v=aaaaaaaaaaa",
        "market_opportunity_score": 59,
        "market_confidence": 0.78,
        "content_worthiness_score": 70,
        "content_confidence": 0.33,  # transcript-only (level 1) — must stay 0.33
        "source_selection_score": 61,
        "source_selection_confidence": 0.58,
        "outcome": "standard_review",
        "recommended_format": "commentary_story",
        "recommended_angle": "the everyday hero",
        "suggested_timestamps": [{"start_s": 10.5, "end_s": 22.5, "basis": "hook"}],
        "top_reasons": ["relatable pain point", "strong hook"],
    }
    c.update(overrides)
    return c


class TestCustomId(unittest.TestCase):
    def test_rejects_the_actual_broken_live_card_before_posting(self):
        # Original card used the YouTube ID as its fingerprint; router refused
        # it before the worker ran, despite HTTP200 for the private error reply.
        self.assertIsNone(parse_source_custom_id("sa_ZaIDxlEuKpc"))
        with self.assertRaisesRegex(ValueError, "tenant-scoped"):
            make_source_custom_id(DECISION_APPROVE, "ZaIDxlEuKpc")
        self.assertIsNone(parse_source_custom_id("sa_" + "z" * 64))

    def test_zero_to_eight_window_renders_without_anchors(self):
        c = _candidate(suggested_timestamps=[], boundary_plan={
            "recommended_clip_start": 0.0, "recommended_clip_end": 8.0, "start_anchors": []})
        embed = format_source_embed(c)
        window = next(f for f in embed["fields"] if f["name"] == "Clip window")
        self.assertIn("00:00", window["value"])
        self.assertIn("00:08", window["value"])

    def test_roundtrip(self):
        for decision in (DECISION_APPROVE, DECISION_HOLD, DECISION_REJECT):
            cid = make_source_custom_id(decision, "a" * 64)
            self.assertEqual(parse_source_custom_id(cid),
                             {"decision": decision, "fingerprint": "a" * 64})

    def test_rejects_clip_review_ids(self):
        # A base64 clip-review custom_id must NOT parse as a source id.
        self.assertIsNone(parse_source_custom_id("eyJkIjoiYXBwcm92ZSJ9"))

    def test_rejects_garbage(self):
        self.assertIsNone(parse_source_custom_id(""))
        self.assertIsNone(parse_source_custom_id("xx_short"))
        self.assertIsNone(parse_source_custom_id("sa_not64hex"))


class TestFormatting(unittest.TestCase):
    def test_embed_shows_both_halves(self):
        e = format_source_embed(_candidate())
        text = json_fields_text(e)
        self.assertIn("Selection", text)
        self.assertIn("Market", text)
        self.assertIn("Content", text)

    def test_embed_keeps_transcript_only_confidence_honest(self):
        e = format_source_embed(_candidate(content_confidence=0.33))
        text = json_fields_text(e)
        self.assertIn("33%", text)  # never inflated to high confidence
        self.assertIn("Transcript only", text)

    def test_embed_has_stage_header(self):
        e = format_source_embed(_candidate())
        self.assertTrue(e["title"].startswith("🔎 SOURCE REVIEW ·"))

    def test_embed_thumbnail_for_youtube(self):
        e = format_source_embed(_candidate(platform="youtube", platform_content_id="aaaaaaaaaaa"))
        self.assertIsNotNone(e.get("thumbnail"))
        self.assertIn("i.ytimg.com/vi/aaaaaaaaaaa", e["thumbnail"]["url"])

    def test_embed_fixture_footer(self):
        e = format_source_embed(_candidate(), fixture=True)
        self.assertIn("TEST FIXTURE", e["footer"]["text"])

    def test_buttons_three(self):
        comps = format_source_buttons("a" * 64)
        self.assertEqual(len(comps), 1)
        buttons = comps[0]["components"]
        self.assertEqual(len(buttons), 3)
        labels = [b["label"] for b in buttons]
        # Order: Approve (green), Reject (red), Hold (gray).
        self.assertEqual(labels, ["Approve for Acquisition", "Reject", "Hold"])
        self.assertEqual([b["style"] for b in buttons], [3, 4, 2])

    def test_buttons_disabled_for_fixture(self):
        comps = format_source_buttons("a" * 64, disabled=True)
        buttons = comps[0]["components"]
        self.assertTrue(all(b.get("disabled") for b in buttons))

    def test_open_source_link_button(self):
        comps = format_source_buttons("a" * 64, source_url="https://example.com/v")
        buttons = comps[0]["components"]
        self.assertEqual(len(buttons), 4)
        self.assertEqual(buttons[-1]["label"], "Open Source")
        self.assertEqual(buttons[-1]["style"], 5)
        self.assertEqual(buttons[-1]["url"], "https://example.com/v")

    def test_class_color(self):
        self.assertIsInstance(class_color("fast_track"), int)


def json_fields_text(embed):
    import json
    return json.dumps(embed.get("fields", []))


class TestDecisionPayload(unittest.TestCase):
    def test_payload_shape(self):
        p = source_decision_payload(_candidate(), DECISION_APPROVE, "bryan#1")
        self.assertEqual(p["decision"], DECISION_APPROVE)
        self.assertEqual(p["reviewer"], "bryan#1")
        self.assertIsNone(p["rejection_category"])
        self.assertIsNone(p["reviewer_instructions"])

    def test_reject_carries_category(self):
        p = source_decision_payload(_candidate(), DECISION_REJECT, "bryan#1",
                                    category="content_issue", instructions="re-verify")
        self.assertEqual(p["rejection_category"], "content_issue")
        self.assertEqual(p["reviewer_instructions"], "re-verify")

    def test_decided_embed(self):
        e = decided_embed(DECISION_APPROVE, "bryan#1", created=True)
        self.assertIn("Decision recorded", e["title"])


class TestTimestampFormatting(unittest.TestCase):
    def test_milliseconds_and_minute_rollover(self):
        self.assertEqual(_fmt_seconds(0.125), "00:00.125")
        self.assertEqual(_fmt_seconds(59.9996), "01:00")
        self.assertEqual(_fmt_seconds(3600.375), "1:00:00.375")

    def test_fractional_seconds_preserved(self):
        out = _format_timestamps([{"start_s": 10.5, "end_s": 22.5}])
        self.assertEqual(out, "00:10.5–00:22.5")

    def test_below_hour_mmss(self):
        out = _format_timestamps([
            {"start_s": 110.0, "end_s": 116.5},
            {"start_s": 215.5, "end_s": 228.3},
        ])
        self.assertIn("01:50–01:56.5", out)
        self.assertIn("03:35.5–03:48.3", out)

    def test_above_hour_hms(self):
        out = _format_timestamps([{"start_s": 3661.0, "end_s": 3720.0}])
        self.assertEqual(out, "1:01:01–1:02:00")

    def test_missing_timestamps(self):
        self.assertIsNone(_format_timestamps(None))
        self.assertIsNone(_format_timestamps([]))

    def test_malformed_skipped(self):
        out = _format_timestamps([
            {"start_s": "bad", "end_s": "bad"},
            {"start_s": 10, "end_s": 20},
        ])
        self.assertEqual(out, "00:10–00:20")

    def test_boundaries(self):
        self.assertEqual(_fmt_seconds(0), "00:00")
        self.assertEqual(_fmt_seconds(59), "00:59")
        self.assertEqual(_fmt_seconds(60), "01:00")
        self.assertEqual(_fmt_seconds(3599), "59:59")
        self.assertEqual(_fmt_seconds(3600, force_hms=True), "1:00:00")

    def test_reason_label_preserved(self):
        out = _format_timestamps([{"start_s": 10.5, "end_s": 22.5, "basis": "Opening hook"}])
        self.assertEqual(out, "00:10.5–00:22.5 — Opening hook")

    def test_never_raw_format(self):
        out = _format_timestamps([{"start_s": 110, "end_s": 116.5}])
        self.assertEqual(out, "01:50–01:56.5")
        self.assertNotIn("110", out)   # no raw seconds
        self.assertNotIn("116.5", out)  # no fractional raw
        self.assertNotIn("s", out)      # no 's' suffix


class TestNonProduction(unittest.TestCase):
    def test_is_non_production(self):
        self.assertTrue(_is_non_production("supabase-acceptance-proof", "dreemzy"))
        self.assertFalse(_is_non_production("bryan-face-reaction", "dreemzy"))


class TestMiningPresentation(unittest.TestCase):
    def candidate(self, **context):
        return _candidate(
            boundary_plan={"recommended_clip_start": 0.125,
                           "recommended_clip_end": 8.625, "event_timestamp": 2.75,
                           "start_reason": "setup starts", "end_reason": "reaction settles",
                           "start_anchors": []},
            moment_evidence={"intent": "tenant-configured intent",
                             "review_context": context,
                             "market_evidence": {"views": 123456, "likes": 0},
                             "what_happens": "A concrete event", "hook_0_3s": "Visible setup",
                             "payoff": "Visible resolution", "reaction_angle": "Explain the surprise"},
            source_relative_rank=1, source_candidate_count=3,
            completeness_verdict="PASS", fire_passed=True)

    def test_strict_card_preserves_exact_window_link_and_market_unknowns(self):
        e = format_source_embed(self.candidate())
        text = json_fields_text(e)
        self.assertEqual(e["title"], "SEGMENT 5 — VIRAL MOMENT MINING ACCEPTANCE")
        self.assertIn("Event 00:02.75", text)
        self.assertIn("Start 00:00.125", text)
        self.assertIn("End 00:08.625", text)
        self.assertIn("Duration 8.5s", text)
        self.assertIn("Likes 0", text)
        self.assertIn("Comments Unavailable", text)
        self.assertIn("Moment rank #1 of 3 complete qualifying moments", text)
        self.assertEqual(e["url"], self.candidate()["canonical_url"])
        self.assertNotIn("thumbnail", e, "strict cards cannot imply an unverified image is verified")

    def test_absent_event_does_not_hide_resolved_native_window(self):
        c = self.candidate()
        del c["boundary_plan"]["event_timestamp"]
        text = json_fields_text(format_source_embed(c))
        self.assertIn("Event Unavailable", text)
        self.assertIn("End 00:08.625", text)

    def test_verified_candidate_thumbnail_takes_precedence_over_still(self):
        e = format_source_embed(self.candidate(
            verified_thumbnail_url="https://images.example/candidate.jpg",
            verified_frame_url="https://images.example/moment.jpg", verified_frame_timestamp_s=3.5))
        self.assertEqual(e["image"]["url"], "https://images.example/candidate.jpg")
        self.assertNotIn("thumbnail", e)

    def test_still_preview_must_be_from_inside_recommended_window(self):
        for when, allowed in ((3.5, True), (0.0, False), (9.0, False), (None, False)):
            with self.subTest(frame_timestamp=when):
                e = format_source_embed(self.candidate(
                    verified_frame_url="https://images.example/moment.jpg",
                    verified_frame_timestamp_s=when))
                self.assertEqual("image" in e, allowed)
                self.assertEqual(e["url"], self.candidate()["canonical_url"])


class TestAuthoritativeCandidateQueue(unittest.TestCase):
    """The queue reads the authoritative post-FIRE candidate table when live."""

    def test_prefers_candidate_table_with_fire_filter(self):
        from unittest import mock
        import source_review as sr
        row = {
            "duplicate_fingerprint": "f" * 64, "tenant_id": "dreemzy",
            "profile_id": "bryan-face-reaction", "platform": "youtube",
            "platform_content_id": "ccccccccccc", "title": "Puppy rescue",
            "canonical_url": "https://www.youtube.com/watch?v=ccccccccccc",
            "creator_channel_identity": "c", "publication_timestamp": None,
            "market_score": 80, "content_score": 85, "selection_score": 88,
            "confidence_score": 87, "selection_outcome": "fast_track",
            "fire_passed": True, "fire_reasons": [],
            "completeness_verdict": "PASS", "duplicate_state": "DISTINCT",
            "boundary_plan": {"start_anchors": [{"timestamp": 1.0}]},
        }
        with mock.patch.object(sr, "_candidates_table_available", return_value=True), \
             mock.patch.object(sr, "decided_fingerprints", return_value=set()), \
             mock.patch.object(sr, "_get", return_value=[row]) as get:
            out = sr.fetch_pending_candidates("http://sb", "k")
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["title"], "Puppy rescue")   # title rides the row
        self.assertTrue(out[0]["fire_passed"])
        # The queue asked for FIRE-passing, non-suppressed rows.
        call = get.call_args_list[0][0][2]
        self.assertIn("fire_passed=is.true", call)
        self.assertIn("duplicate_state=neq.DUPLICATE_SUPPRESSED", call)

    def test_falls_back_to_legacy_join_without_candidate_table(self):
        from unittest import mock
        import source_review as sr
        sel_row = {
            "duplicate_fingerprint": "g" * 64, "tenant_id": "dreemzy",
            "profile_id": "bryan-face-reaction", "platform": "youtube",
            "market_opportunity_score": 70, "market_confidence": 0.8,
            "content_worthiness_score": 75, "content_confidence": 0.5,
            "source_selection_score": 72, "source_selection_confidence": 0.6,
            "outcome": "standard_review",
        }
        src_row = {"duplicate_fingerprint": "g" * 64, "title": "Legacy title",
                   "canonical_url": "https://x", "platform_content_id": "ddddddddddd",
                   "creator_channel_identity": "c", "publication_timestamp": None}
        ca_row = {"duplicate_fingerprint": "g" * 64, "reasons": [],
                  "content_confidence": 0.5, "evidence_level": "transcript"}

        def _fake_get(url, key, path):
            if "loom_source_selections" in path:
                return [sel_row]
            if "loom_discovered_sources" in path:
                return [src_row]
            if "loom_content_assessments" in path:
                return [ca_row]
            return []

        with mock.patch.object(sr, "_candidates_table_available", return_value=False), \
             mock.patch.object(sr, "decided_fingerprints", return_value=set()), \
             mock.patch.object(sr, "_get", side_effect=_fake_get):
            out = sr.fetch_pending_candidates("http://sb", "k")
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["title"], "Legacy title")

    def test_candidate_row_carries_boundary_plan_for_handoff(self):
        from unittest import mock
        import source_review as sr
        row = {
            "duplicate_fingerprint": "h" * 64, "tenant_id": "dreemzy",
            "profile_id": "bryan-face-reaction", "platform": "youtube",
            "platform_content_id": "eeeeeeeeeee", "title": "T",
            "canonical_url": "https://y", "market_score": 80, "content_score": 85,
            "selection_score": 88, "confidence_score": 87,
            "selection_outcome": "fast_track", "fire_passed": True,
            "duplicate_state": "DISTINCT",
            "boundary_plan": {"recommended_clip_start": 2.0, "recommended_clip_end": 14.0},
        }
        with mock.patch.object(sr, "_candidates_table_available", return_value=True), \
             mock.patch.object(sr, "_get", return_value=[row]):
            cand = sr.fetch_candidate_by_fingerprint("http://sb", "k", "h" * 64)
        self.assertEqual(cand["boundary_plan"]["recommended_clip_start"], 2.0)
        self.assertEqual(cand["boundary_plan"]["recommended_clip_end"], 14.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
