"""Platform normalization: raw provider fields -> ONE market-evidence shape.

Platforms name the same idea differently (`playCount` vs `views`, `diggCount` vs
`likes`, `score` vs reach) and some carry evidence view floors cannot express
(Reddit score/hour, Twitch category). This module maps raw per-platform metrics
into one structure for the deterministic market gate, while keeping
platform-native evidence intact.

Missing data STAYS missing — never fabricated, never defaulted to zero.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from typing import Any

from .market_math import (MIN_ENGAGEMENT_RATE, age_hours, engagement_rate,
                          market_gate, outlier_multiple, views_per_hour)

# Canonical reason vocabulary required by the acceptance contract.
LOW_VIEWS = "LOW_VIEWS"
LOW_LIKES = "LOW_LIKES"
LOW_VELOCITY = "LOW_VELOCITY"
LOW_ENGAGEMENT = "LOW_ENGAGEMENT"
ORDINARY_PERFORMANCE = "ORDINARY_PERFORMANCE"
STALE = "STALE"
INSUFFICIENT_MARKET_EVIDENCE = "INSUFFICIENT_MARKET_EVIDENCE"
LOW_SCORE = "LOW_SCORE"
LOW_SCORE_VELOCITY = "LOW_SCORE_VELOCITY"

# View-floor platforms (Reddit is deliberately NOT here).
VIEW_FLOOR_PLATFORMS = ("youtube", "tiktok", "instagram", "twitch", "kick")

# Raw field aliases per platform. First present key wins.
_PLATFORM_ALIASES: dict[str, dict[str, tuple[str, ...]]] = {
    "youtube": {"views": ("viewCount", "views", "view_count"), "likes": ("likeCount", "likes"),
                "comments": ("commentCount", "comments"), "shares": ("shareCount",),
                "subscribers": ("subscriberCount", "subscribers")},
    "tiktok": {"views": ("playCount", "views", "viewCount"), "likes": ("diggCount", "likes"),
               "comments": ("commentCount", "comments"), "shares": ("shareCount", "shares"),
               "saves": ("collectCount", "saves", "favorites")},
    "instagram": {"views": ("videoPlayCount", "playCount", "plays", "views", "viewCount"),
                  "likes": ("likesCount", "likes"), "comments": ("commentsCount", "comments"),
                  "shares": ("sharesCount", "shares"), "saves": ("savesCount", "saves")},
    "twitch": {"views": ("view_count", "views", "viewCount"), "likes": ("likes",),
               "comments": ("comments",)},
    "kick": {"views": ("view_count", "views", "viewCount", "like_count"), "likes": ("likes",),
             "comments": ("comments",)},
    "reddit": {"likes": ("score", "ups"), "comments": ("num_comments", "comments")},
}

STALE_AGE_HOURS = 24 * 30.0        # a month old is stale for moment sourcing
REDDIT_MIN_SCORE = 250
REDDIT_MIN_SCORE_PER_HOUR = 25.0


@dataclass(frozen=True)
class MarketEvidence:
    """One normalized market-evidence record. Unknown fields stay None."""

    platform: str
    views: int | None = None
    likes: int | None = None
    comments: int | None = None
    shares: int | None = None
    saves: int | None = None
    subscribers: int | None = None
    published_at: Any = None
    age_hours: float | None = None
    vph: float | None = None
    engagement: float | None = None
    creator_id: str = ""
    creator_baseline: float | None = None
    outlier_multiple: float | None = None
    provenance: str = ""
    native: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"platform": self.platform, "views": self.views, "likes": self.likes,
                "comments": self.comments, "shares": self.shares, "saves": self.saves,
                "subscribers": self.subscribers, "age_hours": self.age_hours,
                "vph": self.vph, "engagement": self.engagement,
                "creator_baseline": self.creator_baseline,
                "outlier_multiple": self.outlier_multiple, "provenance": self.provenance,
                **self.native}


def _pick(metrics: dict[str, Any], keys: tuple[str, ...]) -> int | None:
    for k in keys:
        v = metrics.get(k)
        if isinstance(v, bool):
            continue
        if isinstance(v, (int, float)) and v >= 0:
            return int(v)
    return None


def normalize_market_evidence(record: Any, *, creator_baseline: float | None = None,
                              now: _dt.datetime | None = None) -> MarketEvidence:
    """Map a DiscoveryRecord (or raw dict) into normalized market evidence."""
    if isinstance(record, dict):
        platform = str(record.get("platform") or "").lower()
        metrics = dict(record.get("observed_metrics") or {})
        published = record.get("publication_timestamp")
        creator = str(record.get("creator_channel_identity") or "")
        provenance = str(record.get("provenance_rights_evidence") or "")
    else:
        platform = str(getattr(record, "platform", "") or "").lower()
        metrics = dict(getattr(record, "observed_metrics", {}) or {})
        published = getattr(record, "publication_timestamp", None)
        creator = str(getattr(record, "creator_channel_identity", "") or "")
        provenance = str(getattr(record, "provenance_rights_evidence", "") or "")

    aliases = _PLATFORM_ALIASES.get(platform, {})
    views = _pick(metrics, aliases.get("views", ("views", "viewCount", "playCount")))
    likes = _pick(metrics, aliases.get("likes", ("likes", "likeCount")))
    comments = _pick(metrics, aliases.get("comments", ("comments", "commentCount")))
    shares = _pick(metrics, aliases.get("shares", ("shares",)))
    saves = _pick(metrics, aliases.get("saves", ("saves",)))

    age = age_hours(published, now)
    native: dict[str, Any] = {}
    if platform == "reddit":
        # Reddit-native evidence: reach is expressed as score + velocity, NOT views.
        score = likes
        native["reddit_score"] = score
        native["reddit_score_per_hour"] = (round(score / age, 4) if score is not None and age and age >= 1 else None)
        native["reddit_comments_per_hour"] = (round(comments / age, 4) if comments is not None and age and age >= 1 else None)
        native["reddit_subreddit"] = metrics.get("subreddit") or ""
        native["reddit_rank"] = metrics.get("rank") or metrics.get("hot_rank") or ""
        native["reddit_is_meta"] = bool(metrics.get("is_meta") or metrics.get("stickied"))
    if platform == "twitch":
        native["twitch_category"] = metrics.get("game_name") or metrics.get("game") or ""
        native["twitch_duration"] = metrics.get("duration") or metrics.get("duration_seconds")
        native["twitch_thumbnail"] = metrics.get("thumbnail_url") or metrics.get("thumbnail") or ""
    if platform == "kick":
        for k in ("thumbnail", "thumbnail_url", "duration", "category", "clip_id"):
            if metrics.get(k) is not None:
                native[f"kick_{k}"] = metrics.get(k)

    return MarketEvidence(
        platform=platform, views=views, likes=likes, comments=comments, shares=shares,
        saves=saves, subscribers=_pick(metrics, aliases.get("subscribers", ("subscribers",))),
        published_at=published, age_hours=age, vph=views_per_hour(views, published, now),
        engagement=engagement_rate(likes, comments, shares, views), creator_id=creator,
        creator_baseline=creator_baseline,
        outlier_multiple=outlier_multiple(views, creator_baseline),
        provenance=provenance, native=native)


def _map_reason(reason: str) -> str:
    if "views_floor" in reason:
        return LOW_VIEWS
    if "likes_floor" in reason:
        return LOW_LIKES
    if "velocity" in reason:
        return LOW_VELOCITY
    if "engagement" in reason:
        return LOW_ENGAGEMENT
    if "outlier_multiple" in reason:
        return ORDINARY_PERFORMANCE
    if "no_view_count" in reason:
        return INSUFFICIENT_MARKET_EVIDENCE
    return INSUFFICIENT_MARKET_EVIDENCE


@dataclass(frozen=True)
class PlatformMarketDecision:
    passed: bool
    reasons: tuple[str, ...] = ()
    evidence: dict[str, Any] = field(default_factory=dict)


def _reddit_gate(ev: MarketEvidence) -> PlatformMarketDecision:
    """Reddit-native gate: score, score/hour and freshness. NEVER view floors."""
    reasons: list[str] = []
    score = ev.native.get("reddit_score")
    sph = ev.native.get("reddit_score_per_hour")
    age = ev.age_hours
    if score is None:
        reasons.append(INSUFFICIENT_MARKET_EVIDENCE)
    else:
        if score < REDDIT_MIN_SCORE:
            reasons.append(LOW_SCORE)
        if sph is None:
            reasons.append(INSUFFICIENT_MARKET_EVIDENCE)
        elif sph < REDDIT_MIN_SCORE_PER_HOUR:
            reasons.append(LOW_SCORE_VELOCITY)
    if age is None:
        reasons.append(INSUFFICIENT_MARKET_EVIDENCE)
    elif age > STALE_AGE_HOURS:
        reasons.append(STALE)
    if ev.native.get("reddit_is_meta"):
        # sticky / pinned / admin meta posts are never the moment
        reasons.append("SUBREDDIT_META")
    return PlatformMarketDecision(not reasons, tuple(dict.fromkeys(reasons)), ev.as_dict())


def platform_market_gate(ev: MarketEvidence, *, now: _dt.datetime | None = None) -> PlatformMarketDecision:
    """Dispatch to the right market gate for the platform.

    View-floor platforms use the age-adjusted deterministic gate; Reddit uses its
    own score/velocity gate. Missing evidence fails closed with an explicit code.
    """
    if ev.platform == "reddit":
        return _reddit_gate(ev)

    if ev.views is None:
        return PlatformMarketDecision(False, (INSUFFICIENT_MARKET_EVIDENCE,), ev.as_dict())

    if ev.age_hours is not None and ev.age_hours > STALE_AGE_HOURS:
        return PlatformMarketDecision(False, (STALE,), ev.as_dict())

    d = market_gate(views=ev.views, likes=ev.likes, comments=ev.comments, shares=ev.shares,
                    published_at=ev.published_at, baseline_median=ev.creator_baseline, now=now)
    codes = tuple(dict.fromkeys(_map_reason(r) for r in d.reasons))
    return PlatformMarketDecision(d.passed, codes, ev.as_dict())
