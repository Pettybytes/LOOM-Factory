"""Manual Intelligence Intake V1.2 — operator-supplied media into existing LOOM contracts.

Thin ingress, maximum reuse. An operator supplies media that was ALREADY acquired
outside LOOM (commonly via Cobalt) plus the original source URL, optionally a
tenant and optionally a clip window. This module:

  * validates/canonicalizes the original URL with the EXISTING V1 intake contract,
  * probes the supplied bytes locally (ffprobe only — no network, no provider),
  * stores the durable copy in GHL media storage (filesafe CDN) through the
    PRODUCTION helper ``adapter.ghl_upload_file``,
  * persists one ``loom_manual_submissions`` row with ``origin='manual_media'``
    and a durable ``media_reference``.

It does NOT analyze, score, or approve anything, and it never re-acquires media
from a paid provider. Content Intelligence is a separate seam
(``manual_intake_intelligence``) that consumes the SUPPLIED bytes.

Local media is transient working material: it exists only while the submission is
ingested and analyzed, then it is removed. GHL holds the durable copy and the
submission row holds the durable pointer, so no permanent source library is kept
on the Mac.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import urllib.request
from pathlib import Path
from typing import Any

import manual_intake

# Local inspection artifact cap (seconds). CI windows are bounded anyway; this
# only stops a malformed request from clipping a whole long-form source.
MAX_LOCAL_CLIP_SECONDS = 60.0

BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

_TS = re.compile(r"^(?:(\d+):)?(\d{1,2}):(\d{2}(?:\.\d+)?)$|^(\d+(?:\.\d+)?)$")
_URL = re.compile(r"https?://\S+")


def sha256_file(path: str | os.PathLike[str]) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_timestamp(raw: str | None) -> float | None:
    """Accept 'SS', 'SS.ss', 'MM:SS', 'MM:SS.ss', 'HH:MM:SS'. None when blank."""
    text = (raw or "").strip()
    if not text:
        return None
    m = _TS.match(text)
    if not m:
        raise ValueError(f"unrecognized timestamp {raw!r} (use SS, MM:SS, or HH:MM:SS)")
    if m.group(4) is not None:            # bare seconds
        return float(m.group(4))
    hours = float(m.group(1) or 0)
    return hours * 3600 + float(m.group(2)) * 60 + float(m.group(3))


def parse_intake_message(content: str) -> dict[str, Any]:
    """Parse an operator message: a URL plus optional ``start=``/``end=``/``tenant=``.

    Accepted forms: bare URL anywhere in the text, or explicit ``url:``.
    Timestamps: ``start=1:23 end=2:45`` (also ``start=83``).
    """
    text = content or ""
    out: dict[str, Any] = {"url": "", "start_s": None, "end_s": None, "tenant": ""}
    for token in ("url", "source"):
        m = re.search(rf"^{token}\s*[:=]\s*(\S+)", text, re.IGNORECASE | re.MULTILINE)
        if m:
            out["url"] = m.group(1)
            break
    if not out["url"]:
        m = _URL.search(text)
        if m:
            out["url"] = m.group(0).rstrip(".,)")
    start_raw = re.search(r"\bstart\s*[:=]\s*([0-9:.]+)", text, re.IGNORECASE)
    end_raw = re.search(r"\bend\s*[:=]\s*([0-9:.]+)", text, re.IGNORECASE)
    if start_raw:
        out["start_s"] = parse_timestamp(start_raw.group(1))
    if end_raw:
        out["end_s"] = parse_timestamp(end_raw.group(1))
    tenant_raw = re.search(r"\btenant\s*[:=]\s*([A-Za-z0-9_.-]+)", text, re.IGNORECASE)
    if tenant_raw:
        out["tenant"] = tenant_raw.group(1).strip()
    return out


def probe_media(path: str | os.PathLike[str]) -> dict[str, Any]:
    """Local ffprobe only: duration/dimensions/hash/size. No network, no provider."""
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
        "has_audio": bool(audio),
        "has_video": bool(video),
        "size_bytes": size,
        "sha256": sha256_file(p),
        "container": fmt.get("format_name", ""),
    }


def scan_for_video(attachments: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Pick the first video/audio attachment. Discord reports video with
    ``content_type``; a bare .mp4 sometimes arrives as octet-stream."""
    for att in attachments or []:
        ctype = (att.get("content_type") or "").lower()
        name = (att.get("filename") or "").lower()
        if ctype.startswith("video/") or ctype.startswith("audio/") or \
                name.endswith((".mp4", ".mov", ".mkv", ".webm", ".m4v", ".mp3", ".m4a", ".wav")):
            return att
    return None


