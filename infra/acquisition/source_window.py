"""Shared source-window contract for bounded (timestamped) acquisitions.

ONE contract, reused by every long-form-capable platform (YouTube today; Twitch and
Kick later). Never a per-platform timestamp system.

Canonical unit: **integer milliseconds**. Timestamps are normalized on the way in,
so the same human intent always produces the same identity.

Policy encoded here:
  * the 300 s limit applies to the resulting CLIP, never to the source VOD;
  * an approved range must be > 0 length and <= 300 s;
  * `end` may not exceed a known source duration beyond a small, documented
    metadata tolerance;
  * a full-item acquisition is only allowed when the source itself is <= 300 s;
  * range identity distinguishes source video AND exact approved window, so one
    long-form source can legitimately yield several library assets.
"""

from __future__ import annotations

import hashlib
import math
import re

MS_MAX = 300_000                     # 300 s — the clip limit (not the source limit)
SOURCE_END_TOLERANCE_MS = 2_000      # metadata tolerance: end vs known source duration
FINAL_DURATION_TOLERANCE_MS = 2_000  # final media vs approved window
ORIGINS = frozenset({"automated_source_engine", "manual_submit_source",
                     "operator_adjusted"})

MODE_FULL = "full_item"
MODE_RANGED = "bounded_segment"

DEFAULT_PROOF_WINDOW_MS = (30_000, 60_000)     # 00:30–01:00


class WindowError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message


def _reject(code: str, message: str):
    raise WindowError(code, message)


# ---------------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------------
def _int_ms(value, *, field: str) -> int:
    """Coerce a millisecond value, failing closed with a stable code."""
    if value is None:
        _reject("MISSING_TIMESTAMP", f"{field} is required")
    if isinstance(value, bool):
        _reject("BAD_TIMESTAMP", f"{field} must be numeric, got a boolean")
    try:
        num = float(value)
    except (TypeError, ValueError):
        _reject("BAD_TIMESTAMP", f"{field} is not numeric: {value!r}")
    if not math.isfinite(num):
        _reject("NON_FINITE", f"{field} must be finite (no NaN/inf): {value!r}")
    if num < 0:
        _reject("NEGATIVE", f"{field} must be >= 0: {num}")
    return int(round(num))


def to_ms(value, *, field: str) -> int:
    """Coerce a timestamp to integer milliseconds, rejecting anything unusable."""
    if value is None:
        _reject("MISSING_TIMESTAMP", f"{field} is required")
    if isinstance(value, bool):
        _reject("BAD_TIMESTAMP", f"{field} must be numeric, got a boolean")
    try:
        num = float(value)
    except (TypeError, ValueError):
        _reject("BAD_TIMESTAMP", f"{field} is not numeric: {value!r}")
    if not math.isfinite(num):
        _reject("NON_FINITE", f"{field} must be finite (no NaN/inf): {value!r}")
    if num < 0:
        _reject("NEGATIVE", f"{field} must be >= 0: {num}")
    return int(round(num * 1000.0))


def from_seconds(start_s, end_s, *, field_prefix: str = "time") -> tuple[int, int]:
    return (to_ms(start_s, field=f"{field_prefix}_start"),
            to_ms(end_s, field=f"{field_prefix}_end"))


# ---------------------------------------------------------------------------
# Human-readable rendering
# ---------------------------------------------------------------------------
def human_clock(ms: int) -> str:
    total = int(round(int(ms) / 1000.0))
    hours, rem = divmod(total, 3600)
    minutes, seconds = divmod(rem, 60)
    return (f"{hours:d}:{minutes:02d}:{seconds:02d}" if hours
            else f"{minutes:02d}:{seconds:02d}")


def human_duration(ms: int) -> str:
    seconds = int(ms) / 1000.0
    if seconds < 60:
        return f"{seconds:.1f} s"
    minutes, rem = divmod(seconds, 60)
    return f"{int(minutes)}m {rem:04.1f}s"


