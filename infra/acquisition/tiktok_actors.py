"""TikTok paid-provider actor adapters — Clockworks and Apidojo.

Two INDEPENDENT actor identities. Each carries its own cost, its own request
translation, its own response translation, and its own readiness flag. Neither
inherits the other's proof, allowance, or field names.

Wire identity
-------------
Apify's ``username~actor-name`` tilde slug is itself a valid REST actor id, so the
readable identity is used verbatim on the wire. There is no numeric translation
step and therefore no ``/`` in the URL path — the slug form that produced a live
404 at the Actor *start* endpoint for Instagram.

Request translation (actor-scoped)
----------------------------------
``startUrls`` + actor-input ``maxItems: 1``. The charge cap is deliberately NOT
actor input: it travels as a RUN PARAMETER (``maxTotalChargeUsd``) on the
start-run call, where Apify's billing layer enforces it. Putting it in the input
payload would make it an input field that caps nothing.

Response translation (actor-scoped)
-----------------------------------
The two actors do NOT return the same field names, so every read goes through
THIS actor's ordered path list. The first present, non-empty path wins. A schema
that matches nothing is refused (``SCHEMA_ERROR`` / ``MISSING_MEDIA``) rather than
guessed at.

Readiness
---------
``runtime_proven`` is ``False`` for both actors and may only be flipped by a real
completed live receipt. ``proven_modes`` records exactly which modes are proven.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

PLATFORM = "tiktok"

ACTOR_CLOCKWORKS = "clockworks~tiktok-scraper"
ACTOR_APIDOJO = "apidojo~tiktok-scraper"

# Pinned per-result costs (micro-dollars). Independent: one actor's price never
# implies the other's.
CLOCKWORKS_RESULT_MICRO = 1_700
APIDOJO_RESULT_MICRO = 300

#: The AUTHORIZED ceiling for one TikTok actor run ($0.01). This is the bound the
#: project is accountable to; it is enforced by the ledger's full-exposure
#: reservation and by acquiring exactly one result.
AUTHORIZED_CHARGE_CEILING_USD = 0.01

#: LIVE FINDING (2026-09-18): Apify REJECTS ``maxTotalChargeUsd`` below **$0.50**
#: with ``max-total-charge-usd-below-minimum`` ("Maximum cost per run is less than
#: the allowed minimum of $0.50"). The literal $0.01 cap is therefore NOT
#: expressible as a run parameter on this platform. The run parameter carries the
#: platform floor instead, and the $0.01/$0.02 authorization is enforced by the
#: ledger plus ``maxItems: 1`` over one direct video URL. Both numbers are always
#: reported, so the deviation is documented rather than hidden.
APIFY_MIN_MAX_TOTAL_CHARGE_USD = 0.50

MAX_TOTAL_CHARGE_USD = APIFY_MIN_MAX_TOTAL_CHARGE_USD

MAX_ITEMS = 1
MAX_DURATION_SECONDS = 300

#: Apify run terminal states.
TERMINAL_STATES = frozenset({"SUCCEEDED", "FAILED", "TIMED-OUT", "ABORTED", "ABORTING"})
FAILED_STATES = frozenset({"FAILED", "TIMED-OUT", "ABORTED", "ABORTING"})

#: TikTok / ByteDance media CDN suffixes. A suffix match means a subdomain of a
#: host we trust; a lookalike such as ``tiktokcdn.com.evil.com`` does NOT match.
APPROVED_MEDIA_SUFFIXES = (
    ".tiktokcdn.com", ".tiktokcdn-us.com", ".tiktokcdn-eu.com", ".tikcdn.com",
    ".tiktokv.com", ".tiktokv.us", ".tiktok.com", ".ttwstatic.com",
    ".muscdn.com", ".musical.ly", ".byteoversea.com", ".ibytedtos.com",
    ".ibyteimg.com", ".bytecdntp.com", ".bytedance.com",
)
APPROVED_MEDIA_EXACT = frozenset({s.lstrip(".") for s in APPROVED_MEDIA_SUFFIXES})


#: The live receipt that flips Apidojo's readiness. Recorded in code so the
#: claim is auditable and cannot drift from the evidence.
APIDOJO_PROOF_RECEIPT = {
    "proven_at": "2026-09-18",
    "mode": "video",
    "run_id": "wqL0eJXuT1HLhlEZj",
    "build": "0.0.1098",
    "actor_api_id": "5K30i8aFccKNF5ICs",
    "dataset_id": "RqGVB1EddoJJ20Byr",
    "content_id": "7598767615980408095",
    "canonical_url": "https://www.tiktok.com/@thoughtswthot/video/7598767615980408095",
    "media_field": "video.url",
    "http_status": 200,
    "downloaded_content_type": "video/mp4",
    "size_bytes": 1405272,
    "duration_seconds": 8.868005,
    "width": 720, "height": 1280,
    "video_codec": "h264", "audio_codec": "aac", "pix_fmt": "yuv420p",
    "container": "mov,mp4,m4a,3gp,3g2,mj2",
    "faststart": True,
    "has_audio": True,
    "sha256": "0e9af235dacf129a93f64ef40b89eab3512c65d63f1f870f1c7cbc58cddf1d6c",
    "library_row": "542ef5cf-2681-4ef7-96b1-4ae3f925e55c",
    "charge_micro": 0,
}


class TikTokActorError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message


# ---------------------------------------------------------------------------
# Actor specs
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class TikTokActorSpec:
    """One actor's identity, cost, and its OWN field translation table."""

    actor: str
    label: str
    cost_micro_per_result: int
    #: Actor-specific INPUT shape, read from the live input schema:
    #:   "postURLs"  — Clockworks takes direct video URLs as ``postURLs``
    #:                 (build 0.0.608). It has NO ``startUrls``/``maxItems``; the
    #:                 one-item bound is the single direct URL itself.
    #:   "startUrls" — Apidojo takes ``startUrls`` + ``maxItems`` (build 0.0.1097).
    input_style: str
    video_id_paths: tuple[str, ...]
    webpage_paths: tuple[str, ...]
    video_paths: tuple[str, ...]
    audio_paths: tuple[str, ...]
    slideshow_flag_paths: tuple[str, ...]
    slideshow_payload_paths: tuple[str, ...]
    duration_paths: tuple[str, ...]
    width_paths: tuple[str, ...]
    height_paths: tuple[str, ...]
    expiry_paths: tuple[str, ...]
    handle_paths: tuple[str, ...]
    runtime_proven: bool = False
    proven_modes: frozenset = field(default_factory=frozenset)