def download_attachment(url: str, dest: str, timeout: int = 300) -> str:
    """Fetch a Discord CDN attachment to a transient local path (browser UA)."""
    req = urllib.request.Request(url, headers={"User-Agent": BROWSER_UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp, open(dest, "wb") as out:
        shutil.copyfileobj(resp, out, length=1024 * 1024)
    return dest


def store_durable_media(path: str, name: str, folder_id: str | None = None) -> dict[str, Any]:
    """Write the durable GHL copy via the PRODUCTION upload helper.

    Returns {'url', 'file_id'} on success. Raises RuntimeError on failure so the
    caller can keep local media and report an observable failure (never a silent
    success). ``folder_id`` is recorded when set; GHL folder targeting is only
    attempted when an id is supplied by the caller.
    """
    import adapter  # production helper (single upload implementation)

    result = adapter.ghl_upload_file(path, name)
    if not isinstance(result, dict) or not result.get("url"):
        raise RuntimeError("GHL media upload failed (no durable url returned)")
    return {"url": result["url"], "file_id": result.get("fileId", ""),
            "folder_id": folder_id or ""}


def clip_supplied_media(media_path: str, *, start: float = 0.0, end: float = 3.0,
                        out_dir: str | None = None) -> BoundedPreview:
    """Clip [start, end] from SUPPLIED local media with ffmpeg.

    This is the analysis-side replacement for the URL fetcher: no network, no
    provider, no re-acquisition. Returns the same BoundedPreview contract the
    existing Content Intelligence inspection consumes.
    """
    # Imported lazily: this module must import cleanly wherever the adapter does
    # (the adapter's own sys.path does not always include infra/).
    from content_intelligence.bounded_preview import BoundedPreview

    start = max(0.0, float(start))
    end = start + max(0.0, min(float(end) - start, MAX_LOCAL_CLIP_SECONDS))
    folder = out_dir or tempfile.mkdtemp(prefix="loom-manual-clip-")
    Path(folder).mkdir(parents=True, exist_ok=True)
    out = os.path.join(folder, f"clip-{int(start * 1000)}-{int(end * 1000)}.mp4")
    cmd = ["ffmpeg", "-v", "error", "-ss", f"{start}", "-i", media_path,
           "-t", f"{end - start}", "-c:v", "libx264", "-preset", "veryfast",
           "-crf", "28", "-c:a", "aac", "-movflags", "+faststart", "-y", out]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if proc.returncode != 0 or not os.path.exists(out):
        return BoundedPreview(start, end, provider="manual_media",
                              method="unavailable",
                              reason=(proc.stderr.strip()[:180] or "ffmpeg clip failed"),
                              provenance=("supplied_media", "local_clip_failed"))
    return BoundedPreview(start, end, start, end, out, os.path.getsize(out),
                          "manual_media", "local_supplied_media", False, "",
                          ("supplied_media", "local_ffmpeg_clip", "no_network"))


def preview_fetchers(media_path: str):
    """Return (bounded, contiguous) fetchers bound to SUPPLIED media.

    Both ignore the URL argument the pipeline would otherwise fetch and clip the
    supplied working copy instead — the acquisition bypass for media.
    """
    def _bounded(url: str = "", *, start: float = 0.0, end: float = 3.0, **kwargs: Any):
        return clip_supplied_media(media_path, start=start, end=end)

    def _contiguous(url: str = "", *, start: float, end: float, **kwargs: Any):
        return clip_supplied_media(media_path, start=start, end=end)

    return _bounded, _contiguous


def build_media_submission(*, url: str, destination: str, content_format: str,
                           submitter: str, media_reference: dict[str, Any],
                           probe: dict[str, Any], human_window: tuple[float | None, float | None],
                           reviewer_note: str = "",
                           active_tenants: set[str] | None = None) -> dict[str, Any]:
    """Build the durable submission payload, reusing the V1 contract verbatim.

    ``origin='manual_media'`` records that the operator supplied the bytes;
    ``boundary_provenance`` records who chose the window (the human, when given).
    """
    payload = manual_intake.build_submission(
        url=url, destination=destination, content_format=content_format,
        submitter=submitter, time_start_s=human_window[0], time_end_s=human_window[1],
        reviewer_note=reviewer_note, active_tenants=active_tenants)
    start_s, end_s = human_window
    payload.update({
        "origin": "manual_media",
        "media_reference": {**media_reference,
                            "duration_seconds": probe.get("duration_seconds"),
                            "width": probe.get("width"), "height": probe.get("height"),
                            "sha256": probe.get("sha256"),
                            "size_bytes": probe.get("size_bytes"),
                            "has_audio": probe.get("has_audio")},
        "boundary_provenance": ("manual_intake" if start_s is not None or end_s is not None
                                else "machine"),
    })
    return payload


def persist_media_submission(url: str, key: str, payload: dict[str, Any]):
    """Persist through the existing V1 writer (same table, same dedup)."""
    return manual_intake.persist_submission(url, key, payload)


def cleanup(path: str | None) -> None:
    """Remove transient working material. Never touches the durable GHL copy."""
    if not path:
        return
    try:
        p = Path(path)
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
        elif p.exists():
            p.unlink()
    except Exception:  # noqa: BLE001
        pass
