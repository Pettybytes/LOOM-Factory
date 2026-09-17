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

DIRECT_VIDEO = "direct_video"   # unambiguous standalone container (.m4v / .webm)
MP4 = "mp4"                     # AMBIGUOUS: standalone (ftyp) OR fMP4 fragment (moof/styp/sidx)
MANIFEST = "manifest"           # HLS (.m3u8) or DASH (.mpd)
SEGMENT = "segment"             # DASH/HLS media segments
AUDIO = "audio"

REPRESENTATION_VIDEO = "video"
REPRESENTATION_AUDIO = "audio"

# ``.mp4`` is AMBIGUOUS on Instagram: the CDN serves fragmented-MP4 fragments
# with ``.mp4`` suffixes. Only the byte signature (ftyp vs moof/styp/sidx) is
# authoritative, so ``.mp4`` is NOT classified as a standalone direct video.
_DIRECT_VIDEO_EXT = {".m4v", ".webm"}
_MP4_EXT = {".mp4"}
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
    if ext in _MP4_EXT:
        return MP4
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


def representation_of(mime_type: str = "", url: str = "") -> str | None:
    """Best-effort video vs audio representation from MIME type or extension.

    Returns REPRESENTATION_AUDIO / REPRESENTATION_VIDEO, or None if unknown.
    """
    mt = (mime_type or "").lower()
    if mt.startswith("audio/"):
        return REPRESENTATION_AUDIO
    if mt.startswith("video/"):
        return REPRESENTATION_VIDEO
    try:
        ext = os.path.splitext(urlsplit(url).path or "")[1].lower()
    except Exception:
        ext = ""
    if ext in _AUDIO_EXT:
        return REPRESENTATION_AUDIO
    if ext in (_DIRECT_VIDEO_EXT | _MP4_EXT | _SEGMENT_EXT):
        return REPRESENTATION_VIDEO
    return None


def redact_url(url: str) -> str:
    """Strip query string + fragment (signed tokens). Returns origin + path."""
    try:
        parts = urlsplit(url)
        return parts.scheme + "://" + parts.netloc + (parts.path or "")
    except Exception:
        return "(unparseable)"


# ---------------------------------------------------------------------------
# Request classification + inspection summary (diagnostic, never null)
# ---------------------------------------------------------------------------
REJECTION_SCHEME = "scheme_rejected"
REJECTION_HOSTNAME = "hostname_rejected"
REJECTION_RESOURCE_TYPE = "resource_type_rejected"
REJECTION_CONTENT_TYPE = "content_type_rejected"
REJECTION_IMAGE = "image_rejected"
REJECTION_AD = "analytics_ad_rejected"
ACCEPTED = "accepted"

_NON_MEDIA_RESOURCE_TYPES = frozenset({
    "Document", "Stylesheet", "Script", "Font", "Manifest", "TextTrack",
    "EventSource", "WebSocket", "Ping", "Preflight",
})

_IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif", ".heic", ".bmp"}

_AD_HOST_MARKERS = ("analytics", "telemetry", "tracking", "ads.", "doubleclick")
_AD_PATH_MARKERS = ("/analytics", "/telemetry", "/tracking", "/ads", "/tr")


def is_analytics_or_ad(hostname: str, path: str) -> bool:
    h = (hostname or "").lower()
    p = (path or "").lower()
    if any(m in h for m in _AD_HOST_MARKERS):
        return True
    return any(m in p for m in _AD_PATH_MARKERS)


def _is_image_url(url: str, mime_type: str) -> bool:
    mt = (mime_type or "").lower()
    if mt.startswith("image/"):
        return True
    try:
        ext = os.path.splitext(urlsplit(url).path or "")[1].lower()
    except Exception:
        return False
    return ext in _IMAGE_EXT


def classify_request(url: str, mime_type: str = "", resource_type: str = "") -> str:
    """Classify a captured network request: a rejection reason or 'accepted'."""
    if not (url or "").startswith("https://"):
        return REJECTION_SCHEME
    rt = resource_type or ""
    if rt == "Image":
        return REJECTION_IMAGE
    if rt in _NON_MEDIA_RESOURCE_TYPES:
        return REJECTION_RESOURCE_TYPE
    try:
        parts = urlsplit(url)
    except Exception:
        return REJECTION_CONTENT_TYPE
    hostname = parts.hostname or ""
    if is_analytics_or_ad(hostname, parts.path or ""):
        return REJECTION_AD
    if not is_approved_media_host(hostname):
        return REJECTION_HOSTNAME
    if _is_image_url(url, mime_type):
        return REJECTION_IMAGE
    if is_media_candidate(url, mime_type):
        return ACCEPTED
    return REJECTION_CONTENT_TYPE


def summarize_inspection(candidates: list, counts: dict) -> dict:
    """Explicit result dict — NEVER null. Zero candidates -> explicit reason."""
    n = len(candidates)
    return {
        "result": "no_media_traffic_observed" if n == 0 else "complete",
        "candidateCount": n,
        "candidates": list(candidates),
        "rejectionCounts": dict(counts or {}),
    }
