"""Combined Source Selection — versioned weighted composition.

Combines the two independent halves (market opportunity from Topic Intelligence
and content worthiness from Content Intelligence) plus tenant/format fit and
provenance/usability into a single SOURCE SELECTION SCORE and a SEPARATE
SOURCE SELECTION CONFIDENCE.

Score and confidence are deliberately separate: a high score built only from
title/metadata must never appear equivalent to a high score backed by
transcript/audio/visual evidence. Confidence carries that distinction.

V1 weights (explicit policy; calibrate from real performance data later):
    35% market opportunity
    45% content worthiness
    15% tenant + format fit
     5% provenance + usability

No double-counting: Topic Intelligence's `relevance` (topical match) lives
inside the MARKET layer. The `tenant/format fit` layer here measures FORMAT fit
(which content format suits this source) + differentiation — a content-level
concern, not topical match.

Decision rules are deterministic and documented below.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

SELECTION_POLICY_VERSION = "1.0.0"

# V1 weights (sum to 1.0).
WEIGHTS: dict[str, float] = {
    "market": 0.35,
    "content": 0.45,
    "fit": 0.15,
    "provenance": 0.05,
}

# Decision thresholds (explicit V1 policy).
HIGH = 70
LOW = 50
STRONG_CONTENT = 70
WEAK_CONTENT = 50
LOW_EVIDENCE_CONFIDENCE = 0.45  # below this => never fast-track automatically

# Outcomes.
OUTCOME_FAST_TRACK = "fast_track"
OUTCOME_HYPE = "hold_hype"                    # high market, weak content
OUTCOME_EMERGING = "emerging"                 # low market, high content
OUTCOME_REJECT = "reject"                     # low market, weak content
OUTCOME_LOW_EVIDENCE = "human_review_low_confidence"
OUTCOME_DIFFERENTIATION = "differentiation_required"
OUTCOME_STANDARD_REVIEW = "standard_review"


@dataclass(frozen=True)
class SelectionResult:
    """A validated combined source selection."""

    duplicate_fingerprint: str
    tenant_id: str
    profile_id: str
    platform: str
    scoring_policy_version: str
    market_opportunity_score: int
    market_confidence: float
    content_worthiness_score: int
    content_confidence: float
    tenant_format_fit_score: int
    provenance_usability_score: int
    source_selection_score: int
    source_selection_confidence: float
    recommended_format: str
    recommended_angle: str
    suggested_timestamps: list[dict[str, Any]]
    reason_codes: list[str]
    explanation: str
    missing_evidence: list[str]
    disqualifying_risks: list[str]
    outcome: str
    idempotency_key: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "duplicate_fingerprint": self.duplicate_fingerprint,
            "tenant_id": self.tenant_id,
            "profile_id": self.profile_id,
            "platform": self.platform,
            "scoring_policy_version": self.scoring_policy_version,
            "market_opportunity_score": self.market_opportunity_score,
            "market_confidence": self.market_confidence,
            "content_worthiness_score": self.content_worthiness_score,
            "content_confidence": self.content_confidence,
            "tenant_format_fit_score": self.tenant_format_fit_score,
            "provenance_usability_score": self.provenance_usability_score,
            "source_selection_score": self.source_selection_score,
            "source_selection_confidence": self.source_selection_confidence,
            "recommended_format": self.recommended_format,
            "recommended_angle": self.recommended_angle,
            "suggested_timestamps": list(self.suggested_timestamps),
            "reason_codes": list(self.reason_codes),
            "explanation": self.explanation,
            "missing_evidence": list(self.missing_evidence),
            "disqualifying_risks": list(self.disqualifying_risks),
            "outcome": self.outcome,
            "idempotency_key": self.idempotency_key,
        }


class SelectionError(ValueError):
    pass


def _clamp_score(value: Any) -> int:
    if isinstance(value, bool):
        raise SelectionError("score must be int 0-100")
    if not isinstance(value, int):
        raise SelectionError(f"score must be int 0-100 (got {type(value).__name__})")
    if value < 0 or value > 100:
        raise SelectionError(f"score {value} out of range 0-100")
    return value


def _clamp_conf(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SelectionError("confidence must be number 0..1")
    v = float(value)
    if v < 0.0 or v > 1.0:
        raise SelectionError(f"confidence {v} out of range 0..1")
    return round(v, 4)


def compute_selection(
    *,
    duplicate_fingerprint: str,
    tenant_id: str,
    profile_id: str,
    platform: str,
    market_opportunity_score: int,
    market_confidence: float,
    content_worthiness_score: int,
    content_confidence: float,
    tenant_format_fit_score: int,
    provenance_usability_score: int,
    recommended_format: str = "hold_unassigned",
    recommended_angle: str = "",
    suggested_timestamps: list[dict[str, Any]] | None = None,
    reason_codes: list[str] | None = None,
    explanation: str = "",
    missing_evidence: list[str] | None = None,
    disqualifying_risks: list[str] | None = None,
    oversaturated: bool = False,
    has_differentiated_angle: bool = False,
    policy_version: str = SELECTION_POLICY_VERSION,
) -> SelectionResult:
    """Compute the combined source selection + outcome.

    All score inputs are validated int 0..100; confidences float 0..1. The
    weighted score and the weighted confidence are computed independently
    (never coupled), so high scores from low-evidence inputs carry low
    confidence through.
    """
    market_opportunity_score = _clamp_score(market_opportunity_score)
    content_worthiness_score = _clamp_score(content_worthiness_score)
    tenant_format_fit_score = _clamp_score(tenant_format_fit_score)
    provenance_usability_score = _clamp_score(provenance_usability_score)

    market_confidence = _clamp_conf(market_confidence)
    content_confidence = _clamp_conf(content_confidence)

    # Fit confidence: deterministic/structural fit carries high confidence when
    # present; content-derived fit inherits content confidence. Provenance
    # confidence is derived from the provenance signal itself (1.0 here unless
    # a risk is flagged — caller may pass a lower value via provenance score=0).
    fit_confidence = max(content_confidence, 0.7) if tenant_format_fit_score > 0 else 0.0
    provenance_confidence = 1.0 if provenance_usability_score > 0 else 0.0

    selection_score = int(round(
        WEIGHTS["market"] * market_opportunity_score
        + WEIGHTS["content"] * content_worthiness_score
        + WEIGHTS["fit"] * tenant_format_fit_score
        + WEIGHTS["provenance"] * provenance_usability_score
    ))
    selection_confidence = round(
        WEIGHTS["market"] * market_confidence
        + WEIGHTS["content"] * content_confidence
        + WEIGHTS["fit"] * fit_confidence
        + WEIGHTS["provenance"] * provenance_confidence,
        4,
    )

    missing_evidence = list(missing_evidence or [])
    disqualifying_risks = list(disqualifying_risks or [])
    reason_codes = list(reason_codes or [])

    outcome = _decide_outcome(
        market_opportunity_score=market_opportunity_score,
        content_worthiness_score=content_worthiness_score,
        selection_confidence=selection_confidence,
        oversaturated=oversaturated,
        has_differentiated_angle=has_differentiated_angle,
        missing_evidence=missing_evidence,
        disqualifying_risks=disqualifying_risks,
    )

    idem_key = _idempotency_key(duplicate_fingerprint, policy_version)

    return SelectionResult(
        duplicate_fingerprint=duplicate_fingerprint,
        tenant_id=tenant_id,
        profile_id=profile_id,
        platform=platform,
        scoring_policy_version=policy_version,
        market_opportunity_score=market_opportunity_score,
        market_confidence=market_confidence,
        content_worthiness_score=content_worthiness_score,
        content_confidence=content_confidence,
        tenant_format_fit_score=tenant_format_fit_score,
        provenance_usability_score=provenance_usability_score,
        source_selection_score=selection_score,
        source_selection_confidence=selection_confidence,
        recommended_format=recommended_format,
        recommended_angle=recommended_angle,
        suggested_timestamps=list(suggested_timestamps or []),
        reason_codes=reason_codes,
        explanation=explanation,
        missing_evidence=missing_evidence,
        disqualifying_risks=disqualifying_risks,
        outcome=outcome,
        idempotency_key=idem_key,
    )


def _decide_outcome(
    *,
    market_opportunity_score: int,
    content_worthiness_score: int,
    selection_confidence: float,
    oversaturated: bool,
    has_differentiated_angle: bool,
    missing_evidence: list[str],
    disqualifying_risks: list[str],
) -> str:
    """Deterministic outcome (explicit V1 policy)."""
    # Disqualifying risk always wins (do not auto-advance).
    if disqualifying_risks:
        return OUTCOME_REJECT

    high_market = market_opportunity_score >= HIGH
    weak_market = market_opportunity_score < LOW
    strong_content = content_worthiness_score >= STRONG_CONTENT
    weak_content = content_worthiness_score < WEAK_CONTENT

    # Oversaturation requires a differentiated angle to advance at all.
    if oversaturated and strong_content and not has_differentiated_angle:
        return OUTCOME_DIFFERENTIATION

    # High score + low evidence => never fast-track automatically.
    low_evidence = selection_confidence < LOW_EVIDENCE_CONFIDENCE

    if high_market and strong_content:
        if low_evidence:
            return OUTCOME_LOW_EVIDENCE
        return OUTCOME_FAST_TRACK

    if high_market and weak_content:
        return OUTCOME_HYPE

    if weak_market and strong_content:
        return OUTCOME_EMERGING

    if weak_market and weak_content:
        return OUTCOME_REJECT

    if low_evidence and (market_opportunity_score >= HIGH or content_worthiness_score >= STRONG_CONTENT):
        return OUTCOME_LOW_EVIDENCE

    return OUTCOME_STANDARD_REVIEW


def _idempotency_key(duplicate_fingerprint: str, policy_version: str) -> str:
    import hashlib
    raw = f"{duplicate_fingerprint}\x1f{policy_version}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
