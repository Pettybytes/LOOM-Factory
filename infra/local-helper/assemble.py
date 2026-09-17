"""fMP4 assembly planning + ffmpeg arg building (deterministic, testable).

The assembly flow: download the manifest (MPD/M3U8), parse it, download the
init segment(s) + ordered media segments, raw-concatenate them into a complete
fMP4, then remux (or mux separate video+audio) with ffmpeg WITHOUT re-encoding.

ffmpeg is always invoked with a fixed argument ARRAY (never shell interpolation).
"""

from __future__ import annotations

import os
from urllib.parse import urlsplit

MAX_SEGMENTS = 400

# A timeout must NOT force assembly of an incomplete fMP4 set. This code is
# returned (surfaced by the extension + helper) when the captured fragment set
# cannot be assembled (missing init, missing/gapped/overlapping/out-of-order
# byte ranges, mixed representations, or incomplete audio).
INSUFFICIENT_FMP4_SET = "INSUFFICIENT_FMP4_SET"


class AssemblyError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message


# ---------------------------------------------------------------------------
# Pure planning / validation
# ---------------------------------------------------------------------------
def plan_assembly(video, audio, *, max_segments: int = MAX_SEGMENTS) -> dict:
    """Validate + plan an fMP4 assembly. Returns a plan, or raises AssemblyError."""
    if not video.get("init_url"):
        raise AssemblyError("MISSING_INIT", "video init segment missing")
    segs = video.get("segments") or []
    if not segs:
        raise AssemblyError("MISSING_SEGMENTS", "video media segments missing")
    if len(segs) > max_segments:
        raise AssemblyError("SEGMENT_LIMIT", f"segment count {len(segs)} exceeds {max_segments}")
    audio_segs = (audio or {}).get("segments") or []
    audio_init = (audio or {}).get("init_url") or ""
    if audio and audio_init and not audio_segs:
        raise AssemblyError("MISSING_AUDIO_SEGMENTS", "audio init present but no audio segments")
    if audio and audio_segs and not audio_init:
        raise AssemblyError("MISSING_AUDIO_INIT", "audio segments present but no audio init")
    return {
        "video_init": video["init_url"],
        "video_segments": segs,
        "audio_init": audio_init,
        "audio_segments": audio_segs,
        "has_audio": bool(audio and audio_init and audio_segs),
    }


def segment_order(segments) -> list:
    """Return segment indices in manifest order (stable, for ordering checks)."""
    return list(range(len(segments or [])))


def _redact_url(url: str) -> str:
    try:
        p = urlsplit(url or "")
        return p.scheme + "://" + p.netloc + (p.path or "")
    except Exception:
        return ""


def validate_ranged_segments(segments) -> list:
    """Validate a ranged fMP4 segment list (pure, deterministic).

    Rejects: missing byte ranges; overlapping or duplicate ranges; gaps (missing
    range); malformed ranges; an open-ended range that is not last; inconsistent
    Content-Range totals (mixed resource / representation); segments from
    different base URLs (mixed representations).

    Raises AssemblyError (``INSUFFICIENT_FMP4_SET`` or ``MIXED_REPRESENTATION``).
    Returns the segments in byte order.
    """
    segs = list(segments or [])
    if not segs:
        raise AssemblyError(INSUFFICIENT_FMP4_SET, "no media segments")
    norm = []
    for s in segs:
        if isinstance(s, str):
            norm.append({"url": s, "range_start": None, "range_end": None, "range_total": None})
        elif isinstance(s, dict):
            norm.append(s)
        else:
            raise AssemblyError(INSUFFICIENT_FMP4_SET, "invalid segment entry")

    # Every segment must carry a byte range to establish a deterministic order.
    if any(s.get("range_start") is None for s in norm):
        raise AssemblyError(INSUFFICIENT_FMP4_SET, "segment missing byte range")

    # All media fragments must belong to the SAME resource (base URL).
    base = _redact_url(norm[0].get("url", ""))
    if any(_redact_url(s.get("url", "")) != base for s in norm):
        raise AssemblyError("MIXED_REPRESENTATION", "segments from different resources")

    ordered = sorted(norm, key=lambda s: s["range_start"])
    total = ordered[0].get("range_total")
    prev_end = None
    for idx, s in enumerate(ordered):
        start = s["range_start"]
        end = s.get("range_end")
        if end is None:
            if idx != len(ordered) - 1:
                raise AssemblyError(INSUFFICIENT_FMP4_SET, "open-ended range is not last")
            continue
        if start > end:
            raise AssemblyError(INSUFFICIENT_FMP4_SET, "malformed range (start > end)")
        st = s.get("range_total")
        if total is not None and st is not None and st != total:
            raise AssemblyError("MIXED_REPRESENTATION", "inconsistent Content-Range totals")
        if prev_end is not None:
            if start <= prev_end:
                raise AssemblyError(INSUFFICIENT_FMP4_SET, "overlapping or duplicate range")
            if start > prev_end + 1:
                raise AssemblyError(INSUFFICIENT_FMP4_SET, "gap (missing range)")
        prev_end = end
    return ordered


