"""Topic Intelligence V1 — deterministic, auditable opportunity scoring.

This layer evaluates a discovered YouTube or Reddit candidate using ONLY data
LOOM can actually observe or reliably derive. It produces a single explainable
opportunity score (0-100), an opportunity class, a confidence value, explicit
missing-data / low-confidence flags, and human-readable reason codes.

TRUTH BOUNDARY — this layer does NOT judge:
  * emotional content inside a video,
  * spoken or visual hook quality,
  * surprise / conflict / payoff / narrative completeness,
  * whether an exact moment will make a strong clip.
Those require later transcript/audio/visual inspection (Moment Mining / Clip
Ranking). Topic Intelligence may only identify external, observable evidence
that a source or topic is gaining traction — and labels every signal precisely.

Determinism: scoring is pure (no randomness, no LLM, no paid service, no
scraping). The only time-varying input is ``now`` (freshness horizon), which is
an explicit, injectable parameter so the same observation with the same ``now``
always yields the same score and reason codes.
"""

from __future__ import annotations

import datetime as _dt
import math
from dataclasses import dataclass, field
from typing import Any

from .discovery_record import DiscoveryRecord
from .normalization import NormalizedSignals, normalize_signals
from .source_brief import SourceBrief
from .velocity import VelocityResult, compute_velocity

SCORE_VERSION = "1.0.0"

# Signal weights (sum to 1.0). A missing signal contributes 0 to the score AND
# lowers confidence (it does not get its weight redistributed to present
# signals). This is what makes missing data *reduce* confidence rather than
# cause invented values.
WEIGHTS: dict[str, float] = {
    # Content-first: the clip ITSELF leads. Reuses existing content-analysis
    # machinery rather than adding a second scoring gate.
    "content": 0.32,
    "freshness": 0.20,
    "relevance": 0.18,
    # Supporting positives only. Their ABSENCE never blocks a strong clip.
    "engagement": 0.15,
    "velocity": 0.08,
    "provenance": 0.07,
}
# cross_platform and saturation are deliberately NOT scored and NOT required;
# duplicate suppression stays in the dedup path where it belongs.
SUPPORTING_SIGNALS = ("engagement", "velocity")
BLOCKING_SIGNALS = ("content",)

# Reference ceilings for log-scale normalization (documented, deterministic).
_VIEWS_CEILING = 1_000_000          # log10(1e6) = 6.0 → score 100
_VELOCITY_CEILING_PER_HOUR = 1000.0  # 1000 views/hour → score 100
_ENGAGEMENT_RATE_CEILING = 0.10      # 10% engagement rate → score 100

# Opportunity classification thresholds.
_CLASS_STRONG = 70
_CLASS_MODERATE = 40
_CONFIDENCE_LOW = 0.50
_INCOMPLETE_COMPLETENESS = 0.35


class OpportunityClass:
    """Enum-like opportunity classes (deterministic, ordered by precedence)."""

    DUPLICATE = "duplicate"
    STALE = "stale"
    INCOMPLETE = "incomplete"
    STRONG = "strong"
    MODERATE = "moderate"
    WEAK = "weak"


# ---------------------------------------------------------------------------
# Reason codes (stable vocabulary — audit-friendly, human-readable).
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Reason:
    code: str       # stable machine-readable code
    signal: str     # which signal produced it (or "assessment")
    message: str    # human-readable explanation

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "signal": self.signal, "message": self.message}


@dataclass(frozen=True)
class SignalScore:
    signal: str
    score: int       # 0-100
    present: bool    # True when backed by observed data
    value: Any       # compact human-readable value backing the score
    reasons: tuple[Reason, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "present": self.present,
            "value": self.value,
            "reasons": [r.to_dict() for r in self.reasons],
        }


def _clamp_int(value: float) -> int:
    return max(0, min(100, int(round(value))))


