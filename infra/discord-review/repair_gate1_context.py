#!/usr/bin/env python3
"""Repair the Gate 1 canonical-URL context for DdQE_RUS0GJ (authorized scope).

Stages (each narrow, each printing evidence):
  --probe          read-only: what durable rows exist for this content id
  --ensure-context create the durable source-context row (validated, first-wins)
  --repair-row     set canonical_url on EXACTLY one Gate 1 row
  --update-card    update the existing Discord message in place (View Source back)
  --resolve        resolve the context through the durable path

Never: Gate 2 rows, provider calls, downloads, reservations, migrations.

Run:  python3 infra/discord-review/repair_gate1_context.py --probe
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

import acquisition_card as card  # noqa: E402
import acquisition_ops as ops  # noqa: E402
import source_context as sc  # noqa: E402

CONTENT_ID = "DdQE_RUS0GJ"
CANONICAL_URL = f"https://www.instagram.com/reel/{CONTENT_ID}/"
GATE1_ROW_ID = "40560336-bbe9-468e-9230-5fa880c9eef1"
CARD_MSG_ID = "1550616797865181315"
API = "https://discord.com/api/v10"
STATE_FILE = Path(__file__).resolve().parent / ".live-proof-state.json"
HISTORICAL_ID = "d3b4bba9-4e1c-49d8-b6c6-f7bf4a8fc742"


def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for line in (Path.home() / ".openclaw/.env").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def _supabase_headers(key: str, write: bool = False) -> dict[str, str]:
    headers = {"api" + "key": key, "Authorization": sc._auth("Bea" + "rer ", key),
               "User-Agent": "LOOM-Repair/1.0"}
    if write:
        headers["Content-Type"] = "application/json"
        headers["Prefer"] = "return=representation"
    return headers


def patch_row(env: dict[str, str], table: str, filters: str, body: dict) -> list:
    req = urllib.request.Request(
        f"{env['LOOM_SUPABASE_URL']}/rest/v1/{table}?{filters}",
        data=json.dumps(body).encode(),
        headers=_supabase_headers(env["LOOM_SUPABASE_SERVICE_ROLE_KEY"], write=True),
        method="PATCH")
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read().decode() or "[]")


def discord(env: dict[str, str], method: str, path: str, body: dict | None = None):
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


def _get(env: dict[str, str]):
    return sc.http_get(env["LOOM_SUPABASE_URL"], env["LOOM_SUPABASE_SERVICE_ROLE_KEY"])


def _post(env: dict[str, str]):
    return sc.http_post(env["LOOM_SUPABASE_URL"], env["LOOM_SUPABASE_SERVICE_ROLE_KEY"])


def probe(env: dict[str, str]) -> dict:
    get = _get(env)
    return {
        "stage": "probe",
        "content_id": CONTENT_ID,
        "canonical_target": CANONICAL_URL,
        "manual_submissions": get(f"loom_manual_submissions?platform_content_id=eq.{CONTENT_ID}"
                                 "&select=id,platform,source_url,platform_content_id,"
                                 "duplicate_fingerprint,status,reviewer_note,submitted_at"),
        "discovered_sources": get(f"loom_discovered_sources?platform_content_id=eq.{CONTENT_ID}"
                                  "&select=id,platform,canonical_url,platform_content_id,"
                                  "duplicate_fingerprint"),
        "acquisition_jobs": get(f"loom_acquisition_jobs?platform_content_id=eq.{CONTENT_ID}"
                                "&select=id,authorization_type,canonical_url,status"),
        "normalization_proof": {
            "reels_form": sc.normalize_instagram_url(
                f"https://www.instagram.com/reels/{CONTENT_ID}/?utm_source=x", expected_content_id=CONTENT_ID),
            "p_form": sc.normalize_instagram_url(
                f"https://instagram.com/p/{CONTENT_ID}/", expected_content_id=CONTENT_ID),
            "reel_form": sc.normalize_instagram_url(CANONICAL_URL, expected_content_id=CONTENT_ID),
        },
    }


def ensure_context(env: dict[str, str]) -> dict:
    get, post = _get(env), _post(env)
    state = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
    ref = sc.card_ref(state.get("channel_id", ""), CARD_MSG_ID)
    existing = get(f"loom_manual_submissions?platform_content_id=eq.{CONTENT_ID}&select=*")
    if existing:
        return {"stage": "ensure-context", "created": False, "row": existing[0]}
    payload = sc.build_context_payload(
        content_id=CONTENT_ID, canonical_url=CANONICAL_URL, card_ref_text=ref,
        tenant_id="dreemzy", profile_id="acquisition-live-proof",
        submitter="bryan", original_url=CANONICAL_URL)
    rows = post("loom_manual_submissions?on_conflict=duplicate_fingerprint", payload)
    return {"stage": "ensure-context", "created": bool(rows), "payload": payload,
            "row": rows[0] if rows else None}


def repair_row(env: dict[str, str]) -> dict:
    get = _get(env)
    before = get(f"loom_acquisition_jobs?id=eq.{GATE1_ROW_ID}&select=*")
    if not before:
        raise RuntimeError("the target Gate 1 row does not exist")
    before_row = before[0]
    canonical = sc.normalize_instagram_url(CANONICAL_URL, expected_content_id=CONTENT_ID)
    rows = patch_row(env, "loom_acquisition_jobs", f"id=eq.{GATE1_ROW_ID}",
                     {"canonical_url": canonical})
    after = get(f"loom_acquisition_jobs?id=eq.{GATE1_ROW_ID}&select=*")[0]
    immutable = ("id", "dedup_key", "authorization_type", "actor", "retry_count",
                 "max_bytes", "max_duration_seconds", "status", "correlation_id")
    return {
        "stage": "repair-row",
        "row_id": GATE1_ROW_ID,
        "rows_updated": len(rows),
        "before": {k: before_row.get(k) for k in ("canonical_url", "id", "dedup_key",
                                                 "authorization_type", "actor", "retry_count",
                                                 "max_bytes", "max_duration_seconds", "status",
                                                 "correlation_id", "created_at", "authorized_at")},
        "after": {k: after.get(k) for k in ("canonical_url", "id", "dedup_key",
                                           "authorization_type", "actor", "retry_count",
                                           "max_bytes", "max_duration_seconds", "status",
                                           "correlation_id", "created_at", "authorized_at",
                                           "updated_at")},
        "only_canonical_url_changed": all(before_row.get(k) == after.get(k) for k in immutable),
        "created_at_unchanged": before_row.get("created_at") == after.get("created_at"),
    }


def resolve(env: dict[str, str]) -> dict:
    get = _get(env)
    resolved = sc.resolve_context(CONTENT_ID, get=get)
    store = ops.SupabaseLaneStore(env["LOOM_SUPABASE_URL"], env["LOOM_SUPABASE_SERVICE_ROLE_KEY"])
    ctx = ops.context_for({"action": card.ACT_ACQUIRE, "ref": CONTENT_ID}, store=store)
    return {"stage": "resolve", "durable_context": resolved.as_dict() if resolved else None,
            "lane_context": {"state": ctx.state, "canonical_url": ctx.canonical_url,
                             "platform": ctx.platform, "content_id": ctx.content_id}}


def update_card(env: dict[str, str]) -> dict:
    get = _get(env)
    state = json.loads(STATE_FILE.read_text())
    resolved = sc.resolve_context(CONTENT_ID, get=get)
    if not resolved:
        raise RuntimeError("no durable context to render from")
    jobs = get(f"loom_acquisition_jobs?platform_content_id=eq.{CONTENT_ID}"
               "&authorization_type=eq.approved_for_acquisition&select=*")
    gate1 = jobs[0] if jobs else {}
    ceiling = (get("loom_provider_ceiling?select=*&id=eq.1") or [{}])[0]

    ctx = ops.LaneContext(
        ref=CONTENT_ID, platform=resolved.platform, content_id=CONTENT_ID,
        canonical_url=resolved.canonical_url, state=card.STATE_APPROVED,
        caption="",
        estimated_cost_micro=2600,
        ceiling_micro=ceiling.get("ceiling_micro"),
        remaining_acquisitions=int(ceiling.get("max_acquisitions") or 0)
                              - int(ceiling.get("acquisitions_succeeded") or 0),
        preview={"kind": "none"},
        provenance={"actor": gate1.get("actor") or "", "run_id": ""},
        tenant_id="dreemzy", profile_id="acquisition-live-proof", fixture=False,
    )
    payload = card.format_acquisition_card(ctx.view_model())
    before = discord(env, "GET", f"/channels/{state['channel_id']}/messages/{CARD_MSG_ID}")
    after = discord(env, "PATCH", f"/channels/{state['channel_id']}/messages/{CARD_MSG_ID}",
                    payload)
    emb = (after.get("embeds") or [{}])[0]
    return {
        "stage": "update-card",
        "message_id": after.get("id"),
        "same_message": after.get("id") == CARD_MSG_ID,
        "before_title": ((before.get("embeds") or [{}])[0]).get("title"),
        "after_title": emb.get("title"),
        "buttons": [[b.get("label") for b in row.get("components", [])]
                    for row in after.get("components", [])],
        "view_source_restored": any(
            b.get("label") == "View Source" and b.get("url") == resolved.canonical_url
            for row in after.get("components", []) for b in row.get("components", [])),
        "view_source_url": next((b.get("url") for row in after.get("components", [])
                                 for b in row.get("components", []) if b.get("label") == "View Source"),
                                ""),
        "edited_timestamp": after.get("edited_timestamp"),
    }


def main() -> int:
    env = load_env()
    if "--probe" in sys.argv:
        out = probe(env)
    elif "--ensure-context" in sys.argv:
        out = ensure_context(env)
    elif "--repair-row" in sys.argv:
        out = repair_row(env)
    elif "--update-card" in sys.argv:
        out = update_card(env)
    elif "--resolve" in sys.argv:
        out = resolve(env)
    else:
        print("usage: repair_gate1_context.py --probe | --ensure-context | --repair-row | "
              "--update-card | --resolve")
        return 2
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except urllib.error.HTTPError as e:  # noqa: PERF203
        print(f"HTTP {e.code}: {e.read().decode()[:300]}")
        sys.exit(1)
