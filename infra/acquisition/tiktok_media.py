"""TikTok → shared media pipeline.

A successful TikTok provider translation produces volatile signed media URLs.
This module turns those URLs into ONE durable Reaction Library asset through the
SAME pipeline every other acquisition path uses:

    download → reject HTML/error payloads → ffprobe (validate video + audio)
             → mux a separate audio stream when the video carries none
             → normalize to MP4/H.264/AAC/yuv420p/fast-start
             → enforce the established size limit
             → hash the FINALIZED asset
             → finalize atomically
             → create/reconcile the durable Reaction Library row

Idempotency: a repeat acquisition of the same canonical source is answered from
the library rows instead of downloading anything a second time.

Every external effect is injected, so the whole contract is exercised with fakes.
Signed URLs are never persisted: only the shared, redacted records are stored.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import media_normalize
import reaction_library
import approved_lane as lane

PLATFORM = "tiktok"
CONTENT_TYPE_MP4 = "video/mp4"

MAX_BYTES = media_normalize.MAX_BYTES                  # 100 MiB (shared limit)
MAX_DURATION_SECONDS = media_normalize.MAX_DURATION_SECONDS   # 300 s (shared limit)


class TikTokMediaError(RuntimeError):
    def __init__(self, code: str, message: str, *,
                 detail: dict[str, Any] | None = None) -> None:
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message
        #: Optional structured context (e.g. the finalized artifact when only the
        #: durable row failed), so a failure never loses evidence of real work.
        self.detail = dict(detail or {})


@dataclass
class TikTokMediaIO:
    """Every external effect the media path needs. Faked in tests."""

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


def _already_acquired(library_rows: list[dict[str, Any]] | None, *,
                      content_id: str, canonical_url: str) -> dict[str, Any] | None:
    for row in library_rows or []:
        if not isinstance(row, dict):
            continue
        if content_id and str(row.get("platform_content_id") or "") == content_id:
            return row
        if canonical_url and str(row.get("canonical_url") or "") == canonical_url:
            return row
    return None


def _durable_record(record: dict[str, Any]) -> dict[str, Any]:
    """Sanitize for persistence and drop empty uuid-typed columns.

    ``acquisition_job_id`` is a uuid column, and an empty string is NOT a valid
    uuid — PostgREST answers 400 ``22P02``. An acquisition with no job row must
    therefore OMIT the key rather than send ``""``.
    """
    safe = lane.safe_record(record)
    for key in ("acquisition_job_id",):
        if not str(safe.get(key) or "").strip():
            safe.pop(key, None)
    return safe


def _download(io: TikTokMediaIO, url: str, *, max_bytes: int) -> tuple[str, int, int, str]:
    try:
        tmp_path, written, status, content_type = io.download_one_video(
            url, io.tmp_dir, max_bytes=max_bytes, timeout=io.download_timeout)
    except Exception as e:  # noqa: BLE001 — classified, never a raw traceback
        raise TikTokMediaError(getattr(e, "code", "DOWNLOAD_FAILED"),
                               getattr(e, "message", str(e))[:200]) from e
    # Status/Content-Type/byte-signature gate: this is what refuses an HTML or
    # error payload masquerading as media BEFORE ffprobe ever sees it.
    try:
        io.classify_downloaded_file(tmp_path, http_status=status,
                                    content_type=content_type, size_bytes=written)
    except Exception as e:  # noqa: BLE001
        io.cleanup_temp(tmp_path)
        raise TikTokMediaError(getattr(e, "code", "MALFORMED_MP4"),
                               getattr(e, "message", str(e))[:200]) from e
    return tmp_path, written, status, content_type


def _probe(io: TikTokMediaIO, path: str, *, written: int,
           max_bytes: int, max_duration: float) -> dict[str, Any]:
    try:
        meta = io.parse_ffprobe_result(io.run_ffprobe(path),
                                       actual_size_bytes=written,
                                       max_bytes=max_bytes,
                                       max_duration=max_duration)
    except Exception as e:  # noqa: BLE001
        raise TikTokMediaError(getattr(e, "code", "MEDIA_INVALID"),
                               getattr(e, "message", str(e))[:200]) from e
    if not meta.get("video_codec"):
        raise TikTokMediaError("NO_VIDEO_STREAM", "downloaded media has no decodable video")
    return meta


def _mux_audio(io: TikTokMediaIO, video_path: str, audio_url: str, *,
               max_bytes: int) -> tuple[str, int]:
    if io.mux_builder is None:
        raise TikTokMediaError("AUDIO_MISSING",
                               "video carries no audio and no muxer is configured")
    audio_path, written, _status, _ct = _download(io, audio_url, max_bytes=max_bytes)
    out_path = str(Path(io.tmp_dir) / "muxed.mp4")
    try:
        io.run_ffmpeg(io.mux_builder(video_path, audio_path, out_path))
        if not Path(out_path).is_file():
            raise TikTokMediaError("NO_OUTPUT", "mux produced no file")
        return out_path, Path(out_path).stat().st_size
    finally:
        io.cleanup_temp(audio_path)


def acquire_tiktok_media(*, target: dict[str, Any], io: TikTokMediaIO,
                         library_rows: list[dict[str, Any]] | None = None,
                         persist_library: Callable[[dict], dict] | None = None,
                         max_bytes: int = MAX_BYTES,
                         max_duration: float = MAX_DURATION_SECONDS) -> dict[str, Any]:
    """Turn translated TikTok media into one durable, delivery-ready asset.

    ``target`` is the output of ``tiktok_actors.translate_item``.
    """
    content_id = str(target.get("content_id") or "")
    canonical_url = str(target.get("canonical_url") or "")
    if not content_id or not canonical_url:
        raise TikTokMediaError("BAD_TARGET", "target needs a content id and canonical URL")

    # --- idempotency: never re-acquire the same canonical source -----------
    existing = _already_acquired(library_rows, content_id=content_id,
                                 canonical_url=canonical_url)
    if existing is not None:
        return {"ok": True, "idempotent": True, "already_acquired": True,
                "library_row": existing, "content_id": content_id,
                "canonical_url": canonical_url}

    media_url = str(target.get("media_url") or "")
    if not media_url:
        raise TikTokMediaError("MISSING_MEDIA", "target carries no media URL")

    # Fail FAST if no library root is configured. Defaulting to the current working
    # directory is a footgun: a misconfigured root silently writes finalized assets
    # into whatever directory the process happens to be running in.
    if not str(io.library_dir or "").strip():
        raise TikTokMediaError(
            "NO_LIBRARY_DIR",
            "no Reaction Library directory is configured; refusing to finalize "
            "an asset into the current working directory")
    dst_dir = Path(io.library_dir)

    tmp_path = ""
    muxed_path = ""
    norm_path = ""
    try:
        tmp_path, written, http_status, content_type = _download(
            io, media_url, max_bytes=max_bytes)
        meta = _probe(io, tmp_path, written=written, max_bytes=max_bytes,
                      max_duration=max_duration)

        muxed = False
        if not meta.get("has_audio"):
            audio_url = str(target.get("audio_url") or "")
            if not audio_url:
                raise TikTokMediaError(
                    "AUDIO_MISSING",
                    "video stream carries no audio and the actor returned no audio URL")
            muxed_path, written = _mux_audio(io, tmp_path, audio_url, max_bytes=max_bytes)
            tmp_path = muxed_path
            muxed = True
            meta = _probe(io, tmp_path, written=written, max_bytes=max_bytes,
                          max_duration=max_duration)
            if not meta.get("has_audio"):
                raise TikTokMediaError("AUDIO_MISSING", "muxed MP4 still has no audio")

        # --- shared normalization contract (MP4/H.264/AAC/yuv420p/faststart) --
        dst_dir.mkdir(parents=True, exist_ok=True)
        staging = dst_dir / f".{content_id}.normalizing.mp4"
        norm_path = str(staging)
        try:
            report = media_normalize.normalize_and_finalize(
                src=tmp_path, dst=staging, probe=io.run_ffprobe(tmp_path),
                run_ffmpeg=io.run_ffmpeg, run_ffprobe=io.run_ffprobe_json,
                max_bytes=max_bytes, max_duration=max_duration)
        except media_normalize.NormalizeError as e:
            # The shared pipeline's stable code becomes this path's stable code:
            # a size/duration/compatibility refusal is never a raw traceback.
            raise TikTokMediaError(e.code, e.message) from e

        # --- hash the FINALIZED asset, then finalize atomically -------------
        digest = io.checksum_sha256(norm_path)
        if not digest or len(digest) != 64:
            raise TikTokMediaError("BAD_HASH", "checksum of the finalized asset failed")
        filename = reaction_library.filename_for(PLATFORM, content_id, digest)
        final_path = str(dst_dir / filename)
        io.finalize(norm_path, final_path)      # atomic move
        norm_path = ""
    finally:
        if tmp_path and tmp_path != muxed_path:
            io.cleanup_temp(tmp_path)
        if muxed_path:
            io.cleanup_temp(muxed_path)
        if norm_path and Path(norm_path).exists():
            io.cleanup_temp(norm_path)

    record = reaction_library.plan_library_entry(
        duplicate_fingerprint=canonical_url or content_id,
        platform=PLATFORM,
        platform_content_id=content_id,
        canonical_url=canonical_url,
        acquisition_job_id=str(target.get("job_id") or ""),
        filename=filename,
        digest=digest,
        size_bytes=report["size_bytes"],
        duration_seconds=report["duration_seconds"],
        content_type=CONTENT_TYPE_MP4,
        storage_url="",                       # local Mac; no hosted URL yet
    )
    safe = _durable_record(record)
    if persist_library:
        try:
            persisted = persist_library(safe) or safe
        except Exception as e:  # noqa: BLE001
            # The asset IS finalized on disk; only the durable row failed. The
            # error carries the artifact so the failure cannot lose the evidence.
            raise TikTokMediaError(
                getattr(e, "code", "LIBRARY_PERSIST_FAILED"),
                f"asset finalized but the Reaction Library row was not written "
                f"({type(e).__name__})",
                detail={"asset_filename": filename, "asset_hash": digest,
                        "local_path": final_path,
                        "size_bytes": report["size_bytes"],
                        "duration_seconds": report["duration_seconds"],
                        "record": safe}) from e
    else:
        persisted = safe

    return {
        "ok": True,
        "idempotent": False,
        "already_acquired": False,
        "content_id": content_id,
        "canonical_url": canonical_url,
        "asset_filename": filename,
        "asset_hash": digest,
        "local_path": final_path,
        "size_bytes": report["size_bytes"],
        "duration_seconds": report["duration_seconds"],
        "width": report["width"],
        "height": report["height"],
        "frame_rate": report["frame_rate"],
        "video_codec": report["video_codec"],
        "audio_codec": report["audio_codec"],
        "pix_fmt": report["pix_fmt"],
        "container": report["container"],
        "normalized": report.get("normalized", False),
        "remuxed": report.get("remuxed", False),
        "gaps": report.get("gaps", []),
        "muxed": muxed,
        "http_status": http_status,
        "downloaded_content_type": content_type,
        "library_row": persisted,
    }
