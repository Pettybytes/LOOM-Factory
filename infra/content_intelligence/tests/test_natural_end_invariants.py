"""The natural-end contract: FIRST complete boundary forward, never the last."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from content_intelligence.moment_quality import (  # noqa: E402
    T_FALL, T_HOT_TAKE, T_PHYSICAL, T_STORY, build_boundary_plan, completeness_gate,
    natural_end,
)


def seg(s: float, t: str, d: float = 4.0) -> dict:
    return {"start_s": float(s), "end_s": float(s) + d, "text": t}


LONG = [seg(0, "welcome back."), seg(46, "and then he just says it,"),
        seg(50, "the whole thing was wrong."), seg(120, "later, something else."),
        seg(190, "and that was the end of the video.")]


class NaturalEndInvariantTests(unittest.TestCase):
    def test_does_not_select_the_last_boundary_in_the_source(self):
        end, _r = natural_end(T_HOT_TAKE, 50.0, 46.0, LONG)
        self.assertLess(end, 70.0)                 # NOT 190
        self.assertNotAlmostEqual(end, 194.0, places=0)

    def test_first_complete_sentence_after_a_speech_payoff(self):
        end, reason = natural_end(T_HOT_TAKE, 50.0, 46.0, LONG)
        self.assertEqual(reason, "sentence_boundary")
        self.assertEqual(end, 54.0)                # the payoff sentence completes

    def test_physical_moment_ends_at_action_and_reaction_resolution(self):
        v = [seg(46, "he walks up,"), seg(50, "he fell over.", 3.0),
             seg(54, "everyone screamed and laughed.", 4.0)]
        end, reason = natural_end(T_FALL, 50.0, 46.0, v)
        self.assertEqual(reason, "resolved_after_impact")
        self.assertGreaterEqual(end, 58.0)         # through the reaction

    def test_later_boundary_only_when_the_earlier_one_is_incomplete(self):
        # payoff sentence has NO terminal punctuation -> must advance to the next one
        v = [seg(10, "and then he said something", 4.0), seg(14, "and that was the point.", 4.0)]
        end, _r = natural_end(T_HOT_TAKE, 10.0, 10.0, v)
        self.assertGreaterEqual(end, 18.0)         # advanced past the incomplete one

    def test_unrelated_later_content_does_not_extend_the_clip(self):
        v = [seg(50, "he fell over.", 3.0), seg(54, "everyone laughed.", 4.0),
             seg(150, "much later, unrelated material.")]
        end, _r = natural_end(T_PHYSICAL, 50.0, 46.0, v)
        self.assertLess(end, 80.0)

    def test_completeness_still_passes_for_a_strong_complete_moment(self):
        segs = [seg(0, "he fell out of the chair."), seg(4, "everyone laughed.")]
        plan = build_boundary_plan(T_PHYSICAL, 4.0, segs)
        self.assertTrue(completeness_gate(plan, segs).passed)

    def test_short_source_as_moment_resolves_within_its_duration(self):
        segs = [seg(0, "oh my god look at this.", 3.0)]
        end, _r = natural_end(T_PHYSICAL, 1.0, 0.0, segs, duration_hint=3.0)
        self.assertLessEqual(end, 3.0)
        self.assertGreater(end, 0.0)

    def test_long_form_zone_resolves_to_a_moment_not_the_region(self):
        plan = build_boundary_plan(T_HOT_TAKE, 50.0, LONG)
        span = plan.recommended_clip_end - plan.recommended_clip_start
        self.assertLess(span, 80.0)                # not the ~146s region
        self.assertGreater(span, 0.0)

    def test_never_moves_before_the_event(self):
        for mtype in (T_HOT_TAKE, T_PHYSICAL, T_STORY, T_FALL):
            with self.subTest(mtype):
                end, _r = natural_end(mtype, 50.0, 46.0, LONG)
                self.assertGreaterEqual(end, 50.0)

    def test_is_deterministic(self):
        first = natural_end(T_PHYSICAL, 50.0, 46.0, LONG)
        for _ in range(5):
            self.assertEqual(natural_end(T_PHYSICAL, 50.0, 46.0, LONG), first)


if __name__ == "__main__":
    unittest.main()
