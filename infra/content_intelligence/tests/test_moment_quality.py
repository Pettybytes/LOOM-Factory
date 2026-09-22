"""Required regressions: moment type, natural end, completeness, rank, dedup."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from content_intelligence.moment_quality import (  # noqa: E402
    C_EVENT_UNRESOLVED, C_INCOMPLETE_SENTENCE, C_MISSING_PAYOFF, RankInput,
    T_FALL, T_HOT_TAKE, T_INFO, T_PHYSICAL, T_STORY, T_UNKNOWN,
    build_boundary_plan, classify_moment_type, completeness_gate, dedup, natural_end,
    relative_rank, repair_boundaries, type_aware_start,
)


def seg(s: float, t: str, dur: float = 4.0) -> dict:
    return {"start_s": float(s), "end_s": float(s) + dur, "text": t}


# ------------------------------------------------------------ SS16 moment type

class MomentTypeTests(unittest.TestCase):
    def test_physical_event_wins_when_action_precedes_spoken_reaction(self):
        ev = classify_moment_type(
            transcript_text="he fell out of the chair and everyone screamed",
            signal_bases=["motion", "scene_change", "audio_spike"])
        self.assertEqual(ev.moment_type, T_FALL)
        self.assertGreater(ev.confidence, 0.5)
        self.assertTrue(ev.evidence)
        self.assertTrue(ev.provenance)

    def test_spoken_thought_classifies_as_speech_family(self):
        ev = classify_moment_type(transcript_text="honestly i think the truth is nobody talks about this")
        self.assertEqual(ev.moment_type, T_HOT_TAKE)

    def test_story_setup_classifies_as_story(self):
        self.assertEqual(classify_moment_type(transcript_text="so i was walking one time and then").moment_type, T_STORY)

    def test_informational_classifies(self):
        self.assertEqual(classify_moment_type(transcript_text="here's why the reason this happens").moment_type, T_INFO)

    def test_ambiguous_evidence_can_be_unknown(self):
        ev = classify_moment_type(transcript_text="banana")
        self.assertEqual(ev.moment_type, T_UNKNOWN)
        self.assertTrue(ev.ambiguous)

    def test_no_single_basis_forces_a_type(self):
        # visual signals alone, no keywords, only one basis -> not forced
        ev = classify_moment_type(transcript_text="", signal_bases=["motion"])
        self.assertEqual(ev.moment_type, T_UNKNOWN)


class TypeAwareStartTests(unittest.TestCase):
    SEGS = [seg(0, "welcome back"), seg(16, "the chair is about to wobble"),
            seg(18, "uh oh here we go"), seg(20, "he fell out of the chair"),
            seg(22, "oh my god he screamed")]

    def test_physical_start_may_move_before_the_spoken_reaction(self):
        start, reason, alts = type_aware_start(T_FALL, 22.0, self.SEGS)
        self.assertLess(start, 22.0)                 # before the speech
        self.assertLessEqual(start, 20.0)            # can reach the physical event
        self.assertTrue(reason)
        self.assertGreaterEqual(len(alts), 4)
        self.assertTrue(all(isinstance(a.hook_strength, float) for a in alts))

    def test_speech_start_uses_minimal_lead_not_five_seconds_of_dead_air(self):
        start, _r, alts = type_aware_start(T_HOT_TAKE, 20.0, self.SEGS)
        leads = {round(20.0 - a.start, 1) for a in alts}
        self.assertNotIn(8.0, leads)
        self.assertLessEqual(max(leads), 3.0)

    def test_story_start_preserves_setup(self):
        _s, _r, alts = type_aware_start(T_STORY, 20.0, self.SEGS)
        self.assertGreaterEqual(max(20.0 - a.start for a in alts), 8.0)


class NaturalEndTests(unittest.TestCase):
    def test_physical_end_includes_immediate_reaction(self):
        segs = [seg(10, "he fell out of the chair."), seg(12, "everyone screamed.")]
        end, reason = natural_end(T_FALL, 10.0, 10.0, segs)
        self.assertGreaterEqual(end, 14.0)
        self.assertEqual(reason, "resolved_after_impact")

    def test_speech_end_lands_on_a_sentence_boundary(self):
        segs = [seg(10, "i think this is wrong.", 4.0), seg(14, "and here is why.", 4.0)]
        end, reason = natural_end(T_HOT_TAKE, 10.0, 10.0, segs)
        self.assertEqual(reason, "sentence_boundary")
        self.assertGreaterEqual(end, 18.0)           # does not cut mid-thought

    def test_story_end_includes_the_punchline(self):
        segs = [seg(10, "so the setup was long.", 6.0), seg(20, "and that was the punchline.", 4.0)]
        end, reason = natural_end(T_STORY, 20.0, 10.0, segs)
        self.assertGreaterEqual(end, 22.0)
        self.assertEqual(reason, "punchline_and_sentence_complete")

    def test_end_is_not_a_fixed_duration(self):
        short, _ = natural_end(T_FALL, 10.0, 10.0, [seg(10, "he fell out of the chair.")], signals=[])
        long_, _ = natural_end(T_STORY, 10.0, 0.0, [seg(0, "setup."), seg(40, "punchline.")])
        self.assertNotEqual(short, long_)

    def test_premature_end_rejected_when_event_still_active(self):
        segs = [seg(10, "he fell out of the chair.")]
        plan = build_boundary_plan(T_FALL, 10.0, segs)
        active = [{"basis": "motion", "start_s": 10.0, "end_s": plan.recommended_clip_end}]
        gate = completeness_gate(plan, segs, signals=active)
        self.assertFalse(gate.passed)
        self.assertIn(C_EVENT_UNRESOLVED, gate.reasons)


class CompletenessTests(unittest.TestCase):
    def test_great_hook_but_missing_payoff_fails(self):
        segs = [seg(0, "oh my god look at this."), seg(4, "welcome back everyone.")]
        plan = build_boundary_plan(T_HOT_TAKE, 0.0, segs)
        gate = completeness_gate(plan, segs)
        self.assertFalse(gate.passed)
        self.assertIn(C_MISSING_PAYOFF, gate.reasons)

    def test_sentence_truncated_fails(self):
        segs = [seg(0, "so i was walking down the")]
        plan = build_boundary_plan(T_HOT_TAKE, 0.0, segs)
        gate = completeness_gate(plan, segs)
        self.assertFalse(gate.passed)
        self.assertIn(C_INCOMPLETE_SENTENCE, gate.reasons)

    def test_event_unresolved_fails(self):
        segs = [seg(0, "he fell out of the chair.")]
        plan = build_boundary_plan(T_PHYSICAL, 0.0, segs)
        gate = completeness_gate(plan, segs, signals=[{"basis": "motion", "start_s": 0.0, "end_s": 100.0}])
        self.assertFalse(gate.passed)
        self.assertIn(C_EVENT_UNRESOLVED, gate.reasons)

    def test_repairable_candidate_passes_after_bounded_extension(self):
        # The follow-up line deliberately carries NO payoff/reaction cue, so the
        # natural end stops mid-sentence and bounded repair is what completes it.
        segs = [seg(0, "he fell out of the chair"), seg(4, "and that was the end of it.")]
        plan = build_boundary_plan(T_PHYSICAL, 0.0, segs)
        first = completeness_gate(plan, segs)
        self.assertFalse(first.passed)                       # ends mid-sentence
        repaired, gate, attempts = repair_boundaries(plan, first, segs)
        self.assertTrue(gate.passed, gate.reasons)
        self.assertTrue(attempts)
        self.assertTrue(all(a["extended_s"] <= 4.0 for a in attempts))
        self.assertGreater(repaired.recommended_clip_end, plan.recommended_clip_end)

    def test_repair_is_bounded_and_does_not_expand_indefinitely(self):
        segs = [seg(0, "he fell out of the chair")]          # never completes
        plan = build_boundary_plan(T_PHYSICAL, 0.0, segs)
        _p, gate, attempts = repair_boundaries(plan, completeness_gate(plan, segs), segs)
        self.assertFalse(gate.passed)
        self.assertLessEqual(len(attempts), 3)

    def test_strong_complete_moment_passes(self):
        segs = [seg(0, "he fell out of the chair."), seg(4, "everyone laughed.")]
        plan = build_boundary_plan(T_PHYSICAL, 4.0, segs)
        gate = completeness_gate(plan, segs)
        self.assertTrue(gate.passed, gate.reasons)


class RankingTests(unittest.TestCase):
    def _cand(self, fp, ts, **kw):
        base = dict(fingerprint=fp, event_timestamp=ts, moment_type=T_PHYSICAL,
                    market=85, content=85, selection=88, confidence=88, hook=True,
                    activity=True, payoff=True, completeness=True, reaction_angle=True)
        base.update(kw)
        return RankInput(**base)

    def test_ranks_are_unique_and_deterministic(self):
        cands = [self._cand("a", 10.0), self._cand("b", 900.0, selection=92),
                 self._cand("c", 1800.0, selection=80, content=70)]
        r1 = relative_rank(cands)
        r2 = relative_rank(list(reversed(cands)))
        self.assertEqual([x.fingerprint for x in r1], [x.fingerprint for x in r2])
        self.assertEqual(sorted(x.source_relative_rank for x in r1), [1, 2, 3])
        self.assertTrue(all(x.source_candidate_count == 3 for x in r1))

    def test_strong_physical_can_outrank_talk_only_when_evidence_justifies(self):
        physical = self._cand("phys", 100.0, moment_type=T_FALL, selection=92, content=90)
        talk = self._cand("talk", 900.0, moment_type=T_HOT_TAKE, selection=80, content=74,
                          activity=False, payoff=True)
        order = [x.fingerprint for x in relative_rank([physical, talk])]
        self.assertEqual(order[0], "phys")

    def test_strong_hot_take_can_outrank_mediocre_action(self):
        talk = self._cand("talk", 100.0, moment_type=T_HOT_TAKE, selection=93, content=91, confidence=93)
        weak_action = self._cand("weak", 900.0, moment_type=T_PHYSICAL, selection=76, content=72,
                                 confidence=75, payoff=False)
        order = [x.fingerprint for x in relative_rank([talk, weak_action])]
        self.assertEqual(order[0], "talk")          # not hardcoded by type

    def test_incomplete_candidate_ranks_below_complete_one(self):
        complete = self._cand("good", 10.0, completeness=True)
        incomplete = self._cand("bad", 10.0, completeness=False, selection=99, content=99)
        self.assertEqual([x.fingerprint for x in relative_rank([complete, incomplete])][0], "good")


class DedupTests(unittest.TestCase):
    def _overlap(self, fp, start, end, **kw):
        return RankInput(fingerprint=fp, event_timestamp=start, overlap_start=start,
                         overlap_end=end, selection=kw.pop("selection", 85),
                         content=85, confidence=85, market=85, hook=True, activity=True,
                         payoff=True, completeness=True, **kw)

    def test_four_overlapping_cuts_collapse_to_one_primary(self):
        cands = [self._overlap("a", 18 * 60 + 38, 18 * 60 + 55, selection=92),
                 self._overlap("b", 18 * 60 + 39, 18 * 60 + 56, selection=88),
                 self._overlap("c", 18 * 60 + 40, 18 * 60 + 54, selection=84),
                 self._overlap("d", 18 * 60 + 41, 18 * 60 + 57, selection=80)]
        out = {x.fingerprint: x for x in dedup(cands)}
        self.assertEqual(out["a"].duplicate_state, "PRIMARY")
        for fp in ("b", "c", "d"):
            self.assertEqual(out[fp].duplicate_state, "DUPLICATE_SUPPRESSED")
            self.assertEqual(out[fp].rank_evidence["suppressed_by"], "a")

    def test_two_separate_events_both_survive_as_distinct(self):
        cands = [self._overlap("fall", 18 * 60 + 40, 18 * 60 + 55),
                 self._overlap("arg", 18 * 60 + 58, 19 * 60 + 20)]
        out = {x.fingerprint: x for x in dedup(cands)}
        self.assertEqual(out["fall"].duplicate_state, "DISTINCT")
        self.assertEqual(out["arg"].duplicate_state, "DISTINCT")

    def test_lineage_is_preserved_not_deleted(self):
        cands = [self._overlap("a", 100.0, 115.0, selection=92), self._overlap("b", 101.0, 116.0)]
        out = dedup(cands)
        self.assertEqual(len(out), 2)                       # nothing removed
        self.assertTrue(any(x.duplicate_state == "DUPLICATE_SUPPRESSED" for x in out))


if __name__ == "__main__":
    unittest.main()
