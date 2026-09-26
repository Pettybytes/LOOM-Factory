"""Targeted tests: manual-intake card must render the analyzer's real findings.

Live defect (2026-09-26): the real supplied TikTok clip was analyzed (media_assessment
carries dimension scores and explicit reasons) and a coherent reaction angle was
produced, yet the card rendered Moment / Why selected / Hook / Payoff as
"Unavailable" — i.e. real analysis looked like missing data. Content was genuinely
rejected (no qualifying moment), so the card must say THAT, with the model's own
reason, and manual approvals must be named for what they do.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
for _p in (str(_REPO / "infra" / "discord-review"), str(_REPO / "infra"), str(_REPO / "infra" / "acquisition")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import source_review  # noqa: E402

REASON = "The video consists of a simple outfit showcase with minimal narrative progression."


def _candidate(*, analyzed_no_moment=True, manual=True):
    me = {
        "intent": "operator-supplied content for human review",
        "reaction_angle": "Reviewing a casual everyday outfit fit check.",
        "inspected_moments": [],
        "qualification": {"intent_match": True, "adult_status": "confirmed_adults",
                          "nudity": False, "explicit_sex": False,
                          "concept_evidence": [{"concept": "outfit showcase",
                                                "observation": "A person is showing off casual clothing in front of a mirror."}]},
        "market_evidence": {"views": None, "likes": None, "market_score": 0},
        "review_context": {"review_mode": "human_curated", "review_rank": 1, "review_count": 1},
        "lineage": {"origin": "manual_media_intake" if manual else "platform_discovery",
                    "media_already_acquired": manual},
    }
    if not analyzed_no_moment:
        me["what_happens"] = "Presenter reacts to the wave"
        me["inspected_moments"] = [{"start_s": 0.0}]
    return {
        "duplicate_fingerprint": "a" * 64, "tenant_id": "dreemzy", "platform": "tiktok",
        "title": "https://www.tiktok.com/@midflightclips/video/7667225763720023327",
        "canonical_url": "https://www.tiktok.com/@midflightclips/video/7667225763720023327",
        "creator_channel_identity": "passportpetty",
        "market_opportunity_score": 0, "content_worthiness_score": 0,
        "source_selection_score": 0, "source_selection_confidence": 33,
        "fire_passed": False, "fire_reasons": ["LOW_CONTENT"],
        "completeness_verdict": "FAIL", "moment_type": {"moment_type": "unknown"},
        "boundary_plan": {}, "suggested_timestamps": [],
        "moment_evidence": me,
        "media_assessment": ({"scores": {"hook": 35, "relatability": 40, "story_progression": 20,
                                         "narrative_completeness": 30},
                              "moments": [], "reasons": [{"code": "LOW_NARRATIVE",
                                                          "signal": "visual_motion",
                                                          "message": REASON}]}
                             if analyzed_no_moment else {}),
    }


def _field(embed, name):
    for f in embed.get("fields") or []:
        if f.get("name") == name:
            return str(f.get("value"))
    return ""


class TestRejectedButAnalyzedCard(unittest.TestCase):
    def test_states_no_qualifying_moment_with_the_models_own_reason(self):
        embed = source_review.format_source_embed(_candidate())
        self.assertIn("No qualifying moment identified", _field(embed, "Moment"))
        self.assertIn(REASON, _field(embed, "Why selected"))

    def test_hook_and_payoff_are_not_rendered_as_missing_data(self):
        embed = source_review.format_source_embed(_candidate())
        self.assertIn("no qualifying moment", _field(embed, "First 0–3 second hook"))
        self.assertIn("no qualifying moment", _field(embed, "Payoff / resolution"))

    def test_analyzer_findings_keep_the_understanding_visible(self):
        embed = source_review.format_source_embed(_candidate())
        findings = _field(embed, "Analyzer findings")
        self.assertIn("Observed:", findings)
        self.assertIn("outfit showcase", findings)
        self.assertIn("Dimensions:", findings)
        self.assertIn("hook 35", findings)

    def test_coherent_reaction_angle_is_preserved(self):
        embed = source_review.format_source_embed(_candidate())
        self.assertIn("outfit fit check", _field(embed, "Reaction / commentary angle"))

    def test_candidate_with_a_real_moment_is_unchanged(self):
        embed = source_review.format_source_embed(_candidate(analyzed_no_moment=False))
        self.assertIn("Presenter reacts to the wave", _field(embed, "Moment"))
        self.assertNotIn("No qualifying moment identified", _field(embed, "Moment"))
        self.assertEqual(_field(embed, "Analyzer findings"), "")


class TestApprovalNaming(unittest.TestCase):
    def _labels(self, **kw):
        rows = source_review.format_source_buttons("a" * 64, source_url="https://x", **kw)
        return [b.get("label") for row in rows for b in row["components"]]

    def test_manual_media_is_approved_as_a_clip(self):
        self.assertIn("Approve Clip", self._labels(manual_media=True))
        self.assertNotIn("Approve for Acquisition", self._labels(manual_media=True))

    def test_discovered_candidates_keep_the_acquisition_wording(self):
        self.assertIn(source_review.DECISION_LABELS[source_review.DECISION_APPROVE],
                      self._labels())


class TestAssessmentReachesTheCard(unittest.TestCase):
    def test_row_mapper_passes_the_media_assessment_through(self):
        row = {"duplicate_fingerprint": "a" * 64,
               "media_assessment": {"reasons": [{"message": REASON}]},
               "moment_evidence": {}}
        self.assertEqual(source_review._candidate_from_row(row)["media_assessment"]["reasons"][0]["message"],
                         REASON)


if __name__ == "__main__":
    unittest.main(verbosity=2)
