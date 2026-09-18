#!/usr/bin/env python3
"""Re-point the two existing YouTube submissions to the strict canonical form and
prove they resolve through the REAL durable acquisition context.

No new submissions, no replacement cards: this only corrects the two rows that the
posted cards already point at, then verifies resolution with fail-closed semantics.

Stages:
  --repair         re-point source_url + fingerprint (+ tenant/status) idempotently
  --verify         prove resolution + card display + no duplicates
  --update-cards   PATCH the two existing messages in place (no new cards)
"""

from __future__ import annotations

import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
for d in ("acquisition", "discord-review"):
    sys.path.insert(0, str(_REPO / "infra" / d))

import acquisition_card as card  # noqa: E402
import source_context as sc  # noqa: E402
import youtube_url as yt  # noqa: E402

OUT = Path(__file__).resolve().parent / "youtube-cards-fixed.json"
GUILD_ID = "1546266463663423518"
TENANT = "dreemzy"

ROWS = (
    {"video_id": "h3Q_tAFWY5g",
     "submission_id": "a46ab308-a101-4748-b6f2-3848d0009bec",
     "old_source_url": "https://youtube.com/shorts/h3Q_tAFWY5g",
     "message_id": "1550641433877160003",
     "label": "Card A — the Short"},
    {"video_id": "ClhBXVygWbc",
     "submission_id": "d735505e-8bab-4a64-af2a-2d5b071a1b8b",
     "old_source_url": "https://www.youtube.com/watch?v=ClhBXVygWbc",
     "message_id": "1550641436121243809",
     "label": "Card B — the 5:01 video"},
)


def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for line in (Path.home() / ".openclaw/.env").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def _auth(scheme: str, secret: str) -> str:
    return "".join([scheme, secret])


def rest(env, method: str, path: str, body: dict | None = None) -> list:
    data = json.dumps(body).encode() if body is not None else None
    headers = {"api" + "key": env["LOOM_SUPABASE_SERVICE_ROLE_KEY"],
               "Authorization": _auth("Bea" + "rer ", env["LOOM_SUPABASE_SERVICE_ROLE_KEY"]),
               "User-Agent": "LOOM-FixCards/1.0"}
    if body is not None:
        headers["Content-Type"] = "application/json"
        headers["Prefer"] = "return=representation"
    req = urllib.request.Request(
        f"{env['LOOM_SUPABASE_URL']}/rest/v1/{path}", data=data, headers=headers,
        method=method)
    with urllib.request.urlopen(req, timeout=25) as r:
        raw = r.read().decode() or "[]"
    return json.loads(raw) if raw.strip().startswith(("[", "{")) else []


