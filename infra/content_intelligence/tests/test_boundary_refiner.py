"""Focused tests for the deterministic boundary refiner (Segment 5C.2).

Covers: start refinement, end refinement, silence trimming, bounded
expansion/contraction, alignment sanity validation, fallback, media bounds,
event-tail preservation and deterministic replay.

Run from inside this directory:  python3 -m unittest discover -s tests -v
"""

import sys
import unittest
from pathlib import Path

_SUITE_DIR = Path(__file__).resolve().parents[1]
if str(_SUITE_DIR) not in sys.path:
    sys.path.insert(0, str(_SUITE_DIR))

from boundary_refiner import (  # noqa: E402
    BoundaryRefineConfig,
    refine_boundaries,
)

# A two-phrase timeline:  phrase 1 = "Hello there friend" (10.0-11.5),
# phrase 2 = "Now listen carefully" (14.5-16.4).  A 3.0 s gap separates them.
WORDS = [
    {"word": "Hello", "start": 10.0, "end": 10.4},
    {"word": "there", "start": 10.5, "end": 10.9},
    {"word": "friend", "start": 11.0, "end": 11.5},
    {"word": "Now", "start": 14.5, "end": 14.9},
    {"word": "listen", "start": 15.0, "end": 15.6},
    {"word": "carefully", "start": 15.7, "end": 16.4},
]
MEDIA = 60.0


def refine(start, end, **kw):
    kw.setdefault("words", WORDS)
    kw.setdefault("media_duration", MEDIA)
    return refine_boundaries(start, end, **kw)


class StartRefinementTests(unittest.TestCase):
    def test_mid_word_start_is_snapped_and_phrase_start_preferred(self):
        r = refine(11.2, 11.5)
        self.assertEqual(r.timing_source, "transcript")
        self.assertEqual(r.start_reason, "expanded_back_to_phrase_start")
        self.assertAlmostEqual(r.start_s, 9.95, places=3)
        self.assertLess(r.delta_start_s, 0)

    def test_start_inside_a_thought_expands_backward(self):
        # Candidate begins on "carefully" — a mid-thought word.
        r = refine(15.85, 16.4)
        self.assertEqual(r.start_reason, "expanded_back_to_phrase_start")
        self.assertAlmostEqual(r.start_s, 14.45, places=3)
        self.assertAlmostEqual(r.delta_start_s, -1.4, places=3)

    def test_excessive_leading_silence_is_trimmed(self):
        # 13.5 sits in the 3 s gap before phrase 2.
        r = refine(13.5, 16.4)
        self.assertEqual(r.start_reason, "trimmed_leading_silence")
        self.assertAlmostEqual(r.start_s, 14.45, places=3)

    def test_tight_start_is_left_alone(self):
        r = refine(14.5, 16.4)
        self.assertEqual(r.start_reason, "unchanged")
        self.assertAlmostEqual(r.start_s, 14.5, places=3)


class EndRefinementTests(unittest.TestCase):
    def test_mid_word_end_is_snapped_and_phrase_end_preferred(self):
        # Candidate is >= min_duration_s so the min-duration repair does not fire
        # first and move the edge before refinement sees it.
        r = refine(9.6, 10.7)
        self.assertEqual(r.end_reason, "extended_to_phrase_end")
        self.assertAlmostEqual(r.end_s, 11.55, places=3)

    def test_end_before_completion_expands_forward(self):
        # Ends in the 0.2 s gap between "listen" and "carefully".
        r = refine(14.5, 15.65)
        self.assertEqual(r.end_reason, "extended_to_phrase_end")
        self.assertGreater(r.end_s, 16.0)

    def test_trailing_dead_air_is_trimmed(self):
        r = refine(14.5, 25.0)
        self.assertEqual(r.end_reason, "trimmed_trailing_silence")
        self.assertAlmostEqual(r.end_s, 16.45, places=3)
        self.assertLess(r.delta_end_s, -8)

    def test_tight_end_is_left_alone(self):
        r = refine(14.5, 16.4)
        self.assertEqual(r.end_reason, "unchanged")
        self.assertAlmostEqual(r.end_s, 16.4, places=3)


