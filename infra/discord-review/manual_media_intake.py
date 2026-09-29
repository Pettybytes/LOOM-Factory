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

# --- window-intent tokens -------------------------------------------------
# Delimiters are OPTIONAL so the shapes an operator actually types
# (`START 120`, `window: 120-180`) are understood instead of being silently
# dropped into a full-source acquisition. A number is still REQUIRED by the
# value patterns, so ordinary prose cannot be mistaken for a value.
_NUM = r"[0-9][0-9:.]*"
_WIN_KV_START = re.compile(rf"\bstart\s*[:=]?\s*({_NUM})", re.IGNORECASE)
_WIN_KV_END = re.compile(rf"\bend\s*[:=]?\s*({_NUM})", re.IGNORECASE)
# A range may be introduced by a keyword, or stand alone on its own line. The
# line form is anchored to EXACTLY two values so a date (2026-09-27) can never
# be read as a window.
_WIN_RANGE_KW = re.compile(
    rf"\b(?:window|range|clip)\s*[:=]?\s*({_NUM})\s*[-\u2013]\s*({_NUM})", re.IGNORECASE)
_WIN_RANGE_LINE = re.compile(
    rf"^\s*({_NUM})\s*[-\u2013]\s*({_NUM})\s*$", re.MULTILINE)
# "Intent" is deliberately narrower than "any mention": it needs a delimiter or
# a digit after the keyword, so a sentence like "start the download" is NOT
# intent, while `START: abc` (delimiter, unusable value) IS intent and fails.
_WIN_INTENT = re.compile(
    r"\bstart\s*[:=]|\bend\s*[:=]|\bstart\s+[0-9]|\bend\s+[0-9]|"
    r"\b(?:window|range|clip)\s*[:=]",
    re.IGNORECASE)


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
    """Parse an operator message: a URL plus an OPTIONAL bounded window.

    Accepted URL forms: bare URL anywhere in the text, or explicit ``url:``.

    Accepted window forms (delimiter optional; clock values supported):
      ``start=1:23 end=2:45`` · ``START 120`` / ``END 180`` · ``START: 120``
      ``window: 120-180`` · a standalone ``120-180`` line

    Returns ``window_intent``/``window_problem`` so a caller can tell "no window
    requested" (full source is legitimate) from "a window was requested but could
    not be understood" (which must FAIL CLOSED, never fall back to the source).
    """
    text = content or ""
    out: dict[str, Any] = {"url": "", "start_s": None, "end_s": None, "tenant": "",
                           "window_intent": False, "window_problem": ""}
    for token in ("url", "source"):
        m = re.search(rf"^{token}\s*[:=]\s*(\S+)", text, re.IGNORECASE | re.MULTILINE)
        if m:
            out["url"] = m.group(1)
            break
    if not out["url"]:
        m = _URL.search(text)
        if m:
            out["url"] = m.group(0).rstrip(".,)")

    # Scan window tokens with URLs REMOVED: a query string can legitimately carry
    # `&start=`, and that is part of the source identity rather than an operator
    # window instruction.
    scan = _URL.sub(" ", text)
    start_raw = _WIN_KV_START.search(scan)
    end_raw = _WIN_KV_END.search(scan)
    range_kw = _WIN_RANGE_KW.search(scan)
    range_line = _WIN_RANGE_LINE.search(scan)
    intent = bool(_WIN_INTENT.search(scan) or range_kw or range_line)
    out["window_intent"] = intent

    def _to_seconds(token: str) -> float | None:
        try:
            return parse_timestamp((token or "").strip())
        except ValueError:
            return None

    if range_kw:
        out["start_s"] = _to_seconds(range_kw.group(1))
        out["end_s"] = _to_seconds(range_kw.group(2))
    elif range_line and not (start_raw or end_raw):
        out["start_s"] = _to_seconds(range_line.group(1))
        out["end_s"] = _to_seconds(range_line.group(2))
    else:
        if start_raw:
            out["start_s"] = _to_seconds(start_raw.group(1))
        if end_raw:
            out["end_s"] = _to_seconds(end_raw.group(1))

    if intent:
        if out["start_s"] is None and out["end_s"] is None:
            out["window_problem"] = "window_unparsed"
        elif out["start_s"] is None or out["end_s"] is None:
            out["window_problem"] = "window_incomplete"

    tenant_raw = re.search(r"\btenant\s*[:=]\s*([A-Za-z0-9_.-]+)", text, re.IGNORECASE)
    if tenant_raw:
        out["tenant"] = tenant_raw.group(1).strip()
    return out


def probe_media(path: str | os.PathLike[str]) -> dict[str, Any]:
    """Local ffprobe only: duration/dimensions/hash/size. No network, no provider.

    Thin alias for the SHARED probe in ``media_validation`` so there is one
    implementation of "what are this file's properties". Raises ValueError when
    ffprobe itself fails (unreadable), which is distinct from "readable but
    unsafe" — that judgement belongs to :func:`validate_finalized_media`.
    """
    return _acq("media_validation").probe_media_file(path)


