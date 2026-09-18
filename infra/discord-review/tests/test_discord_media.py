"""Media attach / truthful fallback tests (mocked HTTP + mocked ffmpeg).

Run:  python3 -m unittest discover -s infra/discord-review/tests -v

No network, no real upload, no transcoding. The upload path is stubbed so the
decisions — attach the video, or attach the poster and state the real reason —
are verified without touching Discord.
"""

import sys
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

_REPO_ROOT = Path(__file__).resolve().parents[3]
for d in ("discord-review", "acquisition"):
    sys.path.insert(0, str(_REPO_ROOT / "infra" / d))

import discord_media as dm  # noqa: E402

MIB = 1024 * 1024


class TestLimits(unittest.TestCase):
    def test_tier_to_limit_map(self):
        self.assertEqual(dm.upload_limit_for_tier(0), 10 * MIB)
        self.assertEqual(dm.upload_limit_for_tier(1), 25 * MIB)
        self.assertEqual(dm.upload_limit_for_tier(2), 50 * MIB)
        self.assertEqual(dm.upload_limit_for_tier(3), 100 * MIB)

    def test_unknown_tier_defaults_to_the_smallest(self):
        self.assertEqual(dm.upload_limit_for_tier(None), 10 * MIB)
        self.assertEqual(dm.upload_limit_for_tier(99), 10 * MIB)

    def test_human_bytes(self):
        self.assertEqual(dm.human_bytes(10 * MIB), "10.0 MiB")
        self.assertEqual(dm.human_bytes(2048), "2 KiB")


class TestAttach(unittest.TestCase):
    def setUp(self):
        self.tmp = Path("/tmp/loom-media-test")
        self.tmp.mkdir(parents=True, exist_ok=True)
        self.small = self.tmp / "small.mp4"
        self.big = self.tmp / "big.mp4"
        self.small.write_bytes(b"x" * 1024)
        self.big.write_bytes(b"x" * (16 * MIB))
        self.poster = self.tmp / "poster.jpg"
        self.poster.write_bytes(b"j" * 2048)

    def _attach(self, asset, *, limit=10 * MIB, patch_side_effect=None, poster=True):
        captured = {}

        def fake_patch(token, channel, message, payload, filename, data, ctype):
            captured.update({"filename": filename, "content_type": ctype,
                             "payload": payload, "bytes": len(data)})
            if patch_side_effect:
                raise patch_side_effect
            return {"id": message, "attachments": [{"url": f"https://cdn/{filename}"}]}

        with mock.patch.object(dm, "_patch_message", fake_patch):
            result = dm.attach_acquired_media(
                token="t", channel_id="c", message_id="m", asset_path=asset,
                embed={"title": "🎬 ACQUISITION · ACQUIRED"}, components=[{"type": 1}],
                limit_bytes=limit, tier=0,
                poster_path=self.poster if poster else None)
        return result, captured

    def test_video_attached_when_it_fits(self):
        result, captured = self._attach(self.small)
        self.assertEqual(result["mode"], "video")
        self.assertTrue(result["playable"])
        self.assertEqual(captured["filename"], "small.mp4")
        self.assertEqual(captured["content_type"], "video/mp4")
        self.assertEqual(captured["payload"]["embeds"][0]["video"]["url"],
                         "attachment://small.mp4")

    def test_oversized_video_falls_back_to_poster_with_the_numbers(self):
        result, captured = self._attach(self.big, limit=10 * MIB)
        self.assertEqual(result["mode"], "poster")
        self.assertFalse(result["playable"])
        self.assertIn("16.0 MiB", result["reason"])
        self.assertIn("10.0 MiB", result["reason"])
        self.assertIn("premium tier 0", result["reason"])
        self.assertEqual(captured["filename"], dm.POSTER_NAME)
        self.assertEqual(captured["content_type"], "image/jpeg")
        self.assertNotIn("video", captured["payload"]["embeds"][0])

    def test_discord_refusal_even_when_it_fits_falls_back_truthfully(self):
        calls = {"n": 0}

        def flaky_patch(*a, **kw):
            calls["n"] += 1
            if calls["n"] == 1:                       # the VIDEO upload is refused
                raise urllib.error.HTTPError("u", 413, "Payload Too Large", None, None)
            return {"id": "m", "attachments": [{"url": "https://cdn/poster.jpg"}]}

        with mock.patch.object(dm, "_patch_message", flaky_patch):
            result = dm.attach_acquired_media(
                token="***", channel_id="c", message_id="m", asset_path=self.small,
                embed={"title": "x"}, components=[], limit_bytes=10 * MIB, tier=0,
                poster_path=self.poster)
        self.assertEqual(result["mode"], "poster")
        self.assertFalse(result["playable"])
        self.assertIn("HTTP 413", result["reason"])

    def test_both_uploads_failing_is_reported_not_hidden(self):
        result, _ = self._attach(self.big, limit=10 * MIB,
                                 patch_side_effect=urllib.error.HTTPError(
                                     "u", 500, "Server Error", None, None))
        self.assertEqual(result["mode"], "none")
        self.assertFalse(result["attached"])
        self.assertIn("poster upload also failed", result["reason"])

    def test_never_transcodes_the_original(self):
        before = dm.sha256_file(self.small)

        class _Proc:
            returncode = 0

        def fake_run(cmd, capture_output=True, timeout=0):
            # emulate ffmpeg writing a single frame
            Path(cmd[-1]).write_bytes(b"p" * 512)
            return _Proc()

        with mock.patch.object(dm.subprocess, "run", fake_run):
            extraction = dm.extract_poster(self.small, self.tmp / "p.jpg")
        self.assertTrue(extraction["ok"])
        self.assertFalse(extraction["transcoded"])
        self.assertEqual(extraction["frames"], 1)
        self.assertTrue(extraction["asset_unchanged"])
        self.assertEqual(dm.sha256_file(self.small), before)
        self.assertIn("-frames:v", " ".join(["-frames:v", "1"]))  # documented flag

    def test_poster_extraction_failure_is_reported(self):
        result, _ = self._attach(self.big, limit=10 * MIB, poster=False)
        # no poster supplied -> the guard reports the real reason instead of crashing
        self.assertEqual(result["mode"], "none")
        self.assertFalse(result["playable"])
        self.assertIn("16.0 MiB", result["reason"])
        self.assertIn("no poster frame is available", result["reason"])


if __name__ == "__main__":
    unittest.main()
