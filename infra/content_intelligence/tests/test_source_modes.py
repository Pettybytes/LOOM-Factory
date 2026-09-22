"""Cross-platform source modes: decided by source CHARACTERISTICS, not platform."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_SUITE = Path(__file__).resolve().parents[1]
_ROOT = _SUITE.parent
for p in (str(_SUITE), str(_ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

from content_intelligence.moment_locator import (  # noqa: E402
    MODE_LONG_FORM, MODE_SOURCE_AS_MOMENT, locate_moments, promote_zones, source_mode,
    source_mode_for_record,
)
from content_intelligence.pipeline import Pass1Config, Pass1Pipeline  # noqa: E402
from test_pipeline import (_Brief, _FakeProvider, _FakeStore, _FakeTopic,  # noqa: E402
                           _Record, _patched_fetch)


def rec(url, duration=None, asset_kind=None, cid="sm1"):
    r = _Record(platform="youtube", cid=cid)
    r.canonical_url = url
    if duration is not None:
        r.duration_seconds = duration
    if asset_kind is not None:
        r.asset_kind = asset_kind
    return r


def cap(s, t, d=4.0):
    return {"start_s": float(s), "end_s": float(s) + d, "text": t}


class SourceModeClassificationTests(unittest.TestCase):
    """SS8: every required platform/source pairing resolves correctly."""

    CASES = [
        ("YouTube long upload", "https://www.youtube.com/watch?v=abc", 3600, None, MODE_LONG_FORM),
        ("YouTube Short", "https://www.youtube.com/shorts/xyz", 30, "short", MODE_SOURCE_AS_MOMENT),
        ("Twitch VOD", "https://www.twitch.tv/videos/123", 7200, None, MODE_LONG_FORM),
        ("Twitch Clip", "https://clips.twitch.tv/AwkwardHelpless", 20, None, MODE_SOURCE_AS_MOMENT),
        ("Kick VOD", "https://kick.com/video/abc", 5400, None, MODE_LONG_FORM),
        ("Kick Clip", "https://kick.com/chan?clip=clip_123", 25, None, MODE_SOURCE_AS_MOMENT),
        ("TikTok", "https://www.tiktok.com/@u/video/1", 15, None, MODE_SOURCE_AS_MOMENT),
        ("Instagram Reel", "https://www.instagram.com/reel/abc/", 20, None, MODE_SOURCE_AS_MOMENT),
        ("Reddit short video", "https://www.reddit.com/r/x/comments/y/z/", 12, "clip", MODE_SOURCE_AS_MOMENT),
    ]

    def test_required_pairings(self):
        for name, url, dur, kind, expected in self.CASES:
            with self.subTest(name):
                self.assertEqual(source_mode_for_record(rec(url, dur, kind)), expected)

    def test_platform_name_alone_never_decides_the_mode(self):
        """Same characteristics on different platforms must resolve identically."""
        for url in ("https://www.twitch.tv/videos/1", "https://kick.com/video/1",
                    "https://www.youtube.com/watch?v=1"):
            self.assertEqual(source_mode_for_record(rec(url, 3600)), MODE_LONG_FORM)
        for url in ("https://clips.twitch.tv/x", "https://kick.com/c?clip=x",
                    "https://www.youtube.com/shorts/x"):
            self.assertEqual(source_mode_for_record(rec(url, 30)), MODE_SOURCE_AS_MOMENT)

    def test_asset_kind_outranks_duration(self):
        # a long "clip" is still a clip; a short "stream" fragment is still a container
        self.assertEqual(source_mode(600, asset_kind="clip"), MODE_SOURCE_AS_MOMENT)
        self.assertEqual(source_mode(20, asset_kind="stream"), MODE_LONG_FORM)

    def test_explicit_clip_url_and_duration_precedence(self):
        # a clip URL is decisive even for a long asset
        self.assertEqual(source_mode(3600, url="https://www.youtube.com/shorts/abc"),
                         MODE_SOURCE_AS_MOMENT)
        # a short DURATION wins over a container-shaped URL (Shorts use /watch too)
        self.assertEqual(source_mode(20, url="https://www.youtube.com/watch?v=x"),
                         MODE_SOURCE_AS_MOMENT)
        # a container URL decides only when duration is unknown
        self.assertEqual(source_mode(None, url="https://www.twitch.tv/videos/9"),
                         MODE_LONG_FORM)

    def test_unknown_source_is_scanned_not_assumed(self):
        self.assertEqual(source_mode(None), MODE_LONG_FORM)
        self.assertEqual(source_mode_for_record(rec("https://example.invalid/x")), MODE_LONG_FORM)


class LocatorInvocationTests(unittest.TestCase):
    """SS8: containers scan; source-as-moment bypasses the whole-source locator."""

    SCAN = [cap(0, "welcome back."), cap(316, "and then she said something wild."),
            cap(318, "oh my god no way."), cap(1866, "wait what just happened.")]

    def test_container_scans_for_multiple_moments(self):
        zones = locate_moments(self.SCAN, source_id="c1", source_duration=2400.0)
        self.assertGreaterEqual(len(zones), 1)
        for z in zones:
            self.assertNotEqual(z.locator_reason, "short_form_source_is_the_moment")

    def test_source_as_moment_bypasses_the_locator(self):
        zones = locate_moments(self.SCAN[:2], source_id="s1", source_duration=45.0)
        self.assertEqual(len(zones), 1)
        self.assertEqual(zones[0].locator_reason, "short_form_source_is_the_moment")

    def test_asset_kind_drives_locator_behaviour_without_duration(self):
        zones = locate_moments(self.SCAN, source_id="s2", source_duration=2400.0,
                               asset_kind="clip")
        self.assertEqual(zones[0].locator_reason, "short_form_source_is_the_moment")


class CommonContractTests(unittest.TestCase):
    """SS8: BOTH modes feed the SAME normalized moment-quality / FIRE contract."""

    def _run(self, r):
        pipe = Pass1Pipeline(Pass1Config(max_candidates=1, market_shortlist_threshold=0),
                             _Brief(), _FakeTopic(score=85), provider=_FakeProvider())
        from test_bounded_preview import _Recorder
        rec_prev = _Recorder(signals_by_end={3.0: [{"basis": "motion", "start_s": 1.0,
                                                    "end_s": 3.0}]})
        pipe._inspection_preview = rec_prev
        pipe._inspection_analyze = rec_prev.signals
        pipe._inspection_captions = lambda u: ([], "none")
        segs = ([cap(0, "oh my god look at this.", 3.0)]
                if getattr(r, "asset_kind", None) == "clip" else
                [cap(0, "welcome back."), cap(1116, "so a fan walks up."),
                 cap(1118, "uh oh here we go."), cap(1120, "he fell out of the chair.")])
        with _patched_fetch(segs):
            return pipe.run([r], _FakeStore()).candidates[0]

    def test_short_form_and_container_share_the_same_contract(self):
        short = self._run(rec("https://www.tiktok.com/@u/video/1", 30, "clip", cid="short1"))
        longf = self._run(rec("https://www.youtube.com/watch?v=abc", 2400, None, cid="long1"))
        for c, expected_mode in ((short, MODE_SOURCE_AS_MOMENT), (longf, MODE_LONG_FORM)):
            with self.subTest(mode=expected_mode):
                self.assertEqual(c.source_mode, expected_mode)
                # the SAME normalized quality contract is applied to both
                self.assertIsNotNone(c.boundary_plan)
                self.assertIsNotNone(c.completeness)
                self.assertIsNotNone(c.moment_type)
                self.assertIn(c.completeness["completeness_verdict"], ("PASS", "FAIL", "AMBIGUOUS"))
                self.assertIsInstance(c.fire_passed, bool)
                self.assertIn("lead_seconds", c.boundary_plan)


if __name__ == "__main__":
    unittest.main()
