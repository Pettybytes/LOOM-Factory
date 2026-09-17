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

CAPABILITY_STATES = frozenset({
    CAP_RUNTIME_PROVEN,
    CAP_IMPLEMENTED_NOT_PROVEN,
    CAP_BLOCKED_PENDING_ADAPTER,
    CAP_UNSUPPORTED_WITH_REASON,
})

# Platform set (must match manual_intake PLATFORM_HOSTS keys).
PLATFORMS = frozenset({"youtube", "reddit", "tiktok", "instagram", "twitch", "kick"})

# Current acquisition-readiness matrix. Honest as of 2026-09-17: NO platform has
# a runtime-proven acquisition adapter yet (no bounded media download has been
# authorized or run). yt-dlp is integrated for transcript-only extraction
# (0 media bytes) but a bounded media acquisition proof has NOT run.
_CAPABILITY: dict[str, dict[str, str]] = {
    "youtube": {
        "state": CAP_IMPLEMENTED_NOT_PROVEN,
        "reason": "yt-dlp integrated (transcript-only proven, 0 media bytes); bounded media acquisition proof not yet run.",
    },
    "reddit": {
        "state": CAP_BLOCKED_PENDING_ADAPTER,
        "reason": "Reddit media is host-diverse (v.redd.it / external links); per-host resolver not yet implemented.",
    },
    "twitch": {
        "state": CAP_BLOCKED_PENDING_ADAPTER,
        "reason": "Clip + bounded VOD adapter not yet implemented.",
    },
    "kick": {
        "state": CAP_BLOCKED_PENDING_ADAPTER,
        "reason": "Clip + bounded VOD adapter not yet implemented.",
    },
    "instagram": {
        "state": CAP_BLOCKED_PENDING_ADAPTER,
        "reason": "Reels download requires undocumented/authenticated endpoints; no first-party adapter.",
    },
    "tiktok": {
        "state": CAP_BLOCKED_PENDING_ADAPTER,
        "reason": "No first-party adapter; TikTok remains held pending explicit authorization.",
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


def matrix() -> dict[str, dict[str, str]]:
    """Read-only copy of the full capability matrix ({platform: {state, reason}})."""
    return {p: dict(entry) for p, entry in sorted(_CAPABILITY.items())}


def capability_map() -> dict[str, str]:
    """{platform: state} summary."""
    return {p: _CAPABILITY[p]["state"] for p in sorted(_CAPABILITY)}
