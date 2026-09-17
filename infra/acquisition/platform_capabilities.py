"""Platform Adapter Capability Contract — honest acquisition readiness.

Acquisition readiness is SEPARATE from URL intake: a platform whose URL intake
works is NOT automatically acquirable. This module is the single source of
truth for what the Acquisition Boundary may attempt.

Capability states:
  * runtime_proven          — a bounded acquisition proof has succeeded.
  * implemented_not_proven  — adapter code exists; no bounded proof run yet.
  * blocked_pending_adapter — no adapter implemented yet (planned).
  * unsupported_with_reason — no feasible first-party path (DRM/auth wall/ToS).

Only ``runtime_proven`` platforms may be downloaded. Everything else yields a
``blocked_pending_platform_adapter`` acquisition job (authorization recorded,
no download). Never claim an adapter works merely because URL intake works.
"""

from __future__ import annotations

CAP_RUNTIME_PROVEN = "runtime_proven"
CAP_IMPLEMENTED_NOT_PROVEN = "implemented_not_proven"
CAP_BLOCKED_PENDING_ADAPTER = "blocked_pending_adapter"
CAP_UNSUPPORTED_WITH_REASON = "unsupported_with_reason"
CAP_BLOCKED_LOCAL_FMP4_INCOMPLETE = "blocked_local_fmp4_incomplete"

CAPABILITY_STATES = frozenset({
    CAP_RUNTIME_PROVEN,
    CAP_IMPLEMENTED_NOT_PROVEN,
    CAP_BLOCKED_PENDING_ADAPTER,
    CAP_UNSUPPORTED_WITH_REASON,
    CAP_BLOCKED_LOCAL_FMP4_INCOMPLETE,
})

# Platform set (must match manual_intake PLATFORM_HOSTS keys).
PLATFORMS = frozenset({"youtube", "reddit", "tiktok", "instagram", "twitch", "kick"})

# Current acquisition-readiness matrix. Honest as of 2026-09-17: NO platform has
# a runtime-proven acquisition adapter yet (no bounded media download has been
# authorized or run). yt-dlp is integrated for transcript-only extraction
# (0 media bytes) but a bounded media acquisition proof has NOT run.
_CAPABILITY: dict[str, dict] = {
    "youtube": {
        "state": CAP_IMPLEMENTED_NOT_PROVEN,
        "reason": "yt-dlp 2026.03.13 installed; transcript-only extraction proven (0 media bytes); bounded media acquisition proof not yet run.",
        "candidates": ["yt-dlp", "cobalt"],
    },
    "reddit": {
        "state": CAP_IMPLEMENTED_NOT_PROVEN,
        "reason": "yt-dlp Reddit extractor installed; media is host-diverse (v.redd.it / external) so per-host behavior is unproven.",
        "candidates": ["yt-dlp", "cobalt", "gallery-dl"],
    },
    "twitch": {
        "state": CAP_IMPLEMENTED_NOT_PROVEN,
        "reason": "yt-dlp twitch:clips/vod installed; streamlink (live) not installed. No bounded acquisition proof yet.",
        "candidates": ["yt-dlp", "streamlink", "cobalt"],
    },
    "kick": {
        "state": CAP_IMPLEMENTED_NOT_PROVEN,
        "reason": "yt-dlp kick:clips/vod/live installed; no bounded acquisition proof yet.",
        "candidates": ["yt-dlp", "streamlink"],
    },
    "tiktok": {
        "state": CAP_IMPLEMENTED_NOT_PROVEN,
        "reason": "yt-dlp TikTok extractor installed but anonymous resolution is hit-or-miss (anti-bot); cobalt + browser-assisted candidates unproven. No bounded proof.",
        "candidates": ["yt-dlp", "cobalt", "streamlink"],
    },
    "instagram": {
        "state": CAP_BLOCKED_LOCAL_FMP4_INCOMPLETE,
        "reason": "Local browser-assisted fMP4 assembly failed the agreed proof threshold (INSUFFICIENT_FMP4_SET — no assemblable manifest/init+segments set). Local chrome-extension code retained; pivoting to Apify paid provider (evaluation in progress, $0 spent).",
        "candidates": ["chrome-extension", "apify"],
    },
}


def capability(platform: str) -> str:
    entry = _CAPABILITY.get(platform)
    if not entry:
        raise KeyError(f"unknown platform {platform!r}")
    return entry["state"]


def capability_reason(platform: str) -> str:
    entry = _CAPABILITY.get(platform)
    return entry["reason"] if entry else ""


def can_acquire(platform: str) -> bool:
    """True only when the platform adapter is runtime-proven (download allowed)."""
    return capability(platform) == CAP_RUNTIME_PROVEN


def matrix() -> dict[str, dict]:
    """Read-only copy of the full capability matrix ({platform: {state, reason, candidates}})."""
    return {p: dict(entry) for p, entry in sorted(_CAPABILITY.items())}


def candidates(platform: str) -> list[str]:
    """Ordered candidate adapter ladder for a platform."""
    entry = _CAPABILITY.get(platform)
    return list(entry.get("candidates", [])) if entry else []


def capability_map() -> dict[str, str]:
    """{platform: state} summary."""
    return {p: _CAPABILITY[p]["state"] for p in sorted(_CAPABILITY)}
