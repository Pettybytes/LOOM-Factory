"""Source Review Inbox — safe Discord + Supabase runtime proof.

Proves the recurring path WITHOUT touching any real production candidate:

  1. Discord connectivity (guilds + channels; #source-review exists).
  2. `/source-review` guild command registered (idempotent).
  3. A synthetic non-production card is posted to #source-review via the existing
     bot, then deleted (cleanup — no clutter left in Bryan's channel).
  4. A non-production decision is persisted to loom_source_reviews (migration 024)
     with first-decision-wins idempotency, then verified excluded from the
     production queue.

No download, no OpenShorts, no Acquisition, no publishing. No secrets printed.

Run from repo root:  python3 infra/discord-review/prove_source_review.py
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
PROOF_PROFILE = "source-review-proof"  # non-production
PROOF_FP = "f" * 64  # synthetic non-production fingerprint

_PASS: list[str] = []
_FAIL: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (_PASS if ok else _FAIL).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def _load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for p in (Path.home() / ".openclaw/.env",):
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


def _synthetic_candidate() -> dict:
    return {
        "duplicate_fingerprint": PROOF_FP,
        "tenant_id": "dreemzy",
        "profile_id": PROOF_PROFILE,
        "platform": "youtube",
        "platform_content_id": "proof-synthetic",
        "title": "🧪 SOURCE REVIEW PROOF — safe synthetic test card",
        "canonical_url": "",
        "market_opportunity_score": 65,
        "market_confidence": 0.7,
        "content_worthiness_score": 60,
        "content_confidence": 0.33,
        "source_selection_score": 62,
        "source_selection_confidence": 0.5,
        "outcome": "standard_review",
        "recommended_format": "commentary_story",
        "recommended_angle": "proof angle",
        "suggested_timestamps": [{"start_s": 1.0, "end_s": 5.0, "basis": "proof"}],
        "top_reasons": ["proof reason"],
    }


def main() -> int:
    env = _load_env()
    token = env.get("LOOM_DISCORD_BOT_TOKEN", "")
    sb_url = env.get("LOOM_SUPABASE_URL", "").rstrip("/")
    sb_key = env.get("LOOM_SUPABASE_SERVICE_ROLE_KEY", "")
    if not token:
        print("FAIL: LOOM_DISCORD_BOT_TOKEN absent")
        return 2
    if not (sb_url and sb_key):
        print("FAIL: Supabase creds absent")
        return 2

    print("=== LOOM Source Review Inbox — safe runtime proof ===\n")

    print("[1] Discord connectivity:")
    st, guilds = _discord("GET", "/users/@me/guilds", token)
    check("list guilds 200", st == 200)
    chan_id = _resolve_channel(guilds, token, "source-review")
    check("source-review channel resolved", chan_id is not None, str(chan_id))
    if not chan_id:
        return 2

    print("[2] register /source-review command:")
    # Reuse the registration logic inline (idempotent).
    from register_source_review_command import COMMAND, _req as reg_req
    app_id = env.get("LOOM_DISCORD_APP_ID", "")
    base = f"/applications/{app_id}/guilds/{GUILD_ID}/commands"
    st, existing = reg_req("GET", base, token)
    if isinstance(existing, list) and any(c.get("name") == COMMAND["name"] for c in existing):
        check("command already registered", True)
    else:
        st, created = reg_req("POST", base, token, COMMAND)
        check("command registered", st in (200, 201), f"id={created.get('id') if isinstance(created, dict) else '?'}")

    print("[3] post synthetic card to #source-review + delete:")
    cand = _synthetic_candidate()
    payload = {
        "embeds": [source_review.format_source_embed(cand)],
        "components": source_review.format_source_buttons(cand["duplicate_fingerprint"]),
    }
    st, msg = _discord("POST", f"/channels/{chan_id}/messages", token, payload)
    msg_id = msg.get("id") if isinstance(msg, dict) else None
    check("card posted", st == 200 and msg_id is not None, f"msg_id={msg_id}")
    if msg_id:
        st_del, _ = _discord("DELETE", f"/channels/{chan_id}/messages/{msg_id}", token)
        check("card deleted (cleanup)", st_del in (200, 204))

    print("[4] persist non-production decision (first-decision-wins):")
    payload = source_review.source_decision_payload(cand, source_review.DECISION_APPROVE, "bryan#proof")
    try:
        row, created = source_review.persist_decision(sb_url, sb_key, payload)
        check("decision persisted", created, f"decision={row.get('decision')}")
        row2, created2 = source_review.persist_decision(sb_url, sb_key, payload)
        check("replay does not overwrite (first-decision-wins)", created2 is False,
              f"created2={created2}")
    except Exception as e:
        check("decision persisted", False, str(e)[:200])
        return 1

    print("[5] production-queue exclusion:")
    pend = source_review.fetch_pending_candidates(sb_url, sb_key, 50)
    leak = [c for c in pend if c.get("profile_id") == PROOF_PROFILE]
    check("proof profile absent from production queue", len(leak) == 0, f"{len(leak)} leaked")

    print("\n=== RESULT ===")
    print(f"  PASS: {len(_PASS)}  FAIL: {len(_FAIL)}")
    if _FAIL:
        print("  FAILED:", ", ".join(_FAIL))
        return 1
    print("  ALL SOURCE REVIEW PROOF CRITERIA PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