CLOCKWORKS = TikTokActorSpec(
    actor=ACTOR_CLOCKWORKS,
    label="Clockworks TikTok Scraper",
    cost_micro_per_result=CLOCKWORKS_RESULT_MICRO,
    input_style="postURLs",
    video_id_paths=("id", "video.id", "itemId"),
    webpage_paths=("webVideoUrl", "url", "shareUrl"),
    video_paths=("videoUrlNoWaterMark", "videoUrl", "mediaUrls", "downloadAddr"),
    audio_paths=("musicMeta.musicUrl", "musicMeta.playUrl", "musicUrl"),
    slideshow_flag_paths=("isSlideshow", "isSlideshowItem", "imagePost"),
    slideshow_payload_paths=("slideshowImageLinks", "images", "imagePost.images"),
    duration_paths=("videoMeta.duration", "duration"),
    width_paths=("videoMeta.width", "width"),
    height_paths=("videoMeta.height", "height"),
    expiry_paths=("videoUrlExpiresAt", "expiresAt", "videoMeta.expiresAt"),
    handle_paths=("authorMeta.name", "authorMeta.uniqueId", "author.uniqueId", "author"),
)

APIDOJO = TikTokActorSpec(
    actor=ACTOR_APIDOJO,
    label="Apidojo TikTok Scraper",
    cost_micro_per_result=APIDOJO_RESULT_MICRO,
    input_style="startUrls",
    video_id_paths=("id", "video.id", "itemId", "aweme_id"),
    webpage_paths=("webVideoUrl", "url", "shareUrl", "webVideoUrlNoWaterMark"),
    # Media paths are LIVE-VERIFIED against the real dataset item (2026-09-18):
    # `video.url` is where the downloadable MP4 actually lives. `song` carries no
    # audio URL, so a separately-hosted audio stream is not expected from this
    # actor — the video's own audio stream is authoritative.
    video_paths=("video.url", "video.playAddr", "video.downloadAddr",
                 "downloadAddr", "videoUrl", "video.playAddrH264"),
    audio_paths=("song.playUrl", "song.url", "music.playUrl", "musicMeta.musicUrl"),
    slideshow_flag_paths=("isSlideshow", "imagePost", "isImagePost"),
    slideshow_payload_paths=("images", "imagePost.images", "slideshowImageLinks"),
    duration_paths=("video.duration", "duration", "videoMeta.duration"),
    width_paths=("video.width", "width", "videoMeta.width"),
    height_paths=("video.height", "height", "videoMeta.height"),
    expiry_paths=("video.expiresAt", "expiresAt", "urlExpiry"),
    handle_paths=("channel.username", "channel.name", "author.uniqueId",
                  "author.nickname", "authorMeta.name"),
    # PROVEN 2026-09-18 for VIDEO mode, from a real receipt (APIDOJO_PROOF_RECEIPT):
    # one exact video translated via `video.url`, downloaded (HTTP 200, video/mp4),
    # normalized to MP4/H.264/AAC/yuv420p/fast-start and finalized into the Reaction
    # Library with a durable row. Only this actor's readiness moved; Clockworks'
    # did not, because its base result carries no downloadable media.
    runtime_proven=True,
    proven_modes=frozenset({"video"}),
)

