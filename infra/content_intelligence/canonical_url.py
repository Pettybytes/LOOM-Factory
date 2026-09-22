"""Human-facing vs internal media URLs.

A card's Open Source action must always point at the canonical platform PAGE.
Direct media (``.m3u8``/CDN/playlist/temporary signed assets) may be used
internally for retrieval, and must never become the user-facing source URL.
"""

from __future__ import annotations

from urllib.parse import urlsplit

MEDIA_SUFFIXES = (".m3u8", ".mp4", ".mpd", ".ts", ".webm", ".m4v", ".mov", ".gifv")
CDN_HOST_MARKERS = ("cdninstagram.com", "fbcdn.net", "tiktokcdn", "twitchcdn",
                    "ttvnw.net", "kick.com/clips/", "googlevideo.com", "ytimg.com",
                    "redd.it", "v.redd.it", "video.twimg.com")


def is_media_url(url: str) -> bool:
    """True when the URL is an internal media/manifest target, not a page."""
    u = str(url or "").strip().lower()
    if not u:
        return False
    path = urlsplit(u).path
    if any(path.endswith(s) for s in MEDIA_SUFFIXES):
        return True
    # media manifests are commonly nested under a path segment
    if any(seg in path for seg in ("/playlist", "/manifest", "/hls", "/dash")) and path.count("/") > 2:
        return True
    return any(m in u for m in CDN_HOST_MARKERS)


def canonical_source_url(url: str, *, fallback: str = "") -> str:
    """Return a human-facing page URL, never an internal media URL."""
    candidate = str(url or "").strip()
    if candidate and not is_media_url(candidate):
        return candidate
    return str(fallback or "").strip()


def assert_canonical_source(url: str) -> None:
    """Raise when an internal media URL is used as the user-facing source."""
    if is_media_url(url):
        raise ValueError(f"internal media URL cannot be the user-facing source: {url[:60]}")
