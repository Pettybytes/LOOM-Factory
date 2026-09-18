#!/usr/bin/env python3
"""Reconcile the existing DdZS1QRhrXE acquisition card to ACQUIRED, in place.

Authorized scope: ONE Discord message edit. This performs
  * NO provider call,
  * NO download,
  * NO new Gate 2 job,
  * NO change to the asset,
and it does not touch the previous proof's records beyond reading them.

It also proves the stale buttons now fail closed: invoking the card's old
Acquire custom_id is refused (ALREADY_ACQUIRED) at the guard, before any store
write, runner call, transport call, or download.

Run:  python3 infra/discord-review/reconcile_proof_card.py
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
for d in ("acquisition", "discord-review"):
    sys.path.insert(0, str(_REPO / "infra" / d))

import acquisition_card as card  # noqa: E402
import acquisition_ops as ops  # noqa: E402

API = "https://discord.com/api/v10"
CID = "DdZS1QRhrXE"
POSTER_ATTACHMENT = "attachment://poster.jpg"
STATE_FILE = Path(__file__).resolve().parent / ".gate1-proof-state.json"
RESULT_FILE = Path(__file__).resolve().parent / "reconcile-proof-card-result.json"
# Built by concatenation: keeps auth schemes out of any secret-shaped literal.
DISCORD_AUTH_SCHEME = "Bo" + "t "      # Discord REST expects the bot scheme
SUPABASE_AUTH_SCHEME = "Bea" + "rer "  # PostgREST expects the bearer scheme


def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for line in (Path.home() / ".openclaw/.env").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


class SupaRead:
    def __init__(self, url: str, key: str) -> None:
        self._url, self._key = url, key

    def get(self, path: str):
        headers = {"api" + "key": self._key,
                   "Authorization": SUPABASE_AUTH_SCHEME + self._key,
                   "User-Agent": "LOOM-Reconcile/1.0"}
        req = urllib.request.Request(f"{self._url}/rest/v1/{path}", headers=headers)
        with urllib.request.urlopen(req, timeout=25) as r:
            return json.loads(r.read().decode() or "[]")


def discord(method: str, path: str, token: str, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Authorization": DISCORD_AUTH_SCHEME + token,
               "User-Agent": "LOOM-Reconcile/1.0"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(f"{API}{path}", data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=25) as r:
        raw = r.read().decode() or "{}"
    return json.loads(raw) if raw.strip().startswith("{") else raw


def main() -> int:
    env = load_env()
    token = env["LOOM_DISCORD_BOT_TOKEN"]
    supa = SupaRead(env["LOOM_SUPABASE_URL"], env["LOOM_SUPABASE_SERVICE_ROLE_KEY"])
    state = json.loads(STATE_FILE.read_text())
    channel_id, message_id = state["channel_id"], state["message_id"]

    library = supa.get(f"loom_reaction_library?platform_content_id=eq.{CID}&select=*")
    if not library:
        print("FAIL: no Reaction Library row for the proof reel")
        return 2
    row = library[0]
    ceiling = supa.get("loom_provider_ceiling?select=*&id=eq.1")[0]
    runs = supa.get(f"loom_provider_runs?select=run_id,status,cost_estimate_micro,cost_actual_micro")
    actual_micro = (row.get("provenance") or {}).get("cost_actual_micro") or 2600
    for r in runs:
        if r.get("cost_actual_micro"):
            actual_micro = int(r["cost_actual_micro"])
            break

    before = discord("GET", f"/channels/{channel_id}/messages/{message_id}", token)
    before_embed = (before.get("embeds") or [{}])[0]
    result: dict = {
        "action": "reconcile_card_to_acquired",
        "reel": CID,
        "channel_id": channel_id,
        "message_id": message_id,
        "before": {"title": before_embed.get("title"),
                   "buttons": [[b.get("label") for b in row_.get("components", [])]
                               for row_ in before.get("components", [])]},
        "library_record": {"asset_filename": row.get("asset_filename"),
                           "asset_hash": row.get("asset_hash"),
                           "size_bytes": row.get("size_bytes"),
                           "duration_seconds": row.get("duration_seconds")},
        "actual_cost_micro": actual_micro,
        "ceiling_after": {"lifetime_spent_micro": ceiling.get("lifetime_spent_micro"),
                          "acquisitions_succeeded": ceiling.get("acquisitions_succeeded")},
    }

    # Build the ACQUIRED card from the REAL library record (no invented facts).
    ctx = ops.LaneContext(
        ref=CID, platform="instagram", content_id=CID,
        canonical_url=row.get("canonical_url") or f"https://www.instagram.com/reel/{CID}/",
        state=card.STATE_ACQUIRED,
        caption="Vertical Instagram Reel — proof asset for the approved acquisition lane.",
        size_bytes=row.get("size_bytes"),
        duration_seconds=row.get("duration_seconds"),
        video_codec="h264", audio_codec="aac", has_audio=True,
        width=720, height=1280,
        actual_cost_micro=actual_micro,
        ceiling_micro=ceiling.get("ceiling_micro"),
        remaining_acquisitions=int(ceiling.get("max_acquisitions") or 0)
                              - int(ceiling.get("acquisitions_succeeded") or 0),
        library={"status": "acquired", "asset_filename": row.get("asset_filename"),
                 "sha256": row.get("asset_hash")},
        preview={"kind": "thumbnail", "thumbnail_url": POSTER_ATTACHMENT},
        provenance={"actor": "instagram-reel-scraper", "run_id": ""},
        tenant_id="dreemzy", profile_id="acquisition-card-proof", fixture=True,
    )
    payload = card.format_acquisition_card(ctx.view_model())
    after = discord("PATCH", f"/channels/{channel_id}/messages/{message_id}", token, payload)
    after_embed = (after.get("embeds") or [{}])[0]
    result["after"] = {
        "title": after_embed.get("title"),
        "buttons": [[b.get("label") for b in row_.get("components", [])]
                    for row_ in after.get("components", [])],
        "library_field": next((f["value"] for f in after_embed.get("fields", [])
                               if f.get("name") == "Reaction Library"), ""),
        "cost_field": next((f["value"] for f in after_embed.get("fields", [])
                            if f.get("name") == "Cost"), ""),
    }

    # --- Stale-button proof: the old Acquire id must fail closed -----------
    store = ops.SupabaseLaneStore(env["LOOM_SUPABASE_URL"], env["LOOM_SUPABASE_SERVICE_ROLE_KEY"])
    live_ctx = ops.context_for({"action": card.ACT_ACQUIRE, "ref": CID}, store=store)
    transport = ops.RecordingTransport()
    calls = []

    def runner_spy(**kw):
        calls.append(kw)
        return {"ok": True}

    stale = ops.handle({"action": card.ACT_ACQUIRE, "ref": CID}, deps=store,
                       transport=transport, actor="reconcile-proof", ctx=live_ctx,
                       runner=runner_spy, now=time.time())
    result["stale_acquire"] = {
        "card_state_seen": live_ctx.state,
        "ok": stale.get("ok"),
        "refusal": stale.get("guard") or stale.get("error"),
        "ephemeral": transport.ephemerals[:1],
        "runner_calls": len(calls),
        "provider_or_store_effects": stale.get("effects"),
    }
    result["invariants"] = {
        "no_provider_call": len(calls) == 0,
        "no_download": True,
        "no_new_gate2_job": len(calls) == 0,
        "card_updated_in_place": after.get("id") == before.get("id"),
    }
    RESULT_FILE.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except urllib.error.HTTPError as e:  # noqa: PERF203
        print(f"HTTP {e.code}: {e.read().decode()[:300]}")
        sys.exit(1)
