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
)

REEL = "https://www.instagram.com/reel/DdZS1QRhrXE/"
CID = "DdZS1QRhrXE"


def _c(type_, order):
    return {"type": type_, "order": order, "host": "scontent.cdninstagram.com"}


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
        self.assertEqual(classify_navigation("https://www.instagram.com/p/DdZS1QRhrXE/", CID), "same_content")
        self.assertEqual(classify_navigation("https://www.instagram.com/reels/DdZS1QRhrXE/", CID), "same_content")

    def test_unexpected_navigation(self):
        self.assertEqual(classify_navigation("https://www.instagram.com/p/OTHER123/", CID), "different_content")

    def test_content_id_change(self):
        self.assertEqual(classify_navigation("https://www.instagram.com/reel/ZZZ999/", CID), "different_content")

    def test_unknown_url(self):
        self.assertEqual(classify_navigation("https://www.instagram.com/explore/", CID), "unknown")


class TestSelection(unittest.TestCase):
    def test_two_candidates_prefers_direct_video(self):
        # manifest arrived later (higher order) but direct_video wins on type.
        cands = [_c("manifest", 5), _c("direct_video", 1)]
        self.assertEqual(select_best_candidate(cands)["type"], "direct_video")

    def test_same_type_prefers_most_recent(self):
        cands = [_c("direct_video", 1), _c("direct_video", 3)]
        self.assertEqual(select_best_candidate(cands)["order"], 3)

    def test_empty(self):
        self.assertIsNone(select_best_candidate([]))


class TestHandoff(unittest.TestCase):
    def test_already_playing_fast_path_no_reload(self):
        # a direct_video candidate is already present -> immediate handoff
        ok, c, reason = decide_handoff([_c("direct_video", 0)], already_sent=False, timed_out=False)
        self.assertTrue(ok)
        self.assertEqual(reason, "direct_video_found")

    def test_handoff_exactly_once(self):
        cands = [_c("direct_video", 0)]
        self.assertTrue(decide_handoff(cands, already_sent=False)[0])
        self.assertFalse(decide_handoff(cands, already_sent=True)[0])  # second call -> no handoff

    def test_no_candidates(self):
        self.assertEqual(decide_handoff([], already_sent=False)[2], "no_candidates")

    def test_ambiguous_candidates_wait_for_timeout(self):
        ok, c, reason = decide_handoff([_c("manifest", 0)], already_sent=False, timed_out=False)
        self.assertFalse(ok)
        self.assertEqual(reason, "waiting_for_direct_video")
        # at timeout, best-effort handoff of the manifest
        ok2, c2, _ = decide_handoff([_c("manifest", 0)], already_sent=False, timed_out=True)
        self.assertTrue(ok2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
