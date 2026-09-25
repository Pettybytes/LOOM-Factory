"""Platform normalization + the platform-dispatched market gate."""

from __future__ import annotations

import datetime as _dt
import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]          # infra/ so the package imports
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from content_intelligence.platform_metrics import (  # noqa: E402
    INSUFFICIENT_MARKET_EVIDENCE, LOW_SCORE, LOW_SCORE_VELOCITY, LOW_VIEWS,
    STALE, normalize_market_evidence, platform_market_gate,
)

NOW = _dt.datetime(2026, 9, 21, 12, 0, tzinfo=_dt.timezone.utc)


def _rec(platform, metrics, hours=30.0, creator="creator-1"):
    return {"platform": platform, "observed_metrics": metrics, "creator_channel_identity": creator,
            "provenance_rights_evidence": "provider metadata",
            "publication_timestamp": (NOW - _dt.timedelta(hours=hours)).isoformat()}


class NormalizationTests(unittest.TestCase):
    def test_tiktok_aliases(self):
        ev = normalize_market_evidence(_rec("tiktok", {"playCount": 5000, "diggCount": 400,
                                                       "commentCount": 30, "shareCount": 10,
                                                       "collectCount": 7}), now=NOW)
        self.assertEqual((ev.views, ev.likes, ev.comments, ev.shares, ev.saves), (5000, 400, 30, 10, 7))
        self.assertIsNotNone(ev.vph)
        self.assertIsNotNone(ev.engagement)

    def test_youtube_aliases_and_subscribers(self):
        ev = normalize_market_evidence(_rec("youtube", {"viewCount": 12000, "likeCount": 900,
                                                        "commentCount": 120, "subscriberCount": 50_000}), now=NOW)
        self.assertEqual(ev.views, 12000)
        self.assertEqual(ev.subscribers, 50_000)

    def test_instagram_aliases(self):
        ev = normalize_market_evidence(_rec("instagram", {"videoPlayCount": 8000, "likesCount": 700,
                                                          "commentsCount": 40}), now=NOW)
        self.assertEqual((ev.views, ev.likes, ev.comments), (8000, 700, 40))

    def test_twitch_native_fields_preserved(self):
        ev = normalize_market_evidence(_rec("twitch", {"view_count": 4000, "game_name": "Just Chatting",
                                                       "duration": 42, "thumbnail_url": "https://x/y.jpg"}), now=NOW)
        self.assertEqual(ev.views, 4000)
        self.assertEqual(ev.native["twitch_category"], "Just Chatting")
        self.assertEqual(ev.native["twitch_duration"], 42)

    def test_twitch_official_category_id_is_not_dropped(self):
        ev=normalize_market_evidence(_rec('twitch', {'game_id':'509658',
                    'thumbnail_url':'https://example.invalid/clip.jpg'}), now=NOW)
        self.assertEqual(ev.native['twitch_category'], '509658')
        self.assertEqual(ev.native['twitch_thumbnail'], 'https://example.invalid/clip.jpg')

    def test_kick_keeps_only_available_fields(self):
        ev = normalize_market_evidence(_rec("kick", {"view_count": 1500, "category": "Gaming"}), now=NOW)
        self.assertEqual(ev.views, 1500)
        self.assertIsNone(ev.likes)                       # not fabricated
        self.assertEqual(ev.native.get("kick_category"), "Gaming")

    def test_reddit_uses_score_not_views(self):
        ev = normalize_market_evidence(_rec("reddit", {"score": 900, "num_comments": 120}), now=NOW)
        self.assertIsNone(ev.views)                       # never invented
        self.assertEqual(ev.native["reddit_score"], 900)
        self.assertIsNotNone(ev.native["reddit_score_per_hour"])

    def test_missing_metrics_stay_missing(self):
        ev = normalize_market_evidence(_rec("tiktok", {}), now=NOW)
        for field in ("views", "likes", "comments", "shares", "saves", "vph"):
            self.assertIsNone(getattr(ev, field), field)

    def test_outlier_multiple_uses_creator_baseline(self):
        ev = normalize_market_evidence(_rec("tiktok", {"playCount": 6000}), creator_baseline=2000, now=NOW)
        self.assertEqual(ev.outlier_multiple, 3.0)


class PlatformGateTests(unittest.TestCase):
    def test_eighty_four_view_tiktok_is_rejected_low_views(self):
        ev = normalize_market_evidence(_rec("tiktok", {"playCount": 84, "diggCount": 2}), now=NOW)
        d = platform_market_gate(ev, now=NOW)
        self.assertFalse(d.passed)
        self.assertIn(LOW_VIEWS, d.reasons)

    def test_strong_tiktok_passes(self):
        ev = normalize_market_evidence(_rec("tiktok", {"playCount": 900_000, "diggCount": 80_000,
                                                       "commentCount": 4_000, "shareCount": 9_000},
                                            hours=48), now=NOW)
        self.assertTrue(platform_market_gate(ev, now=NOW).passed)

    def test_stale_candidate_rejected(self):
        ev = normalize_market_evidence(_rec("tiktok", {"playCount": 900_000}, hours=24 * 40), now=NOW)
        d = platform_market_gate(ev, now=NOW)
        self.assertFalse(d.passed)
        self.assertIn(STALE, d.reasons)

    def test_missing_views_fails_closed_as_insufficient_evidence(self):
        ev = normalize_market_evidence(_rec("tiktok", {}), now=NOW)
        d = platform_market_gate(ev, now=NOW)
        self.assertFalse(d.passed)
        self.assertIn(INSUFFICIENT_MARKET_EVIDENCE, d.reasons)

    def test_reddit_not_judged_by_view_floors(self):
        """Reddit has no views; a strong score must still pass."""
        ev = normalize_market_evidence(_rec("reddit", {"score": 4_000, "num_comments": 900}, hours=6), now=NOW)
        d = platform_market_gate(ev, now=NOW)
        self.assertTrue(d.passed, d.reasons)

    def test_reddit_low_score_rejected(self):
        ev = normalize_market_evidence(_rec("reddit", {"score": 12, "num_comments": 1}, hours=6), now=NOW)
        d = platform_market_gate(ev, now=NOW)
        self.assertFalse(d.passed)
        self.assertIn(LOW_SCORE, d.reasons)

    def test_reddit_subreddit_meta_post_suppressed(self):
        """sticky / rules / admin meta posts are never the moment."""
        ev = normalize_market_evidence(_rec("reddit", {"score": 5_000, "num_comments": 900,
                                                       "is_meta": True}, hours=6), now=NOW)
        d = platform_market_gate(ev, now=NOW)
        self.assertFalse(d.passed)
        self.assertIn("SUBREDDIT_META", d.reasons)


if __name__ == "__main__":
    unittest.main()
