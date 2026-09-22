"""Platform-aware signal normalization for Topic Intelligence.

Converts a platform-specific ``DiscoveryRecord.observed_metrics`` into a shared
internal contract (``NormalizedSignals``) that Topic Intelligence can score
uniformly — WITHOUT pretending YouTube and Reddit metrics are identical.

Normalization rules (honest, first-party RSS only):

* **YouTube** exposes ``views`` (from ``media:statistics``). It does NOT expose
  likes or comments over RSS, so those are recorded as *missing* — not zero,
  not estimated.
* **Reddit** exposes NO engagement numbers over RSS (score/ups/comments require
  the JSON or OAuth API, which LOOM does not use). Volume is therefore absent,
  and this is recorded as missing metrics, not fabricated engagement.

Each normalized signal carries a list of ``missing_metrics`` — the metrics a
platform *could* provide but did not — so confidence can be reduced honestly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .discovery_record import DiscoveryRecord

# Metric alias groups (first hit wins). Order matters for volume selection.
# Volume keys are platform-aware: TikTok/Instagram name views differently, and
# a platform-agnostic key list silently dropped playCount/videoPlayCount,
# recording "missing views" on posts with millions of them.
_VOLUME_KEYS_BY_PLATFORM: dict[str, tuple[str, ...]] = {
    "tiktok": ("playCount", "views", "viewCount"),
    "instagram": ("videoPlayCount", "playCount", "plays", "views", "viewCount"),
    "youtube": ("views", "viewCount", "view_count"),
    "twitch": ("view_count", "views", "viewCount"),
    "kick": ("view_count", "views", "viewCount", "like_count"),
    "reddit": ("score", "ups", "upvotes"),
}
_VOLUME_KEYS = ("views", "score", "ups", "upvotes",
                "playCount", "videoPlayCount", "plays", "viewCount", "view_count")
_LIKE_KEYS = ("likes", "like_count", "favorites", "diggCount", "likesCount")
_COMMENT_KEYS = ("comments", "num_comments", "comment_count", "commentCount", "commentsCount")


def _first_numeric(metrics: dict[str, Any], *keys: str) -> tuple[int | None, str]:
    """Return (value, metric_name) for the first numeric metric in ``keys``."""
    for k in keys:
        v = metrics.get(k)
        if isinstance(v, bool):
            continue
        if isinstance(v, (int, float)):
            return int(v), k
        if isinstance(v, str):
            try:
                return int(float(v)), k
            except ValueError:
                continue
    return None, ""


@dataclass(frozen=True)
class NormalizedSignals:
    """Platform-normalized, nullable signal bundle for one discovered source."""

    platform: str
    content_type: str
    # Volume = primary audience-reach metric (views / score / upvotes).
    volume: int | None
    volume_metric: str
    likes: int | None
    comments: int | None
    # engagement_rate = (likes + comments) / volume, computed ONLY when both
    # likes and comments are present and volume > 0. Otherwise None.
    engagement_rate: float | None
    source_url_references: tuple[str, ...]
    publication_timestamp: str
    metric_history: tuple[dict[str, Any], ...]
    title: str
    body_excerpt: str
    creator: str
    discovery_reason: str
    provenance: str
    evidence_confidence: float | None
    # Metrics this platform normally exposes but that were absent here.
    missing_metrics: tuple[str, ...]

    @property
    def has_volume(self) -> bool:
        return self.volume is not None

    @property
    def has_engagement_rate(self) -> bool:
        return self.engagement_rate is not None


def _missing_for_platform(platform: str) -> tuple[str, ...]:
    """Metrics a platform is expected to expose but which are absent over RSS.

    This drives honest "missing data" flags (never invented values).
    """
    if platform == "youtube":
        # RSS gives views only; likes/comments require the Data API (unused).
        return ("likes", "comments")
    if platform == "reddit":
        # RSS omits all engagement; score/ups/comments need JSON/OAuth (unused).
        return ("score", "ups", "num_comments")
    return ()


def normalize_signals(record: DiscoveryRecord) -> NormalizedSignals:
    """Normalize a discovery record into platform-aware signals."""
    metrics = dict(record.observed_metrics or {})
    platform = (record.platform or "").lower()

    volume_keys = _VOLUME_KEYS_BY_PLATFORM.get(platform, _VOLUME_KEYS)
    volume, volume_metric = _first_numeric(metrics, *volume_keys)
    likes, _ = _first_numeric(metrics, *_LIKE_KEYS)
    comments, _ = _first_numeric(metrics, *_COMMENT_KEYS)

    engagement_rate: float | None = None
    if (
        likes is not None
        and comments is not None
        and volume is not None
        and volume > 0
    ):
        engagement_rate = (likes + comments) / float(volume)

    source_urls: list[str] = []
    raw_refs = metrics.get("source_url_references")
    if isinstance(raw_refs, list):
        source_urls = [str(u) for u in raw_refs if isinstance(u, str) and u]

    missing = list(_missing_for_platform(platform))
    if volume is None:
        # Record specifically that the volume metric itself was absent.
        missing.append(volume_metric or "views")

    ec = record.evidence_confidence
    if isinstance(ec, str) and ec == "":
        ec = None

    return NormalizedSignals(
        platform=platform,
        content_type=str(metrics.get("content_type") or ""),
        volume=volume,
        volume_metric=volume_metric,
        likes=likes,
        comments=comments,
        engagement_rate=engagement_rate,
        source_url_references=tuple(source_urls),
        publication_timestamp=record.publication_timestamp or "",
        metric_history=tuple(dict(x) for x in (record.metric_history or [])),
        title=record.title or "",
        body_excerpt=record.body_excerpt or "",
        creator=record.creator_channel_identity or "",
        discovery_reason=record.discovery_reason or "",
        provenance=record.provenance_rights_evidence or "",
        evidence_confidence=float(ec) if ec is not None else None,
        missing_metrics=tuple(missing),
    )
