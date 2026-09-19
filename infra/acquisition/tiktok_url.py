"""Strict TikTok URL contract: one exact immutable video identity, or a stable refusal.

Canonical forms:
  * Video — https://www.tiktok.com/@<handle>/video/<numeric id>
  * Photo/slideshow — https://www.tiktok.com/@<handle>/photo/<numeric id>  (NOT a video)

Short links (vm.tiktok.com/<code>, vt.tiktok.com/<code>) carry NO identity: they must be
resolved first, and an unresolved short link is refused rather than guessed.

Tracking/query parameters never change identity. Identity is the numeric video id.
"""

from __future__ import annotations

import re
from urllib.parse import unquote, urlsplit

PLATFORM = "tiktok"

ALLOWED_HOSTS = ("tiktok.com", "www.tiktok.com", "m.tiktok.com")
SHORT_HOSTS = ("vm.tiktok.com", "vt.tiktok.com")

VIDEO_RE = re.compile(r"^/@([A-Za-z0-9._]{2,24})/video/(\d{6,25})/?$")
PHOTO_RE = re.compile(r"^/@([A-Za-z0-9._]{2,24})/photo/(\d{6,25})/?$")
PROFILE_RE = re.compile(r"^/@([A-Za-z0-9._]{2,24})/?$")

NON_MEDIA_PREFIXES = ("/search", "/discover", "/foryou", "/following", "/live",
                      "/upload", "/messages", "/setting", "/t/", "/tag/")


class TikTokUrlError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message


def _reject(code: str, message: str):
    raise TikTokUrlError(code, message)


def is_short_link(url: str) -> bool:
    host = (urlsplit((url or "").strip()).hostname or "").lower()
    return host in SHORT_HOSTS


def classify(url: str, *, resolved_url: str = "") -> dict[str, str]:
    """Parse one exact TikTok media URL into an immutable identity.

    A short link MUST be resolved first (pass `resolved_url`); otherwise the refusal is
    SHORT_LINK_UNRESOLVED. Identity is never guessed from a redirect code.
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

    if host in SHORT_HOSTS:
        if not resolved_url:
            _reject("SHORT_LINK_UNRESOLVED",
                    "a TikTok short link must be resolved before identity is known")
        return classify(resolved_url)

    if host not in ALLOWED_HOSTS:
        _reject("FOREIGN_HOST", f"{host!r} is not a TikTok host")

    path = unquote(parts.path or "")
    lowered = path.lower()
    for prefix in NON_MEDIA_PREFIXES:
        if lowered.startswith(prefix):
            _reject("NOT_A_MEDIA_URL", f"{path!r} is a search/discover/tag page")

    m = VIDEO_RE.match(path)
    if m:
        handle, vid = m.group(1), m.group(2)
        return {"platform": PLATFORM, "kind": "video", "handle": handle,
                "content_id": vid,
                "canonical_url": f"https://www.tiktok.com/@{handle}/video/{vid}"}
    m = PHOTO_RE.match(path)
    if m:
        handle, vid = m.group(1), m.group(2)
        return {"platform": PLATFORM, "kind": "slideshow", "handle": handle,
                "content_id": vid,
                "canonical_url": f"https://www.tiktok.com/@{handle}/photo/{vid}"}

    if lowered.startswith("/@") and "/video" in lowered:
        _reject("BAD_VIDEO_ID", f"{path!r} does not contain a valid TikTok video id")
    if lowered.startswith("/@") and "/photo" in lowered:
        _reject("BAD_PHOTO_ID", f"{path!r} does not contain a valid TikTok photo id")
    if PROFILE_RE.match(path):
        _reject("PROFILE_PAGE", "a profile page is not one immutable video")
    _reject("UNSUPPORTED_PATH", f"{path!r} is not a supported TikTok video URL")


def parse(url: str, *, expected_content_id: str = "",
          resolved_url: str = "") -> dict[str, str]:
    parsed = classify(url, resolved_url=resolved_url)
    if expected_content_id and parsed["content_id"] != expected_content_id:
        _reject("SOURCE_ID_DRIFT",
                f"content id {parsed['content_id']!r} != expected {expected_content_id!r}")
    return parsed


def require_video(url: str, *, resolved_url: str = "") -> dict[str, str]:
    """Media path guard: photo/slideshow content is refused, never passed off as video."""
    parsed = classify(url, resolved_url=resolved_url)
    if parsed["kind"] != "video":
        _reject("SLIDESHOW_NOT_VIDEO",
                "photo/slideshow posts are not acquirable as video")
    return parsed


def fingerprint_for(url: str, *, resolved_url: str = "") -> str:
    import hashlib
    parsed = classify(url, resolved_url=resolved_url)
    raw = f"{PLATFORM}\x1f{parsed['content_id']}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
