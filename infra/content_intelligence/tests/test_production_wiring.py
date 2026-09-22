"""SS15 production-wiring proofs: the wired path must inspect the MOMENT."""

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
from content_intelligence.moment_locator import (  # noqa: E402
    locate_moments, promote_zones,
)
from content_intelligence.pipeline import Pass1Config, Pass1Pipeline  # noqa: E402
from test_pipeline import (_Brief, _FakeProvider, _FakeStore, _FakeTopic,  # noqa: E402
                           _Record, _patched_fetch)


def cap(start: float, text: str) -> dict:
    return {"start_s": float(start), "end_s": float(start + 2), "text": text}


# A 40-minute source where the interesting event sits at 18:42 (1122s).
FORTY_MIN = [
    cap(0, "welcome back to the stream"),
    cap(4, "so anyway here is what happened"),
    cap(316, "and then she said something wild"),          # ~05:16
    cap(318, "oh my god no way"),                          # ~05:18 moment
    cap(1116, "the chair is about to wobble"),             # ~18:36
    cap(1118, "uh oh here we go"),                         # ~18:38
    cap(1120, "he fell out of the chair"),                 # ~18:40
    cap(1122, "oh my god he screamed"),                    # ~18:42 event
    cap(1866, "wait what just happened"),                  # ~31:06
    cap(1868, "that was insane"),                          # ~31:08 moment
]


class _StartRecorder:
    """Records the absolute start of every bounded request made."""

    def __init__(self):
        self.requests: list[tuple[float, float]] = []

    def __call__(self, url, *, start=0.0, end=3.0, **kw):
        self.requests.append((float(start), float(end)))
        return BoundedPreview(float(start), float(end), float(start), float(end),
                              "/tmp/seg.mp4", 2048, "youtube", "section_download", False,
                              "", ("bounded_section",))

    @staticmethod
    def signals(path, max_seconds=None, **kw):
        return {"signal_windows": [{"basis": "motion", "start_s": 1118.0, "end_s": 1122.0}]}


def _pipe():
    return Pass1Pipeline(Pass1Config(max_candidates=1, market_shortlist_threshold=0),
                         _Brief(), _FakeTopic(score=85), provider=_FakeProvider())


def _longform_record(cid="lf40"):
    r = _Record(platform="youtube", title="wild fan interaction on stream", cid=cid)
    r.duration_seconds = 2400.0            # 40 minutes
    return r


class LongFormDynamicStartTests(unittest.TestCase):
    def test_long_form_inspects_the_moment_not_source_start(self):
        pipe, store = _pipe(), _FakeStore()
        rec = _longform_record()
        recorder = _StartRecorder()
        pipe._inspection_preview = recorder
        pipe._inspection_analyze = recorder.signals
        pipe._inspection_captions = lambda url: ([], "none")
        with _patched_fetch(FORTY_MIN):
            r = pipe.run([rec], store)
        c = r.candidates[0]
        self.assertEqual(c.source_mode, "long_form_container")
        self.assertIsNotNone(c.selected_zone)
        self.assertTrue(recorder.requests, "inspection must have been attempted")
        starts = [s for s, _ in recorder.requests]
        # THE INVARIANT: inspection happens at the LOCATED moment, never at source
        # start. (Which zone wins is a ranking judgement; asserting a specific
        # winner would over-specify and invite tuning weights to fit one fixture.)
        self.assertNotIn(0.0, starts)
        self.assertTrue(all(s > 0 for s in starts), starts)
        plan_start = c.clip_start_plan["recommended_clip_start"]
        self.assertEqual(set(starts), {plan_start})         # inspects the CHOSEN start
        zone_ts = c.selected_zone["candidate_event_timestamp"]
        self.assertGreater(zone_ts, 0.0)
        self.assertLessEqual(abs(plan_start - zone_ts), 10.0)   # near the selected moment

    def test_multiple_moments_stay_independent(self):
        zones = locate_moments(FORTY_MIN, source_id="lf40", source_duration=2400.0)
        stamps = sorted(z.event_timestamp for z in zones)
        self.assertGreaterEqual(len(stamps), 2)
        self.assertGreater(stamps[-1] - stamps[0], 600.0)   # distinct, far apart

    def test_nonverbal_moment_can_be_promoted(self):
        """Weak transcript + aligned audio/motion/scene evidence => valid candidate."""
        zones = locate_moments([], source_id="V", source_duration=2400.0,
                               signal_peaks=[{"basis": "motion", "start_s": 1122.0},
                                             {"basis": "audio_spike", "start_s": 1124.0},
                                             {"basis": "scene_change", "start_s": 1126.0}])
        promoted = promote_zones(zones)
        self.assertEqual(len(promoted), 1)
        self.assertGreaterEqual(promoted[0].locator_confidence, 0.45)
        self.assertIn("signal:motion", promoted[0].locator_evidence)

    def test_weak_zone_is_not_promoted(self):
        """No transcript cue + one isolated weak blip => no candidate."""
        zones = locate_moments([], source_id="V", source_duration=2400.0,
                               signal_peaks=[{"basis": "speech_onset", "start_s": 600.0}])
        self.assertEqual(promote_zones(zones), [])

    def test_continuity_clusters_adjacent_signals_into_one_zone(self):
        zones = locate_moments([], source_id="V", source_duration=2400.0,
                               signal_peaks=[{"basis": "motion", "start_s": 1120.0},
                                             {"basis": "audio_spike", "start_s": 1122.0},
                                             {"basis": "motion", "start_s": 1124.0}])
        self.assertEqual(len(zones), 1)

    def test_multisignal_alignment_outranks_a_single_weak_spike(self):
        aligned = locate_moments([], source_id="V", source_duration=2400.0,
                                 signal_peaks=[{"basis": "motion", "start_s": 100.0},
                                               {"basis": "audio_spike", "start_s": 102.0},
                                               {"basis": "scene_change", "start_s": 104.0}])[0]
        single = locate_moments([], source_id="V", source_duration=2400.0,
                                signal_peaks=[{"basis": "audio_energy", "start_s": 900.0}])[0]
        self.assertGreater(aligned.locator_confidence, single.locator_confidence)

    def test_source_as_moment_bypasses_the_locator(self):
        pipe, store = _pipe(), _FakeStore()
        rec = _Record(platform="tiktok", cid="short01")
        rec.duration_seconds = 45.0
        # Realistic TikTok metric keys (likeCount is a YouTube key, not TikTok's).
        rec.observed_metrics = {"playCount": 900_000, "diggCount": 80_000,
                                "commentCount": 4_000, "shareCount": 9_000}
        recorder = _StartRecorder()
        pipe._inspection_preview = recorder
        pipe._inspection_analyze = recorder.signals
        pipe._inspection_captions = lambda url: ([], "none")
        with _patched_fetch([cap(0, "oh my god look at this"), cap(2, "no way")]):
            r = pipe.run([rec], store)
        c = r.candidates[0]
        self.assertEqual(c.source_mode, "source_as_moment")
        self.assertEqual(c.selected_zone["locator_reason"], "short_form_source_is_the_moment")


if __name__ == "__main__":
    unittest.main()