class BoundedChangeTests(unittest.TestCase):
    def test_expansion_is_capped(self):
        cfg = BoundaryRefineConfig(max_expand_s=0.3)
        r = refine(14.5, 15.65, config=cfg)
        self.assertTrue(r.clamped)
        self.assertIn("end_expansion_capped", r.evidence)
        self.assertAlmostEqual(r.end_s, 15.95, places=3)

    def test_dead_air_trim_never_cuts_carried_speech(self):
        # 8.6 s of trailing dead air is trimmed, but the refined window must
        # still CONTAIN every word the original candidate carried.
        r = refine(14.5, 25.0)
        self.assertEqual(r.end_reason, "trimmed_trailing_silence")
        self.assertAlmostEqual(r.end_s, 16.45, places=3)
        carried = [w for w in WORDS if 14.5 <= w["start"] < 25.0]
        self.assertTrue(carried)
        for w in carried:
            self.assertLessEqual(r.start_s, w["start"] + 1e-6)
            self.assertGreaterEqual(r.end_s, w["end"] - 1e-6)

    def test_min_duration_is_enforced(self):
        r = refine(10.0, 10.2, words=[])
        self.assertGreaterEqual(round(r.end_s - r.start_s, 3), 1.0)
        self.assertTrue(r.clamped)
        self.assertIn("candidate_shorter_than_min_duration", r.evidence)


class AlignmentSanityTests(unittest.TestCase):
    def test_implausibly_stretched_word_is_rejected(self):
        # 3.36 s for one word — the real 5C.1 rapid-speech anomaly.
        aligned = [dict(w) for w in WORDS]
        aligned[4] = {"word": "listen", "start": 15.0, "end": 18.36}
        r = refine(14.5, 16.4, aligned_words=aligned)
        self.assertIn("alignment:dropped_1_implausible_spans", r.evidence)
        self.assertTrue(r.alignment_used)

    def test_severe_disagreement_falls_back_to_transcript(self):
        aligned = [{"word": w["word"], "start": w["start"] + 3.0, "end": w["end"] + 3.0}
                   for w in WORDS]
        r = refine(14.5, 16.4, aligned_words=aligned)
        self.assertFalse(r.alignment_used)
        self.assertEqual(r.fallback, "alignment_disagreement_high")
        self.assertEqual(r.timing_source, "transcript")
        # Fallback must still produce the same, safe refinement.
        self.assertAlmostEqual(r.start_s, 14.5, places=3)

    def test_low_correspondence_falls_back_to_transcript(self):
        aligned = [{"word": "unrelated", "start": 14.5, "end": 14.9},
                   {"word": "gibberish", "start": 15.0, "end": 15.4}]
        r = refine(14.5, 16.4, aligned_words=aligned)
        self.assertFalse(r.alignment_used)
        self.assertEqual(r.fallback, "alignment_correspondence_low")

    def test_out_of_bounds_aligned_word_is_dropped(self):
        aligned = [dict(w) for w in WORDS]
        aligned.append({"word": "ghost", "start": 70.0, "end": 70.4})
        r = refine(14.5, 16.4, aligned_words=aligned)
        self.assertIn("alignment:dropped_1_out_of_bounds", r.evidence)

    def test_accepted_alignment_is_used_as_supplemental_timing(self):
        # Every word shifted +0.10s: within tolerance, so alignment is accepted
        # and the refined start follows the aligned edge.
        aligned = [{"word": w["word"], "start": w["start"] + 0.10, "end": w["end"] + 0.10}
                   for w in WORDS]
        r = refine(14.5, 16.4, aligned_words=aligned)
        self.assertTrue(r.alignment_used)
        self.assertEqual(r.timing_source, "aligned")
        self.assertAlmostEqual(r.start_s, 14.55, places=2)


