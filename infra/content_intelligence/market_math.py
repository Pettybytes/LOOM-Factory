"""Deterministic market math for moment sourcing. Pure: no network, no model.

LOOM sources MOMENTS people react to. The market layer must reject noise BEFORE
any content analysis — an 84-view candidate should die here, never in front of a
human reviewer.

Ideas were studied from donor repositories; none of those carry a licence file,
so nothing here is copied. These are independent LOOM-native implementations of
the concepts: creator-relative median baseline, views-per-hour velocity,
age-adjusted absolute floors, engagement, and a cheap market gate.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

# Age-bucketed absolute floors. A moment that is hours old needs far less
# absolute traction than one that is a week old — judging young and old
# candidates by one number is the mistake this removes.
DEFAULT_AGE_FLOORS: tuple[tuple[float, int, int], ...] = (
    (6.0, 1_000, 50),        # (max_age_hours, min_views, min_likes)
    (24.0, 3_000, 100),
    (72.0, 8_000, 200),
    (168.0, 15_000, 300),
    (float("inf"), 30_000, 500),
)

MIN_VPH = 150.0                 # views per hour floor (velocity)
MIN_ENGAGEMENT_RATE = 0.01      # likes+comments+shares / views
MIN_OUTLIER_MULTIPLE = 2.0      # creator-relative: views vs own median
DEFAULT_BASELINE_WINDOW = 30


def _as_dt(value: Any) -> _dt.datetime | None:
    if isinstance(value, _dt.datetime):
        dt = value
    elif isinstance(value, str) and value.strip():
        try:
            dt = _dt.datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=_dt.timezone.utc)


def age_hours(published_at: Any, now: _dt.datetime | None = None) -> float | None:
    dt = _as_dt(published_at)
    if dt is None:
        return None
    now = now or _dt.datetime.now(_dt.timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=_dt.timezone.utc)
    return max(0.0, (now - dt).total_seconds() / 3600.0)


def median(values: Iterable[float]) -> float | None:
    """Median, not mean: one viral post must not drag the baseline."""
    vals = sorted(float(v) for v in values if v is not None)
    n = len(vals)
    if n == 0:
        return None
    mid = n // 2
    return vals[mid] if n % 2 else (vals[mid - 1] + vals[mid]) / 2.0


def views_per_hour(views: int | None, published_at: Any,
                   now: _dt.datetime | None = None) -> float | None:
    """Velocity. Below one hour of life VPH is unstable, so it is not claimed."""
    if views is None or views < 0:
        return None
    age = age_hours(published_at, now)
    if age is None or age < 1.0:
        return None
    return round(float(views) / age, 4)


def creator_baseline(samples: Sequence[Any], *, window: int = DEFAULT_BASELINE_WINDOW) -> float | None:
    """Median views of a creator's own recent posts.

    ``samples`` accepts numbers or ``{"views": int, "is_pinned": bool}`` dicts;
    pinned posts are excluded because they are not representative of baseline
    performance.
    """
    vals: list[float] = []
    for s in samples or []:
        if isinstance(s, dict):
            if s.get("is_pinned"):
                continue
            v = s.get("views")
        else:
            v = s
        if isinstance(v, (int, float)) and v >= 0:
            vals.append(float(v))
    return median(vals[:window] if window and window > 0 else vals)


def outlier_multiple(views: int | None, baseline_median: float | None) -> float | None:
    """Creator-relative lift: this post's views / the creator's own median."""
    if views is None or not baseline_median or baseline_median <= 0:
        return None
    return round(float(views) / float(baseline_median), 4)


def engagement_rate(likes: int | None, comments: int | None, shares: int | None,
                    views: int | None) -> float | None:
    if not views or views <= 0:
        return None
    total = sum(int(x) for x in (likes, comments, shares) if isinstance(x, (int, float)))
    return round(total / float(views), 6)


def age_adjusted_floor(age_h: float | None,
                       tiers: Sequence[tuple[float, int, int]] = DEFAULT_AGE_FLOORS) -> tuple[int, int]:
    """(min_views, min_likes) for a candidate of this age."""
    if age_h is None:
        return tiers[-1][1], tiers[-1][2]
    for max_age, min_views, min_likes in tiers:
        if age_h <= max_age:
            return min_views, min_likes
    return tiers[-1][1], tiers[-1][2]


@dataclass(frozen=True)
class MarketDecision:
    """Deterministic market verdict with explicit reasons (no silent rejection)."""

    passed: bool
    reasons: tuple[str, ...] = ()
    metrics: dict[str, Any] = field(default_factory=dict)


def market_gate(*, views: int | None, likes: int | None = None,
                comments: int | None = None, shares: int | None = None,
                published_at: Any = None, baseline_median: float | None = None,
                now: _dt.datetime | None = None) -> MarketDecision:
    """Cheap market rejection, BEFORE any content analysis.

    Rejects on: absolute age-adjusted views/likes floor, velocity (VPH),
    engagement rate, and — when a creator baseline is known — creator-relative
    outlier multiple. Reasons are always returned.
    """
    if views is None:
        return MarketDecision(False, ("no_view_count",), {})

    age = age_hours(published_at, now)
    vph = views_per_hour(views, published_at, now)
    mult = outlier_multiple(views, baseline_median)
    eng = engagement_rate(likes, comments, shares, views)
    min_views, min_likes = age_adjusted_floor(age)
    metrics = {"views": views, "age_hours": age, "vph": vph, "outlier_multiple": mult,
               "engagement_rate": eng, "min_views": min_views, "min_likes": min_likes}

    reasons: list[str] = []
    if views < min_views:
        reasons.append(f"below_age_adjusted_views_floor({views}<{min_views})")
    if isinstance(likes, int) and likes < min_likes:
        reasons.append(f"below_age_adjusted_likes_floor({likes}<{min_likes})")
    if vph is not None and vph < MIN_VPH:
        reasons.append(f"below_velocity_floor({vph}<{MIN_VPH})")
    if eng is not None and views >= 1_000 and eng < MIN_ENGAGEMENT_RATE:
        reasons.append(f"below_engagement_floor({eng}<{MIN_ENGAGEMENT_RATE})")
    if mult is not None and mult < MIN_OUTLIER_MULTIPLE:
        reasons.append(f"below_creator_outlier_multiple({mult}<{MIN_OUTLIER_MULTIPLE})")

    return MarketDecision(not reasons, tuple(reasons), metrics)
