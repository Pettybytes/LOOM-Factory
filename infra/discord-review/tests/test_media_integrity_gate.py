"""Manual Intake media-integrity gate + fail-closed acquisition contract.

Regression proof for the incident where a TRUNCATED YouTube download was
accepted far enough to reach the probe stage, and where the acquisition result
selector returned the AUDIO-ONLY half of a split-stream download (surfacing as
the misleading "supplied file has no video stream").

Covers:
  * the artifact selector fails closed instead of falling back to "newest file"
  * an audio-only artifact is never returned as acquired media
  * unfinished split-stream downloads are a FAILURE, not a result
  * the integrity gate rejects a truncated artifact whose metadata still reads
    cleanly (the exact shape of the incident)
  * the size ladder steps DOWN and fails truthfully when nothing fits
  * operator-facing failure classification
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[1]))          # infra/discord-review
sys.path.insert(0, str(_HERE.parents[2] / "acquisition"))

import manual_media_intake as mmi  # noqa: E402
import ytdlp_adapter as ya  # noqa: E402
import adapter  # noqa: E402

HAVE_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
INCIDENT_ARTIFACT = ("/var/folders/zp/m0yvx5fd3x385sjm9gqqz2l40000gn/T/"
                     "loom-manual-intake-taatwym4/59xxeJCbzaM.f299.mp4")


def _make_video(dest: Path, seconds: int = 3) -> None:
    """A tiny REAL mp4 with moov at the FRONT (faststart)."""
    cmd = ["ffmpeg", "-v", "error", "-f", "lavfi",
           "-i", f"testsrc=size=320x240:rate=10",
           "-f", "lavfi", "-i", "sine=frequency=440",
           "-t", str(seconds),
           "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-movflags", "+faststart", "-y", str(dest)]
    subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=True)


def _truncate(src: Path, dest: Path, drop_bytes: int = 20000) -> None:
    """Copy everything EXCEPT the tail — the incident's exact failure shape."""
    data = src.read_bytes()
    dest.write_bytes(data[: max(len(data) - drop_bytes, 1)])


class TestArtifactSelectionFailsClosed(unittest.TestCase):
    """The selector must never invent a result."""

    def test_printed_finalized_path_is_returned(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "media.mp4"
            f.write_bytes(b"x")
            path, failure, _ = mmi._select_finalized_artifact(d, f"{f}\n")
            self.assertEqual(path, str(f))
            self.assertEqual(failure, "")

    def test_no_printed_path_and_empty_dir_is_no_finalized_artifact(self):
        with tempfile.TemporaryDirectory() as d:
            path, failure, _ = mmi._select_finalized_artifact(d, "")
            self.assertIsNone(path)
            self.assertEqual(failure, "NO_FINALIZED_ARTIFACT")

    def test_part_file_means_the_download_never_finished(self):
        # This is what --no-part used to hide: an unfinished transfer left at a
        # normal-looking name. With .part present we know it is incomplete.
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "abc.mp4.part").write_bytes(b"y" * 32)
            path, failure, ev = mmi._select_finalized_artifact(d, "")
            self.assertIsNone(path)
            self.assertEqual(failure, "DOWNLOAD_INCOMPLETE")
            self.assertEqual(ev["partial_files"], ["abc.mp4.part"])

    def test_split_streams_alone_are_not_success(self):
        # THE incident: fNNN video-only + fNNN audio-only, no merged artifact.
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "59xxeJCbzaM.f299.mp4").write_bytes(b"v" * 64)
            (Path(d) / "59xxeJCbzaM.f140.m4a").write_bytes(b"a" * 64)
            path, failure, ev = mmi._select_finalized_artifact(d, "")
            self.assertIsNone(path)
            self.assertEqual(failure, "SPLIT_STREAM_UNFINISHED")
            self.assertEqual(len(ev["split_streams"]), 2)

    def test_newest_nonzero_file_is_never_chosen(self):
        # The removed behaviour: with no printed path, the newest non-empty file
        # was returned — which is how the audio-only half became "the media".
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "newest.mp4").write_bytes(b"z" * 99)
            path, failure, _ = mmi._select_finalized_artifact(d, "")
            self.assertIsNone(path, "a bare file on disk must not be selected")
            self.assertEqual(failure, "NO_FINALIZED_ARTIFACT")


