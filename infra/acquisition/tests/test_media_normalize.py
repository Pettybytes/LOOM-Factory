"""Reaction Library delivery-format contract tests (no ffmpeg, no network).

Run:  python3 -m unittest discover -s infra/acquisition/tests -v

Covers: VP9/Opus -> H.264/AAC/yuv420p MP4; already-compatible input is NOT
re-encoded; limits enforced on the finished asset; a failed conversion never
replaces or corrupts the existing asset; and the acquisition path hashes and names
the asset it actually finalized.
"""

import hashlib
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
for d in ("acquisition", "discord-review"):
    sys.path.insert(0, str(_REPO_ROOT / "infra" / d))

import media_normalize as mn  # noqa: E402
import ytdlp_adapter as ya  # noqa: E402

VID = "dQw4w9WgXcQ"
URL = f"https://www.youtube.com/watch?v={VID}"


def probe(*, v="vp9", a="opus", pix="yuv420p", duration=41.0, size=1024,
          fmt="mov,mp4,m4a,3gp,3g2,mj2", streams=1):
    return {
        "streams": ([{"codec_type": "video", "codec_name": v, "pix_fmt": pix,
                      "width": 1080, "height": 1920, "avg_frame_rate": "30/1"}][:streams]
                    + [{"codec_type": "audio", "codec_name": a, "channels": 2}]),
        "format": {"format_name": fmt, "duration": str(duration)},
    }


class TestCompatibilityRules(unittest.TestCase):
    def test_vp9_opus_is_not_delivery_ready(self):
        gaps = mn.compatibility_gaps(probe())
        self.assertIn("vcodec:vp9", gaps)
        self.assertIn("acodec:opus", gaps)
        self.assertTrue(mn.needs_normalization(probe()))

    def test_h264_aac_yuv420p_is_ready(self):
        ready = probe(v="h264", a="aac")
        self.assertEqual(mn.compatibility_gaps(ready), [])
        self.assertFalse(mn.needs_normalization(ready))

    def test_non_mp4_container_and_missing_audio_are_gaps(self):
        self.assertTrue(any(g.startswith("container:")
                            for g in mn.compatibility_gaps(probe(fmt="matroska,webm"))))
        no_audio = {"streams": [{"codec_type": "video", "codec_name": "h264",
                                 "pix_fmt": "yuv420p"}],
                    "format": {"format_name": "mov,mp4", "duration": "10"}}
        self.assertIn("no_audio", mn.compatibility_gaps(no_audio))

    def test_wrong_pixel_format_is_a_gap(self):
        self.assertIn("pix_fmt:yuv444p",
                      mn.compatibility_gaps(probe(v="h264", a="aac", pix="yuv444p")))


class TestNormalizeCommand(unittest.TestCase):
    def test_command_targets_the_contract_and_preserves_geometry(self):
        cmd = mn.build_normalize_command("/in.mp4", "/out.mp4", probe=probe())
        joined = " ".join(cmd)
        self.assertIn("libx264", joined)
        self.assertIn("-pix_fmt yuv420p", joined)
        self.assertIn("-c:a aac", joined)
        self.assertIn("+faststart", joined)
        # geometry preserved: no scaling or frame-rate filters
        self.assertNotIn("-vf", cmd)
        self.assertNotIn("scale=", joined)
        self.assertNotIn("-r", cmd)
        # both streams encoded in one pass (sync preserved)
        self.assertIn("0:v:0", cmd)
        self.assertIn("0:a:0", cmd)

    def test_rotation_is_carried_over_when_present(self):
        rotated = probe()
        rotated["streams"][0]["side_data_list"] = [
            {"side_data_type": "Display Matrix", "rotation": -90}]
        cmd = mn.build_normalize_command("/in.mp4", "/out.mp4", probe=rotated)
        self.assertIn("rotate=-90", " ".join(cmd))


