"""The candidate-window contract: normalization, peak detection, window
generation and fusion/dedup.

This is the SINGLE suite for that math — `multimodal_signals` consumes it and
does not reimplement or re-export it. Acceptance items A-F are asserted here at
the contract level and again end-to-end on real media in
`test_signal_integration.py`.

Run:  python3 -m unittest discover -s tests -v   (from infra/content_intelligence/)
"""

import sys
import unittest
from pathlib import Path

# Importable from any CWD: the suite directory carries candidate_fusion and
# multimodal_signals as top-level modules, and the README documents running the
# suite from the repo root as well as from inside this directory.
_SUITE_DIR = Path(__file__).resolve().parents[1]
if str(_SUITE_DIR) not in sys.path:
    sys.path.insert(0, str(_SUITE_DIR))

from candidate_fusion import (  # noqa: E402
    find_peaks,
    fuse_candidates,
    generate_signal_windows,
    normalize_signal,
)


class SignalMathTests(unittest.TestCase):
    def test_normalize_percentile_ranks_within_source(self):
        sig = [10.0, 50.0, 20.0, 100.0, 0.0]
        norm = normalize_signal(sig, method="percentile")
        self.assertEqual(norm[4], 0.0)   # min
        self.assertEqual(norm[0], 0.25)
        self.assertEqual(norm[2], 0.5)   # median
        self.assertEqual(norm[1], 0.75)
        self.assertEqual(norm[3], 1.0)   # max

    def test_normalize_flat_signal_is_all_zero(self):
        # F (contract level): a constant signal must never invent salience.
        self.assertEqual(normalize_signal([0.1] * 10, method="percentile"), [0.0] * 10)
        self.assertEqual(normalize_signal([], method="percentile"), [])

    def test_normalize_minmax(self):
        self.assertEqual(normalize_signal([0.0, 5.0, 10.0], method="minmax"), [0.0, 0.5, 1.0])

    def test_find_peaks(self):
        sig = [0.1, 0.2, 0.9, 0.3, 0.1, 0.8, 0.2, 0.1, 0.95, 0.1]
        self.assertEqual(find_peaks(sig, threshold=0.7, min_distance=2), [2, 5, 8])

    def test_find_peaks_keeps_the_higher_of_two_close_peaks(self):
        sig = [0.1, 0.8, 0.9, 0.8, 0.1]
        self.assertEqual(find_peaks(sig, threshold=0.7, min_distance=2), [2])

    def test_find_peaks_flat_signal_has_none(self):
        # F (contract level): quiet/control media produces no candidates at all.
        self.assertEqual(find_peaks(normalize_signal([0.1] * 10), threshold=0.9, min_distance=2), [])
        self.assertEqual(find_peaks([], threshold=0.9, min_distance=2), [])

    def test_generate_signal_windows_tags_provenance_and_caps_end(self):
        wins = generate_signal_windows([10, 30], basis="audio_spike",
                                       window_before=5, window_after=15, max_len=40)
        self.assertEqual(len(wins), 2)
        self.assertEqual(wins[0], {
            "start_s": 5.0, "end_s": 25.0, "peak_s": 10.0,
            "basis": "audio_spike", "source": "signal_peak",
        })
        self.assertEqual(wins[1]["start_s"], 25.0)
        self.assertEqual(wins[1]["end_s"], 40.0)  # 30 + 15 capped at max_len


