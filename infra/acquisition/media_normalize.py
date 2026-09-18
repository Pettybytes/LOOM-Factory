"""Reaction Library delivery-format contract (shared by every acquisition path).

Everything finalized into the library must play in QuickTime, OBS, OpenShorts,
browsers and the future SaaS platform, so the delivery format is fixed:

    MP4 container · H.264 video · AAC audio · yuv420p · fast-start metadata

Source dimensions, aspect ratio, orientation and frame rate are preserved (no
scaling filters), and audio/video sync is preserved by re-encoding both streams in
one pass. Anything already compliant is passed through untouched — normalizing is
a fallback, never the default. The existing ≤100 MiB and ≤300 s limits are
re-checked on the FINALIZED asset, including after normalization.

Pure command builders and validators keep this testable without ffmpeg.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any, Callable

COMPAT_VIDEO_CODEC = "h264"
COMPAT_AUDIO_CODEC = "aac"
COMPAT_PIX_FMT = "yuv420p"
COMPAT_CONTAINER_MARKERS = ("mp4", "mov", "m4a", "m4v")   # the MP4 family
FASTSTART_FLAG = "+faststart"

MAX_BYTES = 100 * 1024 * 1024
MAX_DURATION_SECONDS = 300
MIB = 1024 * 1024

DEFAULT_CRF = 20
DEFAULT_PRESET = "veryfast"
DEFAULT_AUDIO_BITRATE = "160k"


class NormalizeError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message


# ---------------------------------------------------------------------------
# Inspection (pure)
# ---------------------------------------------------------------------------
def _streams(probe: dict[str, Any]) -> tuple[list[dict], list[dict]]:
    streams = [s for s in (probe or {}).get("streams") or [] if isinstance(s, dict)]
    video = [s for s in streams if s.get("codec_type") == "video"]
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    return video, audio


def rotation_degrees(probe: dict[str, Any]) -> int:
    """Display-matrix rotation carried in side data, if any (0 otherwise)."""
    video, _ = _streams(probe)
    for stream in video:
        for side in stream.get("side_data_list") or []:
            if not isinstance(side, dict):
                continue
            kind = str(side.get("side_data_type", "")).lower()
            if "rotation" in kind or "display matrix" in kind:
                try:
                    return int(side.get("rotation") or 0)
                except (TypeError, ValueError):
                    return 0
        tags = stream.get("tags") or {}
        if "rotate" in {str(k).lower() for k in tags}:
            try:
                return int([v for k, v in tags.items()
                            if str(k).lower() == "rotate"][0])
            except (TypeError, ValueError, IndexError):
                return 0
    return 0


def compatibility_gaps(probe: dict[str, Any]) -> list[str]:
    """Why this media is not delivery-ready (empty list == already compatible)."""
    gaps: list[str] = []
    fmt = (probe or {}).get("format") or {}
    names = str(fmt.get("format_name") or "")
    if names and not any(marker in names for marker in COMPAT_CONTAINER_MARKERS):
        gaps.append(f"container:{names}")
    video, audio = _streams(probe)
    if len(video) != 1:
        gaps.append(f"video_streams:{len(video)}")
    if not audio:
        gaps.append("no_audio")
    if video:
        vcodec = str(video[0].get("codec_name") or "").lower()
        if vcodec != COMPAT_VIDEO_CODEC:
            gaps.append(f"vcodec:{vcodec}")
        pix = str(video[0].get("pix_fmt") or "").lower()
        if pix and pix != COMPAT_PIX_FMT:
            gaps.append(f"pix_fmt:{pix}")
    if audio:
        acodec = str(audio[0].get("codec_name") or "").lower()
        if acodec != COMPAT_AUDIO_CODEC:
            gaps.append(f"acodec:{acodec}")
    return gaps


def needs_normalization(probe: dict[str, Any]) -> bool:
    return bool(compatibility_gaps(probe))


# ---------------------------------------------------------------------------
# Normalization command (pure)
# ---------------------------------------------------------------------------
def build_normalize_command(src: str | Path, dst: str | Path, *,
                            probe: dict[str, Any] | None = None,
                            crf: int = DEFAULT_CRF,
                            preset: str = DEFAULT_PRESET,
                            audio_bitrate: str = DEFAULT_AUDIO_BITRATE,
                            ffmpeg: str = "ffmpeg") -> list[str]:
    """One-pass H.264/AAC/yuv420p transcode that mirrors the source geometry.

    No scaling or fps filters are used, so dimensions, aspect ratio, orientation
    and frame rate carry over, and both streams are encoded in the same pass so
    audio/video sync is preserved.
    """
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
           "-i", str(src),
           "-map", "0:v:0", "-map", "0:a:0",
           "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
           "-pix_fmt", COMPAT_PIX_FMT,
           "-c:a", COMPAT_AUDIO_CODEC, "-b:a", audio_bitrate, "-ac", "2",
           "-movflags", FASTSTART_FLAG,
           "-map_metadata", "-1"]
    rotation = rotation_degrees(probe or {})
    if rotation:
        cmd += ["-metadata:s:v:0", f"rotate={rotation}"]
    cmd += ["-f", "mp4", str(dst)]
    return cmd


# ---------------------------------------------------------------------------
# Finalized-asset validation (pure)
# ---------------------------------------------------------------------------
def validate_finalized(probe: dict[str, Any], *, path: str | Path,
                       max_bytes: int = MAX_BYTES,
                       max_duration: float = MAX_DURATION_SECONDS) -> dict[str, Any]:
    """The delivery gate. Raises NormalizeError; never returns a doubtful result."""
    p = Path(path)
    if not p.is_file():
        raise NormalizeError("MISSING_FILE", "finalized file does not exist")
    size = p.stat().st_size
    if size <= 0:
        raise NormalizeError("ZERO_BYTES", "finalized file is empty")
    if size > max_bytes:
        raise NormalizeError("TOO_LARGE",
                             f"{size} bytes exceeds the {max_bytes}-byte limit")

    gaps = compatibility_gaps(probe)
    if gaps:
        raise NormalizeError("INCOMPATIBLE", "not delivery-ready: " + ", ".join(gaps))

    fmt = (probe or {}).get("format") or {}
    try:
        duration = float(fmt.get("duration"))
    except (TypeError, ValueError):
        raise NormalizeError("BAD_DURATION", "finalized container has no duration")
    if duration <= 0:
        raise NormalizeError("BAD_DURATION", "finalized duration is not positive")
    if duration > max_duration + 0.5:      # container rounding only
        raise NormalizeError("TOO_LONG",
                             f"{duration:.1f}s exceeds the {max_duration}s limit")

    video, audio = _streams(probe)
    return {
        "size_bytes": size,
        "duration_seconds": duration,
        "video_codec": str(video[0].get("codec_name") or "").lower(),
        "audio_codec": str(audio[0].get("codec_name") or "").lower(),
        "pix_fmt": str(video[0].get("pix_fmt") or "").lower(),
        "width": video[0].get("width"),
        "height": video[0].get("height"),
        "frame_rate": video[0].get("avg_frame_rate") or video[0].get("r_frame_rate"),
        "container": str(fmt.get("format_name") or ""),
        "rotation": rotation_degrees(probe),
    }


# ---------------------------------------------------------------------------
# Orchestration (injected runners -> testable without ffmpeg)
# ---------------------------------------------------------------------------
def normalize_and_finalize(*, src: str | Path, dst: str | Path,
                           probe: dict[str, Any],
                           run_ffmpeg: Callable[[list[str]], Any],
                           run_ffprobe: Callable[[str], dict],
                           max_bytes: int = MAX_BYTES,
                           max_duration: float = MAX_DURATION_SECONDS,
                           keep_compatible: bool = True) -> dict[str, Any]:
    """Deliver `src` as a compatible asset at `dst`.

    * already compatible (and ``keep_compatible``) -> the source file is moved, so
      nothing is transcoded;
    * otherwise -> normalize to a temporary file beside the destination, validate
      it, and only then move it into place atomically;
    * on any failure the destination is untouched and the temp file is removed.
    """
    src_path, dst_path = Path(src), Path(dst)
    src_gaps = compatibility_gaps(probe)
    tmp = dst_path.with_name(dst_path.name + ".normalizing.mp4")

    if not src_gaps and keep_compatible:
        src_probe = probe
        report = validate_finalized(src_probe, path=src_path,
                                    max_bytes=max_bytes, max_duration=max_duration)
        os.replace(src_path, dst_path)                  # nothing re-encoded
        return {**report, "normalized": False, "gaps": [], "temp_removed": True}

    try:
        run_ffmpeg(build_normalize_command(src_path, tmp, probe=probe))
        if not tmp.is_file():
            raise NormalizeError("NO_OUTPUT", "ffmpeg produced no normalized file")
        out_probe = run_ffprobe(str(tmp))
        report = validate_finalized(out_probe, path=tmp,
                                    max_bytes=max_bytes, max_duration=max_duration)
        os.replace(tmp, dst_path)                       # atomic swap
    except Exception:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        raise
    return {**report, "normalized": True, "gaps": src_gaps, "temp_removed": True}


def run_ffmpeg_command(cmd: list[str], *, timeout: float = 900.0) -> str:
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip().splitlines()[-1:] or [""]
        raise NormalizeError("FFMPEG_FAILED",
                             f"ffmpeg exited {proc.returncode}: {tail[0][:200]}")
    return proc.stdout


def run_ffprobe_json(path: str) -> dict:
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format",
         "-of", "json", path],
        capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        raise NormalizeError("FFPROBE_FAILED", (proc.stderr or "ffprobe failed")[:200])
    return json.loads(proc.stdout or "{}")