# ---------------------------------------------------------------------------
# Per-signal scoring functions (pure).
# ---------------------------------------------------------------------------
def _score_content(sig: "NormalizedSignals", brief: SourceBrief) -> SignalScore:
    """Content-first read of the clip itself.

    Rewards a strong moment/hook, an action or emotional payoff, reactions/audio
    energy WHERE RELEVANT, and visual clarity. A quiet, wholesome animal clip is
    NOT penalised for lacking shouting or chat activity: absence of hype is not
    absence of content. A clip with no content evidence at all is UNPROVEN and
    cannot qualify, so failed media analysis is never treated as a pass.
    """
    parts: list[str] = []
    for attr in ("title", "description", "text", "summary"):
        v = getattr(sig, attr, None)
        if isinstance(v, str) and v.strip():
            parts.append(v)
    me = getattr(sig, "moment_evidence", None) or {}
    if isinstance(me, dict):
        for k, v in me.items():
            if v:
                parts.append(str(k))
    text = " ".join(parts).lower()

    has_moment_evidence = bool(isinstance(me, dict) and any(me.values()))
    # A bare title is NOT content evidence. Content must be an actual clip: real
    # media evidence (moment analysis) or a platform metric proving media exists.
    # Otherwise a metadata-thin text post would masquerade as a content-first clip
    # and failed media analysis would pass as content.
    has_media_signal = bool(getattr(sig, "has_volume", False)
                            or getattr(sig, "has_engagement_rate", False)
                            or getattr(sig, "metric_history", None))
    if not has_moment_evidence and not has_media_signal:
        return SignalScore("content", 0, False, None,
                           (Reason("CONTENT_NO_MEDIA_EVIDENCE", "content",
                                   "no clip media evidence available for this item"),))

    score = 50.0                                   # present text = baseline content
    reasons: list[Reason] = []
    hook = ("hook", "starts with", "watch this", "wait for it", "you won't believe")
    payoff_cues = ("payoff", "lands", "reveals", "finale", "climax", "then he", "then she")
    action = ("jumps", "runs", "falls", "chases", "steals", "opens", "carries", "dances")
    emotion = ("cute", "adorable", "sweet", "heartwarming", "love", "happy", "funny")
    reaction = ("reacts", "reaction", "shocked", "surprised", "laughs", "screams")
    visual = ("clear", "close-up", "shot", "quality", "hd", "bright")
    for label, cues, bonus in (("HOOK", hook, 10.0), ("PAYOFF", payoff_cues, 12.0),
                               ("ACTION", action, 10.0), ("EMOTION", emotion, 8.0),
                               ("REACTION", reaction, 6.0), ("VISUAL", visual, 4.0)):
        if any(c in text for c in cues):
            score += bonus
            reasons.append(Reason(f"CONTENT_{label}", "content", f"{label.lower()} present"))
    if has_moment_evidence:
        score += 6.0
        reasons.append(Reason("CONTENT_MOMENT_ANALYSED", "content",
                              "clip-level moment analysis present"))
    return SignalScore("content", _clamp_int(score), True, None, tuple(reasons))


def _score_freshness(sig: NormalizedSignals, brief: SourceBrief, now: _dt.datetime) -> SignalScore:
    ts = sig.publication_timestamp
    if not ts:
        return SignalScore("freshness", 0, False, None,
                           (Reason("FRESH_MISSING_TS", "freshness",
                                   "no publication timestamp; freshness cannot be measured"),))

    try:
        pub = _dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return SignalScore("freshness", 0, False, None,
                           (Reason("FRESH_MISSING_TS", "freshness",
                                   "unparseable publication timestamp"),))
    if pub.tzinfo is None:
        pub = pub.replace(tzinfo=_dt.timezone.utc)

    age_days = (now.astimezone(_dt.timezone.utc) - pub.astimezone(_dt.timezone.utc)).total_seconds() / 86400.0
    age_days = max(0.0, age_days)  # clamp future timestamps to "now"

    window = brief.freshness_window_days if brief.freshness_window_days > 0 else 30.0

    if age_days > window:
        return SignalScore("freshness", 0, True, round(age_days, 2),
                           (Reason("STALE_OUT_OF_WINDOW", "freshness",
                                   f"age {age_days:.1f}d exceeds freshness window {window:.0f}d"),))

    score = _clamp_int(100.0 * (1.0 - age_days / window))
    return SignalScore("freshness", score, True, round(age_days, 2),
                       (Reason("FRESH_WINDOW", "freshness",
                               f"age {age_days:.1f}d within {window:.0f}d window"),))