_REGISTRY: dict[str, TikTokActorSpec] = {}


def register_actor(spec: TikTokActorSpec, *, replace: bool = False) -> TikTokActorSpec:
    if spec.actor in _REGISTRY and not replace:
        raise ValueError(f"TikTok actor {spec.actor!r} is already registered")
    _REGISTRY[spec.actor] = spec
    return spec


def unregister_actor(actor: str) -> None:
    _REGISTRY.pop(actor, None)


def get_actor(actor: str) -> TikTokActorSpec:
    spec = _REGISTRY.get(actor or "")
    if spec is None:
        raise TikTokActorError("UNKNOWN_TIKTOK_ACTOR",
                               f"{actor!r} is not a registered TikTok actor")
    return spec


def actor_for(actor: str) -> TikTokActorSpec | None:
    return _REGISTRY.get(actor or "")


def actors() -> tuple[str, ...]:
    return tuple(sorted(_REGISTRY))


def register_all() -> None:
    """Idempotent module registration (import-time, and safe for tests)."""
    for spec in (CLOCKWORKS, APIDOJO):
        register_actor(spec, replace=True)


register_all()


# ---------------------------------------------------------------------------
# Request translation
# ---------------------------------------------------------------------------
#: Clockworks' base result is LIVE-VERIFIED to carry NO downloadable video: the
#: dataset item has ``mediaUrls: []`` and ``videoMeta`` exposes only stills and
#: metadata (coverUrl/definition/duration/width/height). A video only appears with
#: the paid ``shouldDownloadVideos ($)`` add-on, which is a separate chargeable
#: event outside the single per-result authorization — so it is never sent.
CLOCKWORKS_MEDIA_BLOCKER = "paid_addon_required:shouldDownloadVideos"

#: Fields the live schemas mark as PAID ADD-ONS ("($)"). None may ever be sent:
#: each one is a separate chargeable event and would spend outside the single
#: per-result authorization.
PAID_ADDON_FIELDS = frozenset({
    "shouldDownloadVideos", "shouldDownloadCovers", "shouldDownloadSlideshowImages",
    "shouldDownloadAvatars", "shouldDownloadMusicCovers", "aiVideoDescription",
    "aiVideoSummary", "oldestPostDateUnified", "newestPostDateUnified",
    "mostDiggs", "leastDiggs", "maxFollowersPerProfile", "maxFollowingPerProfile",
    "videoSearchSorting", "videoSearchDateFilter",
})


def build_actor_input(actor: str, canonical_url: str, *,
                      max_items: int = MAX_ITEMS) -> dict[str, Any]:
    """Actor INPUT, in THIS actor's own shape, for exactly one item.

    The charge cap is NOT here on purpose — it is a run parameter. No paid add-on
    field is ever sent: one direct URL plus one per-result charge is the whole
    authorization.
    """
    spec = get_actor(actor)
    url = (canonical_url or "").strip()
    if not url:
        raise TikTokActorError("NO_URL", "no canonical URL to acquire")

    if spec.input_style == "postURLs":
        # Clockworks has no maxItems field; one direct URL IS the one-item bound.
        inp: dict[str, Any] = {"postURLs": [url]}
    elif spec.input_style == "startUrls":
        if max_items != MAX_ITEMS:
            raise TikTokActorError("BAD_MAX_ITEMS",
                                   f"maxItems must be exactly {MAX_ITEMS}")
        inp = {"startUrls": [{"url": url}], "maxItems": MAX_ITEMS}
    else:
        raise TikTokActorError("UNKNOWN_INPUT_STYLE",
                               f"{spec.actor} declares no usable input style")

    leaked = sorted(PAID_ADDON_FIELDS.intersection(inp))
    if leaked:
        raise TikTokActorError("PAID_ADDON_REJECTED",
                               f"paid add-on fields are never sent: {leaked}")
    return inp


