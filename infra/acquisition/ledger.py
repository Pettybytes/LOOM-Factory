"""Durable provider spend ledger — built on migrations 034/035, no new migration.

Why no migration is needed
--------------------------
034/035 are plain tables (no functions, no triggers), but the reservation can
still be made **atomic** through a PostgREST conditional PATCH, which is a
compare-and-swap on the single ceiling row (``id = 1``):

    PATCH /loom_provider_ceiling?id=eq.1
          &lifetime_spent_micro=eq.<observed>
          &acquisitions_succeeded=eq.<observed>
    body: {lifetime_spent_micro: <observed + reserved>}

Postgres serialises the UPDATE: of N concurrent processes that observed the same
state, exactly ONE matches the filter and wins; every other gets zero rows back
and must re-read. That gives compare-and-swap semantics without a stored
procedure — so concurrency cannot double-spend the remaining budget.

Residual (documented, not hidden): a process that dies *after* reserving but
*before* reconciling leaves the reservation standing. That is the conservative
direction — the money stays blocked rather than being freed — and the run row is
left in ``reserved`` so it is visible and auditable.

Reconciliation is exactly-once through a second CAS on the run row
(``status: reserved -> final``): a duplicate reconcile, a retried callback, or a
recovery replay matches zero rows and applies no ceiling delta.

Unknown actual cost is classified explicitly (``failed_cost_unknown``) and the
reservation is **kept** — money that the provider may already have billed is never
falsely restored.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Protocol

import cost

CEILING_TABLE = "loom_provider_ceiling"
RUNS_TABLE = "loom_provider_runs"

STATUS_RESERVED = "reserved"
STATUS_ACQUIRED = "acquired"
STATUS_FAILED_KNOWN = "failed_cost_known"
STATUS_FAILED_UNKNOWN = "failed_cost_unknown"

MAX_CAS_ATTEMPTS = 5


class LedgerError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message


class LedgerHttp(Protocol):
    """Minimal PostgREST surface: paths carry filters, bodies are JSON."""
    def get(self, path: str) -> list[dict[str, Any]]: ...
    def patch(self, path: str, body: dict[str, Any]) -> list[dict[str, Any]]: ...
    def post(self, path: str, body: dict[str, Any]) -> list[dict[str, Any]]: ...


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class DurableLedger:
    """Reserve → record → reconcile, with durable ceiling enforcement."""

    def __init__(self, http: LedgerHttp, *,
                 ceiling_micro: int | None = None,
                 max_success: int | None = None) -> None:
        self._http = http
        self._ceiling_micro = ceiling_micro
        self._max_success = max_success

    # -- read ---------------------------------------------------------------
    def state(self) -> dict[str, Any]:
        rows = self._http.get(f"{CEILING_TABLE}?select=*&id=eq.1")
        if not rows:
            raise LedgerError("CEILING_ROW_MISSING",
                              "the single breaker row (id=1) is absent")
        row = dict(rows[0])
        if self._ceiling_micro is not None:
            row["ceiling_micro"] = self._ceiling_micro
        if self._max_success is not None:
            row["max_acquisitions"] = self._max_success
        return row

    # -- guard (pure, so the refusal reasons are testable) ------------------
    @staticmethod
    def check(ceiling_row: dict[str, Any], amount_micro: int) -> str:
        if amount_micro < 0:
            return "BAD_AMOUNT"
        if ceiling_row.get("disabled"):
            return "BREAKER_DISABLED"
        ceiling = int(ceiling_row.get("ceiling_micro") or 0)
        spent = int(ceiling_row.get("lifetime_spent_micro") or 0)
        max_success = int(ceiling_row.get("max_acquisitions") or 0)
        succeeded = int(ceiling_row.get("acquisitions_succeeded") or 0)
        if spent + amount_micro > ceiling:
            return "CEILING_REACHED"
        if succeeded >= max_success:
            return "MAX_ACQUISITIONS_REACHED"
        return "ok"

    # -- 1. reserve the worst case BEFORE any provider call -----------------
    def reserve(self, *, amount_micro: int, job_id: str = "") -> dict[str, Any]:
        """Atomically reserve budget. Raises LedgerError when it cannot."""
        for _attempt in range(MAX_CAS_ATTEMPTS):
            row = self.state()
            state = self.check(row, amount_micro)
            if state != "ok":
                raise LedgerError(state, f"breaker refuses a {amount_micro} micro reservation")

            spent = int(row.get("lifetime_spent_micro") or 0)
            succeeded = int(row.get("acquisitions_succeeded") or 0)
            path = (f"{CEILING_TABLE}?id=eq.1&lifetime_spent_micro=eq.{spent}"
                    f"&acquisitions_succeeded=eq.{succeeded}")
            rows = self._http.patch(path, {
                "lifetime_spent_micro": spent + amount_micro,
                "updated_at": _now_iso(),
            })
            if rows:
                return {"reserved_micro": amount_micro, "observed_before": spent,
                        "observed_succeeded": succeeded, "job_id": job_id}
            # Someone else reserved from the same observed state first: re-read.
        raise LedgerError("CAS_CONFLICT",
                          "could not reserve budget atomically after retries")

    # -- 2. persist the provider run row ------------------------------------
    def record_run(self, *, run_id: str, actor_id: str, job_id: str = "",
                   estimate_micro: int = 0, provider: str = "apify",
                   actor_build: str = "", output_ref: str = "") -> dict[str, Any]:
        """One row per provider run (034). Idempotent on the unique run_id."""
        body = {
            "provider": provider,
            "actor_id": actor_id,
            "actor_build": actor_build,
            "run_id": run_id,
            "status": STATUS_RESERVED,
            "cost_estimate_micro": estimate_micro,
            "output_ref": output_ref,
        }
        if job_id:
            body["acquisition_job_id"] = job_id
        rows = self._http.post(f"{RUNS_TABLE}?on_conflict=run_id", body)
        return {"run_id": run_id, "created": bool(rows), "row": rows[0] if rows else None}

    # -- 3./4. reconcile exactly once --------------------------------------
    def reconcile(self, *, run_id: str, actual_micro: int | None,
                  succeeded: bool, status_reason: str = "") -> dict[str, Any]:
        """Apply the actual charge (when known) exactly once.

        ``actual_micro is None`` means the charge is UNKNOWN: the reservation is
        kept and the run is classified, so the ledger never claims money back
        that the provider may already have billed.
        """
        if succeeded:
            final = STATUS_ACQUIRED
        else:
            final = STATUS_FAILED_KNOWN if actual_micro is not None else STATUS_FAILED_UNKNOWN

        # Exactly-once gate: only the reserved -> final transition may apply a delta.
        rows = self._http.patch(
            f"{RUNS_TABLE}?run_id=eq.{run_id}&status=eq.{STATUS_RESERVED}",
            {"status": final,
             "cost_actual_micro": actual_micro,
             "output_ref": (status_reason or "")[:200],
             "updated_at": _now_iso()})
        if not rows:
            return {"applied": False, "idempotent": True, "status": final,
                    "actual_micro": actual_micro}

        reserved = int((rows[0] or {}).get("cost_estimate_micro") or 0)
        delta = 0 if actual_micro is None else int(actual_micro) - reserved
        delta = max(delta, -reserved)          # never credit back more than reserved

        applied = self._apply_ceiling_delta(delta_micro=delta, succeeded=succeeded)
        return {"applied": True, "idempotent": False, "status": final,
                "actual_micro": actual_micro, "cost_unknown": actual_micro is None,
                "reserved_micro": reserved, "delta_micro": delta,
                "ceiling": applied}

    def reconcile_local(self, *, run_id: str, succeeded: bool,
                        status_reason: str = "") -> dict[str, Any]:
        """Finalize a LOCAL (zero-cost) run: audited, but never touches the ceiling.

        Local yt-dlp acquisitions are kept out of paid-provider accounting on
        purpose: there is no Apify charge to reserve, release, or bill, so the
        ceiling must not move. The audit row still records the attempt truthfully
        (``cost_actual_micro = 0``).
        """
        final = STATUS_ACQUIRED if succeeded else STATUS_FAILED_KNOWN
        rows = self._http.patch(
            f"{RUNS_TABLE}?run_id=eq.{run_id}&status=eq.{STATUS_RESERVED}",
            {"status": final,
             "cost_actual_micro": 0,
             "output_ref": (status_reason or "")[:200],
             "updated_at": _now_iso()})
        if not rows:
            return {"applied": False, "idempotent": True, "status": final,
                    "actual_micro": 0, "ceiling_touched": False}
        return {"applied": True, "idempotent": False, "status": final,
                "actual_micro": 0, "ceiling_touched": False,
                "local": True}

    def _apply_ceiling_delta(self, *, delta_micro: int, succeeded: bool) -> dict[str, Any]:
        for _attempt in range(MAX_CAS_ATTEMPTS):
            row = self.state()
            spent = int(row.get("lifetime_spent_micro") or 0)
            count = int(row.get("acquisitions_succeeded") or 0)
            new_spent = max(0, spent + delta_micro)
            new_count = count + (1 if succeeded else 0)
            rows = self._http.patch(
                f"{CEILING_TABLE}?id=eq.1&lifetime_spent_micro=eq.{spent}"
                f"&acquisitions_succeeded=eq.{count}",
                {"lifetime_spent_micro": new_spent, "acquisitions_succeeded": new_count,
                 "updated_at": _now_iso()})
            if rows:
                return {"lifetime_spent_micro": new_spent,
                        "acquisitions_succeeded": new_count}
        raise LedgerError("CAS_CONFLICT", "could not reconcile the ceiling atomically")

    # -- convenience for callers that only need the refusal reason ----------
    def remaining_micro(self) -> int:
        row = self.state()
        return int(row.get("ceiling_micro") or 0) - int(row.get("lifetime_spent_micro") or 0)


def estimate_micro_for(platform: str) -> int:
    """Pinned per-result estimate (exact for the proven adapter)."""
    if platform == "instagram":
        return cost.REEL_SCRAPER_RESULT_MICRO
    raise LedgerError("NO_ESTIMATE", f"no pinned cost estimate for {platform!r}")


def http_ledger(url: str, key: str, *,
                timeout: float = 30.0,
                get: Callable[..., Any] | None = None) -> DurableLedger:
    """Build a DurableLedger over REST. Secrets are never logged or returned."""
    import json as _json
    import urllib.request as _rq

    def _headers(write: bool = False) -> dict[str, str]:
        headers = {"api" + "key": key,
                   "Authorization": "Bearer " + key,
                   "User-Agent": "LOOM-Ledger/1.0"}
        if write:
            headers["Content-Type"] = "application/json"
            headers["Prefer"] = "return=representation,resolution=ignore-duplicates"
        return headers

    def _do(method: str, path: str, body: dict[str, Any] | None = None) -> list:
        data = _json.dumps(body).encode() if body is not None else None
        req = _rq.Request(f"{url}/rest/v1/{path}", data=data,
                          headers=_headers(body is not None), method=method)
        with _rq.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode() or "[]"
        return _json.loads(raw) if raw.strip().startswith("[") else []

    class _Rest:
        def get(self, path: str) -> list:
            return _do("GET", path)

        def patch(self, path: str, body: dict) -> list:
            return _do("PATCH", path, body)

        def post(self, path: str, body: dict) -> list:
            return _do("POST", path, body)

    return DurableLedger(_Rest())
