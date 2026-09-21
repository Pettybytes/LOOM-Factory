"""Evidence Confidence + the FIRE production gate.

Evidence Confidence is EVIDENCE COMPLETENESS / RELIABILITY — never "the model is
N% sure". It is platform-aware: a platform is judged on the evidence it
reasonably exposes, and is never punished for a field it genuinely does not have
(Reddit has no view counts; Twitch has category/thumbnail instead).

HARD RULE (structural, not a prompt): a candidate assessed only from metadata —
title / caption / transcript / hashtags — can NEVER reach the 85 confidence the
FIRE gate requires. The ceiling is applied in code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

# ---- FIRE thresholds (fixed; never lowered) --------------------------------
FIRE_MARKET_MIN = 75
FIRE_CONTENT_MIN = 80
FIRE_SELECTION_MIN = 85
FIRE_CONFIDENCE_MIN = 85

# Metadata-only evidence cannot reach production confidence.
METADATA_ONLY_CONFIDENCE_CEILING = 60
# Model-only content worthiness cannot stand in for real moment evidence.
METADATA_ONLY_CONTENT_CEILING = 70

# ---- FIRE rejection reasons -------------------------------------------------
LOW_MARKET = "LOW_MARKET"
LOW_CONTENT = "LOW_CONTENT"
LOW_SELECTION = "LOW_SELECTION"
LOW_CONFIDENCE = "LOW_CONFIDENCE"
NO_IMMEDIATE_HOOK = "NO_IMMEDIATE_HOOK"
LOW_ACTIVITY = "LOW_ACTIVITY"
NO_PAYOFF = "NO_PAYOFF"
WEAK_REACTION_OPPORTUNITY = "WEAK_REACTION_OPPORTUNITY"
WRONG_TENANT = "WRONG_TENANT"
DUPLICATE = "DUPLICATE"
INSUFFICIENT_VISUAL_EVIDENCE = "INSUFFICIENT_VISUAL_EVIDENCE"
INSUFFICIENT_CONTEXT = "INSUFFICIENT_CONTEXT"
MARKET_GATE_FAILED = "MARKET_GATE_FAILED"

# Evidence each platform can reasonably be expected to supply. A dimension is
# only REQUIRED when the platform genuinely exposes it.
PLATFORM_EXPECTED: dict[str, frozenset[str]] = {
    "youtube": frozenset({"market_metrics", "age", "engagement", "preview", "visual_opening"}),
    "tiktok": frozenset({"market_metrics", "age", "engagement", "preview", "visual_opening"}),
    "instagram": frozenset({"market_metrics", "age", "engagement", "visual_opening"}),
    "twitch": frozenset({"market_metrics", "age", "preview", "category_metadata", "visual_opening"}),
    "kick": frozenset({"market_metrics", "age", "visual_opening"}),
    # Reddit exposes score/hot-rank evidence, NOT view metrics.
    "reddit": frozenset({"score_metrics", "age", "comments", "rank"}),
}

# Weights apply only to dimensions that are RELEVANT for the platform.
DIMENSION_WEIGHTS: dict[str, int] = {
    "market_metrics": 12,
    "score_metrics": 12,
    "age": 6,
    "engagement": 10,
    "comments": 6,
    "rank": 6,
    "category_metadata": 4,
    "creator_baseline": 8,      # OPTIONAL: never required when history is absent
    "preview": 8,
    "visual_opening": 18,
    "immediate_hook": 10,
    "action_activity": 12,
    "spoken_context": 6,
    "payoff_understanding": 10,
    "reaction_opportunity": 8,
    "timestamps": 6,
    "provenance": 4,
}

OPTIONAL_DIMENSIONS = frozenset({"creator_baseline"})


@dataclass(frozen=True)
class ConfidenceResult:
    score: int
    ceiling_applied: str
    raw_before_ceiling: int
    present: tuple[str, ...]
    missing_required: tuple[str, ...]
    dimensions: dict[str, Any] = field(default_factory=dict)
    reasons: tuple[str, ...] = ()


def _weight(dim: str, platform: str) -> int:
    expected = PLATFORM_EXPECTED.get(platform)
    if expected is None:
        return DIMENSION_WEIGHTS.get(dim, 0)
    if dim not in expected and dim not in OPTIONAL_DIMENSIONS:
        return 0                      # irrelevant for this platform -> no weight
    return DIMENSION_WEIGHTS.get(dim, 0)


def evidence_confidence(*, platform: str, evidence: dict[str, Any]) -> ConfidenceResult:
    """Completeness/reliability of evidence, scaled to what the platform exposes."""
    platform = (platform or "").lower()
    expected = PLATFORM_EXPECTED.get(platform, frozenset())
    present: list[str] = []
    missing_required: list[str] = []
    total_weight = 0
    earned = 0
    for dim, weight in DIMENSION_WEIGHTS.items():
        w = _weight(dim, platform)
        if w <= 0:
            continue
        present_dim = bool(evidence.get(dim))
        # OPTIONAL dimensions add to the score when present but must never
        # dilute it when genuinely unavailable (e.g. no creator history yet).
        if dim in OPTIONAL_DIMENSIONS and not present_dim:
            continue
        total_weight += w
        if present_dim:
            earned += w
            present.append(dim)
        elif dim in expected and dim not in OPTIONAL_DIMENSIONS:
            missing_required.append(dim)
    raw = int(round(100.0 * earned / total_weight)) if total_weight else 0

    reasons: list[str] = []
    ceiling = ""
    score = raw
    if not evidence.get("visual_opening"):
        # Structural ceiling: metadata-only can never clear the FIRE bar. The
        # ceiling APPLIES whenever there is no visual opening; it only has to
        # BIND when the raw completeness would otherwise exceed it.
        ceiling = "metadata_only_no_visual_opening"
        if score > METADATA_ONLY_CONFIDENCE_CEILING:
            score = METADATA_ONLY_CONFIDENCE_CEILING
        reasons.append(INSUFFICIENT_VISUAL_EVIDENCE)
    if not evidence.get("payoff_understanding"):
        reasons.append(NO_PAYOFF)
    if not evidence.get("action_activity"):
        reasons.append(LOW_ACTIVITY)
    return ConfidenceResult(score, ceiling, raw, tuple(present), tuple(missing_required),
                            dict(evidence), tuple(dict.fromkeys(reasons)))


def recalibrate_content_score(base_content_score: int | None, evidence: dict[str, Any]) -> int:
    """Evolve the EXISTING Content Intelligence result around real moment evidence.

    No second scoring model: the model's worthiness is bounded by the evidence
    actually produced, so metadata-only candidates cannot carry a high Content
    score.
    """
    base = int(base_content_score or 0)
    if not evidence.get("visual_opening"):
        return min(base, METADATA_ONLY_CONTENT_CEILING)
    bonus = 0
    if evidence.get("immediate_hook"):
        bonus += 10
    if evidence.get("action_activity"):
        bonus += 8
    if evidence.get("payoff_understanding"):
        bonus += 8
    if evidence.get("reaction_opportunity"):
        bonus += 4
    return max(0, min(100, base + bonus))


def selection_score(*, market: int, content: int, tenant_fit_verdict: str,
                    reaction_opportunity: bool, evidence_completeness: int,
                    duplicate_risk: bool, format_suitable: bool = True) -> int:
    """Combined Selection: should THIS moment enter the producer inbox?

    High market + boring content cannot pass, and good content + no traction
    cannot pass, because both are multiplied rather than averaged.
    """
    m = max(0, min(100, int(market)))
    c = max(0, min(100, int(content)))
    # Geometric-ish blend: a weak factor drags the result down hard.
    blend = int(round((m * c) ** 0.5))
    score = blend
    score += 6 if reaction_opportunity else -12
    score += 6 if format_suitable else -8
    score += int(round(0.12 * max(0, min(100, evidence_completeness))))
    if tenant_fit_verdict and tenant_fit_verdict != "PASS":
        score -= 25
    if duplicate_risk:
        score -= 40
    return max(0, min(100, score))


@dataclass(frozen=True)
class FireDecision:
    passed: bool
    reasons: tuple[str, ...] = ()
    scores: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"passed": self.passed, "reasons": list(self.reasons), **self.scores}


def fire_gate(*, market_score: int, content_score: int, selection: int,
              confidence: int, market_gate_pass: bool, tenant_fit_pass: bool,
              immediate_hook: bool, action_activity: bool, payoff_present: bool,
              duplicate_risk: bool = False,
              reaction_opportunity: bool = True) -> FireDecision:
    """The final production decision. Any failure => no production card."""
    reasons: list[str] = []
    if not market_gate_pass or market_score < FIRE_MARKET_MIN:
        reasons.append(MARKET_GATE_FAILED if not market_gate_pass else LOW_MARKET)
    if not tenant_fit_pass:
        reasons.append(WRONG_TENANT)
    if content_score < FIRE_CONTENT_MIN:
        reasons.append(LOW_CONTENT)
    if not immediate_hook:
        reasons.append(NO_IMMEDIATE_HOOK)
    if not action_activity:
        reasons.append(LOW_ACTIVITY)
    if not payoff_present:
        reasons.append(NO_PAYOFF)
    if not reaction_opportunity:
        reasons.append(WEAK_REACTION_OPPORTUNITY)
    if selection < FIRE_SELECTION_MIN:
        reasons.append(LOW_SELECTION)
    if confidence < FIRE_CONFIDENCE_MIN:
        reasons.append(LOW_CONFIDENCE)
    if duplicate_risk:
        reasons.append(DUPLICATE)

    scores = {"market": market_score, "content": content_score, "selection": selection,
              "confidence": confidence, "market_gate": market_gate_pass,
              "tenant_fit": tenant_fit_pass, "immediate_hook": immediate_hook,
              "action_activity": action_activity, "payoff": payoff_present,
              "duplicate_risk": duplicate_risk}
    return FireDecision(not reasons, tuple(dict.fromkeys(reasons)), scores)
