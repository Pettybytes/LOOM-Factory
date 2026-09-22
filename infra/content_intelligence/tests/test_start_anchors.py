"""Required guards: no fixed lead presets, and production/helper parity.

SS11/SS13 regression: the start the pipeline uses must be the start the anchor
module recommends, and the module must contain no fixed lead ladder.
"""

from __future__ import annotations

import inspect
import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import content_intelligence.start_anchors as sa  # noqa: E402
from content_intelligence import moment_quality as mq  # noqa: E402
from content_intelligence.start_anchors import discover_anchors, recommend_start  # noqa: E402


def seg(s: float, t: str, d: float = 4.0) -> dict:
    return {"start_s": float(s), "end_s": float(s) + d, "text": t}


# Evidence clearly supports a lead longer than 5 seconds (setup 18:28, escalation
# 18:32, anticipation 18:35, payoff 18:42).
FIXTURE = [
    seg(0, "welcome back to the stream."),
    seg(1108, "so a fan walks up to him."),
    seg(1112, "then he starts grabbing the mic."),
    seg(1115, "the guy is about to lose it."),
    seg(1122, "he snaps and walks off."),
]
SIGNALS = [{"basis": "motion", "start_s": 1112.0}, {"basis": "audio_spike", "start_s": 1115.0}]
EVENT = 1122.0
MTYPE = "fan_interaction"


class ParityTests(unittest.TestCase):
    def test_production_and_helper_agree_on_backward_search(self):
        sel = recommend_start(EVENT, FIXTURE, signals=SIGNALS, moment_type=MTYPE)
        plan = mq.build_boundary_plan(MTYPE, EVENT, FIXTURE, signals=SIGNALS)
        self.assertEqual(sel.start, plan.recommended_clip_start)
        self.assertAlmostEqual(sel.telemetry["lead_seconds"], plan.lead_seconds, places=3)
        self.assertGreater(len(plan.start_anchors), 1)          # earlier boundaries exist
        self.assertLess(plan.recommended_clip_start, EVENT)     # a real lead, not the event
        self.assertNotEqual(plan.recommended_clip_start, 0.0)   # and not source start

    def test_backward_search_is_not_collapsed_by_a_zone_start(self):
        """Regression for the PROVEN root cause: a zone provisional start equal to the
        event timestamp must not eliminate every earlier anchor."""
        anchors, tel = discover_anchors(EVENT, FIXTURE, signals=SIGNALS, moment_type=MTYPE)
        self.assertTrue([a for a in anchors if a.timestamp < EVENT])
        self.assertGreaterEqual(tel["anchor_count"], 2)

    def test_production_lead_exceeds_five_seconds_on_this_evidence(self):
        plan = mq.build_boundary_plan(MTYPE, EVENT, FIXTURE, signals=SIGNALS)
        self.assertGreater(plan.lead_seconds, 5.0)

    def test_telemetry_is_exposed_on_the_production_plan(self):
        plan = mq.build_boundary_plan(MTYPE, EVENT, FIXTURE, signals=SIGNALS)
        self.assertIn("search_floor_s", plan.search_telemetry)
        d = plan.as_dict()
        for key in ("lead_seconds", "candidate_start_anchors", "search_telemetry",
                    "natural_event_onset"):
            self.assertIn(key, d)


class PresetGuardTests(unittest.TestCase):
    def test_production_start_logic_has_no_lead_ladder(self):
        src = inspect.getsource(mq.type_aware_start)
        self.assertNotIn("leads = (", src)
        self.assertIn("recommend_start", src)      # it delegates

    def test_anchor_module_has_no_lead_ladder(self):
        src = inspect.getsource(sa)
        self.assertNotIn("leads = (", src)
        for ladder in ("(5.0, 3.0, 1.0, 0.0)", "(3.0, 1.0, 0.0)", "(12.0, 8.0",
                       "(8.0, 6.0, 5.0, 3.0, 1.0, 0.0)"):
            self.assertNotIn(ladder, src)

    def test_lead_is_derived_only(self):
        plan = mq.build_boundary_plan(MTYPE, EVENT, FIXTURE, signals=SIGNALS)
        self.assertAlmostEqual(plan.lead_seconds,
                               plan.event_timestamp - plan.recommended_clip_start, places=3)

    def test_start_kinds_are_boundaries_not_offsets(self):
        plan = mq.build_boundary_plan(MTYPE, EVENT, FIXTURE, signals=SIGNALS)
        kinds = {a.get("kind") for a in plan.start_anchors}
        self.assertTrue(kinds - {"payoff"})       # real boundaries were discovered


if __name__ == "__main__":
    unittest.main()