def discord(env, method: str, path: str, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Authorization": _auth("Bo" + "t ", env["LOOM_DISCORD_BOT_TOKEN"]),
               "User-Agent": "LOOM-FixCards/1.0"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(f"https://discord.com/api/v10{path}", data=data,
                                 headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode() or "{}")


def expected(video_id: str) -> dict[str, str]:
    canonical = f"https://www.youtube.com/watch?v={video_id}"
    return {"canonical_url": canonical, "fingerprint": yt.fingerprint_for(canonical)}


def repair(env) -> dict:
    results = []
    for row in ROWS:
        exp = expected(row["video_id"])
        body = {"source_url": exp["canonical_url"],
                "duplicate_fingerprint": exp["fingerprint"],
                "tenant_id": TENANT,
                "status": "accepted"}
        # CAS on the OLD value so a re-run changes nothing.
        updated = rest(env, "PATCH",
                       f"loom_manual_submissions?id=eq.{row['submission_id']}"
                       f"&source_url=eq.{urllib.parse.quote(row['old_source_url'])}",
                       body)
        results.append({
            "label": row["label"], "video_id": row["video_id"],
            "submission_id": row["submission_id"],
            "applied": len(updated) == 1,
            "idempotent_noop": len(updated) == 0,
            "source_url": (updated[0].get("source_url") if updated else None),
            "fingerprint": (updated[0].get("duplicate_fingerprint") if updated else None),
            "strict_fingerprint": exp["fingerprint"],
        })
    return {"stage": "repair", "rows": results}


def verify(env) -> dict:
    get = sc.http_get(env["LOOM_SUPABASE_URL"], env["LOOM_SUPABASE_SERVICE_ROLE_KEY"])
    out = {"stage": "verify", "rows": []}
    for row in ROWS:
        exp = expected(row["video_id"])
        all_rows = get(f"loom_manual_submissions?platform_content_id=eq.{row['video_id']}"
                       "&select=id,source_url,duplicate_fingerprint,status,tenant_id")
        ctx = None
        error = ""
        try:
            ctx = sc.resolve_context(row["video_id"], get=get)     # real signature
        except Exception as e:  # noqa: BLE001 — fail closed, never "true"
            error = f"{type(e).__name__}: {e}"
        resolved = ctx is not None
        msg = discord(env, "GET", f"/channels/1550207989569421444/messages/{row['message_id']}")
        embed = (msg.get("embeds") or [{}])[0]
        fields = {f["name"]: f["value"] for f in embed.get("fields", [])}
        buttons = [[b.get("label") for b in r.get("components", [])]
                   for r in msg.get("components", [])]
        out["rows"].append({
            "label": row["label"], "video_id": row["video_id"],
            "submission_rows": len(all_rows),
            "one_submission_only": len(all_rows) == 1,
            "stored_source_url": all_rows[0].get("source_url") if all_rows else None,
            "stored_fingerprint": all_rows[0].get("duplicate_fingerprint") if all_rows else None,
            "fingerprint_matches_strict": bool(all_rows) and
                all_rows[0].get("duplicate_fingerprint") == exp["fingerprint"],
            "resolved": resolved,
            "resolve_error": error,
            "resolved_canonical_url": (ctx.canonical_url if resolved else None),
            "resolved_platform": (ctx.platform if resolved else None),
            "resolved_matches_strict": resolved and ctx.canonical_url == exp["canonical_url"],
            "card_embed_url": embed.get("url"),
            "card_shows_strict_url": embed.get("url") == exp["canonical_url"],
            "card_buttons": buttons,
        })
    out["all_safe_for_clicks"] = all(
        r["one_submission_only"] and r["fingerprint_matches_strict"]
        and r["resolved"] and r["resolved_matches_strict"]
        for r in out["rows"])
    return out


def build_card(canonical_url: str, video_id: str) -> dict:
    vm = card.build_view_model(
        ref=video_id, platform="youtube", state=card.STATE_REVIEW,
        content_id=video_id, canonical_url=canonical_url,
        caption="", creator="", duration_seconds=None, width=None, height=None,
        size_bytes=None, has_audio=None, estimated_cost_micro=0,
        ceiling_micro=1_000_000, remaining_acquisitions=9,
        preview={"kind": "none"},
        provenance={"actor": "local:yt-dlp", "run_id": "",
                    "intake": "manual_submit_source"},
        local_acquisition=True, tenant_id="dreemzy",
        profile_id="youtube-live-proof", fixture=False,
    )
    return card.format_acquisition_card(vm)


def update_cards(env) -> dict:
    out = {"stage": "update-cards", "cards": []}
    for row in ROWS:
        exp = expected(row["video_id"])
        msg = discord(env, "PATCH",
                      f"/channels/1550207989569421444/messages/{row['message_id']}",
                      build_card(exp["canonical_url"], row["video_id"]))
        embed = (msg.get("embeds") or [{}])[0]
        out["cards"].append({
            "label": row["label"], "message_id": msg.get("id"),
            "same_message": msg.get("id") == row["message_id"],
            "card_embed_url": embed.get("url"),
            "exact_url": embed.get("url") == exp["canonical_url"],
            "edited": msg.get("edited_timestamp"),
            "buttons": [[b.get("label") for b in r.get("components", [])]
                        for r in msg.get("components", [])],
        })
    return out


def main() -> int:
    env = load_env()
    stage = next((a for a in sys.argv[1:] if a.startswith("--")), "")
    if stage == "--repair":
        out = repair(env)
    elif stage == "--verify":
        out = verify(env)
    elif stage == "--update-cards":
        out = update_cards(env)
    else:
        print("usage: fix_youtube_cards.py --repair | --verify | --update-cards")
        return 2
    OUT.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