class TestFinalizedValidation(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="loom-norm-"))
        self.good = self.tmp / "good.mp4"
        self.good.write_bytes(b"x" * 4096)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_accepts_a_compatible_asset_and_reports_facts(self):
        report = mn.validate_finalized(probe(v="h264", a="aac"), path=self.good)
        self.assertEqual(report["video_codec"], "h264")
        self.assertEqual(report["audio_codec"], "aac")
        self.assertEqual(report["pix_fmt"], "yuv420p")
        self.assertEqual(report["width"], 1080)
        self.assertEqual(report["frame_rate"], "30/1")

    def test_rejects_incompatible_media(self):
        with self.assertRaises(mn.NormalizeError) as e:
            mn.validate_finalized(probe(), path=self.good)
        self.assertEqual(e.exception.code, "INCOMPATIBLE")

    def test_enforces_size_and_duration_limits(self):
        with self.assertRaises(mn.NormalizeError) as e:
            mn.validate_finalized(probe(v="h264", a="aac", duration=305.0),
                                  path=self.good)
        self.assertEqual(e.exception.code, "TOO_LONG")
        with self.assertRaises(mn.NormalizeError) as e2:
            mn.validate_finalized(probe(v="h264", a="aac"), path=self.good,
                                  max_bytes=1024)
        self.assertEqual(e2.exception.code, "TOO_LARGE")

    def test_rejects_missing_and_empty_files(self):
        with self.assertRaises(mn.NormalizeError) as e:
            mn.validate_finalized(probe(v="h264", a="aac"), path=self.tmp / "nope.mp4")
        self.assertEqual(e.exception.code, "MISSING_FILE")
        empty = self.tmp / "empty.mp4"
        empty.write_bytes(b"")
        with self.assertRaises(mn.NormalizeError) as e2:
            mn.validate_finalized(probe(v="h264", a="aac"), path=empty)
        self.assertEqual(e2.exception.code, "ZERO_BYTES")


class TestNormalizeAndFinalize(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="loom-norm-fin-"))
        self.src = self.tmp / "source.mp4"
        self.src.write_bytes(b"vp9-encoded" * 512)
        self.dst = self.tmp / "delivered.mp4"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _runners(self, *, converted_ok=True, converted_probe=None):
        calls = {"ffmpeg": 0}

        def run_ffmpeg(cmd):
            calls["ffmpeg"] += 1
            if not converted_ok:
                raise mn.NormalizeError("FFMPEG_FAILED", "simulated failure")
            out = Path(cmd[-1])
            out.write_bytes(b"h264-encoded" * 512)
            return ""

        def run_ffprobe(path):
            if str(path).endswith(".normalizing.mp4"):
                return converted_probe or probe(v="h264", a="aac")
            return probe()
        return run_ffmpeg, run_ffprobe, calls

    def test_vp9_opus_is_normalized_to_h264_aac_mp4(self):
        run_ffmpeg, run_ffprobe, calls = self._runners()
        report = mn.normalize_and_finalize(src=self.src, dst=self.dst, probe=probe(),
                                           run_ffmpeg=run_ffmpeg, run_ffprobe=run_ffprobe)
        self.assertTrue(report["normalized"])
        self.assertEqual(report["video_codec"], "h264")
        self.assertEqual(report["audio_codec"], "aac")
        self.assertEqual(report["pix_fmt"], "yuv420p")
        self.assertEqual(calls["ffmpeg"], 1)
        self.assertTrue(self.dst.is_file())
        self.assertFalse((self.tmp / "delivered.mp4.normalizing.mp4").exists())

    def test_already_compatible_input_is_not_transcoded(self):
        run_ffmpeg, run_ffprobe, calls = self._runners()
        report = mn.normalize_and_finalize(src=self.src, dst=self.dst,
                                           probe=probe(v="h264", a="aac"),
                                           run_ffmpeg=run_ffmpeg, run_ffprobe=run_ffprobe)
        self.assertFalse(report["normalized"])
        self.assertEqual(calls["ffmpeg"], 0, "must not re-encode compatible media")
        self.assertTrue(self.dst.is_file())

    def test_failed_conversion_leaves_the_existing_asset_untouched(self):
        self.dst.write_bytes(b"PRE-EXISTING-DELIVERED")
        before = hashlib.sha256(self.dst.read_bytes()).hexdigest()
        run_ffmpeg, run_ffprobe, _ = self._runners(converted_ok=False)
        with self.assertRaises(mn.NormalizeError):
            mn.normalize_and_finalize(src=self.src, dst=self.dst, probe=probe(),
                                      run_ffmpeg=run_ffmpeg, run_ffprobe=run_ffprobe)
        self.assertEqual(hashlib.sha256(self.dst.read_bytes()).hexdigest(), before)
        self.assertFalse((self.tmp / "delivered.mp4.normalizing.mp4").exists(),
                         "temp file must be cleaned up")

    def test_incompatible_conversion_output_is_refused(self):
        self.dst.write_bytes(b"PRE-EXISTING")
        run_ffmpeg, run_ffprobe, _ = self._runners(
            converted_probe=probe(v="vp9", a="opus"))     # converter produced junk
        with self.assertRaises(mn.NormalizeError) as e:
            mn.normalize_and_finalize(src=self.src, dst=self.dst, probe=probe(),
                                      run_ffmpeg=run_ffmpeg, run_ffprobe=run_ffprobe)
        self.assertEqual(e.exception.code, "INCOMPATIBLE")
        self.assertEqual(self.dst.read_bytes(), b"PRE-EXISTING")

    def test_oversized_conversion_is_refused_before_replacing(self):
        self.dst.write_bytes(b"PRE-EXISTING")
        run_ffmpeg, run_ffprobe, _ = self._runners()
        with self.assertRaises(mn.NormalizeError) as e:
            mn.normalize_and_finalize(src=self.src, dst=self.dst, probe=probe(),
                                      run_ffmpeg=run_ffmpeg, run_ffprobe=run_ffprobe,
                                      max_bytes=1024)
        self.assertEqual(e.exception.code, "TOO_LARGE")
        self.assertEqual(self.dst.read_bytes(), b"PRE-EXISTING")


