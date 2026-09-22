"""Run-level relative ranking + dedup/diversity, without touching absolute FIRE."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS

_SUITE = Path(__file__).resolve().parents[1]
_ROOT = _SUITE.parent
for p in (str(_SUITE), str(_ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

from content_intelligence.pipeline import (  # noqa: E402
    Pass1Config, Pass1Pipeline, apply_run_level_ranking, production_review_queue,
)
from test_pipeline import (_Brief, _FakeProvider, _FakeStore, _FakeTopic,  # noqa: E402
                           _Record, _patched_fetch)


def cap(s, t, d=4.0):
    return {"start_s": float(s), "end_s": float(s) + d, "text": t}


def cand(cid, start, end, *, market=85, content=88, selection=90, confidence=90,
         fire=True, complete=True, hook=True, activity=True, payoff=True, reaction=True,
         mtype="funny_awkward"):
    return NS(platform_content_id=cid,
              boundary_plan={"event_timestamp": start + 2.0,
                             "recommended_clip_start": float(start),
                             "recommended_clip_end": float(end)},
              moment_evidence={"hook_0_3s": "hook" if hook else None,
                               "action_activity": 0.5 if activity else None,
                               "payoff": "payoff" if payoff else None,
                               "reaction_angle": "angle" if reaction else None},
              completeness={"completeness_verdict": "PASS" if complete else "FAIL"},
              moment_type={"moment_type": mtype},
              market_score=market, content_score_recalibrated=content,
              selection_score=selection, evidence_confidence=confidence,
              fire_passed=fire, source_relative_rank=None, source_candidate_count=0,
              duplicate_state="", rank_evidence=None)


class RunLevelRankingTests(unittest.TestCase):
    def test_ranks_are_assigned_to_every_candidate(self):
        cs = [cand("a", 100.0, 115.0), cand("b", 900.0, 915.0, selection=92)]
        rankings = apply_run_level_ranking(cs)
        self.assertEqual(len(rankings), 2)
        for c in cs:
            self.assertIsNotNone(c.source_relative_rank)
            self.assertEqual(c.source_candidate_count, 2)
            self.assertTrue(c.duplicate_state)
            self.assertIsInstance(c.rank_evidence, dict)

    def test_near_identical_versions_of_one_event_are_suppressed(self):
        cs = [cand("a", 1118.0, 1135.0, selection=92),
              cand("b", 1119.0, 1136.0, selection=88)]
        apply_run_level_ranking(cs)
        states = {c.platform_content_id: c.duplicate_state for c in cs}
        self.assertEqual(states["a"], "PRIMARY")
        self.assertEqual(states["b"], "DUPLICATE_SUPPRESSED")
        self.assertEqual(cs[1].rank_evidence.get("suppressed_by"),
                         cs[0].rank_evidence.get("suppressed_by") or cs[1].rank_evidence.get("suppressed_by"))
        # lineage preserved: both objects remain, nothing deleted
        self.assertEqual(len(cs), 2)

    def test_separate_moments_are_preserved_as_distinct(self):
        cs = [cand("fall", 1118.0, 1133.0), cand("arg", 1178.0, 1200.0)]
        apply_run_level_ranking(cs)
        self.assertEqual({c.duplicate_state for c in cs}, {"DISTINCT"})

    def test_ranking_does_not_change_fire(self):
        cs = [cand("strong", 100.0, 115.0, selection=93),
              cand("weak", 900.0, 915.0, selection=70, content=60, fire=False)]
        apply_run_level_ranking(cs)
        by = {c.platform_content_id: c for c in cs}
        self.assertTrue(by["strong"].fire_passed)
        self.assertFalse(by["weak"].fire_passed)      # relative rank never promotes
        self.assertEqual(by["strong"].source_relative_rank, 1)

    def test_review_queue_excludes_suppressed_duplicates(self):
        rows = [{"outcome": "standard_review", "source_selection_score": 90,
                 "source_selection_confidence": 0.9, "fire_passed": True,
                 "duplicate_state": "PRIMARY"},
                {"outcome": "standard_review", "source_selection_score": 90,
                 "source_selection_confidence": 0.9, "fire_passed": True,
                 "duplicate_state": "DUPLICATE_SUPPRESSED"},
                {"outcome": "standard_review", "source_selection_score": 90,
                 "source_selection_confidence": 0.9, "fire_passed": False,
                 "duplicate_state": "DISTINCT"}]
        out = production_review_queue(rows)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["duplicate_state"], "PRIMARY")


class RunLevelWiringTests(unittest.TestCase):
    def test_pipeline_run_populates_ranking_fields(self):
        pipe = Pass1Pipeline(Pass1Config(max_candidates=2, market_shortlist_threshold=0),
                             _Brief(), _FakeTopic(score=85), provider=_FakeProvider())
        from test_bounded_preview import _Recorder
        rp = _Recorder(signals_by_end={3.0: [{"basis": "motion", "start_s": 1.0, "end_s": 3.0}]})
        pipe._inspection_preview = rp
        pipe._inspection_analyze = rp.signals
        pipe._inspection_captions = lambda u: ([], "none")
        recs = [_Record(platform="youtube", title="wild fan interaction one", cid="r1"),
                _Record(platform="youtube", title="wild fan interaction two", cid="r2")]
        segs = [cap(0, "oh my god look at this."), cap(4, "and then everyone laughed.")]
        with _patched_fetch(segs):
            res = pipe.run(recs, _FakeStore())
        self.assertTrue(res.candidates)
        for c in res.candidates:
            self.assertIsNotNone(c.source_relative_rank)
            self.assertGreaterEqual(c.source_candidate_count, 1)
            self.assertTrue(c.duplicate_state)


if __name__ == "__main__":
    unittest.main()
