"""Shared, platform-independent post-acquisition media validation.

Platform-specific code decides **what** its finalized artifact is. This module
decides whether that artifact is **safe for LOOM**. It contains no yt-dlp, no
platform and no network knowledge, so every acquisition mechanism (local yt-dlp,
a provider actor, a local FFmpeg extraction, a future OBS recording) can be gated
by the same contract.

Normalized checks, in order (the first failure is the reported reason):

    exists -> non-zero -> within configured ceiling -> video-bearing
    -> positive duration -> media integrity valid -> eligible for durable storage

WHY INTEGRITY IS A DECODE, NOT METADATA
Runtime evidence from the incident this was extracted from: a truncated artifact
answered ``has_video=True`` at 1920x1080 / 466.6s through ``ffprobe
-show_streams`` (rc=0, empty stderr) while being 19,611 bytes short. Container
metadata therefore cannot gate durable storage; decoding the tail can.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any, Callable

# How much of the artifact's TAIL the integrity gate decode-verifies.
INTEGRITY_TAIL_SECONDS = 2.0

# Stable reason codes. These are the same vocabulary the intake classifier maps
# onto operator-facing messages, so a failure never has to be re-interpreted.
R_MISSING_FILE = "MISSING_FILE"
R_ZERO_BYTES = "ZERO_BYTES"
R_EXCEEDS_CEILING = "MEDIA_EXCEEDS_LIMIT"
R_NO_VIDEO = "NO_VIDEO_STREAM"
R_BAD_DURATION = "BAD_DURATION"
R_INTEGRITY = "MEDIA_INTEGRITY_FAILED"


def sha256_file(path: str | os.PathLike[str]) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def probe_media_file(path: str | os.PathLike[str]) -> dict[str, Any]:
    """Local ffprobe only: duration/dimensions/hash/size. No network, no provider.

    Raises ``ValueError`` when ffprobe itself fails, so a caller can distinguish
    "unreadable file" from "readable but unsafe".
    """
    p = str(path)
    size = os.path.getsize(p)
    cmd = ["ffprobe", "-v", "error", "-print_format", "json", "-show_format",
           "-show_streams", p]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        raise ValueError(f"ffprobe failed ({proc.returncode}): {proc.stderr.strip()[:200]}")
    meta = json.loads(proc.stdout or "{}")
    fmt = meta.get("format") or {}
    streams = meta.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), {})
    audio = next((s for s in streams if s.get("codec_type") == "audio"), {})
    duration = fmt.get("duration") or video.get("duration")
    return {
        "duration_seconds": float(duration) if duration else None,
        "width": video.get("width"),
        "height": video.get("height"),
        "video_codec": (video.get("codec_name") or "").lower(),
        "audio_codec": (audio.get("codec_name") or "").lower(),
        "has_audio": bool(audio),
        "has_video": bool(video),
        "size_bytes": size,
        "sha256": sha256_file(p),
        "container": fmt.get("format_name", ""),
    }


def verify_media_integrity(path: str | os.PathLike[str], *,
                           tail_seconds: float = INTEGRITY_TAIL_SECONDS,
                           runner: Callable[[list[str]], Any] | None = None
                           ) -> dict[str, Any]:
    """Decode-verify the TAIL of a media artifact. Returns a verdict dict.

    Decoding the tail (not the whole file) is what makes truncation detectable at
    negligible cost: measured 0.06 s on a healthy 7:46 1080p source and 0.10 s on
    the truncated artifact, versus a metadata read that passed both.
    """
    p = str(path)
    tail = max(float(tail_seconds), 0.1)
    cmd = ["ffmpeg", "-v", "error", "-xerror",
           "-sseof", f"-{tail:g}", "-i", p, "-f", "null", "-"]
    try:
        if runner is not None:
            runner(cmd)
            return {"ok": True, "reason": "", "detail": "", "tail_seconds": tail}
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    except subprocess.TimeoutExpired:
        return {"ok": False, "reason": "integrity_timeout",
                "detail": "integrity check timed out", "tail_seconds": tail}
    except (OSError, subprocess.SubprocessError) as exc:  # noqa: BLE001
        return {"ok": False, "reason": "integrity_unavailable",
                "detail": type(exc).__name__, "tail_seconds": tail}
    if proc.returncode == 0 and not (proc.stderr or "").strip():
        return {"ok": True, "reason": "", "detail": "", "tail_seconds": tail}
    detail = " | ".join((proc.stderr or "").strip().splitlines()[:3])[:300]
    return {"ok": False, "reason": "media_integrity_failed",
            "detail": detail, "tail_seconds": tail, "returncode": proc.returncode}


def validate_finalized_media(path: str | os.PathLike[str], *,
                             max_bytes: int,
                             probe_fn: Callable[[Any], dict] | None = None,
                             integrity_fn: Callable[[Any], dict] | None = None,
                             require_audio: bool = False) -> dict[str, Any]:
    """The shared post-acquisition contract. Never raises for unsafe media.

    Returns a verdict whose ``checks`` map is the normalized order above, plus the
    probes it used, so a caller records evidence rather than a bare boolean.
    ``eligible`` is True only when every check passed.
    """
    p = str(path)
    _probe = probe_fn or probe_media_file
    _integrity = integrity_fn or verify_media_integrity

    checks: dict[str, bool] = {
        "exists": False, "non_zero": False, "within_ceiling": False,
        "video_bearing": False, "positive_duration": False, "integrity_valid": False,
    }
    out: dict[str, Any] = {
        "eligible": False, "reason": "", "checks": checks, "path": p,
        "size_bytes": None, "duration_seconds": None,
        "has_video": False, "has_audio": False, "sha256": "", "container": "",
        "probe": None, "integrity": None,
    }

    def fail(code: str) -> dict[str, Any]:
        out["reason"] = code
        return out

    if not os.path.isfile(p):
        return fail(R_MISSING_FILE)
    checks["exists"] = True

    size = os.path.getsize(p)
    out["size_bytes"] = size
    if size <= 0:
        return fail(R_ZERO_BYTES)
    checks["non_zero"] = True

    if size > max_bytes:
        return fail(R_EXCEEDS_CEILING)
    checks["within_ceiling"] = True

    try:
        info = _probe(p)
    except Exception as exc:  # noqa: BLE001 — an unreadable file is not safe media
        out["probe_error"] = f"{type(exc).__name__}"
        return fail(R_NO_VIDEO)
    out["probe"] = info
    out["has_video"] = bool(info.get("has_video"))
    out["has_audio"] = bool(info.get("has_audio"))
    out["sha256"] = info.get("sha256", "")
    out["container"] = info.get("container", "")
    out["duration_seconds"] = info.get("duration_seconds")

    if not info.get("has_video"):
        return fail(R_NO_VIDEO)
    if require_audio and not info.get("has_audio"):
        return fail(R_NO_VIDEO)
    checks["video_bearing"] = True

    duration = info.get("duration_seconds")
    if not isinstance(duration, (int, float)) or isinstance(duration, bool) or duration <= 0:
        return fail(R_BAD_DURATION)
    checks["positive_duration"] = True

    verdict = _integrity(p)
    out["integrity"] = verdict
    if not verdict.get("ok"):
        return fail(R_INTEGRITY)
    checks["integrity_valid"] = True

    out["eligible"] = True
    return out
