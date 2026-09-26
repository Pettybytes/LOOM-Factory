"""Targeted tests for Manual Intelligence Intake V1.2 — Content Intelligence seam.

Deterministic and offline: the Content Intelligence runtime and the durable store
are stubbed, so these tests prove the WIRING (boundary provenance, lineage,
failure observability, acquisition bypass) without provider spend.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
for _p in (str(_REPO / "infra" / "discord-review"), str(_REPO / "infra"), str(_REPO / "infra" / "acquisition")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# The production adapter reads its config from the process env by name.
if not os.environ.get("LOOM_SUPABASE_URL"):
    _env_file = Path.home() / ".openclaw/.env"
    if _env_file.exists():
        for _line in _env_file.read_text().splitlines():
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))
os.environ.setdefault("LOOM_ACQUISITION_LIVE", "0")

import manual_intake_intelligence as mii  # noqa: E402


class _FakeCand:
    def __init__(self, plan):
        self.boundary_plan = plan
        self.platform_content_id = "7515185025495633182"
        self.status = "scored"
        self.fire_passed = False


class _FakeResult:
    def __init__(self, candidates):
        self.candidates = candidates

    def summary(self):
        return {"processed": len(self.candidates)}


class _FakeStore:
    def __init__(self, row):
        self.row = row
        self.calls = []

    def _request(self, method, path, body=None, prefer=None):
        self.calls.append((method, path, body))
        if method == "GET":
            return [dict(self.row)]
        return [dict(self.row)]


def _submission():
    return {"id": "sub-1", "tenant_id": "dreemzy", "platform": "tiktok",
            "source_url": "https://www.tiktok.com/@espn/video/7515185025495633182",
            "platform_content_id": "7515185025495633182", "duplicate_fingerprint": "b" * 64,
            "submitter": "bryan", "media_reference": {"url": "https://cdn/x.mp4"}}


class TestManualBrief(unittest.TestCase):
    def test_tenant_scoped_and_safety_gates_retained(self):
        b = mii.manual_brief("dreemzy")
        self.assertEqual(b.tenant_id, "dreemzy")
        self.assertEqual(b.profile_id, "manual-intake")
        req = b.mining_requirements
        self.assertTrue(req["adults_only"])
        self.assertEqual(req["required_concepts"], [])
        self.assertTrue(req["require_direct_event"])
        self.assertTrue(len(b.target_platforms) >= 1, "brief requires non-empty platforms")

    def test_no_theme_prefilter_for_operator_chosen_source(self):
        self.assertEqual(mii.manual_brief("client-a").mining_requirements["required_concepts"], [])


class TestRecord(unittest.TestCase):
    def test_record_carries_media_pointer_and_identity(self):
        brief = mii.manual_brief("dreemzy")
        probe = {"duration_seconds": 8.0, "width": 1080, "height": 1920, "sha256": "c" * 64}
        rec = mii.build_record(_submission(), probe, brief=brief, media_url="https://cdn/x.mp4")
        self.assertEqual(rec.tenant_id, "dreemzy")
        self.assertEqual(rec.platform, "tiktok")
        self.assertEqual(rec.duplicate_fingerprint, "b" * 64)
        self.assertTrue(rec.observed_metrics["supplied_media"])
        self.assertEqual(rec.observed_metrics["supplied_media_url"], "https://cdn/x.mp4")
        self.assertEqual(rec.observed_metrics["duration_seconds"], 8.0)


class TestIntelligenceWiring(unittest.TestCase):
    """Stub the runtime; assert the seams V1 lacked."""

    def _run(self, *, plan, human_window, candidates=None, raises=None):
        import content_intelligence.pipeline as pipe

        calls = {"patches": []}

        class FakePipeline:
            def __init__(self, cfg, brief, topic, provider=None):
                self.cfg = cfg

            def run(self, records, store):
                if raises:
                    raise raises
                return _FakeResult(candidates if candidates is not None else [_FakeCand(plan)])

        original = pipe.Pass1Pipeline
        pipe.Pass1Pipeline = FakePipeline
        try:
            row = {"duplicate_fingerprint": "b" * 64, "moment_evidence": {"existing": True}}
            store = _FakeStore(row)
            out = mii.run_manual_intelligence(
                submission=_submission(), media_path="/tmp/none.mp4",
                probe={"duration_seconds": 8.0, "sha256": "c" * 64},
                media_url="https://cdn/x.mp4", brief=mii.manual_brief("dreemzy"),
                provider=object(), store=store, human_window=human_window)
            return out, store
        finally:
            pipe.Pass1Pipeline = original

    def test_human_window_preserved_and_machine_result_retained(self):
        out, store = self._run(plan={"recommended_clip_start": 1.0, "recommended_clip_end": 9.0},
                               human_window=(2.0, 5.0))
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["boundary_provenance"], "manual_intake")
        plan = out["boundary_plan"]
        self.assertEqual((plan["recommended_clip_start"], plan["recommended_clip_end"]), (2.0, 5.0))
        # the machine recommendation is evidence, never silently discarded
        self.assertEqual(plan["machine_recommendation"]["recommended_clip_start"], 1.0)
        patch = next(c for c in store.calls if c[0] == "PATCH" and "loom_source_candidates" in c[1])
        self.assertEqual(patch[2]["boundary_plan"]["recommended_clip_start"], 2.0)

    def test_machine_window_kept_when_no_human_timestamps(self):
        out, store = self._run(plan={"recommended_clip_start": 3.0, "recommended_clip_end": 8.0},
                               human_window=(None, None))
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["boundary_provenance"], "machine")
        self.assertNotIn("machine_recommendation", out["boundary_plan"])

    def test_lineage_marks_media_already_acquired(self):
        out, store = self._run(plan={"recommended_clip_start": 0.0, "recommended_clip_end": 5.0},
                               human_window=(None, None))
        patch = next(c for c in store.calls if c[0] == "PATCH" and "loom_source_candidates" in c[1])
        lineage = patch[2]["moment_evidence"]["lineage"]
        self.assertEqual(lineage["origin"], "manual_media_intake")
        self.assertTrue(lineage["media_already_acquired"])
        self.assertEqual(lineage["submission_id"], "sub-1")
        self.assertTrue(patch[2]["moment_evidence"]["existing"], "existing evidence preserved")

    def test_submission_row_records_boundary_provenance(self):
        out, store = self._run(plan={"recommended_clip_start": 1.0, "recommended_clip_end": 4.0},
                               human_window=(1.0, 4.0))
        subs = [c for c in store.calls if c[0] == "PATCH" and "loom_manual_submissions" in c[1]]
        self.assertTrue(subs and subs[0][2]["boundary_provenance"] == "manual_intake")

    def test_no_candidate_is_an_explicit_failure_not_a_silent_pass(self):
        out, _ = self._run(plan={}, human_window=(None, None), candidates=[])
        self.assertFalse(out["ok"])
        self.assertEqual(out["stage"], "no_candidate")
        self.assertTrue(out["error"])

    def test_runtime_error_is_reported_with_stage(self):
        out, _ = self._run(plan={}, human_window=(None, None), raises=RuntimeError("provider down"))
        self.assertFalse(out["ok"])
        self.assertEqual(out["stage"], "pipeline_run")
        self.assertIn("provider down", out["error"])


class TestAcquisitionBypass(unittest.TestCase):
    def test_supplied_media_never_plans_a_paid_job(self):
        import adapter  # production class under test
        from unittest import mock

        handler = object.__new__(adapter.Handler)
        with mock.patch.object(adapter, "_acquisition_store") as store_fn:
            note = handler._source_approve_handoff(
                {"duplicate_fingerprint": "b" * 64, "platform": "tiktok",
                 "moment_evidence": {"lineage": {"media_already_acquired": True}}},
                "bryan#1")
        self.assertIn("already stored", note)
        store_fn.assert_not_called()

    def test_discovered_candidate_still_reaches_the_lane(self):
        import adapter
        from unittest import mock

        handler = object.__new__(adapter.Handler)
        # No supplied-media lineage -> the normal acquisition lane is entered.
        with mock.patch.object(adapter, "_acquisition_store",
                               side_effect=RuntimeError("lane entered")):
            with self.assertRaises(RuntimeError):
                handler._source_approve_handoff(
                    {"duplicate_fingerprint": "c" * 64, "platform": "tiktok",
                     "moment_evidence": {"lineage": {}}}, "bryan#1")


if __name__ == "__main__":
    unittest.main(verbosity=2)
