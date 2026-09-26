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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import sys as _sys

# The adapter's runtime (run.sh) puts this directory and the acquisition package on
# sys.path but not infra/ itself, while the shared LOOM packages (content_intelligence,
# discovery) live under infra/. Make them importable so this module behaves
# identically inside the running service and in operator tools.
_INFRA = Path(__file__).resolve().parents[1]
if str(_INFRA) not in _sys.path:
    _sys.path.insert(0, str(_INFRA))

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


# ---------------------------------------------------------------------------
# Mode B — URL-ONLY intake.
#
# Reuses what already exists instead of adding a second downloader:
#   * identity/detection  -> the EXISTING intake contract (manual_intake
#                            .validate_and_canonicalize), which itself already
#                            delegates YouTube and Kick to their acquisition
#                            contracts, so intake and acquisition share ONE id.
#   * transport           -> the acquisition lane's EXISTING bounded, hygiene
#                            -checked local yt-dlp command builders ($0).
#
# It deliberately does NOT create a reaction-library row or an acquisition job:
# Manual Intake keeps no permanent source library on this machine — GHL holds the
# durable copy and the submission row holds the durable pointer. Both modes
# therefore converge at exactly one place: `durable media available`.
MAX_URL_BYTES = 100 * 1024 * 1024


def _acq(name: str):
    """Import a module from the acquisition package (both layouts the service uses)."""
    try:
        return __import__(f"acquisition.{name}", fromlist=[name])
    except ImportError:
        import sys as _s
        acq_dir = str(_INFRA / "acquisition")
        if acq_dir not in _s.path:
            _s.path.insert(0, acq_dir)
        return __import__(name)


def resolve_source_url(url: str) -> dict[str, Any]:
    """Mode B resolver: identify the platform and normalize identity.

    Raises manual_intake.SubmissionError with a classified code on any refusal
    (unsupported host, credentials, malformed id, non-media path).
    """
    return manual_intake.validate_and_canonicalize(url)


# Canonical metric -> (extractor field, key written into observed_metrics). The
# written key is the name `content_intelligence.platform_metrics` actually reads for
# that platform, so the existing market policy consumes the capture unchanged.
_YTDLP_SOURCES: dict[str, tuple[str, str]] = {
    "views": ("view_count", "views"),
    "likes": ("like_count", "likes"),
    "comments": ("comment_count", "comments"),
    "shares": ("repost_count", "shares"),
    "saves": ("save_count", "saves"),
}

# Reddit expresses reach as a NATIVE score, never views, and its market policy reads
# `score` / `num_comments`. Emitting the generic keys there would silently drop the
# same numbers, and inventing views would fabricate evidence.
_PLATFORM_KEY_OVERRIDES: dict[str, dict[str, tuple[str, str] | None]] = {
    "reddit": {"views": None, "shares": None, "saves": None,
               "likes": ("like_count", "score"),
               "comments": ("comment_count", "num_comments")},
}

ENGAGEMENT_KEYS = ("views", "likes", "comments", "shares", "saves")


def fetch_source_metadata(url: str, platform: str = "") -> dict[str, Any]:
    """Source engagement metadata via the EXISTING free extractor path.

    Reuses the acquisition lane's already-proven bounded metadata probe
    (``ytdlp_adapter.build_probe_command``: metadata only, ``--skip-download``, no
    cookies, no auth, no download, no paid provider). Returns canonical metrics plus
    the provider's own raw fields for provenance.

    NEVER raises and NEVER fabricates: a field the platform does not expose through
    this path is simply absent, which every downstream consumer reads as UNKNOWN —
    never as zero performance. Enrichment only; nothing here can reject an asset.
    """
    unavailable_via_path = "UNAVAILABLE VIA CURRENT PATH"
    empty = {"metrics": {}, "raw": {}, "publication_timestamp": None,
             "available": [], "unavailable": [], "provenance": unavailable_via_path}
    try:
        ya = _acq("ytdlp_adapter")
        meta = json.loads(ya.run_ytdlp_command(
            ya.build_probe_command(url), timeout=120.0) or "{}")
    except Exception:  # noqa: BLE001 — enrichment must never block intake
        return empty
    if not isinstance(meta, dict):
        return empty
    if meta.get("_type") in ("playlist", "multi_video"):
        entries = [e for e in (meta.get("entries") or []) if isinstance(e, dict)]
        if len(entries) == 1:
            meta = entries[0]
    if not meta.get("id"):
        return empty

    sources: dict[str, tuple[str, str] | None] = dict(_YTDLP_SOURCES)
    sources.update(_PLATFORM_KEY_OVERRIDES.get((platform or "").lower(), {}))

    metrics: dict[str, int] = {}
    measured: list[str] = []
    unsupported: list[str] = []
    for canonical in ENGAGEMENT_KEYS:
        source = sources.get(canonical)
        if source is None:
            unsupported.append(canonical)
            continue
        source_key, output_key = source
        value = meta.get(source_key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            continue
        metrics[output_key] = int(value)
        measured.append(canonical)

    published = None
    ts = meta.get("timestamp")
    if isinstance(ts, (int, float)) and not isinstance(ts, bool) and ts > 0:
        published = datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()
    else:
        upload_date = str(meta.get("upload_date") or "")
        if len(upload_date) == 8 and upload_date.isdigit():
            published = (f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:]}"
                         "T00:00:00+00:00")

    raw = {k: meta.get(k) for k in
           ("view_count", "like_count", "comment_count", "repost_count", "save_count",
            "score", "num_comments", "play_count", "timestamp", "upload_date")
           if meta.get(k) is not None}
    return {
        "metrics": metrics,
        "raw": raw,
        "publication_timestamp": published,
        "creator": str(meta.get("uploader") or meta.get("channel") or ""),
        "available": sorted(measured),
        "unavailable": sorted(k for k in ENGAGEMENT_KEYS
                              if sources.get(k) is not None and k not in measured),
        "unsupported": sorted(unsupported),
        "provenance": "local:yt-dlp metadata probe",
    }