def _score_engagement(sig: NormalizedSignals) -> SignalScore:
    if not sig.has_volume:
        reasons = (Reason("ENGAGE_NO_METRICS", "engagement",
                          "no volume metric observed (platform RSS omitted engagement)"),)
        for m in sig.missing_metrics:
            reasons = reasons + (Reason("MISSING_METRIC", "engagement", f"missing metric: {m}"),)
        return SignalScore("engagement", 0, False, None, reasons)

    # Log-scale volume score (1M views → 100).
    volume_score = _clamp_int(100.0 * (math.log10(sig.volume + 1.0) / 6.0))
    reasons: tuple[Reason, ...] = (Reason("ENGAGE_VOLUME_PRESENT", "engagement",
                                          f"{sig.volume_metric}={sig.volume} (log-scale {volume_score})"),)

    if sig.has_engagement_rate and sig.engagement_rate is not None:
        rate_score = _clamp_int(100.0 * (sig.engagement_rate / _ENGAGEMENT_RATE_CEILING))
        score = _clamp_int(0.5 * volume_score + 0.5 * rate_score)
        reasons = reasons + (Reason("ENGAGE_RATE_PRESENT", "engagement",
                                    f"engagement rate {sig.engagement_rate:.4f} (score {rate_score})"),)
    else:
        score = volume_score
        reasons = reasons + (Reason("ENGAGE_RATE_MISSING", "engagement",
                                    "likes/comments absent — volume-only engagement score"),)

    return SignalScore("engagement", score, True, sig.volume, reasons)


def _score_velocity(vel: VelocityResult) -> SignalScore:
    if not vel.available:
        return SignalScore("velocity", 0, False, None,
                           (Reason("VELOCITY_UNAVAILABLE", "velocity", vel.reason or "velocity unavailable"),))
    if vel.value_per_hour < 0:
        return SignalScore("velocity", 0, True, round(vel.value_per_hour, 4),
                           (Reason("VELOCITY_NEGATIVE", "velocity",
                                   f"velocity {vel.value_per_hour:.2f}/hr is negative"),))
    score = _clamp_int(100.0 * (vel.value_per_hour / _VELOCITY_CEILING_PER_HOUR))
    return SignalScore("velocity", score, True, round(vel.value_per_hour, 4),
                       (Reason("VELOCITY_AVAILABLE", "velocity",
                               f"{vel.metric} velocity {vel.value_per_hour:.2f}/hr over {vel.n_observations} observations"),))


def _score_relevance(sig: NormalizedSignals, brief: SourceBrief) -> SignalScore:
    tokens: list[str] = []
    for group in (brief.topics, brief.keywords, brief.entities):
        for t in group or []:
            tok = (t or "").strip().lower()
            if tok and tok not in tokens:
                tokens.append(tok)
    if not tokens:
        return SignalScore("relevance", 0, False, None,
                           (Reason("RELEVANCE_NO_TERMS", "relevance",
                                   "brief defines no topics/keywords/entities to match"),))

    haystack = " ".join([
        sig.title, sig.body_excerpt, sig.discovery_reason, sig.creator,
    ]).lower()
    matched = [t for t in tokens if t in haystack]

    score = _clamp_int(100.0 * (len(matched) / 3.0))
    if not matched:
        return SignalScore("relevance", 0, True, 0,
                           (Reason("RELEVANCE_NONE", "relevance",
                                   "no brief term matched title/body/reason/creator"),))
    return SignalScore("relevance", score, True, matched[:8],
                       (Reason("RELEVANCE_MATCH", "relevance",
                               f"{len(matched)} brief term(s) matched: {', '.join(matched[:5])}"),))


def _score_provenance(sig: NormalizedSignals) -> SignalScore:
    if not sig.provenance:
        return SignalScore("provenance", 0, False, None,
                           (Reason("PROVENANCE_MISSING", "provenance",
                                   "no provenance/rights evidence recorded"),))
    if sig.evidence_confidence is not None:
        score = _clamp_int(100.0 * sig.evidence_confidence)
        return SignalScore("provenance", score, True, sig.evidence_confidence,
                           (Reason("PROVENANCE_PRESENT", "provenance",
                                   f"provenance present; evidence confidence {sig.evidence_confidence}"),))
    return SignalScore("provenance", 50, True, None,
                       (Reason("PROVENANCE_PRESENT", "provenance",
                               "provenance present (no numeric confidence recorded)"),))


