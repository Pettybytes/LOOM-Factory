"""Durable creator baselines: median, window, pinned, isolation, dedupe, UNKNOWN."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from content_intelligence.creator_history import (  # noqa: E402
    INSUFFICIENT_HISTORY, KNOWN, UNAVAILABLE, baseline_for, baseline_from_observations,
    latest_per_source, observation_row, observations_for, observations_from_record,
    outlier_label, relative_evidence,
)


def obs(views, *, creator="c1", platform="tiktok", tenant="dreemzy", src="s1",
        pinned=False, observed="2026-09-21T10:00:00+00:00", published="2026-09-01T00:00:00+00:00"):
    return {"tenant_id": tenant, "platform": platform, "creator_id": creator,
            "source_content_id": src, "views": views, "is_pinned": pinned,
            "observed_at": observed, "published_at": published}


class BaselineMathTests(unittest.TestCase):
    def test_median_not_mean_from_observations(self):
        rows = [obs(v, src=f"s{i}") for i, v in enumerate([100, 110, 90, 105, 1_000_000])]
        r = baseline_from_observations(rows)
        self.assertEqual(r.state, KNOWN)
        self.assertEqual(r.value, 105)          # median, not the 200,081 mean

    def test_pinned_excluded(self):
        rows = [obs(50, src="pin", pinned=True)] + [obs(v, src=f"n{i}") for i, v in enumerate([900, 1000, 1100, 1200, 1300])]
        self.assertEqual(baseline_from_observations(rows).value, 1100)

    def test_bounded_recent_window(self):
        rows = [obs(v, src=f"w{i}", published=f"2026-09-{i+1:02d}T00:00:00+00:00")
                for i, v in enumerate([10, 20, 30, 1000, 1100])]   # window=3 -> newest 3
        # window=3 with a matching minimum: the default minimum (5 samples) would
        # (correctly) report INSUFFICIENT_HISTORY rather than fabricate a baseline.
        self.assertEqual(baseline_from_observations(rows, window=3, minimum=3).value, 1000)

    def test_insufficient_history_is_unknown_not_zero(self):
        r = baseline_from_observations([obs(500, src="a"), obs(600, src="b")])
        self.assertEqual(r.state, INSUFFICIENT_HISTORY)
        self.assertIsNone(r.value)
        self.assertFalse(r.available)

    def test_missing_views_do_not_count_toward_history(self):
        rows = [obs(None, src="n1"), obs(None, src="n2")] + [obs(v, src=f"v{i}") for i, v in enumerate([100, 200, 300, 400, 500])]
        self.assertEqual(baseline_from_observations(rows).value, 300)

    def test_creator_isolation(self):
        rows = [obs(v, creator="a", src=f"a{i}") for i, v in enumerate([100, 100, 100, 100, 100])]
        rows += [obs(v, creator="b", src=f"b{i}") for i, v in enumerate([9000, 9000, 9000, 9000, 9000])]
        self.assertEqual(baseline_for(rows, tenant_id="dreemzy", platform="tiktok", creator_id="a").value, 100)
        self.assertEqual(baseline_for(rows, tenant_id="dreemzy", platform="tiktok", creator_id="b").value, 9000)

    def test_platform_isolation(self):
        rows = [obs(v, platform="tiktok", src=f"t{i}") for i, v in enumerate([100, 100, 100, 100, 100])]
        rows += [obs(v, platform="youtube", src=f"y{i}") for i, v in enumerate([5000, 5000, 5000, 5000, 5000])]
        self.assertEqual(baseline_for(rows, tenant_id="dreemzy", platform="youtube", creator_id="c1").value, 5000)

    def test_tenant_isolation(self):
        rows = [obs(v, tenant="other", src=f"o{i}") for i, v in enumerate([1, 1, 1, 1, 1])]
        rows += [obs(v, tenant="dreemzy", src=f"d{i}") for i, v in enumerate([700, 700, 700, 700, 700])]
        self.assertEqual(baseline_for(rows, tenant_id="dreemzy", platform="tiktok", creator_id="c1").value, 700)

    def test_repeated_observation_of_same_source_does_not_duplicate(self):
        rows = [obs(1000, src="same", observed="2026-09-21T09:00:00+00:00"),
                obs(2000, src="same", observed="2026-09-21T11:00:00+00:00")]
        self.assertEqual(len(latest_per_source(rows)), 1)
        self.assertEqual(latest_per_source(rows)[0]["views"], 2000)   # newest wins

    def test_observations_for_selector(self):
        rows = [obs(1, creator="x"), obs(2, creator="y")]
        self.assertEqual(len(observations_for(rows, tenant_id="dreemzy", platform="tiktok", creator_id="x")), 1)


class OutlierTests(unittest.TestCase):
    def test_outlier_multiple_and_labels(self):
        rows = [obs(v, src=f"o{i}") for i, v in enumerate([100, 100, 100, 100, 100])]
        base = baseline_for(rows, tenant_id="dreemzy", platform="tiktok", creator_id="c1")
        self.assertEqual(relative_evidence(1000, base)["outlier_multiple"], 10.0)
        self.assertEqual(outlier_label(1.5), "ordinary")
        self.assertEqual(outlier_label(3), "outlier")
        self.assertEqual(outlier_label(7), "strong_outlier")
        self.assertEqual(outlier_label(12), "breakout")

    def test_missing_baseline_never_invents_relative_evidence(self):
        unknown = baseline_from_observations([obs(100, src="only")])
        rel = relative_evidence(999_999, unknown)
        self.assertEqual(rel["creator_baseline_state"], INSUFFICIENT_HISTORY)
        self.assertIsNone(rel["outlier_multiple"])
        self.assertIsNone(rel["creator_baseline"])
        self.assertEqual(rel["outlier_label"], UNAVAILABLE)

    def test_observation_row_and_record_extraction(self):
        row = observation_row(tenant_id="dreemzy", platform="tiktok", creator_id="c",
                              source_content_id="s", views=5, is_pinned=True)
        self.assertEqual((row["tenant_id"], row["views"], row["is_pinned"]), ("dreemzy", 5, True))

        class _Rec:
            tenant_id = "dreemzy"; platform = "tiktok"; creator_channel_identity = "c"
            platform_content_id = "s9"; publication_timestamp = "2026-09-01T00:00:00+00:00"
            observed_metrics = {"playCount": 123, "diggCount": 4, "collectCount": 2}
            connector_name = "tiktok-apify"; provenance_rights_evidence = "provider"
        r = observations_from_record(_Rec())
        self.assertEqual((r["views"], r["likes"], r["saves"]), (123, 4, 2))


if __name__ == "__main__":
    unittest.main()