class TestAudioOnlyIsNeverReturned(unittest.TestCase):
    """The precise defect: an audio-only artifact must fail, not be returned."""

    def test_audio_only_artifact_raises_no_video_stream(self):
        with tempfile.TemporaryDirectory() as d:
            audio = Path(d) / "media.m4a"
            audio.write_bytes(b"a" * 128)
            with self.assertRaises(mmi.MediaIntakeError) as ctx:
                mmi.acquire_url_media(
                    "https://www.youtube.com/watch?v=59xxeJCbzaM", out_dir=d,
                    run_download=lambda cmd: str(audio),
                    probe_fn=lambda p: {"has_video": False, "has_audio": True,
                                        "container": "mov,mp4,m4a"},
                    integrity_fn=lambda p: {"ok": True})
            self.assertEqual(ctx.exception.code, "NO_VIDEO_STREAM")


class TestIntegrityGate(unittest.TestCase):
    """ffprobe metadata is NOT sufficient — this is the proof."""

    @classmethod
    def setUpClass(cls):
        if not HAVE_FFMPEG:
            raise unittest.SkipTest("ffmpeg/ffprobe not available")
        cls.tmp = tempfile.mkdtemp(prefix="loom-integrity-test-")
        cls.healthy = Path(cls.tmp) / "healthy.mp4"
        cls.truncated = Path(cls.tmp) / "truncated.mp4"
        _make_video(cls.healthy, seconds=4)
        _truncate(cls.healthy, cls.truncated, drop_bytes=20000)

    @classmethod
    def tearDownClass(cls):
        if HAVE_FFMPEG:
            shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_truncated_artifact_still_reads_cleanly_through_current_probe(self):
        # Establishes the premise: the OLD gate cannot see truncation.
        info = mmi.probe_media(self.truncated)
        self.assertTrue(info["has_video"], "metadata reads fine on a truncated file")
        self.assertGreater(info["duration_seconds"], 0)

    def test_new_gate_rejects_the_truncated_artifact(self):
        verdict = mmi.verify_artifact_integrity(self.truncated)
        self.assertFalse(verdict["ok"], "truncation must be rejected")
        self.assertEqual(verdict["reason"], "media_integrity_failed")

    def test_new_gate_accepts_a_healthy_artifact(self):
        verdict = mmi.verify_artifact_integrity(self.healthy)
        self.assertTrue(verdict["ok"], verdict.get("detail"))
        self.assertEqual(verdict["reason"], "")

    def test_gate_is_cheap(self):
        import time
        start = time.time()
        mmi.verify_artifact_integrity(self.healthy)
        self.assertLess(time.time() - start, 5.0, "integrity must not be expensive")

    @unittest.skipUnless(os.path.exists(INCIDENT_ARTIFACT),
                         "retained incident artifact no longer on disk")
    def test_the_actual_incident_artifact_is_rejected(self):
        """REQUIRED REGRESSION PROOF A — the real forensic artifact."""
        verdict = mmi.verify_artifact_integrity(INCIDENT_ARTIFACT)
        self.assertFalse(verdict["ok"], "the incident artifact MUST fail the gate")
        self.assertEqual(verdict["reason"], "media_integrity_failed")
        # ...and the old gate's blind spot is demonstrated on the real file.
        self.assertTrue(mmi.probe_media(INCIDENT_ARTIFACT)["has_video"])


