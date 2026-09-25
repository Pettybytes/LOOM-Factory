"""Bounded visual inspection — explicit source ranges, no acquisition fallback.

Why this exists: `fetch_media_and_signals` is bounded by *video duration*
(worst-quality download of up to `max_duration` seconds). That is not acceptable
for pre-approval sourcing inspection, which starts from an explicit short range
WITHOUT silently acquiring the source.

This adapter downloads ONLY the requested section through yt-dlp
`--download-sections "*start-end"` (ffmpeg-backed), measures the artifact, and
retains it until cleanup. The contiguous helper can assemble a longer requested
inspection interval from separately bounded, verified, reusable chunks; that
transfer limit is not a selected-moment duration cap. If a section-limited fetch
is not possible it REFUSES and records why
— it never silently falls back to a full-source download, because hiding
full-source transfer behind the word "bounded" is exactly the failure this
replaces.

DISCOVERY / INSPECTION != ACQUISITION. Nothing here authorizes acquiring the
source; that still happens only after Bryan approves.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

# Default 0-3s; escalation only when a decision is still ambiguous.
WINDOWS: tuple[float, ...] = (3.0, 8.0, 12.0)
MAX_WINDOW = 12.0

# Optional authenticated extraction. The browser/profile spec is read from the
# environment ONLY: cookies are never exported, printed, logged or committed, and
# only the bounded section is ever fetched. Unset => unauthenticated (unchanged).
COOKIES_FROM_BROWSER_ENV = "LOOM_YTDLP_COOKIES_FROM_BROWSER"


def _auth_args() -> list[str]:
    spec = (os.environ.get(COOKIES_FROM_BROWSER_ENV) or "").strip()
    return ["--cookies-from-browser", spec] if spec else []
_SIDECAR = {".json", ".vtt", ".part", ".ytdl", ".temp", ".description"}


@dataclass(frozen=True)
class BoundedPreview:
    """One bounded inspection artifact (or an explicit refusal)."""

    requested_start: float
    requested_end: float
    inspected_start: float = 0.0
    inspected_end: float = 0.0
    artifact_path: str = ""
    artifact_bytes: int = 0
    provider: str = ""
    method: str = ""
    fallback_used: bool = False
    reason: str = ""
    provenance: tuple[str, ...] = field(default_factory=tuple)

    @property
    def available(self) -> bool:
        return bool(self.artifact_path)

    def as_dict(self) -> dict[str, Any]:
        return {"requested_start": self.requested_start, "requested_end": self.requested_end,
                "inspected_start": self.inspected_start, "inspected_end": self.inspected_end,
                "artifact_bytes": self.artifact_bytes, "provider": self.provider,
                "method": self.method, "fallback_used": self.fallback_used,
                "reason": self.reason, "provenance": list(self.provenance),
                "available": self.available}


def _provider_of(url: str) -> str:
    u = (url or "").lower()
    for name in ("youtube", "youtu.be", "tiktok", "instagram", "twitch", "kick", "reddit"):
        if name in u:
            return "youtube" if name == "youtu.be" else name
    return "unknown"


def fetch_bounded_preview(url: str, *, end: float = 3.0, start: float = 0.0,
                          timeout: int = 180, keep: bool = False,
                          runner: Callable[..., Any] = subprocess.run) -> BoundedPreview:
    """Fetch ONLY ``[start, end]`` of a source, without acquiring the whole thing.

    Section-limited download is the chosen mechanism because it is a single
    existing-toolchain call (yt-dlp + ffmpeg) that transfers just the range. It
    is never allowed to degrade into a full-source download.
    """
    # The hard cap bounds the INSPECTED DURATION, never the absolute end: with a
    # dynamic start (e.g. a moment at 18:42) clamping the end to 12s would produce
    # the nonsensical request "start=1122, end=12".
    start = max(0.0, float(start))
    end = start + max(0.0, min(float(end) - start, MAX_WINDOW))
    provider = _provider_of(url)
    tmp = tempfile.mkdtemp(prefix="loom-inspect-")
    # The artifact is RETAINED for the caller (analysis happens after this
    # returns) unless `keep` copied it elsewhere. Deleting the temp dir here
    # made every returned preview point at a deleted file: available=True with
    # no bytes to analyze. Callers MUST call cleanup() once analysis finishes.
    retain_tmp = False
    try:
        cmd = [
            "yt-dlp", "--no-playlist", "--no-update",
            "-f", "worstvideo+worstaudio/worst",
            "--download-sections", f"*{start}-{end}",
            "--force-keyframes-at-cuts",
            *_auth_args(),
            "-o", f"{tmp}/%(id)s.%(ext)s", url,
        ]
        proc = runner(cmd, capture_output=True, text=True, timeout=timeout)
        media = [p for p in Path(tmp).glob("*")
                 if p.is_file() and p.suffix.lower() not in _SIDECAR]
        if not media:
            tail = [l for l in (getattr(proc, "stderr", "") or "").splitlines() if l.strip()]
            return BoundedPreview(start, end, provider=provider, method="unavailable",
                                  reason=(tail[-1][:180] if tail else
                                          f"yt-dlp exit {getattr(proc, 'returncode', '?')}"),
                                  provenance=("section_download_failed",))
        art = media[0]
        size = art.stat().st_size
        if keep:
            keepdir = Path(tempfile.mkdtemp(prefix="loom-inspect-keep-"))
            kept = keepdir / art.name
            shutil.copy2(art, kept)
            return BoundedPreview(start, end, start, end, str(kept), size, provider,
                                  "section_download", False, "",
                                  (f"bounded_section_0_{int(end)}s", "artifact_retained"))
        retain_tmp = True
        return BoundedPreview(start, end, start, end, str(art), size, provider,
                              "section_download", False, "",
                              (f"bounded_section_0_{int(end)}s",))
    except subprocess.TimeoutExpired:
        return BoundedPreview(start, end, provider=provider, method="timeout",
                              reason=f"section download exceeded {timeout}s",
                              provenance=("section_download_timeout",))
    except FileNotFoundError as exc:
        return BoundedPreview(start, end, provider=provider, method="unavailable",
                              reason=f"toolchain missing: {type(exc).__name__}",
                              provenance=("toolchain_missing",))
    finally:
        if not retain_tmp:
            shutil.rmtree(tmp, ignore_errors=True)


def cleanup(preview: Any) -> None:
    """Delete a retained artifact (temporary inspection data, not an asset).

    Accepts a BoundedPreview or a bare artifact path. Only removes directories
    this module created (loom-inspect-*): a fabricated or foreign path must
    never cause a parent-directory delete."""
    path = preview if isinstance(preview, str) else getattr(preview, "artifact_path", "")
    if not path:
        return
    parent = Path(path).parent
    if not parent.name.startswith("loom-inspect"):
        return
    shutil.rmtree(str(parent), ignore_errors=True)


@dataclass
class InspectionMetrics:
    """Lightweight efficiency tracking for the sourcing funnel."""

    preview_requests: int = 0
    requested_seconds: list[float] = field(default_factory=list)
    inspected_seconds: float = 0.0
    bytes_downloaded: int = 0
    artifact_bytes: int = 0
    duration_ms: int = 0
    provider: str = ""
    escalated: bool = False
    windows_tried: list[float] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"preview_requests": self.preview_requests,
                "requested_seconds": list(self.requested_seconds),
                "inspected_seconds": self.inspected_seconds,
                "bytes_downloaded": self.bytes_downloaded,
                "artifact_bytes": self.artifact_bytes,
                "duration_ms": self.duration_ms, "provider": self.provider,
                "escalated": self.escalated, "windows_tried": list(self.windows_tried)}


class _InspectionCoverageError(ValueError):
    """Internal, value-safe failure code; never includes tool output or URLs."""


def fetch_contiguous_inspection_preview(url: str, *, start: float, end: float,
        fetcher=None, runner=subprocess.run, cache_dir=None,
        allow_terminal_partial: bool = False) -> BoundedPreview:
    """Build contiguous video coverage from individually bounded cached transfers.

    Each transfer is <=12 seconds, not a limit on the selected moment. Cache
    files are published atomically under a per-key interprocess lock and re-probed
    on reuse. Stream timing must cover the requested interval; ordinary VFR
    cadence is measured, not confused with a missing transfer. Material gaps
    fail closed; endpoints allow only one encoded frame of quantization.
    Explicit ``allow_terminal_partial`` permits ONLY the last chunk to end early:
    inspected_end then reports measured video coverage, never the requested end.
    No full-source fallback is permitted. Returned artifacts require cleanup().
    """
    import fcntl
    import hashlib
    import json
    import math
    import statistics

    fetcher = fetcher or fetch_bounded_preview
    try:
        start, end = float(start), float(end)
        chunk_size = min(float(MAX_WINDOW), 12.0)
    except (TypeError, ValueError, OverflowError):
        return BoundedPreview(0.0, 0.0, method="unavailable", reason="invalid_coverage")
    if not (math.isfinite(start) and math.isfinite(end) and 0 <= start < end and
            math.isfinite(chunk_size) and chunk_size > 0):
        return BoundedPreview(start, end, method="unavailable", reason="invalid_coverage")

    root = Path(cache_dir or Path(tempfile.gettempdir()) / f"loom-inspection-chunks-{os.getuid()}")
    outdir = None
    chunks, chunk_durations, received, cache_hits = [], [], 0, 0
    active_range, phase = (start, end), "cache_setup"
    timing_stats = []

    def verify_video(path, wanted, *, source_section=False, terminal_partial=False):
        if not path.is_file() or path.is_symlink() or not path.stat().st_size:
            raise _InspectionCoverageError("artifact_missing_or_empty")
        proc = runner([
            "ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
            "format=duration:stream=codec_type:packet=pts_time,duration_time",
            "-of", "json", str(path),
        ], capture_output=True, text=True, timeout=30)
        if proc.returncode:
            raise _InspectionCoverageError("video_probe_failed")
        try:
            payload = json.loads(proc.stdout)
            streams = payload["streams"]
            packets = payload["packets"]
            format_duration = float(payload["format"]["duration"])
            if not streams or streams[0].get("codec_type") != "video" or not packets:
                raise ValueError
            intervals = sorted((float(p["pts_time"]), float(p["duration_time"])) for p in packets)
            if not math.isfinite(format_duration) or any(
                    not math.isfinite(pts) or not math.isfinite(length) or length <= 0
                    for pts, length in intervals):
                raise ValueError
        except (KeyError, TypeError, ValueError, OverflowError):
            raise _InspectionCoverageError("video_coverage_unproven") from None
        cadence = statistics.median(length for _, length in intervals)
        # VFR playback holds a displayed frame until the next presentation time.
        # Small cadence irregularities do not establish an incomplete transfer.
        # Do not interpolate any new imagery; reject materially missing spans.
        gap_limit = min(.25, max(3 * cadence, .1))
        largest_gap = 0.0
        reached = intervals[0][0]
        for pts, length in intervals:
            largest_gap = max(largest_gap, pts - reached)
            if pts > reached + gap_limit + .003:
                raise _InspectionCoverageError("video_timeline_gap")
            reached = max(reached, pts + length)
        span = reached - intervals[0][0]
        # Container/audio padding does not prove visual coverage. Require both
        # packet span and container duration; allow one frame at the endpoints.
        tolerance = cadence + .003
        # A decoded source section can overhang a cut by a frame on each side.
        # Trim that real overhang locally, but never extend a short section.
        excess = tolerance * (2 if source_section else 1)
        actual = min(wanted, span) if terminal_partial else wanted
        if ((not terminal_partial and span < wanted - tolerance) or span > wanted + excess or
                format_duration < actual - tolerance or format_duration > wanted + excess):
            raise _InspectionCoverageError("video_duration_mismatch")
        return {"largest_gap": largest_gap, "cadence": cadence,
                "span": span, "actual_duration": actual}

    try:
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        outdir = Path(tempfile.mkdtemp(prefix="loom-inspect-contiguous-"))
        cursor = start
        while cursor < end:
            stop = min(cursor + chunk_size, end)
            terminal_partial = allow_terminal_partial is True and stop == end
            actual_duration = stop - cursor
            active_range, phase = (cursor, stop), "cache_probe"
            if stop <= cursor:
                raise _InspectionCoverageError("invalid_chunk_progress")
            # Versioned, exact coordinates avoid rounded-window cache collisions
            # and prevent reuse of files published by the older non-atomic code.
            cache_identity = ["packet-coverage-v2", url, cursor.hex(), stop.hex()]
            if terminal_partial:
                # A permissive terminal cache must never satisfy a strict caller.
                cache_identity.append("allow-terminal-partial")
            key = hashlib.sha256(json.dumps(
                cache_identity,
                separators=(",", ":"),
            ).encode()).hexdigest()
            target = root / (key + ".mp4")
            # Do not remove lock files: unlinking one permits two lock inodes for
            # the same key. flock releases automatically if a worker dies.
            with (root / (key + ".lock")).open("a+b") as lock:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
                valid = False
                if target.exists():
                    try:
                        cached_stats = verify_video(target, stop - cursor, terminal_partial=terminal_partial)
                        timing_stats.append(cached_stats)
                        actual_duration = cached_stats["actual_duration"]
                        valid = True
                    except _InspectionCoverageError:
                        pass
                if valid:
                    cache_hits += 1
                else:
                    phase = "section_fetch"
                    preview = fetcher(url, start=cursor, end=stop)
                    try:
                        if not preview.available:
                            raise _InspectionCoverageError("section_unavailable")
                        if preview.fallback_used:
                            raise _InspectionCoverageError("full_source_fallback_refused")
                        try:
                            bounds = [float(preview.requested_start), float(preview.requested_end),
                                      float(preview.inspected_start), float(preview.inspected_end)]
                        except (TypeError, ValueError, OverflowError):
                            raise _InspectionCoverageError("invalid_preview_bounds") from None
                        if (any(not math.isfinite(value) for value in bounds) or any(
                                abs(value - expected) > .001
                                for value, expected in zip(bounds[:3], [cursor, stop, cursor])) or
                                (not terminal_partial and abs(bounds[3] - stop) > .001) or
                                (terminal_partial and not cursor < bounds[3] <= stop + .001)):
                            raise _InspectionCoverageError("coverage_gap_or_offset")
                        source = Path(preview.artifact_path)
                        phase = "source_probe"
                        source_stats = verify_video(source, stop - cursor, source_section=True,
                                                    terminal_partial=terminal_partial)
                        timing_stats.append(source_stats)
                        actual_duration = source_stats["actual_duration"]
                        if terminal_partial:
                            actual_duration = min(actual_duration, bounds[3] - cursor)
                        # Same-filesystem staging is invisible to other readers.
                        # Never let ffmpeg write a partially published cache file.
                        with tempfile.TemporaryDirectory(prefix=f".{key}-", dir=root) as staging:
                            staged = Path(staging) / "normalized.mp4"
                            phase = "chunk_normalization"
                            proc = runner([
                                "ffmpeg", "-v", "error", "-nostdin", "-y", "-i", str(source),
                                "-map", "0:v:0", "-map", "0:a:0?", "-t", str(actual_duration),
                                "-vf", "setpts=PTS-STARTPTS", "-af", "asetpts=PTS-STARTPTS",
                                "-fps_mode", "passthrough",
                                "-c:v", "libx264", "-preset", "ultrafast", "-crf", "30",
                                "-pix_fmt", "yuv420p", "-c:a", "aac", str(staged),
                            ], capture_output=True, text=True, timeout=120)
                            if proc.returncode:
                                raise _InspectionCoverageError("chunk_normalization_failed")
                            phase = "normalized_probe"
                            normalized_stats = verify_video(staged, actual_duration)
                            timing_stats.append(normalized_stats)
                            if terminal_partial:
                                actual_duration = min(actual_duration, normalized_stats["span"])
                            # Flush before publishing so a returned cache entry is
                            # complete even if a later worker/process exits.
                            with staged.open("rb") as handle:
                                os.fsync(handle.fileno())
                            os.replace(staged, target)
                        received += source.stat().st_size
                    finally:
                        cleanup(preview)
                # Pin the validated inode for this assembly. Later cache repair
                # cannot change the bytes behind this request's manifest.
                local = outdir / f"chunk-{len(chunks):05d}.mp4"
                phase = "pin_cache_artifact"
                try:
                    os.link(target, local)
                except OSError:
                    shutil.copyfile(target, local)
                chunks.append(local)
                chunk_durations.append(actual_duration)
            cursor = stop
        manifest = outdir / "chunks.txt"
        active_range, phase = (start, end), "assembly"
        # Relative generated names also avoid path quoting / concat injection.
        manifest.write_text("".join(
            f"file '{p.name}'\nduration {length:.12f}\n"
            for p, length in zip(chunks, chunk_durations)))
        artifact = outdir / "inspection.mp4"
        actual_coverage = sum(chunk_durations)
        proc = runner([
            "ffmpeg", "-v", "error", "-nostdin", "-y", "-f", "concat", "-safe", "1",
            "-i", str(manifest), "-c", "copy", "-t", str(actual_coverage), str(artifact),
        ], capture_output=True, text=True, timeout=120)
        if proc.returncode:
            raise _InspectionCoverageError("assembly_failed")
        phase = "assembly_probe"
        assembled_stats = verify_video(artifact, actual_coverage)
        timing_stats.append(assembled_stats)
        if allow_terminal_partial is True:
            actual_coverage = min(actual_coverage, assembled_stats["span"])
        actual_end = start + actual_coverage
        coverage_note = (f"terminal_partial:requested_end={end:.6f}:actual_end={actual_end:.6f}"
                         if actual_end < end - .003 else "coverage:requested_interval")
        return BoundedPreview(
            start, end, start, actual_end, str(artifact), artifact.stat().st_size,
            _provider_of(url), "contiguous_sections", False, "",
            ("verified_contiguous_coverage", coverage_note, f"chunks:{len(chunks)}",
             f"cache_hits:{cache_hits}", f"fetched_artifact_bytes:{received}",
             f"largest_video_timing_gap:{max(x['largest_gap'] for x in timing_stats):.6f}s",
             f"max_native_frame_interval:{max(x['cadence'] for x in timing_stats):.6f}s"),
        )
    except _InspectionCoverageError as exc:
        reason = str(exc)
    except subprocess.TimeoutExpired:
        reason = "inspection_tool_timeout"
    except FileNotFoundError:
        reason = "toolchain_or_artifact_missing"
    except OSError:
        reason = "inspection_io_failed"
    except Exception as exc:
        reason = type(exc).__name__
    if outdir is not None:
        shutil.rmtree(outdir, ignore_errors=True)
    return BoundedPreview(
        start, end, provider=_provider_of(url), method="unavailable",
        reason=(f"contiguous_inspection_failed:{reason}:phase={phase}:"
                f"chunk={active_range[0]:.6f}-{active_range[1]:.6f}"),
        provenance=("coverage_not_proven",),
    )
