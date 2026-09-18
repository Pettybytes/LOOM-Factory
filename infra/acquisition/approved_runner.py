"""Bounded approved-job runner (local Mac).

The ONLY component that may dispatch a paid provider run. It refuses anything
that is not an explicit gate-2 authorization, clamps the pilot bounds, performs
exactly ONE attempt, and never falls back or retries automatically.

All I/O is injected (``transport`` / ``downloader`` / ``finalizer``), so the
whole path is exercised with mocks. The real wiring passes the Apify transport
and the local bounded downloader; a real call remains an explicit, separate
authorization and is NOT performed by this module's tests.

Guarantees enforced here (and asserted by tests):
  * one exact URL          — the job's canonical URL only; no discovery, no expansion
  * one item               — provider.build_input pins maxResults = 1
  * ≤ 100 MiB              — clamped down, never widened
  * ≤ 5 minutes            — clamped down, never widened
  * one attempt            — retry only with a separate, explicit authorization
  * no fallback            — inactive/fallback actors are refused
  * no secret persistence  — records pass through approved_lane.safe_record
"""

from __future__ import annotations

from typing import Any, Callable

import acquisition
import approved_lane as lane
import cost
import provider

# Statuses that mean "this job already finished — do not run it again".
_TERMINAL_STATUSES = frozenset({acquisition.STATUS_ACQUIRED, acquisition.STATUS_FAILED})


class RunnerError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message


def run_approved_job(
    *,
    job: dict[str, Any] | None,
    lifetime_spent_micro: int,
    acquisitions_succeeded: int,
    already_dispatched: bool = False,
    retry_authorized: bool = False,
    transport: Any,
    downloader: Callable[[str], dict[str, Any]],
    finalizer: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]],
    dispatch: Callable[..., dict[str, Any]],
    estimate_micro: int = cost.REEL_SCRAPER_RESULT_MICRO,
    is_exact: bool = True,
) -> dict[str, Any]:
    """Run ONE bounded acquisition attempt for an authorized job.

    Returns a structured outcome; the caller persists it. Nothing is persisted
    here, so a dry run is safe and the whole path is testable.
    """
    # --- Gate checks (before anything can spend) --------------------------
    if not job:
        raise RunnerError("NO_JOB", "no acquisition job")
    if job.get("status") in _TERMINAL_STATUSES:
        raise RunnerError("ALREADY_FINISHED",
                          f"job is already {job.get('status')}; repeat acquisition refused")
    lane.require_spend_authorization(job)
    if not lane.attempts_allowed(job, retry_authorized=retry_authorized):
        raise RunnerError("ATTEMPTS_EXHAUSTED",
                          "one attempt only; a retry needs a separate explicit authorization")
    lane.require_runtime_proven(job)

    # One exact URL, exactly as authorized.
    canonical_url = (job.get("canonical_url") or "").strip()
    if not canonical_url:
        raise RunnerError("NO_URL", "authorized job carries no canonical URL")
    if "?" in canonical_url or "#" in canonical_url:
        canonical_url = canonical_url.split("#", 1)[0].split("?", 1)[0]

    content_id = (job.get("platform_content_id") or "").strip()
    if not content_id:
        raise RunnerError("AMBIGUOUS_COLLECTION", "no single-item content id")

    if already_dispatched:
        raise RunnerError("ALREADY_DISPATCHED", "provider run already exists for this job")

    # --- Bounds (clamped down, never widened) -----------------------------
    bounds = lane.enforce_pilot_bounds(job)
    effective = dict(job)
    effective.update(bounds["patch"])
    # Keep the job and the dispatched URL identical: provider.verify_authorization
    # requires the authorized URL to match exactly, so the stripped URL must be
    # the one carried on the job too.
    effective["canonical_url"] = canonical_url

    actor = lane.default_provider_actor(job.get("platform") or "")
    try:
        active = provider.actor_active(job.get("platform") or "", actor)
    except provider.ProviderError as e:
        # Refuse rather than silently falling back to another actor.
        raise RunnerError(e.code, e.message) from e
    if not active:
        # The configured fallback actor is INACTIVE and is never auto-called:
        # a fallback could create a second charge.
        raise RunnerError("ACTOR_INACTIVE",
                          f"actor {actor!r} is not active for this platform; no fallback allowed")

    # --- One dispatch -----------------------------------------------------
    result = dispatch(
        job=effective,
        canonical_url=canonical_url,
        content_id=content_id,
        platform=job.get("platform") or "",
        actor=actor,
        lifetime_spent_micro=lifetime_spent_micro,
        acquisitions_succeeded=acquisitions_succeeded,
        already_dispatched=False,
        transport=transport,
        downloader=downloader,
        finalizer=finalizer,
        estimate_micro=estimate_micro,
        is_exact=is_exact,
    )

    ok = bool(result.get("ok"))
    status = lane.next_status_after_result(ok)

    outcome: dict[str, Any] = {
        "ok": ok,
        "status": status,
        "attempts": int(job.get("retry_count") or 0) + 1,
        "bounds_clamped": bounds["clamped"],
        "effective_max_bytes": effective["max_bytes"],
        "effective_max_duration_seconds": effective["max_duration_seconds"],
        "max_results": lane.PILOT_MAX_RESULTS,
        "fallback_used": False,
        "auto_retry": False,
        "job_patch": {
            "status": status,
            "retry_count": int(job.get("retry_count") or 0) + 1,
            "max_bytes": effective["max_bytes"],
            "max_duration_seconds": effective["max_duration_seconds"],
            "status_reason": "" if ok else str(result.get("error") or "failed"),
        },
    }
    if ok:
        outcome["library"] = lane.safe_record(result.get("library") or {})
        outcome["cost"] = {
            "reserved_micro": result.get("reserved_micro"),
            "actual_micro": result.get("actual_micro"),
        }
        outcome["provenance"] = lane.safe_record(result.get("provenance") or {})
    else:
        outcome["error"] = {"code": result.get("error"),
                            "message": result.get("message", "")}
    return outcome


def describe_bounds(job: dict[str, Any]) -> str:
    """Human-readable bound summary (for the card / logs)."""
    b = lane.enforce_pilot_bounds(job)
    eff = dict(job)
    eff.update(b["patch"])
    minutes = float(eff["max_duration_seconds"]) / 60.0
    mib = float(eff["max_bytes"]) / (1024 * 1024)
    return (f"1 URL · 1 item · ≤{mib:.0f} MiB · ≤{minutes:.0f} min · "
            f"1 attempt · no fallback{' (clamped)' if b['clamped'] else ''}")
