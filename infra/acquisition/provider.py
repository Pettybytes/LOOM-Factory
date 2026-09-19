"""Platform-neutral paid-provider acquisition boundary (deterministic contracts).

Phase 2: contracts + deterministic logic ONLY. No network, no token use, no
Actor call, no media download. The Apify transport is injected and only
exercised through deterministic mocks.

The provider returns a SIGNED media URL as metadata; LOOM retrieves that URL
through the existing bounded local helper (download + ffprobe + SHA-256) and
finalizes into the Reaction Library. The signed URL is VOLATILE — it is never
logged, persisted, or stored in any ledger/provenance.
"""

from __future__ import annotations

from urllib.parse import urlsplit

import media_filter
import tiktok_actors as tiktok

PROVIDER_APIFY = "apify"

# Pinned actor allowlist. Only these identities may be dispatched. The fallback
# actor is configured but INACTIVE — it is never auto-called (a fallback call
# could create a second charge and requires an explicit classified decision).
ACTOR_REEL_SCRAPER = "apify/instagram-reel-scraper"
ACTOR_INSTAGRAM_SCRAPER = "apify/instagram-scraper"

#: TikTok's proven video-acquisition actor (runtime-proven 2026-09-18).
#: Clockworks is deliberately NOT pinned anywhere: its proven base response
#: supplied no downloadable media, so it must never be auto-selected as a
#: TikTok provider (see tiktok_actors.CLOCKWORKS_MEDIA_BLOCKER).
ACTOR_TIKTOK_VIDEO = tiktok.ACTOR_APIDOJO

ROLE_DEFAULT = "default"
ROLE_FALLBACK = "fallback"

# Pinned numeric Apify actor ids for the REST API. The human-readable name
# (e.g. 'apify/instagram-reel-scraper') is the WEBSITE slug and is NOT a valid
# API actor id — the API expects the numeric id or the 'username~actor-name'
# tilde slug. The numeric id is stable and unambiguous.
ACTOR_API_IDS = {
    ACTOR_REEL_SCRAPER: "xMc5Ga1oCONPmWJIa",
    ACTOR_INSTAGRAM_SCRAPER: "shu8hvrXbJbY3Eb9W",
}

# platform -> ordered pinned actors (role + active flag).
_ACTOR_ALLOWLIST: dict[str, tuple[dict, ...]] = {
    "instagram": (
        {"actor": ACTOR_REEL_SCRAPER, "role": ROLE_DEFAULT, "active": True},
        {"actor": ACTOR_INSTAGRAM_SCRAPER, "role": ROLE_FALLBACK, "active": False},
    ),
    # TikTok has exactly ONE pinned actor and NO fallback entry: an unpinned
    # actor cannot be selected, so Clockworks cannot be reached even by accident.
    "tiktok": (
        {"actor": ACTOR_TIKTOK_VIDEO, "role": ROLE_DEFAULT, "active": True},
    ),
}

MAX_RESULTS = 1
MAX_DURATION_SECONDS = 300          # 5 minutes
MAX_BYTES = 100 * 1024 * 1024       # 100 MiB

# Job statuses that authorize a provider acquisition.
_AUTHORIZED_JOB_STATUSES = frozenset({
    "queued", "running", "blocked_pending_platform_adapter",
})

# Candidate output field names for the video URL. Phase 3 verifies the exact
# name for apify/instagram-reel-scraper; the adapter reads the first present.
VIDEO_URL_FIELDS = ("videoUrl", "video_url", "videoDownloadUrl", "video_download_url")


class ProviderError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message


def allowed_actor(platform: str, actor: str) -> dict:
    """Return the pinned actor entry, or raise UNKNOWN_ACTOR."""
    for entry in _ACTOR_ALLOWLIST.get(platform, ()):
        if entry["actor"] == actor:
            return dict(entry)
    raise ProviderError("UNKNOWN_ACTOR", f"actor {actor!r} not allowed for platform {platform!r}")


def actor_active(platform: str, actor: str) -> bool:
    entry = allowed_actor(platform, actor)
    if not entry["active"]:
        raise ProviderError("INACTIVE_ACTOR", f"actor {actor!r} is configured but inactive")
    return True


