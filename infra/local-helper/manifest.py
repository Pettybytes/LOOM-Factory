"""DASH MPD + HLS M3U8 manifest parsing for fMP4 assembly (deterministic).

Instagram serves Reels as fragmented-MP4 (DASH/HLS). The captured ".mp4" URL is
actually a media segment; a playable MP4 requires the manifest (MPD/M3U8) plus
its initialization segment + ordered media segments. This module parses the
manifest into that structure so the helper can download + assemble.

Security is enforced by the caller (https + approved CDN suffix + redirect
validation + ceilings). These parsers only extract structure; they never fetch.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from urllib.parse import urljoin


def detect_manifest_type(data) -> str:
    """Return 'mpd' | 'm3u8' | None from raw manifest bytes/text."""
    if isinstance(data, bytes):
        text = data[:4096].decode("utf-8", "ignore")
    else:
        text = (data or "")[:4096]
    tl = text.lstrip()
    if tl.startswith("#EXTM3U"):
        return "m3u8"
    if "<MPD" in text or "<mpd" in text or "<?xml" in text:
        return "mpd"
    return None


def _resolve_url(uri: str, base_url: str) -> str:
    if not uri:
        return ""
    if re.match(r"^https?://", uri):
        return uri
    if base_url:
        return urljoin(base_url, uri)
    return uri


def _extract_uri(line: str) -> str:
    m = re.search(r'URI="([^"]+)"', line)
    return m.group(1) if m else ""


def _substitute(template: str, number: int) -> str:
    return template.replace("$Number$", str(number)).replace("$RepresentationID$", "")


def parse_m3u8(text: str, base_url: str = "") -> dict:
    """Parse an HLS playlist -> {init_url, segments, audio_playlist_url, has_audio}."""
    init_url = ""
    segments = []
    audio_playlist_url = ""
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#EXT-X-MAP:") and "URI=" in line:
            init_url = _extract_uri(line)
        elif line.startswith("#EXT-X-MEDIA:") and "TYPE=AUDIO" in line.upper() and "URI=" in line:
            audio_playlist_url = _extract_uri(line)
        elif line.startswith("#"):
            continue
        else:
            segments.append(line)
    return {
        "init_url": _resolve_url(init_url, base_url),
        "segments": [_resolve_url(s, base_url) for s in segments],
        "audio_playlist_url": _resolve_url(audio_playlist_url, base_url) if audio_playlist_url else "",
        "has_audio": bool(audio_playlist_url),
    }


def _find_child(elem, suffix):
    for child in elem:
        if child.tag.endswith(suffix):
            return child
    return None


def _mpd_segment_urls(st, media_template: str, base_url: str) -> list:
    segs = []
    start_number = int(st.get("startNumber", "0") or 0)
    timeline = _find_child(st, "SegmentTimeline")
    if timeline is not None:
        num = start_number
        for s in timeline:
            if not s.tag.endswith("S"):
                continue
            r = int(s.get("r", "0") or 0)
            for _ in range(r + 1):
                url = _substitute(media_template, num)
                if url:
                    segs.append(url)
                num += 1
    else:
        # No timeline: no duration info here. Emit a bounded placeholder list;
        # the caller enforces a hard segment-count ceiling.
        for i in range(400):
            url = _substitute(media_template, start_number + i)
            if url:
                segs.append(url)
    return [_resolve_url(s, base_url) for s in segs]


def parse_mpd(xml_text: str, base_url: str = "") -> dict:
    """Parse a DASH MPD -> {manifest_type, video:{init_url,segments}, audio|None, has_audio}."""
    root = ET.fromstring(xml_text)
    video = {"init_url": "", "segments": []}
    audio = None
    for aset in root.iter():
        if not aset.tag.endswith("AdaptationSet"):
            continue
        mime = (aset.get("mimeType", "") or aset.get("contentType", "")).lower()
        st = _find_child(aset, "SegmentTemplate")
        if st is None:
            continue
        init = st.get("initialization", "")
        media = st.get("media", "")
        entry = {
            "init_url": _resolve_url(init, base_url) if init else "",
            "segments": _mpd_segment_urls(st, media, base_url),
        }
        if "audio" in mime:
            if audio is None:
                audio = entry
        else:
            if not video["segments"] and not video["init_url"]:
                video = entry
    has_audio = audio is not None and bool(audio["segments"])
    return {"manifest_type": "mpd", "video": video, "audio": audio, "has_audio": has_audio}