def human_range(start_ms: int, end_ms: int) -> str:
    return f"{human_clock(start_ms)}–{human_clock(end_ms)}"


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------
def range_fingerprint(platform: str, content_id: str, start_ms: int, end_ms: int) -> str:
    raw = f"{platform}\x1f{content_id}\x1f{int(start_ms)}\x1f{int(end_ms)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def dedup_identity(platform: str, content_id: str,
                   window: dict | None = None) -> str:
    """Same source + same range is idempotent; different ranges are distinct."""
    if window:
        return range_fingerprint(platform, content_id,
                                 window["start_ms"], window["end_ms"])
    raw = f"{platform}\x1f{content_id}\x1f{MODE_FULL}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def filename_stem(platform: str, content_id: str, window: dict | None,
                  digest: str) -> str:
    """Deterministic name that distinguishes the SOURCE and the approved RANGE."""
    if window:
        stamp = f"{window['start_ms']//1000}-{window['end_ms']//1000}"
        return f"{platform}__{content_id}__{stamp}__{digest[:12]}"
    return f"{platform}__{content_id}__{digest[:12]}"


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
def _canonicalize_for(platform: str, canonical_url: str, content_id: str) -> dict:
    """Ask the PLATFORM REGISTRY to canonicalize. No platform name appears here."""
    try:
        import source_adapters as _sa
    except ImportError:
        import os as _os
        import sys as _sys
        _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
        import source_adapters as _sa
    try:
        return _sa.canonicalize(platform, canonical_url,
                                expected_content_id=content_id)
    except _sa.NoAdapterError as e:
        _reject("NO_ADAPTER", f"no adapter registered for {e.platform!r}")
    except Exception as e:  # noqa: BLE001 — an adapter refusal is a drift refusal
        _reject("SOURCE_URL_DRIFT", f"{getattr(e, 'code', type(e).__name__)}: {e}")


def _drift_guard(*, platform: str, canonical_url: str, content_id: str,
                 expected_content_id: str = "") -> None:
    if expected_content_id and expected_content_id != content_id:
        _reject("CONTENT_ID_DRIFT",
                f"content id {content_id!r} != expected {expected_content_id!r}")
    resolved = _canonicalize_for(platform, canonical_url, content_id)
    if resolved["canonical_url"] != canonical_url:
        _reject("SOURCE_URL_DRIFT",
                "canonical URL and content id disagree with the approved source")


def validate_source_identity(*, platform: str, canonical_url: str, content_id: str,
                             expected_content_id: str = "") -> dict[str, str]:
    """Validate source identity (URL/ID agreement) WITHOUT requiring a window.

    Used by the automated-candidate contract: a candidate with no usable window is
    still reviewable, but its identity must already be sound.
    """
    if not platform or not content_id or not canonical_url:
        _reject("MISSING_SOURCE", "platform, content id and canonical URL are required")
    _drift_guard(platform=platform, canonical_url=canonical_url,
                 content_id=content_id, expected_content_id=expected_content_id)
    return _canonicalize_for(platform, canonical_url, content_id)


def validate_window(*, platform: str, canonical_url: str, content_id: str,
                    start_ms: int, end_ms: int, origin: str,
                    source_duration_s: float | None = None,
                    authorized_by: str = "", authorized_at: str = "",
                    expected_content_id: str = "") -> dict:
    """Validate and describe an approved acquisition window (fails closed)."""
    if origin not in ORIGINS:
        _reject("BAD_ORIGIN", f"origin {origin!r} not in {sorted(ORIGINS)}")
    if not platform or not content_id or not canonical_url:
        _reject("MISSING_SOURCE", "platform, content id and canonical URL are required")
    _drift_guard(platform=platform, canonical_url=canonical_url,
                 content_id=content_id, expected_content_id=expected_content_id)

    start = _int_ms(start_ms, field="start_ms")
    end = _int_ms(end_ms, field="end_ms")
    if start < 0:
        _reject("NEGATIVE", "range start must be >= 0")
    if end <= start:
        _reject("REVERSED_OR_EMPTY",
                f"range end ({end} ms) must be greater than start ({start} ms)")
    clip = end - start
    if clip > MS_MAX:
        _reject("RANGE_TOO_LONG",
                f"approved range is {clip / 1000:.1f} s; the limit is "
                f"{MS_MAX / 1000:.0f} s")

    source_ms = None
    if source_duration_s is not None:
        source_ms = to_ms(source_duration_s, field="source_duration")
        if end > source_ms + SOURCE_END_TOLERANCE_MS:
            _reject("RANGE_BEYOND_SOURCE",
                    f"range end {end} ms exceeds the source duration {source_ms} ms "
                    f"(tolerance {SOURCE_END_TOLERANCE_MS} ms)")

    return {
        "platform": platform,
        "canonical_url": canonical_url,
        "content_id": content_id,
        "source_duration_ms": source_ms,
        "start_ms": start,
        "end_ms": end,
        "clip_duration_ms": clip,
        "origin": origin,
        "authorized_by": authorized_by,
        "authorized_at": authorized_at,
        "mode": MODE_RANGED,
        "range_fingerprint": range_fingerprint(platform, content_id, start, end),
        "identity": dedup_identity(platform, content_id,
                                   {"start_ms": start, "end_ms": end}),
    }


