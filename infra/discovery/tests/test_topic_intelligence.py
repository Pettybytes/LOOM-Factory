"""Deterministic tests for Topic Intelligence V1.

Run:  python3 -m unittest discover -s infra/discovery/tests -v
"""

import datetime as _dt
import json
import sys
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
for _p in (str(_REPO_ROOT / "infra"), str(_REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from discovery.source_brief import SourceBrief, is_non_production  # noqa: E402
from discovery.discovery_record import DiscoveryRecord  # noqa: E402
from discovery.normalization import normalize_signals  # noqa: E402
from discovery.velocity import compute_velocity, VelocityResult  # noqa: E402
from discovery.topic_intelligence import (  # noqa: E402
    TopicIntelligence,
    OpportunityAssessment,
    OpportunityClass,
    WEIGHTS,
    SCORE_VERSION,
)
from discovery.store import DiscoveryStore  # noqa: E402


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
NOW = _dt.datetime(2026, 9, 17, 12, 0, 0, tzinfo=_dt.timezone.utc)


def make_brief(**overrides) -> SourceBrief:
    data = dict(
        tenant_id="dreemzy",
        profile_id="bryan-face-reaction",
        version="1",
        target_platforms=["youtube", "reddit"],
        topics=["pets", "animals"],
        entities=["puppy"],
        keywords=["rescue", "viral", "cute"],
        exclusions=["graphic"],
        creator_channel_allowlist=[],
        creator_channel_blocklist=[],
        freshness_window_days=14,
        content_type_requirements=["video", "post"],
        language="en",
        provenance_rights_requirements="first-party source URL",
        acquisition_eligibility=True,
        cost_ceiling_usd=0.0,
        manual_review_required=True,
        connector_restrictions=[],
    )
    data.update(overrides)
    return SourceBrief(**data)


def make_record(**overrides) -> DiscoveryRecord:
    data = dict(
        tenant_id="dreemzy",
        source_brief_version="1",
        profile_id="bryan-face-reaction",
        platform="youtube",
        canonical_url="https://www.youtube.com/watch?v=aaaaaaaaaaa",
        platform_content_id="aaaaaaaaaaa",
        creator_channel_identity="Some Channel",
        creator_channel_url="https://www.youtube.com/channel/UCx",
        title="Puppy rescue goes viral",
        publication_timestamp="2026-09-16T12:00:00+00:00",
        observed_metrics={"views": 1000000, "content_type": "video"},
        metric_observation_timestamp="2026-09-17T11:00:00+00:00",
        metric_history=[
            {"observed_at": "2026-09-16T12:00:00+00:00", "metrics": {"views": 500000}},
            {"observed_at": "2026-09-17T11:00:00+00:00", "metrics": {"views": 1000000}},
        ],
        discovery_query="channel:UCx",
        discovery_reason="youtube channel feed",
        evidence_confidence=1.0,
        provenance_rights_evidence="first-party RSS",
        duplicate_fingerprint="fp-aaaaaaaaaaa",
        global_fingerprint="gp-aaaaaaaaaaa",
        processing_eligibility="manual_ingest_only",
        qualification_status="manual_review",
        connector_name="youtube",
        connector_version="1.0.0",
    )
    data.update(overrides)
    return DiscoveryRecord(**data)


class TestVelocity(unittest.TestCase):
    def test_no_history_unavailable(self):
        v = compute_velocity([], metric_key="views")
        self.assertFalse(v.available)
        self.assertIn("no observation history", v.reason)

    def test_single_observation_unavailable(self):
        v = compute_velocity(
            [{"observed_at": "2026-09-16T12:00:00+00:00", "metrics": {"views": 500}}],
            metric_key="views",
        )
        self.assertFalse(v.available)
        self.assertIn("one distinct observation", v.reason)

    def test_missing_metric_unavailable(self):
        v = compute_velocity(
            [
                {"observed_at": "2026-09-16T12:00:00+00:00", "metrics": {"content_type": "post"}},
                {"observed_at": "2026-09-17T12:00:00+00:00", "metrics": {"content_type": "post"}},
            ],
            metric_key="views",
        )
        self.assertFalse(v.available)
        self.assertIn("absent", v.reason)

    def test_two_observations_computable(self):
        v = compute_velocity(
            [
                {"observed_at": "2026-09-16T12:00:00+00:00", "metrics": {"views": 1000}},
                {"observed_at": "2026-09-16T14:00:00+00:00", "metrics": {"views": 3000}},
            ],
            metric_key="views",
        )
        self.assertTrue(v.available)
        self.assertEqual(v.value_per_hour, 1000.0)

    def test_negative_velocity_reported(self):
        v = compute_velocity(
            [
                {"observed_at": "2026-09-16T12:00:00+00:00", "metrics": {"views": 3000}},
                {"observed_at": "2026-09-16T14:00:00+00:00", "metrics": {"views": 1000}},
            ],
            metric_key="views",
        )
        self.assertTrue(v.available)
        self.assertLess(v.value_per_hour, 0)

    def test_zero_elapsed_unavailable(self):
        v = compute_velocity(
            [
                {"observed_at": "2026-09-16T12:00:00+00:00", "metrics": {"views": 1000}},
                {"observed_at": "2026-09-16T12:00:00+00:00", "metrics": {"views": 3000}},
            ],
            metric_key="views",
        )
        # Identical timestamps collapse to one distinct observation.
        self.assertFalse(v.available)
        self.assertIn("one distinct observation", v.reason)

    def test_duplicate_timestamps_collapse(self):
        v = compute_velocity(
            [
                {"observed_at": "2026-09-16T12:00:00+00:00", "metrics": {"views": 1000}},
                {"observed_at": "2026-09-16T12:00:00+00:00", "metrics": {"views": 2000}},
                {"observed_at": "2026-09-16T14:00:00+00:00", "metrics": {"views": 4000}},
            ],
            metric_key="views",
        )
        # 2000 -> 4000 over 2h = 1000/hr (duplicate 12:00 collapsed to latest).
        self.assertTrue(v.available)
        self.assertEqual(v.value_per_hour, 1000.0)
        self.assertEqual(v.n_observations, 2)


class TestNormalization(unittest.TestCase):
    def test_youtube_views_normalized(self):
        sig = normalize_signals(make_record())
        self.assertEqual(sig.volume, 1000000)
        self.assertEqual(sig.volume_metric, "views")
        self.assertIn("likes", sig.missing_metrics)
        self.assertIn("comments", sig.missing_metrics)
        self.assertIsNone(sig.engagement_rate)

    def test_reddit_missing_engagement(self):
        rec = make_record(
            platform="reddit",
            platform_content_id="1w5k5h4",
            canonical_url="https://www.reddit.com/r/pets/comments/1w5k5h4/x/",
            duplicate_fingerprint="fp-reddit",
            global_fingerprint="gp-reddit",
            observed_metrics={"content_type": "post", "source_url_references": ["https://example.com/puppy"]},
            metric_history=[{"observed_at": "2026-09-17T11:00:00+00:00", "metrics": {"content_type": "post"}}],
        )
        sig = normalize_signals(rec)
        self.assertIsNone(sig.volume)
        self.assertIn("score", sig.missing_metrics)
        self.assertIn("ups", sig.missing_metrics)
        self.assertEqual(len(sig.source_url_references), 1)


class TestTopicIntelligence(unittest.TestCase):
    def setUp(self):
        self.ti = TopicIntelligence()
        self.brief = make_brief()

    def test_deterministic_same_input_same_output(self):
        rec = make_record()
        a = self.ti.assess(rec, self.brief, now=NOW)
        b = self.ti.assess(rec, self.brief, now=NOW)
        self.assertEqual(a.opportunity_score, b.opportunity_score)
        self.assertEqual(a.opportunity_class, b.opportunity_class)
        self.assertEqual(a.to_dict()["reasons"], b.to_dict()["reasons"])
        self.assertEqual(a.flags, b.flags)

    def test_strong_candidate(self):
        rec = make_record()
        a = self.ti.assess(rec, self.brief, now=NOW, competition_count=0)
        self.assertGreaterEqual(a.opportunity_score, 70)
        self.assertEqual(a.opportunity_class, OpportunityClass.STRONG)

    def test_weak_candidate(self):
        rec = make_record(
            publication_timestamp="2026-09-04T12:00:00+00:00",  # 13d old, within window
            observed_metrics={"views": 0, "content_type": "video"},
            metric_history=[],
            title="unrelated topic here",
        )
        a = self.ti.assess(rec, self.brief, now=NOW, competition_count=0)
        self.assertLess(a.opportunity_score, 40)
        self.assertEqual(a.opportunity_class, OpportunityClass.WEAK)

    def test_stale_candidate(self):
        rec = make_record(publication_timestamp="2026-08-01T12:00:00+00:00")
        a = self.ti.assess(rec, self.brief, now=NOW)
        self.assertEqual(a.opportunity_class, OpportunityClass.STALE)

    def test_duplicate_candidate(self):
        rec = make_record()
        a = self.ti.assess(rec, self.brief, now=NOW, duplicate_kind="repost")
        self.assertEqual(a.opportunity_class, OpportunityClass.DUPLICATE)
        self.assertIn("DUPLICATE_REPOST", a.flags)

    def test_incomplete_candidate(self):
        rec = make_record(
            platform="reddit",
            platform_content_id="1w5k5h4",
            canonical_url="https://www.reddit.com/r/pets/comments/1w5k5h4/x/",
            duplicate_fingerprint="fp-reddit",
            global_fingerprint="gp-reddit",
            publication_timestamp="",
            observed_metrics={"content_type": "post"},
            metric_history=[],
            provenance_rights_evidence="",
            title="Puppy rescue",
        )
        a = self.ti.assess(rec, self.brief, now=NOW, competition_count=0)
        self.assertEqual(a.opportunity_class, OpportunityClass.INCOMPLETE)
        self.assertIn("LOW_CONFIDENCE", a.flags)

    def test_velocity_contributes_when_available(self):
        no_vel = self.ti.assess(
            make_record(metric_history=[]), self.brief, now=NOW, competition_count=0
        )
        with_vel = self.ti.assess(
            make_record(), self.brief, now=NOW, competition_count=0
        )
        self.assertGreater(with_vel.signal_scores["velocity"].score,
                           no_vel.signal_scores["velocity"].score)

    def test_missing_metrics_lower_confidence(self):
        full = self.ti.assess(make_record(), self.brief, now=NOW, competition_count=0)
        missing = self.ti.assess(
            make_record(metric_history=[], provenance_rights_evidence=""),
            self.brief, now=NOW, competition_count=0,
        )
        self.assertGreater(full.data_completeness, missing.data_completeness)
        self.assertGreater(full.confidence, missing.confidence)

    def test_reddit_not_pretended_identical(self):
        reddit = make_record(
            platform="reddit",
            platform_content_id="1w5k5h4",
            canonical_url="https://www.reddit.com/r/pets/comments/1w5k5h4/x/",
            duplicate_fingerprint="fp-reddit",
            global_fingerprint="gp-reddit",
            observed_metrics={"content_type": "post"},
            metric_history=[],
            title="Puppy rescue",
        )
        yt = make_record()
        a_reddit = self.ti.assess(reddit, self.brief, now=NOW, competition_count=0)
        a_yt = self.ti.assess(yt, self.brief, now=NOW, competition_count=0)
        self.assertFalse(a_reddit.signal_scores["engagement"].present)
        self.assertTrue(a_yt.signal_scores["engagement"].present)
        # Reddit engagement is honestly absent, not equal to YouTube's.
        self.assertNotEqual(a_reddit.signal_scores["engagement"].score,
                            a_yt.signal_scores["engagement"].score)

    def test_content_format_carried_through(self):
        rec = make_record(content_format="ranking_top5")
        a = self.ti.assess(rec, self.brief, now=NOW)
        self.assertEqual(a.content_format, "ranking_top5")

    def test_content_format_defaults_hold_unassigned(self):
        a = self.ti.assess(make_record(), self.brief, now=NOW)
        self.assertEqual(a.content_format, "hold_unassigned")

    def test_weights_sum_to_one(self):
        self.assertAlmostEqual(sum(WEIGHTS.values()), 1.0, places=9)


class TestAssessmentStore(unittest.TestCase):
    def _assess(self, ti, brief, rec, **kw):
        return ti.assess(rec, brief, now=NOW, competition_count=kw.get("competition_count", 0))

    def test_upsert_idempotent_replay(self):
        store = DiscoveryStore(":memory:")
        ti = TopicIntelligence()
        brief = make_brief()
        rec = make_record()
        a = self._assess(ti, brief, rec)
        self.assertEqual(store.upsert_assessment(a), "inserted")
        self.assertEqual(store.upsert_assessment(a), "updated")
        self.assertEqual(store.assessment_count(), 1)

    def test_replay_does_not_duplicate_or_corrupt(self):
        store = DiscoveryStore(":memory:")
        ti = TopicIntelligence()
        brief = make_brief()
        rec = make_record()
        a1 = self._assess(ti, brief, rec)
        store.upsert_assessment(a1)
        # Re-scoring the SAME record produces the same assessment; replaying it
        # must not append a new row or change the score.
        a2 = self._assess(ti, brief, rec)
        self.assertEqual(a1.opportunity_score, a2.opportunity_score)
        store.upsert_assessment(a2)
        self.assertEqual(store.assessment_count(), 1)
        rows = store.assessments()
        self.assertEqual(rows[0]["opportunity_score"], a1.opportunity_score)

    def test_production_assessments_exclude_proof_profiles(self):
        store = DiscoveryStore(":memory:")
        ti = TopicIntelligence()
        brief = make_brief()
        prod = make_record()
        proof = make_record(
            profile_id="supabase-acceptance-proof",
            platform_content_id="bbbbbbbbbbb",
            canonical_url="https://www.youtube.com/watch?v=bbbbbbbbbbb",
            duplicate_fingerprint="fp-proof",
            global_fingerprint="gp-proof",
        )
        store.upsert_assessment(self._assess(ti, brief, prod))
        store.upsert_assessment(self._assess(ti, brief, proof))
        prods = store.production_assessments()
        self.assertEqual(len(prods), 1)
        self.assertEqual(prods[0]["profile_id"], "bryan-face-reaction")


if __name__ == "__main__":
    unittest.main(verbosity=2)
