"""Reddit context + content classification (Layer 2).

Classification is evidence-driven. A specific classification is only returned when
runtime evidence distinguishes it; a generic HTTP 403 is NEVER translated into
`AGE_GATE_REQUIRED`, because that would guess at Reddit's reason.

Sources, in preference order:
  1. the resolved canonical URL (identity is already established by Layer 1)
  2. `yt-dlp` extractor metadata for the exact post
  3. stored Reddit discovery metadata (RSS-derived), when already available

The unauthenticated Reddit `.json` API is NOT used: it returns HTTP 403.
RSS is never used for media acquisition — only for context.
"""

from __future__ import annotations

import re
from typing import Any

PLATFORM = "reddit"

# --- classifications ------------------------------------------------------
NATIVE_VIDEO = "native_reddit_video"
EXTERNAL_HOST = "external_host_media"
IMAGE = "image"
GALLERY = "gallery"
TEXT_ONLY = "text_only"
GIF = "gif"
POST_DELETED = "post_deleted"
POST_REMOVED = "post_removed"
POST_UNAVAILABLE = "post_unavailable"
COMMUNITY_PRIVATE = "community_private"
COMMUNITY_QUARANTINED = "community_quarantined"
AGE_GATED = "age_gated"
UNKNOWN = "unknown_or_unsupported"

CLASSIFICATIONS = (NATIVE_VIDEO, EXTERNAL_HOST, IMAGE, GALLERY, TEXT_ONLY, GIF,
                   POST_DELETED, POST_REMOVED, POST_UNAVAILABLE, COMMUNITY_PRIVATE,
                   COMMUNITY_QUARANTINED, AGE_GATED, UNKNOWN)

# --- failure codes --------------------------------------------------------
POST_UNAVAILABLE_CODE = "POST_UNAVAILABLE"
POST_DELETED_CODE = "POST_DELETED"
POST_REMOVED_CODE = "POST_REMOVED"
COMMUNITY_PRIVATE_CODE = "COMMUNITY_PRIVATE"
COMMUNITY_QUARANTINED_CODE = "COMMUNITY_QUARANTINED"
AGE_GATE_CODE = "AGE_GATE_AUTH_REQUIRED"
NOT_A_VIDEO_POST = "NOT_A_VIDEO_POST"
GALLERY_NOT_VIDEO = "GALLERY_NOT_VIDEO"
EXTERNAL_HOST_UNSUPPORTED = "EXTERNAL_HOST_UNSUPPORTED"
NO_VIDEO_STREAM = "NO_VIDEO_STREAM"
MANIFEST_UNAVAILABLE = "MANIFEST_UNAVAILABLE"
MEDIA_URL_EXPIRED = "MEDIA_URL_EXPIRED"
SCHEMA_MISMATCH = "SCHEMA_MISMATCH"
REDDIT_RATE_LIMITED = "REDDIT_RATE_LIMITED"

#: Extractor key -> LOOM platform (external-host delegation).
EXTRACTOR_PLATFORMS = {
    "youtube": "youtube", "youtube:tab": "youtube", "youtube:search": "youtube",
    "tiktok": "tiktok", "tiktok:user": "tiktok", "instagram": "instagram",
    "twitch": "twitch", "twitch:clips": "twitch", "twitch:vod": "twitch",
    "kick": "kick", "kick:clips": "kick", "kick:vod": "kick",
    "streamable": "streamable", "imgur": "imgur", "gfycat": "gfycat",
    "redgifs": "redgifs", "vimeo": "vimeo", "twitter": "twitter",
}

# --- evidence patterns (ordered; first match wins) ------------------------
#: Typed, wording-specific signals. Deliberately excludes a bare "403"/"forbidden",
#: which carries no reliable reason.
_EVIDENCE_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"this post (?:is|was) deleted|\[deleted\]", POST_DELETED_CODE),
    (r"this post (?:is|was) removed|removed by (?:a )?(?:moderator|reddit)", POST_REMOVED_CODE),
    (r"this (?:community|subreddit) is private|private (?:community|subreddit)",
     COMMUNITY_PRIVATE_CODE),
    (r"quarantin", COMMUNITY_QUARANTINED_CODE),
    (r"this post (?:is|was) unavailable|post (?:is )?unavailable", POST_UNAVAILABLE_CODE),
    (r"nsfw|over 18|mature content|age[- ]restricted|only available for registered users"
     r"|login required|log ?in to view", AGE_GATE_CODE),
    (r"rate ?limit|too many requests|\b429\b", REDDIT_RATE_LIMITED),
    (r"no video formats|no media|not a video", NOT_A_VIDEO_POST),
    (r"manifest|m3u8|mpd", MANIFEST_UNAVAILABLE),
    (r"expired|signature", MEDIA_URL_EXPIRED),
)

GALLERY_HINTS = ("gallery", "carousel")
IMAGE_HINTS = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic")
GIF_HINTS = ("gif", "gifv")


def classify_failure(message: str) -> str:
    """Map an extractor/HTTP message to ONE evidence-backed code, or ''."""
    text = str(message or "").lower()
    for pattern, code in _EVIDENCE_PATTERNS:
        if re.search(pattern, text):
            return code
    return ""


