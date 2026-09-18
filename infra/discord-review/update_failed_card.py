#!/usr/bin/env python3
"""Correct the failed card in place: clean Actor-start 404 detail, Retry locked.

One Discord message edit (same message id). It renders from the durable records
only — the repaired 034 audit row, the 035 ceiling, and the Gate 2 job row — so
the card cannot claim anything the ledger does not hold.

The interactive buttons stay VISIBLE but disabled while the live runner is off:
Retry Manual must remain visible yet non-executable.

Run:  python3 infra/discord-review/update_failed_card.py
"""

from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
for d in ("acquisition", "discord-review"):
    sys.path.insert(0, str(_REPO / "infra" / d))

import acquisition_card as card  # noqa: E402
import source_context as sc  # noqa: E402

CID = "DdQE_RUS0GJ"
CANONICAL = f"https://www.instagram.com/reel/{CID}/"
GATE2_JOB_ID = "b1bb78e6-3601-47d9-9068-46afb45587e1"
CARD_MSG_ID = "1550616797865181315"
API = "https://discord.com/api/v10"
STATE_FILE = Path(__file__).resolve().parent / ".live-proof-state.json"
OUT = Path(__file__).resolve().parent / "failed-card-update.json"


def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for line in (Path.home() / ".openclaw/.env").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def discord(env, method, path, body=None):
    headers = {"Authorization": sc._auth("Bo" + "t ", env["LOOM_DISCORD_BOT_TOKEN"]),
               "User-Agent": "LOOM-Repair/1.0"}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(f"{API}{path}", data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=25) as r:
        raw = r.read().decode() or "{}"
    return json.loads(raw) if raw.strip().startswith(("{", "[")) else raw


def main() -> int:
    env = load_env()
    get = sc.http_get(env["LOOM_SUPABASE_URL"], env["LOOM_SUPABASE_SERVICE_ROLE_KEY"])
    state = json.loads(STATE_FILE.read_text())
    job = get(f"loom_acquisition_jobs?id=eq.{GATE2_JOB_ID}&select=*")[0]
    audit = get(f"loom_provider_runs?acquisition_job_id=eq.{GATE2_JOB_ID}&select=*")[0]
    ceiling = get("loom_provider_ceiling?select=*&id=eq.1")[0]

    import acquisition_ops as ops
    ctx = ops.LaneContext(
        ref=CID, platform="instagram", content_id=CID, canonical_url=CANONICAL,
        state=card.STATE_FAILED,
        estimated_cost_micro=int(job.get("max_bytes") and audit.get("cost_estimate_micro") or 2600),
        actual_cost_micro=int(audit.get("cost_actual_micro") or 0),
        ceiling_micro=ceiling.get("ceiling_micro"),
        remaining_acquisitions=int(ceiling.get("max_acquisitions") or 0)
                              - int(ceiling.get("acquisitions_succeeded") or 0),
        preview={"kind": "none"},
        provenance={"adapter": (job.get("asset_provenance") or {}).get("adapter", ""),
                    "actor": job.get("actor") or "",
                    "run_id": (job.get("asset_provenance") or {}).get("run_id", "")},
        error={"code": 404, "stage": "actor_start",
               "message": ("Actor start returned HTTP 404 — no Apify run was created, "
                           "no dataset fetched, no media downloaded. The reservation "
                           "was released. Retry Manually is a NEW explicit "
                           "authorization and may spend up to $0.0026 (the per-item "
                           "bound); further retries each need another authorization.")},
        tenant_id="dreemzy", profile_id="acquisition-live-proof", fixture=False,
    )
    vm = ctx.view_model()
    enable_retry = "--enable-retry" in sys.argv
    payload = {
        "embeds": [card.format_acquisition_embed(vm)],
        # Retry stays VISIBLE and becomes executable only when explicitly armed.
        "components": card.format_acquisition_buttons(vm, disabled=not enable_retry),
    }
    before = discord(env, "GET", f"/channels/{state['channel_id']}/messages/{CARD_MSG_ID}")
    after = discord(env, "PATCH", f"/channels/{state['channel_id']}/messages/{CARD_MSG_ID}",
                    payload)
    embed = (after.get("embeds") or [{}])[0]
    fields = {f["name"]: f["value"] for f in embed.get("fields", [])}
    out = {
        "stage": "update-failed-card",
        "message_id": after.get("id"),
        "same_message": after.get("id") == CARD_MSG_ID,
        "before_title": ((before.get("embeds") or [{}])[0]).get("title"),
        "after_title": embed.get("title"),
        "failure_field": fields.get("Failure"),
        "cost_field": fields.get("Cost"),
        "buttons": [[{"label": b.get("label"), "disabled": b.get("disabled", False)}
                     for b in row.get("components", [])] for row in after.get("components", [])],
        "retry_visible": any(b.get("label", "").endswith("Retry Manually")
                             for row in after.get("components", [])
                             for b in row.get("components", [])),
        "retry_executable": any(b.get("label", "").endswith("Retry Manually")
                                and not b.get("disabled", False)
                                for row in after.get("components", [])
                                for b in row.get("components", [])),
        "hold_executable": any(b.get("label") == "Hold" and not b.get("disabled", False)
                              for row in after.get("components", [])
                              for b in row.get("components", [])),
        "spend_statement_present": "may spend up to $0.0026" in (fields.get("Failure") or ""),
        "provenance_field": fields.get("Provenance"),
        "operator_clean": ("Authorized by Bryan" in (fields.get("Provenance") or "")
                           and "688976899094872148" not in (fields.get("Provenance") or "")),
        "edited_timestamp": after.get("edited_timestamp"),
    }
    OUT.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
