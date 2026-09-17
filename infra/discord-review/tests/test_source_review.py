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


class TestNonProduction(unittest.TestCase):
    def test_is_non_production(self):
        self.assertTrue(_is_non_production("supabase-acceptance-proof", "dreemzy"))
        self.assertFalse(_is_non_production("bryan-face-reaction", "dreemzy"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