class TestAcquisitionIntegrityAndSizeLadder(unittest.TestCase):
    def test_integrity_failure_aborts_acquisition(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "media.mp4"
            f.write_bytes(b"m" * 128)
            with self.assertRaises(mmi.MediaIntakeError) as ctx:
                mmi.acquire_url_media(
                    "https://www.youtube.com/watch?v=59xxeJCbzaM", out_dir=d,
                    run_download=lambda cmd: str(f),
                    probe_fn=lambda p: {"has_video": True, "has_audio": True},
                    integrity_fn=lambda p: {"ok": False, "reason": "media_integrity_failed",
                                            "detail": "corrupt input packet"})
            self.assertEqual(ctx.exception.code, "MEDIA_INTEGRITY_FAILED")

    def test_unfinished_split_download_aborts_acquisition(self):
        with tempfile.TemporaryDirectory() as d:
            def fake_download(cmd):
                (Path(d) / "vid.f299.mp4").write_bytes(b"v" * 64)
                (Path(d) / "aud.f140.m4a").write_bytes(b"a" * 64)
                return ""          # yt-dlp printed no finalized path
            with self.assertRaises(mmi.MediaIntakeError) as ctx:
                mmi.acquire_url_media(
                    "https://www.youtube.com/watch?v=59xxeJCbzaM", out_dir=d,
                    run_download=fake_download,
                    probe_fn=lambda p: {"has_video": True},
                    integrity_fn=lambda p: {"ok": True})
            self.assertEqual(ctx.exception.code, "SPLIT_STREAM_UNFINISHED")

    def test_ladder_steps_down_and_returns_the_fitting_representation(self):
        with tempfile.TemporaryDirectory() as d:
            calls = []
            def fake_download(cmd):
                height = int(cmd[cmd.index("--format") + 1]
                             .split("height<=")[1].split("]")[0])
                calls.append(height)
                f = Path(d) / f"h{height}.mp4"
                # 1080 is "too big" for this fake ceiling; 720 fits.
                f.write_bytes(b"x" * (500 if height == 1080 else 10))
                return str(f)
            path, _ = mmi.acquire_url_media(
                "https://www.youtube.com/watch?v=59xxeJCbzaM", out_dir=d,
                max_bytes=100,
                run_download=fake_download,
                probe_fn=lambda p: {"has_video": True, "has_audio": True},
                integrity_fn=lambda p: {"ok": True})
            self.assertTrue(path.endswith("h720.mp4"))
            self.assertEqual(calls[0], 1080, "highest quality is tried first")
            self.assertEqual(calls[1], 720)

    def test_nothing_fits_fails_truthfully_and_never_raises_the_cap(self):
        with tempfile.TemporaryDirectory() as d:
            def fake_download(cmd):
                f = Path(d) / "toobig.mp4"
                f.write_bytes(b"x" * 5000)
                return str(f)
            with self.assertRaises(mmi.MediaIntakeError) as ctx:
                mmi.acquire_url_media(
                    "https://www.youtube.com/watch?v=59xxeJCbzaM", out_dir=d,
                    max_bytes=100,
                    run_download=fake_download,
                    probe_fn=lambda p: {"has_video": True, "has_audio": True},
                    integrity_fn=lambda p: {"ok": True})
            self.assertEqual(ctx.exception.code, "MEDIA_EXCEEDS_LIMIT")


class TestOperatorFacingClassification(unittest.TestCase):
    def test_each_required_failure_kind_is_distinguishable(self):
        cases = [
            (mmi.MediaIntakeError("DOWNLOAD_INCOMPLETE", "x"), "DOWNLOAD_INCOMPLETE"),
            (mmi.MediaIntakeError("MERGE_FAILED", "x"), "MERGE_FAILED"),
            (mmi.MediaIntakeError("MEDIA_INTEGRITY_FAILED", "x"), "MEDIA_INTEGRITY_FAILED"),
            (mmi.MediaIntakeError("NO_VIDEO_STREAM", "x"), "NO_VIDEO_STREAM"),
            (mmi.MediaIntakeError("MEDIA_EXCEEDS_LIMIT", "x"), "MEDIA_EXCEEDS_LIMIT"),
            (ValueError("ffprobe failed (1): bad"), "PROBE_FAILED"),
        ]
        for exc, expected in cases:
            code, reason = adapter.classify_intake_failure(exc, "probe_media")
            self.assertEqual(code, expected, repr(exc))
            self.assertTrue(reason and reason.endswith("."), reason)

    def test_legacy_messages_still_classify(self):
        self.assertEqual(
            adapter.classify_intake_failure(RuntimeError("supplied file has no video stream"))[0],
            "NO_VIDEO_STREAM")
        self.assertEqual(
            adapter.classify_intake_failure(RuntimeError("attachment 5 bytes exceeds cap 3"))[0],
            "MEDIA_EXCEEDS_LIMIT")
        self.assertEqual(
            adapter.classify_intake_failure(RuntimeError("media integrity failed (x)"))[0],
            "MEDIA_INTEGRITY_FAILED")

    def test_unknown_is_not_silently_reported_as_success_or_a_leak(self):
        code, reason = adapter.classify_intake_failure(RuntimeError("boom"))
        self.assertEqual(code, "UNKNOWN")
        self.assertNotIn("boom", reason, "raw detail stays in the log")


if __name__ == "__main__":
    unittest.main(verbosity=2)
