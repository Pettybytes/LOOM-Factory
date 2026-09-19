"""Reddit native media acquisition (Layer 3A) + external-host delegation (Layer 3B).

Native path (proven shape, bounded):

    download video stream ─┐
    download audio stream ─┼→ mux ─→ shared normalization ─→ hash ─→ atomic finalize
                           │        (MP4/H.264/AAC/yuv420p/fast-start)
    probe to establish audio presence

Reddit serves video and audio as SEPARATE streams, so the mux is mandatory — a
video-stream-only download plays silent. Audio presence is decided by PROBING the
downloaded bytes, never by the title or the format list (a post titled
"(no music)" still exposed AAC audio renditions).

A source that is PROBED and found legitimately silent is not an error: it is
finalized video-only through the same contract with `require_audio=False`.

External path (Layer 3B): a post whose media lives on another platform is handed to
that platform's adapter and capability. This module never downloads it here.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import media_normalize
import reaction_library
import reddit_context as rctx
import approved_lane as lane

PLATFORM = "reddit"
CONTENT_TYPE_MP4 = "video/mp4"

MAX_BYTES = media_normalize.MAX_BYTES
MAX_DURATION_SECONDS = media_normalize.MAX_DURATION_SECONDS


class RedditMediaError(RuntimeError):
    def __init__(self, code: str, message: str, *,
                 detail: dict[str, Any] | None = None) -> None:
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message
        self.detail = dict(detail or {})


@dataclass
class RedditMediaIO:
    """Every external effect the native path needs. Faked in tests."""

    download_one_video: Callable[..., tuple]
    classify_downloaded_file: Callable[..., str]
    run_ffprobe: Callable[[str], dict]
    parse_ffprobe_result: Callable[..., dict]
    checksum_sha256: Callable[[str], str]
    finalize: Callable[[str, str], str]
    cleanup_temp: Callable[[str], None]
    run_ffmpeg: Callable[[list[str]], Any]
    run_ffprobe_json: Callable[[str], dict]
    mux_builder: Callable[..., list[str]] | None = None
    tmp_dir: str = ""
    library_dir: str = ""
    download_timeout: float = 60.0


def already_acquired(library_rows: list[dict[str, Any]] | None, *,
                     post_id: str, canonical_url: str = "") -> dict[str, Any] | None:
    """Repeat acquisition answers from the durable row — no second download."""
    for row in library_rows or []:
        if not isinstance(row, dict):
            continue
        if post_id and str(row.get("platform_content_id") or "") == post_id:
            return row
        if canonical_url and str(row.get("canonical_url") or "") == canonical_url:
            return row
    return None


def delegate_external(platform_hint: str, *, capability_lookup=None) -> dict[str, Any]:
    """Layer 3B: hand an external-host post to that platform's adapter/capability."""
    platform = (platform_hint or "").lower()
    if not platform:
        return {"delegated": False, "error": rctx.EXTERNAL_HOST_UNSUPPORTED,
                "message": "external host with no identified destination platform"}
    supported = (capability_lookup(platform) if capability_lookup
                 else rctx.external_supported(platform))
    if not supported:
        return {"delegated": False, "error": rctx.EXTERNAL_HOST_UNSUPPORTED,
                "message": f"{platform!r} is not an acquirable destination",
                "destination_platform": platform}
    return {"delegated": True, "destination_platform": platform,
            "message": f"delegated to the {platform} adapter"}


def _download(io: RedditMediaIO, url: str, *, max_bytes: int) -> tuple[str, int, int, str]:
    try:
        tmp_path, written, status, content_type = io.download_one_video(
            url, io.tmp_dir, max_bytes=max_bytes, timeout=io.download_timeout)
    except Exception as e:  # noqa: BLE001
        raise RedditMediaError(getattr(e, "code", "DOWNLOAD_FAILED"),
                               getattr(e, "message", str(e))[:200]) from e
    try:
        io.classify_downloaded_file(tmp_path, http_status=status,
                                    content_type=content_type, size_bytes=written)
    except Exception as e:  # noqa: BLE001
        io.cleanup_temp(tmp_path)
        raise RedditMediaError(getattr(e, "code", "MALFORMED_MP4"),
                               getattr(e, "message", str(e))[:200]) from e
    return tmp_path, written, status, content_type


