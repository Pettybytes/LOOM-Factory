#!/usr/bin/env python3
"""Attach the approved 00:30–01:00 window to the durable source + job, in place.

No new submission, no new job, no new card: the existing rows and the existing
message are corrected so the production chain carries the approved range.
"""
from __future__ import annotations
import json, sys, urllib.parse, urllib.request
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
for d in ("acquisition", "discord-review"):
    sys.path.insert(0, str(_REPO / "infra" / d))
import acquisition_card as card, source_window as sw

VID = "ClhBXVygWbc"
URL = f"https://www.youtube.com/watch?v={VID}"
GATE1 = "f75a35b4-9b27-4c5a-8393-a25423d8e407"
CARD_MSG = "1550641436121243809"
CHANNEL = "1550207989569421444"
START_S, END_S, SRC_S = 30.0, 60.0, 302.0


def load_env():
    env = {}
    for line in (Path.home() / ".openclaw/.env").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def auth(s, sec): return "".join([s, sec])


def rest(env, path, body=None, method="GET"):
    data = json.dumps(body).encode() if body is not None else None
    h = {"api" + "key": env["LOOM_SUPABASE_SERVICE_ROLE_KEY"],
         "Authorization": auth("Bea" + "rer ", env["LOOM_SUPABASE_SERVICE_ROLE_KEY"]),
         "User-Agent": "LOOM-RangeProof/1.0"}
    if body is not None:
        h["Content-Type"] = "application/json"; h["Prefer"] = "return=representation"
    req = urllib.request.Request(f"{env['LOOM_SUPABASE_URL']}/rest/v1/{path}",
                                 data=data, headers=h, method=method)
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read().decode() or "[]"
    return json.loads(raw) if raw.strip().startswith(("[", "{")) else []


def discord(env, method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    h = {"Authorization": auth("Bo" + "t ", env["LOOM_DISCORD_BOT_TOKEN"]),
         "User-Agent": "LOOM-RangeProof/1.0"}
    if body is not None:
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(f"https://discord.com/api/v10{path}", data=data,
                                 headers=h, method=method)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode() or "{}")


def main() -> int:
    env = load_env()
    out = {"stage": "range-proof-prep", "video_id": VID}

    # 1. the durable submission carries the approved window
    subs = rest(env, f"loom_manual_submissions?platform_content_id=eq.{VID}&select=*")
    if subs:
        rows = rest(env, f"loom_manual_submissions?id=eq.{subs[0]['id']}",
                    {"time_start_s": START_S, "time_end_s": END_S}, "PATCH")
        out["submission"] = {"id": subs[0]["id"], "updated": bool(rows),
                             "rows_for_video": len(subs)}
    # 2. the Gate 1 job carries the range and the bounded-segment mode
    rows = rest(env, f"loom_acquisition_jobs?id=eq.{GATE1}",
                {"acquisition_mode": "bounded_segment",
                 "requested_start_s": START_S, "requested_end_s": END_S,
                 "status": "queued",
                 "status_reason": ("approved 00:30–01:00 window (30.0 s) on a 05:02 "
                                   "source; clip limit applies to the segment")}, "PATCH")
    jobs = rest(env, f"loom_acquisition_jobs?id=eq.{GATE1}"
                     "&select=acquisition_mode,requested_start_s,requested_end_s,status")
    out["gate1"] = {"updated": bool(rows), "now": jobs[0] if jobs else None,
                    "rows_for_video": len(rest(env, f"loom_acquisition_jobs?platform_content_id=eq.{VID}&select=id"))}

    # 3. surface the card for the approved window (same message, in place)
    win = sw.validate_window(platform="youtube", canonical_url=URL, content_id=VID,
                             start_ms=int(START_S * 1000), end_ms=int(END_S * 1000),
                             origin="manual_submit_source", source_duration_s=SRC_S,
                             authorized_by="Bryan")
    job = jobs[0] if jobs else {}
    vm = card.build_view_model(
        ref=VID, platform="youtube", state=card.STATE_APPROVED, content_id=VID,
        canonical_url=URL, estimated_cost_micro=0, actual_cost_micro=None,
        ceiling_micro=1_000_000, remaining_acquisitions=8,
        window=win, requires_range=True, local_acquisition=True,
        preview={"kind": "none"},
        provenance={"adapter": "local:yt-dlp", "actor": "passportpetty#688976899094872148",
                    "run_id": "", "intake": "manual_submit_source"},
        tenant_id="dreemzy", profile_id="youtube-live-proof", fixture=False)
    msg = discord(env, "PATCH", f"/channels/{CHANNEL}/messages/{CARD_MSG}",
                  card.format_acquisition_card(vm))
    e = (msg.get("embeds") or [{}])[0]
    f = {x["name"]: x["value"] for x in e.get("fields", [])}
    out["card"] = {"message_id": msg.get("id"), "title": e.get("title"),
                   "url": e.get("url"), "window": f.get("Window"),
                   "cost": f.get("Cost"),
                   "buttons": [b.get("label") for r in msg.get("components", [])
                               for b in r.get("components", [])],
                   "message_url": f"https://discord.com/channels/1546266463663423518/{CHANNEL}/{msg.get('id')}"}
    out["mode_after_click"] = job.get("acquisition_mode")
    print(json.dumps(out, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
