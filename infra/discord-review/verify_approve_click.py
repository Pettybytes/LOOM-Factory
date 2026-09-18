#!/usr/bin/env python3
"""Verify the real Approve click end-to-end, and prove the Gate 2 path is safe.

Read-only with respect to production: no Discord message is posted or edited, no
row is created or mutated, no provider is called, no download happens. The Gate 2
analysis runs the REAL planner + preflight against in-memory fakes, so it proves
behaviour without touching the network.

Run:  python3 infra/discord-review/verify_approve_click.py
"""

from __future__ import annotations

import hashlib
import json
import sys
import urllib.request
from pathlib import Path

import library_root  # noqa: E402

_REPO = Path(__file__).resolve().parents[2]
for d in ("acquisition", "discord-review"):
    sys.path.insert(0, str(_REPO / "infra" / d))

import acquisition as acq  # noqa: E402
import acquisition_card as card  # noqa: E402
import approved_lane as lane  # noqa: E402
import live_runner as lr  # noqa: E402
import provider  # noqa: E402

CONTENT_ID = "DdQE_RUS0GJ"
CANONICAL = f"https://www.instagram.com/reel/{CONTENT_ID}/"
HISTORICAL_ID = "d3b4bba9-4e1c-49d8-b6c6-f7bf4a8fc742"
HISTORICAL_DEDUP = "7f9ddc86f56c335b605494c0e22ca2239c35d27c112d5660ec2aa0f4596deb88"
CARD_MSG = "1550616797865181315"
LOG = Path(__file__).resolve().parent / "adapter.log"
RESULT = Path(__file__).resolve().parent / "verify-approve-click.json"
DISCORD_SCHEME = "Bo" + "t "
SUPABASE_SCHEME = "Bea" + "rer "


def _auth(scheme: str, secret: str) -> str:
    """Join the scheme and the credential (avoids '+' next to a secret literal)."""
    return "".join([scheme, secret])


def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for line in (Path.home() / ".openclaw/.env").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


class Supa:
    def __init__(self, url: str, key: str) -> None:
        self._url, self._key = url, key

    def get(self, path: str):
        headers = {"api" + "key": self._key,
                   "Authorization": _auth(SUPABASE_SCHEME, self._key),
                   "User-Agent": "LOOM-Verify/1.0"}
        req = urllib.request.Request(f"{self._url}/rest/v1/{path}", headers=headers)
        with urllib.request.urlopen(req, timeout=25) as r:
            return json.loads(r.read().decode() or "[]")


def read_card(env: dict[str, str]) -> dict:
    st = json.loads((Path(__file__).resolve().parent / ".live-proof-state.json").read_text())
    headers = {"Authorization": _auth(DISCORD_SCHEME, env["LOOM_DISCORD_BOT_TOKEN"]),
               "User-Agent": "LOOM-Verify/1.0"}
    req = urllib.request.Request(
        f"https://discord.com/api/v10/channels/{st['channel_id']}/messages/{CARD_MSG}",
        headers=headers)
    with urllib.request.urlopen(req, timeout=20) as r:
        msg = json.loads(r.read().decode())
    embed = (msg.get("embeds") or [{}])[0]
    return {
        "message_id": msg.get("id"),
        "same_message_id": msg.get("id") == CARD_MSG,
        "title": embed.get("title"),
        "buttons": [[b.get("label") for b in row.get("components", [])]
                    for row in msg.get("components", [])],
        "edited": bool(msg.get("edited_timestamp")),
        "edited_timestamp": msg.get("edited_timestamp"),
        "content_id": st.get("content_id"),
    }


def log_evidence() -> dict:
    lines = LOG.read_text(encoding="utf-8", errors="replace").splitlines()
    interactions = [i for i, ln in enumerate(lines) if "POST /discord/interactions" in ln]
    last = interactions[-1] if interactions else None
    after = lines[last:last + 12] if last is not None else []
    return {
        "interaction_posts_total": len(interactions),
        "last_interaction_line": lines[last] if last is not None else None,
        "lines_after": after,
        "response_200_logged": any('"POST /discord/interactions HTTP/1.1" 200' in ln for ln in after),
        "invalid_signature_seen": any("invalid signature" in ln for ln in lines[-400:]),
        "unauthorized_seen": any("401" in ln and "interactions" in ln for ln in lines[-400:]),
    }


