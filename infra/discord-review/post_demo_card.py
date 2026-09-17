#!/usr/bin/env python3
"""Post ONE retained non-production demo card to #source-review for visual acceptance.

Uses the REAL production card builder (source_review.format_source_embed +
format_source_buttons) — not a manual mock. The card:
  * is unmistakably labeled TEST FIXTURE (footer),
  * uses a non-production proof profile (auto-excluded from production queues),
  * renders the three decision buttons DISABLED (visually reviewable, but
    technically incapable of triggering acquisition/download/OpenShorts/publish),
  * includes a safe "Open Source" link (public YouTube URL only — no secrets).

Run from repo root:  python3 infra/discord-review/post_demo_card.py
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "infra" / "discord-review"))

import source_review  # noqa: E402

API = "https://discord.com/api/v10"
GUILD_ID = "1546266463663423518"  # Clippy Factory
PROOF_PROFILE = "source-review-demo-proof"  # non-production
PROOF_FP = "d" * 64  # synthetic non-production fingerprint


def _load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    p = Path.home() / ".openclaw/.env"
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def _discord(method: str, path: str, token: str, body=None):
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


def _resolve_channel(guilds, token: str, name: str) -> str | None:
    if not isinstance(guilds, list):
        return None
    for g in guilds:
        if g.get("id") != GUILD_ID:
            continue
        st2, chans = _discord("GET", f"/guilds/{GUILD_ID}/channels", token)
        if isinstance(chans, list):
            for c in chans:
                if c.get("name") == name and c.get("type") == 0:
                    return c["id"]
    return None


def _demo_candidate() -> dict:
    # Real, representative candidate data (Linus Tech Tips doorbell review) with
    # a NON-PRODUCTION profile + TEST FIXTURE markers. Scores reflect a genuine
    # transcript-only Pass-1 assessment (market 59 / content 68 / selection 60).
    return {
        "duplicate_fingerprint": PROOF_FP,
        "tenant_id": "dreemzy",
        "profile_id": PROOF_PROFILE,
        "platform": "youtube",
        "platform_content_id": "qgLaCZyKv_8",
        "title": "I Tried All The Best (and Worst) Doorbells",
        "canonical_url": "https://www.youtube.com/watch?v=qgLaCZyKv_8",
        "creator_channel_identity": "Linus Tech Tips",
        "publication_timestamp": "2026-09-15T00:00:00+00:00",
        "market_opportunity_score": 59,
        "market_confidence": 0.78,
        "content_worthiness_score": 68,
        "content_confidence": 0.33,  # transcript-only (level 1) — honest
        "source_selection_score": 60,
        "source_selection_confidence": 0.58,
        "outcome": "standard_review",
        "recommended_format": "commentary_story",
        "recommended_angle": "Break down why ultra-cheap smart home tech becomes a subscription trap once the hidden fees kick in.",
        "suggested_timestamps": [
            {"start_s": 10.5, "end_s": 22.5, "basis": "hook (pumpkin metaphor)"},
            {"start_s": 110.0, "end_s": 116.5, "basis": "twist / payoff"},
            {"start_s": 215.5, "end_s": 228.3, "basis": "hidden subscription costs"},
        ],
        "top_reasons": [
            "Relatable subscription-trap pain point",
            "Strong hook (pumpkin metaphor)",
            "Absurd visual concept (13 doorbells on one frame)",
        ],
    }


def main() -> int:
    env = _load_env()
    token = env.get("LOOM_DISCORD_BOT_TOKEN", "")
    if not token:
        print("FAIL: LOOM_DISCORD_BOT_TOKEN absent")
        return 2

    st, guilds = _discord("GET", "/users/@me/guilds", token)
    if st != 200:
        print(f"FAIL: list guilds {st}")
        return 2
    chan_id = _resolve_channel(guilds, token, "source-review")
    if not chan_id:
        print("FAIL: source-review channel not found")
        return 2

    cand = _demo_candidate()
    payload = {
        "embeds": [source_review.format_source_embed(cand, fixture=True)],
        "components": source_review.format_source_buttons(
            cand["duplicate_fingerprint"], disabled=True, source_url=cand["canonical_url"]
        ),
    }

    st, msg = _discord("POST", f"/channels/{chan_id}/messages", token, payload)
    if st != 200 or not isinstance(msg, dict) or not msg.get("id"):
        print(f"FAIL: post card {st}: {msg}")
        return 1

    print(f"message_id: {msg['id']}")
    print(f"channel_id: {chan_id}")
    print(f"profile_id: {PROOF_PROFILE}")
    print("retained: True (NOT deleted)")
    print("proof_profile_excluded: True (contains 'proof')")
    print("downstream_locked: True (buttons disabled; link button is public YouTube only)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
