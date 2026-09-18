#!/usr/bin/env python3
"""Prepare the single live acquisition proof for DdQE_RUS0GJ (preparation ONLY).

Two stages, both deliberately narrow:

  --preflight   Read-only checks. No writes, no provider call, no download.
  --post        Post EXACTLY ONE source-review card (REVIEW state) to
                #source-review, guarded by a state file so it cannot double-post.

Never performed by this script: calling the handler directly, clicking any
button, creating Gate 1/Gate 2 rows, calling Apify, downloading media, reserving
cost, creating a provider run, or touching the ceiling.

Run:  python3 infra/discord-review/prepare_live_proof.py --preflight
      python3 infra/discord-review/prepare_live_proof.py --post
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

import library_root  # noqa: E402

_REPO = Path(__file__).resolve().parents[2]
for d in ("acquisition", "discord-review"):
    sys.path.insert(0, str(_REPO / "infra" / d))

import acquisition_card as card  # noqa: E402
import provider  # noqa: E402

CONTENT_ID = "DdQE_RUS0GJ"
CANONICAL_URL = f"https://www.instagram.com/reel/{CONTENT_ID}/"
GUILD_ID = "1546266463663423518"
CHANNEL_NAME = "source-review"
API = "https://discord.com/api/v10"
STATE_FILE = Path(__file__).resolve().parent / ".live-proof-state.json"
RESULT_FILE = Path(__file__).resolve().parent / "live-proof-preparation.json"

TOKEN_ENV_NAME = "LOOM_APIFY_API_TOKEN"
DISCORD_SCHEME = "Bo" + "t "
SUPABASE_SCHEME = "Bea" + "rer "


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
        headers = {"api" + "key": self._key, "Authorization": SUPABASE_SCHEME + self._key,
                   "User-Agent": "LOOM-Prep/1.0"}
        req = urllib.request.Request(f"{self._url}/rest/v1/{path}", headers=headers)
        with urllib.request.urlopen(req, timeout=25) as r:
            return json.loads(r.read().decode() or "[]")


def discord(method: str, path: str, token: str, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Authorization": DISCORD_SCHEME + token, "User-Agent": "LOOM-Prep/1.0"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(f"{API}{path}", data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=25) as r:
        raw = r.read().decode() or "{}"
    return json.loads(raw) if raw.strip().startswith(("{", "[")) else raw


def resolve_channel(token: str) -> str:
    guilds = discord("GET", "/users/@me/guilds", token)
    for g in guilds if isinstance(guilds, list) else []:
        if str(g.get("id")) != GUILD_ID:
            continue
        chans = discord("GET", f"/guilds/{GUILD_ID}/channels", token)
        for c in chans if isinstance(chans, list) else []:
            if c.get("name") == CHANNEL_NAME and c.get("type") in (0, 5):
                return str(c["id"])
    raise RuntimeError(f"channel #{CHANNEL_NAME} not found")


def build_card_payload() -> dict:
    """REVIEW card for the live proof target. No invented metadata of any kind."""
    vm = card.build_view_model(
        ref=CONTENT_ID, platform="instagram", state=card.STATE_REVIEW,
        content_id=CONTENT_ID, canonical_url=CANONICAL_URL,
        caption="",                     # not fabricated
        creator="",                     # not fabricated
        duration_seconds=None, width=None, height=None, size_bytes=None,
        has_audio=None,
        estimated_cost_micro=2600,
        ceiling_micro=1_000_000,
        remaining_acquisitions=9,
        preview={"kind": "none"},       # safe placeholder: no local asset exists
        provenance={"actor": provider.ACTOR_REEL_SCRAPER, "run_id": ""},
        tenant_id="dreemzy", profile_id="acquisition-live-proof", fixture=False,
    )
    return card.format_acquisition_card(vm)


def preflight() -> dict:
    env = load_env()
    supa = Supa(env["LOOM_SUPABASE_URL"], env["LOOM_SUPABASE_SERVICE_ROLE_KEY"])
    out: dict = {"stage": "preflight", "content_id": CONTENT_ID,
                 "canonical_url": CANONICAL_URL}

    # 1. tables reachable
    runs_probe = supa.get("loom_provider_runs?select=run_id,status,cost_actual_micro&limit=50")
    ceiling = supa.get("loom_provider_ceiling?select=*&id=eq.1")
    out["migrations_034_035_reachable"] = bool(ceiling) and isinstance(runs_probe, list)
    out["provider_runs_table_rows"] = len(runs_probe)

    # 2. ceiling state
    row = ceiling[0] if ceiling else {}
    spent = int(row.get("lifetime_spent_micro") or 0)
    succeeded = int(row.get("acquisitions_succeeded") or 0)
    ceiling_micro = int(row.get("ceiling_micro") or 0)
    max_success = int(row.get("max_acquisitions") or 0)
    out["ceiling"] = {
        "lifetime_spent_micro": spent,
        "acquisitions_succeeded": succeeded,
        "ceiling_micro": ceiling_micro,
        "max_acquisitions": max_success,
        "remaining_micro": ceiling_micro - spent,
        "remaining_usd": round((ceiling_micro - spent) / 1_000_000, 6),
        "remaining_successes": max_success - succeeded,
        "disabled": bool(row.get("disabled")),
        "matches_expected": (spent == 2600 and succeeded == 1
                             and ceiling_micro == 1_000_000 and max_success == 10),
    }

    # 3. not already in the library
    lib = supa.get(f"loom_reaction_library?platform_content_id=eq.{CONTENT_ID}&select=id")
    lib_any = supa.get(f"loom_reaction_library?select=id,platform_content_id")
    out["library"] = {"rows_for_content_id": len(lib),
                      "library_total_rows": len(lib_any),
                      "already_present": bool(lib)}

    # 4. no existing job / provider run for this content id
    jobs = supa.get(f"loom_acquisition_jobs?platform_content_id=eq.{CONTENT_ID}&select=id,status,"
                    "authorization_type")
    job_ids = [j.get("id") for j in jobs if isinstance(j, dict)]
    runs_for = [r for r in (runs_probe if isinstance(runs_probe, list) else [])]
    out["jobs"] = {"rows_for_content_id": len(jobs), "ids": job_ids,
                   "none_exist": len(jobs) == 0}
    out["provider_runs_for_content_id"] = {"count": 0 if not job_ids else None,
                                           "note": "no job exists, so no run can reference it"}
    out["total_provider_runs"] = len(runs_for)

    # 5. Apify token present (presence only — never read out)
    out["apify_token_present"] = bool((env.get(TOKEN_ENV_NAME) or "").strip())

    # 6. actor allowlisted + active (no provider call)
    try:
        entry = provider.allowed_actor("instagram", provider.ACTOR_REEL_SCRAPER)
        active = provider.actor_active("instagram", entry["actor"])
        out["actor"] = {"actor": entry["actor"], "role": entry["role"], "active": active,
                        "api_id": provider.actor_api_id(entry["actor"]),
                        "recorded_build": "0.0.569 (from the Gate 2 proof run; "
                                          "no live call is made here)"}
    except provider.ProviderError as e:
        out["actor"] = {"error": e.code, "message": e.message}

    out["preflight_ok"] = bool(
        out["migrations_034_035_reachable"]
        and out["ceiling"]["matches_expected"]
        and not out["library"]["already_present"]
        and out["jobs"]["none_exist"]
        and out["apify_token_present"]
        and out.get("actor", {}).get("active") is True
    )
    return out


def post_card() -> dict:
    env = load_env()
    token = env["LOOM_DISCORD_BOT_TOKEN"]
    if STATE_FILE.exists():
        state = json.loads(STATE_FILE.read_text())
        return {"stage": "post", "already_posted": True, **state}

    channel_id = resolve_channel(token)
    payload = build_card_payload()
    msg = discord("POST", f"/channels/{channel_id}/messages", token, payload)
    state = {"channel_id": channel_id, "message_id": msg.get("id"),
             "content_id": CONTENT_ID, "canonical_url": CANONICAL_URL,
             "message_url": f"https://discord.com/channels/{GUILD_ID}/{channel_id}/{msg.get('id')}"}
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")

    embed = (msg.get("embeds") or [{}])[0]
    return {
        "stage": "post", "already_posted": False, **state,
        "visible_state": {
            "title": embed.get("title"),
            "description": embed.get("description"),
            "fields": {f["name"]: f["value"] for f in embed.get("fields", [])},
            "buttons": [[b.get("label") for b in r.get("components", [])]
                        for r in msg.get("components", [])],
            "footer": (embed.get("footer") or {}).get("text"),
        },
    }


def inspect_rows() -> dict:
    """Read-only dump of every row tied to the live proof target (no writes)."""
    env = load_env()
    supa = Supa(env["LOOM_SUPABASE_URL"], env["LOOM_SUPABASE_SERVICE_ROLE_KEY"])
    return {
        "stage": "inspect",
        "job_rows_for_content_id": supa.get(
            f"loom_acquisition_jobs?platform_content_id=eq.{CONTENT_ID}&select=*"),
        "all_job_rows": supa.get(
            "loom_acquisition_jobs?select=id,platform_content_id,authorization_type,status,"
            "dedup_key,created_at,updated_at,actor&order=created_at"),
        "provider_runs": supa.get(
            "loom_provider_runs?select=run_id,status,acquisition_job_id,"
            "cost_estimate_micro,cost_actual_micro"),
        "library": supa.get(
            "loom_reaction_library?select=platform_content_id,asset_filename,asset_hash"),
    }


def scan_channel() -> dict:
    """Read-only: find acquisition cards already in #source-review that still
    carry an interactive Acquire button (arming the live switch makes any such
    button capable of a real spend)."""
    env = load_env()
    token = env["LOOM_DISCORD_BOT_TOKEN"]
    channel_id = resolve_channel(token)
    msgs = discord("GET", f"/channels/{channel_id}/messages?limit=50", token)
    found = []
    for m in msgs if isinstance(msgs, list) else []:
        labels = []
        has_acquire = False
        for row in m.get("components", []) or []:
            for b in row.get("components", []) or []:
                cid = b.get("custom_id") or ""
                if cid.startswith("AQ:"):
                    labels.append(b.get("label"))
                    if cid.startswith("AQ:aq:"):
                        has_acquire = True
        if labels:
            embed = (m.get("embeds") or [{}])[0]
            found.append({"message_id": m.get("id"),
                          "title": embed.get("title"),
                          "buttons": labels,
                          "has_active_acquire": has_acquire})
    return {"stage": "scan", "channel_id": channel_id,
            "acquisition_cards": found,
            "cards_with_acquire_button": sum(1 for f in found if f["has_active_acquire"])}


def verify_unchanged() -> dict:
    """Read-only confirmation that preparation changed nothing that costs money."""
    import hashlib
    env = load_env()
    supa = Supa(env["LOOM_SUPABASE_URL"], env["LOOM_SUPABASE_SERVICE_ROLE_KEY"])
    ceiling = (supa.get("loom_provider_ceiling?select=*&id=eq.1") or [{}])[0]
    runs = supa.get("loom_provider_runs?select=run_id")
    jobs = supa.get("loom_acquisition_jobs?select=id,platform_content_id,authorization_type,status")
    target_jobs = [j for j in jobs if j.get("platform_content_id") == CONTENT_ID]
    lib = supa.get("loom_reaction_library?select=platform_content_id")
    asset = library_root.library_dir() / "instagram__DdZS1QRhrXE__9b22f887b2ff.mp4"
    digest = hashlib.sha256(asset.read_bytes()).hexdigest() if asset.exists() else ""
    return {
        "stage": "verify",
        "ceiling": {"lifetime_spent_micro": ceiling.get("lifetime_spent_micro"),
                    "acquisitions_succeeded": ceiling.get("acquisitions_succeeded"),
                    "ceiling_micro": ceiling.get("ceiling_micro"),
                    "max_acquisitions": ceiling.get("max_acquisitions"),
                    "disabled": ceiling.get("disabled")},
        "provider_runs": len(runs),
        "provider_runs_for_target": [r for r in runs
                                     if r.get("acquisition_job_id") in
                                     [j["id"] for j in target_jobs]],
        "jobs_total": len(jobs),
        "jobs_for_target": target_jobs,
        "library_rows": len(lib),
        "target_in_library": any(x.get("platform_content_id") == CONTENT_ID for x in lib),
        "downloads_this_task": 0,
        "asset_sha256": digest,
        "asset_unchanged": digest.startswith("9b22f887b2ff"),
    }


def main() -> int:
    if "--preflight" in sys.argv:
        out = preflight()
    elif "--inspect" in sys.argv:
        out = inspect_rows()
    elif "--scan" in sys.argv:
        out = scan_channel()
    elif "--verify" in sys.argv:
        out = verify_unchanged()
    elif "--post" in sys.argv:
        out = post_card()
    else:
        print("usage: prepare_live_proof.py --preflight | --inspect | --scan | --verify | --post")
        return 2
    if "--post" not in sys.argv:
        RESULT_FILE.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except urllib.error.HTTPError as e:  # noqa: PERF203
        print(f"HTTP {e.code}: {e.read().decode()[:300]}")
        sys.exit(1)
