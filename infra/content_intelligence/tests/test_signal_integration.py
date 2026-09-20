"""Runtime acceptance A-F for the deterministic signal layer, on REAL media.

Every assertion here runs the actual ffmpeg decode path over real bytes; none of
them assert behaviour that "should" happen. Each acceptance item reports the
observable it is checking:

  A  transcript-driven moment surfaces (and is never displaced by a signal)
  B  audio-salient moment surfaces
  C  visual-salient moment surfaces where transcript evidence is weak/absent
  D  hybrid evidence surfaces (transcript window + signal on the same moment)
  E  overlapping candidates deduplicate/fuse correctly
  F  quiet/control media produces no garbage candidate inflation

The fixture `fixtures/test.mp4` is REQUIRED and committed. Missing fixture =
hard failure, never a skip.

Run:  python3 -m unittest discover -s tests -v   (from infra/content_intelligence/)
"""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

# Importable from any CWD (see test_candidate_fusion.py).
_SUITE_DIR = Path(__file__).resolve().parents[1]
if str(_SUITE_DIR) not in sys.path:
    sys.path.insert(0, str(_SUITE_DIR))

from candidate_fusion import fuse_candidates  # noqa: E402
from multimodal_signals import analyze_media_signals  # noqa: E402

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "test.mp4"
OVERLAP_THRESHOLD = 0.5


def require_fixture(case: unittest.TestCase) -> None:
    if not FIXTURE.exists():
        case.fail(
            f"REQUIRED test fixture missing: {FIXTURE}. "
            "It is committed with this suite; restore it instead of skipping."
        )


def overlap_ratio(w1: dict, w2: dict) -> float:
    overlap = max(0.0, min(w1["end_s"], w2["end_s"]) - max(w1["start_s"], w2["start_s"]))
    span = min(max(0.1, w1["end_s"] - w1["start_s"]),
               max(0.1, w2["end_s"] - w2["start_s"]))
    return overlap / span


def make_audio_control(path: Path, seconds: int = 12) -> None:
    """Continuous low tone with a loud 3s burst from t=5.

    Audio salience is measured against a rolling median, so the control must have
    a non-silent baseline for the burst to register as a deviation."""
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y",
         "-f", "lavfi", "-i", f"color=c=gray:s=320x240:r=10:d={seconds}",
         "-f", "lavfi", "-i", f"sine=frequency=220:sample_rate=44100:duration={seconds}",
         # NOTE: the `volume=enable=...` form silently does nothing here, which
         # produced a flat clip and a phantom peak. Measured: this expression
         # form scales the burst to RMS ~2870 vs ~145 for the baseline.
         "-af", "volume=volume='if(between(t,5,8),1,0.05)':eval=frame",
         "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "128k", str(path)],
        check=True, capture_output=True, timeout=120,
    )


def make_quiet_control(path: Path, seconds: int = 10) -> None:
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y",
         "-f", "lavfi", "-i", f"color=c=black:s=320x240:r=10:d={seconds}",
         "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono",
         "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
         str(path)],
        check=True, capture_output=True, timeout=120,
    )


def make_steady_control(path: Path, seconds: int = 12) -> None:
    """A NON-silent but perfectly steady control: constant tone, static frame.

    This is the case that used to manufacture candidates. The audio is real
    (measured RMS ~144.7 every second) but carries no salience at all, so a
    detector that only ranks within the source reported its ~0.1 RMS float noise
    as a peak."""
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y",
         "-f", "lavfi", "-i", f"color=c=gray:s=320x240:r=10:d={seconds}",
         "-f", "lavfi", "-i", f"sine=frequency=220:sample_rate=44100:duration={seconds}",
         "-af", "volume=0.05",
         "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "128k", str(path)],
        check=True, capture_output=True, timeout=120,
    )


