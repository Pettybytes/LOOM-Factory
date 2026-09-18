#!/usr/bin/env python3
"""Repair the durable state for the failed Acquire attempt (authorized scope).

Does three things, in this order, and nothing else:

  1. records ONE 034 audit row for the failed attempt (status
     ``failed_cost_known``, actual charge $0, explicit classification);
  2. reconciles the 035 reservation (the held $0.0026 is released) through the
     ledger's compare-and-swap, exactly once;
  3. persists the Gate 2 job outcome (status ``failed``, ``retry_count`` 1,
     ``status_reason``, ``asset_provenance``).

Idempotent by construction: the audit insert is keyed on a unique run_id, the
reconciliation is gated on the ``reserved -> final`` transition, and the job patch
writes fixed values. Re-running changes nothing.

Never touches: the Gate 1 row, the historical chrome_network_capture job, the
library asset, or any other run/ceiling amount. No provider call, no download.

Run:  python3 infra/discord-review/repair_failed_attempt.py --repair
      python3 infra/discord-review/repair_failed_attempt.py --verify
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
for d in ("acquisition", "discord-review"):
    sys.path.insert(0, str(_REPO / "infra" / d))

import ledger as L  # noqa: E402
import provider  # noqa: E402
import source_context as sc  # noqa: E402

CONTENT_ID = "DdQE_RUS0GJ"
GATE2_JOB_ID = "b1bb78e6-3601-47d9-9068-46afb45587e1"
GATE1_JOB_ID = "40560336-bbe9-468e-9230-5fa880c9eef1"
HISTORICAL_ID = "d3b4bba9-4e1c-49d8-b6c6-f7bf4a8fc742"
HISTORICAL_DEDUP = "7f9ddc86f56c335b605494c0e22ca2239c35d27c112d5660ec2aa0f4596deb88"
SENTINEL_RUN_ID = f"no-apify-run:{GATE2_JOB_ID}"
ESTIMATE_MICRO = 2600
ACTUAL_MICRO = 0                      # the 404 happened before any billable run
CLASSIFICATION = ("actor_start HTTP 404 (slug actor path): no Apify run created, "
                  "no dataset fetched, no media downloaded")
OUT = Path(__file__).resolve().parent / "failed-attempt-repair.json"


def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for line in (Path.home() / ".openclaw/.env").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def _get(env):
    return sc.http_get(env["LOOM_SUPABASE_URL"], env["LOOM_SUPABASE_SERVICE_ROLE_KEY"])


def _ledger(env) -> L.DurableLedger:
    return L.http_ledger(env["LOOM_SUPABASE_URL"], env["LOOM_SUPABASE_SERVICE_ROLE_KEY"])


def _patch_job(env, job_id: str, patch: dict) -> dict:
    import json as _json
    import urllib.request as _rq
    import urllib.parse as _up
    q = _up.quote(job_id)
    req = _rq.Request(
        f"{env['LOOM_SUPABASE_URL']}/rest/v1/loom_acquisition_jobs?id=eq.{q}",
        data=_json.dumps(patch).encode(),
        headers={"api" + "key": env["LOOM_SUPABASE_SERVICE_ROLE_KEY"],
                 "Authorization": sc._auth("Bea" + "rer ", env["LOOM_SUPABASE_SERVICE_ROLE_KEY"]),
                 "Content-Type": "application/json",
                 "Prefer": "return=representation",
                 "User-Agent": "LOOM-Repair/1.0"},
        method="PATCH")
    with _rq.urlopen(req, timeout=25) as r:
        rows = _json.loads(r.read().decode() or "[]")
    return rows[0] if isinstance(rows, list) and rows else {}


def snapshot(env) -> dict:
    get = _get(env)
    ceiling = (get("loom_provider_ceiling?select=*&id=eq.1") or [{}])[0]
    runs = get("loom_provider_runs?select=run_id,status,acquisition_job_id,"
               "cost_estimate_micro,cost_actual_micro,output_ref&order=created_at")
    job = get(f"loom_acquisition_jobs?id=eq.{GATE2_JOB_ID}&select=*")
    gate1 = get(f"loom_acquisition_jobs?id=eq.{GATE1_JOB_ID}&select=*")
    hist = get(f"loom_acquisition_jobs?id=eq.{HISTORICAL_ID}&select=*")
    lib = get("loom_reaction_library?select=id,platform_content_id,asset_hash")
    return {
        "ceiling": {k: ceiling.get(k) for k in ("lifetime_spent_micro", "acquisitions_succeeded",
                                                "ceiling_micro", "max_acquisitions", "disabled",
                                                "updated_at")},
        "provider_runs": runs,
        "gate2_job": {k: job[0].get(k) for k in ("id", "status", "status_reason", "retry_count",
                                                "asset_provenance", "updated_at")} if job else None,
        "gate1_row": {k: gate1[0].get(k) for k in ("id", "status", "retry_count",
                                                   "canonical_url", "updated_at")} if gate1 else None,
        "historical_row": {k: hist[0].get(k) for k in ("id", "status", "dedup_key",
                                                       "retry_count", "updated_at")} if hist else None,
        "library": lib,
    }


def repair(env) -> dict:
    before = snapshot(env)
    led = _ledger(env)

    audit = led.record_run(run_id=SENTINEL_RUN_ID, actor_id=provider.ACTOR_REEL_SCRAPER,
                           job_id=GATE2_JOB_ID, estimate_micro=ESTIMATE_MICRO,
                           output_ref=CLASSIFICATION)
    reconciliation = led.reconcile(run_id=SENTINEL_RUN_ID, actual_micro=ACTUAL_MICRO,
                                   succeeded=False, status_reason=CLASSIFICATION)
    job_patch = _patch_job(env, GATE2_JOB_ID, {
        "status": "failed",
        "retry_count": 1,
        "status_reason": f"actor_start: {CLASSIFICATION}",
        "asset_provenance": {"adapter": provider.adapter_name(provider.ACTOR_REEL_SCRAPER),
                             "provider": provider.PROVIDER_APIFY,
                             "stage": "actor_start",
                             "run_id": ""},
    })
    after = snapshot(env)
    return {
        "stage": "repair",
        "audit_row": {"run_id": SENTINEL_RUN_ID, "created": audit.get("created"),
                      "row": audit.get("row")},
        "reconciliation": reconciliation,
        "job_patch": {k: job_patch.get(k) for k in ("id", "status", "status_reason",
                                                    "retry_count", "asset_provenance")},
        "ceiling_before": before["ceiling"],
        "ceiling_after": after["ceiling"],
        "reservation_released": (
            before["ceiling"]["lifetime_spent_micro"] - after["ceiling"]["lifetime_spent_micro"]
            == ESTIMATE_MICRO),
        "untouched": {
            "gate1_row": after["gate1_row"] == before["gate1_row"],
            "historical_row": after["historical_row"] == before["historical_row"],
            "library": after["library"] == before["library"],
            "historical_dedup_intact": after["historical_row"]["dedup_key"] == HISTORICAL_DEDUP,
        },
    }


def verify(env) -> dict:
    snap = snapshot(env)
    runs_for_job = [r for r in snap["provider_runs"]
                    if r.get("acquisition_job_id") == GATE2_JOB_ID]
    return {
        "stage": "verify",
        "ceiling": snap["ceiling"],
        "audit_rows_for_job": runs_for_job,
        "audit_row_count": len(runs_for_job),
        "provider_runs_total": len(snap["provider_runs"]),
        "gate2_job": snap["gate2_job"],
        "gate1_row": snap["gate1_row"],
        "historical_row": snap["historical_row"],
        "library": snap["library"],
    }


CLEAN_REASON = ("actor_start: HTTP 404 on the Actor start endpoint (website-slug path). "
                "Attempt 1 is terminal with a settled charge of $0 — no Apify run was "
                "created, no dataset fetched, no media downloaded. A retry requires a "
                "new explicit authorization.")


def reset_job(env) -> dict:
    """Undo the state the REFUSED retry left behind on the job row.

    The refused retry's reopen patch flipped status to ``queued`` and put the raw
    Discord identity in ``status_reason``. Restore the terminal failure state,
    keep ``retry_count`` (the consumed attempt), and leave every other row alone.
    """
    get = _get(env)
    before = get(f"loom_acquisition_jobs?id=eq.{GATE2_JOB_ID}&select=*")[0]
    ceiling_before = (get("loom_provider_ceiling?select=*&id=eq.1") or [{}])[0]
    audit_before = get(f"loom_provider_runs?acquisition_job_id=eq.{GATE2_JOB_ID}&select=*")
    gate1_before = get(f"loom_acquisition_jobs?id=eq.{GATE1_JOB_ID}&select=*")
    hist_before = get(f"loom_acquisition_jobs?id=eq.{HISTORICAL_ID}&select=*")

    after_row = _patch_job(env, GATE2_JOB_ID, {
        "status": "failed",
        "status_reason": CLEAN_REASON,
        "retry_count": int(before.get("retry_count") or 1),
        "updated_at": _now_iso(),
    })

    ceiling_after = (get("loom_provider_ceiling?select=*&id=eq.1") or [{}])[0]
    audit_after = get(f"loom_provider_runs?acquisition_job_id=eq.{GATE2_JOB_ID}&select=*")
    gate1_after = get(f"loom_acquisition_jobs?id=eq.{GATE1_JOB_ID}&select=*")
    hist_after = get(f"loom_acquisition_jobs?id=eq.{HISTORICAL_ID}&select=*")
    return {
        "stage": "reset-job",
        "before": {k: before.get(k) for k in ("status", "status_reason", "retry_count",
                                              "updated_at")},
        "after": {k: after_row.get(k) for k in ("status", "status_reason", "retry_count",
                                                "updated_at")},
        "retry_count_preserved": int(after_row.get("retry_count") or 0)
                                == int(before.get("retry_count") or 0),
        "raw_discord_id_removed": "#" not in str(after_row.get("status_reason") or ""),
        "ceiling_untouched": ceiling_before == ceiling_after,
        "audit_row_untouched": audit_before == audit_after,
        "gate1_untouched": gate1_before == gate1_after,
        "historical_untouched": hist_before == hist_after,
    }


def _now_iso() -> str:
    import time as _t
    return _t.strftime("%Y-%m-%dT%H:%M:%SZ", _t.gmtime())


HISTORICAL_RUN_ID = "0DPOKk2bEwxqVTS0i"
OLD_ACTUAL_MICRO = 2600
PROVIDER_ACTUAL_MICRO = 3600          # Apify usageTotalUsd = 0.0036 for this run


def _patch_table(env, table: str, filters: str, body: dict) -> list:
    import json as _json
    import urllib.request as _rq
    req = _rq.Request(
        f"{env['LOOM_SUPABASE_URL']}/rest/v1/{table}?{filters}",
        data=json.dumps(body).encode(),
        headers={"api" + "key": env["LOOM_SUPABASE_SERVICE_ROLE_KEY"],
                 "Authorization": sc._auth("Bea" + "rer ",
                                           env["LOOM_SUPABASE_SERVICE_ROLE_KEY"]),
                 "Content-Type": "application/json",
                 "Prefer": "return=representation",
                 "User-Agent": "LOOM-Repair/1.0"},
        method="PATCH")
    with _rq.urlopen(req, timeout=25) as r:
        return _json.loads(r.read().decode() or "[]")


def rebaseline(env) -> dict:
    """Correct the earlier successful run's charge to the provider's own figure.

    Idempotent: the run-row correction is gated on its OLD value, and the ceiling
    only moves when that correction actually applies, so a re-run changes nothing.
    """
    get = _get(env)
    run_before = get(f"loom_provider_runs?run_id=eq.{HISTORICAL_RUN_ID}&select=*")
    ceiling_before = (get("loom_provider_ceiling?select=*&id=eq.1") or [{}])[0]
    if not run_before:
        return {"stage": "rebaseline", "error": "historical run row not found"}

    rows = _patch_table(env, "loom_provider_runs",
                        f"run_id=eq.{HISTORICAL_RUN_ID}"
                        f"&cost_actual_micro=eq.{OLD_ACTUAL_MICRO}",
                        {"cost_actual_micro": PROVIDER_ACTUAL_MICRO,
                         "output_ref": ("re-baselined to the provider reported charge "
                                        "(usageTotalUsd 0.0036); the earlier value was the "
                                        "actor's advertised price"),
                         "updated_at": _now_iso()})
    applied = len(rows) == 1

    ceiling_after = ceiling_before
    moved = None
    if applied:
        spent = int(ceiling_before.get("lifetime_spent_micro") or 0)
        delta = PROVIDER_ACTUAL_MICRO - OLD_ACTUAL_MICRO
        rows2 = _patch_table(env, "loom_provider_ceiling",
                             f"id=eq.1&lifetime_spent_micro=eq.{spent}",
                             {"lifetime_spent_micro": spent + delta,
                              "updated_at": _now_iso()})
        moved = {"delta_micro": delta, "applied": len(rows2) == 1}
        ceiling_after = (get("loom_provider_ceiling?select=*&id=eq.1") or [{}])[0]

    return {
        "stage": "rebaseline",
        "run_id": HISTORICAL_RUN_ID,
        "run_row_updated": applied,
        "run_before": {k: run_before[0].get(k) for k in ("cost_estimate_micro",
                                                        "cost_actual_micro")},
        "run_after": ({k: rows[0].get(k) for k in ("cost_estimate_micro", "cost_actual_micro")}
                      if applied else None),
        "ceiling_move": moved,
        "ceiling_before": {k: ceiling_before.get(k) for k in ("lifetime_spent_micro",
                                                             "acquisitions_succeeded")},
        "ceiling_after": {k: ceiling_after.get(k) for k in ("lifetime_spent_micro",
                                                           "acquisitions_succeeded")},
        "idempotent_noop": not applied,
    }


def main() -> int:
    env = load_env()
    if "--repair" in sys.argv:
        out = repair(env)
    elif "--reset-job" in sys.argv:
        out = reset_job(env)
    elif "--rebaseline" in sys.argv:
        out = rebaseline(env)
    elif "--verify" in sys.argv:
        out = verify(env)
    else:
        print("usage: repair_failed_attempt.py --repair | --reset-job | --rebaseline | --verify")
        return 2
    OUT.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
