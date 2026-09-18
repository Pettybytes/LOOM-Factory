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

PROVIDER_APIFY = "apify"

# Pinned actor allowlist. Only these identities may be dispatched. The fallback
# actor is configured but INACTIVE — it is never auto-called (a fallback call
# could create a second charge and requires an explicit classified decision).
ACTOR_REEL_SCRAPER = "apify/instagram-reel-scraper"
ACTOR_INSTAGRAM_SCRAPER = "apify/instagram-scraper"

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
    """Return the pinned numeric Apify actor id for the REST API."""
    return ACTOR_API_IDS.get(actor, actor)


def build_input(canonical_url: str, *, platform: str = "instagram") -> dict:
    """Deterministic single-item input: exactly one direct reel URL, maxResults=1.

    The reel-scraper's input field is ``username`` (array), which accepts
    usernames, profile URLs, IDs, OR direct reel URLs. LOOM passes exactly ONE
    direct reel URL (never a username/profile/hashtag/search), so no profile,
    related-content, pagination, or carousel expansion can occur. ``resultsLimit``
    is ignored for direct URLs but kept at 1 for safety. ``includeDownloadedVideo``
    is deliberately omitted (defaults false) so no premium stored-file event runs.
    """
    if platform not in _ACTOR_ALLOWLIST:
        raise ProviderError("UNSUPPORTED_PLATFORM", f"no actor allowlist for platform {platform!r}")
    return {
        "username": [canonical_url],
        "resultsLimit": MAX_RESULTS,
    }


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


def _validate_video_url(url: str) -> None:
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
    if not media_filter.is_approved_media_host(host):
        raise ProviderError("HOST_NOT_ALLOWED", "video URL host not in approved Meta/IG CDN allowlist")


def validate_output(items: list, *, content_id: str) -> dict:
    """Validate provider dataset output. Returns {video_url, result} or raises.

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
