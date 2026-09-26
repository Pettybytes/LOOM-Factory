"""Regression: the adult/age gate must never come back silently.

Bryan's directive (2026-09-26): the `adults_only` / machine age-inference gate was
NEVER a product requirement. It must not exist as a qualification, eligibility,
routing, discovery, source-mining, Manual Intake, candidate, approval, scoring,
ranking, persistence, review-eligibility, acquisition or production gate — and it
must not be replaced by age attestation either.

Canonical rule: LOOM informs; the human decides.

These tests fail loudly if an age consumer is re-added anywhere on the decision
path, which is the only durable protection against quiet reintroduction.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
for _p in (str(_REPO / "infra"), str(_REPO / "infra" / "discord-review"),
           str(_REPO / "infra" / "acquisition")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from content_intelligence.mining_contract import resolve_inspected_moments, inspection_prompt  # noqa: E402

# Files that make decisions. If any of them mentions the age fields again, the
# gate has returned (as a check, a default, a schema field, or a score input).
_DECISION_FILES = [
    _REPO / "infra/content_intelligence/mining_contract.py",
    _REPO / "infra/content_intelligence/pipeline.py",
    _REPO / "infra/content_intelligence/tenant_fit.py",
    _REPO / "infra/content_intelligence/moment_inspection.py",
    _REPO / "infra/discord-review/manual_intake_intelligence.py",
    _REPO / "infra/discord-review/manual_media_intake.py",
    _REPO / "infra/discord-review/source_review.py",
    _REPO / "infra/discord-review/tenant_routing.py",
]
_FORBIDDEN = ("adults_only", "adult_status", "adult_evidence")


def _fixture():
    return {
        "qualification": {"intent_match": True, "nudity": False, "explicit_sex": False,
                          "concept_evidence": [{"concept": "beach", "observation": "on sand"}]},
        "moments": [{"start": 0, "event_timestamp": 2, "what_happens": "an event",
                     "why_reaction_worthy": "unexpected", "hook_0_3s": "setup",
                     "payoff": "resolution", "reaction_angle": "explain it",
                     "start_evidence": "setup visible",
                     "boundaries": [{"end": 6, "complete": True, "evidence": "resolves"}]}],
    }


class TestAgeNeverGates(unittest.TestCase):
    def _resolve(self, qualification_extra=None, requirements=None):
        parsed = _fixture()
        parsed["qualification"].update(qualification_extra or {})
        return resolve_inspected_moments(parsed, requirements or {},
                                         inspected_start=0, inspected_end=8)

    def test_age_metadata_never_suppresses_a_qualifying_candidate(self):
        for extra in ({"adult_status": "ambiguous"}, {"adult_status": "minors"},
                      {"adult_status": "confirmed_adults", "adult_evidence": "x"},
                      {"adult_evidence": ""}, {}):
            out, reasons = self._resolve(extra)
            self.assertTrue(out, extra)
            self.assertNotIn("adult_status_unproven", reasons, extra)

    def test_legacy_adults_only_requirement_has_no_effect(self):
        out, reasons = self._resolve({"adult_status": "ambiguous"},
                                     requirements={"adults_only": True})
        self.assertTrue(out)
        self.assertNotIn("adult_status_unproven", reasons)

    def test_age_fields_absent_from_the_analyzer_contract(self):
        prompt = inspection_prompt({"required_concepts": []}, source_start=0, inspected_duration=5)
        for token in _FORBIDDEN:
            self.assertNotIn(token, prompt, f"{token} must not be requested from the analyzer")

    def test_no_decision_file_consumes_age(self):
        offenders = []
        for path in _DECISION_FILES:
            text = path.read_text(encoding="utf-8")
            for token in _FORBIDDEN:
                if token in text:
                    offenders.append(f"{path.name}:{token}")
        self.assertEqual(offenders, [], "age fields must have no decision consumer")

    def test_manual_intake_brief_carries_no_age_requirement(self):
        import manual_intake_intelligence as mii
        req = mii.manual_brief("dreemzy").mining_requirements
        for token in _FORBIDDEN:
            self.assertNotIn(token, req)

    def test_only_authorized_gates_remain(self):
        # Content-quality/safety criteria that ARE authorized must be untouched.
        out, reasons = self._resolve({"nudity": True})
        self.assertEqual(out, [])
        self.assertIn("unsafe_or_unproven_media", reasons)
        out2, reasons2 = self._resolve({"intent_match": False})
        self.assertEqual(out2, [])
        self.assertIn("inspected_intent_mismatch", reasons2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
