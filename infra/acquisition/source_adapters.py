"""Platform adapter registry — the shared layer never branches on a platform name.

Shared code (candidate contract, SourceWindow validation, timestamp parsing, the
Adjust Window modal, range-aware identity, approvals, durable authorization, card
states/actions, ACQUIRING/ACQUIRED transitions, library persistence, both intake
paths) must stay platform-neutral.

A platform adapter owns ONLY:
  * URL canonicalization
  * content-ID extraction
  * metadata probing
  * ranged-download implementation
  * authentication / provider requirements
  * cost model
  * platform-specific error classification

Adapters register themselves here; the shared layer asks the registry. Adding
Twitch or Kick is a registration, never a new branch in shared code.

Runtime-proven status is declared by the adapter and must reflect a real bounded
proof. YouTube is the only long-form adapter proven so far; Twitch/Kick ship as
not-implemented stubs and must stay unproven until their real adapters are
connected and exercised.
"""

from __future__ import annotations

from typing import Any, Protocol


class NoAdapterError(ValueError):
    def __init__(self, platform: str) -> None:
        super().__init__(f"[NO_ADAPTER] no adapter registered for platform {platform!r}")
        self.code = "NO_ADAPTER"
        self.platform = platform


class PlatformAdapter(Protocol):
    platform: str
    adapter_id: str
    runtime_proven: bool
    long_form: bool

    def canonicalize(self, url: str, *, expected_content_id: str = "") -> dict[str, str]:
        """Return {platform, canonical_url, content_id} or raise ValueError."""
        ...


_REGISTRY: dict[str, Any] = {}


def register_adapter(adapter: Any, *, replace: bool = False) -> Any:
    name = (getattr(adapter, "platform", "") or "").lower()
    if not name:
        raise ValueError("adapter must declare its platform")
    if name in _REGISTRY and not replace:
        raise ValueError(f"adapter for {name!r} is already registered")
    _REGISTRY[name] = adapter
    return adapter


def unregister_adapter(platform: str) -> None:
    _REGISTRY.pop((platform or "").lower(), None)


def get_adapter(platform: str) -> Any:
    adapter = _REGISTRY.get((platform or "").lower())
    if adapter is None:
        raise NoAdapterError(platform)
    return adapter


def adapter_for(platform: str) -> Any | None:
    return _REGISTRY.get((platform or "").lower())


def registered_platforms() -> tuple[str, ...]:
    return tuple(sorted(_REGISTRY))


def canonicalize(platform: str, url: str, *,
                 expected_content_id: str = "") -> dict[str, str]:
    """Platform-neutral entry point used by every shared module."""
    adapter = get_adapter(platform)
    result = adapter.canonicalize(url, expected_content_id=expected_content_id)
    if not isinstance(result, dict) or not result.get("canonical_url"):
        raise ValueError(f"[BAD_ADAPTER_RESULT] {platform!r} returned no canonical URL")
    return {"platform": result.get("platform") or platform,
            "canonical_url": result["canonical_url"],
            "content_id": result.get("content_id") or ""}


def infer_platform(meta: dict[str, Any] | None) -> str:
    """Which platform does this extractor metadata belong to?"""
    key = str((meta or {}).get("extractor_key") or (meta or {}).get("extractor") or "").lower()
    for platform in registered_platforms():
        if key.startswith(platform):
            return platform
    return ""


def reconcile_identity(platform: str, expected_content_id: str,
                       meta: dict[str, Any] | None) -> bool:
    """Does this metadata belong to the expected immutable identity?

    Platform quirks belong to the ADAPTER (e.g. Twitch reports its own numeric id
    for a slug-identified clip, and prefixes VOD ids with "v"). Shared code only
    asks the registry.
    """
    adapter = adapter_for(platform)
    hook = getattr(adapter, "reconcile_identity", None) if adapter else None
    if callable(hook):
        return bool(hook(expected_content_id, meta or {}))
    return str((meta or {}).get("id") or "") == expected_content_id


def runtime_proven(platform: str) -> bool:
    adapter = adapter_for(platform)
    return bool(adapter and getattr(adapter, "runtime_proven", False))


def cost_model(platform: str) -> dict[str, Any]:
    """Paid-vs-local is an adapter property, never a shared-layer special case."""
    adapter = adapter_for(platform)
    if adapter is None:
        raise NoAdapterError(platform)
    return dict(getattr(adapter, "cost_model", {}) or {})


