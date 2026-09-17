"""fMP4 assembly planning + ffmpeg arg building (deterministic, testable).

The assembly flow: download the manifest (MPD/M3U8), parse it, download the
init segment(s) + ordered media segments, raw-concatenate them into a complete
fMP4, then remux (or mux separate video+audio) with ffmpeg WITHOUT re-encoding.

ffmpeg is always invoked with a fixed argument ARRAY (never shell interpolation).
"""

from __future__ import annotations

import os

MAX_SEGMENTS = 400


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
