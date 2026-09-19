"""Local YouTube acquisition adapter (yt-dlp). No paid provider, no Apify.

Hard bounds, enforced before, during, and after the download:

    duration <= 300 s   size <= 100 MiB   results == 1   attempts == 1

Hygiene rules baked into every command: ``--no-playlist`` (one exact item), never
``--cookies``/``--cookies-from-browser``/``--username``/``--password``/``--netrc``
and never an ``Authorization`` header. Separate video/audio streams are merged
locally with ffmpeg; the final file is moved into the library atomically only
after full validation, and partial files are removed on any failure.

The command builders and the validators are pure, so the contract is testable
without network, yt-dlp, or ffmpeg.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path
from typing import Any, Callable

import media_normalize as mn
import source_window as swc

PROVIDER_LOCAL = "local"
ADAPTER_LOCAL_YTDLP = "local:yt-dlp"

MAX_DURATION_SECONDS = 300
# Ranged mode imposes NO arbitrary source ceiling: a Twitch/Kick VOD may be many
# hours and only the AUTHORIZED EXCERPT is bounded (shared clip limit).
MAX_SOURCE_DURATION_SECONDS = None
MAX_BYTES = 100 * 1024 * 1024
MAX_RESULTS = 1
MAX_ATTEMPTS = 1

MIB = 1024 * 1024

# Bounded, compatible selection: best video+audio, falling back to best combined.
# Prefer a delivery-ready stream set; normalization is the fallback.
FORMAT_SELECTOR = ("bv*[vcodec^=avc1]+ba[acodec^=mp4a]"
                   "/b[ext=mp4]"
                   "/bv*+ba/b")

# Flags that must NEVER appear in a generated command.
FORBIDDEN_FLAGS = (
    "--cookies", "--cookies-from-browser", "--username", "--password",
    "--video-password", "--netrc", "--ap-mso", "--ap-username", "--ap-password",
    "--add-header", "--referer", "--user-agent",
)

ALLOWED_VIDEO_CODECS = frozenset({"h264", "avc1", "vp9", "vp09", "av01", "av1", "hevc", "h265"})
ALLOWED_AUDIO_CODECS = frozenset({"aac", "mp4a", "opus", "vorbis", "mp3", "ac3", "eac3"})


class YtDlpError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message


# ---------------------------------------------------------------------------
# Command builders (pure)
# ---------------------------------------------------------------------------
def build_probe_command(url: str, *, ytdlp: str = "yt-dlp") -> list[str]:
    """Metadata-only probe: no download, one item, no cookies, no auth."""
    cmd = [ytdlp, "--dump-single-json", "--no-playlist", "--playlist-items", "1",
           "--skip-download", "--no-warnings", "--no-progress", "--no-color",
           "--socket-timeout", "20", "--retries", "0", "--", url]
    return _assert_hygiene(cmd)


def build_download_command(url: str, *, out_template: str, ytdlp: str = "yt-dlp",
                           max_bytes: int = MAX_BYTES,
                           section_arg: str | None = None) -> list[str]:
    """One bounded item with local ffmpeg merge; never enumerates the playlist."""
    cmd = [ytdlp,
           "--no-playlist",
           "--playlist-items", "1",
           "--format", FORMAT_SELECTOR,
           "--merge-output-format", "mp4",
           "--no-warnings", "--no-progress", "--no-color", "--newline",
           "--socket-timeout", "20",
           "--retries", "0",
           "--max-filesize", str(max_bytes),
           "--no-part",
           "--output", out_template,
           "--print", "after_move:filepath",
           "--", url]
    if section_arg:
        # Bounded segment: ffmpeg cuts the exact requested window and re-encodes at
        # the cut points so the delivered clip starts/ends where it was approved.
        cmd[cmd.index("--"):cmd.index("--")] = ["--download-sections", section_arg,
                                                "--force-keyframes-at-cuts"]
    return _assert_hygiene(cmd)


def _assert_hygiene(cmd: list[str]) -> list[str]:
    for flag in FORBIDDEN_FLAGS:
        if flag in cmd:
            raise YtDlpError("COMMAND_HYGIENE", f"forbidden flag {flag!r} in command")
    if "--no-playlist" not in cmd:
        raise YtDlpError("COMMAND_HYGIENE", "--no-playlist is required")
    if cmd.count("--playlist-items") and "1" not in cmd:
        raise YtDlpError("COMMAND_HYGIENE", "playlist-items must be bounded to 1")
    return cmd


# ---------------------------------------------------------------------------
# Validation (pure)
# ---------------------------------------------------------------------------
def _infer_platform_from_url(legacy_url: str) -> str:
    """Which registered adapter claims this URL?

    Used only when a caller omitted the platform (older call sites), so the
    filename prefix stays truthful instead of defaulting to a platform name.
    """
    try:
        import source_adapters as _sa
    except ImportError:
        return ""
    for name in _sa.registered_platforms():
        adapter = _sa.adapter_for(name)
        if adapter is None:
            continue
        try:
            adapter.canonicalize(legacy_url)
            return name
        except Exception:  # noqa: BLE001 — not this adapter's URL
            continue
    return ""


def _identity_ok(expected_id: str, meta: dict[str, Any], platform: str = "") -> bool:
    """Platform-neutral identity check delegated to the adapter registry.

    The platform is passed EXPLICITLY wherever the caller knows it: the media-stage
    check only has an id, and inferring from metadata there silently fell back to a
    literal comparison (which is how `v2852456270` and a clip's numeric id drifted).
    """
    try:
        import source_adapters as _sa
    except ImportError:
        import os as _os
        import sys as _sys
        _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
        import source_adapters as _sa
    resolved = (platform or "").lower() or _sa.infer_platform(meta)
    return _sa.reconcile_identity(resolved, expected_id, meta)


def validate_metadata(meta: dict[str, Any], *, expected_id: str,
                      allow_long_source: bool = False) -> dict[str, Any]:
    """Refuse BEFORE downloading when trustworthy metadata already violates a bound."""
    if not isinstance(meta, dict):
        raise YtDlpError("BAD_METADATA", "probe returned no metadata")

    entries = meta.get("entries")
    if isinstance(entries, list):
        counts = [e for e in entries if isinstance(e, dict)]
        if not counts and meta.get("id"):
            # Some responses carry the video fields at top level with an EMPTY
            # `entries` array; the object itself is the single item.
            meta = {k: v for k, v in meta.items() if k != "entries"}
        elif not counts:
            # An empty result set (no id, nothing extracted) is a transient
            # extractor response — retryable, never a policy refusal.
            raise YtDlpError("NO_RESULTS",
                             "the extractor returned no item for this URL")
        elif len(counts) != 1:
            raise YtDlpError("MULTIPLE_RESULTS",
                             f"probe returned {len(counts)} items; exactly 1 is required")
        else:
            meta = counts[0]

    if meta.get("_type") in ("playlist", "multi_video"):
        # yt-dlp may wrap the ONE requested video in a single-entry playlist
        # envelope. That is still exactly one item; only a genuine multi-item
        # result is refused (the pilot acquires one video, never a list).
        wrapped = [e for e in (meta.get("entries") or []) if isinstance(e, dict)]
        if len(wrapped) == 1:
            meta = wrapped[0]
        else:
            raise YtDlpError("PLAYLIST_REJECTED",
                             f"playlist/multi-video result rejected "
                             f"({len(wrapped)} entries)")

    video_id = str(meta.get("id") or "")
    if not video_id:
        raise YtDlpError("MISSING_VIDEO_ID", "probe returned no video id")
    if not _identity_ok(expected_id, meta):
        raise YtDlpError("CONTENT_ID_DRIFT",
                         f"downloaded id {video_id!r} != expected {expected_id!r}")

    live_status = str(meta.get("live_status") or "")
    if meta.get("is_live") or live_status in ("is_live", "is_upcoming"):
        raise YtDlpError("LIVE_REJECTED", "live/upcoming stream rejected")
    # A FINISHED broadcast (was_live / post_live) is an ordinary VOD and is
    # acquirable — its live origin is recorded truthfully instead of refused.
    live_origin = live_status in ("was_live", "post_live")

    duration = meta.get("duration")
    if duration is None:
        raise YtDlpError("UNKNOWN_DURATION", "duration unknown; refusing to guess")
    try:
        duration_f = float(duration)
    except (TypeError, ValueError):
        raise YtDlpError("BAD_DURATION", f"unusable duration {duration!r}")
    source_limit = MAX_SOURCE_DURATION_SECONDS if allow_long_source else MAX_DURATION_SECONDS
    if source_limit is not None and duration_f > source_limit:
        # Ranged mode (source_limit None) is bounded by the WINDOW and the finalized
        # asset checks instead of an arbitrary source ceiling.
        raise YtDlpError("TOO_LONG",
                         f"{duration_f:.1f}s exceeds the {source_limit}s "
                         f"{'source' if allow_long_source else 'clip'} limit")

    size = meta.get("filesize") or meta.get("filesize_approx")
    if size and int(size) > MAX_BYTES:
        raise YtDlpError("TOO_LARGE",
                         f"reported size {int(size)} exceeds the {MAX_BYTES}-byte limit")

    return {"video_id": video_id, "duration_seconds": duration_f,
            "title": str(meta.get("title") or ""),
            "reported_bytes": int(size) if size else None}


def validate_media(probe: dict[str, Any], *, path: str | Path, expected_id: str,
                   video_id_from_meta: str = "", platform: str = "" ) -> dict[str, Any]:
    """Post-download gate. Everything here must hold before finalization."""
    p = Path(path)
    if not p.is_file():
        raise YtDlpError("MISSING_FILE", "downloaded file does not exist")
    size = p.stat().st_size
    if size <= 0:
        raise YtDlpError("ZERO_BYTES", "downloaded file is empty")
    if size > MAX_BYTES:
        raise YtDlpError("TOO_LARGE", f"{size} bytes exceeds the {MAX_BYTES}-byte limit")

    if video_id_from_meta and not _identity_ok(expected_id, {"id": video_id_from_meta},
                                               platform):
        raise YtDlpError("CONTENT_ID_DRIFT",
                         f"media id {video_id_from_meta!r} != expected {expected_id!r}")

    if not isinstance(probe, dict) or probe.get("error"):
        raise YtDlpError("FFPROBE_FAILED", "ffprobe did not return usable stream data")

    streams = probe.get("streams") or []
    if not isinstance(streams, list) or not streams:
        raise YtDlpError("NO_STREAMS", "no media streams found")
    video = [s for s in streams if s.get("codec_type") == "video"]
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    if len(video) != 1:
        raise YtDlpError("VIDEO_STREAM_COUNT", f"expected exactly 1 video stream, got {len(video)}")
    if not audio:
        raise YtDlpError("NO_AUDIO", "no audio stream present")

    vcodec = str(video[0].get("codec_name") or "").lower()
    acodec = str(audio[0].get("codec_name") or "").lower()
    if vcodec not in ALLOWED_VIDEO_CODECS:
        raise YtDlpError("UNSUPPORTED_VIDEO_CODEC", f"video codec {vcodec!r} not supported")
    if acodec not in ALLOWED_AUDIO_CODECS:
        raise YtDlpError("UNSUPPORTED_AUDIO_CODEC", f"audio codec {acodec!r} not supported")

    fmt = probe.get("format") or {}
    duration = fmt.get("duration")
    try:
        duration_f = float(duration) if duration is not None else None
    except (TypeError, ValueError):
        duration_f = None
    if duration_f is None or duration_f <= 0:
        raise YtDlpError("BAD_DURATION", "container duration missing or zero")
    if duration_f > MAX_DURATION_SECONDS + 1.0:
        raise YtDlpError("TOO_LONG",
                         f"{duration_f:.1f}s exceeds the {MAX_DURATION_SECONDS}s limit")

    def _int(value):
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    return {
        "size_bytes": size,
        "duration_seconds": duration_f,
        "video_codec": vcodec,
        "audio_codec": acodec,
        "width": _int(video[0].get("width")),
        "height": _int(video[0].get("height")),
        "sha256": sha256_file(p),
        "container": str(fmt.get("format_name") or ""),
    }


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Orchestration (injected runners -> testable without yt-dlp/ffmpeg)
# ---------------------------------------------------------------------------
def acquire_local(*, url: str, expected_id: str, tmp_dir: Path, library_dir: Path,
                  run_probe: Callable[[list[str]], dict],
                  run_download: Callable[[list[str]], str],
                  run_ffprobe: Callable[[str], dict],
                  run_ffmpeg: Callable[[list[str]], Any] | None = None,
                  window: dict[str, Any] | None = None,
                  cleanup: Callable[[Path], None] | None = None,
                  ytdlp: str = "yt-dlp", platform: str = "" ) -> dict[str, Any]:
    """One bounded attempt; returns the finalized library record or raises."""
    tmp_dir = Path(tmp_dir)
    library_dir = Path(library_dir)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    library_dir.mkdir(parents=True, exist_ok=True)

    meta = run_probe(build_probe_command(url, ytdlp=ytdlp))
    checked = validate_metadata(meta, expected_id=expected_id,
                                 allow_long_source=bool(window))      # pre-download gate

    stem = f"{expected_id}"
    out_template = str(tmp_dir / f"{stem}.%(ext)s")
    produced = run_download(build_download_command(
        url, out_template=out_template, ytdlp=ytdlp,
        section_arg=swc.ytdlp_section_arg(window) if window else None))
    tmp_path = Path(str(produced).strip().splitlines()[-1]) if produced else None
    if tmp_path is None or not tmp_path.is_file():
        candidates = sorted(p for p in tmp_dir.glob(f"{stem}.*") if p.is_file())
        if not candidates:
            raise YtDlpError("NO_OUTPUT", "yt-dlp produced no file")
        tmp_path = candidates[0]

    try:
        probe = run_ffprobe(str(tmp_path))
        media = validate_media(probe, path=tmp_path, expected_id=expected_id,
                                platform=platform,
                               video_id_from_meta=checked["video_id"])
        # Shared delivery contract: MP4 / H.264 / AAC / yuv420p / fast-start.
        # Compatible media is moved untouched; anything else is normalized once.
        staged = tmp_dir / f"{stem}.compat.mp4"
        delivered = mn.normalize_and_finalize(
            src=tmp_path, dst=staged, probe=probe,
            run_ffmpeg=run_ffmpeg or mn.run_ffmpeg_command,
            run_ffprobe=run_ffprobe)
        digest = sha256_file(staged)              # hash what we actually ship
        if window:
            # The delivered clip must match the window that was actually approved.
            swc.validate_resulting_duration(approved=window,
                                            measured_ms=int(round(delivered["duration_seconds"] * 1000)))
        # The filename prefix is the REAL platform, for whole-item and ranged
        # alike. Never default it: a wrong prefix mislabels the asset forever.
        stem_platform = str((window or {}).get("platform") or platform or "").lower()
        if not stem_platform:
            stem_platform = _infer_platform_from_url(url)
        if not stem_platform:
            raise YtDlpError("PLATFORM_MISSING",
                             "no platform available for the asset filename")
        filename = swc.filename_stem(stem_platform, expected_id, window, digest) + ".mp4"
        final_path = library_dir / filename
        os.replace(staged, final_path)            # atomic finalize

        # No debris on success: the download (and any staging leftovers) go away
        # once the delivered asset is in the library. The media pipeline is the
        # only writer here, so this cannot touch another attempt's files.
        for leftover in [tmp_path, *tmp_dir.glob(f"{stem}.*")]:
            try:
                if Path(leftover).is_file():
                    Path(leftover).unlink()
            except OSError:
                pass
    except Exception:
        # never leave partial debris behind
        try:
            if cleanup:
                cleanup(tmp_path)
            elif tmp_path and Path(tmp_path).exists():
                Path(tmp_path).unlink()
        finally:
            for leftover in tmp_dir.glob(f"{stem}.*"):
                try:
                    leftover.unlink()
                except OSError:
                    pass
        raise

    return {
        "asset_filename": filename,
        "path": str(final_path),
        "video_id": expected_id,
        "live_origin": bool((checked or {}).get("live_origin")),
        "canonical_url": url,
        "duration_seconds": delivered["duration_seconds"],
        "size_bytes": delivered["size_bytes"],
        "video_codec": delivered["video_codec"],
        "audio_codec": delivered["audio_codec"],
        "pix_fmt": delivered["pix_fmt"],
        "normalized": delivered["normalized"],
        "source_video_codec": media["video_codec"],
        "source_audio_codec": media["audio_codec"],
        "width": delivered.get("width") or media["width"],
        "height": delivered.get("height") or media["height"],
        "sha256": digest,
        "container": delivered["container"],
        "provider": PROVIDER_LOCAL,
        "adapter": ADAPTER_LOCAL_YTDLP,
        "provider_cost_micro": 0,
        "attempts": 1,
        "expected_id": expected_id,
        "title": checked.get("title", ""),
    }


def filename_for(video_id: str, sha256: str) -> str:
    """Deterministic: youtube__<video_id>__<short_hash>.mp4"""
    return f"youtube__{video_id}__{sha256[:12]}.mp4"


def run_ytdlp_command(cmd: list[str], *, timeout: float = 600.0) -> str:
    """Real runner (used only in the live proof, never in tests)."""
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip().splitlines()[-1:] or [""]
        raise YtDlpError("YTDLP_FAILED", f"yt-dlp exited {proc.returncode}: {tail[0][:200]}")
    return proc.stdout


def run_ffprobe_json(path: str) -> dict:
    cmd = ["ffprobe", "-v", "error", "-show_streams", "-show_format",
           "-of", "json", path]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        raise YtDlpError("FFPROBE_FAILED", (proc.stderr or "ffprobe failed")[:200])
    import json as _json
    return _json.loads(proc.stdout or "{}")