class FallbackTests(unittest.TestCase):
    def test_no_word_timing_uses_segments(self):
        segs = [{"start": 10.0, "end": 20.0}]
        r = refine_boundaries(12.0, 18.0, segments=segs, words=[], media_duration=MEDIA)
        self.assertEqual(r.timing_source, "segments")
        self.assertEqual(r.fallback, "no_word_timing_used_segments")
        self.assertAlmostEqual(r.start_s, 10.0, places=3)
        self.assertAlmostEqual(r.end_s, 20.0, places=3)

    def test_no_timing_at_all_is_safe(self):
        r = refine_boundaries(12.0, 18.0, media_duration=MEDIA)
        self.assertEqual(r.start_s, 12.0)
        self.assertEqual(r.end_s, 18.0)


class MediaBoundsTests(unittest.TestCase):
    def test_edge_candidate_cannot_go_negative_or_exceed_media(self):
        r = refine(-5.0, MEDIA + 10.0)
        self.assertGreaterEqual(r.start_s, 0.0)
        self.assertLessEqual(r.end_s, MEDIA)
        self.assertGreater(r.end_s, r.start_s)

    def test_inverted_candidate_is_repaired(self):
        r = refine(30.0, 20.0)
        self.assertLess(r.start_s, r.end_s)
        self.assertIn("candidate_bounds_were_inverted", r.evidence)

    def test_no_negative_time_without_known_duration(self):
        r = refine_boundaries(-3.0, 5.0, words=WORDS)
        self.assertGreaterEqual(r.start_s, 0.0)


class EventTailTests(unittest.TestCase):
    def test_payoff_after_speech_is_preserved(self):
        r = refine(14.5, 16.4, signal_windows=[
            {"start_s": 16.6, "end_s": 18.2, "basis": "audio_spike"}])
        self.assertTrue(r.end_reason.startswith("preserved_event_tail"))
        self.assertGreater(r.event_tail_s, 0)
        self.assertGreater(r.end_s, 18.0)
        self.assertIn("audio_spike", r.end_reason)

    def test_event_tail_is_bounded(self):
        r = refine(14.5, 16.4, signal_windows=[
            {"start_s": 16.6, "end_s": 40.0, "basis": "visual_motion"}])
        self.assertLessEqual(r.event_tail_s, 2.5 + 0.5)

    def test_distant_signal_does_not_expand_the_candidate(self):
        r = refine(14.5, 16.4, signal_windows=[
            {"start_s": 30.0, "end_s": 35.0, "basis": "audio_spike"}])
        self.assertEqual(r.end_reason, "unchanged")
        self.assertEqual(r.event_tail_s, 0.0)

    def test_unauthorized_basis_is_ignored(self):
        r = refine(14.5, 16.4, signal_windows=[
            {"start_s": 16.6, "end_s": 18.2, "basis": "made_up_basis"}])
        self.assertEqual(r.event_tail_s, 0.0)


class DeterminismTests(unittest.TestCase):
    def test_replay_is_identical(self):
        first = refine(13.5, 25.0, signal_windows=[
            {"start_s": 16.6, "end_s": 18.2, "basis": "audio_spike"}]).to_dict()
        second = refine(13.5, 25.0, signal_windows=[
            {"start_s": 16.6, "end_s": 18.2, "basis": "audio_spike"}]).to_dict()
        self.assertEqual(first, second)

    def test_input_words_are_not_mutated(self):
        import copy
        before = copy.deepcopy(WORDS)
        refine(14.5, 16.4)
        self.assertEqual(WORDS, before)

    def test_result_carries_no_transcript_content(self):
        r = refine(14.5, 16.4)
        self.assertNotIn("text", r.to_dict())


if __name__ == "__main__":
    unittest.main(verbosity=2)
