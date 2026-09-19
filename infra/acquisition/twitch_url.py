"""Strict Twitch URL contract: one exact immutable media identity, or a stable refusal.

Supported (canonical forms):
  * VOD   — https://www.twitch.tv/videos/<numeric id>
  * Clip  — https://clips.twitch.tv/<Slug>            (old-style, and the canonical
            form for https://www.twitch.tv/<channel>/clip/<Slug>)

Tracking/query parameters never change identity. `?t=` is preserved only as a
non-identity HINT (a start hint), never as part of the fingerprint.

Rejections carry stable codes so the shared lane can classify them without knowing
anything about Twitch.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, unquote, urlsplit

PLATFORM = "twitch"

# Hosts we will canonicalize. Anything else is a foreign host.
ALLOWED_HOSTS = ("twitch.tv", "www.twitch.tv", "m.twitch.tv", "clips.twitch.tv")

VOD_RE = re.compile(r"^/videos/(\d{5,12})/?$")
CLIP_PATH_RE = re.compile(r"^/([^/]+)/clip/([A-Za-z0-9_-]{6,80})/?$")
CLIP_HOST_RE = re.compile(r"^/([A-Za-z0-9_-]{6,80})/?$")

DIRECTORY_PREFIXES = ("/directory", "/directory/", "/search", "/collections",
                      "/settings", "/subscriptions", "/inventory", "/wallet",
                      "/drops", "/p/", "/downloads")
NON_MEDIA_PREFIXES = ("/downloads", "/jobs", "/turbo", "/store")


class TwitchUrlError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message


def _reject(code: str, message: str):
    raise TwitchUrlError(code, message)


def classify(url: str) -> dict[str, str]:
    """Parse one exact Twitch media URL into an immutable identity.

    Returns {"platform", "canonical_url", "content_id", "kind", "start_hint_ms"}.
    """
    raw = (url or "").strip()
    if not raw:
        _reject("EMPTY_URL", "no URL supplied")
    parts = urlsplit(raw)
    if parts.scheme.lower() not in ("http", "https"):
        _reject("BAD_SCHEME", f"unsupported scheme {parts.scheme!r}")
    if parts.username or parts.password:
        _reject("CREDENTIALS", "URL credentials are not accepted")
    host = (parts.hostname or "").lower()
    if not host:
        _reject("MALFORMED_URL", "no host in URL")
    if host not in ALLOWED_HOSTS:
        _reject("FOREIGN_HOST", f"{host!r} is not a Twitch host")

    path = unquote(parts.path or "")
    query = parse_qs(parts.query or "", keep_blank_values=True)

    # directories / search / collections / playlists are never a single identity
    lowered = path.lower()
    for prefix in DIRECTORY_PREFIXES + NON_MEDIA_PREFIXES:
        if lowered.startswith(prefix):
            _reject("NOT_A_MEDIA_URL",
                    f"{path!r} is a directory, search, collection or non-media page")

    start_hint_ms = _time_hint_ms(query)

    if host == "clips.twitch.tv":
        m = CLIP_HOST_RE.match(path)
        if not m:
            _reject("BAD_CLIP_ID", f"malformed clip slug in {path!r}")
        slug = m.group(1)
        return {"platform": PLATFORM, "canonical_url": f"https://clips.twitch.tv/{slug}",
                "content_id": slug, "kind": "clip",
                "start_hint_ms": str(start_hint_ms)}

    m = VOD_RE.match(path)
    if m:
        vod = m.group(1)
        return {"platform": PLATFORM, "canonical_url": f"https://www.twitch.tv/videos/{vod}",
                "content_id": vod, "kind": "vod", "start_hint_ms": str(start_hint_ms)}

    m = CLIP_PATH_RE.match(path)
    if m:
        slug = m.group(2)
        return {"platform": PLATFORM, "canonical_url": f"https://clips.twitch.tv/{slug}",
                "content_id": slug, "kind": "clip", "start_hint_ms": str(start_hint_ms)}

    # a bare channel path is a homepage — and a live channel has no immutable id
    if re.fullmatch(r"/[A-Za-z0-9_]{1,25}/?", path or ""):
        _reject("CHANNEL_PAGE",
                "a channel page is not one immutable media item (live has no stable id)")

    if path.lower().startswith("/videos"):
        # a /videos path that is not one numeric id is a malformed id or a listing
        _reject("BAD_VOD_ID", f"{path!r} does not contain a valid numeric VOD id")
    if path.lower().startswith("/clip"):
        _reject("MULTIPLE_OR_INCOMPLETE",
                f"{path!r} does not identify exactly one VOD or clip")

    if lowered.startswith("/collections") or "collection" in lowered:
        _reject("COLLECTION", "collections/playlists are not a single media identity")

    _reject("UNSUPPORTED_PATH", f"{path!r} is not a supported Twitch VOD or clip URL")


def _time_hint_ms(query: dict[str, list[str]]) -> int:
    """`?t=1h2m3s` / `?t=90s` as a NON-IDENTITY start hint (0 when absent)."""
    raw = (query.get("t") or [""])[0]
    if not raw:
        return 0
    total = 0
    for value, unit in re.findall(r"(\d+)([hms])", raw.lower()):
        total += int(value) * {"h": 3_600_000, "m": 60_000, "s": 1_000}[unit]
    return total


def parse(url: str, *, expected_content_id: str = "") -> dict[str, str]:
    """classify() + drift check: the URL's identity must match what was approved."""
    parsed = classify(url)
    if expected_content_id and parsed["content_id"] != expected_content_id:
        _reject("SOURCE_ID_DRIFT",
                f"content id {parsed['content_id']!r} != expected {expected_content_id!r}")
    return parsed


def fingerprint_for(url: str, *, expected_content_id: str = "") -> str:
    """Stable identity fingerprint: platform + kind + immutable id (tracking-free)."""
    import hashlib
    parsed = parse(url, expected_content_id=expected_content_id)
    raw = f"{PLATFORM}\x1f{parsed['kind']}\x1f{parsed['content_id']}"
    return hashlib.sha256(raw.encode()).hexdigest()
