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
    """True only for a runtime-proven adapter with no provider cost."""
    model = cost_model(platform)
    return bool(runtime_proven(platform)) and int(model.get("provider_cost_micro", 1)) == 0


# ---------------------------------------------------------------------------
# YouTube — the first (and only) long-form adapter with a runtime proof
# ---------------------------------------------------------------------------
class YouTubeAdapter:
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

    long_form = True
    runtime_proven = False
    cost_model: dict[str, Any] = {}

    def canonicalize(self, url: str, *, expected_content_id: str = "") -> dict[str, str]:
        raise NotImplementedError(
            f"[ADAPTER_NOT_IMPLEMENTED] {self.platform} has no adapter yet")


class TwitchAdapter(_UnimplementedLongFormAdapter):
    platform = "twitch"
    adapter_id = "twitch:unimplemented"


class KickAdapter(_UnimplementedLongFormAdapter):
    platform = "kick"
    adapter_id = "kick:unimplemented"


register_adapter(YouTubeAdapter())
register_adapter(TwitchAdapter())
register_adapter(KickAdapter())
