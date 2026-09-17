"""Deterministic tests for browser-assisted acquisition flow decisions.

Run:  python3 -m unittest discover -s infra/acquisition/tests -v
"""

import sys
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO_ROOT / "infra" / "acquisition"))

from browser_flow import (  # noqa: E402
    instagram_content_id, classify_navigation, select_best_candidate, decide_handoff,
    distinct_candidates, build_fmp4_set, persisted_candidate, fragment_role,
    representation_of, parse_range_header,
    INSUFFICIENT_FMP4_SET, MANIFEST, MP4, DIRECT_VIDEO, SEGMENT, AUDIO,
)

REEL = "https://www.instagram.com/reel/DdZS1QRhrXE/"
CID = "DdZS1QRhrXE"
BASE = "https://scontent.cdninstagram.com/v/reel.mp4"
INIT_URL = "https://scontent.cdninstagram.com/v/init.mp4"
ABASE = "https://scontent.cdninstagram.com/v/audio.mp4"
AINIT_URL = "https://scontent.cdninstagram.com/v/ainit.mp4"


def _mp4(url, order, *, range_start=None, range_end=None, range_total=None,
         representation=None, role=None, mime_type=None):
    c = {"url": url, "type": "mp4", "order": order, "host": "scontent.cdninstagram.com",
         "range_start": range_start, "range_end": range_end, "range_total": range_total}
    if representation:
        c["representation"] = representation
    if role:
        c["role"] = role
    if mime_type:
        c["mime_type"] = mime_type
    return c


def _manifest(url, order):
    return {"url": url, "type": "manifest", "order": order, "host": "scontent.cdninstagram.com"}


class TestContentId(unittest.TestCase):
    def test_reel_and_post_share_content_id(self):
        self.assertEqual(instagram_content_id(REEL), CID)
        self.assertEqual(instagram_content_id("https://www.instagram.com/p/DdZS1QRhrXE/"), CID)
        self.assertEqual(instagram_content_id("https://www.instagram.com/other/stuff"), "")

    def test_reels_plural_same_content_id(self):
        self.assertEqual(instagram_content_id("https://www.instagram.com/reels/DdZS1QRhrXE/"), CID)


class TestNavigation(unittest.TestCase):
    def test_expected_same_content_reload(self):
        self.assertEqual(classify_navigation(REEL, CID), "same_content")

    def test_unexpected_navigation(self):
        self.assertEqual(classify_navigation("https://www.instagram.com/p/OTHER123/", CID), "different_content")

    def test_unknown_url(self):
        self.assertEqual(classify_navigation("https://www.instagram.com/explore/", CID), "unknown")


class TestRoles(unittest.TestCase):
    def test_mp4_with_range_is_media_fragment(self):
        self.assertEqual(fragment_role(_mp4(BASE, 0, range_start=0, range_end=999)), "media")

    def test_mp4_without_range_is_init(self):
        self.assertEqual(fragment_role(_mp4(BASE, 0)), "init")

    def test_explicit_role_wins(self):
        self.assertEqual(fragment_role(_mp4(BASE, 0, role="init", range_start=0)), "init")

    def test_audio_representation_from_mime(self):
        self.assertEqual(representation_of(_mp4(BASE, 0, mime_type="audio/mp4")), "audio")
        self.assertEqual(representation_of(_mp4(BASE, 0, mime_type="video/mp4")), "video")

    def test_parse_range_header(self):
        self.assertEqual(parse_range_header("bytes=0-999"), (0, 999))
        self.assertEqual(parse_range_header("bytes=1000-"), (1000, None))
        self.assertIsNone(parse_range_header("items=0-999"))


class TestSelection(unittest.TestCase):
    def test_manifest_preferred(self):
        cands = [_mp4(BASE, 1), _manifest("https://x/m.mpd", 5)]
        self.assertEqual(select_best_candidate(cands)["type"], "manifest")

    def test_empty(self):
        self.assertIsNone(select_best_candidate([]))


