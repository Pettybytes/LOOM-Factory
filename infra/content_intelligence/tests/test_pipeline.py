"""Deterministic tests for the recurring Pass-1 pipeline + selection queue.

Run:  python3 -m unittest discover -s infra/content_intelligence/tests -v
"""

import sys
import unittest
from pathlib import Path
from unittest import mock

_REPO_ROOT = Path(__file__).resolve().parents[3]
for _p in (str(_REPO_ROOT / "infra"), str(_REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from content_intelligence import pipeline  # noqa: E402
from content_intelligence.pipeline import (  # noqa: E402
    Pass1Config, Pass1Pipeline, Pass1RunResult,
    eligible_selection, selection_queue, fetch_transcript,
    OUTCOME_STRONG, OUTCOME_STANDARD, OUTCOME_REJECT,
)
from content_intelligence.providers import ProviderError, GeminiProvider  # noqa: E402
from content_intelligence.providers import _build_prompt  # noqa: E402
from content_intelligence.provider import Evidence  # noqa: E402


class _Record:
    def __init__(self, fp="fp1", platform="youtube", title="Puppy rescue", cid="aaaaaaaaaaa"):
        self.duplicate_fingerprint = fp
        self.platform_content_id = cid
        self.title = title
        self.canonical_url = f"https://www.youtube.com/watch?v={cid}"
        self.tenant_id = "dreemzy"
        self.profile_id = "bryan-face-reaction"
        self.platform = platform


class _Brief:
    topics = ["pets", "animals"]
    keywords = ["rescue", "viral"]
    entities = ["puppy"]


class _FakeTopic:
    def __init__(self, score, confidence=0.8):
        self.score = score
        self.confidence = confidence

    def assess(self, rec, brief, competition_count=0):
        return _FakeTopicResult(self.score, self.confidence)


class _FakeTopicResult:
    def __init__(self, score, confidence):
        self.opportunity_score = score
        self.confidence = confidence


class _FakeProvider:
    def __init__(self, worthiness=70, identity=None, usage=None, raise_on_call=False):
        self.worthiness = worthiness
        self.identity = identity or {"provider": "gemini", "requested_model": "gemini-3.5-flash-lite",
                                     "resolved_model": "gemini-3.5-flash-lite", "resolved_version": "gemini-3.5-flash-lite"}
        self.usage = usage or {"input_tokens": 100, "output_tokens": 50, "latency_s": 1.0, "cost_usd": 0.001}
        self.raise_on_call = raise_on_call
        self.name = "gemini"
        self.version = "gemini-3.5-flash-lite"
        self.calls = 0

    def assess(self, evidence_bundle, brief):
        self.calls += 1
        if self.raise_on_call:
            raise ProviderError("TIMEOUT", "timeout", retryable=True)
        return {
            "scores": {"hook": 70, "emotion": 60, "conflict_tension": 50, "surprise": 40,
                       "humor": 30, "relatability": 70, "stakes": 60, "story_progression": 60,
                       "payoff": 80, "narrative_completeness": 70, "context_independence": 60,
                       "reaction_potential": 65, "commentary_value": 60, "originality_novelty": 55},
            "content_worthiness_score": self.worthiness,
            "candidate_timestamps": [{"start_s": 10, "end_s": 30, "basis": "hook"}],
            "recommended_content_format": "commentary_story",
            "recommended_angle": "the hero who saved a puppy",
            "reasons": [{"code": "STRONG_HOOK", "signal": "hook", "message": "strong hook"}],
            "model_identity": self.identity,
            "usage": self.usage,
        }


class _CapturingProvider(_FakeProvider):
    """A _FakeProvider that records the evidence bundle it was handed."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.last_evidence = None

    def assess(self, evidence_bundle, brief):
        self.last_evidence = evidence_bundle
        return super().assess(evidence_bundle, brief)


class _FakeStore:
    def __init__(self):
        self.assessments = {}
        self.selections = {}

    def upsert_assessment(self, a):
        key = a.idempotency_key
        self.assessments[key] = a

    def upsert_selection(self, s):
        key = s.idempotency_key
        self.selections[key] = s

    def production_selections(self):
        from content_intelligence.store import is_non_production
        return [s.to_dict() for s in self.selections.values()
                if not is_non_production(s.profile_id, s.tenant_id)]


def _patched_fetch(segments, ttype="auto_generated"):
    return mock.patch.object(pipeline, "fetch_transcript", return_value=(segments, ttype))


class TestPass1Pipeline(unittest.TestCase):
    def _pipeline(self, **cfg):
        market_score = cfg.pop("market_score", 70)
        config = Pass1Config(**cfg)
        topic = _FakeTopic(score=market_score)
        provider = _FakeProvider()
        return Pass1Pipeline(config, _Brief(), topic, provider=provider), provider

    def test_qualified_candidate_enters_pass1(self):
        pipe, provider = self._pipeline()
        store = _FakeStore()
        segs = [{"start_s": 0.0, "end_s": 5.0, "text": "hello"}]
        with _patched_fetch(segs):
            result = pipe.run([_Record()], store)
        self.assertEqual(result.scored, 1)
        self.assertEqual(provider.calls, 1)  # exactly one model call
        self.assertEqual(result.candidates[0].status, "scored")

    def test_weak_market_avoids_model_cost(self):
        pipe, provider = self._pipeline(market_score=10)
        store = _FakeStore()
        with _patched_fetch([]):
            result = pipe.run([_Record()], store)
        self.assertEqual(result.candidates[0].status, "skipped_market")
        self.assertEqual(provider.calls, 0)
        self.assertEqual(store.assessments, {})  # nothing persisted

    def test_captions_unavailable_assesses_from_metadata_only(self):
        """Platform Evidence Resolver: no transcript is NOT terminal.

        Under the OLD contract the candidate was discarded as
        `captions_unavailable`, which is why a qualified non-YouTube candidate
        could never complete a selection and never reached Source Review. It is
        now assessed from platform metadata alone, with evidence depth recorded
        honestly, and the model still receives exactly one call.
        """
        pipe, provider = self._pipeline()
        store = _FakeStore()
        with _patched_fetch([], "unavailable"):
            result = pipe.run([_Record()], store)
        c = result.candidates[0]
        self.assertEqual(c.status, "scored")
        self.assertEqual(c.evidence_depth, "metadata_only")
        self.assertEqual(provider.calls, 1)
        # the metadata-only assessment is persisted like any other
        self.assertEqual(len(store.assessments), 1)

    def test_caption_fetch_failure(self):
        pipe, provider = self._pipeline()
        store = _FakeStore()
        with mock.patch.object(pipeline, "fetch_transcript", side_effect=RuntimeError("boom")):
            result = pipe.run([_Record()], store)
        self.assertEqual(result.candidates[0].status, "error")
        self.assertEqual(provider.calls, 0)

    def test_provider_failure_does_not_advance(self):
        pipe, _ = self._pipeline()
        pipe.provider = _FakeProvider(raise_on_call=True)
        store = _FakeStore()
        segs = [{"start_s": 0.0, "end_s": 5.0, "text": "hello"}]
        with _patched_fetch(segs):
            result = pipe.run([_Record()], store)
        self.assertEqual(result.candidates[0].status, "error")
        self.assertEqual(store.selections, {})

    def test_model_identity_persists(self):
        pipe, _ = self._pipeline()
        store = _FakeStore()
        segs = [{"start_s": 0.0, "end_s": 5.0, "text": "hello"}]
        with _patched_fetch(segs):
            pipe.run([_Record()], store)
        a = next(iter(store.assessments.values()))
        self.assertEqual(a.model_provider, "gemini")
        self.assertIn("3.5-flash-lite", a.model_version)

    def test_replay_idempotent(self):
        pipe, _ = self._pipeline()
        store = _FakeStore()
        segs = [{"start_s": 0.0, "end_s": 5.0, "text": "hello"}]
        with _patched_fetch(segs):
            pipe.run([_Record()], store)
            pipe.run([_Record()], store)
        self.assertEqual(len(store.assessments), 1)  # same idempotency key
        self.assertEqual(len(store.selections), 1)

    def test_cost_ceiling_stops_run(self):
        pipe, _ = self._pipeline(cost_ceiling_usd=0.0005)
        pipe.provider = _FakeProvider(usage={"input_tokens": 100, "output_tokens": 50,
                                             "latency_s": 1.0, "cost_usd": 0.001})
        store = _FakeStore()
        segs = [{"start_s": 0.0, "end_s": 5.0, "text": "hello"}]
        with _patched_fetch(segs):
            result = pipe.run([_Record(), _Record(fp="fp2", cid="bbbbbbbbbbb")], store)
        # After first candidate exceeds ceiling, pipeline stops before 2nd.
        self.assertLessEqual(result.scored, 1)

    def test_max_candidates_bounded(self):
        pipe, _ = self._pipeline(max_candidates=2)
        store = _FakeStore()
        segs = [{"start_s": 0.0, "end_s": 5.0, "text": "hello"}]
        recs = [_Record(fp=f"fp{i}", cid=f"cccccccccc{i}") for i in range(5)]
        with _patched_fetch(segs):
            result = pipe.run(recs, store)
        self.assertEqual(len(result.candidates), 2)

    def test_signal_windows_flow_to_provider_through_the_fused_contract(self):
        config = Pass1Config(extract_multimodal_signals=True)
        provider = _CapturingProvider()
        pipe = Pass1Pipeline(config, _Brief(), _FakeTopic(score=70), provider=provider)
        store = _FakeStore()
        segs = [{"start_s": 0.0, "end_s": 5.0, "text": "hello"}]
        fake_windows = [{"start_s": 0.0, "end_s": 10.0, "peak_s": 5.0,
                         "basis": "visual_motion", "source": "signal_peak"}]
        with _patched_fetch(segs), mock.patch.object(
                pipeline, "fetch_media_and_signals", return_value=fake_windows):
            result = pipe.run([_Record()], store)
        self.assertEqual(result.scored, 1)
        self.assertIsNotNone(provider.last_evidence)
        # Signals reach the provider only via the Evidence bundle, already fused
        # onto the single candidate-window contract.
        self.assertEqual(provider.last_evidence.signal_windows,
                         pipeline.fuse_candidates([], fake_windows))
        self.assertEqual(provider.last_evidence.signal_windows[0]["origin"], "signal")
        self.assertEqual(provider.last_evidence.signal_windows[0]["supported_by"],
                         ["visual_motion"])
        self.assertEqual(result.candidates[0].signal_status, "extracted:1")

    def test_overlapping_signal_windows_are_deduped_before_the_provider(self):
        config = Pass1Config(extract_multimodal_signals=True)
        provider = _CapturingProvider()
        pipe = Pass1Pipeline(config, _Brief(), _FakeTopic(score=70), provider=provider)
        store = _FakeStore()
        segs = [{"start_s": 0.0, "end_s": 5.0, "text": "hello"}]
        overlapping = [
            {"start_s": 0.0, "end_s": 10.0, "peak_s": 2.0,
             "basis": "visual_motion", "source": "signal_peak"},
            {"start_s": 0.0, "end_s": 10.0, "peak_s": 1.0,
             "basis": "scene_cut", "source": "signal_peak"},
        ]
        with _patched_fetch(segs), mock.patch.object(
                pipeline, "fetch_media_and_signals", return_value=overlapping):
            result = pipe.run([_Record()], store)
        self.assertEqual(result.scored, 1)
        windows = provider.last_evidence.signal_windows
        self.assertEqual(len(windows), 1)
        self.assertEqual(sorted(windows[0]["supported_by"]), ["scene_cut", "visual_motion"])
        self.assertEqual(result.candidates[0].signal_status, "extracted:1")

    def test_multimodal_signals_off_by_default(self):
        pipe, provider = self._pipeline()  # extract_multimodal_signals defaults False
        store = _FakeStore()
        segs = [{"start_s": 0.0, "end_s": 5.0, "text": "hello"}]
        with _patched_fetch(segs), mock.patch.object(
                pipeline, "fetch_media_and_signals") as fm:
            result = pipe.run([_Record()], store)
        fm.assert_not_called()
        self.assertEqual(result.scored, 1)
        self.assertEqual(result.candidates[0].signal_status, "disabled")
        self.assertEqual(provider.calls, 1)

    def test_signal_failure_is_non_fatal_and_recorded(self):
        config = Pass1Config(extract_multimodal_signals=True)
        provider = _CapturingProvider()
        pipe = Pass1Pipeline(config, _Brief(), _FakeTopic(score=70), provider=provider)
        store = _FakeStore()
        segs = [{"start_s": 0.0, "end_s": 5.0, "text": "hello"}]
        with _patched_fetch(segs), mock.patch.object(
                pipeline, "fetch_media_and_signals",
                side_effect=RuntimeError("media decode failed")):
            result = pipe.run([_Record()], store)
        # Non-fatal: the transcript-only assessment still completes...
        self.assertEqual(result.scored, 1)
        self.assertEqual(result.candidates[0].status, "scored")
        self.assertEqual(provider.last_evidence.signal_windows, [])
        # ...and is NOT silent about it.
        self.assertEqual(result.candidates[0].signal_status, "failed:RuntimeError")

    def test_bounded_media_refusal_is_recorded_with_its_reason(self):
        """Bounding a too-long video is CORRECT, but the reason must stay
        visible: `not_fetched:<reason>` must never be indistinguishable from
        `extracted:0` (analysed successfully, no salient peaks)."""
        config = Pass1Config(extract_multimodal_signals=True)
        provider = _CapturingProvider()
        pipe = Pass1Pipeline(config, _Brief(), _FakeTopic(score=70), provider=provider)
        store = _FakeStore()
        segs = [{"start_s": 0.0, "end_s": 5.0, "text": "hello"}]
        with _patched_fetch(segs), mock.patch.object(
                pipeline, "fetch_media_and_signals",
                side_effect=pipeline.MediaNotFetched("does not pass filter (duration <= 600)")):
            result = pipe.run([_Record()], store)
        # Still non-fatal: the transcript-only assessment completes.
        self.assertEqual(result.scored, 1)
        self.assertEqual(provider.last_evidence.signal_windows, [])
        status = result.candidates[0].signal_status
        self.assertTrue(status.startswith("not_fetched:"), status)
        self.assertIn("duration <= 600", status)

    def test_provider_assess_is_called_with_two_positional_arguments(self):
        """Guard: the multimodal work must not reintroduce a call-site-only
        ``provider.assess(..., signal_windows=...)`` kwarg. Every provider takes
        ``(evidence_bundle, brief)``."""
        calls = []

        class _StrictProvider(_FakeProvider):
            def assess(self, evidence_bundle, brief):
                calls.append((evidence_bundle, brief))
                return super().assess(evidence_bundle, brief)

        config = Pass1Config(extract_multimodal_signals=True)
        pipe = Pass1Pipeline(config, _Brief(), _FakeTopic(score=70),
                             provider=_StrictProvider())
        store = _FakeStore()
        segs = [{"start_s": 0.0, "end_s": 5.0, "text": "hello"}]
        with _patched_fetch(segs), mock.patch.object(
                pipeline, "fetch_media_and_signals", return_value=[]):
            result = pipe.run([_Record()], store)
        self.assertEqual(result.scored, 1)
        self.assertEqual(len(calls), 1)
        evidence, brief = calls[0]
        self.assertIsInstance(evidence, Evidence)
        self.assertTrue(hasattr(evidence, "signal_windows"))


class TestTranscriptFetch(unittest.TestCase):
    def test_fetch_uses_skip_download(self):
        captured = {}

        def fake_run(args, **kw):
            captured["args"] = args
            return mock.Mock(returncode=0)

        with mock.patch.object(pipeline.subprocess, "run", side_effect=fake_run):
            with mock.patch.object(pipeline.Path, "glob", return_value=[]):
                fetch_transcript("https://www.youtube.com/watch?v=x")
        args = captured.get("args", [])
        self.assertIn("--skip-download", args)
        # Never a full media download flag.
        for bad in ("--write-video", "--format", "-f", "--download-sections"):
            self.assertNotIn(bad, args)


class TestSelectionQueue(unittest.TestCase):
    def test_eligible_requires_completed_selection(self):
        self.assertTrue(eligible_selection({"outcome": OUTCOME_STRONG,
                                            "source_selection_score": 80,
                                            "source_selection_confidence": 0.8}))
        self.assertFalse(eligible_selection({"outcome": "", "source_selection_score": 80}))
        self.assertFalse(eligible_selection(None))

    def test_selection_queue_filters(self):
        rows = [
            {"outcome": OUTCOME_STRONG, "source_selection_score": 85, "source_selection_confidence": 0.9},
            {"outcome": OUTCOME_STANDARD, "source_selection_score": 60, "source_selection_confidence": 0.6},
            {"outcome": OUTCOME_REJECT, "source_selection_score": 20, "source_selection_confidence": 0.8},
            {"outcome": "garbage", "source_selection_score": 50, "source_selection_confidence": 0.5},
        ]
        q = selection_queue(rows)
        self.assertEqual(len(q), 3)  # excludes 'garbage'
        self.assertEqual(len(selection_queue(rows, {OUTCOME_STRONG})), 1)


class TestProviderPrompt(unittest.TestCase):
    def test_prompt_truncates_transcript(self):
        p = _build_prompt("x" * 100000, max_chars=12000)
        # 12000 chars of transcript + prompt overhead; never the full 100k.
        self.assertLess(len(p), 16000)
        self.assertNotIn("x" * 90000, p)


if __name__ == "__main__":
    unittest.main(verbosity=2)