def require_full_item_allowed(*, platform: str, content_id: str,
                              canonical_url: str,
                              source_duration_s: float | None) -> dict:
    """A WHOLE source may only be acquired when it fits the clip limit."""
    if source_duration_s is None:
        _reject("UNKNOWN_SOURCE_DURATION",
                "cannot acquire a whole source without a known duration")
    duration_ms = to_ms(source_duration_s, field="source_duration")
    if duration_ms > MS_MAX:
        _reject("LONG_FORM_REQUIRES_RANGE",
                f"source is {duration_ms / 1000:.1f} s; a long-form source must be "
                f"acquired through an approved range of at most {MS_MAX / 1000:.0f} s")
    return {
        "platform": platform, "canonical_url": canonical_url, "content_id": content_id,
        "source_duration_ms": duration_ms, "start_ms": None, "end_ms": None,
        "clip_duration_ms": duration_ms, "mode": MODE_FULL,
        "identity": dedup_identity(platform, content_id, None),
    }


def validate_resulting_duration(*, approved: dict, measured_ms: int,
                                tolerance_ms: int = FINAL_DURATION_TOLERANCE_MS) -> dict:
    """The delivered clip must match the approved window within tolerance."""
    approved_ms = int(approved["clip_duration_ms"])
    measured = int(measured_ms)
    if abs(measured - approved_ms) > tolerance_ms:
        raise WindowError(
            "CLIP_DURATION_MISMATCH",
            f"delivered {measured / 1000:.1f} s vs approved {approved_ms / 1000:.1f} s "
            f"(tolerance {tolerance_ms / 1000:.1f} s)")
    return {"approved_ms": approved_ms, "measured_ms": measured,
            "delta_ms": measured - approved_ms, "tolerance_ms": tolerance_ms}


# ---------------------------------------------------------------------------
# WORKING-ASSET range contract
#
# Everything that operates ON a durable working asset (a bounded extraction, or a
# full source whose origin is 0) must express its range in THAT asset's own
# 0-based timeline. ORIGINAL source coordinates are preserved separately as
# provenance and must never be written into a working-relative field.
# ---------------------------------------------------------------------------
WORKING_RANGE_TOLERANCE_S = 2.0


def validate_working_range(*, start_s: float, end_s: float,
                           working_duration_s: float,
                           tolerance_s: float = WORKING_RANGE_TOLERANCE_S) -> dict:
    """Assert a plan's range fits the WORKING asset. Fails closed.

    Defence in depth behind the coordinate CONVERSION: even a correct conversion
    must not be trusted blindly, because a wrong upstream value would otherwise
    surface only as editor-side clamping over bad data.
    """
    try:
        start = float(start_s)
        end = float(end_s)
        duration = float(working_duration_s)
    except (TypeError, ValueError):
        _reject("WORKING_RANGE_UNUSABLE", "working range values are not numeric")
    if duration <= 0:
        _reject("WORKING_SOURCE_DURATION_UNKNOWN",
                "working-source duration is unknown or zero")
    if start < 0:
        _reject("WORKING_RANGE_NEGATIVE", f"working start {start} is negative")
    if end <= start:
        _reject("WORKING_RANGE_REVERSED",
                f"working end {end} must be greater than start {start}")
    if end > duration + float(tolerance_s):
        _reject("WORKING_RANGE_BEYOND_WORKING_SOURCE",
                f"working range {start}-{end}s exceeds the working source "
                f"({duration}s, tolerance {tolerance_s}s)")
    return {"start_s": start, "end_s": end, "working_duration_s": duration,
            "clip_duration_s": round(end - start, 3)}


def ytdlp_section_arg(window: dict) -> str:
    """yt-dlp/ffmpeg download-section expression for the approved interval."""
    return f"*{window['start_ms'] / 1000:.3f}-{window['end_ms'] / 1000:.3f}"


def parse_clock(text: str) -> int:
    """Parse '30', '0:30', '00:30', '1:02:03', or '30.5' into milliseconds."""
    raw = str(text or "").strip()
    if not raw:
        _reject("BAD_TIMESTAMP", "empty timestamp")
    if not re.fullmatch(r"\d+(?::\d{1,2}){0,2}(?:\.\d+)?", raw):
        _reject("BAD_TIMESTAMP", f"unrecognized timestamp {text!r}")
    parts = raw.split(":")
    seconds = 0.0
    for part in parts:
        seconds = seconds * 60 + float(part)
    return int(round(seconds * 1000.0))
