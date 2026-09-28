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
                   "(ClhBXVygWbc, 302 s) was refused at metadata with no download. "
                   "BOUNDED WINDOW re-proven 2026-09-28: watch?v=59xxeJCbzaM (467 s) "
                   "yielded ONLY the requested 02:00-03:00 working source (60.0 s, "
                   "1920x1080 H.264/AAC, integrity OK, sha256 09f403a5b172…, "
                   "29,279,708 B) — the full source was never retained."),
        "proven_modes": ("full_item", "bounded_segment"),
        "candidates": ["yt-dlp", "cobalt"],
    },
    "reddit": {
        "state": CAP_IMPLEMENTED_NOT_PROVEN,
        "reason": "yt-dlp Reddit extractor installed; media is host-diverse (v.redd.it / external) so per-host behavior is unproven.",
        "candidates": ["yt-dlp", "cobalt", "gallery-dl"],
    },
    "twitch": {
        # The adapter lane IS genuinely proven (bounded VOD window + whole clip,
        # 2026-09-18, $0 local). The stale matrix previously reported
        # implemented_not_proven and, because `can_acquire()` gates the approved
        # lane, that DOWNGRADED a proven lane. Corrected to match the adapter.
        "state": CAP_RUNTIME_PROVEN,
        "reason": ("Bounded Twitch acquisition PROVEN 2026-09-18 through the shared "
                   "local lane ($0, local:yt-dlp): a whole 34.333 s clip "
                   "(25,546,606 B) and a bounded VOD window 00:30-01:00 "
                   "(12,230,775 B, 30.000 s) were both acquired, hashes verified "
                   "against their library rows, H.264/AAC/yuv420p/fast-start. "
                   "streamlink (live) is not installed and is not needed for VOD/clip. "
                   "MANUAL INTAKE live acceptance for Twitch remains OUTSTANDING — the "
                   "adapter lane is proven, the Manual Intake path through it is not."),
        "proven_modes": ("full_item", "bounded_segment"),
        "candidates": ["yt-dlp", "streamlink", "cobalt"],
    },
    "kick": {
        # MODE-SCOPED ON PURPOSE. A Kick CLIP is proven; a Kick VOD / ranged VOD is
        # NOT live proven (the previous proof source became externally unavailable,
        # HTTP 404 in every canonical form). The state therefore stays short of full
        # readiness so that clip support can never be read as VOD support.
        "state": CAP_IMPLEMENTED_NOT_PROVEN,
        "reason": ("CLIP PROVEN 2026-09-18 through the shared local lane ($0, "
                   "local:yt-dlp): one Kick clip acquired, H.264/AAC/yuv420p/"
                   "fast-start, hash verified against its library row. VOD / RANGED "
                   "VOD NOT LIVE PROVEN — the supplied proof VOD "
                   "01a02be6-62d0-76a1-b338-0a9c3f3b8175 is externally unavailable "
                   "(yt-dlp kick:vod and Kick public metadata both return HTTP 404). "
                   "Clip capability does NOT imply VOD capability. Manual Intake live "
                   "acceptance for Kick VOD remains OUTSTANDING."),
        "proven_modes": ("clip",),
        "candidates": ["yt-dlp", "streamlink"],
    },
    "tiktok": {
        "state": CAP_RUNTIME_PROVEN_PROVIDER,
        "reason": ("Provider actor acquisition RUNTIME-PROVEN 2026-09-18 via "
                   "apidojo~tiktok-scraper (build 0.0.1098, run wqL0eJXuT1HLhlEZj): one "
                   "exact video translated from `video.url`, downloaded (HTTP 200, "
                   "video/mp4, 1,405,272 B), normalized to MP4/H.264/AAC/yuv420p/"
                   "fast-start (sha256 0e9af235dacf…), finalized into the Reaction "
                   "Library with a durable row; $0 charged (reservation released). "
                   "NOT YET WIRED into the shared lane: no pinned provider actor in "
                   "DEFAULT_PROVIDER_ACTORS (fail-closed NO_PROVIDER_ACTOR). The "
                   "other evaluated actor, clockworks~tiktok-scraper, is NOT proven: "
                   "its base result carries no downloadable video (mediaUrls: []) — "
                   "the paid shouldDownloadVideos add-on would be required."),
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


def proven_modes(platform: str) -> tuple[str, ...]:
    """Which acquisition MODES are actually proven for this platform.

    Mode-scoped truth, so a partially proven platform (Kick: clip only) can never be
    read as fully proven. Read-only view of the same matrix — not another registry.
    """
    entry = _CAPABILITY.get(platform)
    return tuple(entry.get("proven_modes", ())) if entry else ()


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
