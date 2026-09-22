"""Content-first Market scoring.

Direction: Market assesses the CLIP (moment/hook, action or emotional payoff,
reactions/audio energy where relevant, visual clarity) and reuses the existing
content-analysis machinery rather than adding a second gate. Solid views/likes are
supporting positives: missing engagement metrics or velocity history must NOT block a
strong clip. cross_platform and saturation are no longer scored or required (dedup
stays separate). The old quality x coverage rejection is gone. Failed media analysis
is never treated as a pass.
"""

from __future__ import annotations

import datetime
import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from discovery.discovery_record import DiscoveryRecord  # noqa: E402
from discovery.source_brief import SourceBrief  # noqa: E402
from discovery.topic_intelligence import WEIGHTS, TopicIntelligence  # noqa: E402

NOW = datetime.datetime.now(datetime.timezone.utc)
GATE = 75


def _brief():
    return SourceBrief(tenant_id="dreemzy", profile_id="dreemzy-main", version="2",
                       target_platforms=["youtube"], topics=["funny dog moments"], entities=[],
                       keywords=["dog", "funny", "moment", "acting"], exclusions=[],
                       creator_channel_allowlist=[], creator_channel_blocklist=[],
                       freshness_window_days=180, content_type_requirements=["video"],
                       language="en", provenance_rights_requirements="platform api",
                       acquisition_eligibility=True, cost_ceiling_usd=0.10,
                       manual_review_required=True, connector_restrictions=[])


def _record(**kw):
    base = dict(
        tenant_id="dreemzy", source_brief_version="2", profile_id="dreemzy-main",
        platform="youtube", canonical_url="https://www.youtube.com/watch?v=abcdefghijk",
        platform_content_id="abcdefghijk",
        title="dog has a funny moment acting like a human",
        creator_channel_identity="chan", creator_channel_url="https://youtube.com/chan",
        publication_timestamp=(NOW - datetime.timedelta(days=3)).isoformat(),
        observed_metrics={"content_type": "video", "views": 900000, "likes": 50000},
        metric_observation_timestamp=NOW.isoformat(),
        metric_history=[{"observed_at": (NOW - datetime.timedelta(days=2)).isoformat(),
                         "metrics": {"views": 300000}},
                        {"observed_at": NOW.isoformat(), "metrics": {"views": 900000}}],
        discovery_query="funny dog moments", discovery_reason="t", evidence_confidence=0.9,
        provenance_rights_evidence="platform api", duplicate_fingerprint="x",
        global_fingerprint="x", processing_eligibility=True, qualification_status="pending",
        connector_name="t", connector_version="1")
    base.update(kw)
    return DiscoveryRecord(**base)


def _assess(**kw):
    return TopicIntelligence().assess(_record(**kw), _brief())


class ContentFirstWeightingTests(unittest.TestCase):
    def test_content_leads_the_weighting(self):
        self.assertIn("content", WEIGHTS)
        self.assertEqual(max(WEIGHTS, key=WEIGHTS.get), "content")

    def test_cross_platform_and_saturation_are_not_scored(self):
        self.assertNotIn("cross_platform", WEIGHTS)
        self.assertNotIn("saturation", WEIGHTS)

    def test_weights_are_normalised(self):
        self.assertAlmostEqual(sum(WEIGHTS.values()), 1.0, places=6)


class MissingDataMustNotBlockTests(unittest.TestCase):
    def test_missing_engagement_and_velocity_do_not_block(self):
        """A strong clip with no likes/comments and no history still qualifies."""
        a = _assess(observed_metrics={"content_type": "video", "views": 250000},
                    metric_history=[])
        self.assertNotIn("INSUFFICIENT_EVIDENCE", a.flags)
        self.assertGreaterEqual(a.opportunity_score, GATE)

    def test_quiet_wholesome_animal_clip_is_not_penalised(self):
        """No shouting, no chat activity - absence of hype is not absence of content."""
        a = _assess(title="cute dog gently shares his favourite toy with a tiny kitten",
                    observed_metrics={"content_type": "video", "views": 90000},
                    metric_history=[])
        self.assertNotIn("INSUFFICIENT_EVIDENCE", a.flags)
        self.assertGreaterEqual(a.opportunity_score, 60)

    def test_supporting_evidence_only_lifts_the_score(self):
        rich = _assess()
        thin = _assess(observed_metrics={"content_type": "video", "views": 250000},
                       metric_history=[])
        self.assertGreaterEqual(rich.opportunity_score, thin.opportunity_score - 5)


class NoContentEvidenceTests(unittest.TestCase):
    def test_failed_media_analysis_never_passes(self):
        a = _assess(title="", description="", observed_metrics={"content_type": "video"},
                    metric_history=[])
        self.assertIn("INSUFFICIENT_EVIDENCE", a.flags)
        self.assertLess(a.opportunity_score, GATE)

    def test_content_absence_is_reported(self):
        a = _assess(title="", description="", observed_metrics={"content_type": "video"},
                    metric_history=[])
        self.assertTrue(a.reasons)


class EvidenceVisibilityTests(unittest.TestCase):
    def test_coverage_and_confidence_stay_visible(self):
        a = _assess()
        self.assertIsInstance(a.data_completeness, float)
        self.assertIsNotNone(a.confidence)

    def test_strong_candidate_scores_high(self):
        a = _assess()
        self.assertGreaterEqual(a.opportunity_score, 60)


if __name__ == "__main__":
    unittest.main()
