#!/usr/bin/env python3
"""Register (idempotently) the LOOM guild slash commands.

Registers both `/source-review` and `/submit-source` for the existing
"LOOM Clip Review" bot, guild-scoped to Clippy Factory. Loads
LOOM_DISCORD_BOT_TOKEN + LOOM_DISCORD_APP_ID by name (never printed).

Run from repo root:  python3 infra/discord-review/register_source_review_command.py
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

GUILD_ID = "1546266463663423518"  # Clippy Factory
API = "https://discord.com/api/v10"

COMMAND = {
    "name": "source-review",
    "description": "Pull top scored source candidates for review (approve for acquisition / hold / reject)",
    "options": [
        {
            "name": "limit",
            "description": "Number of candidates (default 5, max 10)",
            "type": 4,
            "required": False,
            "min_value": 1,
            "max_value": 10,
        }
    ],
}

SUBMIT_SOURCE_COMMAND = {
    "name": "submit-source",
    "description": "Submit a source URL for manual intake (candidate only — not approval for acquisition)",
    "options": [
        {"name": "url", "description": "Source URL (YouTube, Reddit, TikTok, Instagram, Twitch, Kick)",
         "type": 3, "required": True},
        {"name": "destination", "description": "Destination: 'unassigned' or an active tenant id (e.g. 'dreemzy')",
         "type": 3, "required": True},
        {"name": "format", "description": "Intended content format", "type": 3, "required": True,
         "choices": [
             {"name": "reaction_single", "value": "reaction_single"},
             {"name": "reaction_compilation", "value": "reaction_compilation"},
             {"name": "commentary_story", "value": "commentary_story"},
             {"name": "ranking_top5", "value": "ranking_top5"},
             {"name": "stream_highlight", "value": "stream_highlight"},
             {"name": "hold_unassigned", "value": "hold_unassigned"},
         ]},
        {"name": "start", "description": "Optional start timestamp (seconds)", "type": 10, "required": False},
        {"name": "end", "description": "Optional end timestamp (seconds)", "type": 10, "required": False},
        {"name": "note", "description": "Optional reviewer note", "type": 3, "required": False},
    ],
}

COMMANDS = [COMMAND, SUBMIT_SOURCE_COMMAND]


def _load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    p = os.path.expanduser("~/.openclaw/.env")
    if os.path.exists(p):
        for line in open(p, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def _req(method: str, path: str, token: str, body=None):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Authorization": f"Bot {token}", "User-Agent": "LOOM-SourceReview/1.0"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(f"{API}{path}", data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read().decode() or "[]")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:300]


def main() -> int:
    env = _load_env()
    token = env.get("LOOM_DISCORD_BOT_TOKEN", "")
    app_id = env.get("LOOM_DISCORD_APP_ID", "")
    if not token or not app_id:
        print("FAIL: LOOM_DISCORD_BOT_TOKEN / LOOM_DISCORD_APP_ID absent (name-only)")
        return 2

    base = f"/applications/{app_id}/guilds/{GUILD_ID}/commands"
    st, existing = _req("GET", base, token)
    existing = existing if isinstance(existing, list) else []
    by_name = {c.get("name"): c for c in existing if isinstance(c, dict)}

    rc = 0
    for cmd in COMMANDS:
        if cmd["name"] in by_name:
            st2, _ = _req("PATCH", f"{base}/{by_name[cmd['name']]['id']}", token, cmd)
            print(f"updated {cmd['name']}: {st2}")
            rc = rc or (0 if st2 == 200 else 1)
        else:
            st2, created = _req("POST", base, token, cmd)
            print(f"registered {cmd['name']}: {st2} (id={created.get('id') if isinstance(created, dict) else '?'})")
            rc = rc or (0 if st2 in (200, 201) else 1)
    return rc


if __name__ == "__main__":
    sys.exit(main())
