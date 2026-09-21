"""Creator performance history -> durable, honest baselines.

The math lives in ``market_math``; this module gives it durable evidence and an
explicit availability contract. A baseline is NEVER fabricated: with too little
history the answer is UNKNOWN, and the caller must not fail an otherwise-strong
candidate merely because a baseline is unavailable.

Rules: MEDIAN not mean, bounded recent window, pinned/admin excluded.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from .market_math import creator_baseline, median, outlier_multiple

KNOWN = "known"
UNAVAILABLE = "unavailable"
INSUFFICIENT_HISTORY = "insufficient_history"

MIN_BASELINE_SAMPLES = 5
DEFAULT_WINDOW = 30

# Outlier interpretation (unchanged from the approved contract).
ORDINARY_MAX = 2.0
OUTLIER_MAX = 5.0
STRONG_OUTLIER_MAX = 10.0


@dataclass(frozen=True)
class BaselineResult:
    state: str                      # known | unavailable | insufficient_history
    value: float | None = None      # median views, only when state == known
    samples: int = 0
    window: int = DEFAULT_WINDOW
    note: str = ""

    @property
    def available(self) -> bool:
        return self.state == KNOWN and self.value is not None


def outlier_label(multiple: float | None) -> str:
    """<2x ordinary · 2-5x outlier · 5-10x strong outlier · >=10x breakout."""
    if multiple is None:
        return UNAVAILABLE
    if multiple < ORDINARY_MAX:
        return "ordinary"
    if multiple < OUTLIER_MAX:
        return "outlier"
    if multiple < STRONG_OUTLIER_MAX:
        return "strong_outlier"
    return "breakout"


def _published_key(obs: dict[str, Any]) -> str:
    return str(obs.get("published_at") or obs.get("observed_at") or "")


def baseline_from_observations(observations: Iterable[dict[str, Any]],
                               *, window: int = DEFAULT_WINDOW,
                               minimum: int = MIN_BASELINE_SAMPLES) -> BaselineResult:
    """Median baseline from a creator's own recent observations.

    Pinned/admin observations are excluded. Fewer than ``minimum`` usable
    samples returns INSUFFICIENT_HISTORY with an explicit note — never a made-up
    number, and never zero.
    """
    rows = [o for o in (observations or []) if isinstance(o, dict)]
    # Most recent first, then take the bounded window.
    rows.sort(key=_published_key, reverse=True)
    usable = [r for r in rows if not r.get("is_pinned")][:window if window > 0 else len(rows)]
    vals = [r.get("views") for r in usable if isinstance(r.get("views"), (int, float))]
    if len(vals) < minimum:
        return BaselineResult(INSUFFICIENT_HISTORY, None, len(vals), window,
                             f"needs>={minimum} unpinned observations with views")
    med = creator_baseline(usable, window=window)
    if med is None:
        return BaselineResult(UNAVAILABLE, None, len(vals), window, "no usable views")
    return BaselineResult(KNOWN, med, len(vals), window)


def observations_for(rows: Iterable[dict[str, Any]], *, tenant_id: str, platform: str,
                     creator_id: str) -> list[dict[str, Any]]:
    """CREATOR + PLATFORM isolation: a baseline only ever sees its own creator's
    history on its own platform within its own tenant."""
    return [r for r in (rows or []) if isinstance(r, dict)
            and r.get("tenant_id") == tenant_id
            and r.get("platform") == platform
            and r.get("creator_id") == creator_id]


def latest_per_source(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse repeated observations of one source to its most recent row.

    The database enforces this with a unique key; this keeps the same guarantee
    available to pure callers and tests so replay cannot inflate history.
    """
    best: dict[tuple, dict[str, Any]] = {}
    for r in (rows or []):
        if not isinstance(r, dict):
            continue
        key = (r.get("tenant_id"), r.get("platform"), r.get("creator_id"),
               r.get("source_content_id"))
        cur = best.get(key)
        if cur is None or str(r.get("observed_at") or "") >= str(cur.get("observed_at") or ""):
            best[key] = r
    return list(best.values())


