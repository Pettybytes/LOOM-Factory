"""Deterministic tests for Content Intelligence Preflight V1 + Combined Selection.

Run:  python3 -m unittest discover -s infra/content_intelligence/tests -v
"""

import sys
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
for _p in (str(_REPO_ROOT / "infra"), str(_REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from content_intelligence import evidence  # noqa: E402
from content_intelligence.contract import (  # noqa: E402
    ContentAssessment,
    ContentAssessmentError,
)
from content_intelligence.selection import (  # noqa: E402
    compute_selection,
    OUTCOME_FAST_TRACK,
    OUTCOME_HYPE,
    OUTCOME_EMERGING,
    OUTCOME_REJECT,
    OUTCOME_LOW_EVIDENCE,
    OUTCOME_DIFFERENTIATION,
    OUTCOME_STANDARD_REVIEW,
    WEIGHTS,
)
from content_intelligence.provider import (  # noqa: E402
    Evidence,
    MockProvider,
    ProbePolicy,
    assess_content,
    format_fit_scores,
    recommend_format,
)
from content_intelligence.probe import BoundedMediaProbe  # noqa: E402
from content_intelligence.store import ContentStore, is_non_production  # noqa: E402


# A minimal brief stand-in for the orchestration (only needs topics/keywords/entities).
class _Brief:
    def __init__(self):
        self.topics = ["pets", "animals"]
        self.keywords = ["rescue", "viral"]
        self.entities = ["puppy"]


class _Record:
    def __init__(self, fingerprint="fp1", platform="youtube", title="Puppy rescue"):
        self.duplicate_fingerprint = fingerprint
        self.tenant_id = "dreemzy"
        self.profile_id = "bryan-face-reaction"
        self.platform = platform
        self.title = title


def _assessment(level=0, **overrides):
    base = {
        "duplicate_fingerprint": "fp1",
        "tenant_id": "dreemzy",
        "profile_id": "bryan-face-reaction",
        "platform": "youtube",
        "content_assessment_version": "1.0.0",
        "evidence_level": level,
        "evidence_types": ["metadata"],
        "evidence_completeness": 0.0,
        "scores": {"tenant_fit": 60},
        "content_worthiness_score": 60,
        "content_confidence": 0.3,
        "reasons": [{"code": "X", "signal": "s", "message": "m"}],
        "flags": [],
        "idempotency_key": "k1",
    }
    base.update(overrides)
    return base


class TestEvidenceGating(unittest.TestCase):
    def test_metadata_confidence_ceiling_is_low(self):
        self.assertLess(evidence.confidence_ceiling(0), 0.4)

    def test_transcript_raises_ceiling(self):
        self.assertGreater(evidence.confidence_ceiling(1), evidence.confidence_ceiling(0))

    def test_bounded_media_ceiling_high(self):
        self.assertGreater(evidence.confidence_ceiling(3), 0.9)

    def test_full_content_not_authorized(self):
        self.assertFalse(evidence.is_authorized_level(4))

    def test_visual_requires_static_visual(self):
        self.assertFalse(evidence.dimension_supported("visual_interest", 0))
        self.assertFalse(evidence.dimension_supported("visual_interest", 1))
        self.assertTrue(evidence.dimension_supported("visual_interest", 2))

    def test_vocal_requires_bounded_media(self):
        self.assertFalse(evidence.dimension_supported("vocal_intensity", 2))
        self.assertTrue(evidence.dimension_supported("vocal_intensity", 3))

    def test_evidence_level_detection(self):
        self.assertEqual(Evidence(platform="youtube", title="x").level(), 0)
        self.assertEqual(Evidence(platform="youtube", title="x", transcript_text="hi").level(), 1)
        self.assertEqual(Evidence(platform="youtube", title="x", transcript_text="hi",
                                  thumbnail_available=True).level(), 2)
        self.assertEqual(Evidence(platform="youtube", title="x", bounded_media_windows=[{}]).level(), 3)


class TestContentAssessmentValidation(unittest.TestCase):
    def test_transcript_cannot_produce_visual_claims(self):
        with self.assertRaises(ContentAssessmentError):
            ContentAssessment(**_assessment(level=1, scores={"visual_interest": 50}))

    def test_thumbnail_cannot_produce_vocal_claims(self):
        with self.assertRaises(ContentAssessmentError):
            ContentAssessment(**_assessment(level=2, scores={"vocal_intensity": 50}))

    def test_bounded_media_enables_vocal_and_visual(self):
        a = ContentAssessment(**_assessment(
            level=3,
            scores={"visual_interest": 50, "vocal_intensity": 50},
            content_confidence=0.9,
        ))
        self.assertEqual(a.scores["vocal_intensity"], 50)

    def test_score_out_of_range_rejected(self):
        with self.assertRaises(ContentAssessmentError):
            ContentAssessment(**_assessment(scores={"tenant_fit": 150}))
        with self.assertRaises(ContentAssessmentError):
            ContentAssessment(**_assessment(scores={"tenant_fit": -5}))

    def test_missing_required_field_rejected(self):
        d = _assessment()
        del d["scores"]
        with self.assertRaises(ContentAssessmentError):
            ContentAssessment.from_dict(d)

    def test_unknown_dimension_rejected(self):
        with self.assertRaises(ContentAssessmentError):
            ContentAssessment(**_assessment(scores={"not_a_dimension": 50}))

    def test_confidence_exceeds_ceiling_rejected(self):
        # level 0 ceiling is 0.35; 0.9 must be rejected.
        with self.assertRaises(ContentAssessmentError):
            ContentAssessment(**_assessment(level=0, content_confidence=0.9))

    def test_full_content_level_rejected(self):
        with self.assertRaises(ContentAssessmentError):
            ContentAssessment(**_assessment(level=4))

    def test_metadata_only_cannot_receive_high_confidence(self):
        a = ContentAssessment(**_assessment(level=0, content_confidence=0.3))
        self.assertLessEqual(a.content_confidence, evidence.confidence_ceiling(0))

    def test_roundtrip(self):
        a = ContentAssessment(**_assessment(level=1, scores={"hook": 70, "emotion": 60}))
        again = ContentAssessment.from_dict(a.to_dict())
        self.assertEqual(a.scores, again.scores)
        self.assertEqual(a.idempotency_key, again.idempotency_key)


class TestOrchestration(unittest.TestCase):
    def test_metadata_only_low_confidence(self):
        rec = _Record()
        ev = Evidence(platform="youtube", title="Puppy rescue")
        a = assess_content(record=rec, brief=_Brief(), evidence_bundle=ev, provider=MockProvider())
        self.assertEqual(a.evidence_level, 0)
        self.assertLessEqual(a.content_confidence, evidence.confidence_ceiling(0))
        self.assertIn("METADATA_ONLY", a.flags)
        # No semantic dimension may be present at level 0.
        self.assertNotIn("hook", a.scores)

    def test_transcript_enables_semantic_but_not_visual(self):
        rec = _Record()
        ev = Evidence(platform="youtube", title="Puppy rescue", transcript_text="A puppy is rescued...")
        a = assess_content(record=rec, brief=_Brief(), evidence_bundle=ev, provider=MockProvider())
        self.assertEqual(a.evidence_level, 1)
        self.assertIn("hook", a.scores)
        self.assertNotIn("visual_interest", a.scores)
        self.assertNotIn("vocal_intensity", a.scores)

    def test_missing_transcript_handled_honestly(self):
        rec = _Record()
        ev = Evidence(platform="youtube", title="Puppy rescue")
        a = assess_content(record=rec, brief=_Brief(), evidence_bundle=ev, provider=MockProvider())
        self.assertEqual(a.transcript_availability, "unavailable")

    def test_deterministic_same_input(self):
        rec = _Record()
        ev = Evidence(platform="youtube", title="Puppy rescue", transcript_text="hi")
        a1 = assess_content(record=rec, brief=_Brief(), evidence_bundle=ev, provider=MockProvider())
        a2 = assess_content(record=rec, brief=_Brief(), evidence_bundle=ev, provider=MockProvider())
        self.assertEqual(a1.scores, a2.scores)
        self.assertEqual(a1.idempotency_key, a2.idempotency_key)


class TestSelection(unittest.TestCase):
    def test_weights_sum_to_one(self):
        self.assertAlmostEqual(sum(WEIGHTS.values()), 1.0, places=9)

    def test_high_market_low_content_hype(self):
        r = compute_selection(
            duplicate_fingerprint="f", tenant_id="t", profile_id="p", platform="youtube",
            market_opportunity_score=90, market_confidence=0.9,
            content_worthiness_score=20, content_confidence=0.8,
            tenant_format_fit_score=60, provenance_usability_score=80,
        )
        self.assertEqual(r.outcome, OUTCOME_HYPE)

    def test_low_market_high_content_emerging(self):
        r = compute_selection(
            duplicate_fingerprint="f", tenant_id="t", profile_id="p", platform="youtube",
            market_opportunity_score=20, market_confidence=0.9,
            content_worthiness_score=90, content_confidence=0.8,
            tenant_format_fit_score=60, provenance_usability_score=80,
        )
        self.assertEqual(r.outcome, OUTCOME_EMERGING)

    def test_low_market_low_content_reject(self):
        r = compute_selection(
            duplicate_fingerprint="f", tenant_id="t", profile_id="p", platform="youtube",
            market_opportunity_score=20, market_confidence=0.9,
            content_worthiness_score=20, content_confidence=0.8,
            tenant_format_fit_score=60, provenance_usability_score=80,
        )
        self.assertEqual(r.outcome, OUTCOME_REJECT)

    def test_high_high_fast_track(self):
        r = compute_selection(
            duplicate_fingerprint="f", tenant_id="t", profile_id="p", platform="youtube",
            market_opportunity_score=90, market_confidence=0.9,
            content_worthiness_score=90, content_confidence=0.9,
            tenant_format_fit_score=70, provenance_usability_score=90,
        )
        self.assertEqual(r.outcome, OUTCOME_FAST_TRACK)

    def test_high_score_low_confidence_no_fast_track(self):
        r = compute_selection(
            duplicate_fingerprint="f", tenant_id="t", profile_id="p", platform="youtube",
            market_opportunity_score=90, market_confidence=0.1,   # low market confidence
            content_worthiness_score=90, content_confidence=0.1,  # low content confidence
            tenant_format_fit_score=70, provenance_usability_score=90,
        )
        self.assertNotEqual(r.outcome, OUTCOME_FAST_TRACK)
        self.assertEqual(r.outcome, OUTCOME_LOW_EVIDENCE)

    def test_oversaturation_requires_differentiation(self):
        r = compute_selection(
            duplicate_fingerprint="f", tenant_id="t", profile_id="p", platform="youtube",
            market_opportunity_score=90, market_confidence=0.9,
            content_worthiness_score=90, content_confidence=0.9,
            tenant_format_fit_score=70, provenance_usability_score=90,
            oversaturated=True, has_differentiated_angle=False,
        )
        self.assertEqual(r.outcome, OUTCOME_DIFFERENTIATION)

    def test_score_and_confidence_separate(self):
        # Two selections with the SAME score but DIFFERENT confidence must differ.
        hi = compute_selection(
            duplicate_fingerprint="f", tenant_id="t", profile_id="p", platform="youtube",
            market_opportunity_score=80, market_confidence=0.9,
            content_worthiness_score=80, content_confidence=0.9,
            tenant_format_fit_score=80, provenance_usability_score=80,
        )
        lo = compute_selection(
            duplicate_fingerprint="f", tenant_id="t", profile_id="p", platform="youtube",
            market_opportunity_score=80, market_confidence=0.1,
            content_worthiness_score=80, content_confidence=0.1,
            tenant_format_fit_score=80, provenance_usability_score=80,
        )
        self.assertEqual(hi.source_selection_score, lo.source_selection_score)
        self.assertGreater(hi.source_selection_confidence, lo.source_selection_confidence)

    def test_score_out_of_range_rejected(self):
        from content_intelligence.selection import SelectionError
        with self.assertRaises(SelectionError):
            compute_selection(
                duplicate_fingerprint="f", tenant_id="t", profile_id="p", platform="youtube",
                market_opportunity_score=150, market_confidence=0.9,
                content_worthiness_score=80, content_confidence=0.9,
                tenant_format_fit_score=80, provenance_usability_score=80,
            )


class TestFormatFit(unittest.TestCase):
    def test_format_fit_differs_by_platform(self):
        yt = format_fit_scores("youtube", "video", True)
        rd = format_fit_scores("reddit", "post", False)
        self.assertGreater(yt["reaction_single"], yt["commentary_story"])
        self.assertGreater(rd["commentary_story"], rd["reaction_single"])

    def test_recommend_format(self):
        self.assertEqual(recommend_format(format_fit_scores("reddit", "post", False)), "commentary_story")
        self.assertEqual(recommend_format(format_fit_scores("youtube", "video", False)), "reaction_single")


class TestProbe(unittest.TestCase):
    def _fetch(self, data=b"x" * 1000):
        def _f(start_s, end_s):
            return data
        return _f

    def test_probe_deletes_temp_media(self):
        p = BoundedMediaProbe(
            ProbePolicy(max_seconds_per_window=30, max_total_media_seconds=120,
                        max_total_bytes=20 * 1024 * 1024),
            self._fetch(),
        )
        r = p.probe([{"start_s": 0, "end_s": 10}, {"start_s": 10, "end_s": 20}])
        self.assertTrue(r.ok)
        self.assertTrue(r.temp_files_deleted)
        self.assertEqual(r.total_bytes, 2000)

    def test_probe_enforces_window_limit(self):
        p = BoundedMediaProbe(ProbePolicy(max_windows_per_candidate=2), self._fetch())
        r = p.probe([{"start_s": 0, "end_s": 5} for _ in range(3)])
        self.assertFalse(r.ok)

    def test_probe_failure_does_not_trigger_acquisition(self):
        def _fail(start_s, end_s):
            raise RuntimeError("network down")
        p = BoundedMediaProbe(ProbePolicy(), _fail)
        r = p.probe([{"start_s": 0, "end_s": 5}])
        self.assertFalse(r.ok)
        self.assertIn("probe failed", r.error)


class TestStore(unittest.TestCase):
    def test_assessment_replay_idempotent(self):
        store = ContentStore(":memory:")
        a = ContentAssessment(**_assessment(idempotency_key="k1"))
        self.assertEqual(store.upsert_assessment(a), "inserted")
        self.assertEqual(store.upsert_assessment(a), "updated")
        self.assertEqual(store.assessment_count(), 1)

    def test_new_evidence_version_creates_new_row(self):
        store = ContentStore(":memory:")
        a0 = ContentAssessment(**_assessment(idempotency_key="k0", evidence_level=0))
        a1 = ContentAssessment(**_assessment(
            idempotency_key="k1", evidence_level=1,
            scores={"hook": 70, "tenant_fit": 60}, content_confidence=0.5,
        ))
        store.upsert_assessment(a0)
        store.upsert_assessment(a1)
        self.assertEqual(store.assessment_count(), 2)

    def test_selection_replay_idempotent(self):
        store = ContentStore(":memory:")
        r = compute_selection(
            duplicate_fingerprint="f", tenant_id="t", profile_id="bryan-face-reaction",
            platform="youtube", market_opportunity_score=80, market_confidence=0.9,
            content_worthiness_score=80, content_confidence=0.9,
            tenant_format_fit_score=80, provenance_usability_score=80,
        )
        self.assertEqual(store.upsert_selection(r), "inserted")
        self.assertEqual(store.upsert_selection(r), "updated")
        self.assertEqual(store.selection_count(), 1)

    def test_production_exclusion(self):
        store = ContentStore(":memory:")
        prod = ContentAssessment(**_assessment(idempotency_key="kp", profile_id="bryan-face-reaction"))
        proof = ContentAssessment(**_assessment(idempotency_key="kx", profile_id="supabase-acceptance-proof"))
        store.upsert_assessment(prod)
        store.upsert_assessment(proof)
        self.assertEqual(len(store.production_assessments()), 1)

    def test_is_non_production(self):
        self.assertTrue(is_non_production("supabase-acceptance-proof", "dreemzy"))
        self.assertFalse(is_non_production("bryan-face-reaction", "dreemzy"))


class TestFixtures(unittest.TestCase):
    def test_fixtures_load(self):
        import json
        fx = Path(__file__).resolve().parents[1] / "fixtures"
        transcript = (fx / "transcript_sample.txt").read_text(encoding="utf-8")
        self.assertIn("puppy", transcript.lower())
        data = json.loads((fx / "assessment_level1.json").read_text(encoding="utf-8"))
        a = ContentAssessment.from_dict(data)
        self.assertEqual(a.evidence_level, 1)
        self.assertEqual(a.transcript_availability, "creator_provided")
        self.assertIn("hook", a.scores)
        self.assertNotIn("vocal_intensity", a.scores)  # level 1 => no audio claim


class TestBoundaryGuards(unittest.TestCase):
    def test_no_forbidden_modules_imported(self):
        # The content-intelligence contract layer must never import OpenShorts,
        # Discord, GHL, or distribution code.
        import content_intelligence
        forbidden = ("openshorts", "discord", "ghl", "distribution", "social", "elevenlabs", "retell")
        imported = [m for m in sys.modules if any(f in m.lower() for f in forbidden)]
        self.assertEqual(imported, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