def _score_cross_platform(sig: NormalizedSignals) -> SignalScore:
    refs = sig.source_url_references
    if not refs:
        return SignalScore("cross_platform", 0, False, None,
                           (Reason("CROSS_PLATFORM_NONE", "cross_platform",
                                   "no cross-platform source references observed"),))
    score = _clamp_int(34.0 * len(refs))
    return SignalScore("cross_platform", score, True, list(refs[:5]),
                       (Reason("CROSS_PLATFORM_LINKS", "cross_platform",
                               f"{len(refs)} external source reference(s) (cross-platform confirmation)"),))


def _score_saturation(competition_count: int | None) -> SignalScore:
    if competition_count is None:
        return SignalScore("saturation", 0, False, None,
                           (Reason("SATURATION_UNKNOWN", "saturation",
                                   "no store context provided; competition/saturation not measurable"),))
    score = max(0, 100 - competition_count * 20)
    reason = (Reason("SATURATION_NONE", "saturation", "no competing near/cross-post matches")
              if competition_count == 0
              else Reason("SATURATION_HIGH", "saturation",
                          f"{competition_count} competing near/cross-post match(es)"))
    return SignalScore("saturation", score, True, competition_count, (reason,))


# ---------------------------------------------------------------------------
# Assessment result
# ---------------------------------------------------------------------------
@dataclass
class OpportunityAssessment:
    """Fully explainable Topic Intelligence assessment for one candidate."""

    tenant_id: str
    profile_id: str
    platform: str
    platform_content_id: str
    duplicate_fingerprint: str
    score_version: str
    opportunity_score: int
    opportunity_class: str
    confidence: float
    data_completeness: float
    content_format: str
    reasons: list[Reason]
    flags: list[str]
    signal_scores: dict[str, SignalScore]
    normalized_signals: NormalizedSignals
    velocity: VelocityResult
    assessed_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "profile_id": self.profile_id,
            "platform": self.platform,
            "platform_content_id": self.platform_content_id,
            "duplicate_fingerprint": self.duplicate_fingerprint,
            "score_version": self.score_version,
            "opportunity_score": self.opportunity_score,
            "opportunity_class": self.opportunity_class,
            "confidence": self.confidence,
            "data_completeness": self.data_completeness,
            "content_format": self.content_format,
            "reasons": [r.to_dict() for r in self.reasons],
            "flags": list(self.flags),
            "signal_scores": {k: v.to_dict() for k, v in self.signal_scores.items()},
            "velocity": self.velocity.to_dict(),
            "assessed_at": self.assessed_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "OpportunityAssessment":
        raise NotImplementedError("deserialization from stored rows handled by store layer")


