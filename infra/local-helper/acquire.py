"""Download + validation + payload building for the local acquisition helper.

Deterministic core (unit-testable) is separated from I/O (subprocess ffprobe,
network download, Supabase persist) so the security rules can be tested without
touching the network or a real media file.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import urllib.error
import urllib.request
from urllib.parse import urljoin

import acquisition
import manual_intake
import media_filter
import reaction_library
from validate import ValidationError, validate_media_url

UA = "LOOM-LocalHelper/1.0"


class DownloadError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message


# ---------------------------------------------------------------------------
# Download (validated redirects + byte ceiling + atomic rename)
# ---------------------------------------------------------------------------
class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # never auto-follow; we validate each hop manually


_DEFAULT_OPENER = urllib.request.build_opener(_NoRedirect)


def _follow_redirects(url: str, timeout: float, max_redirects: int, opener):
    current = url
    for _ in range(max_redirects + 1):
        validate_media_url(current)
        req = urllib.request.Request(current, headers={"User-Agent": UA})
        try:
            return opener.open(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            if e.code in (301, 302, 303, 307, 308):
                loc = (e.headers.get("Location") if e.headers else None)
                if not loc:
                    raise DownloadError("REDIRECT_NO_LOCATION", "redirect without Location")
                current = urljoin(current, loc)
                continue
            raise DownloadError("HTTP_ERROR", f"HTTP {e.code}")
    raise DownloadError("TOO_MANY_REDIRECTS", "too many redirects")


def _cleanup(path: str) -> None:
    try:
        if os.path.exists(path):
            os.remove(path)
    except Exception:
        pass


def download_one_video(candidate_url, tmp_dir, *, max_bytes, timeout,
                       max_redirects=5, opener=None):
    """Download exactly one MP4 to a temp file, validating every redirect and
    the byte ceiling. Returns (temp_path, bytes_written). The caller computes the
    checksum, derives the final filename, then atomically finalizes (see
    finalize()). The temp file is cleaned on ANY failure."""
    validate_media_url(candidate_url)
    op = opener or _DEFAULT_OPENER
    os.makedirs(tmp_dir, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=tmp_dir, suffix=".mp4.part")
    os.close(fd)
    written = 0
    try:
        resp = _follow_redirects(candidate_url, timeout, max_redirects, op)
        try:
            with open(tmp_path, "wb") as f:
                while True:
                    chunk = resp.read(64 * 1024)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > max_bytes:
                        raise DownloadError("BYTE_LIMIT", "download exceeds byte ceiling")
                    f.write(chunk)
        finally:
            try:
                resp.close()
            except Exception:
                pass
        if written == 0:
            raise DownloadError("EMPTY_DOWNLOAD", "downloaded zero bytes")
        return tmp_path, written
    except DownloadError:
        _cleanup(tmp_path)
        raise
    except Exception as e:
        _cleanup(tmp_path)
        raise DownloadError("DOWNLOAD_FAILED", type(e).__name__) from e


def finalize(tmp_path: str, final_path: str) -> str:
    """Atomically move a validated temp file to its final library path."""
    os.replace(tmp_path, final_path)
    return final_path


# ---------------------------------------------------------------------------
# ffprobe validation
# ---------------------------------------------------------------------------
def run_ffprobe(path: str, timeout: float = 30) -> dict:
    cmd = ["ffprobe", "-v", "error", "-show_streams", "-show_format",
           "-print_format", "json", path]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise DownloadError("FFPROBE_TIMEOUT", "ffprobe timed out")
    if result.returncode != 0:
        # stderr may contain the local path; never surface raw ffprobe output.
        raise DownloadError("FFPROBE_FAILED", "ffprobe could not read the file")
    try:
        return json.loads(result.stdout.decode() or "{}")
    except json.JSONDecodeError:
        raise DownloadError("FFPROBE_BAD_JSON", "ffprobe returned invalid json")


def parse_ffprobe_result(ffprobe_json: dict, *, actual_size_bytes, max_bytes,
                         max_duration) -> dict:
    """Validate ffprobe output + size/duration ceilings. Returns metadata."""
    streams = ffprobe_json.get("streams") or []
    fmt = ffprobe_json.get("format") or {}

    if actual_size_bytes is None or actual_size_bytes > max_bytes:
        raise DownloadError("SIZE_LIMIT", "final size exceeds byte ceiling")

    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        raise DownloadError("NO_VIDEO", "no video stream found")
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)

    duration = fmt.get("duration") or video.get("duration")
    try:
        duration = float(duration) if duration not in (None, "") else None
    except (TypeError, ValueError):
        duration = None
    if duration is None:
        raise DownloadError("NO_DURATION", "could not determine duration")
    if duration > max_duration:
        raise DownloadError("DURATION_LIMIT", "duration exceeds ceiling")

    width = video.get("width")
    height = video.get("height")
    fps = None
    fr = video.get("avg_frame_rate") or video.get("r_frame_rate") or ""
    if "/" in fr:
        try:
            num, _, den = fr.partition("/")
            if den and float(den):
                fps = float(num) / float(den)
        except (TypeError, ValueError, ZeroDivisionError):
            fps = None

    return {
        "duration_seconds": duration,
        "width": width,
        "height": height,
        "frame_rate": fps,
        "has_audio": audio is not None,
        "container": fmt.get("format_name"),
        "video_codec": video.get("codec_name"),
    }


def checksum_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(64 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Payload building (reuses Acquisition Boundary + Reaction Library contracts)
# ---------------------------------------------------------------------------
def build_acquisition_payload(*, canonical_url, candidate_host, actor, max_bytes,
                              max_duration, adapter="chrome_network_capture") -> dict:
    """Build the acquisition job payload. NEVER includes the signed CDN URL."""
    v = manual_intake.validate_and_canonicalize(canonical_url)
    fingerprint = manual_intake.submission_fingerprint(v["platform"], v["canonical_url"])
    job = acquisition.plan_acquisition_job(
        fingerprint=fingerprint,
        platform=v["platform"],
        platform_content_id=v["content_id"],
        canonical_url=v["canonical_url"],
        authorization_type=acquisition.AUTH_ACQUIRE_FOR_REACTION,
        actor=actor,
        destination=acquisition.DESTINATION_PERSONAL_REACTION_LIBRARY,
        acquisition_mode=acquisition.MODE_FULL_ITEM,
        max_bytes=max_bytes,
        max_duration_seconds=max_duration,
    )
    # The helper IS the acquiring adapter: mark running (not blocked/queued).
    job["status"] = acquisition.STATUS_RUNNING
    job["status_reason"] = ""
    job["adapter"] = adapter
    # Sanitized provenance only — never the signed URL.
    job["asset_provenance"] = {
        "adapter": adapter,
        "candidate_host": media_filter.redact_url(candidate_host) if candidate_host else "",
    }
    return job


def build_library_entry(*, canonical_url, filename, digest, size_bytes,
                        duration_seconds, content_type, local_path, job_id,
                        is_non_production=True) -> dict:
    """Build the Reaction Library record. Provenance excludes the signed URL."""
    v = manual_intake.validate_and_canonicalize(canonical_url)
    fingerprint = manual_intake.submission_fingerprint(v["platform"], v["canonical_url"])
    rec = reaction_library.plan_library_entry(
        duplicate_fingerprint=fingerprint,
        platform=v["platform"],
        platform_content_id=v["content_id"],
        canonical_url=v["canonical_url"],
        acquisition_job_id=job_id,
        filename=filename,
        digest=digest,
        size_bytes=size_bytes,
        duration_seconds=duration_seconds,
        content_type=content_type,
        storage_url=local_path,
    )
    rec["is_non_production"] = is_non_production
    return rec


def library_filename(canonical_url: str, digest: str) -> str:
    v = manual_intake.validate_and_canonicalize(canonical_url)
    return reaction_library.filename_for(v["platform"], v["content_id"], digest)
