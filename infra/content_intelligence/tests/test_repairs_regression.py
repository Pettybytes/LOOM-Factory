"""Regression coverage for the Segment 5 repair chain.

Each test pins ONE previously-broken behaviour and fails if it regresses:
  1. bounded preview artifacts must SURVIVE until cleanup() (the analyzer
     used to receive a path that fetch had already deleted);
  2. native Shorts keep their complete-asset boundaries [0, duration] — a
     120 s short must never be rebuilt into a 15 s guess;
  3. boundary repair extends to the NEXT REAL boundary, never blind +4 s
     steps;
  4. the authoritative candidate (FIRE + completeness + ranking) is persisted
     AFTER fire/ranking, not before;
  5. populate_from_inspection tolerates a PASS with value=None and a missing
     emotion_event_type assessment (previously crashed on hook.value.get).
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_REPO_ROOT = Path(__file__).resolve().parents[3]
for _p in (str(_REPO_ROOT / "infra"), str(_REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import datetime as _dt  # noqa: E402
import content_intelligence.pipeline as pipeline  # noqa: E402
from content_intelligence.bounded_preview import BoundedPreview, cleanup, fetch_bounded_preview  # noqa: E402
from content_intelligence.moment_inspection import (  # noqa: E402
    FieldAssessment, InspectionEvidence, populate_from_inspection,
)
from content_intelligence.moment_quality import (  # noqa: E402
    C_INSUFFICIENT_CONTEXT, C_MISSING_PAYOFF, T_FALL, T_PHYSICAL,
    build_boundary_plan, completeness_gate, repair_boundaries,
)
from content_intelligence.pipeline import Pass1Config, Pass1Pipeline  # noqa: E402
from test_pipeline import (_Brief, _FakeProvider, _FakeTopic, _FakeTopicResult,
                           _Record, _patched_fetch)  # noqa: E402


class PreviewArtifactRetentionTests(unittest.TestCase):
    """fetch_bounded_preview must hand the caller a LIVE artifact."""

    @staticmethod
    def _runner_writing_file(cmd, **kw):
        """Fake yt-dlp: write a real file where -o points, like the tool does."""
        out_arg = cmd[cmd.index("-o") + 1] if "-o" in cmd else ""
        out = out_arg.replace("%(id)s.%(ext)s", "vid123.mp4") if out_arg else ""
        if out:
            Path(out).parent.mkdir(parents=True, exist_ok=True)
            Path(out).write_bytes(b"fake-bounded-media-bytes")
        return type("P", (), {"stderr": "", "returncode": 0})()

    def test_artifact_exists_after_return_and_cleanup_removes_it(self):
        prev = fetch_bounded_preview("https://www.youtube.com/watch?v=abc", end=3,
                                     runner=self._runner_writing_file)
        self.assertTrue(prev.available)
        self.assertTrue(os.path.exists(prev.artifact_path),
                        "artifact was deleted before analysis could read it")
        cleanup(prev)
        self.assertFalse(os.path.exists(prev.artifact_path))

    def test_cleanup_never_touches_foreign_directories(self):
        with tempfile.TemporaryDirectory() as td:
            foreign = Path(td) / "x.mp4"
            foreign.write_bytes(b"x")
            prev = BoundedPreview(0.0, 3.0, 0.0, 3.0, str(foreign), 1, "youtube",
                                  "section_download")
            cleanup(prev)
            self.assertTrue(os.path.exists(foreign),
                            "cleanup must not delete paths outside loom-inspect-* dirs")


class NativeShortCompleteAssetTests(unittest.TestCase):
    """A native Short IS the moment: [0, duration] by construction."""

    def _pipe(self):
        return Pass1Pipeline(Pass1Config(max_candidates=1, market_shortlist_threshold=0),
                             _Brief(), _FakeTopic(score=85), provider=_FakeProvider())

    def test_120s_short_keeps_complete_asset_boundaries(self):
        from test_bounded_preview import _Recorder
        pipe = self._pipe()
        rec = _Record(platform="youtube", title="wild fan interaction", cid="short120")
        rec.duration_seconds = 120
        rec_prev = _Recorder(signals_by_end={3.0: [
            {"basis": "motion", "start_s": 1.0, "end_s": 3.0},
            {"basis": "audio_spike", "start_s": 1.0, "end_s": 3.0}]})
        pipe._inspection_preview = rec_prev
        pipe._inspection_analyze = rec_prev.signals
        pipe._inspection_captions = lambda url: ([
            {"start_s": 0.0, "end_s": 3.0, "text": "oh my god he fell!"}], "auto_generated")
        with _patched_fetch([{"start_s": 0.0, "end_s": 3.0, "text": "oh my god he fell!"}]):
            r = pipe.run([rec], _CapturingStore())
        c = r.candidates[0]
        self.assertEqual(c.source_mode, "source_as_moment")
        self.assertIsNotNone(c.boundary_plan)
        self.assertEqual(c.boundary_plan["recommended_clip_start"], 0.0)
        self.assertEqual(c.boundary_plan["recommended_clip_end"], 120.0)
        self.assertIn("complete_asset", c.boundary_plan["provenance"])
        self.assertEqual(c.repair_attempts, (),
                         "a native short must never be blind-repaired into a truncated guess")


class EvidenceRepairTests(unittest.TestCase):
    """Repair extends to the NEXT REAL boundary — never blind fixed steps."""
    def test_repair_lands_on_a_real_segment_boundary(self):
        # natural_end stops at the mid-sentence cut (3.0 s); the only REAL
        # boundary after it is the 5.0 s sentence end. A blind +4 s step could
        # never land on it.
        segs = [{"start_s": 0.0, "end_s": 2.5, "text": "he fell out of the chair"},
                {"start_s": 2.5, "end_s": 5.0, "text": "that was the whole thing."}]
        plan = build_boundary_plan(T_FALL, 0.0, segs)
        first = completeness_gate(plan, segs)
        self.assertFalse(first.passed)
        repaired, gate, attempts = repair_boundaries(plan, first, segs)
        self.assertTrue(gate.passed, gate.reasons)
        self.assertEqual(repaired.recommended_clip_end, 5.0)
        self.assertTrue(all(a["extended_s"] > 0 for a in attempts))

    def test_repair_stops_when_no_further_boundary_exists(self):
        segs = [{"start_s": 0.0, "end_s": 2.0, "text": "he fell"}]
        plan = build_boundary_plan(T_PHYSICAL, 0.0, segs)
        _p, gate, attempts = repair_boundaries(plan, completeness_gate(plan, segs), segs)
        self.assertFalse(gate.passed)
        self.assertTrue(any(a.get("verdict") == "no_further_boundary" for a in attempts))


class MediaEvidenceCompletenessTests(unittest.TestCase):
    """The caption-only gate must never contradict real media understanding."""

    @staticmethod
    def _short_plan():
        """The pipeline's native-Short plan: the complete asset [0, duration]."""
        from content_intelligence.moment_quality import BoundaryPlan, T_UNKNOWN
        return BoundaryPlan(
            event_timestamp=0.0, recommended_clip_start=0.0,
            recommended_clip_end=8.0, start_reason="native_short_starts_at_zero",
            end_reason="native_short_complete_asset", moment_type=T_UNKNOWN,
            provenance=("source_as_moment", "complete_asset"))

    def test_captionless_short_with_real_evidence_passes(self):
        plan = self._short_plan()
        signals = [{"basis": "audio_spike", "start_s": 0.0, "end_s": 8.0},
                   {"basis": "visual_motion", "start_s": 0.0, "end_s": 8.0},
                   {"basis": "scene_cut", "start_s": 0.0, "end_s": 8.0}]
        gate = completeness_gate(plan, [], signals=signals,
                                 media_evidence={"payoff_understanding": True,
                                                 "context_understanding": True})
        self.assertTrue(gate.passed, gate.reasons)

    def test_media_model_understanding_alone_carries_context_and_payoff(self):
        plan = self._short_plan()
        gate = completeness_gate(plan, [], signals=[],
                                 media_evidence={"payoff_understanding": True,
                                                 "context_understanding": True})
        self.assertTrue(gate.passed, gate.reasons)

    def test_captionless_short_without_evidence_still_fails_honestly(self):
        plan = self._short_plan()
        gate = completeness_gate(plan, [], signals=[])
        self.assertFalse(gate.passed)
        self.assertIn(C_MISSING_PAYOFF, gate.reasons)
        self.assertIn(C_INSUFFICIENT_CONTEXT, gate.reasons)


