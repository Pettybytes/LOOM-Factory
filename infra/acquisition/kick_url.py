"""Strict Kick URL contract: one exact immutable media identity, or a stable refusal.

Supported canonical forms:
  * Clip — https://kick.com/<channel>/clips/<clip_id>       (identity: clip_id)
  * VOD  — https://kick.com/<channel>/videos/<uuid>         (identity: uuid)

The channel segment is presentation for a clip and routing for a VOD; the IDENTITY is
the clip id / VOD uuid. Tracking parameters never change identity; `?t=` is preserved
only as a non-identity start hint.

Note (2026-09-18): the supplied Kick VOD `01a02be6-62d0-76a1-b338-0a9c3f3b8175` is
EXTERNALLY UNAVAILABLE — yt-dlp's `kick:vod` extractor returns HTTP 404 for every
canonical form and Kick's public metadata returns 404. That is an external availability
restriction, not a contract defect; no metadata was fabricated for it.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, unquote, urlsplit

PLATFORM = "kick"

ALLOWED_HOSTS = ("kick.com", "www.kick.com", "m.kick.com")

CLIP_RE = re.compile(r"^/([A-Za-z0-9_.-]{1,40})/clips/(clip_[A-Za-z0-9]{8,48})/?$")
CLIP_BARE_RE = re.compile(r"^/clips/(clip_[A-Za-z0-9]{8,48})/?$")
VOD_RE = re.compile(r"^/([A-Za-z0-9_.-]{1,40})/videos/([0-9a-fA-F-]{36})/?$")
VOD_BARE_RE = re.compile(r"^/videos/([0-9a-fA-F-]{36})/?$")

NON_MEDIA_PREFIXES = ("/category", "/categories", "/search", "/browse", "/following",
                      "/settings", "/subscriptions", "/wallet", "/drops", "/messages",
                      "/dashboard", "/stream", "/about", "/terms", "/privacy")


class KickUrlError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message


def _reject(code: str, message: str):
    raise KickUrlError(code, message)


def _time_hint_ms(query: dict[str, list[str]]) -> int:
    """`?t=1h2m3s` / `?t=90s` as a NON-IDENTITY start hint (0 when absent)."""
    raw = (query.get("t") or query.get("start_time") or [""])[0]
    if not raw:
        return 0
    total = 0
    if str(raw).isdigit():                      # bare seconds
        return int(raw) * 1000
    for value, unit in re.findall(r"(\d+)([hms])", str(raw).lower()):
        total += int(value) * {"h": 3_600_000, "m": 60_000, "s": 1_000}[unit]
    return total


def classify(url: str) -> dict[str, str]:
    """Parse one exact Kick media URL into an immutable identity."""
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
        _reject("FOREIGN_HOST", f"{host!r} is not a Kick host")

    path = unquote(parts.path or "")
    lowered = path.lower()
    for prefix in NON_MEDIA_PREFIXES:
        if lowered.startswith(prefix):
            _reject("NOT_A_MEDIA_URL", f"{path!r} is a directory/search/other page")

    start_hint = _time_hint_ms(parse_qs(parts.query or "", keep_blank_values=True))

    m = CLIP_RE.match(path)
    if m:
        channel, clip_id = m.group(1), m.group(2)
        return {"platform": PLATFORM,
                "canonical_url": f"https://kick.com/{channel}/clips/{clip_id}",
                "content_id": clip_id, "kind": "clip", "channel": channel,
                "start_hint_ms": str(start_hint)}
    m = CLIP_BARE_RE.match(path)
    if m:
        clip_id = m.group(1)
        return {"platform": PLATFORM, "canonical_url": f"https://kick.com/clips/{clip_id}",
                "content_id": clip_id, "kind": "clip", "channel": "",
                "start_hint_ms": str(start_hint)}

    m = VOD_RE.match(path)
    if m:
        channel, uuid = m.group(1), m.group(2).lower()
        return {"platform": PLATFORM,
                "canonical_url": f"https://kick.com/{channel}/videos/{uuid}",
                "content_id": uuid, "kind": "vod", "channel": channel,
                "start_hint_ms": str(start_hint)}
    m = VOD_BARE_RE.match(path)
    if m:
        uuid = m.group(1).lower()
        return {"platform": PLATFORM, "canonical_url": f"https://kick.com/videos/{uuid}",
                "content_id": uuid, "kind": "vod", "channel": "",
                "start_hint_ms": str(start_hint)}

    if lowered.startswith("/clips"):
        _reject("BAD_CLIP_ID", f"{path!r} does not contain a valid Kick clip id")
    if lowered.startswith("/videos"):
        _reject("BAD_VOD_ID", f"{path!r} does not contain a valid Kick VOD uuid")
    if re.fullmatch(r"/[A-Za-z0-9_.-]{1,40}/?", path or ""):
        _reject("CHANNEL_PAGE",
                "a channel page is not one immutable media item (a live channel has no "
                "stable id)")
    _reject("UNSUPPORTED_PATH", f"{path!r} is not a supported Kick clip or VOD URL")


def parse(url: str, *, expected_content_id: str = "") -> dict[str, str]:
    parsed = classify(url)
    if expected_content_id and parsed["content_id"] != expected_content_id:
        _reject("SOURCE_ID_DRIFT",
                f"content id {parsed['content_id']!r} != expected {expected_content_id!r}")
    return parsed


def fingerprint_for(url: str, *, expected_content_id: str = "") -> str:
    """Stable identity: platform + kind + immutable id (tracking-free)."""
    import hashlib
    parsed = parse(url, expected_content_id=expected_content_id)
    raw = f"{PLATFORM}\x1f{parsed['kind']}\x1f{parsed['content_id']}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
