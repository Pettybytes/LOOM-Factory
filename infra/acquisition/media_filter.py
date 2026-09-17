"""Media-candidate filtering for browser-assisted Instagram acquisition.

Deterministic, testable reference for the Chrome-extension network probe. The
extension's JS mirrors these rules. Security: https only, approved Instagram/
Meta CDN host, video/audio/manifest content-type or a defensible media
extension — never page HTML / analytics / ads / avatars / JPG covers.

Never returns or persists full signed CDN URLs — only classification + hostname.
"""

from __future__ import annotations

import os
from urllib.parse import urlsplit

# Approved Instagram/Meta media CDN hosts (suffix match — a subdomain of these
# is owned by Meta; a lookalike like "instagram.com.evil.com" is NOT). The
# caller additionally scopes to the single active Reel tab.
_APPROVED_HOST_SUFFIXES = (".cdninstagram.com", ".fbcdn.net", ".akamaihd.net", ".instagram.com")
_APPROVED_EXACT_HOSTS = frozenset({"instagram.com", "cdninstagram.com", "fbcdn.net", "akamaihd.net"})

DIRECT_VIDEO = "direct_video"
MANIFEST = "manifest"   # HLS (.m3u8) or DASH (.mpd)
SEGMENT = "segment"     # DASH/HLS media segments
AUDIO = "audio"

_DIRECT_VIDEO_EXT = {".mp4", ".m4v", ".webm"}
_MANIFEST_EXT = {".m3u8", ".mpd"}
_SEGMENT_EXT = {".m4s", ".fmp4", ".ts", ".cmfv", ".cmfa"}
_AUDIO_EXT = {".m4a", ".aac", ".mp3", ".ogg", ".opus"}

_MEDIA_MIME_MARKERS = ("video/", "audio/", "application/vnd.apple.mpegurl",
                       "application/dash+xml", "application/x-mpegurl",
                       "application/octet-stream")
_EXCLUDED_MIME_MARKERS = ("image/", "text/html", "text/css", "application/javascript",
                          "application/json", "application/x-javascript")


def is_approved_media_host(hostname: str) -> bool:
    h = (hostname or "").lower().rstrip(".")
    if h in _APPROVED_EXACT_HOSTS:
        return True
    return any(h.endswith(s) for s in _APPROVED_HOST_SUFFIXES)


def classify_media_url(url: str) -> str | None:
    """Classify a media URL by extension. Returns a class or None (not media)."""
    try:
        path = urlsplit(url).path or ""
    except Exception:
        return None
    ext = os.path.splitext(path.lower())[1]
    if ext in _DIRECT_VIDEO_EXT:
        return DIRECT_VIDEO
    if ext in _MANIFEST_EXT:
        return MANIFEST
    if ext in _SEGMENT_EXT:
        return SEGMENT
    if ext in _AUDIO_EXT:
        return AUDIO
    return None


def is_media_candidate(url: str, mime_type: str = "") -> bool:
    """True if url is a media candidate: https + approved host + media signature."""
    try:
        parts = urlsplit(url)
    except Exception:
        return False
    if parts.scheme != "https":
        return False
    if not is_approved_media_host(parts.hostname or ""):
        return False
    mt = (mime_type or "").lower()
    if mt:
        if any(m in mt for m in _EXCLUDED_MIME_MARKERS):
            return False
        if any(m in mt for m in _MEDIA_MIME_MARKERS):
            return True
    return classify_media_url(url) is not None


def redact_url(url: str) -> str:
    """Strip query string + fragment (signed tokens). Returns origin + path."""
    try:
        parts = urlsplit(url)
        return parts.scheme + "://" + parts.netloc + (parts.path or "")
    except Exception:
        return "(unparseable)"
