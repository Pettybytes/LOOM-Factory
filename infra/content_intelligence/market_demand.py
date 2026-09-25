"""Opt-in ``observed_demand_v1``: market demand, not content/title quality.

Established demand and emerging momentum are alternative routes. The existing
Topic Intelligence calibrations are retained: log-volume reaches 100 at 1M,
and observed velocity reaches 100 at 1,000 units/hour. Market still requires 75.
Age-adjusted volume and observed engagement floors remain unchanged. A missing
counter remains unknown; lifetime views/age is informational, never a mandatory
test of present momentum. No I/O, paid work, or media/content assessment occurs.
"""

from __future__ import annotations

import datetime as _dt
import math
from dataclasses import dataclass
from typing import Any

if __package__.startswith("infra."):
    from ..discovery.topic_intelligence import _VIEWS_CEILING, _VELOCITY_CEILING_PER_HOUR
    from ..discovery.velocity import compute_velocity
else:
    from discovery.topic_intelligence import _VIEWS_CEILING, _VELOCITY_CEILING_PER_HOUR
    from discovery.velocity import compute_velocity

from .fire import FIRE_MARKET_MIN
from .market_math import MIN_ENGAGEMENT_RATE, age_adjusted_floor
from .platform_metrics import REDDIT_MIN_SCORE, VIEW_FLOOR_PLATFORMS, normalize_market_evidence

POLICY_VERSION = "observed_demand_v1"


@dataclass(frozen=True)
class DemandDecision:
    score: int
    passed: bool
    reasons: tuple[str, ...]
    evidence: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {"score": self.score, "passed": self.passed,
                "reasons": list(self.reasons), "evidence": self.evidence}


def _get(record: Any, field: str, default=None):
    return record.get(field, default) if isinstance(record, dict) else getattr(record, field, default)


def _numeric_metrics(metrics: Any) -> dict[str, Any]:
    """Prevent nonfinite counters from becoming fabricated scores or invalid JSON."""
    if not isinstance(metrics, dict):
        return {}
    return {key: value for key, value in metrics.items()
            if not isinstance(value, (int, float)) or isinstance(value, bool)
            or (math.isfinite(value) and value >= 0)}


def _clamp(value: float) -> int:
    return max(0, min(100, round(value)))


def _observed_velocity(record: Any, platform: str):
    """Normalize native aliases before using the existing history derivative.

    Helix stores ``view_count``; other connectors use ``playCount`` or ``score``.
    Only numeric observations actually present in history are used. No synthetic
    zero-at-publication observation is added.
    """
    history = []
    for observation in _get(record, "metric_history", []) or []:
        if not isinstance(observation, dict):
            continue
        normalized = normalize_market_evidence({
            "platform": platform,
            "observed_metrics": _numeric_metrics(observation.get("metrics")),
        })
        value = (normalized.native.get("reddit_score") if platform == "reddit"
                 else normalized.views)
        if value is not None:
            history.append({"observed_at": observation.get("observed_at"),
                            "metrics": {"demand": value}})
    return compute_velocity(history, metric_key="demand")


def assess_market_demand(record: Any, *, now: _dt.datetime | None = None,
                         max_age_hours: float | None = None) -> DemandDecision:
    """Return auditable market score/gate without title, content or provenance inputs.

    ``max_age_hours`` is the caller's tenant freshness policy, not a hidden global
    recency restriction. Reddit uses observed native score, never invented views;
    its existing minimum score is preserved. The common log-volume calibration is
    the existing Topic Intelligence scale, including for Reddit native volume.
    """
    now = now or _dt.datetime.now(_dt.timezone.utc)
    platform = str(_get(record, "platform", "") or "").lower()
    ev = normalize_market_evidence({
        "platform": platform,
        "observed_metrics": _numeric_metrics(_get(record, "observed_metrics", {})),
        "publication_timestamp": _get(record, "publication_timestamp"),
    }, now=now)
    is_reddit = platform == "reddit"
    volume = ev.native.get("reddit_score") if is_reddit else ev.views
    min_views, min_likes = age_adjusted_floor(ev.age_hours)
    volume_floor = REDDIT_MIN_SCORE if is_reddit else min_views
    velocity = _observed_velocity(record, platform)
    established = (_clamp(100 * math.log10(volume + 1) / math.log10(_VIEWS_CEILING))
                   if volume is not None else 0)
    emerging = (_clamp(100 * velocity.value_per_hour / _VELOCITY_CEILING_PER_HOUR)
                if velocity.available else None)
    score = max(established, emerging if emerging is not None else 0)
    route = "established" if established >= FIRE_MARKET_MIN else (
        "emerging" if emerging is not None and emerging >= FIRE_MARKET_MIN else "none")

    reasons = []
    if platform not in (*VIEW_FLOOR_PLATFORMS, "reddit"):
        reasons.append("UNSUPPORTED_MARKET_PLATFORM")
    if volume is None:
        reasons.append("INSUFFICIENT_MARKET_EVIDENCE")
    elif volume < volume_floor:
        reasons.append("LOW_SCORE" if is_reddit else "LOW_VIEWS")
    if max_age_hours is not None and ev.age_hours is not None and ev.age_hours > max_age_hours:
        reasons.append("STALE")
    if is_reddit:
        if ev.native.get("reddit_is_meta"):
            reasons.append("SUBREDDIT_META")
    else:
        if ev.likes is not None and ev.likes < min_likes:
            reasons.append("LOW_LIKES")
        if ev.engagement is not None and (ev.views or 0) >= 1000 and ev.engagement < MIN_ENGAGEMENT_RATE:
            reasons.append("LOW_ENGAGEMENT")
    if score < FIRE_MARKET_MIN:
        reasons.append("MARKET_BELOW_75")

    evidence = ev.as_dict()
    # These averages are not the observed derivative; do not preserve ambiguous
    # `vph`/`score_per_hour` aliases for downstream consumers to mistake as current.
    evidence.pop("vph", None)
    lifetime_score = evidence.pop("reddit_score_per_hour", None)
    lifetime_comments = evidence.pop("reddit_comments_per_hour", None)
    evidence.update({
        "market_policy": POLICY_VERSION,
        "market_score": score,
        "market_threshold": FIRE_MARKET_MIN,
        "route": route,
        "demand_metric": "reddit_score" if is_reddit else "views",
        "demand_volume": volume,
        "minimum_volume": volume_floor,
        "established_score": established,
        "emerging_score": emerging,
        "lifetime_average_vph": ev.vph,
        "observed_velocity_per_hour": velocity.value_per_hour if velocity.available else None,
        "observed_velocity_metric": "reddit_score" if is_reddit else "views",
        "velocity_observation_count": velocity.n_observations,
        "velocity_earliest_observation": velocity.earliest_obs or None,
        "velocity_latest_observation": velocity.latest_obs or None,
        "velocity_unavailable_reason": velocity.reason or None,
    })
    if is_reddit:
        evidence.update(lifetime_average_score_per_hour=lifetime_score,
                        lifetime_average_comments_per_hour=lifetime_comments)
    return DemandDecision(score, not reasons, tuple(reasons), evidence)