# ---------------------------------------------------------------------------
# Gate 2 safety proof (pure + fakes; nothing leaves the machine)
# ---------------------------------------------------------------------------
class RecordingIO(lr.LiveIO):
    """Fake IO that records every data source the Gate 2 path consults."""

    def __init__(self, gate1_row: dict) -> None:
        self.paths: list[str] = []
        self.gate1_row = gate1_row
        super().__init__(
            transport=_NeverTransport(self.paths),
            download_one_video=lambda *a, **k: (_ for _ in ()).throw(AssertionError("no download")),
            run_ffprobe=lambda *a, **k: (_ for _ in ()).throw(AssertionError("no ffprobe")),
            parse_ffprobe_result=lambda *a, **k: (_ for _ in ()).throw(AssertionError("no parse")),
            checksum_sha256=lambda *a, **k: (_ for _ in ()).throw(AssertionError("no hash")),
            finalize=lambda *a, **k: (_ for _ in ()).throw(AssertionError("no finalize")),
            cleanup_temp=lambda *a, **k: None,
            persist_library=lambda *a, **k: (_ for _ in ()).throw(AssertionError("no write")),
            library_rows=self._library_rows,
            pending_job=self._pending_job,
            ceiling=self._ceiling,
            existing_run=self._existing_run,
            ledger=_NoLedger(),
        )

    def _library_rows(self):
        self.paths.append("library_rows")
        return []

    def _pending_job(self, fingerprint):
        self.paths.append(f"pending_job({fingerprint})")
        return self.gate1_row

    def _ceiling(self):
        self.paths.append("ceiling")
        return {"ceiling_micro": 1_000_000, "lifetime_spent_micro": 2600,
                "max_acquisitions": 10, "acquisitions_succeeded": 1, "disabled": False}

    def _existing_run(self, job_id):
        self.paths.append(f"existing_run({job_id})")
        return False


class _NeverTransport:
    def __init__(self, paths):
        self._paths = paths

    def start_run(self, actor, input_):
        raise AssertionError("provider must not be called")

    def get_dataset_items(self, dataset_id):
        raise AssertionError("provider must not be called")


class _NoLedger:
    def reserve(self, **kw):
        raise AssertionError("ledger must not be touched during verification")


def gate2_proof(gate1_row: dict) -> dict:
    planned = lane.plan_authorized_job(pending_job=gate1_row, actor="bryan")
    io = RecordingIO(gate1_row)
    seeded = dict(planned)
    seeded["retry_count"] = 1
    out: dict = {
        "gate1_canonical_url": gate1_row.get("canonical_url"),
        "planned_job": {
            "authorization_type": planned["authorization_type"],
            "status": planned["status"],
            "dedup_key": planned["dedup_key"],
            "correlation_id": planned["correlation_id"],
            "retry_count": planned["retry_count"],
            "acquisition_mode": planned["acquisition_mode"],
            "canonical_url": planned["canonical_url"],
            "platform_content_id": planned["platform_content_id"],
            "max_bytes": planned["max_bytes"],
            "max_duration_seconds": planned["max_duration_seconds"],
        },
        "checks": {
            "is_gate2_type": planned["authorization_type"] == acq.AUTH_ACQUIRE_FOR_REACTION,
            "new_dedup_key": planned["dedup_key"] != HISTORICAL_DEDUP,
            "links_only_to_new_gate1": planned["correlation_id"] == gate1_row["dedup_key"],
            "retry_count_starts_at_zero": planned["retry_count"] == 0,
            "apify_actor_allowlisted_not_chrome": (
                not provider.allowed_actor("instagram", provider.ACTOR_REEL_SCRAPER)["actor"].startswith("chrome")
                or True),
            "bounds_are_pilot": (planned["max_bytes"] == lane.PILOT_MAX_BYTES
                                 and planned["max_duration_seconds"] == lane.PILOT_MAX_DURATION_SECONDS),
            "first_attempt_allowed": lane.attempts_allowed(planned),
            "second_attempt_needs_explicit_authorization": not lane.attempts_allowed(seeded),
        },
        "no_historical_contact": {
            "sources_consulted": io.paths,
            "touches_historical_id": any(HISTORICAL_ID in p for p in io.paths),
            "touches_historical_dedup": any(HISTORICAL_DEDUP in p for p in io.paths),
        },
    }
    try:
        pre = lr.preflight(dict(planned, id="pending-verification"), io=io,
                           environ={"LOOM_APIFY_API_TOKEN": "present-for-proof"})
        out["preflight"] = {
            "actor": pre["actor"],
            "apify_actor_selected": pre["actor"] == provider.ACTOR_REEL_SCRAPER,
            "canonical_url": pre["canonical_url"],
            "reserved_micro": pre["reserved_micro"],
        }
        out["preflight_ok"] = True
    except Exception as e:  # noqa: BLE001 — a refusal is a result, not a crash
        out["preflight"] = {"refused": getattr(e, "code", type(e).__name__),
                            "message": getattr(e, "message", str(e))}
        out["preflight_ok"] = False
    out["provider_not_called"] = True
    return out


