"""Complete inspected windows survive ranking and real store serialization."""

import json
import sys
import unittest
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from unittest import mock

for p in (Path(__file__).resolve().parents[2], Path(__file__).parent):
    sys.path.insert(0, str(p))

from content_intelligence.pipeline import Pass1Config, Pass1Pipeline, apply_run_level_ranking
from content_intelligence.moment_locator import MomentZone
from content_intelligence.moment_quality import BoundaryPlan
from content_intelligence.store_supabase import ContentStoreSupabase
from discovery.source_brief import SourceBrief
from test_bounded_preview import _Recorder
from test_pipeline import _FakeProvider, _FakeTopic, _Record, _patched_fetch
from test_tenant_isolation_acceptance import _brief_data


def moment(start=0, end=3, score=90):
    return {"start": start, "event_timestamp": start + 1,
        "what_happens": f"A stumble and recovery at {start} seconds",
        "moment_type": "funny_awkward", "why_reaction_worthy": "An unexpected recovery",
        "hook_0_3s": "The stumble begins immediately", "payoff": "The performer recovers",
        "reaction_angle": "React to the recovery", "start_evidence": "Visible setup starts",
        "content_score": score,
        "boundaries": [{"end": end, "complete": True, "evidence": "Recovery resolves"}]}


def media(moments):
    return {"scores": {"hook": 90, "payoff": 90}, "content_worthiness_score": 90,
        "qualification": {"intent_match": True, "nudity": False, "explicit_sex": False,
            "adult_status": "confirmed_adults", "adult_evidence": "Identified adult performers",
            "concept_evidence": [{"concept": "beach", "observation": "Beach setting"},
                                 {"concept": "bikini", "observation": "Adult swimwear"}]},
        "moments": deepcopy(moments), "usage": {"cost_usd": 0.002}}


