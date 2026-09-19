"""Approved acquisition lane — two human gates, one bounded job per gate.

Gate 1 — QUALIFY (Approve / ``approved_for_acquisition``):
    qualifies a source and creates **exactly one pending** acquisition job.
    Creates no spend authorization; may be repeated safely (idempotent).

Gate 2 — AUTHORIZE (Acquire for Reaction / ``acquire_for_reaction``):
    an explicit human act that creates **exactly one spend-authorized** job and
    is the ONLY thing the runner will dispatch.

The two gates land in two rows because ``loom_acquisition_jobs.dedup_key`` is
derived from ``(fingerprint, authorization_type, mode, range)`` — the existing
schema already models "qualification" and "spend authorization" as distinct,
independently idempotent records. Gate 2 references gate 1 through
``correlation_id``. **No migration is required.**

Defence in depth (enforced here, not only in the DB):

* ``require_spend_authorization()`` refuses any job whose ``authorization_type``
  is not ``acquire_for_reaction``; the runner calls it before dispatch, so a
  pending (gate 1) job can never reach the provider.
* Pilot bounds are clamped DOWN to the provider contract (≤100 MiB, ≤5 min).
  A job asking for more is clamped and flagged; it is never widened.
* Exactly one URL, one item, one attempt. No fallback actor, no automatic retry.

Presentation lives in ``acquisition_card.py``; money and media live in
``provider.py`` / ``cost.py`` / ``orchestrate.py``. This module holds only the
gate logic in between.
"""

from __future__ import annotations

import re

from typing import Any

import acquisition
import platform_capabilities as caps
import provider

# ---------------------------------------------------------------------------
# Pilot bounds (mirror the provider contract; clamped, never widened)
# ---------------------------------------------------------------------------
PILOT_MAX_BYTES: int = provider.MAX_BYTES                    # 100 MiB
PILOT_MAX_DURATION_SECONDS: float = provider.MAX_DURATION_SECONDS  # 300 s
PILOT_MAX_RESULTS: int = provider.MAX_RESULTS                # 1 item
PILOT_MAX_ATTEMPTS: int = 1                                  # one attempt

DESTINATION_PERSONAL_REACTION_LIBRARY = acquisition.DESTINATION_PERSONAL_REACTION_LIBRARY

GATE_QUALIFY = "qualify"
GATE_AUTHORIZE = "authorize"

# The pinned provider adapter per platform. NOTE the deliberate separation:
# ``job["actor"]`` records the HUMAN who authorized the acquisition (audit),
# while the provider call uses this pinned adapter identity. Conflating them
# would feed a person's name into the actor allowlist.
DEFAULT_PROVIDER_ACTORS: dict[str, str] = {
    "instagram": provider.ACTOR_REEL_SCRAPER,
    # TikTok's pinned provider is the proven Apidojo actor. Clockworks is NOT here
    # and is not in the actor allowlist either, so it cannot be auto-selected.
    "tiktok": provider.ACTOR_TIKTOK_VIDEO,
}

# Gate-1 jobs are queued but explicitly NOT spend-authorized. The reason string
# is the human-readable marker; enforcement is `require_spend_authorization`.
REASON_PENDING_AUTHORIZATION = "pending_acquire_authorization"


class LaneError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message


