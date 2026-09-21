"""Boundedness acceptance: escalation stops, and nothing inspects the full source."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_SUITE = Path(__file__).resolve().parents[1]
_ROOT = _SUITE.parent
for p in (str(_SUITE), str(_ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

import datetime as _dt  # noqa: E402
import content_intelligence.pipeline as pipeline_mod  # noqa: E402
from content_intelligence.bounded_preview import (  # noqa: E402
    MAX_WINDOW, WINDOWS, BoundedPreview, fetch_bounded_preview,
)
from content_intelligence.fire import FIRE_CONFIDENCE_MIN, evidence_confidence  # noqa: E402
from content_intelligence.moment_inspection import (  # noqa: E402
    InspectionEvidence, assess_activity, assess_payoff, assess_stop, inspect_with_escalation,
)
from content_intelligence.pipeline import (  # noqa: E402
    Pass1Config, Pass1Pipeline,
)
from test_pipeline import (_Brief, _FakeProvider, _FakeStore, _FakeTopic,  # noqa: E402
                           _Record, _patched_fetch)


class _Recorder:
    """Fake preview fetcher: records requested windows, returns a fake artifact."""

    def __init__(self, available=True, signals_by_end=None):
        self.available = available
        self.ends: list[float] = []
        self.signals_by_end = signals_by_end or {}

    def __call__(self, url, *, end=3.0, start=0.0, **kw):
        self.ends.append(end)
        if not self.available:
            return BoundedPreview(start, end, provider="youtube", method="unavailable",
                                  reason="no section support")
        return BoundedPreview(start, end, start, end, "/tmp/fake-segment.mp4", 4096,
                              "youtube", "section_download", False, "", ("bounded_section",))

    def signals(self, path, max_seconds=None, **kw):
        return {"signal_windows": self.signals_by_end.get(float(max_seconds), [])}


def _decide_from(strength_ends):
    """Decisive only at the windows listed; ambiguous otherwise."""
    def _decide(ev):
        return "decisive" if ev.media_segment_seconds in strength_ends else "ambiguous"
    return _decide


class EscalationTests(unittest.TestCase):
    def _record(self):
        r = _Record(cid="esc1")
        r.canonical_url = "https://www.youtube.com/watch?v=esc1"
        return r

    def test_zero_to_three_decisive_never_escalates(self):
        rec = _Recorder()
        ev, log = inspect_with_escalation(self._record(), fetch_preview=rec,
                                          analyze_signals=rec.signals,
                                          decide=_decide_from({3.0}))
        self.assertEqual(rec.ends, [3.0])
        self.assertEqual(len(log), 1)

    def test_three_ambiguous_eight_decisive_never_reaches_twelve(self):
        rec = _Recorder()
        _, log = inspect_with_escalation(self._record(), fetch_preview=rec,
                                         analyze_signals=rec.signals,
                                         decide=_decide_from({8.0}))
        self.assertEqual(rec.ends, [3.0, 8.0])
        self.assertNotIn(12.0, rec.ends)

    def test_ambiguous_through_eight_reaches_twelve(self):
        rec = _Recorder()
        _, log = inspect_with_escalation(self._record(), fetch_preview=rec,
                                         analyze_signals=rec.signals,
                                         decide=_decide_from({12.0}))
        self.assertEqual(rec.ends, [3.0, 8.0, 12.0])

    def test_twelve_still_ambiguous_stops_at_twelve_never_full_source(self):
        rec = _Recorder()
        ev, log = inspect_with_escalation(self._record(), fetch_preview=rec,
                                          analyze_signals=rec.signals,
                                          decide=lambda e: "ambiguous")
        self.assertEqual(rec.ends, [3.0, 8.0, 12.0])
        self.assertTrue(all(e <= MAX_WINDOW for e in rec.ends))
        self.assertLessEqual(ev.media_segment_seconds, MAX_WINDOW)

    def test_unavailable_artifact_stops_immediately(self):
        rec = _Recorder(available=False)
        ev, log = inspect_with_escalation(self._record(), fetch_preview=rec,
                                          analyze_signals=rec.signals,
                                          decide=lambda e: "ambiguous")
        self.assertEqual(rec.ends, [3.0])          # no pointless escalation
        self.assertFalse(ev.has_visual())
        self.assertEqual(log[0]["available"], False)

    def test_windows_are_the_bounded_plan(self):
        self.assertEqual(WINDOWS, (3.0, 8.0, 12.0))
        self.assertEqual(MAX_WINDOW, 12.0)

    def test_fetch_bounded_preview_never_exceeds_max_window(self):
        """A caller asking for 600s still gets at most the 12s cap."""
        prev = fetch_bounded_preview("https://example.invalid/x", end=600,
                                     runner=lambda *a, **k: type("P", (), {
                                         "stderr": "boom", "returncode": 1})())
        self.assertLessEqual(prev.requested_end, MAX_WINDOW)
        self.assertFalse(prev.available)
        self.assertFalse(prev.fallback_used)        # refuses, never full-downloads


class PipelineBoundednessTests(unittest.TestCase):
    def _pipe(self, *, score=85):
        return Pass1Pipeline(Pass1Config(max_candidates=1, market_shortlist_threshold=0),
                             _Brief(), _FakeTopic(score=score), provider=_FakeProvider())

    def test_market_fail_never_calls_the_preview_adapter(self):
        pipe, store = self._pipe(), _FakeStore()
        rec = _Record(platform="tiktok", cid="ttb1")
        rec.observed_metrics = {"playCount": 84, "diggCount": 1}
        rec.publication_timestamp = (_dt.datetime.now(_dt.timezone.utc)
                                     - _dt.timedelta(hours=30)).isoformat()
        rec_prev = _Recorder()
        pipe._inspection_preview = rec_prev
        with _patched_fetch([]):
            r = pipe.run([rec], store)
        self.assertEqual(r.candidates[0].status, "filtered_market")
        self.assertEqual(rec_prev.ends, [])          # ZERO media acquisition

    def test_tenant_fail_never_calls_the_preview_adapter(self):
        pipe, store = self._pipe(), _FakeStore()
        rec = _Record(platform="youtube", title="I Thought These Guys Made Cables",
                      cid="tenantfail1")
        rec.creator_channel_identity = "Linus Tech Tips"
        rec_prev = _Recorder()
        pipe._inspection_preview = rec_prev
        with _patched_fetch([]):
            r = pipe.run([rec], store)
        self.assertEqual(r.candidates[0].status, "filtered_tenant")
        self.assertEqual(rec_prev.ends, [])          # ZERO media acquisition

    def test_unavailable_visual_artifact_cannot_reach_fire_confidence(self):
        pipe, store = self._pipe(), _FakeStore()
        rec = _Record(platform="youtube", title="wild fan interaction on stream", cid="novisual1")
        rec_prev = _Recorder(available=False)
        pipe._inspection_preview = rec_prev
        with _patched_fetch([{"start_s": 0.0, "end_s": 3.0, "text": "oh my god he fell"}]):
            r = pipe.run([rec], store)
        c = r.candidates[0]
        self.assertLess(c.evidence_confidence, FIRE_CONFIDENCE_MIN)
        self.assertFalse(c.fire_passed)

    def test_metadata_only_ceiling_is_preserved(self):
        r = evidence_confidence(platform="youtube", evidence={
            "market_metrics": True, "age": True, "engagement": True, "preview": True,
            "spoken_context": True, "provenance": True, "timestamps": True})
        self.assertLessEqual(r.score, 60)
        self.assertLess(r.score, FIRE_CONFIDENCE_MIN)


if __name__ == "__main__":
    unittest.main()