def plan_ranged_assembly(video, audio) -> dict:
    """Validate + plan a ranged (no-manifest) fMP4 assembly.

    ``video`` / ``audio`` are dicts with ``init_url`` and a ``segments`` list of
    ranged dicts (``url`` + ``range_start``/``range_end``/``range_total``) or
    plain URL strings. Raises AssemblyError on any invalid/incomplete set.
    """
    if not video.get("init_url"):
        raise AssemblyError("MISSING_INIT", "video init segment missing")
    vsegs = validate_ranged_segments(video.get("segments"))

    audio_init = (audio or {}).get("init_url") or ""
    audio_segs = (audio or {}).get("segments") or []
    if audio and audio_init and not audio_segs:
        raise AssemblyError("MISSING_AUDIO_SEGMENTS", "audio init present but no audio segments")
    if audio and audio_segs and not audio_init:
        raise AssemblyError("MISSING_AUDIO_INIT", "audio segments present but no audio init")
    asegs = validate_ranged_segments(audio_segs) if audio_segs else []

    return {
        "video_init": video["init_url"],
        "video_segments": [s["url"] if isinstance(s, dict) else s for s in vsegs],
        "audio_init": audio_init,
        "audio_segments": [s["url"] if isinstance(s, dict) else s for s in asegs],
        "has_audio": bool(audio and audio_init and audio_segs),
    }


# ---------------------------------------------------------------------------
# Byte concatenation (pure)
# ---------------------------------------------------------------------------
def concatenate_files(paths, output_path) -> int:
    """Raw-byte concatenation of ordered files into output. Returns total bytes."""
    total = 0
    with open(output_path, "wb") as out:
        for p in paths:
            with open(p, "rb") as f:
                while True:
                    chunk = f.read(64 * 1024)
                    if not chunk:
                        break
                    out.write(chunk)
                    total += len(chunk)
    return total


# ---------------------------------------------------------------------------
# ffmpeg argument arrays (fixed — never shell interpolation)
# ---------------------------------------------------------------------------
def build_remux_args(input_path: str, output_path: str) -> list:
    return ["ffmpeg", "-y", "-i", input_path, "-c", "copy",
            "-movflags", "+faststart", output_path]


def build_mux_args(video_path: str, audio_path: str, output_path: str) -> list:
    return ["ffmpeg", "-y", "-i", video_path, "-i", audio_path,
            "-map", "0:v:0", "-map", "1:a:0", "-c", "copy",
            "-movflags", "+faststart", output_path]


# ---------------------------------------------------------------------------
# Failed-job retry semantics
# ---------------------------------------------------------------------------
RETRYABLE_STATUSES = frozenset({"failed", "blocked_pending_platform_adapter"})


def plan_job_retry(existing_status) -> str:
    """Return 'new' | 'retry' | 'duplicate' based on the existing job status.

    A failed (or blocked) job is retryable (idempotent retry); an acquired or
    running job is NOT re-run — it is a true duplicate. This keeps a failed
    duplicate from being misreported as completed."""
    if existing_status is None:
        return "new"
    if existing_status in RETRYABLE_STATUSES:
        return "retry"
    return "duplicate"