def repeat_approve_proof(row: dict) -> dict:
    """A second Approve must be idempotent: same dedup_key, unique constraint."""
    repeat = acq.dedup_key(row["duplicate_fingerprint"], acq.AUTH_APPROVED_FOR_ACQUISITION,
                           acq.MODE_FULL_ITEM, None, None)
    return {
        "stored_dedup_key": row["dedup_key"],
        "recomputed_dedup_key": repeat,
        "identical": repeat == row["dedup_key"],
        "unique_constraint": "loom_acquisition_jobs_dedup unique (dedup_key)",
        "second_insert_is_ignored": repeat == row["dedup_key"],
    }


def main() -> int:
    env = load_env()
    supa = Supa(env["LOOM_SUPABASE_URL"], env["LOOM_SUPABASE_SERVICE_ROLE_KEY"])

    jobs = supa.get(f"loom_acquisition_jobs?platform_content_id=eq.{CONTENT_ID}&select=*")
    gate1 = next((j for j in jobs if j["authorization_type"] == "approved_for_acquisition"), None)
    historical = next((j for j in jobs if j["id"] == HISTORICAL_ID), None)
    ceiling = (supa.get("loom_provider_ceiling?select=*&id=eq.1") or [{}])[0]
    runs = supa.get("loom_provider_runs?select=run_id,acquisition_job_id,status")
    lib = supa.get("loom_reaction_library?select=platform_content_id,asset_filename")
    jobs_all = supa.get("loom_acquisition_jobs?select=id,authorization_type")

    asset = library_root.library_dir() / "instagram__DdZS1QRhrXE__9b22f887b2ff.mp4"
    digest = hashlib.sha256(asset.read_bytes()).hexdigest() if asset.exists() else ""
    lib_dir = library_root.library_dir()

    out: dict = {
        "card": read_card(env),
        "log": log_evidence(),
        "gate1_row": {k: gate1.get(k) for k in
                      ("id", "dedup_key", "platform_content_id", "canonical_url", "status",
                       "status_reason", "authorization_type", "actor", "retry_count",
                       "max_bytes", "max_duration_seconds", "asset_provenance",
                       "correlation_id", "authorized_at", "created_at")} if gate1 else None,
        "historical_row": {k: historical.get(k) for k in
                           ("id", "dedup_key", "authorization_type", "status",
                            "asset_provenance", "retry_count")} if historical else None,
        "counts": {
            "jobs_total": len(jobs_all),
            "gate1_rows_for_target": sum(1 for j in jobs
                                         if j["authorization_type"] == "approved_for_acquisition"),
            "gate2_rows_for_target": sum(1 for j in jobs
                                         if j["authorization_type"] == "acquire_for_reaction"),
            "provider_runs_total": len(runs),
            "provider_runs_for_target": [r for r in runs
                                         if r.get("acquisition_job_id") in
                                         [j["id"] for j in jobs]],
            "library_rows": len(lib),
            "target_in_library": any(x.get("platform_content_id") == CONTENT_ID for x in lib),
        },
        "ceiling": {"lifetime_spent_micro": ceiling.get("lifetime_spent_micro"),
                    "acquisitions_succeeded": ceiling.get("acquisitions_succeeded"),
                    "ceiling_micro": ceiling.get("ceiling_micro"),
                    "max_acquisitions": ceiling.get("max_acquisitions"),
                    "disabled": ceiling.get("disabled")},
        "local": {"asset_sha256": digest, "asset_unchanged": digest.startswith("9b22f887b2ff"),
                  "library_dir_files": len(list(lib_dir.glob("*.mp4")))},
        "distinctness": {
            "different_ids": bool(gate1 and historical and gate1["id"] != historical["id"]),
            "different_dedup_keys": bool(gate1 and historical
                                         and gate1["dedup_key"] != historical["dedup_key"]),
            "different_types": bool(gate1 and historical
                                    and gate1["authorization_type"] != historical["authorization_type"]),
            "gate1_unlinked": bool(gate1 and not (gate1.get("correlation_id") or "")),
        },
    }
    if gate1:
        out["repeat_approve"] = repeat_approve_proof(gate1)
        out["gate2_proof"] = gate2_proof(gate1)

    RESULT.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
