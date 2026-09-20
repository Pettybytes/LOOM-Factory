"""Content Intelligence Preflight — evidence bundling + model provider contract.

This module defines:
  * ``Evidence`` — the bundle of actually-inspected evidence for one candidate,
    plus the derived evidence LEVEL and completeness.
  * ``ProbePolicy`` — hard limits for Pass 2 bounded media probes.
  * ``ContentModelProvider`` — the interface any semantic scorer must satisfy.
  * ``MockProvider`` — a deterministic, dependency-free provider used to build
    and test the pipeline BEFORE any real (paid) model call.
  * ``assess_content`` — orchestration: structural (deterministic) signals +
    provider (semantic) signals -> validated ``ContentAssessment``.

No real model call, no media download, no I/O happens in this module.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Protocol

from . import evidence
from .contract import (
    ContentAssessment,
    TRANSCRIPT_AUTO,
    TRANSCRIPT_CREATOR,
    TRANSCRIPT_INCOMPLETE,
    TRANSCRIPT_MISMATCH,
    TRANSCRIPT_NONE,
)

# Content formats.
FORMATS: tuple[str, ...] = (
    "reaction_single",
    "reaction_compilation",
    "commentary_story",
    "ranking_top5",
    "stream_highlight",
    "hold_unassigned",
)


# ---------------------------------------------------------------------------
# Evidence bundle
# ---------------------------------------------------------------------------
@dataclass
class Evidence:
    platform: str
    content_type: str = ""
    title: str = ""
    description: str = ""
    body_excerpt: str = ""
    transcript_text: str = ""
    transcript_availability: str = TRANSCRIPT_NONE
    transcript_quality: str = ""
    thumbnail_available: bool = False
    thumbnail_reference: str = ""
    bounded_media_windows: list[dict[str, Any]] = field(default_factory=list)
    signal_windows: list[dict[str, Any]] = field(default_factory=list)
    candidate_timestamps: list[dict[str, Any]] = field(default_factory=list)
    market_metrics: dict[str, Any] = field(default_factory=dict)

    def level(self) -> int:
        """Highest evidence level actually present (never inferred upward)."""
        if self.bounded_media_windows:
            return evidence.LEVEL_BOUNDED_MEDIA
        if self.thumbnail_available:
            return evidence.LEVEL_STATIC_VISUAL
        if self.transcript_text or self.body_excerpt:
            return evidence.LEVEL_TEXT
        return evidence.LEVEL_METADATA

    def evidence_types(self) -> list[str]:
        types = ["metadata"]
        if self.transcript_text or self.body_excerpt:
            types.append("text_content")
        if self.thumbnail_available:
            types.append("static_visual")
        if self.bounded_media_windows:
            types.append("bounded_media")
        return types

    def evidence_completeness(self) -> float:
        """0..1 — fraction of the (authorized) evidence tiers present."""
        present = 0
        if self.transcript_text or self.body_excerpt:
            present += 1
        if self.thumbnail_available:
            present += 1
        if self.bounded_media_windows:
            present += 1
        # Three content evidence tiers: text, static visual, bounded media.
        return round(present / 3.0, 4)


# ---------------------------------------------------------------------------
# Bounded media probe policy (Pass 2 limits)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ProbePolicy:
    max_candidates: int = 3
    max_windows_per_candidate: int = 3
    max_seconds_per_window: int = 30
    max_total_media_seconds: int = 120
    max_total_bytes: int = 20 * 1024 * 1024  # 20 MB
    max_model_cost_usd: float = 0.0
    delete_after_probe: bool = True


# ---------------------------------------------------------------------------
# Provider contract
# ---------------------------------------------------------------------------
class ContentModelProvider(Protocol):
    def assess(self, evidence: Evidence, brief: Any) -> dict[str, Any]:
        """Return a raw structured assessment dict (validated by the caller)."""
        ...


# ---------------------------------------------------------------------------
# Deterministic structural signals (no model)
# ---------------------------------------------------------------------------
def tenant_fit_score(brief: Any, title: str, body_excerpt: str, transcript_text: str) -> int:
    """Deterministic topical fit: brief topics/keywords/entities matched against
    title + body + transcript. This is the TENANT/FORMAT-FIT layer's topical
    contribution (distinct from Topic Intelligence's market `relevance`)."""
    tokens: list[str] = []
    for group in ("topics", "keywords", "entities"):
        for t in getattr(brief, group, []) or []:
            tok = (t or "").strip().lower()
            if tok and tok not in tokens:
                tokens.append(tok)
    if not tokens:
        return 0
    hay = f"{title} {body_excerpt} {transcript_text}".lower()
    matched = sum(1 for t in tokens if t in hay)
    return max(0, min(100, round(100.0 * matched / 3.0)))


def saturation_differentiation_score(competition_count: int, has_differentiated_angle: bool) -> int:
    """Deterministic saturation signal: higher competition lowers the score; a
    differentiated angle recovers some of it."""
    if competition_count is None:
        competition_count = 0
    base = max(0, 100 - competition_count * 20)
    if has_differentiated_angle:
        base = min(100, base + 25)
    return base


def originality_score(title: str, seen_titles: set[str] | None) -> int:
    """Deterministic title-novelty signal (cheap, not perceptual)."""
    if not title:
        return 0
    if not seen_titles:
        return 50  # unknown — no comparison set
    key = _title_key(title)
    if key in seen_titles:
        return 0
    return 100


def format_fit_scores(platform: str, content_type: str, has_transcript: bool) -> dict[str, int]:
    """Deterministic STRUCTURAL format fit (platform/content-type only). Content
    -derived format fit is deferred to the model (semantic)."""
    platform = (platform or "").lower()
    content_type = (content_type or "").lower()
    base = {
        "reaction_single": 30,
        "reaction_compilation": 30,
        "commentary_story": 30,
        "ranking_top5": 30,
        "stream_highlight": 0,  # future streamer platforms only
        "hold_unassigned": 0,
    }
    if platform == "youtube" or content_type == "video":
        base["reaction_single"] = 70
        base["reaction_compilation"] = 70
        base["ranking_top5"] = 50
        base["commentary_story"] = 40
    elif platform == "reddit" or content_type == "post":
        base["commentary_story"] = 75
        base["ranking_top5"] = 50
        base["reaction_single"] = 25
        base["reaction_compilation"] = 25
    if has_transcript:
        for k in ("reaction_single", "reaction_compilation", "commentary_story", "ranking_top5"):
            base[k] = min(100, base[k] + 10)
    return base


def recommend_format(fit: dict[str, int]) -> str:
    scored = {k: v for k, v in fit.items() if k != "hold_unassigned"}
    if not scored or max(scored.values()) < 40:
        return "hold_unassigned"
    return max(scored, key=lambda k: scored[k])


def _title_key(title: str) -> str:
    import re
    t = title.strip().lower()
    t = re.sub(r"[^\w\s]", "", t)
    return re.sub(r"\s+", " ", t).strip()


# ---------------------------------------------------------------------------
# Deterministic mock provider (no model, no I/O)
# ---------------------------------------------------------------------------
def _stable_score(seed: str, dimension: str, floor: int = 35, span: int = 60) -> int:
    """Deterministic 0..100 integer derived from a stable seed + dimension."""
    h = hashlib.sha256(f"{seed}\x1f{dimension}".encode("utf-8")).digest()
    v = int.from_bytes(h[:4], "big") % (span + 1)
    return min(100, floor + v)


class MockProvider:
    """Deterministic, dependency-free provider. Produces VALID, evidence-gated
    output so the pipeline can be built + tested without any paid model call."""

    name = "mock"
    version = "1.0.0"

    def assess(self, evidence_bundle: Evidence, brief: Any) -> dict[str, Any]:
        level = evidence_bundle.level()
        seed = evidence_bundle.title or "seed"

        scores: dict[str, int | None] = {}
        # Semantic dimensions (only if evidence supports them).
        for dim, min_lv in evidence.DIMENSION_MIN_LEVEL.items():
            if level >= min_lv:
                scores[dim] = _stable_score(seed, dim)

        fit = format_fit_scores(
            evidence_bundle.platform, evidence_bundle.content_type,
            bool(evidence_bundle.transcript_text or evidence_bundle.body_excerpt),
        )
        scores["format_fit"] = max(fit.values()) if fit else 0

        rec_format = recommend_format(fit)
        worthiness = _mock_worthiness(scores)
        return {
            "scores": scores,
            "content_worthiness_score": worthiness,
            "recommended_content_format": rec_format,
            "recommended_angle": _mock_angle(rec_format, evidence_bundle.platform),
            "candidate_timestamps": _mock_timestamps(evidence_bundle),
            "reasons": [
                {"code": "MOCK_DETERMINISTIC", "signal": "mock",
                 "message": "deterministic mock provider (no model call)"}
            ],
        }


def _mock_worthiness(scores: dict[str, int | None]) -> int:
    """Worthiness = mean of scored semantic dimensions (deterministic)."""
    semantic = [v for d, v in scores.items()
                if d not in ("format_fit", "tenant_fit", "saturation_differentiation",
                             "originality_novelty", "reaction_potential", "commentary_value")
                and v is not None]
    if not semantic:
        return 0
    return int(round(sum(semantic) / len(semantic)))


def _mock_angle(rec_format: str, platform: str) -> str:
    if rec_format == "hold_unassigned":
        return ""
    return f"Deterministic angle for {rec_format} on {platform} (placeholder — real model deferred)."


def _mock_timestamps(evidence_bundle: Evidence) -> list[dict[str, Any]]:
    # Only suggest timestamps when there is actual text/media evidence.
    if not (evidence_bundle.transcript_text or evidence_bundle.bounded_media_windows):
        return []
    if evidence_bundle.candidate_timestamps:
        return list(evidence_bundle.candidate_timestamps)
    return [{"start_s": 0, "end_s": 30, "basis": "transcript-derived (placeholder)"}]


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
def assess_content(
    *,
    record: Any,
    brief: Any,
    evidence_bundle: Evidence,
    provider: ContentModelProvider,
    competition_count: int = 0,
    seen_titles: set[str] | None = None,
    has_differentiated_angle: bool = False,
    raw: dict[str, Any] | None = None,
) -> ContentAssessment:
    """Compose a validated ContentAssessment from structural + provider signals.

    ``raw`` may be supplied to avoid a second provider call when the caller has
    already invoked ``provider.assess`` (e.g. to capture usage/identity)."""
    level = evidence_bundle.level()

    # Structural (deterministic) signals.
    fit = format_fit_scores(
        evidence_bundle.platform, evidence_bundle.content_type,
        bool(evidence_bundle.transcript_text or evidence_bundle.body_excerpt),
    )
    structural: dict[str, int | None] = {
        "tenant_fit": tenant_fit_score(brief, evidence_bundle.title,
                                       evidence_bundle.body_excerpt,
                                       evidence_bundle.transcript_text),
        "format_fit": max(fit.values()) if fit else 0,
        "saturation_differentiation": saturation_differentiation_score(
            competition_count, has_differentiated_angle),
        "originality_novelty": originality_score(evidence_bundle.title, seen_titles),
    }

    # Provider (semantic) signals — gated by evidence level inside the provider.
    if raw is None:
        raw = provider.assess(evidence_bundle, brief)
    model_scores = dict(raw.get("scores") or {})
    # Provider must not claim dimensions its evidence can't support; drop them.
    allowed = evidence.allowed_dimensions(level)
    model_scores = {d: v for d, v in model_scores.items() if d in allowed}

    scores = {**structural, **model_scores}

    worthiness = raw.get("content_worthiness_score", 0)
    if not isinstance(worthiness, int):
        worthiness = int(round(float(worthiness or 0)))

    completeness = evidence_bundle.evidence_completeness()
    confidence = min(evidence.confidence_ceiling(level), completeness)

    flags: list[str] = []
    if level == evidence.LEVEL_METADATA:
        flags.append("METADATA_ONLY")
    if evidence_bundle.transcript_availability == TRANSCRIPT_NONE and level < evidence.LEVEL_TEXT:
        flags.append("NO_TRANSCRIPT")
    if evidence_bundle.transcript_availability in (TRANSCRIPT_AUTO, TRANSCRIPT_INCOMPLETE):
        flags.append("TRANSCRIPT_QUALITY_REDUCED")
    if evidence_bundle.transcript_availability == TRANSCRIPT_MISMATCH:
        flags.append("TRANSCRIPT_LANGUAGE_MISMATCH")

    idem = _assessment_idem_key(record.duplicate_fingerprint, "1.0.0", level, evidence_bundle)

    return ContentAssessment(
        duplicate_fingerprint=record.duplicate_fingerprint,
        tenant_id=record.tenant_id,
        profile_id=record.profile_id,
        platform=record.platform,
        content_assessment_version="1.0.0",
        evidence_level=level,
        evidence_types=evidence_bundle.evidence_types(),
        evidence_completeness=completeness,
        scores=scores,
        content_worthiness_score=worthiness,
        content_confidence=confidence,
        reasons=list(raw.get("reasons") or []),
        flags=flags,
        idempotency_key=idem,
        transcript_availability=evidence_bundle.transcript_availability,
        transcript_quality=evidence_bundle.transcript_quality,
        candidate_timestamps=list(raw.get("candidate_timestamps") or []),
        recommended_content_format=raw.get("recommended_content_format") or "hold_unassigned",
        recommended_angle=raw.get("recommended_angle") or "",
        model_provider=getattr(provider, "name", ""),
        model_version=getattr(provider, "version", ""),
        prompt_version="1.0.0",
    )


def _assessment_idem_key(fingerprint: str, version: str, level: int, evidence_bundle: Evidence) -> str:
    raw = f"{fingerprint}\x1f{version}\x1f{level}\x1f{evidence_bundle.evidence_types()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