class TestAcquisitionPathDeliversCompatibleAsset(unittest.TestCase):
    """acquire_local must hash and name the asset it actually finalized."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="loom-compat-"))
        self.tmp = self.root / "tmp"
        self.lib = self.root / "library"
        for d in (self.tmp, self.lib):
            d.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_vp9_download_is_delivered_as_h264_and_hashed_as_such(self):
        def run_probe(_cmd):
            return {"id": VID, "duration": 41.0, "title": "t", "is_live": False}

        def run_download(cmd):
            path = Path(cmd[cmd.index("--output") + 1].replace("%(ext)s", "mp4"))
            path.write_bytes(b"vp9-source" * 512)
            return str(path)

        def run_ffprobe(path):
            return probe() if str(path).endswith(f"{VID}.mp4") else probe(v="h264", a="aac")

        def run_ffmpeg(cmd):
            Path(cmd[-1]).write_bytes(b"h264-final" * 512)
            return ""

        out = ya.acquire_local(url=URL, expected_id=VID, tmp_dir=self.tmp,
                               library_dir=self.lib, run_probe=run_probe,
                               run_download=run_download, run_ffprobe=run_ffprobe,
                               run_ffmpeg=run_ffmpeg)
        final = Path(out["path"])
        self.assertTrue(final.is_file())
        self.assertTrue(final.name.startswith(f"youtube__{VID}__"))
        self.assertTrue(final.name.endswith(".mp4"))
        self.assertEqual(out["video_codec"], "h264")
        self.assertEqual(out["audio_codec"], "aac")
        self.assertTrue(out["normalized"])
        # the name and hash describe the DELIVERED file, not the download
        self.assertEqual(out["sha256"], hashlib.sha256(final.read_bytes()).hexdigest())
        self.assertEqual(final.name, ya.filename_for(VID, out["sha256"]))
        self.assertEqual(list(self.lib.iterdir()), [final])
        self.assertEqual(list(self.tmp.glob(f"{VID}.*")), [])

    def test_compatible_download_is_delivered_without_transcoding(self):
        calls = {"ffmpeg": 0}

        def run_probe(_cmd):
            return {"id": VID, "duration": 41.0, "is_live": False}

        def run_download(cmd):
            path = Path(cmd[cmd.index("--output") + 1].replace("%(ext)s", "mp4"))
            path.write_bytes(b"h264-source" * 256)
            return str(path)

        def run_ffmpeg(_cmd):
            calls["ffmpeg"] += 1
            return ""

        out = ya.acquire_local(url=URL, expected_id=VID, tmp_dir=self.tmp,
                               library_dir=self.lib, run_probe=run_probe,
                               run_download=run_download,
                               run_ffprobe=lambda _p: probe(v="h264", a="aac"),
                               run_ffmpeg=run_ffmpeg)
        self.assertFalse(out["normalized"])
        self.assertEqual(calls["ffmpeg"], 0)

    def test_download_command_prefers_h264_and_aac(self):
        cmd = ya.build_download_command(URL, out_template="/tmp/x.%(ext)s")
        selector = cmd[cmd.index("--format") + 1]
        self.assertIn("avc1", selector)
        self.assertIn("mp4a", selector)


if __name__ == "__main__":
    unittest.main()
