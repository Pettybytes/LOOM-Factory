"""TikTok paid-provider ceilings and ledger guards.

Three SEPARATE ceiling records, entirely isolated from the Instagram breaker
(``loom_provider_ceiling``, single row ``id=1``). Nothing in this module reads or
writes that row: the Instagram ceiling must remain untouched by TikTok spend.

    Clockworks  10,000 micro-dollars ($0.01)   scope=actor
    Apidojo     10,000 micro-dollars ($0.01)   scope=actor
    combined    20,000 micro-dollars ($0.02)   scope=combined

Guarantees
----------
* **Full-exposure reservation.** Each run reserves its actor's ENTIRE ceiling
  (10,000 micro) before the provider call, so no run can begin against budget it
  might later exceed. Apify's own ``maxTotalChargeUsd`` run parameter enforces the
  same bound a second time at the billing layer.
* **One run at a time.** A second paid run is refused while ANY earlier
  reservation is unresolved, whichever actor holds it.
* **Actor isolation.** A run's reconciliation must name the actor that reserved
  it; one actor's allowance can never be spent, released, or reconciled by the
  other.
* **Exactly-once reconciliation**, keyed on the Apify run id, via a
  compare-and-swap on ``status: reserved -> final``. A duplicate or replayed
  reconcile matches zero rows and applies no delta.
* **Verified release only.** The unused reservation is released only after a
  verified reconciliation. A run that returns no ``usageTotalUsd`` retains its
  full reservation and is classified ``COST_UNVERIFIED`` — money the provider may
  already have billed is never falsely restored.
* **Hard caps.** Neither actor can exceed $0.01 and the pair cannot exceed $0.02
  in reserved-plus-spent micro-dollars.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from tiktok_actors import ACTOR_APIDOJO, ACTOR_CLOCKWORKS

MICRO_PER_DOLLAR = 1_000_000

# --- the three separate ceiling records -----------------------------------
CEILING_CLOCKWORKS_MICRO = 10_000
CEILING_APIDOJO_MICRO = 10_000
CEILING_TIKTOK_COMBINED_MICRO = 20_000

COMBINED_KEY = "tiktok:combined"

STATUS_RESERVED = "reserved"
STATUS_RECONCILED = "reconciled"
STATUS_COST_UNVERIFIED = "cost_unverified"

#: The Instagram breaker this module must never touch.
INSTAGRAM_CEILING_TABLE = "loom_provider_ceiling"
INSTAGRAM_CEILING_ROW = 1


class TikTokLedgerError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message


@dataclass(frozen=True)
class CeilingRecord:
    """One explicit, independently addressable ceiling record."""

    key: str
    scope: str                 # "actor" | "combined"
    actor: str                 # "" for the combined record
    ceiling_micro: int
    label: str = ""


TIKTOK_CEILING_RECORDS: tuple[CeilingRecord, ...] = (
    CeilingRecord(key=ACTOR_CLOCKWORKS, scope="actor", actor=ACTOR_CLOCKWORKS,
                  ceiling_micro=CEILING_CLOCKWORKS_MICRO,
                  label="Clockworks TikTok Scraper"),
    CeilingRecord(key=ACTOR_APIDOJO, scope="actor", actor=ACTOR_APIDOJO,
                  ceiling_micro=CEILING_APIDOJO_MICRO,
                  label="Apidojo TikTok Scraper"),
    CeilingRecord(key=COMBINED_KEY, scope="combined", actor="",
                  ceiling_micro=CEILING_TIKTOK_COMBINED_MICRO,
                  label="TikTok combined authorization"),
)

ACTOR_CEILINGS: dict[str, int] = {
    r.actor: r.ceiling_micro for r in TIKTOK_CEILING_RECORDS if r.scope == "actor"
}


def ceiling_records() -> dict[str, dict[str, Any]]:
    """Read-only view of the three records (the report cites this verbatim)."""
    return {
        r.key: {"key": r.key, "scope": r.scope, "actor": r.actor,
                "ceiling_micro": r.ceiling_micro, "label": r.label}
        for r in TIKTOK_CEILING_RECORDS
    }


def instagram_isolation() -> dict[str, Any]:
    """Statement of isolation, asserted by the tests and quoted in the report."""
    return {
        "instagram_table": INSTAGRAM_CEILING_TABLE,
        "instagram_row": INSTAGRAM_CEILING_ROW,
        "instagram_rows_read": 0,
        "instagram_rows_written": 0,
        "tiktok_ceiling_keys": [r.key for r in TIKTOK_CEILING_RECORDS],
        "isolated": True,
    }


# ---------------------------------------------------------------------------
# Store boundary (injected: memory for tests, REST/table for live)
# ---------------------------------------------------------------------------
class TikTokSpendStore(Protocol):
    def rows(self) -> list[dict[str, Any]]: ...
    def insert(self, row: dict[str, Any]) -> dict[str, Any]: ...
    def cas(self, run_id: str, expect_status: str,
            patch: dict[str, Any]) -> list[dict[str, Any]]: ...


class MemoryTikTokStore:
    """Deterministic store for tests (same contract as the durable one)."""

    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self._rows: list[dict[str, Any]] = [dict(r) for r in (rows or [])]

    def rows(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self._rows]

    def insert(self, row: dict[str, Any]) -> dict[str, Any]:
        if any(r.get("run_id") == row.get("run_id") for r in self._rows):
            raise TikTokLedgerError("DUPLICATE_RUN_ID",
                                    f"run {row.get('run_id')!r} already recorded")
        self._rows.append(dict(row))
        return dict(row)

    def cas(self, run_id: str, expect_status: str,
            patch: dict[str, Any]) -> list[dict[str, Any]]:
        for row in self._rows:
            if row.get("run_id") == run_id and row.get("status") == expect_status:
                row.update(patch)
                return [dict(row)]
        return []


def actual_micro_from_usage(usage_total_usd: float | None) -> int | None:
    """``actual_micro = round(usageTotalUsd × 1e6)``, or ``None`` when unverified."""
    if usage_total_usd is None:
        return None
    try:
        return int(round(float(usage_total_usd) * MICRO_PER_DOLLAR))
    except (TypeError, ValueError):
        return None


class TikTokSpendLedger:
    """Reserve → reconcile, with the three TikTok ceilings enforced."""

    def __init__(self, store: TikTokSpendStore, *,
                 actor_ceilings: dict[str, int] | None = None,
                 combined_ceiling_micro: int = CEILING_TIKTOK_COMBINED_MICRO) -> None:
        self._store = store
        self._ceilings = dict(actor_ceilings or ACTOR_CEILINGS)
        self._combined = combined_ceiling_micro

    # -- reads -------------------------------------------------------------
    def _tiktok_rows(self) -> list[dict[str, Any]]:
        """Only rows belonging to a registered TikTok actor.

        Rows for any other provider (Instagram's) are filtered out entirely, so
        they can never be read as TikTok spend or altered by this ledger.
        """
        return [r for r in self._store.rows() if r.get("actor") in self._ceilings]

    def actor_state(self, actor: str) -> dict[str, Any]:
        if actor not in self._ceilings:
            raise TikTokLedgerError("UNKNOWN_ACTOR", f"{actor!r} has no TikTok ceiling record")
        ceiling = self._ceilings[actor]
        spent = outstanding = 0
        unresolved: list[str] = []
        for row in self._tiktok_rows():
            if row.get("actor") != actor:
                continue
            if row.get("status") == STATUS_RESERVED:
                outstanding += int(row.get("reserved_micro") or 0)
                unresolved.append(str(row.get("run_id") or ""))
            elif row.get("status") == STATUS_COST_UNVERIFIED:
                # An unverified run keeps its reservation standing: it counts as
                # outstanding so it can never be silently re-spent.
                retained = int(row.get("reserved_micro") or 0)
                outstanding += retained
                unresolved.append(str(row.get("run_id") or ""))
            elif row.get("status") == STATUS_RECONCILED:
                spent += int(row.get("actual_micro") or 0)
        committed = spent + outstanding
        return {
            "actor": actor,
            "ceiling_micro": ceiling,
            "spent_micro": spent,
            "outstanding_micro": outstanding,
            "committed_micro": committed,
            "remaining_micro": max(0, ceiling - committed),
            "unresolved_runs": unresolved,
            "within_ceiling": committed <= ceiling,
        }

    # -- bind the provider run id to a provisional reservation -------------
    def provisional_run_id(self, *, actor: str, content_id: str) -> str:
        """First FREE attempt-scoped reservation key for this actor + source.

        Budget must be reserved before the provider call, so the key cannot be the
        Apify run id. It must nonetheless be unique per ATTEMPT: a refused attempt
        that never created a run still leaves an honest, reconciled $0 row, and a
        later attempt must be able to reserve again rather than collide with it.
        """
        existing = {str(r.get("run_id") or "") for r in self._tiktok_rows()}
        attempt = 1
        while f"tiktok:{actor}:{content_id}:{attempt}" in existing:
            attempt += 1
        return f"tiktok:{actor}:{content_id}:{attempt}"

    def bind_run_id(self, *, actor: str, provisional_run_id: str,
                    apify_run_id: str) -> dict[str, Any]:
        """Bind the real Apify run id to an already-made reservation.

        Budget must be reserved BEFORE the provider call, but Apify only assigns
        the run id DURING it. The reservation is therefore opened under a
        deterministic provisional key and atomically renamed to the Apify run id
        the moment it exists — so reconciliation is keyed on the real run id.
        """
        if not apify_run_id:
            raise TikTokLedgerError("NO_RUN_ID", "cannot bind an empty Apify run id")
        if any(r.get("run_id") == apify_run_id for r in self._tiktok_rows()):
            raise TikTokLedgerError("DUPLICATE_RUN_ID",
                                    f"run {apify_run_id!r} is already recorded")
        match = [r for r in self._tiktok_rows() if r.get("run_id") == provisional_run_id]
        if not match:
            raise TikTokLedgerError("UNKNOWN_RUN",
                                    f"no reservation for {provisional_run_id!r}")
        if match[0].get("actor") != actor:
            raise TikTokLedgerError("ACTOR_MISMATCH",
                                    "reservation belongs to a different actor")
        rows = self._store.cas(provisional_run_id, STATUS_RESERVED,
                               {"run_id": apify_run_id, "provisional_run_id": provisional_run_id})
        if not rows:
            raise TikTokLedgerError("BIND_FAILED",
                                    "the reservation is not open for binding")
        return {"bound": True, "provisional_run_id": provisional_run_id,
                "run_id": apify_run_id, "actor": actor}

    def combined_state(self) -> dict[str, Any]:
        states = [self.actor_state(a) for a in sorted(self._ceilings)]
        spent = sum(s["spent_micro"] for s in states)
        outstanding = sum(s["outstanding_micro"] for s in states)
        committed = spent + outstanding
        return {
            "key": COMBINED_KEY,
            "ceiling_micro": self._combined,
            "spent_micro": spent,
            "outstanding_micro": outstanding,
            "committed_micro": committed,
            "remaining_micro": max(0, self._combined - committed),
            "actors": {s["actor"]: s for s in states},
            "unresolved_runs": [r for s in states for r in s["unresolved_runs"]],
            "within_ceiling": committed <= self._combined,
        }

    def state(self) -> dict[str, Any]:
        return {"combined": self.combined_state(),
                "ceilings": ceiling_records(),
                "instagram_isolation": instagram_isolation()}

    # -- guards (pure, so refusals are testable) ---------------------------
    @staticmethod
    def check_reservation(state: dict[str, Any], amount_micro: int) -> str:
        if amount_micro <= 0:
            return "BAD_AMOUNT"
        if state.get("unresolved_runs"):
            return "UNRESOLVED_RESERVATION"
        if state["committed_micro"] + amount_micro > state["ceiling_micro"]:
            return "ACTOR_CEILING_REACHED"
        return "ok"

    @staticmethod
    def check_combined(state: dict[str, Any], amount_micro: int) -> str:
        if state["committed_micro"] + amount_micro > state["ceiling_micro"]:
            return "COMBINED_CEILING_REACHED"
        return "ok"

    # -- 1. reserve the full exposure BEFORE any provider call -------------
    def reserve(self, *, actor: str, run_id: str, job_id: str = "") -> dict[str, Any]:
        if actor not in self._ceilings:
            raise TikTokLedgerError("UNKNOWN_ACTOR",
                                    f"{actor!r} has no TikTok ceiling record")
        if not run_id:
            raise TikTokLedgerError("NO_RUN_ID", "a reservation needs a run id")
        if any(r.get("run_id") == run_id for r in self._tiktok_rows()):
            raise TikTokLedgerError("DUPLICATE_RUN_ID",
                                    f"run {run_id!r} is already recorded")

        actor_state = self.actor_state(actor)
        combined = self.combined_state()

        # "Do not start another paid run while any earlier reservation is
        # unresolved" — enforced across BOTH actors, not just this one.
        if actor_state["unresolved_runs"] or combined["unresolved_runs"]:
            raise TikTokLedgerError(
                "UNRESOLVED_RESERVATION",
                "an earlier reservation is unresolved; reconcile it before spending again")

        # Reserve the full REMAINING authorization for this actor — every dollar
        # still available to it — so a run can never begin against budget it might
        # later exceed. On a first run this is the whole $0.01 ceiling; after a
        # partial charge it is strictly the remainder, which is what keeps the
        # $0.01-per-actor and $0.02-combined caps unbreachable across retries.
        actor_remaining = actor_state["remaining_micro"]
        combined_remaining = combined["remaining_micro"]
        if actor_remaining <= 0:
            raise TikTokLedgerError("ACTOR_CEILING_REACHED",
                                    "this actor's $0.01 ceiling is exhausted")
        if combined_remaining < actor_remaining:
            # The pair's budget cannot cover this actor's full remaining
            # authorization, so no run may start — regardless of how small the
            # expected charge is.
            raise TikTokLedgerError(
                "COMBINED_CEILING_REACHED",
                f"combined TikTok budget ({combined_remaining}) cannot cover this "
                f"actor's remaining authorization ({actor_remaining})")
        amount = actor_remaining

        row = {"run_id": run_id, "actor": actor, "actor_job_id": job_id,
               "status": STATUS_RESERVED, "reserved_micro": amount,
               "actual_micro": None, "released_micro": 0}
        self._store.insert(row)
        return {"reserved_micro": amount, "actor": actor, "run_id": run_id,
                "actor_state": self.actor_state(actor),
                "combined_state": self.combined_state()}

    # -- 2. reconcile exactly once ----------------------------------------
    def reconcile(self, *, actor: str, run_id: str,
                  usage_total_usd: float | None,
                  succeeded: bool = True) -> dict[str, Any]:
        """Apply the provider-reported charge exactly once.

        ``usage_total_usd is None`` means the charge is UNVERIFIED: the
        reservation is retained and the run is classified, so the ledger never
        claims back money the provider may already have billed.
        """
        if actor not in self._ceilings:
            raise TikTokLedgerError("UNKNOWN_ACTOR",
                                    f"{actor!r} has no TikTok ceiling record")

        match = [r for r in self._tiktok_rows() if r.get("run_id") == run_id]
        if not match:
            raise TikTokLedgerError("UNKNOWN_RUN", f"no reservation for run {run_id!r}")
        if match[0].get("actor") != actor:
            # One actor can never reconcile (and so never release or consume) the
            # other actor's allowance.
            raise TikTokLedgerError(
                "ACTOR_MISMATCH",
                f"run {run_id!r} was reserved by {match[0].get('actor')!r}, not {actor!r}")

        reserved = int(match[0].get("reserved_micro") or 0)

        if usage_total_usd is None:
            patch = {"status": STATUS_COST_UNVERIFIED, "actual_micro": None,
                     "released_micro": 0, "verified": False}
        else:
            actual = actual_micro_from_usage(usage_total_usd)
            if actual is None:
                patch = {"status": STATUS_COST_UNVERIFIED, "actual_micro": None,
                         "released_micro": 0, "verified": False}
            else:
                released = max(0, reserved - actual)     # never a negative release
                patch = {"status": STATUS_RECONCILED, "actual_micro": actual,
                         "released_micro": released, "verified": True,
                         "succeeded": bool(succeeded),
                         "over_reservation": actual > reserved}

        # Exactly-once gate: only reserved -> final may apply a delta.
        rows = self._store.cas(run_id, STATUS_RESERVED, patch)
        if not rows:
            return {"applied": False, "idempotent": True, "run_id": run_id,
                    "status": match[0].get("status"),
                    "actual_micro": match[0].get("actual_micro"),
                    "actor_state": self.actor_state(actor),
                    "combined_state": self.combined_state()}

        return {"applied": True, "idempotent": False, "run_id": run_id,
                "actor": actor, "status": patch["status"],
                "reserved_micro": reserved,
                "actual_micro": patch.get("actual_micro"),
                "released_micro": patch.get("released_micro", 0),
                "verified": bool(patch.get("verified")),
                "actor_state": self.actor_state(actor),
                "combined_state": self.combined_state()}

    # -- repair: settle a run whose charge only later became known ---------
    def settle_unverified(self, *, actor: str, run_id: str,
                          usage_total_usd: float | None) -> dict[str, Any]:
        """Later settle a COST_UNVERIFIED run once the real charge is known.

        An unverified row deliberately leaves its reservation standing — the
        conservative direction. When the provider's charge does become known, this
        transitions ``cost_unverified -> reconciled`` EXACTLY ONCE, adds the
        verified charge and releases only the remainder. Without a real charge
        nothing moves, so the reservation stays blocked.
        """
        if actor not in self._ceilings:
            raise TikTokLedgerError("UNKNOWN_ACTOR",
                                    f"{actor!r} has no TikTok ceiling record")
        match = [r for r in self._tiktok_rows() if r.get("run_id") == run_id]
        if not match:
            raise TikTokLedgerError("UNKNOWN_RUN", f"no run {run_id!r}")
        if match[0].get("actor") != actor:
            raise TikTokLedgerError("ACTOR_MISMATCH",
                                    "run belongs to a different actor")
        actual = actual_micro_from_usage(usage_total_usd)
        if actual is None:
            return {"applied": False, "reason": "COST_STILL_UNVERIFIED",
                    "run_id": run_id,
                    "actor_state": self.actor_state(actor)}
        reserved = int(match[0].get("reserved_micro") or 0)
        patch = {"status": STATUS_RECONCILED, "actual_micro": actual,
                 "released_micro": max(0, reserved - actual), "verified": True,
                 "over_reservation": actual > reserved}
        rows = self._store.cas(run_id, STATUS_COST_UNVERIFIED, patch)
        if not rows:
            return {"applied": False, "idempotent": True, "run_id": run_id,
                    "status": match[0].get("status"),
                    "actor_state": self.actor_state(actor)}
        return {"applied": True, "idempotent": False, "run_id": run_id,
                "actor": actor, "status": STATUS_RECONCILED,
                "reserved_micro": reserved, "actual_micro": actual,
                "released_micro": patch["released_micro"], "verified": True,
                "actor_state": self.actor_state(actor),
                "combined_state": self.combined_state()}

    # -- convenience -------------------------------------------------------
    def remaining_micro(self, actor: str) -> int:
        return self.actor_state(actor)["remaining_micro"]

    def combined_remaining_micro(self) -> int:
        return self.combined_state()["remaining_micro"]


# ---------------------------------------------------------------------------
# Durable REST store (no migration: reuses the generic provider-run table)
# ---------------------------------------------------------------------------
PROVIDER_RUNS_TABLE = "loom_provider_runs"


def rest_store(url: str, key: str, *, timeout: float = 30.0) -> TikTokSpendStore:
    """A durable TikTok store over the existing ``loom_provider_runs`` table.

    ``loom_provider_runs`` is the generic one-row-per-provider-run table, so it
    already models a reservation row: ``status='reserved'``,
    ``cost_estimate_micro`` = the reservation, ``cost_actual_micro`` = the charge.
    No migration is required, and because every read and write is filtered to the
    two registered TikTok actors, Instagram's rows (and its single-row breaker in
    ``loom_provider_ceiling``) can never be read or altered here.
    """
    import json as _json
    import urllib.request as _rq

    actors_filter = ",".join(sorted(ACTOR_CEILINGS))

    def _headers(write: bool = False) -> dict[str, str]:
        headers = {"apikey": key, "Authorization": "Bearer " + key,
                   "User-Agent": "LOOM-TikTokLedger/1.0"}
        if write:
            headers["Content-Type"] = "application/json"
            headers["Prefer"] = "return=representation"
        return headers

    def _do(method: str, path: str, body: dict | None = None) -> list:
        data = _json.dumps(body).encode() if body is not None else None
        req = _rq.Request(f"{url}/rest/v1/{path}", data=data,
                          headers=_headers(body is not None), method=method)
        with _rq.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode() or "[]"
        return _json.loads(raw) if raw.strip().startswith("[") else []

    def _to_row(raw: dict) -> dict:
        return {"run_id": str(raw.get("run_id") or ""),
                "actor": str(raw.get("actor_id") or ""),
                "status": str(raw.get("status") or ""),
                "reserved_micro": int(raw.get("cost_estimate_micro") or 0),
                "actual_micro": raw.get("cost_actual_micro")}

    class _Rest:
        def rows(self) -> list[dict]:
            return [_to_row(r) for r in _do(
                "GET", f"{PROVIDER_RUNS_TABLE}?select=run_id,actor_id,status,"
                       f"cost_estimate_micro,cost_actual_micro&actor_id=in.({actors_filter})")]

        def insert(self, row: dict) -> dict:
            body = {"provider": "apify", "actor_id": row["actor"],
                    "run_id": row["run_id"], "status": row["status"],
                    "cost_estimate_micro": int(row["reserved_micro"]),
                    "output_ref": "tiktok reservation"}
            job_id = str(row.get("actor_job_id") or "")
            if len(job_id) == 36 and job_id.count("-") == 4:
                body["acquisition_job_id"] = job_id
            rows = _do("POST", PROVIDER_RUNS_TABLE, body)
            return rows[0] if rows else body

        def cas(self, run_id: str, expect_status: str, patch: dict) -> list[dict]:
            # `patch` is deliberately general: the run-id bind renames a
            # reservation WITHOUT touching its status, so `status` must not be
            # assumed present. Assuming it raised KeyError('status') on a live run
            # AFTER Apify had already created (and chargeable) run.
            body: dict[str, Any] = {}
            if "status" in patch:
                body["status"] = patch["status"]
            if "actual_micro" in patch:
                body["cost_actual_micro"] = patch["actual_micro"]
            if patch.get("run_id"):
                body["run_id"] = patch["run_id"]
            return _do("PATCH", f"{PROVIDER_RUNS_TABLE}?run_id=eq.{run_id}"
                                f"&status=eq.{expect_status}", body)

    return _Rest()
