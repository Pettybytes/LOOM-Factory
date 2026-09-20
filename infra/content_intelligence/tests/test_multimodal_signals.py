"""Deterministic media -> per-second signal extraction (the media layer).

Scope: this suite covers extracting raw signals from real media. The
candidate-window math (normalize/peaks/windows/fusion) is owned and tested by
`test_candidate_fusion.py`.

The fixture `fixtures/test.mp4` is REQUIRED and committed with the suite. If it
is missing these tests FAIL — they never skip, because a skipped media test
would report a green suite while proving nothing.

Run:  python3 -m unittest discover -s tests -v   (from infra/content_intelligence/)
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

# Importable from any CWD (see test_candidate_fusion.py).
_SUITE_DIR = Path(__file__).resolve().parents[1]
if str(_SUITE_DIR) not in sys.path:
    sys.path.insert(0, str(_SUITE_DIR))

from multimodal_signals import (  # noqa: E402
    analyze_media_signals,
    extract_audio_signals,
    extract_visual_signals,
)

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "test.mp4"
VALID_BASES = {"audio_spike", "visual_motion", "scene_cut"}


def require_fixture(case: unittest.TestCase) -> None:
    """Hard requirement: fail loudly rather than silently skipping."""
    if not FIXTURE.exists():
        case.fail(
            f"REQUIRED test fixture missing: {FIXTURE}. "
            "It is committed with this suite; restore it instead of skipping."
        )


def make_quiet_clip(path: Path, seconds: int = 10) -> None:
    """A black, silent control clip — no salience of any kind."""
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y",
         "-f", "lavfi", "-i", f"color=c=black:s=320x240:r=10:d={seconds}",
         "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono",
         "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
         str(path)],
        check=True, capture_output=True, timeout=120,
    )


class MediaSignalExtractionTests(unittest.TestCase):
    def test_audio_signals_are_per_second_and_equal_length(self):
        require_fixture(self)
        audio = extract_audio_signals(str(FIXTURE))
        self.assertEqual(set(audio), {"loudness", "noisiness_zcr", "spike"})
        n = len(audio["loudness"])
        self.assertGreater(n, 0)
        self.assertEqual(n, len(audio["noisiness_zcr"]))
        self.assertEqual(n, len(audio["spike"]))
        # Bounded by the real duration of a 10s fixture (never invent seconds).
        self.assertLessEqual(n, 11)

    def test_visual_signals_are_per_second_and_equal_length(self):
        require_fixture(self)
        visual = extract_visual_signals(str(FIXTURE))
        self.assertEqual(set(visual), {"motion", "scene_cut", "flash"})
        n = len(visual["motion"])
        self.assertGreater(n, 1)
        self.assertEqual(n, len(visual["scene_cut"]))
        self.assertEqual(n, len(visual["flash"]))
        self.assertEqual(visual["motion"][0], 0.0)   # no previous frame
        self.assertEqual(visual["scene_cut"][0], 0.0)
        self.assertEqual(visual["flash"][0], 0.0)

    def test_real_media_extraction_windows_are_well_formed(self):
        require_fixture(self)
        res = analyze_media_signals(str(FIXTURE))
        self.assertNotIn("error", res)
        self.assertGreater(res["duration_s"], 0)
        self.assertGreater(len(res["raw_signals"]["audio"]["loudness"]), 0)
        self.assertGreater(len(res["raw_signals"]["visual"]["motion"]), 0)
        self.assertGreater(len(res["signal_windows"]), 0)
        for w in res["signal_windows"]:
            self.assertIn(w["basis"], VALID_BASES)
            self.assertEqual(w["source"], "signal_peak")
            self.assertLessEqual(w["start_s"], w["end_s"])
            self.assertLessEqual(w["end_s"], res["duration_s"])
            self.assertLessEqual(w["start_s"], w["peak_s"])
            self.assertLessEqual(w["peak_s"], w["end_s"])

    def test_manifest_records_thresholds_for_auditability(self):
        require_fixture(self)
        with tempfile.TemporaryDirectory() as tmp:
            manifest = Path(tmp) / "signal_manifest.json"
            res = analyze_media_signals(str(FIXTURE), manifest_out=str(manifest))
            self.assertTrue(manifest.exists())
            written = json.loads(manifest.read_text(encoding="utf-8"))
            self.assertEqual(written["duration_s"], res["duration_s"])
            self.assertEqual(written["signal_windows"], res["signal_windows"])
            thresholds = written["thresholds"]
            for key in ("peak_percentile", "scene_percentile", "peak_min_distance_s",
                        "normalization", "visual_fps", "visual_size"):
                self.assertIn(key, thresholds)
            self.assertEqual(thresholds["normalization"], "percentile_within_source")

    def test_manifest_cache_returns_identical_result(self):
        require_fixture(self)
        with tempfile.TemporaryDirectory() as tmp:
            manifest = Path(tmp) / "signal_manifest.json"
            first = analyze_media_signals(str(FIXTURE), manifest_out=str(manifest))
            second = analyze_media_signals(str(FIXTURE), manifest_out=str(manifest))
            self.assertEqual(first, second)

    def test_extraction_is_deterministic_across_runs(self):
        require_fixture(self)
        self.assertEqual(analyze_media_signals(str(FIXTURE)),
                         analyze_media_signals(str(FIXTURE)))

    def test_quiet_control_media_produces_no_windows(self):
        # F: quiet/control media must not inflate the candidate list.
        with tempfile.TemporaryDirectory() as tmp:
            quiet = Path(tmp) / "quiet.mp4"
            make_quiet_clip(quiet)
            res = analyze_media_signals(str(quiet))
            self.assertEqual(res.get("signal_windows"), [])
            self.assertEqual(res["peaks"], {"audio_spike": [], "visual_motion": [],
                                            "scene_cut": []})

    def test_max_seconds_bounds_the_decode(self):
        require_fixture(self)
        full = analyze_media_signals(str(FIXTURE))
        bounded = analyze_media_signals(str(FIXTURE), max_seconds=4)
        self.assertGreater(full["duration_s"], bounded["duration_s"])
        self.assertLessEqual(bounded["duration_s"], 4)

    def test_missing_media_raises(self):
        missing = str(FIXTURE.parent / "does_not_exist.mp4")
        with self.assertRaises(FileNotFoundError):
            analyze_media_signals(missing)

    def test_malformed_media_reports_no_garbage(self):
        fd, path = tempfile.mkstemp(suffix=".mp4")
        with os.fdopen(fd, "wb") as fh:
            fh.write(b"\x00\x01\x02garbage-not-media")
        try:
            res = analyze_media_signals(path)
            self.assertEqual(res.get("error"), "malformed_or_empty_media")
            self.assertEqual(res.get("signal_windows"), [])
            self.assertEqual(res.get("duration_s"), 0)
        finally:
            os.remove(path)


if __name__ == "__main__":
    unittest.main(verbosity=2)
