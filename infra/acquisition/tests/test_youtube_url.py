"""YouTube exact-URL contract tests (supported forms, rejections, identity).

Run:  python3 -m unittest discover -s infra/acquisition/tests -v
"""

import sys
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
for d in ("acquisition", "discord-review"):
    sys.path.insert(0, str(_REPO_ROOT / "infra" / d))

import youtube_url as yt  # noqa: E402

VID = "dQw4w9WgXcQ"
OTHER = "abcdefghijk"
CANONICAL = f"https://www.youtube.com/watch?v={VID}"


class TestSupportedForms(unittest.TestCase):
    def test_all_supported_forms_collapse_to_one_canonical_url(self):
        forms = [
            f"https://www.youtube.com/watch?v={VID}",
            f"https://youtube.com/watch?v={VID}",
            f"https://m.youtube.com/watch?v={VID}",
            f"https://youtu.be/{VID}",
            f"https://www.youtu.be/{VID}",
            f"https://www.youtube.com/shorts/{VID}",
            f"http://www.youtube.com/watch?v={VID}",
            f"https://www.youtube.com/watch?v={VID}&t=42s",
            f"https://youtu.be/{VID}?si=shareToken&utm_source=copy",
            f"https://www.youtube.com/watch?v={VID}#t=1m30s",
        ]
        for form in forms:
            parsed = yt.parse(form)
            self.assertEqual(parsed["canonical_url"], CANONICAL, form)
            self.assertEqual(parsed["video_id"], VID, form)
            self.assertEqual(parsed["fingerprint"], yt.fingerprint_for(CANONICAL), form)
            self.assertEqual(parsed["platform"], "youtube", form)

    def test_tracking_parameters_do_not_change_identity(self):
        a = yt.parse(f"https://www.youtube.com/watch?v={VID}")
        b = yt.parse(f"https://www.youtube.com/watch?v={VID}"
                     "&si=abc&feature=share&utm_campaign=x&fbclid=z&ab_channel=name")
        self.assertEqual(a["canonical_url"], b["canonical_url"])
        self.assertEqual(a["fingerprint"], b["fingerprint"])

    def test_source_form_is_reported_but_never_changes_identity(self):
        self.assertEqual(yt.parse(f"https://youtu.be/{VID}")["source_form"], "short_link")
        self.assertEqual(yt.parse(f"https://www.youtube.com/shorts/{VID}")["source_form"],
                         "shorts")
        self.assertEqual(yt.parse(f"https://www.youtube.com/watch?v={VID}")["source_form"],
                         "watch")

    def test_fingerprint_matches_the_manual_intake_construction(self):
        """Cross-lane identity: both lanes must hash the same canonical URL."""
        import manual_intake
        parsed = yt.parse(f"https://youtu.be/{VID}?si=x")
        self.assertEqual(
            parsed["fingerprint"],
            manual_intake.submission_fingerprint("youtube", parsed["canonical_url"]))


class TestRejections(unittest.TestCase):
    def _reject(self, url, code):
        with self.assertRaises(yt.YouTubeUrlError) as ctx:
            yt.parse(url)
        self.assertEqual(ctx.exception.code, code, f"{url} -> {ctx.exception.code}")

    def test_playslists_mixes_and_selection_parameters(self):
        self._reject("https://www.youtube.com/playlist?list=PL1234567890", "UNSUPPORTED_PATH")
        self._reject(f"https://www.youtube.com/watch?v={VID}&list=PL123", "SELECTION_PARAM")
        self._reject(f"https://www.youtube.com/watch?v={VID}&list=RD{VID}&start_radio=1",
                     "SELECTION_PARAM")
        self._reject(f"https://www.youtube.com/watch?v={VID}&index=2", "SELECTION_PARAM")
        self._reject(f"https://youtu.be/{VID}?list=PL123", "SELECTION_PARAM")

    def test_channels_profiles_feeds_searches(self):
        for url in ("https://www.youtube.com/channel/UCabc",
                    "https://www.youtube.com/c/SomeOne",
                    "https://www.youtube.com/user/SomeOne",
                    "https://www.youtube.com/@SomeOne",
                    "https://www.youtube.com/results?search_query=thing",
                    "https://www.youtube.com/feed/trending",
                    "https://www.youtube.com/gaming"):
            with self.assertRaises(yt.YouTubeUrlError):
                yt.parse(url)

    def test_live_upcoming_and_unsupported_paths(self):
        self._reject(f"https://www.youtube.com/live/{VID}", "UNSUPPORTED_PATH")
        self._reject(f"https://www.youtube.com/embed/{VID}", "UNSUPPORTED_PATH")
        self._reject(f"https://www.youtube.com/v/{VID}", "UNSUPPORTED_PATH")

    def test_malformed_ids(self):
        for bad in ("short", "waytoolongvideoid", "bad!chars@@@"):
            self._reject(f"https://www.youtube.com/watch?v={bad}", "MALFORMED_ID")
        # a blank ?v= is absent, not malformed
        self._reject("https://www.youtube.com/watch?v=", "MISSING_VIDEO_ID")
        self._reject("https://www.youtube.com/shorts/", "MALFORMED_ID")
        self._reject("https://www.youtube.com/watch", "MISSING_VIDEO_ID")
        self._reject("https://www.youtube.com/", "MISSING_VIDEO_ID")

    def test_foreign_hosts_and_schemes(self):
        self._reject(f"https://vimeo.com/{VID}", "UNSUPPORTED_HOST")
        self._reject(f"https://youtube.evil.com/watch?v={VID}", "UNSUPPORTED_HOST")
        self._reject(f"https://youtube.com.evil.com/watch?v={VID}", "UNSUPPORTED_HOST")
        self._reject(f"ftp://www.youtube.com/watch?v={VID}", "BAD_SCHEME")
        self._reject("", "EMPTY_URL")

    def test_credentials(self):
        self._reject(f"https://user:pass@www.youtube.com/watch?v={VID}", "CREDENTIALS")

    def test_multiple_videos_and_id_drift(self):
        self._reject(f"https://www.youtube.com/watch?v={VID}&v={OTHER}", "MULTIPLE_VIDEOS")
        self._reject(f"https://www.youtube.com/shorts/{VID}?v={OTHER}", "CONTENT_ID_DRIFT")
        self._reject("https://youtu.be/" + VID + "/" + OTHER, "AMBIGUOUS_PATH")

    def test_try_parse_is_non_raising(self):
        self.assertIsNone(yt.try_parse("https://www.youtube.com/playlist?list=X"))
        self.assertIsNotNone(yt.try_parse(CANONICAL))
        self.assertTrue(yt.is_youtube_url(f"https://youtu.be/{VID}"))
        self.assertFalse(yt.is_youtube_url("https://vimeo.com/1"))


if __name__ == "__main__":
    unittest.main()
