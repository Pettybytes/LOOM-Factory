"""Deterministic tests for fMP4 assembly planning + ffmpeg args + retry.

Run:  python3 -m unittest discover -s infra/local-helper/tests -v
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO_ROOT / "infra" / "local-helper"))

from assemble import (  # noqa: E402
    AssemblyError, plan_assembly, concatenate_files,
    build_remux_args, build_mux_args, plan_job_retry,
    INSUFFICIENT_FMP4_SET, validate_ranged_segments, plan_ranged_assembly,
)

BASE = "https://scontent.cdninstagram.com/v/reel.mp4"
ABASE = "https://scontent.cdninstagram.com/v/audio.mp4"


def _video(init="init.mp4", segs=("s1.m4s", "s2.m4s")):
    return {"init_url": init, "segments": list(segs)}


def _audio(init="ainit.mp4", segs=("a1.m4s",)):
    return {"init_url": init, "segments": list(segs)}


class TestPlan(unittest.TestCase):
    def test_video_only(self):
        p = plan_assembly(_video(), None)
        self.assertFalse(p["has_audio"])
        self.assertEqual(len(p["video_segments"]), 2)

    def test_video_and_audio(self):
        p = plan_assembly(_video(), _audio())
        self.assertTrue(p["has_audio"])
        self.assertEqual(p["audio_init"], "ainit.mp4")

    def test_missing_init(self):
        with self.assertRaises(AssemblyError) as cm:
            plan_assembly(_video(init=""), None)
        self.assertEqual(cm.exception.code, "MISSING_INIT")

    def test_missing_segments(self):
        with self.assertRaises(AssemblyError) as cm:
            plan_assembly(_video(segs=()), None)
        self.assertEqual(cm.exception.code, "MISSING_SEGMENTS")

    def test_segment_ceiling(self):
        many = [f"s{i}.m4s" for i in range(401)]
        with self.assertRaises(AssemblyError) as cm:
            plan_assembly(_video(segs=many), None, max_segments=400)
        self.assertEqual(cm.exception.code, "SEGMENT_LIMIT")

    def test_missing_audio_segments(self):
        with self.assertRaises(AssemblyError) as cm:
            plan_assembly(_video(), _audio(segs=()))
        self.assertEqual(cm.exception.code, "MISSING_AUDIO_SEGMENTS")


class TestConcatenate(unittest.TestCase):
    def test_bytes_concatenation(self):
        d = tempfile.mkdtemp()
        try:
            a = os.path.join(d, "a"); b = os.path.join(d, "b"); out = os.path.join(d, "out")
            open(a, "wb").write(b"hello")
            open(b, "wb").write(b" world")
            n = concatenate_files([a, b], out)
            self.assertEqual(n, 11)
            self.assertEqual(open(out, "rb").read(), b"hello world")
        finally:
            import shutil; shutil.rmtree(d, ignore_errors=True)


class TestFfmpegArgs(unittest.TestCase):
    def test_remux_no_shell(self):
        args = build_remux_args("/tmp/in.mp4", "/tmp/out.mp4")
        self.assertEqual(args[0], "ffmpeg")
        self.assertIn("-c", args)
        self.assertIn("copy", args)
        # fixed argument array: every element is a plain string (no shell metachar)
        for a in args:
            self.assertIsInstance(a, str)
            self.assertNotIn(";", a)
            self.assertNotIn("|", a)

    def test_mux_two_inputs(self):
        args = build_mux_args("/tmp/v.mp4", "/tmp/a.mp4", "/tmp/out.mp4")
        self.assertEqual(args[0], "ffmpeg")
        self.assertIn("0:v:0", args)
        self.assertIn("1:a:0", args)


class TestRetry(unittest.TestCase):
    def test_new(self):
        self.assertEqual(plan_job_retry(None), "new")

    def test_failed_is_retryable(self):
        self.assertEqual(plan_job_retry("failed"), "retry")
        self.assertEqual(plan_job_retry("blocked_pending_platform_adapter"), "retry")

    def test_acquired_is_duplicate(self):
        self.assertEqual(plan_job_retry("acquired"), "duplicate")

    def test_running_is_duplicate(self):
        self.assertEqual(plan_job_retry("running"), "duplicate")


class TestRangedAssembly(unittest.TestCase):
    def _seg(self, url, start, end, total):
        return {"url": url, "range_start": start, "range_end": end, "range_total": total}

    def test_contiguous_ordered_ok(self):
        segs = [self._seg(BASE, 0, 999, 5000), self._seg(BASE, 1000, 1999, 5000)]
        out = validate_ranged_segments(segs)
        self.assertEqual([s["range_start"] for s in out], [0, 1000])

    def test_missing_range_gap_rejected(self):
        segs = [self._seg(BASE, 0, 999, 5000), self._seg(BASE, 2000, 2999, 5000)]
        with self.assertRaises(AssemblyError) as cm:
            validate_ranged_segments(segs)
        self.assertEqual(cm.exception.code, INSUFFICIENT_FMP4_SET)

    def test_overlapping_range_rejected(self):
        segs = [self._seg(BASE, 0, 999, 5000), self._seg(BASE, 500, 1499, 5000)]
        with self.assertRaises(AssemblyError) as cm:
            validate_ranged_segments(segs)
        self.assertEqual(cm.exception.code, INSUFFICIENT_FMP4_SET)

    def test_segment_without_range_rejected(self):
        segs = [{"url": BASE, "range_start": None}]
        with self.assertRaises(AssemblyError) as cm:
            validate_ranged_segments(segs)
        self.assertEqual(cm.exception.code, INSUFFICIENT_FMP4_SET)

    def test_mixed_representation_rejected(self):
        segs = [self._seg(BASE, 0, 999, 5000),
                self._seg("https://other.cdninstagram.com/v/other.mp4", 1000, 1999, 5000)]
        with self.assertRaises(AssemblyError) as cm:
            validate_ranged_segments(segs)
        self.assertEqual(cm.exception.code, "MIXED_REPRESENTATION")

    def test_plan_ranged_missing_init_rejected(self):
        with self.assertRaises(AssemblyError) as cm:
            plan_ranged_assembly({"init_url": "", "segments": [self._seg(BASE, 0, 999, 5000)]}, None)
        self.assertEqual(cm.exception.code, "MISSING_INIT")

    def test_plan_ranged_complete_video_audio(self):
        video = {"init_url": "https://x/init.mp4",
                 "segments": [self._seg(BASE, 0, 999, 5000), self._seg(BASE, 1000, 1999, 5000)]}
        audio = {"init_url": "https://x/ainit.mp4",
                 "segments": [self._seg(ABASE, 0, 499, 2000)]}
        plan = plan_ranged_assembly(video, audio)
        self.assertTrue(plan["has_audio"])
        self.assertEqual(plan["video_init"], "https://x/init.mp4")
        self.assertEqual(len(plan["video_segments"]), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
