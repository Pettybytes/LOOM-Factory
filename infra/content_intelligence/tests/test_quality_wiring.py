"""The quality layer must affect PRODUCTION behaviour, not only unit tests."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_SUITE = Path(__file__).resolve().parents[1]
_ROOT = _SUITE.parent
for p in (str(_SUITE), str(_ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

from content_intelligence.bounded_preview import BoundedPreview  # noqa: E402
from content_intelligence.pipeline import Pass1Config, Pass1Pipeline  # noqa: E402
from test_pipeline import (_Brief, _FakeProvider, _FakeStore, _FakeTopic,  # noqa: E402
                           _Record, _patched_fetch)


def cap(start: float, text: str, dur: float = 4.0) -> dict:
    return {"start_s": float(start), "end_s": float(start + dur), "text": text}


class _Rec:
    def __init__(self):
        self.requests: list[tuple[float, float]] = []

    def __call__(self, url, *, start=0.0, end=3.0, **kw):
        self.requests.append((float(start), float(end)))
        return BoundedPreview(float(start), float(end), float(start), float(end),
                              "/tmp/seg.mp4", 2048, "youtube", "section_download", False, "", ("b",))

    @staticmethod
    def signals(path, max_seconds=None, **kw):
        return {"signal_windows": []}


def _pipe():
    return Pass1Pipeline(Pass1Config(max_candidates=1, market_shortlist_threshold=0),
                         _Brief(), _FakeTopic(score=85), provider=_FakeProvider())


# A 40-min source; the moment is a SPOKEN hot take at 18:40 (1120s).
HOT_TAKE = [
    cap(0, "welcome back to the stream"),
    cap(1116, "honestly i think nobody talks about this,"),
    cap(1120, "the truth is the whole thing was wrong.", 6.0),
    cap(1126, "and here is why it matters."),
]


class QualityWiringTests(unittest.TestCase):
    def _run(self, segments, *, cid="q1", duration=2400.0, title="wild fan interaction on stream"):
        pipe, store = _pipe(), _FakeStore()
        rec = _Record(platform="youtube", title=title, cid=cid)
        rec.duration_seconds = duration
        recorder = _Rec()
        pipe._inspection_preview = recorder
        pipe._inspection_analyze = recorder.signals
        pipe._inspection_captions = lambda url: ([], "none")
        with _patched_fetch(segments):
            res = pipe.run([rec], store)
        return res.candidates[0], recorder

    def test_pipeline_classifies_the_moment_type(self):
        c, _r = self._run(HOT_TAKE)
        self.assertIsNotNone(c.moment_type)
        self.assertEqual(c.moment_type["moment_type"], "hot_take_opinion")
        self.assertTrue(c.moment_type["type_evidence"])

    def test_pipeline_builds_a_type_aware_boundary_plan(self):
        c, recorder = self._run(HOT_TAKE)
        self.assertIsNotNone(c.boundary_plan)
        plan = c.boundary_plan
        self.assertIn("start_reason", plan)
        self.assertIn("end_reason", plan)
        # inspected from the PLAN's start, and never from source 00:00
        starts = [s for s, _ in recorder.requests]
        self.assertTrue(starts)
        self.assertNotIn(0.0, starts)
        self.assertEqual(set(starts), {plan["recommended_clip_start"]})

    def test_speech_type_uses_a_minimal_lead_not_five_seconds(self):
        c, _r = self._run(HOT_TAKE)
        plan = c.boundary_plan
        lead = plan["event_timestamp"] - plan["recommended_clip_start"]
        self.assertLessEqual(lead, 3.0)

    def test_pipeline_records_the_completeness_verdict(self):
        c, _r = self._run(HOT_TAKE)
        self.assertIsNotNone(c.completeness)
        self.assertIn(c.completeness["completeness_verdict"], ("PASS", "FAIL", "AMBIGUOUS"))

    def test_incomplete_clip_blocks_the_production_card(self):
        """FIRE without a complete clip must not produce a card."""
        segs = [cap(1116, "so i was walking down the", 8.0)]   # never completes
        c, _r = self._run(segs, cid="q2")
        self.assertEqual(c.completeness["completeness_verdict"], "FAIL")
        self.assertFalse(c.fire_passed)
        self.assertIn("INCOMPLETE_CLIP", c.fire_reasons)

    def test_repair_attempts_are_recorded_when_repair_runs(self):
        segs = [cap(1116, "he fell out of the chair", 4.0),
                cap(1120, "and that was the end of it.", 4.0)]
        c, _r = self._run(segs, cid="q3")
        self.assertIsNotNone(c.completeness)
        # either repaired to PASS, or bounded attempts recorded and still blocked
        if c.completeness["completeness_verdict"] != "PASS":
            self.assertTrue(c.repair_attempts)
            self.assertFalse(c.fire_passed)

    def test_source_as_moment_still_bypasses_the_locator(self):
        c, _r = self._run([cap(0, "oh my god look at this.")], cid="q4", duration=45.0,
                          title="crazy public moment")
        self.assertEqual(c.source_mode, "source_as_moment")


if __name__ == "__main__":
    unittest.main()