def adapter_name(actor: str) -> str:
    return f"{PROVIDER_APIFY}:{actor.removeprefix('apify/')}"


def actor_api_id(actor: str) -> str:
    """Return the pinned numeric Apify actor id for the REST API.

    An actor identity that is already a valid REST id (a ``user~actor`` tilde
    slug) is returned unchanged — TikTok's pinned actor is sent as its readable
    identity, which is what its proven run used.
    """
    return ACTOR_API_IDS.get(actor, actor)


# ---------------------------------------------------------------------------
# Platform provider profiles
#
# Input shape, output validation and the media CDN allowlist are PLATFORM
# concerns. The shared lane asks a profile instead of branching on a platform
# name. Instagram's profile reproduces the original behaviour exactly; adding
# TikTok was an addition, not a rewrite.
# ---------------------------------------------------------------------------
class InstagramProviderProfile:
    platform = "instagram"

    def build_input(self, canonical_url: str) -> dict:
        return _instagram_input(canonical_url)

    def media_host_ok(self, hostname: str) -> bool:
        return media_filter.is_approved_media_host(hostname)

    def validate_output(self, items: list, *, content_id: str,
                        canonical_url: str = "") -> dict:
        return _validate_instagram_output(items, content_id=content_id)


class TikTokProviderProfile:
    """TikTok through the proven Apidojo actor, delegating to the adapter.

    Every guarantee the adapter owns is preserved: one exact public video URL,
    tracking-parameter removal (the caller passes the canonical URL), canonical
    numeric identity, photo/slideshow refusal, unresolved short-link refusal,
    ``maxItems: 1``, identity reconciliation and the TikTok CDN allowlist.
    """
    platform = "tiktok"
    actor = ACTOR_TIKTOK_VIDEO

    def build_input(self, canonical_url: str) -> dict:
        try:
            return tiktok.build_actor_input(self.actor, canonical_url)
        except tiktok.TikTokActorError as e:
            raise ProviderError(e.code, e.message) from e

    def media_host_ok(self, hostname: str) -> bool:
        return tiktok.is_approved_media_host(hostname)

    def validate_output(self, items: list, *, content_id: str,
                        canonical_url: str = "") -> dict:
        try:
            translated = tiktok.translate(self.actor, items,
                                          expected_content_id=content_id,
                                          canonical_url=canonical_url)
        except tiktok.TikTokActorError as e:
            # Same classified error surface the shared lane already handles.
            raise ProviderError(e.code, e.message) from e
        return {"video_url": translated["media_url"],
                "audio_url": translated["audio_url"],
                "result": translated}


_PROFILES: dict[str, object] = {
    "instagram": InstagramProviderProfile(),
    "tiktok": TikTokProviderProfile(),
}


def profile_for(platform: str):
    profile = _PROFILES.get((platform or "").lower())
    if profile is None:
        raise ProviderError("NO_PROVIDER_PROFILE",
                            f"no provider profile for platform {platform!r}")
    return profile


def media_host_check(platform: str):
    """A host predicate ONLY where the platform differs from the default.

    Instagram's allowlist is already the local helper's default, so it returns
    ``None`` there and the existing download path is untouched.
    """
    profile = _PROFILES.get((platform or "").lower())
    if profile is None or profile.platform == "instagram":
        return None
    return profile.media_host_ok


def _instagram_input(canonical_url: str) -> dict:
    return {
        "username": [canonical_url],
        "resultsLimit": MAX_RESULTS,
    }


def build_input(canonical_url: str, *, platform: str = "instagram") -> dict:
    """Deterministic single-item input, in the PLATFORM's own shape.

    Instagram: one direct reel URL with ``resultsLimit`` 1 (the input field is
    ``username``, which accepts a direct reel URL; no profile/hashtag/search
    expansion is possible). TikTok: the proven actor's own shape, via the adapter.
    """
    if platform not in _ACTOR_ALLOWLIST:
        raise ProviderError("UNSUPPORTED_PLATFORM", f"no actor allowlist for platform {platform!r}")
    return profile_for(platform).build_input(canonical_url)