class AuthoritativePersistenceTests(unittest.TestCase):
    """The queue-facing record must carry the FINAL pipeline state."""

    def test_candidate_persisted_after_fire_and_ranking(self):
        from test_bounded_preview import _Recorder
        pipe = Pass1Pipeline(Pass1Config(max_candidates=1, market_shortlist_threshold=0),
                             _Brief(), _FakeTopic(score=85), provider=_FakeProvider())
        store = _CapturingStore()
        rec = _Record(platform="youtube", title="wild fan interaction on stream", cid="persist01")
        rec.duration_seconds = 60
        rec_prev = _Recorder(signals_by_end={3.0: [
            {"basis": "motion", "start_s": 1.0, "end_s": 3.0},
            {"basis": "scene_change", "start_s": 1.0, "end_s": 3.0},
            {"basis": "audio_spike", "start_s": 1.0, "end_s": 3.0}]})
        pipe._inspection_preview = rec_prev
        pipe._inspection_analyze = rec_prev.signals
        pipe._inspection_captions = lambda url: ([
            {"start_s": 0.0, "end_s": 3.0, "text": "oh my god he fell off the stage and everyone screamed."}],
            "auto_generated")
        with _patched_fetch([
                {"start_s": 0.0, "end_s": 3.0,
                 "text": "oh my god he fell off the stage and everyone screamed."}]):
            r = pipe.run([rec], store)
        self.assertEqual(r.candidates[0].status, "scored")
        self.assertEqual(len(store.candidates), 1)
        stored = list(store.candidates.values())[0]
        self.assertIsNotNone(stored.fire_passed)
        self.assertIsNotNone(stored.completeness)
        self.assertIsNotNone(stored.boundary_plan)
        self.assertTrue(stored.duplicate_state)
        self.assertEqual(len(store.selection_fire), 1)
        sel = list(store.selection_fire.values())[0]
        self.assertIsNotNone(sel.fire_passed)
        self.assertIsNotNone(sel.evidence_confidence)


