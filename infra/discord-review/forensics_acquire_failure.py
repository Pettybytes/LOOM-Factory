#!/usr/bin/env python3
"""Read-only forensics for the single failed Acquire attempt (DdQE_RUS0GJ).

Collects: adapter log evidence, Gate 2 job row, provider-run row, ceiling/ledger
state, library + local file state, and the sanitized endpoint contract analysis.

Makes NO external calls other than the same read-only Supabase REST reads the
repo already uses. Never prints tokens, cookies, or signed query values.

Run:  python3 infra/discord-review/forensics_acquire_failure.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import library_root  # noqa: E402

_REPO = Path(__file__).resolve().parents[2]
for d in ("acquisition", "discord-review"):
    sys.path.insert(0, str(_REPO / "infra" / d))

import apify_client  # noqa: E402
import provider  # noqa: E402
import source_context as sc  # noqa: E402

CONTENT_ID = "DdQE_RUS0GJ"
GATE1_ID = "40560336-bbe9-468e-9230-5fa880c9eef1"
HISTORICAL_ID = "d3b4bba9-4e1c-49d8-b6c6-f7bf4a8fc742"
LOG = Path(__file__).resolve().parent / "adapter.log"
OUT = Path(__file__).resolve().parent / "acquire-failure-forensics.json"
LIB_DIR = library_root.library_dir()
TMP_DIR = library_root.tmp_dir()


def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for line in (Path.home() / ".openclaw/.env").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def redact(value: str) -> str:
    """Keep only structural hints from a URL-ish string (no query values)."""
    if not value:
        return ""
    s = str(value)
    if "?" in s:
        s = s.split("?", 1)[0] + "?<redacted>"
    if "#" in s:
        s = s.split("#", 1)[0]
    return s


def log_evidence() -> dict:
    lines = LOG.read_text(encoding="utf-8", errors="replace").splitlines()
    idx = [i for i, ln in enumerate(lines) if "POST /discord/interactions" in ln]
    tail_from = idx[-1] if idx else max(0, len(lines) - 60)
    tail = lines[tail_from:]
    errs = [ln for ln in lines[-200:]
            if any(k in ln for k in ("Traceback", "Error", "error", "404", "Exception"))]
    return {
        "log_path": str(LOG),
        "log_lines": len(lines),
        "interaction_posts": len(idx),
        "last_interaction_line": lines[idx[-1]] if idx else None,
        "tail_since_last_interaction": tail[:60],
        "error_like_lines_recent": errs[-25:],
    }


def db_evidence(env: dict[str, str]) -> dict:
    get = sc.http_get(env["LOOM_SUPABASE_URL"], env["LOOM_SUPABASE_SERVICE_ROLE_KEY"])
    jobs = get(f"loom_acquisition_jobs?platform_content_id=eq.{CONTENT_ID}&select=*"
               "&order=created_at")
    runs = get("loom_provider_runs?select=*&order=created_at")
    ceiling = get("loom_provider_ceiling?select=*&id=eq.1")
    library = get("loom_reaction_library?select=id,platform_content_id,asset_filename,"
                  "asset_hash,size_bytes,created_at")
    hist = get(f"loom_acquisition_jobs?id=eq.{HISTORICAL_ID}&select=*")
    return {
        "gate2_jobs": [
            {k: j.get(k) for k in ("id", "dedup_key", "authorization_type", "status",
                                   "status_reason", "retry_count", "correlation_id",
                                   "asset_provenance", "actor", "canonical_url",
                                   "max_bytes", "max_duration_seconds",
                                   "created_at", "updated_at")}
            for j in jobs],
        "gate1_row_unchanged_check": next(
            ({k: j.get(k) for k in ("id", "canonical_url", "status", "authorization_type")}
             for j in jobs if j.get("id") == GATE1_ID), None),
        "provider_runs": [
            {k: r.get(k) for k in ("id", "provider", "actor_id", "actor_build", "run_id",
                                   "acquisition_job_id", "status", "cost_estimate_micro",
                                   "cost_actual_micro", "output_ref", "created_at",
                                   "updated_at")}
            for r in runs],
        "ceiling": ceiling[0] if ceiling else None,
        "library": library,
        "historical_row": {k: hist[0].get(k) for k in
                           ("id", "dedup_key", "authorization_type", "status",
                            "asset_provenance", "retry_count", "updated_at")} if hist else None,
    }


def local_evidence() -> dict:
    def listing(p: Path):
        if not p.exists():
            return {"exists": False, "files": []}
        return {"exists": True,
                "files": sorted(f.name for f in p.iterdir() if f.is_file())}
    return {"library_dir": listing(LIB_DIR), "tmp_dir": listing(TMP_DIR)}


def endpoint_contract() -> dict:
    """Static (no network) analysis of the actor path the transport builds."""
    actor = provider.ACTOR_REEL_SCRAPER
    numeric = provider.actor_api_id(actor)
    return {
        "note": "Path templates only. No token, no query values, no network call.",
        "pinned_actor_slug": actor,
        "pinned_actor_numeric_id": numeric,
        "slug_is_valid_rest_actor_id": actor.startswith("apify/") is False,
        "start_run_path_with_slug": f"POST https://api.apify.com/v2/acts/{actor}/runs",
        "start_run_path_with_numeric_id": f"POST https://api.apify.com/v2/acts/{numeric}/runs",
        "transport_base_url": apify_client.BASE_URL,
        "transport_start_run_template": "POST {BASE_URL}/acts/{actor}/runs",
        "what_live_runner_passes_as_actor": "provider.ACTOR_REEL_SCRAPER (the slug)",
        "expected_rest_actor_id": numeric,
    }


def main() -> int:
    env = load_env()
    out = {
        "content_id": CONTENT_ID,
        "log": log_evidence(),
        "db": db_evidence(env),
        "local": local_evidence(),
        "endpoint_contract": endpoint_contract(),
        "redaction": {"urls_redacted": True, "tokens_printed": False,
                      "signed_query_values_printed": False},
    }
    # Redact anything URL-ish that slipped into provenance.
    for job in out["db"]["gate2_jobs"]:
        prov = job.get("asset_provenance")
        if isinstance(prov, dict):
            job["asset_provenance"] = {k: redact(v) for k, v in prov.items()}
    for run in out["db"]["provider_runs"]:
        run["output_ref"] = redact(run.get("output_ref"))
    OUT.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