class MultipleInspectedMomentTests(unittest.TestCase):
    def run_source(self, moments=None, *, tenant="tenant-alpha", profile="acceptance-alpha",
                   signals=None, cid="same-source", duration=12, actual_coverage=None, human_review=False):
        data = _brief_data()
        data.update(tenant_id=tenant, profile_id=profile)
        brief = SourceBrief.from_dict(data)
        if human_review:
            brief.mining_requirements['review_policy'] = {'mode': 'human_curated', 'media_first': True}
        record = _Record(fp="original-source-fingerprint", title="Funny beach bikini moments", cid=cid)
        record.tenant_id, record.profile_id = tenant, profile
        record.creator_channel_identity = "Verified creator"
        record.duration_seconds = duration
        record.thumbnail_reference = "https://example.invalid/actual-source-thumbnail.jpg"
        pipe = Pass1Pipeline(Pass1Config(), brief, _FakeTopic(90), provider=_FakeProvider())
        if signals is None:
            signals = [{"basis": basis, "start_s": start, "end_s": end}
                for start, end in ((0, 3), (7, 10))
                for basis in ("motion", "scene_change", "audio_spike")]
        class VerifiedRecorder(_Recorder):
            terminal_flags = []

            def __call__(self, url, *, start=0.0, end=3.0, **kwargs):
                self.terminal_flags.append(kwargs.get("allow_terminal_partial"))
                result = super().__call__(url, start=start, end=end, **kwargs)
                return (replace(result, inspected_end=float(actual_coverage))
                        if actual_coverage is not None else result)

        preview = VerifiedRecorder(signals_by_end={float(int(duration)): signals})
        pipe._inspection_preview, pipe._inspection_analyze = preview, preview.signals
        pipe._inspection_contiguous_preview = preview
        pipe._inspection_captions = lambda url: ([], "none")
        pipe._media_assess = mock.Mock(return_value=media(
            moments if moments is not None else [moment(), moment(7, 10, 99)]))
        store = ContentStoreSupabase("https://example.invalid", "offline-fixture")
        store._request = mock.Mock(return_value=[])
        with _patched_fetch([]):
            result = pipe.run([record], store)
        if human_review:
            self.assertEqual(pipe.provider.calls, 0, 'No redundant metadata LLM call before actual media')
        self.assertEqual(pipe._media_assess.call_count, 1)
        self.assertEqual(preview.ends, [float(duration)])
        self.assertEqual(preview.terminal_flags, [True])
        if actual_coverage is not None:
            self.assertEqual(pipe._media_assess.call_args.args[0].window_seconds, actual_coverage)
        for candidate in result.candidates:
            json.dumps(candidate.__dict__, allow_nan=False)
        return result, store, record

    def test_human_review_persists_real_moments_without_manufacturing_fire(self):
        result, store, _ = self.run_source([moment(score=65)], human_review=True)
        c = result.candidates[0]
        self.assertFalse(c.fire_passed)
        self.assertEqual(c.content_score_recalibrated, 65)
        self.assertEqual(c.moment_evidence['review_context']['review_mode'], 'human_curated')
        self.assertEqual(c.moment_evidence['review_context']['review_rank'], 1)
        written = [call.args[2] for call in store._request.call_args_list
                   if call.args[1].startswith('loom_source_candidates?')]
        self.assertEqual(written[-1]['moment_evidence']['review_context']['review_count'], 1)

    def test_two_complete_moments_rank_independently_without_more_model_work(self):
        result, _, _ = self.run_source()
        self.assertEqual(len(result.candidates), 2)
        by_start = {c.boundary_plan["recommended_clip_start"]: c for c in result.candidates}
        self.assertEqual(by_start[7].source_relative_rank, 1)
        self.assertEqual(by_start[0].source_relative_rank, 2)
        for candidate in result.candidates:
            self.assertTrue(candidate.fire_passed, candidate.fire_reasons)
            self.assertEqual(candidate.source_candidate_count, 2)
            self.assertEqual(candidate.duplicate_state, "DISTINCT")
            self.assertEqual(len(candidate.moment_evidence["inspected_moments"]), 1)
        self.assertAlmostEqual(result.total_cost_usd, 0.003)
        self.assertAlmostEqual(sum(c.cost_usd for c in result.candidates), 0.003)

    def test_real_store_rows_have_distinct_keys_and_preserved_source_lineage(self):
        result, store, record = self.run_source()
        rows = [call.args[2] for call in store._request.call_args_list
                if call.args[1].startswith("loom_source_candidates?")]
        selections = [call.args[2] for call in store._request.call_args_list
                      if call.args[1].startswith("loom_source_selections?")
                      and "fire_passed" in call.args[2]]
        self.assertEqual(len(rows), 2)
        self.assertEqual(len({r["duplicate_fingerprint"] for r in rows}), 2)
        self.assertEqual(len({r["idempotency_key"] for r in selections}), 2)
        self.assertEqual({r["duplicate_fingerprint"] for r in rows},
                         {r["duplicate_fingerprint"] for r in selections})
        for row in rows:
            self.assertEqual(row["moment_evidence"]["lineage"]["source_duplicate_fingerprint"],
                             record.duplicate_fingerprint)
            self.assertEqual(row["canonical_url"], record.canonical_url)
            self.assertTrue(row["fire_passed"])
            self.assertEqual(row["source_candidate_count"], 2)

    def test_identity_is_replay_order_stable_and_tenant_profile_window_safe(self):
        first, _, _ = self.run_source([moment(), moment(7, 10, 99)])
        replay, _, _ = self.run_source([moment(7, 10, 99), moment()])
        identities = lambda result: {c.duplicate_fingerprint for c in result.candidates}
        self.assertEqual(identities(first), identities(replay))
        for changes in ({"tenant": "tenant-beta"}, {"profile": "acceptance-beta"},
                        {"moments": [moment(0, 2.8), moment(7, 9.8, 99)]}):
            other, _, _ = self.run_source(**changes)
            self.assertFalse(identities(first) & identities(other), changes)

    def test_weaker_complete_moment_does_not_inherit_stronger_content_or_fire(self):
        result, _, _ = self.run_source([moment(score=50), moment(7, 10, 99)])
        by_start = {c.boundary_plan["recommended_clip_start"]: c for c in result.candidates}
        self.assertFalse(by_start[0].fire_passed)
        self.assertIn("LOW_CONTENT", by_start[0].fire_reasons)
        self.assertIsNone(by_start[0].source_relative_rank)
        self.assertEqual(by_start[7].source_candidate_count, 1)

    def test_signals_outside_a_moment_cannot_supply_its_activity(self):
        first_only = [{"basis": basis, "start_s": 0, "end_s": 3}
                      for basis in ("motion", "scene_change", "audio_spike")]
        result, _, _ = self.run_source(signals=first_only)
        by_start = {c.boundary_plan["recommended_clip_start"]: c for c in result.candidates}
        self.assertTrue(by_start[0].fire_passed)
        self.assertFalse(by_start[7].fire_passed)
        self.assertIn("LOW_ACTIVITY", by_start[7].fire_reasons)
        self.assertIsNone(by_start[7].moment_evidence["action_activity"])

    def test_incomplete_or_out_of_inspected_range_moments_never_materialize(self):
        incomplete = moment(7, 10)
        incomplete["boundaries"][0]["complete"] = False
        result, _, _ = self.run_source([moment(), incomplete, moment(15, 18)])
        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(result.candidates[0].boundary_plan["recommended_clip_start"], 0)

    def test_no_complete_moment_keeps_rejected_source_with_no_selected_window(self):
        result, _, _ = self.run_source([])
        self.assertEqual(len(result.candidates), 1)
        self.assertFalse(result.candidates[0].fire_passed)
        self.assertIsNone(result.candidates[0].boundary_plan)
        self.assertIsNone(result.candidates[0].source_relative_rank)

    def test_same_source_duplicate_window_deduplicates_not_different_sources(self):
        same, _, _ = self.run_source([moment(), moment(0.2, 3.2, 99)])
        self.assertEqual({c.duplicate_state for c in same.candidates},
                         {"PRIMARY", "DUPLICATE_SUPPRESSED"})
        first, _, _ = self.run_source([moment()], cid="source-a")
        second, _, _ = self.run_source([moment()], cid="source-b")
        both = first.candidates + second.candidates
        apply_run_level_ranking(both, strict=True)
        self.assertEqual({c.duplicate_state for c in both}, {"DISTINCT"})
        self.assertTrue(all(c.source_candidate_count == 2 for c in both))

    def test_native_87s_inspects_actual_late_event_not_just_first_12s(self):
        signals = [{"basis": basis, "start_s": 70, "end_s": 82}
                   for basis in ("motion", "scene_change", "audio_spike")]
        result, _, _ = self.run_source([moment(70, 82, 95)], signals=signals, duration=87)
        selected = result.candidates[0]
        self.assertTrue(selected.fire_passed, selected.fire_reasons)
        self.assertEqual(selected.boundary_plan["recommended_clip_start"], 70)
        self.assertEqual(selected.boundary_plan["recommended_clip_end"], 82)
        self.assertEqual(selected.moment_evidence["lineage"]["inspection_end"], 87)

    def test_native_18s_keeps_later_complete_moment_even_with_weak_complete_opening(self):
        signals = [{"basis": basis, "start_s": start, "end_s": end}
                   for start, end in ((0, 3), (13, 18))
                   for basis in ("motion", "scene_change", "audio_spike")]
        result, _, _ = self.run_source([moment(score=40), moment(13, 18, 95)],
                                       signals=signals, duration=18)
        by_start = {c.boundary_plan["recommended_clip_start"]: c for c in result.candidates}
        self.assertFalse(by_start[0].fire_passed)
        self.assertTrue(by_start[13].fire_passed)
        self.assertEqual(by_start[13].source_relative_rank, 1)

    def test_rounded_native_duration_does_not_claim_uninspected_terminal_frames(self):
        for requested, actual in ((18.3, 18.0), (8.7, 8.0)):
            with self.subTest(requested=requested):
                beyond, _, _ = self.run_source([moment(0, requested, 95)],
                    duration=requested, actual_coverage=actual)
                self.assertFalse(beyond.candidates[0].fire_passed)
                self.assertIsNone(beyond.candidates[0].boundary_plan)
                valid, _, _ = self.run_source([moment(0, actual, 95)],
                    duration=requested, actual_coverage=actual)
                selected = valid.candidates[0]
                self.assertTrue(selected.fire_passed, selected.fire_reasons)
                self.assertEqual(selected.boundary_plan["recommended_clip_end"], actual)
                self.assertEqual(selected.moment_evidence["lineage"]["inspection_end"], actual)

    def test_longform_inspects_strongest_three_zones_and_scopes_relative_signals(self):
        data = _brief_data()
        brief = SourceBrief.from_dict(data)
        record = _Record(fp="long-source", title="Funny beach bikini moments")
        record.tenant_id, record.profile_id = brief.tenant_id, brief.profile_id
        record.creator_channel_identity = "Verified creator"
        record.duration_seconds = 600
        record.thumbnail_reference = "https://example.invalid/actual-thumbnail.jpg"
        pipe = Pass1Pipeline(Pass1Config(), brief, _FakeTopic(90), provider=_FakeProvider())
        preview = _Recorder(signals_by_end={12.0: [
            {"basis": b, "start_s": 1, "end_s": 5}
            for b in ("motion", "scene_change", "audio_spike")]})
        pipe._inspection_preview, pipe._inspection_analyze = preview, preview.signals
        pipe._inspection_captions = lambda url: ([], "none")
        pipe._media_assess = mock.Mock(side_effect=lambda ev, rec:
            media([moment(ev.source_start + 1, ev.source_start + 5, 95)]))
        zones = [MomentZone("long-source", event, event - 2, event + 5,
                            "transcript_event", locator_confidence=0.9)
                 for event in (40, 140, 240, 340)]
        def plan(kind, event, *args, **kwargs):
            return BoundaryPlan(event, event - 2, event + 5, "setup", "resolution", kind)
        store = ContentStoreSupabase("https://example.invalid", "offline-fixture")
        store._request = mock.Mock(return_value=[])
        with _patched_fetch([]), \
             mock.patch("content_intelligence.moment_locator.locate_moments", return_value=zones) as locate, \
             mock.patch("content_intelligence.moment_quality.build_boundary_plan", side_effect=plan):
            result = pipe.run([record], store)
        self.assertTrue(all(call.kwargs["asset_kind"] == "long_form" for call in locate.call_args_list))
        self.assertEqual(pipe._media_assess.call_count, 3)
        self.assertEqual(len(result.candidates), 3)
        self.assertEqual(preview.ends, [50, 150, 250])
        self.assertEqual({c.boundary_plan["recommended_clip_start"] for c in result.candidates},
                         {39, 139, 239})
        self.assertTrue(all(c.fire_passed for c in result.candidates))
        self.assertTrue(all(c.source_candidate_count == 3 for c in result.candidates))

    def test_longform_event_extends_past_12s_and_stops_at_first_complete_boundary(self):
        data = _brief_data()
        brief = SourceBrief.from_dict(data)
        record = _Record(fp="long-extended", title="Funny beach bikini moments")
        record.tenant_id, record.profile_id = brief.tenant_id, brief.profile_id
        record.creator_channel_identity = "Verified creator"
        record.duration_seconds = 600
        record.thumbnail_reference = "https://example.invalid/actual-thumbnail.jpg"
        pipe = Pass1Pipeline(Pass1Config(), brief, _FakeTopic(90), provider=_FakeProvider())
        signals = [{"basis": b, "start_s": 0, "end_s": 24}
                   for b in ("motion", "scene_change", "audio_spike")]
        preview = _Recorder(signals_by_end={12.0: signals, 24.0: signals})
        pipe._inspection_preview = pipe._inspection_contiguous_preview = preview
        pipe._inspection_analyze = preview.signals
        pipe._inspection_captions = lambda url: ([], "none")
        early = moment(38, 50)
        early["boundaries"][0]["complete"] = False
        completed = moment(38, 56, 95)
        completed["boundaries"].append({"end": 61, "complete": True, "evidence": "Later scene"})
        pipe._media_assess = mock.Mock(side_effect=[media([early]), media([completed])])
        zones = [MomentZone("long-extended", 40, 38, 50, "transcript_event", locator_confidence=0.9)]
        plan = BoundaryPlan(40, 38, 50, "setup", "unresolved", "funny_awkward")
        store = ContentStoreSupabase("https://example.invalid", "offline-fixture")
        store._request = mock.Mock(return_value=[])
        with _patched_fetch([]), \
             mock.patch("content_intelligence.moment_locator.locate_moments", return_value=zones), \
             mock.patch("content_intelligence.moment_quality.build_boundary_plan", return_value=plan):
            result = pipe.run([record], store)
        self.assertEqual(preview.ends, [50, 62])
        self.assertEqual(pipe._media_assess.call_count, 2)
        selected = result.candidates[0]
        self.assertTrue(selected.fire_passed, selected.fire_reasons)
        self.assertEqual(selected.boundary_plan["recommended_clip_end"], 56)
        self.assertEqual(selected.moment_evidence["lineage"]["inspection_end"], 62)


if __name__ == "__main__":
    unittest.main()
