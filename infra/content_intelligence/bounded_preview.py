"""TRUE bounded visual inspection — only the opening seconds are ever fetched.

Why this exists: `fetch_media_and_signals` is bounded by *video duration*
(worst-quality download of up to `max_duration` seconds). That is not acceptable
for pre-approval sourcing inspection, whose entire purpose is to decide from the
opening seconds WITHOUT acquiring the source.

This adapter downloads ONLY the requested section through yt-dlp
`--download-sections "*start-end"` (ffmpeg-backed), measures the artifact, and
deletes it. If a section-limited fetch is not possible it REFUSES and records why
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
    end = min(float(end), MAX_WINDOW)
    provider = _provider_of(url)
    tmp = tempfile.mkdtemp(prefix="loom-inspect-")
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
        if not keep:
            shutil.rmtree(tmp, ignore_errors=True)


def cleanup(preview: BoundedPreview) -> None:
    """Delete a retained artifact (temporary inspection data, not an asset)."""
    if preview.artifact_path:
        shutil.rmtree(str(Path(preview.artifact_path).parent), ignore_errors=True)


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