def _probe(io: RedditMediaIO, path: str, *, written: int,
           max_bytes: int, max_duration: float) -> dict[str, Any]:
    try:
        meta = io.parse_ffprobe_result(io.run_ffprobe(path),
                                       actual_size_bytes=written,
                                       max_bytes=max_bytes,
                                       max_duration=max_duration)
    except Exception as e:  # noqa: BLE001
        raise RedditMediaError(getattr(e, "code", "MEDIA_INVALID"),
                               getattr(e, "message", str(e))[:200]) from e
    if not meta.get("video_codec"):
        raise RedditMediaError(rctx.NO_VIDEO_STREAM,
                               "downloaded media has no decodable video stream")
    return meta


def acquire_reddit_native(*, target: dict[str, Any], io: RedditMediaIO,
                          library_rows: list[dict[str, Any]] | None = None,
                          persist_library: Callable[[dict], dict] | None = None,
                          max_bytes: int = MAX_BYTES,
                          max_duration: float = MAX_DURATION_SECONDS) -> dict[str, Any]:
    """Acquire ONE native Reddit post into the Reaction Library."""
    post_id = str(target.get("post_id") or "")
    canonical = str(target.get("canonical_url") or "")
    if not post_id or not canonical:
        raise RedditMediaError("BAD_TARGET", "target needs a Reddit post id and canonical URL")

    existing = already_acquired(library_rows, post_id=post_id, canonical_url=canonical)
    if existing is not None:
        return {"ok": True, "idempotent": True, "already_acquired": True,
                "library_row": existing, "post_id": post_id, "canonical_url": canonical,
                "downloaded": False}

    video_url = str(target.get("video_url") or "")
    audio_url = str(target.get("audio_url") or "")
    if not video_url:
        raise RedditMediaError(rctx.NO_VIDEO_STREAM, "target carries no video stream URL")

    video_path = audio_path = muxed_path = norm_path = ""
    muxed = False
    try:
        video_path, written, status, content_type = _download(io, video_url,
                                                             max_bytes=max_bytes)
        meta = _probe(io, video_path, written=written, max_bytes=max_bytes,
                      max_duration=max_duration)

        # --- audio: mux when a stream exists; otherwise VERIFY silence --------
        if not meta.get("has_audio"):
            if audio_url:
                if io.mux_builder is None:
                    raise RedditMediaError("AUDIO_MISSING", "no muxer configured")
                audio_path, awritten, _s, _c = _download(io, audio_url,
                                                         max_bytes=max_bytes)
                muxed_path = str(Path(io.tmp_dir) / "reddit-muxed.mp4")
                io.run_ffmpeg(io.mux_builder(video_path, audio_path, muxed_path))
                if not Path(muxed_path).is_file():
                    raise RedditMediaError("NO_OUTPUT", "mux produced no file")
                written = Path(muxed_path).stat().st_size
                video_path = muxed_path
                muxed = True
                meta = _probe(io, video_path, written=written, max_bytes=max_bytes,
                              max_duration=max_duration)
                if not meta.get("has_audio"):
                    # A stream existed and still produced no audio: that is a
                    # failure, NOT a silent source.
                    raise RedditMediaError(
                        "AUDIO_MISSING", "audio stream downloaded but the mux has no audio")
            # No audio stream offered at all: the source is legitimately silent.
            # Only a probe — which we just did — may declare that.

        has_audio = bool(meta.get("has_audio"))
        audio_state = rctx.audio_state(stream_available=bool(audio_url),
                                       probed_has_audio=has_audio)

        dst_dir = Path(io.library_dir or "")
        if not str(io.library_dir or "").strip():
            raise RedditMediaError("NO_LIBRARY_DIR",
                                   "no Reaction Library directory is configured")
        dst_dir.mkdir(parents=True, exist_ok=True)
        staging = dst_dir / f".{post_id}.normalizing.mp4"
        norm_path = str(staging)
        try:
            report = media_normalize.normalize_and_finalize(
                src=video_path, dst=staging, probe=io.run_ffprobe(video_path),
                run_ffmpeg=io.run_ffmpeg, run_ffprobe=io.run_ffprobe_json,
                max_bytes=max_bytes, max_duration=max_duration,
                require_audio=has_audio)
        except media_normalize.NormalizeError as e:
            raise RedditMediaError(e.code, e.message) from e

        digest = io.checksum_sha256(norm_path)
        if not digest or len(digest) != 64:
            raise RedditMediaError("BAD_HASH", "checksum of the finalized asset failed")

        fields = reaction_library.asset_filename(
            platform=PLATFORM, content_id=post_id, digest=digest,
            source_title=str(target.get("title") or ""),
            creator_name=str(target.get("creator_name") or ""),
            creator_handle=str(target.get("creator_handle") or ""),
            clip_name=str(target.get("clip_name") or ""))
        filename = fields["asset_filename"]
        final_path = str(dst_dir / filename)
        io.finalize(norm_path, final_path)
        norm_path = ""
    finally:
        # Every intermediate file is removed, whether the attempt succeeded or not.
        # `normalize_and_finalize` may already have moved the source, so cleanup
        # tolerates a path that no longer exists.
        for p in (audio_path, muxed_path, video_path):
            if p:
                io.cleanup_temp(p)
        if norm_path and Path(norm_path).exists():
            io.cleanup_temp(norm_path)

    record = reaction_library.plan_library_entry(
        duplicate_fingerprint=canonical or post_id,
        platform=PLATFORM,
        platform_content_id=post_id,
        canonical_url=canonical,
        acquisition_job_id=str(target.get("job_id") or ""),
        filename=filename,
        digest=digest,
        size_bytes=report["size_bytes"],
        duration_seconds=report["duration_seconds"],
        content_type=CONTENT_TYPE_MP4,
        storage_url="",
        source_title=fields["source_title"],
        display_title=fields["display_title"],
        creator_name=fields["creator_name"],
        creator_handle=fields["creator_handle"],
    )
    safe = reaction_library.asset_titles(record)  # sanity: titles readable back
    durable = reaction_library.durable_record(record)
    if persist_library:
        try:
            persisted = persist_library(durable) or durable
        except Exception as e:  # noqa: BLE001
            raise RedditMediaError(
                getattr(e, "code", "LIBRARY_PERSIST_FAILED"),
                f"asset finalized but the Reaction Library row was not written "
                f"({type(e).__name__})",
                detail={"asset_filename": filename, "asset_hash": digest,
                        "local_path": final_path, "record": durable}) from e
    else:
        persisted = durable

    return {
        "ok": True, "idempotent": False, "already_acquired": False,
        "post_id": post_id, "canonical_url": canonical,
        "asset_filename": filename, "asset_hash": digest, "local_path": final_path,
        "display_title": safe["display_title"],
        "size_bytes": report["size_bytes"],
        "duration_seconds": report["duration_seconds"],
        "width": report["width"], "height": report["height"],
        "frame_rate": report["frame_rate"],
        "video_codec": report["video_codec"],
        "audio_codec": report["audio_codec"],
        "has_audio": report.get("has_audio", has_audio),
        "audio_state": audio_state,
        "muxed": muxed,
        "normalized": report.get("normalized", False),
        "remuxed": report.get("remuxed", False),
        "gaps": report.get("gaps", []),
        "http_status": status,
        "downloaded_content_type": content_type,
        "library_row": persisted,
    }