def verify_artifact_integrity(path: str | os.PathLike[str], **kwargs: Any) -> dict[str, Any]:
    """The ONE pre-durable integrity gate.

    Deliberately NOT ``probe_media``: container metadata is not proof of usable
    media. Shared with every acquisition mechanism via ``media_validation`` — see
    that module for the mechanism, the evidence, and the cost.
    """
    return _acq("media_validation").verify_media_integrity(path, **kwargs)


def validate_finalized_artifact(path: str | os.PathLike[str], *, max_bytes: int,
                                probe_fn: Any = None, integrity_fn: Any = None,
                                require_audio: bool = False) -> dict[str, Any]:
    """The SHARED post-acquisition contract: is this artifact safe for LOOM?

    Platform-specific code decides WHAT its finalized artifact is; this decides
    whether it may be stored. Kept as a named seam here so Manual Intake consumes
    the same contract every other mechanism does.
    """
    return _acq("media_validation").validate_finalized_media(
        path, max_bytes=max_bytes, probe_fn=probe_fn, integrity_fn=integrity_fn,
        require_audio=require_audio)


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

    ``kind`` (clip vs VOD) is carried through when the platform's own adapter
    reports one, because the long-source window rule depends on it: a clip is short
    by contract and may be acquired whole, while a VOD must be bounded. This only
    surfaces information the adapter already computes -- it is not a second
    identity model, and identity itself still comes from the intake contract.
    """
    resolved = manual_intake.validate_and_canonicalize(url)
    if isinstance(resolved, dict):
        platform = resolved.get("platform", "")
        # A kind this resolver ALREADY holds is never lost: it is only ever filled
        # in from the platform's own adapter when absent.
        known_kind = str(resolved.get("kind") or "")
        try:
            sa = _acq("source_adapters")
            adapter = sa.adapter_for(platform)
            if adapter is not None and not known_kind:
                parsed = adapter.canonicalize(url)
                if isinstance(parsed, dict) and parsed.get("kind"):
                    known_kind = str(parsed["kind"])
        except Exception as exc:  # noqa: BLE001 — kind is advisory; identity is already proven
            # NOT silent. An unobtainable kind is a real gap in the operator surface
            # (whole-source vs bounded-segment), so it is diagnosed rather than
            # erased with `pass`. Identity was proven above and is unaffected, so
            # the submission still proceeds -- `kind` stays advisory.
            print(f"[intake] kind unresolved platform={platform!r} "
                  f"error={type(exc).__name__}: {str(exc)[:160]}", flush=True)
        if known_kind:
            resolved = {**resolved, "kind": known_kind}
        try:
            # A URL timestamp is a HINT, never identity. It is surfaced for the
            # operator (Start prefill) through the platform's own adapter, and it
            # cannot change the canonical URL or the dedup fingerprint.
            hint = _acq("source_adapters").source_start_hint_ms(platform, url)
            if hint:
                resolved = {**resolved, "start_hint_ms": int(hint)}
        except Exception:  # noqa: BLE001 — a hint must never fail a submission
            pass
    return resolved


def probe_source_duration(url: str) -> float | None:
    """FREE metadata-only source duration, through the EXISTING bounded probe.

    Used to decide whether a window is REQUIRED before any download is attempted —
    the same ``--skip-download`` probe the ladder's pre-flight already runs (no
    cookies, no auth, no paid provider, $0). Returns None when the duration is not
    exposed, and the caller must then treat the length as UNKNOWN, never as short.
    """
    ya = _acq("ytdlp_adapter")
    try:
        meta = json.loads(
            ya.run_ytdlp_command(ya.build_probe_command(url), timeout=120.0) or "{}")
        if isinstance(meta, dict) and meta.get("_type") in ("playlist", "multi_video"):
            entries = [e for e in (meta.get("entries") or []) if isinstance(e, dict)]
            if len(entries) == 1:
                meta = entries[0]
        if isinstance(meta, dict) and isinstance(meta.get("duration"), (int, float)):
            return float(meta["duration"])
    except Exception as exc:  # noqa: BLE001 — a probe failure is not a source failure
        print(f"[intake] duration probe unavailable ({type(exc).__name__})", flush=True)
    return None


def probe_source_duration_diag(url: str) -> tuple[float | None, str]:
    """(duration_s, unavailable_reason) from the SAME free metadata probe.

    `probe_source_duration` collapses every failure into "unknown", which is right for
    a safety decision but wrong for the operator: a source that NO LONGER EXISTS then
    reads as "length not proven", the chooser is offered anyway, and the operator spends
    a real decision on something that can never be fetched — only to get a generic
    DOWNLOAD_FAILED later (observed live 2026-09-29 on a deleted Kick VOD: the platform's
    own API answers `No query results for model [App\\Models\\Video]` while LOOM offered
    the whole/segment choice).

    `unavailable_reason` is non-empty ONLY for a definitive "gone" verdict, so a
    transient probe failure degrades exactly as before.
    """
    ya = _acq("ytdlp_adapter")
    try:
        meta = json.loads(
            ya.run_ytdlp_command(ya.build_probe_command(url), timeout=120.0) or "{}")
        if isinstance(meta, dict) and meta.get("_type") in ("playlist", "multi_video"):
            entries = [e for e in (meta.get("entries") or []) if isinstance(e, dict)]
            if len(entries) == 1:
                meta = entries[0]
        if isinstance(meta, dict) and isinstance(meta.get("duration"), (int, float)):
            return float(meta["duration"]), ""
    except Exception as exc:  # noqa: BLE001 — classify, never raise
        detail = str(exc)
        if ya.is_source_unavailable(detail):
            print(f"[intake] source UNAVAILABLE at the metadata stage: {detail[:160]}",
                  flush=True)
            return None, detail[:200]
        print(f"[intake] duration probe unavailable ({type(exc).__name__})", flush=True)
    return None, ""


def whole_source_verdict(*, platform: str, content_id: str, canonical_url: str,
                         item_kind: str = "",
                         source_duration_s: float | None = None,
                         requires_window_for_long_source: bool = False
                         ) -> tuple[bool, str]:
    """Is a WHOLE-SOURCE acquisition legal under the shared safety contract?

    Returns ``(allowed, refusal_code)``. ONE authority: this ASKS the acquisition-mode
    gate rather than restating its rule, so an operator surface can never offer an
    option the gate would refuse — and can explain the refusal in the gate's own
    vocabulary instead of inventing a second one.

    A SHORT ITEM (clip/reel) is short by contract, so it is always legal whole even
    when its duration was not proven.
    """
    swc = _acq("source_window")
    if str(item_kind or "").lower() in swc.SHORT_ITEM_KINDS:
        return True, ""
    try:
        swc.validate_acquisition_mode(
            platform=platform, content_id=content_id, canonical_url=canonical_url,
            source_duration_s=source_duration_s, window=None, item_kind=item_kind,
            requires_window_for_long_source=requires_window_for_long_source)
        return True, ""
    except Exception as exc:  # noqa: BLE001 — a refusal is a refusal
        return False, (getattr(exc, "code", "") or type(exc).__name__)


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


# ---------------------------------------------------------------------------
# Classified intake failures
# ---------------------------------------------------------------------------
class MediaIntakeError(RuntimeError):
    """A URL-acquisition failure with a STABLE code the caller can classify.

    The operator-facing message is derived from ``code``; raw provider stderr is
    kept in ``detail`` for logs only and is never sent to Discord.
    """

    def __init__(self, code: str, message: str, detail: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.detail = detail


# An unfinished transfer is marked by yt-dlp's own ``.part`` suffix, and a
# split-stream download leaves the per-format intermediates behind. Both are
# recognised explicitly so neither can be mistaken for a finished artifact.
_PART_FILE_RE = re.compile(r"\.part$", re.IGNORECASE)
_SPLIT_STREAM_RE = re.compile(r"\.f\d+\.[A-Za-z0-9]+$", re.IGNORECASE)


def _select_finalized_artifact(out_dir: str, stdout: str,
                               ) -> tuple[str | None, str, dict[str, Any]]:
    """The ONE finalized artifact, or (None, failure_code, evidence).

    FAIL CLOSED. The only trusted signal is the path yt-dlp reports through
    ``--print after_move:filepath``, which it emits only after a successful
    download AND merge. There is deliberately NO "newest non-zero file wins"
    fallback: that is exactly how an audio-only ``.m4a`` half was returned as if
    it were the acquired media, producing the misleading
    "supplied file has no video stream".
    """
    printed = []
    for line in (stdout or "").splitlines():
        line = line.strip()
        if line and os.path.isfile(line):
            printed.append(line)
    if printed:
        return printed[-1], "", {"reported_by": "after_move:filepath"}

    try:
        present = sorted(os.listdir(out_dir))
    except OSError:
        present = []
    parts = [f for f in present if _PART_FILE_RE.search(f)]
    split = [f for f in present if _SPLIT_STREAM_RE.search(f)]
    evidence = {"present": present[:20], "partial_files": parts[:10],
                "split_streams": split[:10]}
    if parts:
        # An unfinished transfer: `.part` is still on disk, so the final file
        # was never completed (this is what --no-part used to hide).
        return None, "DOWNLOAD_INCOMPLETE", evidence
    if split:
        # Separate video/audio streams were fetched but the merge that produces
        # the single deliverable did not complete. Intermediates are evidence,
        # never a result.
        return None, "SPLIT_STREAM_UNFINISHED", evidence
    return None, "NO_FINALIZED_ARTIFACT", evidence


def _count_split_intermediates(out_dir: str) -> int:
    try:
        return sum(1 for f in os.listdir(out_dir) if _SPLIT_STREAM_RE.search(f))
    except OSError:
        return 0


def resolve_platform_capability(platform: str) -> dict[str, Any]:
    """The platform's acquisition MECHANISM + capability, from the EXISTING registry.

    Read-only and non-upgrading: it REPORTS what the registered adapter claims
    (``adapter_id``, ``implemented``, ``runtime_proven``, proven modes, long-form /
    window requirements) plus the declared platform capability state.

    It never asserts a capability the registry does not claim. That is why Kick
    reads as clip-proven / VOD-not-proven here rather than being assumed: Manual
    Intake must be able to record the mechanism truthfully per platform.
    """
    out: dict[str, Any] = {
        "platform": (platform or "").lower(), "registered": False, "adapter_id": "",
        "implemented": False, "runtime_proven": False, "proven_modes": [],
        "long_form": False, "requires_window_for_long_source": False,
        "capability_state": "", "capability_reason": "", "can_acquire": False,
    }
    if not out["platform"]:
        return out
    try:
        sa = _acq("source_adapters")
        pc = _acq("platform_capabilities")
    except Exception as exc:  # noqa: BLE001
        out["registry_error"] = type(exc).__name__
        return out
    adapter = sa.adapter_for(out["platform"])
    out["registered"] = adapter is not None
    if adapter is not None:
        out["adapter_id"] = str(getattr(adapter, "adapter_id", "") or "")
        out["implemented"] = bool(getattr(adapter, "implemented", False))
        out["runtime_proven"] = bool(getattr(adapter, "runtime_proven", False))
        modes = getattr(adapter, "proven_modes", None)
        out["proven_modes"] = sorted(modes) if modes else []
        out["long_form"] = bool(getattr(adapter, "long_form", False))
        out["requires_window_for_long_source"] = bool(
            getattr(adapter, "requires_window_for_long_source", False))
    try:
        out["capability_state"] = pc.capability(out["platform"])
        out["capability_reason"] = pc.capability_reason(out["platform"])
        out["can_acquire"] = bool(pc.can_acquire(out["platform"]))
    except Exception:  # noqa: BLE001 — capability metadata is best-effort
        pass
    return out


# Identity reconciliation verdicts. The SEMANTICS are the point: only an
# AUTHORITATIVE, POSITIVE conflict may block an acquisition.
IDENTITY_RECONCILED = "reconciled"        # adapter confirms the identity -> proceed
IDENTITY_MISMATCH = "mismatch"            # authoritative AND conflicting -> FAIL CLOSED
IDENTITY_UNVERIFIED = "unverified"        # no usable signal / undecidable -> do not block
IDENTITY_NOT_APPLICABLE = "not_applicable"  # no adapter for this platform -> do not block


def _has_authoritative_identity(meta: dict[str, Any] | None) -> bool:
    """Does the platform metadata actually carry a usable identity signal?

    Only the platform's own extractor output counts. An ABSENT signal means
    UNVERIFIED, never a manufactured mismatch — a platform must not become
    unusable merely because its identity cannot be proven.
    """
    if not isinstance(meta, dict):
        return False
    for key in ("id", "webpage_url", "url"):
        value = meta.get(key)
        if isinstance(value, str) and value.strip():
            return True
    return False


def _reconcile_platform_identity(platform: str, expected_id: str,
                                 meta: dict[str, Any] | None) -> str:
    """Identity cross-check via the PLATFORM'S OWN rule. Returns one of four verdicts.

    The rule lives in the adapter registry (``source_adapters.reconcile_identity``),
    so Twitch's numeric-id quirk and Kick's case-insensitivity are handled by the
    platform that knows about them rather than by Manual Intake.

    POLICY (only an authoritative positive conflict blocks):
      * ``not_applicable`` — no adapter registered for the platform. Proceed.
      * ``unverified``     — an adapter exists but the metadata carries no usable
                             identity signal, or the check could not decide.
                             Proceed; never fabricate a mismatch from absence.
      * ``reconciled``     — authoritative and consistent. Proceed.
      * ``mismatch``       — authoritative AND conflicting. FAIL CLOSED: storing
                             the wrong asset durably is not recoverable.
    """
    if not platform or not expected_id:
        return IDENTITY_UNVERIFIED
    try:
        sa = _acq("source_adapters")
        if sa.adapter_for(platform) is None:
            return IDENTITY_NOT_APPLICABLE
    except Exception:  # noqa: BLE001
        return IDENTITY_UNVERIFIED
    if not _has_authoritative_identity(meta):
        return IDENTITY_UNVERIFIED
    try:
        return (IDENTITY_RECONCILED if sa.reconcile_identity(platform, expected_id, meta)
                else IDENTITY_MISMATCH)
    except Exception:  # noqa: BLE001
        return IDENTITY_UNVERIFIED


def build_operator_window(*, platform: str, canonical_url: str, content_id: str,
                          start_s: float, end_s: float,
                          source_duration_s: float | None = None) -> dict[str, Any]:
    """Validate + serialize an operator window through the SHARED contract.

    ONE path for BOTH ingress modes (local upload and URL-only) so the 300 s clip
    limit, the source-end tolerance and range identity can never differ between
    them. A refusal is a refusal: it is never softened into a whole-source store.
    """
    swc = _acq("source_window")
    start_ms, end_ms = swc.from_seconds(float(start_s), float(end_s))
    try:
        return swc.validate_window(
            platform=platform, canonical_url=canonical_url, content_id=content_id,
            start_ms=start_ms, end_ms=end_ms, origin="operator_adjusted",
            source_duration_s=source_duration_s)
    except Exception as exc:  # noqa: BLE001 — a window refusal is a refusal
        raise MediaIntakeError(
            "WINDOW_REJECTED",
            "the requested window was refused",
            detail=f"{getattr(exc, 'code', '') or type(exc).__name__}: "
                   f"{str(exc)[:180]}") from None


def _preflight_start_height(canonical_url: str, *, max_bytes: int,
                            probe_runner: Any = None
                            ) -> tuple[int | None, int | None, str, dict[str, Any] | None]:
    """Choose the starting rung from FREE metadata. Best-effort; NEVER fatal.

    Reuses the EXISTING bounded metadata probe (``build_probe_command``:
    ``--skip-download``, no cookies, no auth, no download, $0) so pre-flight adds
    no new provider and no new transport. Any failure degrades to the verified
    ladder rather than blocking the intake.
    """
    try:
        ya = _acq("ytdlp_adapter")
        runner = probe_runner or (lambda cmd: ya.run_ytdlp_command(cmd, timeout=120.0))
        meta = json.loads(runner(ya.build_probe_command(canonical_url)) or "{}")
        if isinstance(meta, dict) and meta.get("_type") in ("playlist", "multi_video"):
            entries = [e for e in (meta.get("entries") or []) if isinstance(e, dict)]
            if len(entries) == 1:
                meta = entries[0]
        if not isinstance(meta, dict) or not meta.get("formats"):
            return None, None, "no_metadata", meta if isinstance(meta, dict) else None
        height, estimate, why = ya.choose_starting_height(meta, max_bytes=max_bytes)
        return height, estimate, why, meta
    except Exception as exc:  # noqa: BLE001 — an optimisation must never block
        print(f"[intake] pre-flight unavailable ({type(exc).__name__}); "
              f"using the verified ladder", flush=True)
        return None, None, "preflight_failed", None


def acquire_url_media(url: str, *, out_dir: str,
                      max_bytes: int = MAX_URL_BYTES,
                      run_download: Any = None,
                      probe_fn: Any = None,
                      integrity_fn: Any = None,
                      preflight_runner: Any = None,
                      start_s: float | None = None,
                      end_s: float | None = None,
                      ) -> tuple[str, dict[str, Any]]:
    """Mode B: source URL -> EXISTING resolver -> EXISTING local downloader -> file.

    Returns (local_path, resolved) where ``local_path`` is ONE finalized,
    VIDEO-BEARING artifact that passed the integrity gate and fits the intake
    ceiling. The caller owns the temp dir.

    CONTRACT (fail closed at every step):
      * the artifact must be the one yt-dlp finalized (no newest-file guessing),
      * it must contain video,
      * its TAIL must decode cleanly (truncation is not acceptable media),
      * it must fit ``max_bytes``.

    TWO-STAGE REPRESENTATION CHOICE:
      1. PRE-FLIGHT (free metadata) picks the STARTING rung so an obviously
         oversized representation is never downloaded at all.
      2. The VERIFIED LADDER remains the safety net: the chosen rung and every
         lower one are tried, each gated by the finalized-artifact check, the
         integrity gate and the post-download ceiling check. If the estimate was
         wrong, the ladder still finds a representation that genuinely fits.
    When a caller injects ``run_download`` it owns representation choice, so
    pre-flight is skipped unless ``preflight_runner`` is given explicitly.
    """
    ya = _acq("ytdlp_adapter")
    resolved = resolve_source_url(url)
    # Platform MECHANISM + capability come from the EXISTING registry (adapter
    # selection), not from a Manual-Intake-local assumption about yt-dlp.
    capability = resolve_platform_capability(resolved.get("platform", ""))
    resolved = {**resolved, "capability": capability}
    print(f"[intake] platform={capability['platform']} "
          f"adapter={capability['adapter_id'] or 'none'} "
          f"state={capability['capability_state'] or 'unknown'} "
          f"proven_modes={capability['proven_modes']}", flush=True)
    os.makedirs(out_dir, exist_ok=True)
    template = os.path.join(out_dir, "%(id)s.%(ext)s")
    _download = run_download or (lambda cmd: ya.run_ytdlp_command(cmd, timeout=600.0))
    _probe = probe_fn or probe_media
    _integrity = integrity_fn or (lambda p: ya.verify_media_integrity(p))

    # ---- stage 1: pre-flight (optimisation only, never authoritative) --------
    start_height: int | None = None
    estimate: int | None = None
    why = "skipped"
    meta: dict[str, Any] | None = None
    if preflight_runner is not None or run_download is None:
        start_height, estimate, why, meta = _preflight_start_height(
            resolved["canonical_url"], max_bytes=max_bytes, probe_runner=preflight_runner)
        print(f"[intake] pre-flight start_height={start_height} "
              f"estimate={estimate} reason={why}", flush=True)

    # Identity cross-check through the PLATFORM's own rule (recorded provenance).
    identity = _reconcile_platform_identity(
        capability["platform"], str(resolved.get("content_id") or ""), meta)
    resolved["identity_reconciliation"] = identity
    if identity == IDENTITY_MISMATCH:
        # AUTHORITATIVE POSITIVE CONFLICT — fail closed. Storing the wrong asset
        # durably is not recoverable, so this is the ONLY verdict that blocks.
        print(f"[intake] identity MISMATCH (authoritative) for "
              f"{capability['platform']} id={resolved.get('content_id')} — refusing",
              flush=True)
        raise MediaIntakeError(
            "IDENTITY_MISMATCH",
            "the source identity conflicts with the identity that was submitted",
            detail=f"platform={capability['platform']} "
                   f"expected={resolved.get('content_id')}")
    if identity in (IDENTITY_UNVERIFIED, IDENTITY_NOT_APPLICABLE):
        # NOT a conflict: record it and continue. Never manufacture a mismatch.
        print(f"[intake] identity {identity} for "
              f"{capability['platform']} (not authoritatively verifiable) — proceeding",
              flush=True)

    # ---- ACQUISITION MODE (the SHARED source_window contract) ---------------
    # No start/end => the whole source. A bounded request is validated HERE and
    # fails closed; it is NEVER silently downgraded to the whole source, and a
    # resulting artifact that does not match the approved window is rejected below.
    #
    # ONE authoritative gate (``validate_acquisition_mode``) decides whether a
    # WHOLE-SOURCE acquisition is allowed at all. A source longer than the clip
    # limit -- or a platform whose sources are long-form with no duration proof --
    # is refused with ``LONG_FORM_REQUIRES_WINDOW`` BEFORE any bytes are fetched,
    # instead of downloading a multi-hour source and failing later on the size
    # ceiling. Legitimate short/full-item sources are unaffected.
    swc = _acq("source_window")
    window: dict[str, Any] | None = None
    source_duration_s: float | None = None
    if isinstance(meta, dict) and isinstance(meta.get("duration"), (int, float)):
        source_duration_s = float(meta["duration"])
    if start_s is not None or end_s is not None:
        if start_s is None or end_s is None:
            raise MediaIntakeError(
                "WINDOW_INCOMPLETE",
                "a bounded acquisition needs BOTH a start and an end")
        window = build_operator_window(
            platform=capability["platform"] or resolved.get("platform", ""),
            canonical_url=resolved["canonical_url"],
            content_id=str(resolved.get("content_id") or ""),
            start_s=float(start_s), end_s=float(end_s),
            source_duration_s=source_duration_s)
    try:
        mode = swc.validate_acquisition_mode(
            platform=capability["platform"] or resolved.get("platform", ""),
            content_id=str(resolved.get("content_id") or ""),
            canonical_url=resolved["canonical_url"],
            source_duration_s=source_duration_s, window=window,
            item_kind=str(resolved.get("kind") or ""),
            requires_window_for_long_source=bool(
                capability.get("requires_window_for_long_source")))
    except Exception as exc:  # noqa: BLE001 — a refusal is a refusal
        raise MediaIntakeError(
            getattr(exc, "code", "") or "WINDOW_REJECTED",
            "the acquisition mode was refused",
            detail=f"{getattr(exc, 'code', '') or type(exc).__name__}: "
                   f"{str(exc)[:180]}") from None
    if window is not None:
        print(f"[intake] bounded window "
              f"{swc.human_range(window['start_ms'], window['end_ms'])} "
              f"({window['clip_duration_ms'] / 1000:.3f}s) "
              f"range={str(window['range_fingerprint'])[:12]}", flush=True)
    elif mode.get("mode") == "full_item":
        print(f"[intake] whole-source acquisition allowed "
              f"(source={source_duration_s}s)", flush=True)
    resolved["window"] = window
    resolved["acquisition_mode"] = "bounded_segment" if window else "full_item"

    # ---- stage 2: the verified ladder (the safety net) ----------------------
    ladder = ya.ladder_from(start_height, ladder=ya.height_ladder(max_bytes))
    attempts: list[dict[str, Any]] = []
    for max_height in ladder:
        cmd = ya.build_download_command(
            resolved["canonical_url"], out_template=template,
            max_bytes=max_bytes, max_height=max_height,
            section_arg=(_acq("source_window").ytdlp_section_arg(window)
                         if window else None))
        stdout = _download(cmd)

        path, failure, evidence = _select_finalized_artifact(out_dir, stdout)
        if path is None:
            # This REPRESENTATION did not finalize (an unfinished transfer, an
            # unmerged split pair, or nothing produced). Never a result — but a
            # lower representation may still finalize, so record and step down.
            attempts.append({"height": max_height, "code": failure, **evidence})
            print(f"[intake] height={max_height} not usable code={failure} "
                  f"evidence={evidence}", flush=True)
            continue

        # ONE shared post-acquisition contract decides whether this artifact is
        # safe for LOOM: exists -> non-zero -> within ceiling -> video-bearing ->
        # positive duration -> integrity valid. The ladder no longer re-implements
        # these checks, so the rule cannot drift between mechanisms.
        verdict = validate_finalized_artifact(
            path, max_bytes=max_bytes, probe_fn=_probe, integrity_fn=_integrity)
        if verdict["eligible"]:
            if window is not None:
                # The delivered clip MUST match the window the operator approved.
                swc_v = _acq("source_window")
                try:
                    swc_v.validate_resulting_duration(
                        approved=window,
                        measured_ms=int(round(float(verdict["duration_seconds"]) * 1000)))
                except Exception as exc:  # noqa: BLE001
                    attempts.append({"height": max_height, "code": "WINDOW_MISMATCH",
                                     "detail": str(exc)[:200]})
                    print(f"[intake] bounded window mismatch at height={max_height}: "
                          f"{str(exc)[:160]}", flush=True)
                    continue
            resolved["working_source"] = {
                "duration_seconds": verdict["duration_seconds"],
                "timebase_origin_s": (window["start_ms"] / 1000.0) if window else 0.0,
            }
            if attempts:
                print(f"[intake] settled at height={max_height} "
                      f"bytes={verdict['size_bytes']} after rejecting "
                      f"{[a['height'] for a in attempts]}", flush=True)
            return path, resolved

        code = verdict["reason"] or "MEDIA_INTEGRITY_FAILED"
        attempts.append({"height": max_height, "code": code,
                         "size_bytes": verdict.get("size_bytes"),
                         "detail": _short_validation_detail(verdict)})
        print(f"[intake] height={max_height} rejected code={code} "
              f"size={verdict.get('size_bytes')} "
              f"detail={_short_validation_detail(verdict)[:160]}", flush=True)

    # Every representation failed. Fail closed with the most actionable reason.
    code = _dominant_failure_code(attempts)
    raise MediaIntakeError(
        code,
        "no usable media representation could be acquired under the intake limit",
        detail=json.dumps(attempts)[:600])


def _short_validation_detail(verdict: dict[str, Any]) -> str:
    """Concise, log-friendly detail for a rejected artifact (never a raw dump)."""
    integrity = verdict.get("integrity") or {}
    if integrity and not integrity.get("ok"):
        return str(integrity.get("detail", ""))[:200]
    probe = verdict.get("probe") or {}
    if probe:
        return (f"container={probe.get('container', '')} "
                f"has_video={probe.get('has_video')} "
                f"duration={probe.get('duration_seconds')}")[:200]
    return str(verdict.get("probe_error") or verdict.get("reason") or "")[:200]


def _dominant_failure_code(attempts: list[dict[str, Any]]) -> str:
    """The single most useful code after every representation failed.

    A size rejection is the most actionable ("the source is too large for this
    ceiling"), then corruption, then a missing video stream; otherwise the first
    download-integrity failure is reported. Never a silent success.
    """
    codes = [str(a.get("code") or "") for a in attempts]
    for preferred in ("MEDIA_EXCEEDS_LIMIT", "MEDIA_INTEGRITY_FAILED",
                      "NO_VIDEO_STREAM", "BAD_DURATION",
                      "SPLIT_STREAM_UNFINISHED", "DOWNLOAD_INCOMPLETE"):
        if preferred in codes:
            return preferred
    return codes[0] if codes and codes[0] else "NO_FINALIZED_ARTIFACT"


def store_durable_media(path: str, name: str, folder_id: str | None = None) -> dict[str, Any]:
    """Write the durable GHL copy via the PRODUCTION upload helper.

    Returns {'url', 'file_id'} on success. Raises RuntimeError on failure so the
    caller can keep local media and report an observable failure (never a silent
    success). ``folder_id`` is recorded when set; GHL folder targeting is only
    attempted when an id is supplied by the caller.
    """
    import adapter  # production helper (single upload implementation)

    # Pass the target folder THROUGH. The upload helper defaults to the configured
    # LOOM folder and VERIFIES placement by reading the durable record back, so a
    # silently-ignored folder target cannot be mistaken for success.
    result = adapter.ghl_upload_file(path, name, parent_id=folder_id)
    if not isinstance(result, dict) or not result.get("url"):
        # The class of failure is KNOWN here, so it is never reported as UNKNOWN.
        # The working source is deliberately kept, so a retry is always safe.
        reason = ""
        getter = getattr(adapter, "ghl_last_upload_error", None)
        if callable(getter):
            try:
                reason = str(getter() or "")
            except Exception:  # noqa: BLE001 — diagnostics must never mask the failure
                reason = ""
        raise MediaIntakeError(
            "DURABLE_STORE_FAILED",
            "the durable copy could not be stored (the working source was kept, so "
            "retrying is safe)",
            detail=f"ghl_upload_failed reason={reason[:200]}")
    placement = {
        "placement_verified": bool(result.get("placement_verified")),
        "placement_expected": result.get("placement_expected", folder_id or ""),
        "placement_actual": result.get("placement_actual"),
        "placement_reason": result.get("placement_reason", ""),
    }
    if placement["placement_expected"] and not placement["placement_verified"]:
        # Durable but in the WRONG place. The asset is kept (the media lifecycle is
        # unchanged) while root placement is never represented as folder success.
        print(f"[manual-intake] media ROUTING FAILURE: expected folder "
              f"{placement['placement_expected']}, got "
              f"parentId={placement['placement_actual']!r} "
              f"({placement['placement_reason']})", flush=True)
    return {"url": result["url"], "file_id": result.get("fileId", ""),
            "folder_id": folder_id or "", **placement}


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


def extract_local_window(path: str, *, start_s: float, end_s: float,
                         out_dir: str | None = None) -> str:
    """Extract the operator's window from a SUPPLIED file -> the working source.

    For the LOCAL-UPLOAD path, where the supplied file may be far longer than the
    moment wanted. Re-encodes to the accepted delivery contract
    (H.264/AAC/yuv420p/fast-start) so the working asset needs no second
    normalization pass. The window is validated by the SHARED ``source_window``
    contract before this runs.

    FAILS CLOSED: a bounded request never falls back to the whole file, and a
    failed extraction is an error rather than a silent whole-source store.
    """
    start = max(0.0, float(start_s))
    end = float(end_s)
    if not (end > start):
        raise MediaIntakeError("WINDOW_REJECTED",
                               "extract window end must be greater than start")
    span = end - start
    folder = out_dir or tempfile.mkdtemp(prefix="loom-manual-window-")
    Path(folder).mkdir(parents=True, exist_ok=True)
    out = os.path.join(folder, f"window-{int(start * 1000)}-{int(end * 1000)}.mp4")
    cmd = ["ffmpeg", "-v", "error", "-ss", f"{start:.3f}", "-i", path,
           "-t", f"{span:.3f}", "-c:v", "libx264", "-preset", "veryfast",
           "-crf", "20", "-pix_fmt", "yuv420p", "-c:a", "aac",
           "-movflags", "+faststart", "-y", out]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    except (OSError, subprocess.SubprocessError) as exc:  # noqa: BLE001
        raise MediaIntakeError("WINDOW_EXTRACTION_FAILED",
                               "the window could not be extracted",
                               detail=type(exc).__name__) from None
    if proc.returncode != 0 or not os.path.exists(out) or os.path.getsize(out) <= 0:
        raise MediaIntakeError(
            "WINDOW_EXTRACTION_FAILED", "the window could not be extracted",
            detail=(proc.stderr or "").strip()[:200])
    return out


def build_media_submission(*, url: str, destination: str, content_format: str,
                           submitter: str, media_reference: dict[str, Any],
                           probe: dict[str, Any], human_window: tuple[float | None, float | None],
                           reviewer_note: str = "",
                           active_tenants: set[str] | None = None,
                           source_metadata: dict[str, Any] | None = None,
                           acquisition_provenance: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build the durable submission payload, reusing the V1 contract verbatim.

    ``origin='manual_media'`` records that the operator supplied the bytes;
    ``boundary_provenance`` records who chose the window (the human, when given).
    ``source_metadata`` (when captured) is embedded in ``media_reference`` so the
    source engagement evidence is durable at the SUBMISSION level — it therefore
    survives even when Content Intelligence later filters the asset before any
    candidate row exists. JSONB, so no migration is required.

    ``acquisition_provenance`` records the acquisition, keeping TWO COORDINATE
    SYSTEMS explicitly distinct so neither is ever inferred from the other:
      * ORIGINAL source coordinates (``original_start_s`` / ``original_end_s``) —
        what the operator asked for, in the source's own timeline;
      * WORKING source coordinates (``working_source_duration_s`` /
        ``working_timebase_origin_s``) — the durable asset's own 0-based timeline,
        where t=0 corresponds to ``working_timebase_origin_s`` in the source.
    A full-item acquisition has ``working_timebase_origin_s == 0``.
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
    if acquisition_provenance:
        reference["acquisition"] = acquisition_provenance
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