# ---------------------------------------------------------------------------
# Gate 1 — qualify → exactly one pending job (no spend, no download)
# ---------------------------------------------------------------------------
def plan_pending_job(
    *,
    fingerprint: str,
    platform: str,
    platform_content_id: str,
    canonical_url: str,
    actor: str,
    destination: str = DESTINATION_PERSONAL_REACTION_LIBRARY,
    rights_usage_note: str = "",
    correlation_id: str = "",
    requested_start_s: float | None = None,
    requested_end_s: float | None = None,
) -> dict[str, Any]:
    """Qualify a source and plan its single pending acquisition job.

    Never authorizes spending. ``max_bytes``/``max_duration_seconds`` are set to
    the pilot bounds so the eventual gate-2 job inherits safe ceilings.
    """
    job = acquisition.plan_acquisition_job(
        fingerprint=fingerprint,
        platform=platform,
        platform_content_id=platform_content_id,
        canonical_url=canonical_url,
        authorization_type=acquisition.AUTH_APPROVED_FOR_ACQUISITION,
        actor=actor,
        destination=destination,
        acquisition_mode=(acquisition.MODE_BOUNDED_SEGMENT
                          if requested_start_s is not None and requested_end_s is not None
                          else acquisition.MODE_FULL_ITEM),
        start_s=requested_start_s,
        end_s=requested_end_s,
        max_bytes=PILOT_MAX_BYTES,
        max_duration_seconds=PILOT_MAX_DURATION_SECONDS,
        rights_usage_note=rights_usage_note,
        correlation_id=correlation_id,
    )
    if job["status"] == acquisition.STATUS_QUEUED:
        job["status_reason"] = REASON_PENDING_AUTHORIZATION
    return job


def plan_authorized_job(
    *,
    pending_job: dict[str, Any],
    actor: str,
    destination: str | None = None,
    rights_usage_note: str = "",
    requested_start_s: float | None = None,
    requested_end_s: float | None = None,
) -> dict[str, Any]:
    """Gate 2: turn an approved source into exactly one spend-authorized job.

    Requires the gate-1 pending job to exist — acquiring is never the first act.
    """
    if not pending_job:
        raise LaneError("NO_PENDING_JOB",
                        "acquire requires an approved (pending) job for this source")
    if pending_job.get("authorization_type") != acquisition.AUTH_APPROVED_FOR_ACQUISITION:
        raise LaneError("BAD_PENDING_JOB",
                        "pending job must carry authorization_type=approved_for_acquisition")

    fingerprint = pending_job.get("duplicate_fingerprint") or ""
    job = acquisition.plan_acquisition_job(
        fingerprint=fingerprint,
        platform=pending_job.get("platform") or "",
        platform_content_id=pending_job.get("platform_content_id") or "",
        canonical_url=pending_job.get("canonical_url") or "",
        authorization_type=acquisition.AUTH_ACQUIRE_FOR_REACTION,
        actor=actor,
        destination=(destination or pending_job.get("destination")
                     or DESTINATION_PERSONAL_REACTION_LIBRARY),
        acquisition_mode=(acquisition.MODE_BOUNDED_SEGMENT
                          if (requested_start_s if requested_start_s is not None
                              else pending_job.get("requested_start_s")) is not None
                          and (requested_end_s if requested_end_s is not None
                               else pending_job.get("requested_end_s")) is not None
                          else acquisition.MODE_FULL_ITEM),
        start_s=(requested_start_s if requested_start_s is not None
                 else pending_job.get("requested_start_s")),
        end_s=(requested_end_s if requested_end_s is not None
               else pending_job.get("requested_end_s")),
        max_bytes=PILOT_MAX_BYTES,
        max_duration_seconds=PILOT_MAX_DURATION_SECONDS,
        rights_usage_note=rights_usage_note or pending_job.get("rights_usage_note", ""),
        correlation_id=pending_job.get("dedup_key", ""),
    )
    return job


# ---------------------------------------------------------------------------
# Enforcement
# ---------------------------------------------------------------------------
def is_spend_authorized(job: dict[str, Any] | None) -> bool:
    """True only for a gate-2 job in a dispatchable status."""
    if not job:
        return False
    if job.get("authorization_type") != acquisition.AUTH_ACQUIRE_FOR_REACTION:
        return False
    return job.get("status") in (acquisition.STATUS_QUEUED, acquisition.STATUS_RUNNING)