class InspectionPopulationSafetyTests(unittest.TestCase):
    """populate_from_inspection must never crash on honest evidence shapes."""

    class _Cand:
        moment_evidence = {}
        evidence_depth = ""
        moment_start = None
        moment_end = None
        moment_duration = None
        source_duration = None

    def test_hook_pass_with_none_value_does_not_crash(self):
        ev = InspectionEvidence(visual_opening=True, signals={"motion": True, "audio_spike": True},
                                signal_window=(0.0, 3.0))
        assessments = {
            "stop": FieldAssessment("PASS", "immediate_activity_and_hook_at_open",
                                    ("5b_signals",), None),   # value=None is legal
            "activity": FieldAssessment("PASS", "multiple_activity_signals",
                                        ("5b_signals",), {"strength": 0.5}),
            "setup": FieldAssessment("AMBIGUOUS", "no_context_text_to_understand_setup", ("none",)),
            "escalation": FieldAssessment("AMBIGUOUS", "activity_present_escalation_unclear",
                                          ("5b_signals",), {"strength": 0.5}),
            "payoff": FieldAssessment("PASS", "payoff_cue:fell", ("captions",)),
            "reaction_angle": FieldAssessment("PASS", "commentable_event:fell",
                                              ("captions", "5b_signals"),
                                              {"angle": "react to the fall"}),
        }
        cand = self._Cand()
        populate_from_inspection(cand, ev, assessments)   # must not raise
        self.assertEqual(cand.moment_evidence["hook_0_3s"],
                         "immediate_activity_and_hook_at_open")

    def test_missing_emotion_event_type_assessment_is_tolerated(self):
        ev = InspectionEvidence(visual_opening=True, signals={"motion": True},
                                signal_window=(0.0, 3.0))
        assessments = {
            "stop": FieldAssessment("AMBIGUOUS", "activity_without_clear_hook", ("5b_signals",)),
            "activity": FieldAssessment("PASS", "multiple_activity_signals",
                                        ("5b_signals",), {"strength": 0.5}),
            "setup": FieldAssessment("AMBIGUOUS", "no_context_text_to_understand_setup", ("none",)),
            "escalation": FieldAssessment("AMBIGUOUS", "activity_present_escalation_unclear", ("5b_signals",)),
            "payoff": FieldAssessment("AMBIGUOUS", "impact_like_signal_without_semantic_payoff", ("5b_signals",)),
            "reaction_angle": FieldAssessment("FAIL", "no_clear_reaction_entry_point", ("none",)),
        }
        cand = self._Cand()
        populate_from_inspection(cand, ev, assessments)   # must not raise
        self.assertIsNone(cand.moment_evidence["emotion_event_type"])


class _CapturingStore:
    """Records the authoritative persistence calls (mirrors ContentStoreSupabase)."""

    def __init__(self):
        self.candidates = {}
        self.selection_fire = {}
        self.assessments = {}
        self.selections = {}

    def upsert_assessment(self, a):
        self.assessments[a.idempotency_key] = a

    def upsert_selection(self, s):
        self.selections[s.idempotency_key] = s

    def upsert_candidate(self, c):
        self.candidates[c.duplicate_fingerprint] = c

    def upsert_selection_fire(self, c):
        self.selection_fire[c.duplicate_fingerprint] = c