def is_local_free(platform: str) -> bool:
    """SPEND POLICY: does this adapter incur any provider cost?

    Deliberately independent of `runtime_proven` (which is READINESS). A $0 local
    adapter needs no separate spend authorization, whether or not its end-to-end
    proof has completed yet — the proof is what flips readiness, not what decides
    whether money can be spent.
    """
    model = cost_model(platform)
    return (str(model.get("kind") or "") == "local"
            and int(model.get("provider_cost_micro", 1)) == 0)


def is_implemented(platform: str) -> bool:
    """An adapter exists AND is really implemented (not a declared stub).

    Stubs declare ``implemented = False`` explicitly; a class name is not evidence.
    """
    adapter = adapter_for(platform)
    if adapter is None:
        return False
    return bool(getattr(adapter, "implemented",
                        not isinstance(adapter, _UnimplementedLongFormAdapter)))


# ---------------------------------------------------------------------------
# YouTube — the first (and only) long-form adapter with a runtime proof
# ---------------------------------------------------------------------------
class YouTubeAdapter:
    implemented = True
    platform = "youtube"
    adapter_id = "local:yt-dlp"
    runtime_proven = True          # bounded ranged proof: 2026-09-18
    long_form = True
    cost_model = {"kind": "local", "provider_cost_micro": 0, "paid": False}

    def canonicalize(self, url: str, *, expected_content_id: str = "") -> dict[str, str]:
        import youtube_url as yt
        parsed = yt.parse(url)
        if expected_content_id and parsed["video_id"] != expected_content_id:
            raise ValueError(f"[CONTENT_ID_DRIFT] {parsed['video_id']!r} != "
                             f"{expected_content_id!r}")
        return {"platform": self.platform, "canonical_url": parsed["canonical_url"],
                "content_id": parsed["video_id"]}


class _UnimplementedLongFormAdapter:
    """Declared, not implemented: registering it changes nothing about readiness."""

    implemented = False
    long_form = True
    runtime_proven = False
    cost_model: dict[str, Any] = {}

    def canonicalize(self, url: str, *, expected_content_id: str = "") -> dict[str, str]:
        raise NotImplementedError(
            f"[ADAPTER_NOT_IMPLEMENTED] {self.platform} has no adapter yet")


class TwitchAdapter:
    """Twitch through the SHARED long-form lane.

    This class owns only Twitch-specific concerns: URL canonicalization, content-ID
    extraction, metadata probing, the ranged acquisition transport, platform
    availability classification and Twitch provenance. Timestamp validation,
    windows, range identity, authorization, card states, durable jobs, $0
    accounting, normalization and library persistence stay in shared code.
    """

    implemented = True
    platform = "twitch"
    adapter_id = "local:yt-dlp"        # the same vetted local tool, Twitch transport
    # PROVEN 2026-09-18: a whole 34.3 s clip and a bounded 00:30–01:00 VOD window
    # were both acquired live through this adapter ($0, H.264/AAC/yuv420p/fast-start,
    # hashes verified, library rows range-aware).
    runtime_proven = True
    long_form = True
    cost_model = {"kind": "local", "provider_cost_micro": 0, "paid": False}
    #: VODs may be many hours; only the EXCERPT is bounded (shared clip limit).
    requires_window_for_long_source = True

    def canonicalize(self, url: str, *, expected_content_id: str = "") -> dict[str, str]:
        import twitch_url as tu
        parsed = tu.parse(url, expected_content_id=expected_content_id)
        return {"platform": self.platform,
                "canonical_url": parsed["canonical_url"],
                "content_id": parsed["content_id"],
                "kind": parsed["kind"]}

    def reconcile_identity(self, expected_content_id: str, meta: dict) -> bool:
        """Twitch identity reconciliation.

        A VOD's identity is the numeric id (yt-dlp prefixes it with "v"); a CLIP's
        identity is its slug, while yt-dlp reports its own numeric id — so the URL
        is the cross-check for clips, and a numeric id is accepted because the
        extractor resolved the URL we authorised.
        """
        raw = str((meta or {}).get("id") or "")
        url = str((meta or {}).get("webpage_url") or (meta or {}).get("url") or "")
        if not raw and not url:
            return False
        if expected_content_id.isdigit():
            return raw == expected_content_id or raw.lstrip("v") == expected_content_id
        if url:
            return expected_content_id in url
        return raw.isdigit()

    def classify_availability(self, meta: dict) -> str:
        """Platform-specific availability classification (shared lane codes)."""
        if not isinstance(meta, dict):
            return "UNAVAILABLE"
        if meta.get("is_live"):
            return "LIVE_WITHOUT_IMMUTABLE_ID"
        if meta.get("availability") in ("subscriber_only", "premium_only"):
            return "SUBSCRIBER_ONLY"
        if meta.get("availability") in ("deleted", "unavailable"):
            return "UNAVAILABLE"
        if meta.get("is_private") or meta.get("availability") == "private":
            return "PRIVATE"
        if meta.get("duration") is None:
            return "UNAVAILABLE"
        return "AVAILABLE"


