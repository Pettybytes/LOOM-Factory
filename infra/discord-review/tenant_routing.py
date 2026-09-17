"""Tenant routing — assign / reassign / copy for Manual Source Intake.

Controlled tenant assignment with an immutable audit trail. Destinations are
limited to active production tenants (from ``loom_tenants``) plus the Unassigned
routing state. The original submission's tenant-neutral data (canonical URL,
platform, submitter, provenance) is never rewritten; routing is recorded as
append-only ``loom_routing_events``.

Operations:
  * assign    — unassigned (tenant_id='') -> active tenant.
  * reassign  — tenant A -> tenant B (blocked if downstream work started).
  * copy      — create a separate tenant-scoped candidate for another tenant.

Requalification: every routing change sets ``qualification_required = true`` —
tenant-specific qualification/assessment/score is NEVER carried across tenants,
and is never fabricated (the pipeline is not yet wired, so no requalification
runs).

Pure decision logic is separated (``plan_routing``) so it is fully unit-testable
without Supabase. I/O helpers take url+key explicitly (no secret logging).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from manual_intake import (
    DESTINATION_UNASSIGNED,
    STATUS_ACCEPTED,
    STATUS_PENDING_PLATFORM,
    STATUS_AWAITING_TENANT,
    SUPPORTED_PLATFORMS,
    SubmissionError,
    is_non_production_tenant,
    validate_destination,
    persist_submission,
)

OP_ASSIGN = "assign"
OP_REASSIGN = "reassign"
OP_COPY = "copy"
OPERATIONS = frozenset({OP_ASSIGN, OP_REASSIGN, OP_COPY})


class RoutingError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message


# ---------------------------------------------------------------------------
# Pure decision logic (unit-testable, no I/O)
# ---------------------------------------------------------------------------
def plan_routing(
    operation: str,
    old_destination: str,
    new_destination: str,
    active_tenants: set[str],
    *,
    downstream_started: bool = False,
    collision: bool = False,
) -> dict[str, str]:
    """Decide the routing action. Returns {'action', 'destination', 'reason'}.

    action is one of: assign | reassign | copy | collision | blocked | noop.
    Raises RoutingError for an invalid operation or invalid destination."""
    if operation not in OPERATIONS:
        raise RoutingError("BAD_OPERATION", f"unknown operation {operation!r}")
    old = (old_destination or "").strip()
    try:
        new = validate_destination(new_destination, active_tenants)
    except SubmissionError as e:
        raise RoutingError(e.code, e.message) from e

    if operation == OP_COPY:
        if new == old:
            return {"action": "noop", "destination": new, "reason": "same destination"}
        return {"action": OP_COPY, "destination": new, "reason": ""}

    if operation == OP_REASSIGN:
        if downstream_started:
            return {"action": "blocked", "destination": old, "reason": "downstream work already started"}
        if collision:
            return {"action": "collision", "destination": new, "reason": "already exists for that tenant"}
        if new == old:
            return {"action": "noop", "destination": new, "reason": "same destination"}
        return {"action": OP_REASSIGN, "destination": new, "reason": ""}

    # assign
    if collision:
        return {"action": "collision", "destination": new, "reason": "already exists for that tenant"}
    if new == DESTINATION_UNASSIGNED:
        return {"action": "noop", "destination": new, "reason": "already unassigned"}
    return {"action": OP_ASSIGN, "destination": new, "reason": ""}


def decide_operation(old_destination: str, explicit: str | None = None) -> str:
    """Derive operation: copy if explicit, else assign (unassigned) or reassign."""
    if explicit in OPERATIONS:
        return explicit
    old = (old_destination or "").strip()
    return OP_ASSIGN if old == DESTINATION_UNASSIGNED else OP_REASSIGN


def build_routing_event(
    fingerprint: str,
    old_destination: str,
    new_destination: str,
    operation: str,
    actor: str,
    reason: str = "",
    correlation_id: str = "",
) -> dict[str, Any]:
    return {
        "duplicate_fingerprint": fingerprint,
        "old_destination": (old_destination or "").strip(),
        "new_destination": (new_destination or "").strip(),
        "operation": operation,
        "actor": actor,
        "reason": (reason or "").strip(),
        "correlation_id": (correlation_id or "").strip(),
    }


# ---------------------------------------------------------------------------
# Supabase I/O (url + key explicit)
# ---------------------------------------------------------------------------
def _headers(key: str, body: bool = False) -> dict[str, str]:
    h = {"apikey": key, "Authorization": f"Bearer {key}", "User-Agent": "LOOM-TenantRouting/1.0"}
    if body:
        h["Content-Type"] = "application/json"
    return h


def _get(url: str, key: str, path: str) -> Any:
    req = urllib.request.Request(f"{url}/rest/v1/{path}", headers=_headers(key))
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode() or "[]")
    except urllib.error.HTTPError as e:
        raise RoutingError("STORE_ERROR", f"HTTP {e.code}: {e.read().decode()[:200]}") from e


def list_active_tenants(url: str, key: str) -> set[str]:
    rows = _get(url, key, "loom_tenants?status=eq.active&select=tenant_id")
    if not isinstance(rows, list):
        return set()
    return {r["tenant_id"] for r in rows if isinstance(r, dict)
            and r.get("tenant_id") and not is_non_production_tenant(r["tenant_id"])}


def fetch_submission(url: str, key: str, fingerprint: str, tenant_id: str) -> dict[str, Any] | None:
    q_fp = urllib.parse.quote(fingerprint)
    q_t = urllib.parse.quote(tenant_id)
    rows = _get(url, key,
                f"loom_manual_submissions?duplicate_fingerprint=eq.{q_fp}&tenant_id=eq.{q_t}&select=*&limit=1")
    return rows[0] if isinstance(rows, list) and rows else None


def is_downstream_started(url: str, key: str, fingerprint: str) -> bool:
    """True if acquisition/editing/publishing has begun for this source.
    (Checks approved-for-acquisition review decisions; media/renders later.)"""
    q = urllib.parse.quote(fingerprint)
    rows = _get(url, key,
                f"loom_source_reviews?duplicate_fingerprint=eq.{q}&decision=eq.approved_for_acquisition&select=id&limit=1")
    return bool(isinstance(rows, list) and rows)


def record_routing_event(url: str, key: str, event: dict[str, Any]) -> None:
    req = urllib.request.Request(
        f"{url}/rest/v1/loom_routing_events",
        data=json.dumps(event).encode(),
        headers=_headers(key, body=True) | {"Prefer": "return=representation"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            r.read()
    except urllib.error.HTTPError as e:
        raise RoutingError("STORE_ERROR", f"HTTP {e.code}: {e.read().decode()[:200]}") from e


def _update_submission_destination(url: str, key: str, fingerprint: str, old_dest: str,
                                   new_dest: str, status: str) -> None:
    q_fp = urllib.parse.quote(fingerprint)
    q_old = urllib.parse.quote(old_dest)
    body = {"tenant_id": new_dest, "status": status, "qualification_required": True}
    req = urllib.request.Request(
        f"{url}/rest/v1/loom_manual_submissions?duplicate_fingerprint=eq.{q_fp}&tenant_id=eq.{q_old}",
        data=json.dumps(body).encode(),
        headers=_headers(key, body=True) | {"Prefer": "return=representation"},
        method="PATCH",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            r.read()
    except urllib.error.HTTPError as e:
        raise RoutingError("STORE_ERROR", f"HTTP {e.code}: {e.read().decode()[:200]}") from e


def _status_for(url: str, key: str, platform: str, new_dest: str) -> str:
    if new_dest == DESTINATION_UNASSIGNED:
        return STATUS_AWAITING_TENANT
    return STATUS_ACCEPTED if platform in SUPPORTED_PLATFORMS else STATUS_PENDING_PLATFORM


def route_submission(
    url: str,
    key: str,
    fingerprint: str,
    old_destination: str,
    new_destination: str,
    operation: str,
    actor: str,
    reason: str = "",
    correlation_id: str = "",
) -> dict[str, Any]:
    """Execute one routing operation. Returns {'action', 'destination', 'collision_id', 'reason'}."""
    active = list_active_tenants(url, key)
    plan = plan_routing(operation, old_destination, new_destination, active,
                        downstream_started=is_downstream_started(url, key, fingerprint),
                        collision=fetch_submission(url, key, fingerprint, new_destination) is not None)

    event = build_routing_event(fingerprint, old_destination, new_destination, operation,
                                actor, reason, correlation_id)

    action = plan["action"]
    if action in ("collision", "blocked", "noop"):
        record_routing_event(url, key, event) if action != "noop" else None
        existing = fetch_submission(url, key, fingerprint, new_destination) if action == "collision" else None
        return {"action": action, "destination": plan["destination"],
                "collision_id": (existing or {}).get("id"), "reason": plan["reason"]}

    if action == OP_COPY:
        # Copy: insert a new tenant-scoped candidate (original untouched).
        src = fetch_submission(url, key, fingerprint, old_destination)
        if not src:
            raise RoutingError("NOT_FOUND", f"source {fingerprint} not found for {old_destination!r}")
        payload = {
            "tenant_id": new_destination,
            "profile_id": "",
            "platform": src.get("platform", ""),
            "source_url": src.get("source_url", ""),
            "original_url": src.get("original_url", ""),
            "platform_content_id": src.get("platform_content_id", ""),
            "content_format": src.get("content_format", "hold_unassigned"),
            "time_start_s": src.get("time_start_s"),
            "time_end_s": src.get("time_end_s"),
            "reviewer_note": src.get("reviewer_note", ""),
            "submitter": src.get("submitter", ""),
            "status": _status_for(url, key, src.get("platform", ""), new_destination),
            "qualification_required": True,
            "duplicate_fingerprint": fingerprint,
        }
        row, created = persist_submission(url, key, payload)
        record_routing_event(url, key, event)
        return {"action": "copy", "destination": new_destination,
                "created_id": row.get("id"), "reason": ""}

    # assign / reassign: update the existing row's destination.
    src = fetch_submission(url, key, fingerprint, old_destination)
    if not src:
        raise RoutingError("NOT_FOUND", f"source {fingerprint} not found for {old_destination!r}")
    status = _status_for(url, key, src.get("platform", ""), new_destination)
    _update_submission_destination(url, key, fingerprint, old_destination, new_destination, status)
    record_routing_event(url, key, event)
    return {"action": action, "destination": new_destination, "reason": ""}
