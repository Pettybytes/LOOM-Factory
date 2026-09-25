"""Offline regression proof for observed-demand, established OR emerging."""

import datetime as dt
import json
import sys
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from content_intelligence.market_demand import assess_market_demand

NOW = dt.datetime(2026, 9, 25, 19, 58, 27, tzinfo=dt.timezone.utc)


def record(views=150395, age_hours=490.7 * 24, platform="twitch", **metrics):
    return {
        "platform": platform,
        "title": "Johnny ball getting cracked",
        "observed_metrics": {"view_count": views, **metrics},
        "publication_timestamp": (NOW - dt.timedelta(hours=age_hours)).isoformat(),
        "metric_history": [],
    }


def history(previous, current, *, key="view_count"):
    return [
        {"observed_at": (NOW - dt.timedelta(hours=1)).isoformat(), "metrics": {key: previous}},
        {"observed_at": NOW.isoformat(), "metrics": {key: current}},
    ]


class MarketDemandTests(unittest.TestCase):
    def assess(self, data, **kwargs):
        result = assess_market_demand(data, now=NOW, **kwargs)
        json.dumps(result.to_dict(), allow_nan=False)
        return result

    def test_real_top_twitch_snapshot_passes_established_at_86(self):
        data = record()
        data["publication_timestamp"] = "2025-05-23T03:02:45Z"
        result = self.assess(data, max_age_hours=3650 * 24)
        self.assertEqual(result.score, 86)
        self.assertTrue(result.passed, result.reasons)
        self.assertEqual(result.evidence["route"], "established")
        self.assertAlmostEqual(result.evidence["lifetime_average_vph"], 12.7703, places=4)
        self.assertIsNone(result.evidence["observed_velocity_per_hour"])
        self.assertNotIn("vph", result.evidence)

    def test_title_content_and_provenance_cannot_change_market(self):
        data = record()
        original = self.assess(data)
        data.update(title="watch this funny clear HD shocking payoff",
                    creator_channel_identity="an unrelated creator",
                    provenance_rights_evidence="perfect rights",
                    evidence_confidence=1.0,
                    moment_evidence={"content_worthiness_score": 100})
        data["observed_metrics"]["moment_evidence"] = {"content_worthiness_score": 100}
        changed = self.assess(data)
        self.assertEqual(changed, original)

    def test_volume_magnitude_is_monotonic_on_unchanged_log_scale(self):
        scores = [self.assess(record(n)).score for n in (75, 1000, 30000, 150395, 1000000)]
        self.assertEqual(scores, [31, 50, 75, 86, 100])

    def test_75_raw_views_never_means_market_75(self):
        result = self.assess(record(75, age_hours=1))
        self.assertFalse(result.passed)
        self.assertIn("LOW_VIEWS", result.reasons)
        self.assertIn("MARKET_BELOW_75", result.reasons)

    def test_age_adjusted_volume_floor_remains_required_even_with_momentum(self):
        data = record(900, age_hours=2)
        data["metric_history"] = history(0, 900)
        result = self.assess(data)
        self.assertEqual(result.score, 90)
        self.assertFalse(result.passed)
        self.assertIn("LOW_VIEWS", result.reasons)
        self.assertEqual(result.evidence["minimum_volume"], 1000)

    def test_observed_derivative_can_qualify_emerging_below_established_score(self):
        data = record(1500, age_hours=2)
        data["metric_history"] = history(500, 1500)
        result = self.assess(data)
        self.assertEqual(result.score, 100)
        self.assertTrue(result.passed, result.reasons)
        self.assertEqual(result.evidence["route"], "emerging")
        self.assertEqual(result.evidence["observed_velocity_per_hour"], 1000)
        self.assertEqual(result.evidence["lifetime_average_vph"], 750)

    def test_single_observation_does_not_invent_emerging_velocity(self):
        data = record(1500, age_hours=1)
        data["metric_history"] = history(500, 1500)[-1:]
        result = self.assess(data)
        self.assertFalse(result.passed)
        self.assertIsNone(result.evidence["observed_velocity_per_hour"])
        self.assertIsNone(result.evidence["emerging_score"])

    def test_zero_or_negative_observed_growth_does_not_erase_established_demand(self):
        for previous in (150395, 151395):
            with self.subTest(previous=previous):
                data = record()
                data["metric_history"] = history(previous, 150395)
                result = self.assess(data)
                self.assertEqual(result.score, 86)
                self.assertTrue(result.passed)
                self.assertEqual(result.evidence["route"], "established")

    def test_unknown_engagement_remains_unknown_but_observed_zero_is_not_ignored(self):
        unknown = self.assess(record())
        self.assertTrue(unknown.passed)
        for key in ("likes", "comments", "shares", "saves", "engagement"):
            self.assertIsNone(unknown.evidence[key], key)
        zero = self.assess(record(likes=0, comments=0))
        self.assertFalse(zero.passed)
        self.assertIn("LOW_LIKES", zero.reasons)
        self.assertIn("LOW_ENGAGEMENT", zero.reasons)

    def test_tenant_freshness_policy_is_respected_not_imposed_globally(self):
        self.assertTrue(self.assess(record()).passed)
        limited = self.assess(record(), max_age_hours=30 * 24)
        self.assertFalse(limited.passed)
        self.assertIn("STALE", limited.reasons)

    def test_missing_or_nonfinite_volume_fails_without_fabricated_views(self):
        for value in (None, float("nan"), float("inf"), True, -10):
            with self.subTest(value=value):
                result = self.assess(record(value))
                self.assertFalse(result.passed)
                self.assertIsNone(result.evidence["views"])
                self.assertIn("INSUFFICIENT_MARKET_EVIDENCE", result.reasons)

    def test_reddit_uses_existing_native_score_scale_and_no_invented_views(self):
        data = record(None, platform="reddit", score=40000, num_comments=900)
        result = self.assess(data)
        self.assertEqual(result.score, 77)
        self.assertTrue(result.passed)
        self.assertEqual(result.evidence["demand_metric"], "reddit_score")
        self.assertEqual(result.evidence["demand_volume"], 40000)
        self.assertEqual(result.evidence["minimum_volume"], 250)
        self.assertIsNone(result.evidence["views"])
        self.assertIsNone(result.evidence["lifetime_average_vph"])
        self.assertNotIn("reddit_score_per_hour", result.evidence)

    def test_reddit_native_derivative_and_meta_suppression(self):
        data = record(None, age_hours=2, platform="reddit", score=1500)
        data["metric_history"] = history(500, 1500, key="ups")
        result = self.assess(data)
        self.assertTrue(result.passed)
        self.assertEqual(result.evidence["route"], "emerging")
        self.assertEqual(result.evidence["observed_velocity_metric"], "reddit_score")
        data["observed_metrics"]["stickied"] = True
        self.assertIn("SUBREDDIT_META", self.assess(data).reasons)

    def test_native_aliases_and_object_records_are_supported_without_mutation(self):
        data = record(None, age_hours=2, platform="tiktok", playCount=1500)
        data["metric_history"] = history(500, 1500, key="playCount")
        original = deepcopy(data)
        result = self.assess(SimpleNamespace(**data))
        self.assertTrue(result.passed)
        self.assertEqual(result.evidence["observed_velocity_per_hour"], 1000)
        self.assertEqual(data, original)


if __name__ == "__main__":
    unittest.main()
