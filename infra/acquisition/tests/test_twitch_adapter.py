"""Twitch adapter: URL contract, classification, shared-lane reuse.

Deterministic: no network, no yt-dlp, no Discord, no Apify.

Run:  python3 -m unittest discover -s infra/acquisition/tests -v
"""

import sys
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
for d in ("acquisition", "discord-review"):
    sys.path.insert(0, str(_REPO / "infra" / d))

import approved_lane as lane  # noqa: E402
import source_adapters as sa  # noqa: E402
import source_window as sw  # noqa: E402
import twitch_url as tu  # noqa: E402
import ytdlp_adapter as ya  # noqa: E402

VOD = "https://www.twitch.tv/videos/1234567890"
CLIP = "https://clips.twitch.tv/AwkwardHelplessSalamander"


class TestUrlContract(unittest.TestCase):
    def test_vod_variants_normalize_to_one_identity(self):
        for url in (VOD, "http://m.twitch.tv/videos/1234567890",
                    "https://www.twitch.tv/videos/1234567890?filter=all&t=1h2m3s",
                    "https://twitch.tv/videos/1234567890/"):
            parsed = tu.classify(url)
            self.assertEqual(parsed["canonical_url"], VOD, url)
            self.assertEqual(parsed["content_id"], "1234567890")
            self.assertEqual(parsed["kind"], "vod")

    def test_clip_variants_normalize_to_one_identity(self):
        for url in (CLIP, "https://www.twitch.tv/somechannel/clip/AwkwardHelplessSalamander",
                    "https://clips.twitch.tv/AwkwardHelplessSalamander/",
                    f"{CLIP}?tt_content=url&tt_medium=clips_api"):
            parsed = tu.classify(url)
            self.assertEqual(parsed["canonical_url"], CLIP, url)
            self.assertEqual(parsed["content_id"], "AwkwardHelplessSalamander")
            self.assertEqual(parsed["kind"], "clip")

    def test_tracking_parameters_never_change_identity(self):
        base = tu.fingerprint_for(VOD)
        self.assertEqual(base, tu.fingerprint_for(VOD + "?filter=all&sort=time"))
        self.assertEqual(base, tu.fingerprint_for(VOD + "?t=3h"))
        self.assertEqual(tu.classify(VOD + "?t=1h2m3s&filter=x")["start_hint_ms"], "3723000")

    def test_rejections_carry_stable_codes(self):
        cases = [
            ("https://www.twitch.tv/somechannel", "CHANNEL_PAGE"),
            ("https://www.twitch.tv/somechannel/videos", "UNSUPPORTED_PATH"),
            ("https://www.twitch.tv/directory/game/Fortnite", "NOT_A_MEDIA_URL"),
            ("https://www.twitch.tv/search?term=fortnite", "NOT_A_MEDIA_URL"),
            ("https://www.twitch.tv/collections/abc123", "NOT_A_MEDIA_URL"),
            ("https://www.twitch.tv/videos/notanid", "BAD_VOD_ID"),
            ("https://www.twitch.tv/videos/123", "BAD_VOD_ID"),
            ("https://clips.twitch.tv/no", "BAD_CLIP_ID"),
            ("https://example.com/videos/1234567890", "FOREIGN_HOST"),
            ("https://user:pw@www.twitch.tv/videos/1234567890", "CREDENTIALS"),
            ("ftp://www.twitch.tv/videos/1234567890", "BAD_SCHEME"),
            ("", "EMPTY_URL"),
        ]
        for url, code in cases:
            with self.assertRaises(tu.TwitchUrlError) as e:
                tu.classify(url)
            self.assertEqual(e.exception.code, code, url)

    def test_source_id_drift_is_refused(self):
        with self.assertRaises(tu.TwitchUrlError) as e:
            tu.parse(VOD, expected_content_id="9999999999")
        self.assertEqual(e.exception.code, "SOURCE_ID_DRIFT")