def _host(url: str) -> str:
    from urllib.parse import urlsplit
    return (urlsplit(url or "").hostname or "").lower()


def classify_metadata(meta: dict[str, Any] | None) -> dict[str, Any]:
    """Classify one post from yt-dlp metadata. Returns classification + evidence."""
    meta = meta or {}
    extractor = str(meta.get("extractor_key") or meta.get("extractor") or "")
    key = extractor.split(":")[0].lower()
    evidence: dict[str, Any] = {"extractor": extractor}

    if not meta:
        return {"classification": UNKNOWN, "evidence": evidence,
                "reason": "no extractor metadata"}

    if key and key != "reddit":
        destination = EXTRACTOR_PLATFORMS.get(extractor.lower()) or EXTRACTOR_PLATFORMS.get(key, "")
        evidence["destination_platform"] = destination
        return {"classification": EXTERNAL_HOST, "evidence": evidence,
                "platform_hint": destination, "reason": f"extractor {extractor!r}"}

    formats = [f for f in (meta.get("formats") or []) if isinstance(f, dict)]
    video = [f for f in formats if str(f.get("vcodec") or "none") != "none"]
    audio = [f for f in formats if str(f.get("acodec") or "none") != "none"
             and str(f.get("vcodec") or "none") == "none"]
    evidence.update({"format_count": len(formats), "video_streams": len(video),
                     "audio_streams": len(audio)})

    if video:
        ext = str(meta.get("ext") or "").lower()
        if ext in GIF_HINTS:
            return {"classification": GIF, "evidence": evidence, "reason": "gif container"}
        return {"classification": NATIVE_VIDEO, "evidence": evidence,
                "reason": "Reddit extractor with video streams"}

    # No video streams: fall back to URL/ext hints rather than asserting.
    url = str(meta.get("url") or meta.get("webpage_url") or "").lower()
    if any(url.endswith(h) for h in IMAGE_HINTS):
        return {"classification": IMAGE, "evidence": evidence, "reason": "image extension"}
    if any(h in url for h in GALLERY_HINTS):
        return {"classification": GALLERY, "evidence": evidence, "reason": "gallery hint"}
    if not formats:
        return {"classification": UNKNOWN, "evidence": evidence,
                "reason": "no formats and no typed evidence"}
    return {"classification": TEXT_ONLY, "evidence": evidence,
            "reason": "no video/audio streams"}


def select_streams(meta: dict[str, Any]) -> dict[str, Any]:
    """Choose ONE compatible video stream and ONE audio stream (or none).

    Compatibility means the shared normalization contract can accept it: H.264
    video and AAC audio. Reddit's `dash-*` renditions are exactly that. The highest
    bitrate wins on both sides, and a DIRECT (`https`) stream is preferred over a
    segmented protocol (`m3u8`/DASH segments) so the bounded single-file downloader
    keeps its byte-ceiling discipline.
    """
    formats = [f for f in (meta or {}).get("formats") or [] if isinstance(f, dict)]

    def br(f: dict) -> float:
        try:
            return float(f.get("tbr") or f.get("vbr") or f.get("abr") or 0)
        except (TypeError, ValueError):
            return 0.0

    def direct(f: dict) -> bool:
        proto = str(f.get("protocol") or "https").lower()
        return proto in ("https", "http", "")

    def is_h264(f: dict) -> bool:
        return str(f.get("vcodec") or "").lower().startswith(("avc", "h264"))

    def best(cands: list[dict]) -> dict | None:
        if not cands:
            return None
        direct_ones = [f for f in cands if direct(f)]
        return max(direct_ones or cands, key=br)

    videos = [f for f in formats
              if str(f.get("vcodec") or "none") != "none"
              and str(f.get("acodec") or "none") == "none"]
    audios = [f for f in formats
              if str(f.get("acodec") or "none") != "none"
              and str(f.get("vcodec") or "none") == "none"]

    compatible_video = [f for f in videos if is_h264(f)] or videos
    compatible_audio = [f for f in audios
                        if str(f.get("acodec") or "").lower().startswith(("mp4a", "aac"))] or audios

    chosen_video = best(compatible_video)
    chosen_audio = best(compatible_audio)
    return {
        "video": chosen_video,
        "audio": chosen_audio,
        #: Audio EXISTS as a stream. Whether it carries real signal is decided by
        #: probing the downloaded bytes — never by the title or the format list.
        "audio_stream_available": chosen_audio is not None,
        "video_candidates": len(videos),
        "audio_candidates": len(audios),
    }


def audio_state(*, stream_available: bool, probed_has_audio: bool | None) -> str:
    """Only a PROBE may declare a source silent."""
    if probed_has_audio is True:
        return "audio"
    if probed_has_audio is False:
        return "verified_silent" if not stream_available else "stream_silent"
    return "unprobed"


def external_supported(platform: str) -> bool:
    """Which external destinations LOOM can actually acquire today."""
    supported = {"youtube", "tiktok", "instagram", "twitch", "kick"}
    return (platform or "").lower() in supported
