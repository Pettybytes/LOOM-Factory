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
CAP_RUNTIME_PROVEN_PROVIDER = "runtime_proven_provider"
CAP_EVALUATED_NOT_PROVEN = "evaluated_not_proven"

CAPABILITY_STATES = frozenset({
    CAP_RUNTIME_PROVEN,
    CAP_IMPLEMENTED_NOT_PROVEN,
    CAP_BLOCKED_PENDING_ADAPTER,
    CAP_UNSUPPORTED_WITH_REASON,
    CAP_BLOCKED_LOCAL_FMP4_INCOMPLETE,
    CAP_RUNTIME_PROVEN_PROVIDER,
    CAP_EVALUATED_NOT_PROVEN,
})

# Platform set (must match manual_intake PLATFORM_HOSTS keys).
PLATFORMS = frozenset({"youtube", "reddit", "tiktok", "instagram", "twitch", "kick"})

# Current acquisition-readiness matrix. Honest as of 2026-09-17: NO platform has
# a runtime-proven acquisition adapter yet (no bounded media download has been
# authorized or run). yt-dlp is integrated for transcript-only extraction
# (0 media bytes) but a bounded media acquisition proof has NOT run.
_CAPABILITY: dict[str, dict] = {
    "youtube": {
        "state": CAP_RUNTIME_PROVEN,
        "reason": ("Bounded local yt-dlp media acquisition PROVEN 2026-09-18: exact URL "
                   "youtube.com/watch?v=h3Q_tAFWY5g acquired in one attempt "
                   "(40.64 s, 16,407,149 B, sha256 d8990649b8e3…), no cookies/auth, "
                   "$0 provider cost, Apify ceiling untouched. A >300 s source "
                   "(ClhBXVygWbc, 302 s) was refused at metadata with no download."),
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
        "state": CAP_EVALUATED_NOT_PROVEN,
        "reason": "Provider evaluated (read-only, 2026-09-17): memo23/tiktok-video-downloader (id pQGdDfhFC35QToTxZ, single-URL, $2/1k, no cookies) recommended; clockworks/tiktok-scraper (id GdWCkxBtKWOsKjdch, 286K users) established fallback. NOT runtime-proven — no bounded live media proof yet.",
        "candidates": ["apify", "yt-dlp", "cobalt"],
    },
    "instagram": {
        "state": CAP_RUNTIME_PROVEN_PROVIDER,
        "reason": "Provider acquisition runtime-proven (apify/instagram-reel-scraper, API id xMc5Ga1oCONPmWJIa, $0.0026/Reel, muxed H.264/AAC MP4). Local chrome-extension fMP4 assembly blocked (INSUFFICIENT_FMP4_SET). Discord/extension UI NOT yet wired to the provider.",
        "candidates": ["apify", "chrome-extension"],
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
    """True when acquisition is runtime-proven (free/local OR paid provider)."""
    return capability(platform) in (CAP_RUNTIME_PROVEN, CAP_RUNTIME_PROVEN_PROVIDER)


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


# ---------------------------------------------------------------------------
# Adapter proof mode (bounded, non-claiming)
#
# A platform whose adapter is IMPLEMENTED but not yet runtime-proven may run its
# bounded proof while the state still says implemented_not_proven. This only
# permits the attempt; it never claims the platform is proven. The capability
# state is flipped to runtime_proven separately, and only after a proof succeeds.
# ---------------------------------------------------------------------------
PROOF_ENV = "LOOM_ADAPTER_PROOF_PLATFORMS"


def proof_platforms() -> frozenset:
    import os
    raw = os.environ.get(PROOF_ENV) or ""
    return frozenset(p.strip().lower() for p in raw.split(",") if p.strip())


def proof_mode_allows(platform: str) -> bool:
    """True while a bounded adapter proof is explicitly in progress."""
    name = (platform or "").lower()
    if name not in proof_platforms():
        return False
    entry = _CAPABILITY.get(name) or {}
    return entry.get("state") in (CAP_IMPLEMENTED_NOT_PROVEN, CAP_RUNTIME_PROVEN,
                                  CAP_RUNTIME_PROVEN_PROVIDER)