class TestDistinctCandidates(unittest.TestCase):
    def test_same_base_url_distinct_ranges_stay_distinct(self):
        c1 = _mp4(BASE + "?sig=A", 0, range_start=0, range_end=999, range_total=5000)
        c2 = _mp4(BASE + "?sig=B", 1, range_start=1000, range_end=1999, range_total=5000)
        out = distinct_candidates([c1, c2])
        self.assertEqual(len(out), 2)

    def test_same_base_url_same_range_deduped(self):
        c1 = _mp4(BASE + "?sig=A", 0, range_start=0, range_end=999)
        c2 = _mp4(BASE + "?sig=B", 1, range_start=0, range_end=999)
        self.assertEqual(len(distinct_candidates([c1, c2])), 1)


class TestHandoff(unittest.TestCase):
    def test_manifest_exits_early(self):
        ok, payload, reason = decide_handoff([_manifest("https://x/m.mpd", 0)],
                                             already_sent=False, timed_out=False)
        self.assertTrue(ok)
        self.assertEqual(reason, "manifest_found")
        self.assertIn("manifest_url", payload)

    def test_one_moof_never_triggers_early_handoff(self):
        # a single ambiguous .mp4 candidate does NOT hand off before timeout
        ok, payload, reason = decide_handoff([_mp4(BASE, 0, range_start=0, range_end=999)],
                                             already_sent=False, timed_out=False)
        self.assertFalse(ok)
        self.assertEqual(reason, "collecting")

    def test_handoff_exactly_once(self):
        ok, _, _ = decide_handoff([_manifest("https://x/m.mpd", 0)], already_sent=False)
        self.assertTrue(ok)
        ok2, _, _ = decide_handoff([_manifest("https://x/m.mpd", 0)], already_sent=True)
        self.assertFalse(ok2)

    def test_no_candidates(self):
        self.assertEqual(decide_handoff([], already_sent=False)[2], "no_candidates")


