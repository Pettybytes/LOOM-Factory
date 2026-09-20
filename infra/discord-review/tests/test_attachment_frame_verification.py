"""End-to-end frame-level verification of rendered review cards & attachments.

Verifies:
  - Output is strictly fixed 9:16 (720x1280).
  - Reserved black header band at the top (row y=4 pure black).
  - Header text rendered strictly within the band (y < band_h).
  - Hook text rendered below the band (y >= band_h).
  - Video covers full canvas width with NO black side bars.

Includes:
  1. Deterministic offline tests using synthesized video frames.
  2. Optional live Discord attachment test (gated by live credentials and explicit opt-in).
"""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from PIL import Image

try:
    import adapter
except ImportError:
    adapter = None

BAND_PCT = 18
SRC_W, SRC_H = 720, 1280
HDR_C = (0, 229, 255)  # Cyan
HOOK_C = (255, 214, 0) # Yellow


def _probe_dims(path: str):
    out = subprocess.check_output([
        "ffprobe", "-v", "error", "-show_entries", "stream=width,height",
        "-of", "csv=s=x:p=0", path
    ], timeout=60).decode().strip().splitlines()[0]
    w, h = out.split("x")
    return int(w), int(h)


def _extract_frame(video_path: str, out_png: str):
    subprocess.run([
        "ffmpeg", "-y", "-i", video_path, "-vframes", "1", out_png
    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return Image.open(out_png).convert("RGB")


def analyze_composition(img: Image.Image, hdr_color=HDR_C, hook_color=HOOK_C, tol=60):
    band_h = int(round(img.height * (BAND_PCT / 100.0)))
    mid_y = band_h + (img.height - band_h) // 2

    def is_black(rgb):
        return all(c < 45 for c in rgb)

    def color_pixels(want):
        return [(x, y) for y in range(0, img.height) for x in range(0, img.width)
                if all(abs(img.getpixel((x, y))[i] - want[i]) <= tol for i in range(3))]

    top_band_black = all(is_black(img.getpixel((x, 4))) for x in range(0, img.width, 40))

    hdr_matches = color_pixels(hdr_color)
    hdr_in_band = [p for p in hdr_matches if p[1] < band_h]
    hdr_below_band = [p for p in hdr_matches if p[1] >= band_h]

    hook_matches = color_pixels(hook_color)
    hook_below_band = [p for p in hook_matches if p[1] >= band_h]

    left_edge_video = not is_black(img.getpixel((2, mid_y)))
    right_edge_video = not is_black(img.getpixel((img.width - 3, mid_y)))

    return {
        "width": img.width,
        "height": img.height,
        "top_band_black": top_band_black,
        "hdr_in_band": len(hdr_in_band),
        "hdr_below_band": len(hdr_below_band),
        "hook_below_band": len(hook_below_band),
        "left_edge_video": left_edge_video,
        "right_edge_video": right_edge_video,
        "no_side_bars": left_edge_video and right_edge_video,
    }


class TestAttachmentCompositionOffline(unittest.TestCase):
    """Deterministic offline test validating the frame analysis contract."""

    def test_composition_analyzer_contract(self):
        # Create a synthetic 720x1280 frame matching the composition contract
        img = Image.new("RGB", (SRC_W, SRC_H), (100, 100, 100)) # Grey video
        band_h = int(round(SRC_H * (BAND_PCT / 100.0)))

        # Draw black band at top
        for y in range(band_h):
            for x in range(SRC_W):
                img.putpixel((x, y), (0, 0, 0))

        # Draw cyan header inside band
        for y in range(80, 100):
            for x in range(300, 320):
                img.putpixel((x, y), HDR_C)

        # Draw yellow hook below band
        for y in range(300, 320):
            for x in range(250, 270):
                img.putpixel((x, y), HOOK_C)

        metrics = analyze_composition(img)
        self.assertEqual(metrics["width"], 720)
        self.assertEqual(metrics["height"], 1280)
        self.assertTrue(metrics["top_band_black"])
        self.assertGreater(metrics["hdr_in_band"], 0)
        self.assertEqual(metrics["hdr_below_band"], 0)
        self.assertGreater(metrics["hook_below_band"], 0)
        self.assertTrue(metrics["no_side_bars"])


class TestDiscordAttachmentLive(unittest.TestCase):
    """Gated live test verifying the real Discord attachment if credentials are present."""

    def test_verified_attachment_pixels(self):
        bot_token = os.environ.get("DISCORD_BOT_TOKEN") or getattr(adapter, "DISCORD_BOT_TOKEN", None)
        if not bot_token:
            self.skipTest("Live DISCORD_BOT_TOKEN not configured; skipping live attachment fetch")

        channel_id = os.environ.get("LOOM_DISCORD_REVIEW_CHANNEL_ID", "1546266875225440397")
        message_id = os.environ.get("LOOM_TEST_VERIFY_MESSAGE_ID", "1551026351908069376")

        try:
            import urllib.request
            _status, msg = adapter.http_json("GET", f"{adapter.DISCORD_API}/channels/{channel_id}/messages/{message_id}", bot_token)
            attachments = msg.get("attachments") or []
            if not attachments:
                self.skipTest(f"No attachments found on Discord message {message_id}")
            url = attachments[0]["url"]
        except Exception as e:
            self.skipTest(f"Discord API unreachable or message lookup failed: {e}")

        with tempfile.TemporaryDirectory() as tmpdir:
            mp4_path = os.path.join(tmpdir, "attach.mp4")
            png_path = os.path.join(tmpdir, "frame.png")

            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as resp, open(mp4_path, "wb") as f:
                f.write(resp.read())

            w, h = _probe_dims(mp4_path)
            self.assertEqual((w, h), (720, 1280), "Delivered Discord video must be 720x1280")

            img = _extract_frame(mp4_path, png_path)
            metrics = analyze_composition(img)

            self.assertTrue(metrics["top_band_black"], "Top band must be black")
            self.assertGreater(metrics["hdr_in_band"], 50, "Header cyan text must be present in band")
            self.assertEqual(metrics["hdr_below_band"], 0, "Header text must not overflow band")
            self.assertGreater(metrics["hook_below_band"], 100, "Hook text must be present below band")
            self.assertTrue(metrics["no_side_bars"], "Video must cover full canvas width with no side bars")


if __name__ == "__main__":
    unittest.main()