def require_spend_authorization(job: dict[str, Any] | None) -> None:
    """Raise unless the job carries an explicit gate-2 spend authorization."""
    if not job:
        raise LaneError("NO_JOB", "no acquisition job")
    if job.get("authorization_type") != acquisition.AUTH_ACQUIRE_FOR_REACTION:
        raise LaneError(
            "NOT_SPEND_AUTHORIZED",
            "job is qualified but not spend-authorized; press Acquire for Reaction")
    if job.get("status") not in (acquisition.STATUS_QUEUED, acquisition.STATUS_RUNNING):
        raise LaneError("BAD_JOB_STATUS",
                        f"job status {job.get('status')!r} is not dispatchable")


def enforce_pilot_bounds(job: dict[str, Any]) -> dict[str, Any]:
    """Clamp the job to the pilot bounds. Returns patched fields + a flag.

    A job that requests more than the pilot bound is clamped (never widened); a
    job that requests less keeps its tighter bound.
    """
    patch: dict[str, Any] = {}
    clamped = False

    mb = job.get("max_bytes")
    if mb is None or not isinstance(mb, (int, float)) or mb <= 0 or mb > PILOT_MAX_BYTES:
        patch["max_bytes"] = PILOT_MAX_BYTES
        clamped = clamped or (mb is not None and isinstance(mb, (int, float)) and mb > PILOT_MAX_BYTES)

    md = job.get("max_duration_seconds")
    if md is None or not isinstance(md, (int, float)) or md <= 0 or md > PILOT_MAX_DURATION_SECONDS:
        patch["max_duration_seconds"] = PILOT_MAX_DURATION_SECONDS
        clamped = clamped or (md is not None and isinstance(md, (int, float))
                              and md > PILOT_MAX_DURATION_SECONDS)

    return {"patch": patch, "clamped": clamped}


def default_provider_actor(platform: str) -> str:
    """Pinned adapter identity for a platform (never a human name)."""
    actor = DEFAULT_PROVIDER_ACTORS.get((platform or "").lower())
    if not actor:
        raise LaneError("NO_PROVIDER_ACTOR",
                        f"no pinned provider actor for platform {platform!r}")
    return actor


def require_runtime_proven(job: dict[str, Any]) -> None:
    """Only a runtime-proven platform adapter may be dispatched."""
    platform = job.get("platform") or ""
    if caps.can_acquire(platform) or acquisition._implemented_local_free(platform):
        return
    if caps.proof_mode_allows(platform):
        return          # bounded adapter proof in progress; state flips only on success
    raise LaneError("NOT_RUNTIME_PROVEN",
                    f"{platform or 'platform'} is not runtime-proven: "
                    f"{caps.capability_reason(platform)}")


def attempts_allowed(job: dict[str, Any], *, retry_authorized: bool = False) -> bool:
    """One attempt. A retry only after an explicit, separate authorization."""
    used = int(job.get("retry_count") or 0)
    if used == 0:
        return True
    return bool(retry_authorized) and used < PILOT_MAX_ATTEMPTS + 1


def next_status_after_result(ok: bool) -> str:
    return acquisition.STATUS_ACQUIRED if ok else acquisition.STATUS_FAILED


# ---------------------------------------------------------------------------
# Already-acquired protection
# ---------------------------------------------------------------------------
RANGE_MATCH_TOLERANCE_MS = 500


def row_matches_range(row: dict[str, Any] | None,
                      window: dict[str, Any] | None) -> bool:
    """True when a job row belongs to the ACTIVE range.

    With an active window, only a row carrying the same bounds qualifies; with no
    window, only an unranged row does. This is what stops an older range's status
    from deciding the current card's state.
    """
    if not isinstance(row, dict):
        return False
    s, e = row.get("requested_start_s"), row.get("requested_end_s")
    if not window:
        return s is None and e is None
    if s is None or e is None or window.get("start_ms") is None:
        return False
    return (abs(float(s) * 1000.0 - float(window["start_ms"])) <= RANGE_MATCH_TOLERANCE_MS
            and abs(float(e) * 1000.0 - float(window["end_ms"])) <= RANGE_MATCH_TOLERANCE_MS)