class TestBuildFmp4Set(unittest.TestCase):
    def _complete_set(self):
        return [
            _mp4(INIT_URL, 0, role="init", mime_type="video/mp4"),
            _mp4(BASE + "?s=A", 1, range_start=0, range_end=999, range_total=5000, mime_type="video/mp4"),
            _mp4(BASE + "?s=B", 2, range_start=1000, range_end=1999, range_total=5000, mime_type="video/mp4"),
            _mp4(AINIT_URL, 3, role="init", mime_type="audio/mp4"),
            _mp4(ABASE + "?s=A", 4, range_start=0, range_end=499, range_total=2000, mime_type="audio/mp4"),
        ]

    def test_complete_init_and_ordered_video_audio_creates_one_handoff(self):
        cands = self._complete_set()
        ok, payload, reason = decide_handoff(cands, already_sent=False, timed_out=True)
        self.assertTrue(ok)
        self.assertEqual(reason, "assemblable_fmp4_set")
        self.assertEqual(payload["init_url"], INIT_URL)
        self.assertEqual(len(payload["segments"]), 2)
        self.assertTrue(payload["has_audio"])
        self.assertEqual(payload["audio_init_url"], AINIT_URL)

    def test_out_of_order_ranges_are_reordered_to_byte_order(self):
        cands = [
            _mp4(INIT_URL, 0, role="init", mime_type="video/mp4"),
            _mp4(BASE + "?s=B", 1, range_start=1000, range_end=1999, range_total=5000, mime_type="video/mp4"),
            _mp4(BASE + "?s=A", 2, range_start=0, range_end=999, range_total=5000, mime_type="video/mp4"),
        ]
        ok, payload, _ = decide_handoff(cands, already_sent=False, timed_out=True)
        self.assertTrue(ok)
        self.assertEqual(payload["segments"], [BASE + "?s=A", BASE + "?s=B"])

    def test_missing_range_fails_closed(self):
        # gap between ranges -> insufficient
        cands = [
            _mp4(INIT_URL, 0, role="init", mime_type="video/mp4"),
            _mp4(BASE + "?s=A", 1, range_start=0, range_end=999, range_total=5000, mime_type="video/mp4"),
            _mp4(BASE + "?s=C", 2, range_start=2000, range_end=2999, range_total=5000, mime_type="video/mp4"),
        ]
        ok, payload, reason = decide_handoff(cands, already_sent=False, timed_out=True)
        self.assertFalse(ok)
        self.assertEqual(reason, INSUFFICIENT_FMP4_SET)

    def test_overlapping_ranges_fail_closed(self):
        cands = [
            _mp4(INIT_URL, 0, role="init", mime_type="video/mp4"),
            _mp4(BASE + "?s=A", 1, range_start=0, range_end=999, range_total=5000, mime_type="video/mp4"),
            _mp4(BASE + "?s=B", 2, range_start=500, range_end=1499, range_total=5000, mime_type="video/mp4"),
        ]
        ok, payload, reason = decide_handoff(cands, already_sent=False, timed_out=True)
        self.assertFalse(ok)
        self.assertEqual(reason, INSUFFICIENT_FMP4_SET)

    def test_video_without_init_fails_closed(self):
        cands = [_mp4(BASE + "?s=A", 0, range_start=0, range_end=999, range_total=5000, mime_type="video/mp4")]
        ok, _, reason = decide_handoff(cands, already_sent=False, timed_out=True)
        # no manifest, no init -> single candidate falls back to signature check
        # (helper classifies lone moof as FRAGMENTED_MP4). build_fmp4_set alone is insufficient.
        _, err = build_fmp4_set(cands)
        self.assertEqual(err, INSUFFICIENT_FMP4_SET)

    def test_mixed_representations_fail_closed(self):
        # two video fragments from different base URLs -> mixed representations
        cands = [
            _mp4(INIT_URL, 0, role="init", mime_type="video/mp4"),
            _mp4(BASE + "?s=A", 1, range_start=0, range_end=999, range_total=5000, mime_type="video/mp4"),
            _mp4("https://other.cdninstagram.com/v/other.mp4", 2, range_start=1000, range_end=1999, range_total=5000, mime_type="video/mp4"),
        ]
        _, err = build_fmp4_set(cands)
        self.assertEqual(err, INSUFFICIENT_FMP4_SET)

    def test_incomplete_audio_fails_closed(self):
        # audio evidence (an audio init) present but no audio segments -> insufficient
        cands = [
            _mp4(INIT_URL, 0, role="init", mime_type="video/mp4"),
            _mp4(BASE + "?s=A", 1, range_start=0, range_end=999, range_total=5000, mime_type="video/mp4"),
            _mp4(AINIT_URL, 2, role="init", mime_type="audio/mp4"),
        ]
        _, err = build_fmp4_set(cands)
        self.assertEqual(err, INSUFFICIENT_FMP4_SET)


class TestPersistedCandidate(unittest.TestCase):
    def test_signed_url_and_sensitive_headers_never_persisted(self):
        raw = {
            "url": "https://scontent.cdninstagram.com/v/a.mp4?sig=SECRET&exp=123",
            "fullUrl": "https://scontent.cdninstagram.com/v/a.mp4?sig=SECRET&exp=123",
            "host": "scontent.cdninstagram.com",
            "type": "mp4",
            "order": 0,
            "range": "bytes=0-999",
            "range_start": 0,
            "range_end": 999,
            "range_total": 5000,
            "cookies": "session=abc",
            "authorization": "Bearer xyz",
            "headers": {"Range": "bytes=0-999", "Cookie": "session=abc"},
        }
        p = persisted_candidate(raw)
        for forbidden in ("url", "fullUrl", "cookies", "cookie", "authorization",
                          "auth", "headers", "requestHeaders", "responseHeaders"):
            self.assertNotIn(forbidden, p)
        # signed query string must not appear anywhere in the persisted blob
        self.assertNotIn("sig=SECRET", str(p))
        self.assertNotIn("session=abc", str(p))
        # redacted URL is origin + path only
        self.assertEqual(p["redacted"], "https://scontent.cdninstagram.com/v/a.mp4")
        # range metadata IS preserved (needed for ordering/validation)
        self.assertEqual(p["range_start"], 0)
        self.assertEqual(p["range_end"], 999)
        self.assertEqual(p["range_total"], 5000)


if __name__ == "__main__":
    unittest.main(verbosity=2)