class KickAdapter:
    """Kick through the SHARED long-form lane.

    Kick-specific surface only: URL canonicalization, content-ID extraction,
    clip/VOD classification, metadata probing, ranged acquisition, availability
    classification and provenance. Windows, range identity, authorization, card
    states, durable jobs, $0 accounting, normalization and library persistence stay
    shared.

    Capability status is EXPLICIT and mode-aware:
      * clip mode — implemented; `runtime_proven` stays False until a real bounded
        proof succeeds (this is what flips it), and `proven_modes` records exactly
        which modes are proven.
      * VOD mode — implemented but NOT proven: the supplied proof URL
        `01a02be6-62d0-76a1-b338-0a9c3f3b8175` is externally unavailable (yt-dlp
        `kick:vod` → HTTP 404 in every canonical form; Kick public metadata → 404).
        Kick overall therefore never claims full clip+VOD proof.
    """

    implemented = True
    platform = "kick"
    adapter_id = "local:yt-dlp"          # the same vetted local tool, Kick transport
    runtime_proven = False               # flipped only by a real clip proof
    proven_modes: frozenset = frozenset()  # e.g. {"clip"} after a successful proof
    long_form = True
    requires_window_for_long_source = True
    cost_model = {"kind": "local", "provider_cost_micro": 0, "paid": False}
    capability_note = ("clip: implemented (proof pending); "
                       "vod: implemented_not_proven — supplied proof URL returns 404")

    def canonicalize(self, url: str, *, expected_content_id: str = "") -> dict[str, str]:
        import kick_url as ku
        parsed = ku.parse(url, expected_content_id=expected_content_id)
        return {"platform": self.platform,
                "canonical_url": parsed["canonical_url"],
                "content_id": parsed["content_id"],
                "kind": parsed["kind"]}

    def reconcile_identity(self, expected_content_id: str, meta: dict) -> bool:
        """Kick reports the same immutable id, so this is a straight comparison.

        VOD uuids are case-insensitive; a mismatch in kind is still a mismatch.
        """
        raw = str((meta or {}).get("id") or "")
        url = str((meta or {}).get("webpage_url") or (meta or {}).get("url") or "")
        if not raw and not url:
            return False
        if raw.lower() == expected_content_id.lower():
            return True
        return expected_content_id.lower() in url.lower() if url else False

    def classify_availability(self, meta: dict) -> str:
        if not isinstance(meta, dict):
            return "UNAVAILABLE"
        if meta.get("is_live"):
            return "LIVE_WITHOUT_IMMUTABLE_ID"
        status = str(meta.get("availability") or "").lower()
        if status in ("subscriber_only", "premium_only"):
            return "SUBSCRIBER_ONLY"
        if status in ("deleted", "unavailable"):
            return "UNAVAILABLE"
        if meta.get("is_private") or status == "private":
            return "PRIVATE"
        if meta.get("duration") is None:
            return "UNAVAILABLE"
        return "AVAILABLE"


class KickVodUnavailable(Exception):
    """Raised when a Kick VOD proof URL is externally unavailable (HTTP 404).

    Classified externally, never as a contract or local defect — and it must not
    produce a card or fabricated metadata.
    """

    code = "VOD_EXTERNALLY_UNAVAILABLE"


register_adapter(YouTubeAdapter())
register_adapter(TwitchAdapter())
register_adapter(KickAdapter())
