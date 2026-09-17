"""Content Intelligence assessment contract — strict structured schema.

A Content Assessment is the structured output of Content Intelligence Preflight
for ONE candidate at ONE evidence level. It records exactly which content
dimensions were scored, backed by which evidence, and how confident LOOM is.

Determinism/auditability:
  * Every numeric score is an integer 0..100 (or None = "not scored / evidence
    insufficient for this dimension").
  * A dimension may only be non-None when the assessment's evidence level is
    >= that dimension's minimum level (see evidence.py). This makes it
    impossible to fabricate visual/audio claims from text or metadata.
  * ``content_confidence`` is capped by the evidence-level ceiling.
  * Malformed / out-of-bounds / evidence-exceeding assessments are REJECTED
    (never silently coerced).

This module is pure (no model, no I/O).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import evidence

SCHEMA_VERSION = "1.0.0"

# Fields every assessment MUST carry.
REQUIRED_FIELDS: tuple[str, ...] = (
    "duplicate_fingerprint",
    "tenant_id",
    "profile_id",
    "platform",
    "content_assessment_version",
    "evidence_level",
    "evidence_types",
    "evidence_completeness",
    "scores",
    "content_worthiness_score",
    "content_confidence",
    "reasons",
    "flags",
    "idempotency_key",
)

# Transcript availability/quality enumeration.
TRANSCRIPT_NONE = "unavailable"
TRANSCRIPT_CREATOR = "creator_provided"
TRANSCRIPT_AUTO = "auto_generated"
TRANSCRIPT_INCOMPLETE = "incomplete"
TRANSCRIPT_MISMATCH = "language_mismatched"
TRANSCRIPT_VALUES = frozenset({
    TRANSCRIPT_NONE, TRANSCRIPT_CREATOR, TRANSCRIPT_AUTO,
    TRANSCRIPT_INCOMPLETE, TRANSCRIPT_MISMATCH,
})


class ContentAssessmentError(ValueError):
    """Raised when an assessment fails structural validation."""


def _validate_int_score(dimension: str, value: Any) -> int:
    """Coerce a score to a validated 0..100 integer; reject anything else."""
    if isinstance(value, bool):
        raise ContentAssessmentError(f"{dimension}: score must be an integer 0-100 (got bool)")
    if isinstance(value, int):
        v = value
    elif isinstance(value, float) and value.is_integer():
        v = int(value)
    else:
        raise ContentAssessmentError(
            f"{dimension}: score must be an integer 0-100 (got {type(value).__name__})"
        )
    if v < 0 or v > 100:
        raise ContentAssessmentError(f"{dimension}: score {v} out of range 0-100")
    return v


@dataclass
class ContentAssessment:
    """A validated Content Intelligence assessment."""

    duplicate_fingerprint: str
    tenant_id: str
    profile_id: str
    platform: str
    content_assessment_version: str
    evidence_level: int
    evidence_types: tuple[str, ...]
    evidence_completeness: float
    scores: dict[str, int | None]
    content_worthiness_score: int
    content_confidence: float
    reasons: list[dict[str, str]]
    flags: list[str]
    idempotency_key: str
    # Optional (honest) metadata.
    transcript_availability: str = TRANSCRIPT_NONE
    transcript_quality: str = ""
    candidate_timestamps: list[dict[str, Any]] = field(default_factory=list)
    recommended_content_format: str = "hold_unassigned"
    recommended_angle: str = ""
    model_provider: str = ""
    model_version: str = ""
    prompt_version: str = ""
    risk_flags: list[str] = field(default_factory=list)
    raw_model_reference: str = ""
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self) -> None:
        self._validate()

    def _validate(self) -> None:
        # 1. Evidence level must be known + authorized.
        if self.evidence_level not in evidence.LEVEL_NAMES:
            raise ContentAssessmentError(f"unknown evidence_level {self.evidence_level}")
        if not evidence.is_authorized_level(self.evidence_level):
            raise ContentAssessmentError(
                f"evidence_level {self.evidence_level} (full content) is not authorized"
            )

        # 2. Scores must be a dict of known dimensions; values int 0..100 or None.
        if not isinstance(self.scores, dict):
            raise ContentAssessmentError("scores must be a dict")
        unknown = set(self.scores) - evidence.KNOWN_DIMENSIONS
        if unknown:
            raise ContentAssessmentError(f"unknown dimensions: {sorted(unknown)}")
        for dim, val in self.scores.items():
            if val is None:
                continue
            self.scores[dim] = _validate_int_score(dim, val)
            # 3. Anti-fabrication: a score may not exceed its evidence level.
            if not evidence.dimension_supported(dim, self.evidence_level):
                min_lv = evidence.required_min_level(dim)
                raise ContentAssessmentError(
                    f"dimension {dim!r} requires evidence level >= {min_lv} "
                    f"({evidence.level_name(min_lv)}); assessment is level "
                    f"{self.evidence_level} ({evidence.level_name(self.evidence_level)})"
                )

        # 4. Overall score bounds.
        self.content_worthiness_score = _validate_int_score(
            "content_worthiness_score", self.content_worthiness_score
        )

        # 5. Confidence bounds + evidence ceiling.
        if not isinstance(self.content_confidence, (int, float)) or isinstance(self.content_confidence, bool):
            raise ContentAssessmentError("content_confidence must be a number 0..1")
        conf = float(self.content_confidence)
        if conf < 0.0 or conf > 1.0:
            raise ContentAssessmentError(f"content_confidence {conf} out of range 0..1")
        ceiling = evidence.confidence_ceiling(self.evidence_level)
        if conf > ceiling + 1e-9:
            raise ContentAssessmentError(
                f"content_confidence {conf} exceeds level-{self.evidence_level} ceiling {ceiling}"
            )
        self.content_confidence = round(conf, 4)

        # 6. Evidence completeness bounds.
        if not isinstance(self.evidence_completeness, (int, float)) or isinstance(self.evidence_completeness, bool):
            raise ContentAssessmentError("evidence_completeness must be a number 0..1")
        ec = float(self.evidence_completeness)
        if ec < 0.0 or ec > 1.0:
            raise ContentAssessmentError(f"evidence_completeness {ec} out of range 0..1")
        self.evidence_completeness = round(ec, 4)

        # 7. Transcript availability enum.
        if self.transcript_availability not in TRANSCRIPT_VALUES:
            raise ContentAssessmentError(
                f"transcript_availability {self.transcript_availability!r} invalid"
            )

        # 8. idempotency key non-empty.
        if not self.idempotency_key or not self.idempotency_key.strip():
            raise ContentAssessmentError("idempotency_key must be non-empty")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ContentAssessment":
        missing = [f for f in REQUIRED_FIELDS if f not in data]
        if missing:
            raise ContentAssessmentError(f"missing required fields: {', '.join(missing)}")

        scores = dict(data["scores"])
        return cls(
            duplicate_fingerprint=str(data["duplicate_fingerprint"]).strip(),
            tenant_id=str(data["tenant_id"]).strip(),
            profile_id=str(data["profile_id"]).strip(),
            platform=str(data["platform"]).strip().lower(),
            content_assessment_version=str(data["content_assessment_version"]).strip(),
            evidence_level=int(data["evidence_level"]),
            evidence_types=tuple(str(e) for e in (data["evidence_types"] or [])),
            evidence_completeness=data["evidence_completeness"],
            scores=scores,
            content_worthiness_score=data["content_worthiness_score"],
            content_confidence=data["content_confidence"],
            reasons=[dict(r) for r in (data["reasons"] or [])],
            flags=[str(f) for f in (data["flags"] or [])],
            idempotency_key=str(data["idempotency_key"]).strip(),
            transcript_availability=str(data.get("transcript_availability") or TRANSCRIPT_NONE),
            transcript_quality=str(data.get("transcript_quality") or ""),
            candidate_timestamps=list(data.get("candidate_timestamps") or []),
            recommended_content_format=str(data.get("recommended_content_format") or "hold_unassigned"),
            recommended_angle=str(data.get("recommended_angle") or ""),
            model_provider=str(data.get("model_provider") or ""),
            model_version=str(data.get("model_version") or ""),
            prompt_version=str(data.get("prompt_version") or ""),
            risk_flags=[str(r) for r in (data.get("risk_flags") or [])],
            raw_model_reference=str(data.get("raw_model_reference") or ""),
            created_at=str(data.get("created_at") or ""),
            updated_at=str(data.get("updated_at") or ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "duplicate_fingerprint": self.duplicate_fingerprint,
            "tenant_id": self.tenant_id,
            "profile_id": self.profile_id,
            "platform": self.platform,
            "content_assessment_version": self.content_assessment_version,
            "evidence_level": self.evidence_level,
            "evidence_types": list(self.evidence_types),
            "evidence_completeness": self.evidence_completeness,
            "scores": dict(self.scores),
            "content_worthiness_score": self.content_worthiness_score,
            "content_confidence": self.content_confidence,
            "reasons": [dict(r) for r in self.reasons],
            "flags": list(self.flags),
            "idempotency_key": self.idempotency_key,
            "transcript_availability": self.transcript_availability,
            "transcript_quality": self.transcript_quality,
            "candidate_timestamps": list(self.candidate_timestamps),
            "recommended_content_format": self.recommended_content_format,
            "recommended_angle": self.recommended_angle,
            "model_provider": self.model_provider,
            "model_version": self.model_version,
            "prompt_version": self.prompt_version,
            "risk_flags": list(self.risk_flags),
            "raw_model_reference": self.raw_model_reference,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
