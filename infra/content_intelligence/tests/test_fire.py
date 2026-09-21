"""FIRE decision layer: confidence, ceiling, scores, gate, queue, regressions."""

from __future__ import annotations

import datetime as _dt
import sys
import unittest
from pathlib import Path

_SUITE = Path(__file__).resolve().parents[1]
_ROOT = _SUITE.parent
for p in (str(_SUITE), str(_ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

import content_intelligence.pipeline as pipeline_mod  # noqa: E402
from content_intelligence.fire import (  # noqa: E402
    FIRE_CONFIDENCE_MIN, FIRE_CONTENT_MIN, FIRE_MARKET_MIN, FIRE_SELECTION_MIN,
    LOW_ACTIVITY, LOW_CONFIDENCE, LOW_CONTENT, LOW_MARKET, LOW_SELECTION,
    METADATA_ONLY_CONFIDENCE_CEILING, METADATA_ONLY_CONTENT_CEILING,
    NO_IMMEDIATE_HOOK, NO_PAYOFF, WEAK_REACTION_OPPORTUNITY, WRONG_TENANT,
    DUPLICATE, evidence_confidence, fire_gate, recalibrate_content_score, selection_score,
)
from content_intelligence.moment_inspection import (  # noqa: E402
    InspectionEvidence, assess_activity, assess_payoff, assess_reaction_angle,
    assess_stop, first_look_windows, inspect_bounded, populate_from_inspection,
)
from content_intelligence.pipeline import (  # noqa: E402
    Pass1Config, Pass1Pipeline, production_review_queue,
)
from test_pipeline import (_Brief, _FakeProvider, _FakeStore, _FakeTopic,  # noqa: E402
                           _Record, _patched_fetch)

NOW = _dt.datetime(2026, 9, 21, 12, 0, tzinfo=_dt.timezone.utc)
META_ONLY = {"market_metrics": True, "age": True, "engagement": True, "preview": True,
             "spoken_context": True, "provenance": True, "timestamps": True}
FULL = dict(META_ONLY, visual_opening=True, action_activity=True,
            payoff_understanding=True, reaction_opportunity=True)


def _strong_evidence() -> InspectionEvidence:
    """Evidence that genuinely justifies a high score (not a threshold hack)."""
    return InspectionEvidence(
        window_seconds=3.0, visual_opening=True, preview_reference="https://cdn/x.jpg",
        caption_text="oh my god he fell off the stage and everyone screamed",
        spoken_hook="oh my god",
        signals={"motion": True, "scene_change": True, "audio_spike": True, "speech_onset": True},
        signal_window=(1.0, 3.0), timestamps=({"start_s": 1.0, "end_s": 3.0, "basis": "motion"},),
        provenance=("captions", "bounded_media_0_3s", "preview"))


def _weak_evidence() -> InspectionEvidence:
    return InspectionEvidence(window_seconds=3.0, visual_opening=True,
                              caption_text="welcome back to the stream", provenance=("captions",))


class ConfidenceTests(unittest.TestCase):
    def test_metadata_only_cannot_reach_the_fire_bar(self):
        r = evidence_confidence(platform="tiktok", evidence=META_ONLY)
        self.assertLess(r.score, FIRE_CONFIDENCE_MIN)
        self.assertLessEqual(r.score, METADATA_ONLY_CONFIDENCE_CEILING)
        self.assertEqual(r.ceiling_applied, "metadata_only_no_visual_opening")

    def test_ceiling_is_structural_even_with_otherwise_perfect_metadata(self):
        r = evidence_confidence(platform="youtube", evidence=dict(META_ONLY, timestamps=True))
        self.assertLess(r.score, 85)

    def test_strong_visual_evidence_reaches_the_bar(self):
        r = evidence_confidence(platform="tiktok", evidence=FULL)
        self.assertGreaterEqual(r.score, FIRE_CONFIDENCE_MIN)
        self.assertEqual(r.ceiling_applied, "")

    def test_weak_visual_evidence_stays_below(self):
        r = evidence_confidence(platform="tiktok", evidence={"market_metrics": True, "visual_opening": True})
        self.assertLess(r.score, FIRE_CONFIDENCE_MIN)

    def test_platform_aware_reddit_is_not_punished_for_missing_views(self):
        reddit = {"score_metrics": True, "age": True, "comments": True, "rank": True, "provenance": True}
        r = evidence_confidence(platform="reddit", evidence=reddit)
        self.assertGreater(r.score, 0)
        self.assertNotIn("market_metrics", r.missing_required)

    def test_creator_baseline_is_optional_not_required(self):
        r = evidence_confidence(platform="tiktok", evidence=FULL)
        self.assertNotIn("creator_baseline", r.missing_required)


class ScoreTests(unittest.TestCase):
    def test_metadata_only_content_is_capped(self):
        self.assertEqual(recalibrate_content_score(95, META_ONLY), METADATA_ONLY_CONTENT_CEILING)

    def test_real_evidence_raises_content_above_the_cap(self):
        self.assertGreater(recalibrate_content_score(78, FULL), METADATA_ONLY_CONTENT_CEILING)

    def test_high_market_boring_content_cannot_pass_selection(self):
        s = selection_score(market=92, content=30, tenant_fit_verdict="PASS",
                            reaction_opportunity=False, evidence_completeness=40, duplicate_risk=False)
        self.assertLess(s, FIRE_SELECTION_MIN)

    def test_good_content_without_market_cannot_pass_selection(self):
        s = selection_score(market=22, content=90, tenant_fit_verdict="PASS",
                            reaction_opportunity=True, evidence_completeness=90, duplicate_risk=False)
        self.assertLess(s, FIRE_SELECTION_MIN)

    def test_strong_pair_passes_selection(self):
        s = selection_score(market=88, content=86, tenant_fit_verdict="PASS",
                            reaction_opportunity=True, evidence_completeness=95, duplicate_risk=False)
        self.assertGreaterEqual(s, FIRE_SELECTION_MIN)


class FireGateTests(unittest.TestCase):
    def _passing(self, **over):
        kw = dict(market_score=80, content_score=85, selection=90, confidence=90,
                  market_gate_pass=True, tenant_fit_pass=True, immediate_hook=True,
                  action_activity=True, payoff_present=True, duplicate_risk=False,
                  reaction_opportunity=True)
        kw.update(over)
        return fire_gate(**kw)

    def test_all_conditions_pass(self):
        d = self._passing()
        self.assertTrue(d.passed, d.reasons)

    def test_thresholds_are_enforced_exactly(self):
        self.assertFalse(self._passing(market_score=FIRE_MARKET_MIN - 1).passed)
        self.assertFalse(self._passing(content_score=FIRE_CONTENT_MIN - 1).passed)
        self.assertFalse(self._passing(selection=FIRE_SELECTION_MIN - 1).passed)
        self.assertFalse(self._passing(confidence=FIRE_CONFIDENCE_MIN - 1).passed)

    def test_every_required_boolean_has_a_reason(self):
        cases = [("immediate_hook", NO_IMMEDIATE_HOOK), ("action_activity", LOW_ACTIVITY),
                 ("payoff_present", NO_PAYOFF), ("reaction_opportunity", WEAK_REACTION_OPPORTUNITY),
                 ("tenant_fit_pass", WRONG_TENANT)]
        for field, reason in cases:
            d = self._passing(**{field: False})
            self.assertFalse(d.passed, field)
            self.assertIn(reason, d.reasons, field)

    def test_duplicate_and_low_scores_reasons(self):
        self.assertIn(DUPLICATE, self._passing(duplicate_risk=True).reasons)
        self.assertIn(LOW_MARKET, self._passing(market_score=10).reasons)
        self.assertIn(LOW_CONTENT, self._passing(content_score=10).reasons)
        self.assertIn(LOW_SELECTION, self._passing(selection=10).reasons)
        self.assertIn(LOW_CONFIDENCE, self._passing(confidence=10).reasons)


class QueueTests(unittest.TestCase):
    def test_source_review_receives_fire_only(self):
        rows = [{"outcome": "standard_review", "source_selection_score": 90,
                 "source_selection_confidence": 0.8, "fire_passed": True},
                {"outcome": "standard_review", "source_selection_score": 88,
                 "source_selection_confidence": 0.8, "fire_passed": False}]
        out = production_review_queue(rows)
        self.assertEqual(len(out), 1)
        self.assertTrue(out[0]["fire_passed"])


class InspectionTests(unittest.TestCase):
    def test_window_plan_is_bounded(self):
        self.assertEqual(first_look_windows(), (3.0, 8.0, 12.0))

    def test_no_activity_credit_without_real_signals(self):
        empty = InspectionEvidence(caption_text="crazy viral funny moment")
        self.assertEqual(assess_activity(empty).verdict, "FAIL")
        self.assertEqual(assess_stop(empty).verdict, "FAIL")

    def test_metadata_words_cannot_create_a_hook(self):
        ev = InspectionEvidence(preview_reference="x", provider_hints={})
        self.assertFalse(assess_stop(ev).passed)

    def test_populate_leaves_unsupported_fields_unavailable(self):
        ev = InspectionEvidence(window_seconds=3.0, visual_opening=True,
                                caption_text="welcome back to the stream")
        a = {"stop": assess_stop(ev), "activity": assess_activity(ev), "setup": None,
             "escalation": None, "payoff": assess_payoff(ev),
             "reaction_angle": assess_reaction_angle(ev)}
        mc = pipeline_mod.__dict__ and None
        class _C:
            moment_evidence = {}
            evidence_depth = ""
        c = populate_from_inspection(_C(), ev, a)
        self.assertIsNone(c.moment_evidence["payoff"])
        self.assertIsNone(c.moment_evidence["action_activity"])


class PipelineRegressionTests(unittest.TestCase):
    """The known bad candidates must not reach a production card."""

    def _pipe(self):
        return Pass1Pipeline(Pass1Config(max_candidates=1, market_shortlist_threshold=0),
                             _Brief(), _FakeTopic(score=70), provider=_FakeProvider())

    def test_84_view_tiktok_dies_at_market_and_is_never_carded(self):
        pipe, store = self._pipe(), _FakeStore()
        rec = _Record(platform="tiktok", cid="tt84c")
        rec.observed_metrics = {"playCount": 84, "diggCount": 2}
        rec.publication_timestamp = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(hours=30)).isoformat()
        with _patched_fetch([]):
            r = pipe.run([rec], store)
        c = r.candidates[0]
        self.assertEqual(c.status, "filtered_market")
        self.assertFalse(c.fire_passed)
        self.assertEqual(r.scored, 0)

    def test_transcript_only_dramatic_story_cannot_reach_fire(self):
        """Interesting language, no visual opening -> confidence stays under 85."""
        pipe, store = self._pipe(), _FakeStore()
        rec = _Record(platform="youtube", title="wild fan interaction on stream", cid="dramaonly")
        with _patched_fetch([{"start_s": 0.0, "end_s": 4.0,
                              "text": "he screamed and everyone was shocked by what happened"}]):
            r = pipe.run([rec], store)
        c = r.candidates[0]
        self.assertFalse(c.fire_passed)
        self.assertLess(c.evidence_confidence, FIRE_CONFIDENCE_MIN)
        self.assertNotIn(LOW_CONFIDENCE, ())  # reason asserted below
        self.assertTrue(any(x in c.fire_reasons for x in (LOW_CONFIDENCE, NO_IMMEDIATE_HOOK, LOW_CONTENT)),
                        c.fire_reasons)

    def test_strong_candidate_with_real_evidence_can_fire(self):
        # Market must genuinely clear the FIRE bar, so this candidate gets a
        # market assessment of 85 (not the 70 used by the weak-case fixtures).
        pipe = Pass1Pipeline(Pass1Config(max_candidates=1, market_shortlist_threshold=0),
                            _Brief(), _FakeTopic(score=85), provider=_FakeProvider())
        store = _FakeStore()
        rec = _Record(platform="youtube", title="wild fan interaction on stream", cid="strong01")
        rec.thumbnail_reference = "https://cdn.example/cover.jpg"
        ev = _strong_evidence()
        # inject the TRUE bounded hooks: preview (0-3s artifact) + signals on that
        # bounded artifact. No real download happens in tests.
        from test_bounded_preview import _Recorder
        rec_prev = _Recorder(signals_by_end={3.0: [
            {"basis": k, "start_s": 1.0, "end_s": 3.0}
            for k in ("motion", "scene_change", "audio_spike")]})
        pipe._inspection_preview = rec_prev
        pipe._inspection_analyze = rec_prev.signals
        pipe._inspection_captions = lambda url: ([{"start_s": 0.0, "end_s": 3.0,
                                                   "text": ev.caption_text}], "auto_generated")
        with _patched_fetch([{"start_s": 0.0, "end_s": 3.0, "text": ev.caption_text}]):
            r = pipe.run([rec], store)
        c = r.candidates[0]
        self.assertGreaterEqual(c.evidence_confidence, FIRE_CONFIDENCE_MIN, c.fire_reasons)
        self.assertGreaterEqual(c.content_score_recalibrated, FIRE_CONTENT_MIN)
        self.assertTrue(c.fire_passed, c.fire_reasons)
        self.assertIsNotNone(c.moment_evidence)
        self.assertIsNotNone(c.moment_candidate)


if __name__ == "__main__":
    unittest.main()
