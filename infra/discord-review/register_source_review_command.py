#!/usr/bin/env python3
"""Register (idempotently) the `/source-review` guild slash command.

Uses the existing "LOOM Clip Review" bot. Guild-scoped to Clippy Factory.
Loads LOOM_DISCORD_BOT_TOKEN + LOOM_DISCORD_APP_ID by name (never printed).

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

    # Idempotent: update if an existing command shares the name.
    st, existing = _req("GET", base, token)
    if isinstance(existing, list):
        for cmd in existing:
            if cmd.get("name") == COMMAND["name"]:
                st2, updated = _req("PATCH", f"{base}/{cmd['id']}", token, COMMAND)
                print(f"updated existing command {cmd['id']}: {st2}")
                return 0 if st2 == 200 else 1

    st, created = _req("POST", base, token, COMMAND)
    print(f"registered command: {st} (id={created.get('id') if isinstance(created, dict) else '?'})")
    return 0 if st in (200, 201) else 1


if __name__ == "__main__":
    sys.exit(main())
