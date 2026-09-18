"""Acquisition Boundary V1 — durable, idempotent acquisition-job contract.

An acquisition job is the ONLY way a source becomes an acquired asset. It is
created ONLY by:
  * an explicit ``acquire_for_reaction`` human action, or
  * a Source Review ``approved_for_acquisition`` decision.

Creating a job records authorization and produces a status — it NEVER downloads
media here. Actual download happens only for ``runtime_proven`` platform adapters
under bounded byte/time ceilings, and is a SEPARATE authorized step.

Statuses:
  * queued                         — eligible to acquire (adapter runtime-proven)
  * running                        — acquisition in progress
  * acquired                       — asset stored in the Reaction Library
  * blocked_pending_platform_adapter — authorization recorded, no adapter yet
  * failed                         — terminal failure (reason recorded)

Idempotency: a deterministic ``dedup_key`` (fingerprint + authorization type +
mode + requested range) prevents duplicate jobs from double-click / redelivery.

Rights: creating an authorization job does NOT authorize publishing or
distribution. That is a separate, audited decision.
"""

from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

import platform_capabilities as caps

AUTH_ACQUIRE_FOR_REACTION = "acquire_for_reaction"
AUTH_APPROVED_FOR_ACQUISITION = "approved_for_acquisition"
AUTHORIZATION_TYPES = frozenset({AUTH_ACQUIRE_FOR_REACTION, AUTH_APPROVED_FOR_ACQUISITION})

MODE_FULL_ITEM = "full_item"
MODE_BOUNDED_SEGMENT = "bounded_segment"
ACQUISITION_MODES = frozenset({MODE_FULL_ITEM, MODE_BOUNDED_SEGMENT})

STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_ACQUIRED = "acquired"
STATUS_BLOCKED_PENDING_ADAPTER = "blocked_pending_platform_adapter"
STATUS_FAILED = "failed"
JOB_STATUSES = frozenset({
    STATUS_QUEUED, STATUS_RUNNING, STATUS_ACQUIRED,
    STATUS_BLOCKED_PENDING_ADAPTER, STATUS_FAILED,
})

DESTINATION_PERSONAL_REACTION_LIBRARY = "personal_reaction_library"

# Ceilings (bounding the eventual download; enforced by the adapter at run time).
DEFAULT_MAX_BYTES = 2 * 1024 * 1024 * 1024   # 2 GiB
DEFAULT_MAX_DURATION_SECONDS = 20 * 60        # 20 minutes

# A single-item acquisition requires a deterministically-known platform content
# id. Playlists / feeds / profiles / channels / ambiguous collections have no
# single content id and are rejected for acquisition (they may still be saved
# as link-only candidates).
_NO_CONTENT_ID_REASON = "no single-item content id (playlist/feed/profile/channel/ambiguous collection)"


class AcquisitionError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message


def dedup_key(fingerprint: str, authorization_type: str, mode: str,
              start_s: float | None, end_s: float | None) -> str:
    """Deterministic idempotency key (stable across NULL ranges)."""
    raw = f"{fingerprint}\x1f{authorization_type}\x1f{mode}\x1f{start_s}\x1f{end_s}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _to_num(v: float | int | str | None) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        raise AcquisitionError("BAD_RANGE", f"range value {v!r} is not numeric")


def _norm_range(start_s, end_s) -> tuple[float | None, float | None]:
    s = _to_num(start_s)
    e = _to_num(end_s)
    if s is not None and s < 0:
        raise AcquisitionError("BAD_RANGE", "start_s must be >= 0")
    if e is not None and e < 0:
        raise AcquisitionError("BAD_RANGE", "end_s must be >= 0")
    if s is not None and e is not None and e <= s:
        raise AcquisitionError("BAD_RANGE", "end_s must be > start_s")
    return s, e


