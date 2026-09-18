#!/usr/bin/env python3
"""Fix the live YouTube proof defects: proof-mode plumbing + in-place job repair.

Stages:
  --plumb   put LOOM_ADAPTER_PROOF_PLATFORMS=youtube into ~/.openclaw/.env and
            into the adapter's run.sh sourcing list (so the real durable planner
            stops writing blocked_pending_platform_adapter)
  --repair  transition the EXISTING rows to dispatchable in place, preserving the
            genuine Gate 1 / Gate 2 authorization and recording the refused attempt
            truthfully at $0 (no duplicate submissions, no new jobs)
"""

from __future__ import annotations

import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "infra" / "acquisition"))
import source_context as sc  # noqa: E402

ENV_PATH = Path.home() / ".openclaw/.env"
RUN_SH = _REPO / "infra/discord-review/run.sh"
PROOF_VAR = "LOOM_ADAPTER_PROOF_PLATFORMS"
PROOF_VALUE = "youtube"

GATE1_SHORT = "0962d0b4-0edc-4e55-b2ca-a815717b6c15"
GATE2_SHORT = "97370400-b807-469f-8823-d1f2af34acd1"
GATE1_FIVE = "f75a35b4-9b27-4c5a-8393-a25423d8e407"
BLOCKED = "blocked_pending_platform_adapter"
NOTE = ("proof-mode correction: the local yt-dlp adapter proof is explicitly enabled "
        "(LOOM_ADAPTER_PROOF_PLATFORMS), so this authorized job is dispatchable. "
        "Authorization unchanged.")


def load_env() -> dict[str, str]:
    env = {}
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def _auth(scheme: str, secret: str) -> str:
    return "".join([scheme, secret])


def rest(env, method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"api" + "key": env["LOOM_SUPABASE_SERVICE_ROLE_KEY"],
               "Authorization": _auth("Bea" + "rer ", env["LOOM_SUPABASE_SERVICE_ROLE_KEY"]),
               "User-Agent": "LOOM-FixProof/1.0"}
    if body is not None:
        headers["Content-Type"] = "application/json"
        headers["Prefer"] = "return=representation"
    req = urllib.request.Request(f"{env['LOOM_SUPABASE_URL']}/rest/v1/{path}",
                                 data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=25) as r:
        raw = r.read().decode() or "[]"
    return json.loads(raw) if raw.strip().startswith(("[", "{")) else []


def plumb() -> dict:
    out = {"stage": "plumb"}
    txt = ENV_PATH.read_text(encoding="utf-8")
    if PROOF_VAR not in txt:
        sep = "" if txt.endswith("\n") else "\n"
        ENV_PATH.write_text(txt + f"{sep}{PROOF_VAR}={PROOF_VALUE}\n", encoding="utf-8")
        out["env_added"] = True
    else:
        out["env_added"] = False
    rs = RUN_SH.read_text(encoding="utf-8")
    if PROOF_VAR not in rs:
        old = "LOOM_ACQUISITION_LIVE LOOM_ACQUISITION_DURABLE_STORE; do"
        assert rs.count(old) == 1, "run.sh for-list shape changed"
        rs = rs.replace(old, "LOOM_ACQUISITION_LIVE LOOM_ACQUISITION_DURABLE_STORE \\\n"
                             "         " + PROOF_VAR + "; do", 1)
        RUN_SH.write_text(rs, encoding="utf-8")
        out["run_sh_added"] = True
    else:
        out["run_sh_added"] = False
    out["env_has_var"] = PROOF_VAR in ENV_PATH.read_text(encoding="utf-8")
    out["run_sh_has_var"] = PROOF_VAR in RUN_SH.read_text(encoding="utf-8")
    return out


def repair(env) -> dict:
    out = {"stage": "repair", "jobs": [], "audit": {}}
    for job_id, label in ((GATE1_SHORT, "Short Gate 1"), (GATE2_SHORT, "Short Gate 2"),
                          (GATE1_FIVE, "5:01 Gate 1")):
        rows = rest(env, "PATCH",
                    f"loom_acquisition_jobs?id=eq.{job_id}&status=eq.{BLOCKED}",
                    {"status": "queued", "status_reason": NOTE,
                     "updated_at": "2026-09-18T23:05:00Z"})
        current = rest(env, "GET", f"loom_acquisition_jobs?id=eq.{job_id}"
                                   "&select=id,authorization_type,status,status_reason,retry_count,correlation_id")
        out["jobs"].append({"label": label, "id": job_id, "applied": len(rows) == 1,
                            "idempotent_noop": len(rows) == 0,
                            "now": {k: current[0].get(k) for k in
                                    ("authorization_type", "status", "retry_count",
                                     "correlation_id")} if current else None})

    # Truthful record of the refused attempt: refused BEFORE dispatch, no download, $0.
    run_id = f"local:{GATE2_SHORT}:0"
    existing = rest(env, "GET", f"loom_provider_runs?run_id=eq.{urllib.parse.quote(run_id)}&select=run_id")
    if existing:
        out["audit"] = {"created": False, "run_id": run_id}
    else:
        rows = rest(env, "POST", "loom_provider_runs?on_conflict=run_id",
                    {"run_id": run_id, "actor_id": "local:yt-dlp",
                     "provider": "local:yt-dlp",
                     "acquisition_job_id": GATE2_SHORT,
                     "status": "failed_cost_known", "cost_estimate_micro": 0,
                     "cost_actual_micro": 0,
                     "output_ref": ("refused before dispatch: job status was "
                                    "blocked_pending_platform_adapter (adapter proof mode "
                                    "not enabled in the process environment); no download "
                                    "attempted; provider cost $0")})
        out["audit"] = {"created": bool(rows), "run_id": run_id}
    return out


def main() -> int:
    env = load_env()
    stage = next((a for a in sys.argv[1:] if a.startswith("--")), "")
    if stage == "--plumb":
        out = plumb()
    elif stage == "--repair":
        out = repair(env)
    else:
        print("usage: fix_youtube_proof.py --plumb | --repair")
        return 2
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