class SignalAcceptanceTests(unittest.TestCase):
    def test_A_transcript_driven_moment_survives_against_real_signals(self):
        require_fixture(self)
        real = analyze_media_signals(str(FIXTURE))["signal_windows"]
        self.assertTrue(real, "fixture produced no real signal windows to fuse against")
        transcript = [{"start_s": 40.0, "end_s": 60.0, "basis": "hook",
                       "source": "transcript"}]
        fused = fuse_candidates(transcript, real)
        transcripts = [w for w in fused if w["source"] == "transcript"]
        self.assertEqual(len(transcripts), 1)
        self.assertEqual(transcripts[0]["start_s"], 40.0)
        self.assertEqual(transcripts[0]["end_s"], 60.0)
        # Observed: the real fixture's windows sit far from 40-60s, so the
        # transcript moment is preserved unannotated rather than absorbed.
        self.assertEqual(transcripts[0]["origin"], "transcript")

    def test_B_audio_salient_moment_surfaces(self):
        with tempfile.TemporaryDirectory() as tmp:
            clip = Path(tmp) / "burst.mp4"
            make_audio_control(clip)
            res = analyze_media_signals(str(clip))
            audio_peaks = res["peaks"]["audio_spike"]
            self.assertTrue(audio_peaks, "no audio peak detected in the audio control clip")
            audio_windows = [w for w in res["signal_windows"] if w["basis"] == "audio_spike"]
            self.assertTrue(audio_windows)
            # The burst is at t=5..8, so the peak must land in that region.
            self.assertTrue(5 <= audio_peaks[0] <= 8,
                            f"audio peak {audio_peaks[0]}s outside the burst window")
            for w in audio_windows:
                self.assertLessEqual(w["start_s"], w["peak_s"])
                self.assertLessEqual(w["peak_s"], w["end_s"])

    def test_C_visual_salient_moment_surfaces_from_real_media(self):
        require_fixture(self)
        res = analyze_media_signals(str(FIXTURE))
        visual = [w for w in res["signal_windows"]
                  if w["basis"] in ("visual_motion", "scene_cut")]
        self.assertTrue(visual, "no visual-salience window detected in the fixture")
        # Observed: the fixture's only peaks are visual; the transcript has no
        # say here at all.
        self.assertEqual(res["peaks"]["audio_spike"], [])
        fused = fuse_candidates([], res["signal_windows"])
        self.assertTrue(any(w["origin"] == "signal" for w in fused))

    def test_D_hybrid_evidence_surfaces_on_real_extraction(self):
        require_fixture(self)
        res = analyze_media_signals(str(FIXTURE))
        signal_windows = res["signal_windows"]
        self.assertTrue(signal_windows)
        # Build a transcript window exactly on the real detected moment.
        target = signal_windows[0]
        transcript = [{"start_s": target["start_s"], "end_s": target["end_s"],
                       "basis": "hook", "source": "transcript"}]
        fused = fuse_candidates(transcript, signal_windows)
        hybrids = [w for w in fused if w["origin"] == "hybrid"]
        self.assertEqual(len(hybrids), 1)
        self.assertEqual(hybrids[0]["source"], "transcript")
        self.assertIn(target["basis"], hybrids[0]["supported_by"])

    def test_E_real_overlapping_candidates_deduplicate(self):
        require_fixture(self)
        raw = analyze_media_signals(str(FIXTURE))["signal_windows"]
        fused = fuse_candidates([], raw)
        # Observed on the committed fixture: two windows both span 0-10s, so
        # fusion must collapse them and keep every basis.
        self.assertLess(len(fused), len(raw))
        self.assertEqual(len(fused), 1)
        self.assertEqual(sorted(fused[0]["supported_by"]), ["scene_cut", "visual_motion"])
        self._assert_no_residual_overlap(fused)

    def test_E_no_residual_overlap_on_richer_real_media(self):
        require_fixture(self)
        raw = analyze_media_signals(str(FIXTURE))["signal_windows"]
        # Duplicate the peaks so many overlapping windows exist at once.
        many = []
        for repeat in range(3):
            for w in raw:
                copy = dict(w)
                copy["start_s"] = w["start_s"] + repeat * 1.0
                copy["end_s"] = w["end_s"] + repeat * 1.0
                copy["peak_s"] = w["peak_s"] + repeat * 1.0
                many.append(copy)
        fused = fuse_candidates([], many)
        self.assertLess(len(fused), len(many))
        self._assert_no_residual_overlap(fused)

    def test_F_quiet_control_media_causes_no_inflation(self):
        with tempfile.TemporaryDirectory() as tmp:
            quiet = Path(tmp) / "quiet.mp4"
            make_quiet_control(quiet)
            res = analyze_media_signals(str(quiet))
            self.assertEqual(res["signal_windows"], [])
            self.assertEqual(res["peaks"],
                             {"audio_spike": [], "visual_motion": [], "scene_cut": []})
            # Nothing to fuse, nothing to inflate.
            self.assertEqual(fuse_candidates([], res["signal_windows"]), [])

    def test_F_steady_non_silent_control_causes_no_inflation(self):
        """Regression: a constant tone is NOT silence, but has zero salience.

        Percentile ranking made the largest sample of any non-constant signal
        rank 1.0, so this control produced a phantom audio peak at t=2s. The
        salience floor is what makes F hold for sources that are steady rather
        than digital silence."""
        with tempfile.TemporaryDirectory() as tmp:
            steady = Path(tmp) / "steady.mp4"
            make_steady_control(steady)
            res = analyze_media_signals(str(steady))
            # The source is genuinely audible — this is not the silence case.
            self.assertGreater(max(res["raw_signals"]["audio"]["loudness"]), 100)
            self.assertEqual(res["signal_windows"], [])
            self.assertEqual(res["peaks"],
                             {"audio_spike": [], "visual_motion": [], "scene_cut": []})

    def test_signal_layer_costs_nothing_and_touches_no_model(self):
        require_fixture(self)
        res = analyze_media_signals(str(FIXTURE))
        # The manifest is the whole provenance record: no provider fields exist.
        for forbidden in ("cost_usd", "model", "provider", "usage", "tokens"):
            self.assertNotIn(forbidden, res)

    def _assert_no_residual_overlap(self, windows):
        for i, a in enumerate(windows):
            for b in windows[i + 1:]:
                self.assertLess(
                    overlap_ratio(a, b), OVERLAP_THRESHOLD,
                    f"unfused overlap remains between {a} and {b}",
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
