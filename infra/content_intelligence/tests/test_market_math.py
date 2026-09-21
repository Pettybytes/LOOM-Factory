"""Deterministic market math: floors, velocity, baseline, outlier, the gate."""

from __future__ import annotations

import datetime as _dt
import sys
import unittest
from pathlib import Path

_SUITE = Path(__file__).resolve().parents[1]
if str(_SUITE) not in sys.path:
    sys.path.insert(0, str(_SUITE))

from market_math import (  # noqa: E402
    MIN_ENGAGEMENT_RATE, MIN_OUTLIER_MULTIPLE, age_adjusted_floor, age_hours,
    creator_baseline, engagement_rate, market_gate, median, outlier_multiple,
    views_per_hour,
)

NOW = _dt.datetime(2026, 9, 21, 12, 0, tzinfo=_dt.timezone.utc)


def _ago(hours: float) -> str:
    return (NOW - _dt.timedelta(hours=hours)).isoformat()


class MedianTests(unittest.TestCase):
    def test_odd_length_picks_middle(self):
        self.assertEqual(median([3, 1, 2]), 2)

    def test_even_length_averages_two_middle(self):
        self.assertEqual(median([1, 2, 3, 4]), 2.5)

    def test_empty_and_none(self):
        self.assertIsNone(median([]))
        self.assertIsNone(median([None, None]))

    def test_outlier_does_not_drag_median_vs_mean(self):
        vals = [100, 110, 90, 105, 1_000_000]
        self.assertEqual(median(vals), 105)


class VelocityTests(unittest.TestCase):
    def test_vph_is_views_over_age(self):
        self.assertEqual(views_per_hour(2400, _ago(24), NOW), 100.0)

    def test_vph_not_claimed_under_an_hour(self):
        self.assertIsNone(views_per_hour(500, _ago(0.5), NOW))

    def test_vph_none_without_views_or_date(self):
        self.assertIsNone(views_per_hour(None, _ago(5), NOW))
        self.assertIsNone(views_per_hour(100, None, NOW))

    def test_age_hours_parses_iso(self):
        self.assertAlmostEqual(age_hours(_ago(3), NOW), 3.0, places=3)


class BaselineTests(unittest.TestCase):
    def test_baseline_is_median_of_creator_history(self):
        self.assertEqual(creator_baseline([100, 200, 300]), 200)

    def test_pinned_posts_excluded_from_baseline(self):
        samples = [{"views": 50, "is_pinned": True}, {"views": 1000}, {"views": 1200}]
        self.assertEqual(creator_baseline(samples), 1100)

    def test_baseline_window_applied(self):
        self.assertEqual(creator_baseline([10, 10, 10, 1000], window=3), 10)

    def test_outlier_multiple(self):
        self.assertEqual(outlier_multiple(3000, 1000), 3.0)
        self.assertIsNone(outlier_multiple(3000, None))
        self.assertIsNone(outlier_multiple(3000, 0))


class EngagementTests(unittest.TestCase):
    def test_engagement_rate_sums_actions_over_views(self):
        self.assertEqual(engagement_rate(50, 10, 5, 1000), 0.065)

    def test_engagement_none_without_views(self):
        self.assertIsNone(engagement_rate(1, 1, 1, 0))


class FloorTests(unittest.TestCase):
    def test_floors_rise_with_age(self):
        young = age_adjusted_floor(2)
        mid = age_adjusted_floor(48)
        old = age_adjusted_floor(500)
        self.assertLess(young[0], mid[0])
        self.assertLess(mid[0], old[0])

    def test_unknown_age_uses_strictest_floor(self):
        self.assertEqual(age_adjusted_floor(None), age_adjusted_floor(10_000))


class MarketGateTests(unittest.TestCase):
    def test_eighty_four_view_candidate_dies_at_market_gate(self):
        """The explicit requirement: noise must never reach a human."""
        d = market_gate(views=84, likes=3, published_at=_ago(30), now=NOW)
        self.assertFalse(d.passed)
        self.assertTrue(any("views_floor" in r for r in d.reasons), d.reasons)
        self.assertEqual(d.metrics["views"], 84)

    def test_strong_old_candidate_passes(self):
        d = market_gate(views=250_000, likes=9_000, comments=400, shares=300,
                        published_at=_ago(200), now=NOW)
        self.assertTrue(d.passed, d.reasons)

    def test_low_velocity_rejected_even_with_big_total(self):
        d = market_gate(views=60_000, likes=4_000, published_at=_ago(600), now=NOW)
        self.assertFalse(d.passed)
        self.assertTrue(any("velocity" in r for r in d.reasons), d.reasons)

    def test_creator_relative_multiple_rejects_below_threshold(self):
        d = market_gate(views=12_000, likes=900, published_at=_ago(40), now=NOW,
                        baseline_median=10_000)
        self.assertFalse(d.passed)
        self.assertTrue(any("outlier_multiple" in r for r in d.reasons), d.reasons)

    def test_creator_relative_multiple_passes_when_well_above_baseline(self):
        d = market_gate(views=12_000, likes=900, published_at=_ago(40), now=NOW,
                        baseline_median=2_000)
        self.assertTrue(d.passed, d.reasons)

    def test_no_view_count_is_explicit(self):
        d = market_gate(views=None, published_at=_ago(10), now=NOW)
        self.assertFalse(d.passed)
        self.assertIn("no_view_count", d.reasons)

    def test_every_rejection_carries_a_reason(self):
        d = market_gate(views=84, likes=0, published_at=_ago(30), now=NOW)
        self.assertFalse(d.passed)
        self.assertTrue(d.reasons)


if __name__ == "__main__":
    unittest.main()
