"""Long-form vs source-as-moment, whole-source locating, and clip-start choice."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from content_intelligence.bounded_preview import MAX_WINDOW, fetch_bounded_preview  # noqa: E402
from content_intelligence.clip_start import (  # noqa: E402
    optimize_clip_start, score_prospective_hook,
)
from content_intelligence.moment_locator import (  # noqa: E402
    MODE_LONG_FORM, MODE_SOURCE_AS_MOMENT, MomentZone, locate_moments, promote_zones,
    source_mode,
)


def seg(i: int, text: str) -> dict:
    return {"start_s": float(i * 4), "end_s": float(i * 4 + 4), "text": text}


STREAM = [
    seg(0, "welcome back to the stream"),
    seg(1, "so today we are doing something different"),
    seg(2, "the chair is about to wobble"),
    seg(3, "uh oh here we go"),
    seg(4, "he fell out of the chair"),
    seg(5, "oh my god he screamed"),
    seg(6, "everyone laughed"),
    seg(7, "ok moving on"),
]


class ModeTests(unittest.TestCase):
    def test_short_form_source_is_the_moment(self):
        self.assertEqual(source_mode(45), MODE_SOURCE_AS_MOMENT)
        self.assertEqual(source_mode(180), MODE_SOURCE_AS_MOMENT)

    def test_long_form_is_a_container(self):
        self.assertEqual(source_mode(2520), MODE_LONG_FORM)      # 42 min
        self.assertEqual(source_mode(10800), MODE_LONG_FORM)     # 3 h

    def test_unknown_duration_scans_rather_than_assumes(self):
        self.assertEqual(source_mode(None), MODE_LONG_FORM)


class LocatorTests(unittest.TestCase):
    def test_long_form_locates_a_moment_not_at_source_start(self):
        zones = locate_moments(STREAM, source_id="V1", source_duration=2520)
        self.assertTrue(zones)
        self.assertNotEqual(zones[0].event_timestamp, 0.0)
        self.assertEqual(zones[0].source_id, "V1")

    def test_locator_returns_multiple_candidate_timestamps(self):
        spread = STREAM + [seg(40, "he screamed and fell"), seg(41, "they all panicked"),
                           seg(90, "another wild reveal happened")]
        zones = locate_moments(spread, source_id="V2", source_duration=3600)
        self.assertGreaterEqual(len(zones), 2)
        stamps = {z.event_timestamp for z in zones}
        self.assertGreater(len(stamps), 1)
        self.assertNotIn(0.0, stamps)

    def test_multiple_moments_from_one_source_are_preserved(self):
        zones = locate_moments(STREAM, source_id="V3", source_duration=2520)
        for z in zones:
            self.assertEqual(z.source_id, "V3")
            self.assertTrue(z.locator_reason)

    def test_short_form_yields_one_whole_asset_zone(self):
        zones = locate_moments(STREAM[:2], source_id="S1", source_duration=45)
        self.assertEqual(len(zones), 1)
        self.assertEqual(zones[0].locator_reason, "short_form_source_is_the_moment")
        self.assertEqual(zones[0].provisional_end, 45.0)

    def test_zone_carries_provenance_and_evidence(self):
        z = locate_moments(STREAM, source_id="V4", source_duration=2520)[0]
        self.assertTrue(z.provenance)
        self.assertTrue(z.locator_evidence)
        self.assertGreater(z.locator_confidence, 0.0)
        self.assertIsInstance(z.as_dict()["provisional_moment_start"], float)

    def test_model_timestamps_are_consumed_without_a_new_model_call(self):
        zones = locate_moments(STREAM[:1], source_id="V5", source_duration=2520,
                               model_timestamps=[{"start_s": 900.0, "end_s": 912.0, "basis": "model"}])
        self.assertTrue(any(z.event_timestamp == 900.0 for z in zones))

    def test_promotion_ranks_locally_and_caps_inspection(self):
        zones = locate_moments(STREAM * 4, source_id="V6", source_duration=3600)
        promoted = promote_zones(zones, keep=2)
        self.assertLessEqual(len(promoted), 2)


def seg2(i: int, text: str) -> dict:
    """2-second captions — realistic granularity, so a 3s window discriminates."""
    return {"start_s": float(i * 2), "end_s": float(i * 2 + 2), "text": text}


# The event itself is described neutrally; the anticipation sits just BEFORE it.
# A naive implementation would return the event timestamp; a correct one prefers
# the lead that produces the stronger first 3 seconds.
DISCRIMINATING = [
    seg2(0, "welcome back to the stream"),
    seg2(1, "so anyway here is the story"),
    seg2(8, "the chair is about to wobble"),      # 16-18 anticipation
    seg2(9, "uh oh here we go"),                  # 18-20 anticipation
    seg2(10, "and that was that"),                # 20-22 neutral event text
]
EVENT = 20.0


class ClipStartTests(unittest.TestCase):
    def test_event_timestamp_is_not_blindly_the_clip_start(self):
        """When the lead-in gives the stronger opening, the lead wins."""
        plan = optimize_clip_start(EVENT, DISCRIMINATING)
        self.assertNotEqual(plan.recommended_start, plan.event_timestamp)
        self.assertGreater(plan.lead_seconds, 0.0)
        self.assertIn(plan.start_reason, ("anticipation_before_event", "setup_lead_in"))

    def test_alternatives_are_scored_so_the_choice_is_deliberate(self):
        plan = optimize_clip_start(16.0, STREAM)
        starts = {a.start for a in plan.alternatives}
        self.assertIn(16.0, starts)                      # the event is one option
        self.assertGreater(len(starts), 1)               # and never the only one
        self.assertTrue(all(a.rationale for a in plan.alternatives))

    def test_plan_records_alternatives_and_hook_evidence(self):
        plan = optimize_clip_start(EVENT, DISCRIMINATING)
        self.assertGreaterEqual(len(plan.alternatives), 3)
        self.assertTrue(plan.hook_evidence_0_3)
        self.assertGreater(plan.hook_strength, 0.0)
        self.assertEqual(plan.as_dict()["event_timestamp"], EVENT)

    def test_start_never_goes_negative(self):
        plan = optimize_clip_start(1.0, STREAM)
        self.assertGreaterEqual(plan.recommended_start, 0.0)

    def test_dead_opening_is_penalised(self):
        strong, _ = score_prospective_hook(STREAM, 16.0)     # action
        weak, ev = score_prospective_hook(STREAM, 0.0)       # "welcome back"
        self.assertLess(weak, strong)
        self.assertIn("dead_opening", ev)

    def test_recommended_end_respects_duration_hint(self):
        plan = optimize_clip_start(16.0, STREAM, duration_hint=20.0)
        self.assertLessEqual(plan.recommended_end, 20.0)


class DynamicStartPreviewTests(unittest.TestCase):
    def test_bounded_preview_accepts_a_dynamic_start(self):
        """Inspection is moment-relative, not forced to source start."""
        prev = fetch_bounded_preview("https://example.invalid/x", start=1122.0, end=1125.0,
                                     runner=lambda *a, **k: type("P", (), {
                                         "stderr": "x", "returncode": 1})())
        self.assertEqual(prev.requested_start, 1122.0)
        self.assertEqual(prev.requested_end, 1125.0)
        self.assertFalse(prev.available)          # unavailable here, but bounded
        self.assertFalse(prev.fallback_used)

    def test_dynamic_start_still_honours_the_hard_cap(self):
        prev = fetch_bounded_preview("https://example.invalid/x", start=600.0, end=1200.0,
                                     runner=lambda *a, **k: type("P", (), {
                                         "stderr": "x", "returncode": 1})())
        self.assertLessEqual(prev.requested_end - prev.requested_start, MAX_WINDOW + 0.001)


if __name__ == "__main__":
    unittest.main()
