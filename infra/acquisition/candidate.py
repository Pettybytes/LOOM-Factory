"""Automated Source Engine candidate contract (shared, honest by construction).

Carries an automatically discovered long-form candidate — with an OPTIONAL suggested
window — into the shared acquisition lane. Nothing here invents data: a title,
creator, reason or metric is only ever carried when the caller genuinely has it, and
a suggestion without a valid window is still reviewable but exposes **no**
acquisition action.

Durable identity is range-aware via `source_window`, so one long-form source can
legitimately yield several distinct library assets and the same source+range stays
idempotent.
"""

from __future__ import annotations

from typing import Any

import source_window as sw

ORIGIN_AUTOMATED = "automated_source_engine"

# Metrics we will carry only when the caller actually measured them.
KNOWN_METRIC_KEYS = frozenset({
    "views", "likes", "comments", "shares", "saves", "reposts", "score",
    "engagement_rate", "published_at", "window_hours", "rank",
})


def build_candidate(*, platform: str, canonical_url: str, content_id: str,
                    source_duration_s: float | None = None,
                    suggested_start_s: float | None = None,
                    suggested_end_s: float | None = None,
                    title: str = "", creator: str = "",
                    suggestion_reason: str = "",
                    scout_identity: str = "", tenant_id: str = "",
                    profile_id: str = "",
                    discovery: dict[str, Any] | None = None,
                    metrics: dict[str, Any] | None = None,
                    acceptance_proof: bool = False,
                    expected_content_id: str = "") -> dict[str, Any]:
    """Build a durable automated candidate. Raises sw.WindowError on a bad identity.

    A malformed/absent window does NOT raise: the candidate is still reviewable, with
    ``window_valid`` False so no acquisition action is ever offered.
    """
    identity = sw.validate_source_identity(
        platform=platform, canonical_url=canonical_url, content_id=content_id,
        expected_content_id=expected_content_id)

    window: dict[str, Any] = {}
    window_error = ""
    if suggested_start_s is not None and suggested_end_s is not None:
        try:
            window = sw.validate_window(
                platform=platform, canonical_url=identity["canonical_url"],
                content_id=content_id,
                start_ms=sw.to_ms(suggested_start_s, field="suggested_start"),
                end_ms=sw.to_ms(suggested_end_s, field="suggested_end"),
                origin=ORIGIN_AUTOMATED,
                source_duration_s=source_duration_s,
                authorized_by=scout_identity or "source_engine")
        except sw.WindowError as e:
            window_error = f"{e.code}: {e.message}"

    # Only genuinely measured metrics travel; unmeasured keys are dropped.
    carried_metrics = {}
    for key, value in (metrics or {}).items():
        if key in KNOWN_METRIC_KEYS and value is not None:
            carried_metrics[key] = value

    return {
        "platform": platform,
        "canonical_url": identity["canonical_url"],
        "content_id": content_id,
        "title": (title or "").strip(),
        "creator": (creator or "").strip(),
        "source_duration_ms": (sw.to_ms(source_duration_s, field="source_duration")
                               if source_duration_s is not None else None),
        "suggested_start_s": suggested_start_s,
        "suggested_end_s": suggested_end_s,
        "suggested_window": window,
        "window_valid": bool(window),
        "window_error": window_error,
        "clip_duration_ms": window.get("clip_duration_ms") if window else None,
        "suggestion_origin": (ORIGIN_AUTOMATED if window else ""),
        "suggestion_reason": (suggestion_reason or "").strip(),
        "scout_identity": (scout_identity or "").strip(),
        "tenant_id": tenant_id,
        "profile_id": profile_id,
        "discovery": dict(discovery or {}),
        "metrics": carried_metrics,
        "metrics_measured": bool(carried_metrics),
        "identity": (window.get("identity") if window
                     else sw.dedup_identity(platform, content_id, None)),
        "range_fingerprint": window.get("range_fingerprint", ""),
        "mode": (sw.MODE_RANGED if window else sw.MODE_FULL),
        "acceptance_proof": bool(acceptance_proof),
        "lane": "automated",
    }


def acquisition_allowed(candidate: dict[str, Any]) -> bool:
    """A candidate may only be acquired when it carries a VALID window."""
    return bool(candidate.get("window_valid")) and bool(candidate.get("range_fingerprint"))


def adjusted_window_from(candidate: dict[str, Any], *, start_ms: int, end_ms: int,
                         operator: str) -> dict[str, Any]:
    """Apply an operator adjustment, preserving the original suggestion."""
    return sw.validate_window(
        platform=candidate["platform"], canonical_url=candidate["canonical_url"],
        content_id=candidate["content_id"], start_ms=start_ms, end_ms=end_ms,
        origin="operator_adjusted",
        source_duration_s=((candidate.get("source_duration_ms") or 0) / 1000.0
                           or None),
        authorized_by=operator)


def provenance_block(candidate: dict[str, Any]) -> dict[str, Any]:
    """Provenance for the card/job: what was suggested, by whom, and from where."""
    original = candidate.get("suggested_window") or {}
    return {
        "lane": "automated",
        "suggestion_origin": candidate.get("suggestion_origin") or ORIGIN_AUTOMATED,
        "suggestion_reason": candidate.get("suggestion_reason") or "",
        "scout_identity": candidate.get("scout_identity") or "",
        "discovery": candidate.get("discovery") or {},
        "original_suggestion": ({"start_ms": original.get("start_ms"),
                                 "end_ms": original.get("end_ms"),
                                 "clip_duration_ms": original.get("clip_duration_ms"),
                                 "range_fingerprint": original.get("range_fingerprint", "")}
                                if original else None),
        "acceptance_proof": bool(candidate.get("acceptance_proof")),
    }