class CandidateFusionTests(unittest.TestCase):
    def test_empty_fusion(self):
        self.assertEqual(fuse_candidates([], []), [])
        self.assertEqual(len(fuse_candidates([{"start_s": 0, "end_s": 1}], [])), 1)
        self.assertEqual(len(fuse_candidates([], [{"start_s": 0, "end_s": 1}])), 1)

    def test_A_transcript_window_survives_fusion_untouched(self):
        # A: a transcript-driven moment surfaces, and fusion must not alter it.
        t_wins = [{"start_s": 10, "end_s": 20, "basis": "hook", "source": "transcript"}]
        s_wins = [{"start_s": 60, "end_s": 70, "basis": "audio_spike", "source": "signal_peak"}]
        fused = fuse_candidates(t_wins, s_wins)
        self.assertEqual(len(fused), 2)
        self.assertEqual(fused[0]["start_s"], 10)
        self.assertEqual(fused[0]["origin"], "transcript")
        self.assertEqual(fused[0]["supported_by"], [])
        self.assertEqual(fused[1]["start_s"], 60)

    def test_B_audio_salient_window_surfaces_standalone(self):
        # B: audio salience alone produces a candidate even with no transcript.
        s_wins = [{"start_s": 30, "end_s": 50, "peak_s": 35,
                   "basis": "audio_spike", "source": "signal_peak"}]
        fused = fuse_candidates([], s_wins)
        self.assertEqual(len(fused), 1)
        self.assertEqual(fused[0]["origin"], "signal")
        self.assertEqual(fused[0]["supported_by"], ["audio_spike"])

    def test_C_visual_salient_window_surfaces_without_transcript(self):
        # C: visual payoff surfaces even where transcript evidence is weak/absent.
        s_wins = [{"start_s": 50, "end_s": 60, "basis": "visual_motion",
                   "source": "signal_peak"}]
        fused = fuse_candidates([], s_wins)
        self.assertEqual(len(fused), 1)
        self.assertEqual(fused[0]["origin"], "signal")
        self.assertIn("visual_motion", fused[0]["supported_by"])

    def test_D_hybrid_evidence_is_recorded_on_the_transcript_window(self):
        # D: overlapping transcript + signal => one hybrid window, provenance kept.
        t_wins = [{"start_s": 10, "end_s": 20, "basis": "hook", "source": "transcript"}]
        s_wins = [{"start_s": 12, "end_s": 22, "basis": "audio_spike",
                   "source": "signal_peak"}]
        fused = fuse_candidates(t_wins, s_wins)
        self.assertEqual(len(fused), 1)
        self.assertEqual(fused[0]["source"], "transcript")  # never displaced
        self.assertEqual(fused[0]["origin"], "hybrid")
        self.assertEqual(fused[0]["supported_by"], ["audio_spike"])
        self.assertEqual(fused[0]["start_s"], 10)  # transcript bounds preserved

    def test_E_overlapping_signal_windows_deduplicate(self):
        # E: two signals firing on one moment must yield ONE candidate.
        s_wins = [
            {"start_s": 8, "end_s": 28, "peak_s": 13, "basis": "scene_cut",
             "source": "signal_peak"},
            {"start_s": 23, "end_s": 30, "peak_s": 28, "basis": "visual_motion",
             "source": "signal_peak"},
        ]
        fused = fuse_candidates([], s_wins)
        self.assertEqual(len(fused), 1)
        self.assertEqual(fused[0]["start_s"], 8)          # earliest window wins
        self.assertEqual(fused[0]["end_s"], 28)
        self.assertEqual(sorted(fused[0]["supported_by"]), ["scene_cut", "visual_motion"])

    def test_E_identical_signal_windows_do_not_multiply(self):
        dup = {"start_s": 0, "end_s": 10, "peak_s": 2, "basis": "visual_motion",
               "source": "signal_peak"}
        fused = fuse_candidates([], [dict(dup), dict(dup)])
        self.assertEqual(len(fused), 1)
        self.assertEqual(fused[0]["supported_by"], ["visual_motion"])

    def test_E_disjoint_signal_windows_both_survive(self):
        s_wins = [
            {"start_s": 0, "end_s": 10, "basis": "audio_spike", "source": "signal_peak"},
            {"start_s": 100, "end_s": 110, "basis": "scene_cut", "source": "signal_peak"},
        ]
        self.assertEqual(len(fuse_candidates([], s_wins)), 2)

    def test_transcript_window_wins_over_an_existing_signal_window(self):
        # Precedence: the transcript is the semantic anchor even when a signal
        # window was already fused into the input.
        s_wins = [{"start_s": 0, "end_s": 20, "basis": "audio_spike",
                   "source": "signal_peak"}]
        partial = fuse_candidates([], s_wins)
        self.assertEqual(len(partial), 1)
        self.assertEqual(partial[0]["origin"], "signal")

        t_wins = [{"start_s": 5, "end_s": 15, "basis": "hook", "source": "transcript"}]
        fused = fuse_candidates(t_wins, partial)
        # The pre-fused signal window overlaps the transcript window
        # (10s of 10s span), so it is absorbed rather than kept as a second
        # candidate: one moment, one candidate, transcript bounds preserved.
        self.assertEqual(len(fused), 1)
        self.assertEqual(fused[0]["source"], "transcript")
        self.assertEqual(fused[0]["origin"], "hybrid")
        self.assertEqual(fused[0]["supported_by"], ["audio_spike"])
        self.assertEqual(fused[0]["start_s"], 5)
        self.assertEqual(fused[0]["end_s"], 15)

        # Same result whether the signal window reaches fusion raw or pre-fused.
        self.assertEqual(len(fuse_candidates(t_wins, s_wins)), 1)

    def test_fusion_threshold_is_respected(self):
        t_wins = [{"start_s": 0, "end_s": 100, "basis": "hook", "source": "transcript"}]
        barely = [{"start_s": 90, "end_s": 200, "basis": "audio_spike",
                   "source": "signal_peak"}]
        # overlap 10s / min-span 100s = 0.1 < 0.5 -> kept separate.
        self.assertEqual(len(fuse_candidates(t_wins, barely)), 2)
        # overlap 60s / min-span 100s = 0.6 -> merged.
        close = [{"start_s": 40, "end_s": 200, "basis": "audio_spike",
                  "source": "signal_peak"}]
        self.assertEqual(len(fuse_candidates(t_wins, close)), 1)

    def test_output_is_sorted_and_deterministic(self):
        t_wins = [{"start_s": 50, "end_s": 60, "source": "transcript"}]
        s_wins = [{"start_s": 0, "end_s": 10, "basis": "audio_spike",
                   "source": "signal_peak"}]
        first = fuse_candidates(t_wins, s_wins)
        self.assertEqual([w["start_s"] for w in first], [0.0, 50.0])
        self.assertEqual(first, fuse_candidates(t_wins, s_wins))


if __name__ == "__main__":
    unittest.main(verbosity=2)
