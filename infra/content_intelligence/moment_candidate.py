"""The canonical MomentCandidate — one production object, existing contracts reused.

Assembled from the contracts LOOM already has (DiscoveryRecord, MarketEvidence,
Evidence, SourceBrief, Topic Intelligence, Content Intelligence, Combined
Selection). This is NOT a parallel architecture: it is the single object the
funnel carries forward.

Every unavailable field is explicitly None / "unavailable" — evidence is never
fabricated, and a missing value can never read as a zero.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

UNAVAILABLE = "unavailable"

# Moment-evidence fields that only a bounded inspection pass can fill. They are
# declared here so the shape is stable while remaining explicitly unavailable.
MOMENT_EVIDENCE_FIELDS: tuple[str, ...] = (
    "preview_reference", "hook_0_3s", "action_activity", "setup",
    "anticipation_escalation", "payoff", "reaction_resolution",
    "emotion_event_type", "reaction_angle", "exact_timestamps",
)


@dataclass
class MomentCandidate:
    # -- identity ---------------------------------------------------------
    tenant_id: str = ""
    platform: str = ""
    source_id: str = ""
    source_url: str = ""
    creator_id: str = ""
    creator_name: str = ""
    published_at: Any = None
    source_duration: float | None = None
    moment_start: float | None = None
    moment_end: float | None = None
    moment_duration: float | None = None

    # -- market -----------------------------------------------------------
    views: int | None = None
    likes: int | None = None
    comments: int | None = None
    shares: int | None = None
    saves: int | None = None
    age_hours: float | None = None
    vph: float | None = None
    engagement_rate: float | None = None
    creator_baseline_state: str = UNAVAILABLE
    creator_baseline: float | None = None
    outlier_multiple: float | None = None
    outlier_label: str = UNAVAILABLE
    baseline_samples: int = 0
    native_rank: Any = None
    provenance: str = ""
    market_gate_passed: bool = False
    market_rejection_reasons: tuple[str, ...] = ()

    # -- moment evidence (unavailable until an inspection pass) ------------
    moment_evidence: dict[str, Any] = field(default_factory=lambda: {
        k: None for k in MOMENT_EVIDENCE_FIELDS})
    evidence_depth: str = UNAVAILABLE

    # -- fit / scoring -----------------------------------------------------
    tenant_fit_verdict: str = ""
    tenant_fit_reasons: tuple[str, ...] = ()
    market_score: int | None = None
    content_score: int | None = None
    selection_score: int | None = None
    evidence_confidence: int | None = None
    fire_result: str = UNAVAILABLE
    rejection_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def from_market_and_market_evidence(record: Any, market_evidence: Any,
                                    *, tenant_fit_result: Any = None,
                                    relative: dict[str, Any] | None = None) -> MomentCandidate:
    """Build a MomentCandidate from a DiscoveryRecord + normalized market evidence."""
    g = lambda o, k, d=None: getattr(o, k, d)  # noqa: E731
    rel = relative or {}
    return MomentCandidate(
        tenant_id=str(g(record, "tenant_id", "") or ""),
        platform=str(g(record, "platform", "") or ""),
        source_id=str(g(record, "platform_content_id", "") or ""),
        source_url=str(g(record, "canonical_url", "") or ""),
        creator_id=str(g(record, "creator_channel_identity", "") or ""),
        creator_name=str(g(record, "creator_channel_identity", "") or ""),
        published_at=g(record, "publication_timestamp"),
        views=g(market_evidence, "views"), likes=g(market_evidence, "likes"),
        comments=g(market_evidence, "comments"), shares=g(market_evidence, "shares"),
        saves=g(market_evidence, "saves"), age_hours=g(market_evidence, "age_hours"),
        vph=g(market_evidence, "vph"), engagement_rate=g(market_evidence, "engagement"),
        creator_baseline_state=rel.get("creator_baseline_state", UNAVAILABLE),
        creator_baseline=rel.get("creator_baseline"),
        outlier_multiple=rel.get("outlier_multiple"),
        outlier_label=rel.get("outlier_label", UNAVAILABLE),
        baseline_samples=int(rel.get("baseline_samples") or 0),
        native_rank=(g(market_evidence, "native", {}) or {}).get("reddit_rank"),
        provenance=str(g(market_evidence, "provenance", "") or ""),
        tenant_fit_verdict=(getattr(tenant_fit_result, "verdict", "") if tenant_fit_result else ""),
        tenant_fit_reasons=tuple(getattr(tenant_fit_result, "reasons", ()) or ()) if tenant_fit_result else (),
    )