def plan_acquisition_job(
    *,
    fingerprint: str,
    platform: str,
    platform_content_id: str,
    canonical_url: str,
    authorization_type: str,
    actor: str,
    destination: str = DESTINATION_PERSONAL_REACTION_LIBRARY,
    acquisition_mode: str = MODE_FULL_ITEM,
    start_s: float | None = None,
    end_s: float | None = None,
    max_bytes: int = DEFAULT_MAX_BYTES,
    max_duration_seconds: float = DEFAULT_MAX_DURATION_SECONDS,
    rights_usage_note: str = "",
    correlation_id: str = "",
) -> dict[str, Any]:
    """Validate + plan a deterministic acquisition job (NO download).

    Returns a full job payload. Status is ``queued`` only when the platform
    adapter is runtime-proven; otherwise ``blocked_pending_platform_adapter``
    (authorization is still recorded, so the source need not be rediscovered or
    re-approved once the adapter is enabled)."""
    if authorization_type not in AUTHORIZATION_TYPES:
        raise AcquisitionError("BAD_AUTHORIZATION",
                               f"unknown authorization_type {authorization_type!r}")
    if platform not in caps.PLATFORMS:
        raise AcquisitionError("BAD_PLATFORM", f"unknown platform {platform!r}")
    if acquisition_mode not in ACQUISITION_MODES:
        raise AcquisitionError("BAD_MODE", f"unknown acquisition_mode {acquisition_mode!r}")

    # Single-item only: reject playlists/feeds/profiles/channels/ambiguous collections.
    content_id = (platform_content_id or "").strip()
    if not content_id:
        raise AcquisitionError("AMBIGUOUS_COLLECTION", _NO_CONTENT_ID_REASON)

    s, e = _norm_range(start_s, end_s)
    if acquisition_mode == MODE_BOUNDED_SEGMENT and (s is None or e is None):
        raise AcquisitionError("BAD_RANGE",
                               "bounded_segment requires both start_s and end_s")

    dest = (destination or "").strip() or DESTINATION_PERSONAL_REACTION_LIBRARY

    # A platform whose adapter proof is explicitly in progress may plan a
    # dispatchable job; this grants no capability claim of its own.
    if caps.can_acquire(platform) or caps.proof_mode_allows(platform):
        status, reason = STATUS_QUEUED, ""
    else:
        status, reason = STATUS_BLOCKED_PENDING_ADAPTER, caps.capability_reason(platform)

    return {
        "dedup_key": dedup_key(fingerprint, authorization_type, acquisition_mode, s, e),
        "duplicate_fingerprint": fingerprint,
        "platform": platform,
        "platform_content_id": content_id,
        "canonical_url": canonical_url,
        "authorization_type": authorization_type,
        "actor": actor,
        "destination": dest,
        "acquisition_mode": acquisition_mode,
        "requested_start_s": s,
        "requested_end_s": e,
        "status": status,
        "status_reason": reason,
        "retry_count": 0,
        "max_bytes": max_bytes,
        "max_duration_seconds": max_duration_seconds,
        "asset_provenance": None,
        "rights_usage_note": (rights_usage_note or "").strip(),
        "correlation_id": (correlation_id or "").strip(),
    }


# ---------------------------------------------------------------------------
# Supabase I/O (url + key explicit; no secret logging). NOT exercised by unit
# tests — used by the Discord adapter when "Acquire for Reaction" is pressed.
# ---------------------------------------------------------------------------
def _headers(key: str, body: bool = False) -> dict[str, str]:
    h = {"apikey": key, "Authorization": f"Bearer {key}", "User-Agent": "LOOM-Acquisition/1.0"}
    if body:
        h["Content-Type"] = "application/json"
        h["Prefer"] = "resolution=ignore-duplicates,return=representation"
    return h


def persist_job(url: str, key: str, job: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Idempotently persist an acquisition job (first-wins on dedup_key)."""
    req = urllib.request.Request(
        f"{url}/rest/v1/loom_acquisition_jobs?on_conflict=dedup_key",
        data=json.dumps(job).encode(),
        headers=_headers(key, body=True),
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            rows = json.loads(r.read().decode() or "[]")
    except urllib.error.HTTPError as e:
        raise AcquisitionError("STORE_ERROR", f"HTTP {e.code}: {e.read().decode()[:200]}") from e
    if isinstance(rows, list) and rows:
        return rows[0], True
    # Already exists — return the existing row.
    q = urllib.parse.quote(job["dedup_key"])
    req2 = urllib.request.Request(
        f"{url}/rest/v1/loom_acquisition_jobs?dedup_key=eq.{q}&select=*&limit=1",
        headers=_headers(key),
    )
    with urllib.request.urlopen(req2, timeout=30) as r:
        existing = json.loads(r.read().decode() or "[]")
    if isinstance(existing, list) and existing:
        return existing[0], False
    return job, True
