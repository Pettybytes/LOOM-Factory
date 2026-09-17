"""fMP4 download + assembly orchestration (I/O; deterministic parts in
manifest.py + assemble.py).

Download the manifest (MPD/M3U8) or the init + ordered segments, raw-concatenate
them into a complete fMP4, then remux (or mux separate video+audio) with ffmpeg
using a FIXED argument array (never shell interpolation). The caller validates
the final artifact with ffprobe + SHA-256 and finalizes atomically.
"""

from __future__ import annotations

import os
import subprocess

import acquire as acq
import assemble as asm
import manifest as mf
from assemble import AssemblyError


def run_ffmpeg(args, timeout: float = 120) -> bool:
    """Run ffmpeg with a fixed argument array (no shell)."""
    try:
        result = subprocess.run(args, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise AssemblyError("FFMPEG_TIMEOUT", "ffmpeg timed out")
    if result.returncode != 0:
        raise AssemblyError("FFMPEG_FAILED", "ffmpeg failed")
    return True


def _download_many(urls, tmp_dir, max_bytes, timeout):
    files = []
    total = 0
    try:
        for url in urls:
            fp, written, _status, _ct = acq.download_one_video(
                url, tmp_dir, max_bytes=max_bytes, timeout=timeout)
            files.append(fp)
            total += written
            if total > max_bytes:
                raise acq.DownloadError("BYTE_LIMIT", "aggregate bytes exceed ceiling")
        return files, total
    except Exception:
        for f in files:
            acq.cleanup_temp(f)
        raise


def assemble_fmp4(*, video_init, video_segments, audio_init="", audio_segments=None,
                  tmp_dir, max_bytes, timeout):
    """Download init+segments, concatenate, remux/mux. Returns (final_path, bytes)."""
    # Validate the plan BEFORE any download: a timeout/partial set must not be
    # assembled. plan_assembly rejects missing init, missing segments, and
    # incomplete audio (init without segments or vice-versa).
    asm.plan_assembly(
        {"init_url": video_init, "segments": list(video_segments)},
        {"init_url": audio_init, "segments": list(audio_segments or [])}
        if (audio_init or audio_segments) else None,
    )
    vfiles, vbytes = _download_many([video_init] + list(video_segments), tmp_dir, max_bytes, timeout)
    video_path = os.path.join(tmp_dir, "video.mp4")
    asm.concatenate_files(vfiles, video_path)

    audio_path = None
    if audio_init and audio_segments:
        afiles, _abytes = _download_many([audio_init] + list(audio_segments), tmp_dir, max_bytes, timeout)
        audio_path = os.path.join(tmp_dir, "audio.m4a")
        asm.concatenate_files(afiles, audio_path)

    final_path = os.path.join(tmp_dir, "final.mp4")
    if audio_path:
        run_ffmpeg(asm.build_mux_args(video_path, audio_path, final_path))
    else:
        run_ffmpeg(asm.build_remux_args(video_path, final_path))
    return final_path, vbytes


def assemble_from_manifest(*, manifest_url, tmp_dir, max_bytes, timeout):
    """Download + parse a manifest, then assemble. Returns (final_path, bytes)."""
    mfp, _written, _status, _ct = acq.download_one_video(
        manifest_url, tmp_dir, max_bytes=max_bytes, timeout=timeout)
    raw = open(mfp, "rb").read()
    mtype = mf.detect_manifest_type(raw)
    text = raw.decode("utf-8", "ignore")

    if mtype == "mpd":
        info = mf.parse_mpd(text, manifest_url)
    elif mtype == "m3u8":
        pl = mf.parse_m3u8(text, manifest_url)
        info = {"video": {"init_url": pl["init_url"], "segments": pl["segments"]},
                "audio": None, "has_audio": pl["has_audio"]}
    else:
        raise AssemblyError("UNKNOWN_MANIFEST", "not a recognized manifest (mpd/m3u8)")

    plan = asm.plan_assembly(info["video"], info.get("audio"))
    return assemble_fmp4(
        video_init=plan["video_init"], video_segments=plan["video_segments"],
        audio_init=plan["audio_init"], audio_segments=plan["audio_segments"],
        tmp_dir=tmp_dir, max_bytes=max_bytes, timeout=timeout)