class _EvidenceAwareTopic(_FakeTopic):
    """Raises the market score only when the record carries grounded content
    evidence (mirrors the real TopicIntelligence content-first behaviour)."""

    def __init__(self, base: int = 62, delta: int = 20):
        super().__init__(base)
        self.delta = delta

    def assess(self, rec, brief, competition_count=0):
        me = getattr(rec, "moment_evidence", None) or {}
        if me.get("content_worthiness_score") is not None:
            return _FakeTopicResult(min(100, self.score + self.delta), self.confidence)
        return _FakeTopicResult(self.score, self.confidence)


class EvidenceInformedMarketTests(unittest.TestCase):
    """FIRE market must be recomputed AFTER real content evidence exists."""

    def test_market_reassessed_with_grounded_content_score(self):
        from test_bounded_preview import _Recorder
        pipe = Pass1Pipeline(Pass1Config(max_candidates=1, market_shortlist_threshold=0),
                             _Brief(), _EvidenceAwareTopic(base=62, delta=20),
                             provider=_FakeProvider())
        store = _CapturingStore()
        rec = _Record(platform="youtube", title="wild fan interaction", cid="mkt01")
        rec.duration_seconds = 60
        rec_prev = _Recorder(signals_by_end={3.0: [
            {"basis": "motion", "start_s": 1.0, "end_s": 3.0},
            {"basis": "audio_spike", "start_s": 1.0, "end_s": 3.0}]})
        pipe._inspection_preview = rec_prev
        pipe._inspection_analyze = rec_prev.signals
        pipe._inspection_captions = lambda url: ([{"start_s": 0.0, "end_s": 3.0,
                                                   "text": "watch this fall!"}], "auto_generated")
        with _patched_fetch([{"start_s": 0.0, "end_s": 3.0, "text": "watch this fall!"}]):
            r = pipe.run([rec], store)
        c = r.candidates[0]
        self.assertEqual(c.status, "scored")
        self.assertEqual(c.market_score, 62)          # pre-evidence shortlist score
        self.assertEqual(c.market_score_final, 82)    # evidence-informed final
        self.assertGreater(c.market_score_final, c.market_score)
        # The persisted authoritative record carries the FINAL market score.
        persisted = list(store.candidates.values())[0]
        self.assertEqual(getattr(persisted, "market_score_final", None), 82)

    def test_market_final_falls_back_when_reassessment_unavailable(self):
        from test_bounded_preview import _Recorder
        pipe = Pass1Pipeline(Pass1Config(max_candidates=1, market_shortlist_threshold=0),
                             _Brief(), _FakeTopic(score=85), provider=_FakeProvider())
        rec = _Record(platform="youtube", title="wild fan interaction", cid="mkt02")
        rec.duration_seconds = 60
        rec_prev = _Recorder(signals_by_end={3.0: [
            {"basis": "motion", "start_s": 1.0, "end_s": 3.0}]})
        pipe._inspection_preview = rec_prev
        pipe._inspection_analyze = rec_prev.signals
        with _patched_fetch([{"start_s": 0.0, "end_s": 3.0, "text": "watch this fall!"}]):
            r = pipe.run([rec], _CapturingStore())
        c = r.candidates[0]
        self.assertEqual(c.market_score_final, 85)


class NormalizedMomentEvidenceTests(unittest.TestCase):
    """normalize_signals carries clip-level moment evidence into topic scoring."""

    def test_moment_evidence_passthrough(self):
        import sys
        sys.path.insert(0, str(_REPO_ROOT / "infra"))
        from discovery.discovery_record import DiscoveryRecord
        from discovery.normalization import normalize_signals
        rec = DiscoveryRecord(
            tenant_id="dreemzy", source_brief_version="2", profile_id="p",
            platform="youtube", canonical_url="https://www.youtube.com/watch?v=x",
            platform_content_id="x", title="crazy fall",
            creator_channel_identity="c", creator_channel_url="https://y",
            publication_timestamp=None,
            observed_metrics={"views": 9000}, metric_observation_timestamp="",
            metric_history=[], discovery_query="q", discovery_reason="r",
            evidence_confidence=0.9, provenance_rights_evidence="yt-dlp",
            duplicate_fingerprint="f", global_fingerprint="f",
            processing_eligibility=True, qualification_status="pending",
            connector_name="t", connector_version="1", duration_seconds=30)
        rec.moment_evidence = {"content_worthiness_score": 88, "payoff": "revealed"}
        sig = normalize_signals(rec)
        self.assertEqual(sig.moment_evidence["content_worthiness_score"], 88)


if __name__ == "__main__":
    unittest.main()