def baseline_for(rows: Iterable[dict[str, Any]], *, tenant_id: str, platform: str,
                 creator_id: str, window: int = DEFAULT_WINDOW,
                 minimum: int = MIN_BASELINE_SAMPLES) -> BaselineResult:
    """The supported entry point: isolate, dedupe, then baseline."""
    isolated = observations_for(rows, tenant_id=tenant_id, platform=platform,
                                creator_id=creator_id)
    return baseline_from_observations(latest_per_source(isolated), window=window,
                                      minimum=minimum)


def relative_evidence(views: int | None, baseline: BaselineResult) -> dict[str, Any]:
    """Baseline + outlier multiple for downstream market evidence.

    Distinguishes known / unavailable / insufficient_history so a missing
    baseline can never masquerade as an ordinary performer.
    """
    if not baseline.available or views is None:
        return {"creator_baseline_state": baseline.state, "creator_baseline": None,
                "outlier_multiple": None, "outlier_label": UNAVAILABLE,
                "baseline_samples": baseline.samples}
    mult = outlier_multiple(views, baseline.value)
    return {"creator_baseline_state": KNOWN, "creator_baseline": baseline.value,
            "outlier_multiple": mult, "outlier_label": outlier_label(mult),
            "baseline_samples": baseline.samples}


def observation_row(*, tenant_id: str, platform: str, creator_id: str, source_content_id: str,
                    views: int | None = None, likes: int | None = None,
                    comments: int | None = None, shares: int | None = None,
                    saves: int | None = None, published_at: Any = None,
                    creator_name: str = "", is_pinned: bool = False,
                    provider: str = "", provenance: str = "",
                    observed_at: _dt.datetime | None = None) -> dict[str, Any]:
    """One durable observation row (upsert key: tenant+platform+creator+source)."""
    return {"tenant_id": tenant_id, "platform": platform, "creator_id": creator_id,
            "creator_name": creator_name, "source_content_id": source_content_id,
            "published_at": published_at, "observed_at":
                (observed_at or _dt.datetime.now(_dt.timezone.utc)).isoformat(),
            "views": views, "likes": likes, "comments": comments, "shares": shares,
            "saves": saves, "is_pinned": bool(is_pinned), "provider": provider,
            "provenance": provenance}


def observations_from_record(record: Any, *, tenant_id: str | None = None,
                             baseline_window: int = DEFAULT_WINDOW) -> dict[str, Any]:
    """Observation row built from an existing DiscoveryRecord (one contract, no parallel model)."""
    m = dict(getattr(record, "observed_metrics", {}) or {})
    def pick(*keys):
        for k in keys:
            v = m.get(k)
            if isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0:
                return int(v)
        return None
    return observation_row(
        tenant_id=tenant_id or str(getattr(record, "tenant_id", "") or ""),
        platform=str(getattr(record, "platform", "") or "").lower(),
        creator_id=str(getattr(record, "creator_channel_identity", "") or ""),
        creator_name=str(getattr(record, "creator_channel_identity", "") or ""),
        source_content_id=str(getattr(record, "platform_content_id", "") or ""),
        views=pick("views", "viewCount", "playCount", "videoPlayCount", "view_count"),
        likes=pick("likes", "likeCount", "diggCount", "likesCount", "score"),
        comments=pick("comments", "commentCount", "commentsCount", "num_comments"),
        shares=pick("shares", "shareCount", "sharesCount"),
        saves=pick("saves", "collectCount", "savesCount"),
        published_at=getattr(record, "publication_timestamp", None),
        is_pinned=bool(m.get("is_pinned") or m.get("stickied")),
        provider=str(getattr(record, "connector_name", "") or ""),
        provenance=str(getattr(record, "provenance_rights_evidence", "") or ""))