def effective_charge_cap_usd(authorized_usd: float = AUTHORIZED_CHARGE_CEILING_USD) -> float:
    """The charge cap Apify will actually accept (its $0.50 floor is a floor)."""
    return max(float(authorized_usd), APIFY_MIN_MAX_TOTAL_CHARGE_USD)


def start_run_params(*, authorized_usd: float = AUTHORIZED_CHARGE_CEILING_USD,
                     ) -> dict[str, Any]:
    """RUN parameters (not actor input) for the start-run call.

    Returns the EFFECTIVE cap — the larger of the authorized bound and Apify's
    $0.50 minimum. The authorized bound is unchanged and still enforced by the
    ledger; ``charge_cap_report`` states both.
    """
    cap = effective_charge_cap_usd(authorized_usd)
    if cap <= 0:
        raise TikTokActorError("BAD_CHARGE_CAP", "maxTotalChargeUsd must be > 0")
    return {"max_total_charge_usd": cap}


def charge_cap_report(*, authorized_usd: float = AUTHORIZED_CHARGE_CEILING_USD,
                      ) -> dict[str, Any]:
    """Both numbers, plus why they differ (quoted verbatim in the proof report)."""
    cap = effective_charge_cap_usd(authorized_usd)
    return {
        "authorized_usd": authorized_usd,
        "effective_run_parameter_usd": cap,
        "apify_minimum_usd": APIFY_MIN_MAX_TOTAL_CHARGE_USD,
        "clamped": cap != authorized_usd,
        "reason": ("Apify rejects maxTotalChargeUsd below $0.50 "
                   "(max-total-charge-usd-below-minimum); the $0.01 bound is "
                   "enforced by the TikTok ledger reservation and maxItems=1"),
    }


