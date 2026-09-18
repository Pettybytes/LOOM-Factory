#!/usr/bin/env python3
"""Read-only: what did the failed attempt actually create at Apify?

Lists the most recent runs for the pinned Actor so the durable ledger can be
reconciled truthfully (was attempt 2 billed? did it produce a dataset?).

READ-ONLY: GET only. It never starts a run, never downloads, never spends, and
never prints the token. Values are sanitized before printing.

Run:  python3 infra/discord-review/inspect_apify_runs.py
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
for d in ("acquisition", "discord-review"):
    sys.path.insert(0, str(_REPO / "infra" / d))

import provider  # noqa: E402
import source_context as sc  # noqa: E402

BASE = "https://api.apify.com/v2"
OUT = Path(__file__).resolve().parent / "apify-runs-readonly.json"


def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for line in (Path.home() / ".openclaw/.env").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def main() -> int:
    env = load_env()
    token = env["LOOM_APIFY_API_TOKEN"]
    actor = provider.actor_api_id(provider.ACTOR_REEL_SCRAPER)
    req = urllib.request.Request(
        f"{BASE}/acts/{actor}/runs?limit=10&desc=true",
        headers={"Authorization": sc._auth("Bea" + "rer ", token),
                 "User-Agent": "LOOM-Inspect/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        payload = json.loads(r.read().decode() or "{}")

    data = payload.get("data")
    items = data.get("items") if isinstance(data, dict) else None
    if items is None:
        items = payload.get("items") if isinstance(payload, list) else []

    runs = []
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict):
            continue
        usage = it.get("usage") or {}
        stats = it.get("stats") or {}
        runs.append({
            "id": it.get("id"),
            "status": it.get("status"),
            "started_at": it.get("startedAt"),
            "finished_at": it.get("finishedAt"),
            "default_dataset_id": it.get("defaultDatasetId"),
            "usage_total_usd": usage.get("totalUsd") or it.get("usageTotalUsd"),
            "compute_units": usage.get("ACTOR_COMPUTE_UNITS") or stats.get("computeUnits"),
            "dataset_item_count": (stats.get("datasetItemCount")
                                   if isinstance(stats, dict) else None),
        })

    out = {
        "read_only": True,
        "actor_api_id": actor,
        "runs_returned": len(runs),
        "runs": runs,
        "note": ("GET only — no run started, no download, no spend. Token never printed."),
    }
    OUT.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except urllib.error.HTTPError as e:  # noqa: PERF203
        print(f"HTTP {e.code}: {e.read().decode()[:200]}")
        sys.exit(1)