class TestClipVersusVod(unittest.TestCase):
    def test_clip_and_vod_identities_cannot_collide(self):
        clip_fp = tu.fingerprint_for(CLIP)
        vod_fp = tu.fingerprint_for(VOD)
        self.assertNotEqual(clip_fp, vod_fp)
        # coincidentally equal raw ids across kinds still differ
        self.assertNotEqual(tu.fingerprint_for("https://clips.twitch.tv/1234567890"),
                            tu.fingerprint_for(VOD))

    def test_another_platforms_similar_id_cannot_collide(self):
        self.assertNotEqual(tu.fingerprint_for(VOD),
                            sw.dedup_identity("youtube", "1234567890", None))

    def test_a_whole_clip_needs_no_window_and_a_long_vod_always_does(self):
        short = sw.require_full_item_allowed(platform="twitch", content_id="clipslug",
                                            canonical_url=CLIP, source_duration_s=42.0)
        self.assertEqual(short["mode"], sw.MODE_FULL)
        with self.assertRaises(sw.WindowError) as e:
            sw.require_full_item_allowed(platform="twitch", content_id="1234567890",
                                         canonical_url=VOD, source_duration_s=21_600.0)
        self.assertEqual(e.exception.code, "LONG_FORM_REQUIRES_RANGE")

    def test_multi_hour_vod_accepts_a_bounded_window(self):
        w = sw.validate_window(platform="twitch", canonical_url=VOD, content_id="1234567890",
                               start_ms=7_200_000, end_ms=7_230_000,
                               origin="operator_adjusted", source_duration_s=21_600.0)
        self.assertEqual(w["clip_duration_ms"], 30_000)
        other = sw.validate_window(platform="twitch", canonical_url=VOD, content_id="1234567890",
                                   start_ms=7_230_000, end_ms=7_260_000,
                                   origin="operator_adjusted", source_duration_s=21_600.0)
        self.assertNotEqual(w["range_fingerprint"], other["range_fingerprint"])

    def test_ranged_metadata_accepts_a_six_hour_source(self):
        checked = ya.validate_metadata({"id": "1234567890", "duration": 21_600.0,
                                        "is_live": False}, expected_id="1234567890",
                                       allow_long_source=True)
        self.assertEqual(checked["duration_seconds"], 21_600.0)
        with self.assertRaises(ya.YtDlpError) as e:
            ya.validate_metadata({"id": "1234567890", "duration": 21_600.0},
                                 expected_id="1234567890")   # whole-item path
        self.assertEqual(e.exception.code, "TOO_LONG")


class TestAdapterSurface(unittest.TestCase):
    def test_registered_as_a_local_zero_cost_long_form_adapter(self):
        a = sa.adapter_for("twitch")
        self.assertIsNotNone(a)
        self.assertTrue(a.long_form)
        self.assertFalse(a.runtime_proven, "runtime_proven flips only after a real proof")
        self.assertIn("local", a.adapter_id)
        self.assertEqual(sa.cost_model("twitch").get("provider_cost_micro"), 0)
        self.assertFalse(sa.is_local_free("twitch"),
                         "not 'local free' until the adapter is genuinely proven")

    def test_availability_classification(self):
        a = sa.adapter_for("twitch")
        self.assertEqual(a.classify_availability({"duration": 60}), "AVAILABLE")
        self.assertEqual(a.classify_availability({"is_live": True}), "LIVE_WITHOUT_IMMUTABLE_ID")
        self.assertEqual(a.classify_availability({"availability": "subscriber_only", "duration": 60}),
                         "SUBSCRIBER_ONLY")
        self.assertEqual(a.classify_availability({"availability": "deleted"}), "UNAVAILABLE")
        self.assertEqual(a.classify_availability({"is_private": True}), "PRIVATE")
        self.assertEqual(a.classify_availability({}), "UNAVAILABLE")

    def test_shared_layer_is_platform_neutral(self):
        """No platform BRANCHING in shared code (prose in a docstring is fine)."""
        branching = ('== "twitch"', "== 'twitch'", 'platform == "twitch"',
                     'import twitch_url', '"twitch":', 'if platform == \'twitch\'')
        for name in ("source_window.py", "approved_lane.py", "candidate.py"):
            src = (_REPO / "infra" / "acquisition" / name).read_text(encoding="utf-8")
            for pattern in branching:
                self.assertNotIn(pattern, src,
                                 f"{name} must not branch on a platform name ({pattern})")

    def test_ranged_transport_is_shared_and_platform_neutral(self):
        cmd = ya.build_download_command(VOD, out_template="/tmp/x.%(ext)s",
                                        section_arg=sw.ytdlp_section_arg(
                                            {"start_ms": 7_200_000, "end_ms": 7_230_000}))
        self.assertIn("--download-sections", cmd)
        self.assertEqual(cmd[cmd.index("--download-sections") + 1], "*7200.000-7230.000")
        self.assertIn("--force-keyframes-at-cuts", cmd)
        self.assertIn("--no-playlist", cmd)
        joined = " ".join(cmd).lower()
        for forbidden in ("cookie", "cookies-from-browser", "apify", "username", "password"):
            self.assertNotIn(forbidden, joined)

    def test_filename_convention(self):
        digest = "a" * 64
        self.assertEqual(
            sw.filename_stem("twitch", "1234567890",
                             {"start_ms": 7_200_000, "end_ms": 7_230_000}, digest),
            f"twitch__1234567890__7200-7230__{'a' * 12}")
        self.assertEqual(sw.filename_stem("twitch", "AwkwardHelplessSalamander", None, digest),
                         f"twitch__AwkwardHelplessSalamander__{'a' * 12}")

    def test_duplicate_identity_is_range_scoped(self):
        w = {"start_ms": 7_200_000, "end_ms": 7_230_000,
             "range_fingerprint": "d" * 64}
        self.assertEqual(lane.already_acquired(
            [{"platform_content_id": "1234567890", "duplicate_fingerprint": "d" * 64}],
            content_id="1234567890", window=w), True)
        other = dict(w, start_ms=7_230_000, end_ms=7_260_000, range_fingerprint="e" * 64)
        self.assertFalse(lane.already_acquired(
            [{"platform_content_id": "1234567890", "duplicate_fingerprint": "d" * 64}],
            content_id="1234567890", window=other))


if __name__ == "__main__":
    unittest.main()
