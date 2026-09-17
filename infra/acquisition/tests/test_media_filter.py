"""Deterministic tests for media-candidate filtering (browser-assisted probe).

Run:  python3 -m unittest discover -s infra/acquisition/tests -v
"""

import sys
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO_ROOT / "infra" / "acquisition"))

from media_filter import (  # noqa: E402
    DIRECT_VIDEO, MANIFEST, SEGMENT, AUDIO,
    is_approved_media_host, classify_media_url, is_media_candidate, redact_url,
)


class TestHost(unittest.TestCase):
    def test_approved_hosts(self):
        for h in [
            "scontent-lax3-1.cdninstagram.com",
            "video-lax3-1.cdninstagram.com",
            "instagram.fdtw1-1.fna.fbcdn.net",
            "scontent.cdninstagram.com",
            "media.akamaihd.net",
        ]:
            self.assertTrue(is_approved_media_host(h), h)

    def test_rejects_other_hosts(self):
        for h in ["google.com", "evil.com", "instagram.com.evil.com", "example.net"]:
            self.assertFalse(is_approved_media_host(h), h)


class TestClassify(unittest.TestCase):
    def test_direct_video(self):
        self.assertEqual(classify_media_url("https://x.cdninstagram.com/v/a.mp4"), DIRECT_VIDEO)

    def test_manifest(self):
        self.assertEqual(classify_media_url("https://x.fbcdn.net/v/a.m3u8"), MANIFEST)
        self.assertEqual(classify_media_url("https://x.fbcdn.net/v/a.mpd"), MANIFEST)

    def test_segment(self):
        self.assertEqual(classify_media_url("https://x.cdninstagram.com/v/a.m4s"), SEGMENT)
        self.assertEqual(classify_media_url("https://x.cdninstagram.com/v/a.ts"), SEGMENT)

    def test_audio(self):
        self.assertEqual(classify_media_url("https://x.cdninstagram.com/v/a.m4a"), AUDIO)

    def test_not_media(self):
        self.assertIsNone(classify_media_url("https://x.cdninstagram.com/v/cover.jpg"))


class TestCandidate(unittest.TestCase):
    def test_https_required(self):
        self.assertFalse(is_media_candidate("http://x.cdninstagram.com/v/a.mp4"))

    def test_host_required(self):
        self.assertFalse(is_media_candidate("https://evil.com/a.mp4"))

    def test_media_candidate(self):
        self.assertTrue(is_media_candidate("https://x.cdninstagram.com/v/a.mp4"))

    def test_rejects_image_mime(self):
        self.assertFalse(is_media_candidate("https://x.cdninstagram.com/v/a", "image/jpeg"))

    def test_accepts_video_mime_without_ext(self):
        self.assertTrue(is_media_candidate("https://x.cdninstagram.com/v/a", "video/mp4"))

    def test_rejects_jpg_on_approved_host(self):
        self.assertFalse(is_media_candidate("https://x.cdninstagram.com/v/cover.jpg"))

    def test_rejects_manifest_mime_on_wrong_host(self):
        self.assertFalse(is_media_candidate("https://evil.com/a.m3u8"))


class TestRedact(unittest.TestCase):
    def test_strips_query_and_fragment(self):
        u = "https://x.cdninstagram.com/v/a.mp4?sig=SECRET&exp=123#frag"
        self.assertEqual(redact_url(u), "https://x.cdninstagram.com/v/a.mp4")


if __name__ == "__main__":
    unittest.main(verbosity=2)