def acquire_url_media(url: str, *, out_dir: str,
                     max_bytes: int = MAX_URL_BYTES) -> tuple[str, dict[str, Any]]:
    """Mode B: source URL -> EXISTING resolver -> EXISTING local downloader -> file.

    Returns (local_path, resolved). The caller owns the temp dir. Raises on
    failure with the underlying classified reason; it never fabricates media.
    """
    ya = _acq("ytdlp_adapter")
    resolved = resolve_source_url(url)
    os.makedirs(out_dir, exist_ok=True)
    template = os.path.join(out_dir, "%(id)s.%(ext)s")
    cmd = ya.build_download_command(resolved["canonical_url"],
                                    out_template=template, max_bytes=max_bytes)
    stdout = ya.run_ytdlp_command(cmd, timeout=600.0)
    path = ""
    for line in (stdout or "").splitlines():
        line = line.strip()
        if line and os.path.exists(line):
            path = line
    if not path:
        cands = sorted((os.path.join(out_dir, f) for f in os.listdir(out_dir)),
                       key=os.path.getmtime, reverse=True)
        path = next((c for c in cands if os.path.getsize(c) > 0), "")
    if not path:
        raise RuntimeError(f"extractor produced no media for {resolved['platform']}")
    return path, resolved


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
                           active_tenants: set[str] | None = None,
                           source_metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build the durable submission payload, reusing the V1 contract verbatim.

    ``origin='manual_media'`` records that the operator supplied the bytes;
    ``boundary_provenance`` records who chose the window (the human, when given).
    ``source_metadata`` (when captured) is embedded in ``media_reference`` so the
    source engagement evidence is durable at the SUBMISSION level — it therefore
    survives even when Content Intelligence later filters the asset before any
    candidate row exists. JSONB, so no migration is required.
    """
    payload = manual_intake.build_submission(
        url=url, destination=destination, content_format=content_format,
        submitter=submitter, time_start_s=human_window[0], time_end_s=human_window[1],
        reviewer_note=reviewer_note, active_tenants=active_tenants)
    start_s, end_s = human_window
    reference = {**media_reference,
                 "duration_seconds": probe.get("duration_seconds"),
                 "width": probe.get("width"), "height": probe.get("height"),
                 "sha256": probe.get("sha256"),
                 "size_bytes": probe.get("size_bytes"),
                 "has_audio": probe.get("has_audio")}
    if source_metadata:
        reference["source_metadata"] = source_metadata
    payload.update({
        "origin": "manual_media",
        "media_reference": reference,
        "boundary_provenance": ("manual_intake" if start_s is not None or end_s is not None
                                else None),
    })
    return payload


def find_existing_media(url: str, key: str, platform: str, canonical_url: str,
                        tenant_id: str, sha256: str) -> dict[str, Any] | None:
    """Existing durable media for the same source+tenant AND the same bytes.

    Retry safety: a re-run of the command for an already-ingested asset must not
    upload a duplicate copy to GHL. Only an exact byte match (sha256) is reused.
    """
    fp = manual_intake.submission_fingerprint(platform, canonical_url)
    req = urllib.request.Request(
        f"{url}/rest/v1/loom_manual_submissions?duplicate_fingerprint=eq."
        f"{urllib.parse.quote(fp)}&tenant_id=eq.{urllib.parse.quote(tenant_id)}"
        f"&select=media_reference&limit=1",
        headers=manual_intake._headers(key))
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            rows = json.load(r)
    except Exception:  # noqa: BLE001
        return None
    if isinstance(rows, list) and rows:
        ref = rows[0].get("media_reference") or {}
        if isinstance(ref, dict) and ref.get("url") and ref.get("sha256") == sha256:
            return ref
    return None


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