def verify_authorization(job: dict | None, canonical_url: str) -> None:
    """An acquisition must have an existing authorization for the exact URL."""
    if not job:
        raise ProviderError("UNAUTHORIZED", "no acquisition authorization for this source")
    if job.get("canonical_url") != canonical_url:
        raise ProviderError("URL_MISMATCH", "authorization URL does not match the source")
    if job.get("status") not in _AUTHORIZED_JOB_STATUSES:
        raise ProviderError("NOT_AUTHORIZED", f"job status {job.get('status')!r} does not authorize acquisition")


def _extract_content_id(result: dict) -> str:
    for key in ("shortCode", "code", "id", "shortcode"):
        v = result.get(key)
        if v:
            return str(v)
    return ""


def _validate_video_url(url: str, *, platform: str = "instagram") -> None:
    if not isinstance(url, str) or not url.strip():
        raise ProviderError("MISSING_VIDEO_URL", "result missing video URL")
    try:
        p = urlsplit(url)
    except Exception:
        raise ProviderError("BAD_VIDEO_URL", "unparseable video URL")
    if p.scheme != "https":
        raise ProviderError("BAD_VIDEO_URL", "video URL must be https")
    if p.username or p.password:
        raise ProviderError("CREDENTIALS", "credential-bearing video URL rejected")
    host = (p.hostname or "").lower()
    if not _PROFILES.get(platform, _PROFILES["instagram"]).media_host_ok(host):
        raise ProviderError("HOST_NOT_ALLOWED",
                            f"video URL host not in the approved {platform} CDN allowlist")


def validate_output(items: list, *, content_id: str, platform: str = "instagram",
                    canonical_url: str = "") -> dict:
    """Validate provider dataset output for the PLATFORM. Returns {video_url, ...}."""
    return profile_for(platform).validate_output(items, content_id=content_id,
                                                 canonical_url=canonical_url)


def _validate_instagram_output(items: list, *, content_id: str) -> dict:
    """Validate Instagram provider dataset output. Returns {video_url, result}.

    Rejects: zero results, multiple results, content-id mismatch, missing/
    malformed/non-HTTPS/non-approved/credential-bearing video URL, and any
    duration over the 5-minute ceiling. Local ffprobe remains authoritative for
    actual media validity (never trust provider metadata for that).
    """
    if not items:
        raise ProviderError("EMPTY_RESULT", "provider returned zero results")
    if len(items) != 1:
        raise ProviderError("MULTIPLE_RESULTS", f"expected exactly 1 result, got {len(items)}")

    result = items[0]
    if not isinstance(result, dict):
        raise ProviderError("BAD_RESULT", "result is not an object")

    result_id = _extract_content_id(result)
    if content_id and result_id and result_id != content_id:
        raise ProviderError("CONTENT_MISMATCH",
                            f"result content id {result_id!r} != authorized {content_id!r}")

    video_url = ""
    for field in VIDEO_URL_FIELDS:
        if result.get(field):
            video_url = result[field]
            break
    _validate_video_url(video_url)

    duration = result.get("videoDuration", result.get("duration"))
    if duration is not None:
        try:
            d = float(duration)
        except (TypeError, ValueError):
            raise ProviderError("BAD_DURATION", f"invalid duration {duration!r}")
        if d > MAX_DURATION_SECONDS:
            raise ProviderError("DURATION_LIMIT", f"duration {d}s exceeds {MAX_DURATION_SECONDS}s")

    return {"video_url": video_url, "result": result}


def build_provenance(actor: str, video_url: str) -> dict:
    """Provenance for the acquisition job — NEVER includes the signed URL.

    Only the adapter name and a REDACTED source host (origin + path, no signed
    query string) are retained. The signed URL itself stays volatile.
    """
    return {
        "adapter": adapter_name(actor),
        "provider": PROVIDER_APIFY,
        "source_host": media_filter.redact_url(video_url) if video_url else "",
    }


def classify_failure(code: str) -> str:
    """Map a provider error code to a stable classified category."""
    return code