def build_run_input(actor: str, canonical_url: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """(actor_input, run_params) — the two halves deliberately kept separate."""
    return (build_actor_input(actor, canonical_url),
            start_run_params())


# ---------------------------------------------------------------------------
# Run translation
# ---------------------------------------------------------------------------
def unwrap_run(payload: Any) -> dict[str, Any]:
    """Apify v2 wraps a single resource as ``{"data": {...}}``."""
    if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
        return payload["data"]
    return payload if isinstance(payload, dict) else {}


def terminal_state(run: Any) -> str:
    return str(unwrap_run(run).get("status") or "")


def is_terminal(run: Any) -> bool:
    return terminal_state(run) in TERMINAL_STATES


def run_id_of(run: Any) -> str:
    return str(unwrap_run(run).get("id") or "")


def dataset_id_of(run: Any) -> str:
    return str(unwrap_run(run).get("defaultDatasetId") or "")


def usage_total_usd(run: Any) -> float | None:
    """The provider-reported charge, or ``None`` when it is not returned.

    ``None`` is a first-class outcome: the caller must retain the reservation and
    classify the run ``COST_UNVERIFIED`` rather than assume $0.
    """
    data = unwrap_run(run)
    for key in ("usageTotalUsd", "usage_usd", "usageTotalUsdEstimated"):
        if key in data and data[key] is not None:
            try:
                return float(data[key])
            except (TypeError, ValueError):
                return None
    usage = data.get("usage")
    if isinstance(usage, dict):
        for key in ("totalUsd", "total_usd"):
            if usage.get(key) is not None:
                try:
                    return float(usage[key])
                except (TypeError, ValueError):
                    return None
    return None


def charge_cap_seen(run: Any) -> float | None:
    """``maxTotalChargeUsd`` as echoed back on the run, when the API returns it."""
    data = unwrap_run(run)
    for key in ("maxTotalChargeUsd", "maxTotalChargeUsdUsd"):
        if data.get(key) is not None:
            try:
                return float(data[key])
            except (TypeError, ValueError):
                return None
    options = data.get("options")
    if isinstance(options, dict) and options.get("maxTotalChargeUsd") is not None:
        try:
            return float(options["maxTotalChargeUsd"])
        except (TypeError, ValueError):
            return None
    return None


def require_terminal_success(run: Any) -> dict[str, Any]:
    """Refuse unless the run reached a SUCCEEDED terminal state."""
    data = unwrap_run(run)
    state = str(data.get("status") or "")
    if not state:
        raise TikTokActorError("RUN_STATE_MISSING", "run carries no status")
    if state == "SUCCEEDED":
        return data
    if state in FAILED_STATES:
        raise TikTokActorError("RUN_FAILED", f"run ended {state}")
    raise TikTokActorError("RUN_NOT_TERMINAL", f"run is still {state}")


# ---------------------------------------------------------------------------
# Response translation
# ---------------------------------------------------------------------------
def _dig(obj: Any, path: str) -> Any:
    cur = obj
    for part in path.split("."):
        if isinstance(cur, list):
            if not cur:
                return None
            cur = cur[0]
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _first(obj: Any, paths: tuple[str, ...]) -> tuple[Any, str]:
    for path in paths:
        value = _dig(obj, path)
        if value is None or value == "" or value == []:
            continue
        return value, path
    return None, ""


def _first_str(obj: Any, paths: tuple[str, ...]) -> tuple[str, str]:
    value, path = _first(obj, paths)
    if value is None:
        return "", ""
    return str(value).strip(), path


def _first_number(obj: Any, paths: tuple[str, ...]) -> tuple[float | None, str]:
    value, path = _first(obj, paths)
    if value is None:
        return None, ""
    try:
        return float(value), path
    except (TypeError, ValueError):
        return None, path


def _first_media(obj: Any, paths: tuple[str, ...]) -> tuple[str, str]:
    """First usable media URL. A list-valued path yields its first entry."""
    for path in paths:
        value = _dig(obj, path)
        if isinstance(value, list):
            for entry in value:
                if isinstance(entry, str) and entry.strip():
                    return entry.strip(), path
                if isinstance(entry, dict):
                    for key in ("url", "playAddr", "downloadAddr", "src"):
                        if entry.get(key):
                            return str(entry[key]).strip(), path
            continue
        if isinstance(value, dict):
            for key in ("url", "playAddr", "downloadAddr", "src"):
                if value.get(key):
                    return str(value[key]).strip(), path
            continue
        if isinstance(value, str) and value.strip():
            return value.strip(), path
    return "", ""


def is_approved_media_host(hostname: str) -> bool:
    host = (hostname or "").lower().rstrip(".")
    if host in APPROVED_MEDIA_EXACT:
        return True
    return any(host.endswith(suffix) for suffix in APPROVED_MEDIA_SUFFIXES)


def validate_media_url(url: str, *, field_name: str = "media") -> str:
    """https, no embedded credentials, approved TikTok/ByteDance CDN host."""
    if not isinstance(url, str) or not url.strip():
        raise TikTokActorError("MISSING_MEDIA", f"{field_name} URL is absent")
    try:
        parts = urlsplit(url.strip())
    except Exception:
        raise TikTokActorError("BAD_MEDIA_URL", f"unparseable {field_name} URL")
    if parts.scheme != "https":
        raise TikTokActorError("BAD_MEDIA_URL", f"{field_name} URL must be https")
    if parts.username or parts.password:
        raise TikTokActorError("CREDENTIALS", f"{field_name} URL carries credentials")
    if not is_approved_media_host(parts.hostname or ""):
        raise TikTokActorError("MEDIA_HOST_NOT_ALLOWED",
                               f"{field_name} host is not an approved TikTok CDN host")
    return url.strip()


def _is_slideshow(spec: TikTokActorSpec, item: dict) -> bool:
    flag, _ = _first(item, spec.slideshow_flag_paths)
    if isinstance(flag, bool):
        return flag
    if isinstance(flag, str) and flag.strip().lower() in ("true", "yes", "1"):
        return True
    if isinstance(flag, dict):
        return True
    payload, _ = _first(item, spec.slideshow_payload_paths)
    if isinstance(payload, list) and payload and not _first(item, spec.video_paths)[0]:
        return True
    if isinstance(payload, dict):
        return True
    return False


def translate_item(actor: str, item: Any, *,
                   expected_content_id: str,
                   canonical_url: str) -> dict[str, Any]:
    """Translate ONE dataset item into the shared acquisition contract.

    Refusals (stable codes): ``SCHEMA_ERROR``, ``SLIDESHOW_NOT_VIDEO``,
    ``IDENTITY_MISMATCH``, ``MISSING_MEDIA``, ``BAD_MEDIA_URL``,
    ``MEDIA_HOST_NOT_ALLOWED``, ``DURATION_LIMIT``.
    """
    spec = get_actor(actor)
    if not isinstance(item, dict):
        raise TikTokActorError("SCHEMA_ERROR", "dataset item is not an object")

    # A photo/slideshow post can never be acquired as a video.
    if _is_slideshow(spec, item):
        raise TikTokActorError("SLIDESHOW_NOT_VIDEO",
                               "photo/slideshow post is not acquirable as video")

    content_id, id_path = _first_str(item, spec.video_id_paths)
    if not content_id:
        raise TikTokActorError("SCHEMA_ERROR",
                               f"{spec.actor}: no video id at any known path")
    if expected_content_id and content_id != expected_content_id:
        raise TikTokActorError(
            "IDENTITY_MISMATCH",
            f"item id {content_id!r} != authorized {expected_content_id!r}")

    media_url, media_path = _first_media(item, spec.video_paths)
    if not media_url:
        raise TikTokActorError("MISSING_MEDIA",
                               f"{spec.actor}: no downloadable video media")
    validate_media_url(media_url, field_name="video")

    audio_url, audio_path = _first_media(item, spec.audio_paths)
    if audio_url:
        try:
            validate_media_url(audio_url, field_name="audio")
        except TikTokActorError:
            # A separately-hosted audio stream is a convenience, never a hard
            # requirement: the downloaded video's own audio is authoritative.
            audio_url, audio_path = "", ""

    duration, _ = _first_number(item, spec.duration_paths)
    if duration is not None and duration > MAX_DURATION_SECONDS:
        raise TikTokActorError("DURATION_LIMIT",
                               f"source duration {duration}s exceeds {MAX_DURATION_SECONDS}s")

    width, _ = _first_number(item, spec.width_paths)
    height, _ = _first_number(item, spec.height_paths)
    expires_at, _ = _first_str(item, spec.expiry_paths)
    handle, _ = _first_str(item, spec.handle_paths)

    return {
        "actor": spec.actor,
        "kind": "video",
        "content_id": content_id,
        "canonical_url": (canonical_url or "").strip(),
        "handle": handle,
        "media_url": media_url,          # signed + volatile; never persisted
        "audio_url": audio_url,          # signed + volatile; never persisted
        "duration_seconds": duration,
        "width": width,
        "height": height,
        "expires_at": expires_at,
        "field_map": {
            "id": id_path,
            "video": media_path,
            "audio": audio_path,
            "duration": spec.duration_paths[0] if duration is not None else "",
        },
    }


def translate(actor: str, items: Any, *,
              expected_content_id: str,
              canonical_url: str) -> dict[str, Any]:
    """Translate a whole dataset. Refuses zero and multiple results."""
    if not isinstance(items, list):
        raise TikTokActorError("SCHEMA_ERROR", "dataset payload is not a list")
    if len(items) == 0:
        raise TikTokActorError("ZERO_RESULT", "actor returned zero results")
    if len(items) != MAX_ITEMS:
        raise TikTokActorError("MULTIPLE_RESULTS",
                               f"expected exactly {MAX_ITEMS} result, got {len(items)}")
    return translate_item(actor, items[0],
                          expected_content_id=expected_content_id,
                          canonical_url=canonical_url)


def map_provider_error(exc: BaseException) -> str:
    """Stable provider/schema classification for any raised error.

    ``TikTokActorError`` codes pass through unchanged; transport-level failures
    are mapped deterministically so the ledger and the report never rely on a
    free-text message.
    """
    code = getattr(exc, "code", None)
    if isinstance(code, str) and code:
        return code
    name = type(exc).__name__
    return {
        "HTTPError": "PROVIDER_HTTP_ERROR",
        "URLError": "PROVIDER_UNREACHABLE",
        "TimeoutError": "PROVIDER_TIMEOUT",
        "JSONDecodeError": "PROVIDER_BAD_JSON",
        "KeyError": "PROVIDER_SCHEMA_ERROR",
        "TypeError": "PROVIDER_SCHEMA_ERROR",
        "ValueError": "PROVIDER_SCHEMA_ERROR",
    }.get(name, "PROVIDER_ERROR")
