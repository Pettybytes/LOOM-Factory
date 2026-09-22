"""Unavailable signals are UNKNOWN, never zero - and coverage is enforced.

Runtime basis: market scoring was a weighted sum in which absent signals contributed
0, so a 150k-view viral Twitch clip scored 26 and NOTHING could ever pass the gate.
Pure renormalization is equally wrong: a record with only freshness+relevance+
provenance renormalizes to ~99, letting sparse evidence masquerade as strong.
The contract is quality discounted by evidence coverage, with a minimum coverage
requirement and the coverage exposed separately.
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
from discovery.topic_intelligence import TopicIntelligence  # noqa: E402

NOW = datetime.datetime.now(datetime.timezone.utc)


def _brief():
    return SourceBrief(tenant_id="dreemzy", profile_id="dreemzy-main", version="2",
                       target_platforms=["youtube"], topics=["funny dog moments"], entities=[],
                       keywords=["dog", "funny", "moment", "acting"], exclusions=[],
                       creator_channel_allowlist=[], creator_channel_blocklist=[],
                       freshness_window_days=90, content_type_requirements=["video"],
                       language="en", provenance_rights_requirements="platform api",
                       acquisition_eligibility=True, cost_ceiling_usd=0.10,
                       manual_review_required=True, connector_restrictions=[])


def _record(**kw):
    base = dict(
        tenant_id="dreemzy", source_brief_version="2", profile_id="dreemzy-main",
        platform="youtube", canonical_url="https://www.youtube.com/watch?v=abcdefghijk",
        platform_content_id="abcdefghijk", title="Funny dog moment acting like a human",
        creator_channel_identity="chan", creator_channel_url="https://youtube.com/chan",
        publication_timestamp=(NOW - datetime.timedelta(days=3)).isoformat(),
        observed_metrics={"content_type": "video", "views": 900000, "likes": 50000,
                          "comments": 4000, "shares": 9000},
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


class ScoringSemanticsTests(unittest.TestCase):
    GATE = 75

    def test_well_supported_candidate_can_pass(self):
        a = _assess()
        self.assertGreaterEqual(a.opportunity_score, self.GATE)
        self.assertEqual(a.opportunity_class, "strong")

    def test_weak_candidate_cannot_pass(self):
        a = _assess(title="unrelated clip",
                    publication_timestamp=(NOW - datetime.timedelta(days=900)).isoformat())
        self.assertLess(a.opportunity_score, self.GATE)

    def test_missing_is_unknown_not_zero(self):
        """Adding real evidence must never lower the score."""
        thin = _assess(observed_metrics={"content_type": "video", "views": 900000},
                       metric_history=[])
        rich = _assess()
        self.assertGreaterEqual(rich.opportunity_score, thin.opportunity_score)

    def test_coverage_is_exposed_and_partial_when_signals_are_missing(self):
        thin = _assess(observed_metrics={"content_type": "video", "views": 900000},
                       metric_history=[])
        rich = _assess()
        self.assertLess(thin.data_completeness, rich.data_completeness)
        self.assertLessEqual(rich.data_completeness, 1.0)

    def test_insufficient_evidence_is_flagged_and_cannot_reach_the_gate(self):
        a = _assess(observed_metrics={"content_type": "video"}, metric_history=[],
                    publication_timestamp=None)
        self.assertLess(a.opportunity_score, self.GATE)
        self.assertIn("INSUFFICIENT_EVIDENCE", a.flags)

    def test_sparse_coverage_is_not_normalized_into_a_strong_score(self):
        a = _assess(observed_metrics={"content_type": "video"}, metric_history=[],
                    publication_timestamp=None)
        self.assertNotEqual(a.opportunity_class, "strong")

    def test_quality_and_coverage_are_separate_signals(self):
        a = _assess()
        self.assertIsInstance(a.data_completeness, float)
        self.assertNotEqual(a.confidence, a.opportunity_score)

    def test_rejection_reasons_are_preserved(self):
        a = _assess(observed_metrics={"content_type": "video"}, metric_history=[],
                    publication_timestamp=None)
        self.assertTrue(a.reasons)


if __name__ == "__main__":
    unittest.main()