def already_acquired(library_rows: list[dict[str, Any]] | None, *,
                     content_id: str, window: dict[str, Any] | None = None) -> bool:
    """Range-aware repeat-acquisition test.

    When a window is approved, identity is the RANGE fingerprint: one long-form
    source may legitimately yield several distinct library assets, while the same
    source+range stays idempotent. Without a window, the whole item is the unit.
    """
    fingerprint = str(((window or {}).get("range_fingerprint")) or "")
    if fingerprint:
        return library_has_content(library_rows, fingerprint=fingerprint)
    return library_has_content(library_rows, platform_content_id=content_id)


def library_has_content(library_rows: list[dict[str, Any]] | None, *,
                        platform_content_id: str = "",
                        fingerprint: str = "") -> bool:
    """True when this item is already in the Reaction Library.

    Prevents a repeat acquisition of an already-acquired item. Identity is the
    platform content id (preferred) or the duplicate fingerprint.
    """
    for row in library_rows or []:
        if not isinstance(row, dict):
            continue
        if platform_content_id and str(row.get("platform_content_id") or "") == platform_content_id:
            return True
        if fingerprint and str(row.get("duplicate_fingerprint") or "") == fingerprint:
            return True
    return False


# ---------------------------------------------------------------------------
# Persistence hygiene
# ---------------------------------------------------------------------------
# Value-level markers: a value containing any of these is a signature/credential
# carrier and is dropped outright (it is never partially rewritten).
_UNSAFE_MARKERS = ("sig=", "oh=", "token=", "signature=", "x-amz-", "key=",
                   "cookie", "authorization", "bearer ")

# Credential-shaped KEYS. Note ``authorization_type`` is a legitimate schema
# column (it records which human gate authorized the job) and must survive —
# only actual credential carriers are dropped.
_CREDENTIAL_KEY_EXACT = frozenset({
    "authorization", "authorization_header", "auth", "bearer", "cookie", "cookies",
    "token", "api_key", "apikey", "secret", "password", "headers",
})
_CREDENTIAL_KEY_SUBSTR = ("token", "cookie", "secret", "apikey", "api_key", "password")


def _is_credential_key(key: str) -> bool:
    k = str(key).lower()
    if k in _CREDENTIAL_KEY_EXACT:
        return True
    if "authorization" in k:
        return not k.endswith("_type")          # authorization_type is data, not a secret
    return any(m in k for m in _CREDENTIAL_KEY_SUBSTR)


_YT_WATCH_RE = re.compile(r"^https://(?:www\.)?youtube\.com/watch$")
_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


def _strip_query_keep_identity(url: str) -> str:
    """Drop query/fragment but KEEP a video's identity query.

    A YouTube watch URL's ``?v=`` IS the video: stripping it would turn stored
    provenance into a link to nothing. Only a single 11-character ``v=``
    survives; tracking params, signatures and tokens are still removed.
    """
    import urllib.parse as _up
    head, _, query = url.split("#", 1)[0].partition("?")
    if _YT_WATCH_RE.match(head):
        for k, val in _up.parse_qsl(query, keep_blank_values=True):
            if k == "v" and _VIDEO_ID_RE.match(val):
                return f"{head}?v={val}"
        return head
    return head


def safe_record(payload: dict[str, Any]) -> dict[str, Any]:
    """Strip query strings and drop any credential-shaped field.

    Applied before anything is persisted or logged, so a signed CDN URL,
    cookie, or auth header cannot reach a record.
    """
    out: dict[str, Any] = {}
    for k, v in (payload or {}).items():
        if _is_credential_key(k):
            continue
        if isinstance(v, str):
            low = v.lower()
            if any(m in low for m in _UNSAFE_MARKERS):
                continue
            if "://" in v:
                v = _strip_query_keep_identity(v)
        out[k] = v
    return out