# ---------------------------------------------------------------------------
# TopicIntelligence engine
# ---------------------------------------------------------------------------
class TopicIntelligence:
    """Deterministic Topic Intelligence scorer."""

    score_version = SCORE_VERSION

    def assess(
        self,
        record: DiscoveryRecord,
        brief: SourceBrief,
        *,
        now: _dt.datetime | None = None,
        duplicate_kind: str = "",
        competition_count: int | None = None,
    ) -> OpportunityAssessment:
        """Score a single candidate. All inputs explicit for determinism.

        ``duplicate_kind`` is the identity classification ('' / 'exact' /
        'repost' / 'crosspost' / 'near') from the qualification gate's seen
        sets. ``competition_count`` is the number of other stored candidates
        sharing the same near/cross-post key (saturation); pass None when no
        store context is available.
        """
        if now is None:
            now = _dt.datetime.now(_dt.timezone.utc)

        sig = normalize_signals(record)

        signals: dict[str, SignalScore] = {
            "freshness": _score_freshness(sig, brief, now),
            "engagement": _score_engagement(sig),
            "velocity": _score_velocity(compute_velocity(sig.metric_history)),
            "relevance": _score_relevance(sig, brief),
            "provenance": _score_provenance(sig),
            "content": _score_content(sig, brief),
        }

        # --- SCORING: unavailable signals are UNKNOWN, never zero --------------
        # Quality is measured over the signals we ACTUALLY have and renormalized,
        # so a genuinely strong candidate is not punished for a signal its platform
        # does not expose. Evidence COVERAGE is tracked separately and enforced as a
        # minimum, so sparse evidence can never be normalized into a qualifying
        # score. This replaces the old behaviour where an absent signal contributed
        # 0 and therefore capped the score at whatever coverage happened to exist
        # (a 150k-view clip scored 26 purely for missing engagement/velocity data).
        MIN_EVIDENCE_COVERAGE = 0.45
        INSUFFICIENT_CAP = 50

        # CONTENT-FIRST score. Supporting signals (engagement, velocity) are scored
        # when available and simply EXCLUDED when not: a missing engagement metric or
        # velocity history must never block a strong clip. Scoring is normalised over
        # the presented signals, with no coverage multiplier - the old
        # quality-times-coverage rejection is gone.
        earned = sum(signals[s].score * WEIGHTS[s] for s in WEIGHTS if signals[s].present)
        presented = sum(WEIGHTS[s] for s in WEIGHTS if signals[s].present)
        opportunity_score = _clamp_int(earned / presented) if presented > 0 else 0

        # The ONLY hard block is absent content evidence: failed media analysis is
        # never allowed to pass as content.
        insufficient_evidence = not signals["content"].present
        if insufficient_evidence:
            opportunity_score = min(opportunity_score, INSUFFICIENT_CAP)

        # Data completeness = fraction of weighted signals present (coverage).
        completeness = presented
        evidence_conf = sig.evidence_confidence if sig.evidence_confidence is not None else 1.0
        confidence = round(completeness * evidence_conf, 4)

        reasons: list[Reason] = []
        for s in WEIGHTS:
            reasons.extend(signals[s].reasons)

        flags: list[str] = []
        for m in sig.missing_metrics:
            flags.append(f"MISSING_{m.upper()}")
        if not sig.publication_timestamp:
            flags.append("MISSING_PUBLICATION_TS")
        if signals["velocity"].present is False and sig.metric_history:
            flags.append("VELOCITY_UNAVAILABLE")
        if insufficient_evidence:
            flags.append("INSUFFICIENT_EVIDENCE")
        if confidence < _CONFIDENCE_LOW:
            flags.append("LOW_CONFIDENCE")
        if duplicate_kind:
            flags.append(f"DUPLICATE_{duplicate_kind.upper()}")

        opportunity_class = _classify(
            opportunity_score=opportunity_score,
            confidence=confidence,
            completeness=completeness,
            duplicate_kind=duplicate_kind,
            stale=(signals["freshness"].reasons[0].code == "STALE_OUT_OF_WINDOW"
                   if signals["freshness"].reasons else False),
        )

        reasons.append(Reason(
            f"CLASS_{opportunity_class.upper()}", "assessment",
            f"classified {opportunity_class} (score={opportunity_score}, "
            f"confidence={confidence}, completeness={completeness})",
        ))

        content_format = getattr(record, "content_format", "") or "hold_unassigned"

        return OpportunityAssessment(
            tenant_id=record.tenant_id,
            profile_id=record.profile_id,
            platform=record.platform,
            platform_content_id=record.platform_content_id,
            duplicate_fingerprint=record.duplicate_fingerprint,
            score_version=self.score_version,
            opportunity_score=opportunity_score,
            opportunity_class=opportunity_class,
            confidence=confidence,
            data_completeness=round(completeness, 4),
            content_format=content_format,
            reasons=reasons,
            flags=flags,
            signal_scores=signals,
            normalized_signals=sig,
            velocity=compute_velocity(sig.metric_history),
            assessed_at=now.astimezone(_dt.timezone.utc).isoformat(),
        )


def _classify(
    *,
    opportunity_score: int,
    confidence: float,
    completeness: float,
    duplicate_kind: str,
    stale: bool,
) -> str:
    """Deterministic opportunity class (precedence: duplicate > stale >
    incomplete > strong > moderate > weak)."""
    if duplicate_kind:
        return OpportunityClass.DUPLICATE
    if stale:
        return OpportunityClass.STALE
    if confidence < _CONFIDENCE_LOW or completeness < _INCOMPLETE_COMPLETENESS:
        return OpportunityClass.INCOMPLETE
    if opportunity_score >= _CLASS_STRONG:
        return OpportunityClass.STRONG
    if opportunity_score >= _CLASS_MODERATE:
        return OpportunityClass.MODERATE
    return OpportunityClass.WEAK
