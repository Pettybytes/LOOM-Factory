"""Required regression: an internal media URL must never be the user-facing source.

The failed acceptance exposed a Kick clip whose card linked a ``playlist.m3u8`` —
clicking it downloaded media instead of opening the platform page.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from content_intelligence.canonical_url import (  # noqa: E402
    assert_canonical_source, canonical_source_url, is_media_url,
)

KICK_CLIP_PAGE = "https://kick.com/adinross?clip=clip_01M336WPW5CNDSNE1YXQJDBXTV"


class CanonicalUrlTests(unittest.TestCase):
    def test_kick_playlist_m3u8_is_not_a_source(self):
        u = "https://clips.kick.com/clips/d7/clip_x/playlist.m3u8"
        self.assertTrue(is_media_url(u))
        self.assertEqual(canonical_source_url(u, fallback=KICK_CLIP_PAGE), KICK_CLIP_PAGE)

    def test_cdn_and_signed_media_are_not_sources(self):
        for u in ("https://scontent-arn2-1.cdninstagram.com/v/t51/x.jpg",
                  "https://v16-webapp.tiktokcdn.com/x/y.mp4",
                  "https://googlevideo.com/videoplayback?x=1",
                  "https://v.redd.it/abc/HLSPlaylist.m3u8"):
            with self.subTest(u=u):
                self.assertTrue(is_media_url(u))
                self.assertNotEqual(canonical_source_url(u, fallback="https://example.com/p"), u)

    def test_canonical_platform_pages_pass_through_untouched(self):
        for u in ("https://www.youtube.com/watch?v=abc",
                  "https://www.youtube.com/shorts/xyz",
                  "https://www.tiktok.com/@u/video/1",
                  "https://www.instagram.com/reel/abc/",
                  "https://www.twitch.tv/adinross/clip/SlugName",
                  KICK_CLIP_PAGE,
                  "https://www.reddit.com/r/x/comments/y/z/"):
            with self.subTest(u=u):
                self.assertFalse(is_media_url(u))
                self.assertEqual(canonical_source_url(u), u)

    def test_guard_raises_on_media_url(self):
        with self.assertRaises(ValueError):
            assert_canonical_source("https://clips.kick.com/clips/d7/x/playlist.m3u8")
        assert_canonical_source(KICK_CLIP_PAGE)   # must not raise

    def test_empty_media_url_uses_fallback(self):
        self.assertEqual(canonical_source_url("", fallback=KICK_CLIP_PAGE), KICK_CLIP_PAGE)
        self.assertEqual(canonical_source_url("https://x/y/playlist.m3u8", fallback=""), "")


if __name__ == "__main__":
    unittest.main()
