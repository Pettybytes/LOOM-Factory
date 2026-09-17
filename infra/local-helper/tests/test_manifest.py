"""Deterministic tests for DASH MPD + HLS M3U8 manifest parsing.

Run:  python3 -m unittest discover -s infra/local-helper/tests -v
"""

import sys
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO_ROOT / "infra" / "local-helper"))

from manifest import detect_manifest_type, parse_m3u8, parse_mpd  # noqa: E402

BASE = "https://scontent.cdninstagram.com/v/"

MPD = """<?xml version="1.0"?>
<MPD xmlns="urn:mpeg:dash:schema:mpd:2011" type="static" mediaPresentationDuration="PT10S">
  <Period>
    <AdaptationSet mimeType="video/mp4" contentType="video">
      <SegmentTemplate initialization="init-video.mp4" media="seg-video-$Number$.m4s" startNumber="1" timescale="1000">
        <SegmentTimeline><S d="2000" r="4"/></SegmentTimeline>
      </SegmentTemplate>
    </AdaptationSet>
    <AdaptationSet mimeType="audio/mp4" contentType="audio">
      <SegmentTemplate initialization="init-audio.mp4" media="seg-audio-$Number$.m4s" startNumber="1">
        <SegmentTimeline><S d="2000" r="4"/></SegmentTimeline>
      </SegmentTemplate>
    </AdaptationSet>
  </Period>
</MPD>"""

M3U8 = """#EXTM3U
#EXT-X-VERSION:7
#EXT-X-TARGETDURATION:6
#EXT-X-MEDIA-SEQUENCE:0
#EXT-X-MAP:URI="init.mp4"
#EXTINF:6.0,
seg0.m4s
#EXTINF:6.0,
seg1.m4s
#EXT-X-ENDLIST
"""

M3U8_AUDIO = """#EXTM3U
#EXT-X-VERSION:7
#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="aac",NAME="audio",URI="audio.m3u8"
#EXT-X-MAP:URI="init.mp4"
#EXTINF:6.0,
seg0.m4s
#EXT-X-ENDLIST
"""


class TestDetect(unittest.TestCase):
    def test_mpd(self):
        self.assertEqual(detect_manifest_type(MPD), "mpd")

    def test_m3u8(self):
        self.assertEqual(detect_manifest_type(M3U8), "m3u8")

    def test_none(self):
        self.assertIsNone(detect_manifest_type(b"not a manifest"))


class TestM3U8(unittest.TestCase):
    def test_init_and_segments(self):
        m = parse_m3u8(M3U8, BASE)
        self.assertEqual(m["init_url"], BASE + "init.mp4")
        self.assertEqual(m["segments"], [BASE + "seg0.m4s", BASE + "seg1.m4s"])
        self.assertFalse(m["has_audio"])

    def test_audio_playlist_detected(self):
        m = parse_m3u8(M3U8_AUDIO, BASE)
        self.assertEqual(m["audio_playlist_url"], BASE + "audio.m3u8")
        self.assertTrue(m["has_audio"])


class TestMPD(unittest.TestCase):
    def test_video_and_audio(self):
        m = parse_mpd(MPD, BASE)
        self.assertEqual(m["video"]["init_url"], BASE + "init-video.mp4")
        self.assertEqual(len(m["video"]["segments"]), 5)  # r=4 -> 5 segments
        self.assertTrue(m["video"]["segments"][0].endswith("seg-video-1.m4s"))
        self.assertEqual(m["audio"]["init_url"], BASE + "init-audio.mp4")
        self.assertEqual(len(m["audio"]["segments"]), 5)
        self.assertTrue(m["has_audio"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
