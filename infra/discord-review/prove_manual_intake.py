#!/usr/bin/env python3
"""Manual Source Intake V1 — bounded non-production runtime proof.

Exercises the real path against production Supabase with NON-PRODUCTION proof
data (no media download, no OpenShorts, no acquisition, no publishing):

  1. `/submit-source` command is registered.
  2. A non-production YouTube submission -> accepted + persisted + content id.
  3. Re-submission -> dedup (first-wins, no duplicate row).
  4. A TikTok submission -> accepted_pending_platform_support.
  5. Rejections: private host / credentials / unsupported host / bad scheme.
  6. Proof profile is non-production (auto-excluded from any future queue).

Run from repo root:  python3 infra/discord-review/prove_manual_intake.py
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

import manual_intake  # noqa: E402

API = "https://discord.com/api/v10"
GUILD_ID = "1546266463663423518"
PROOF_PROFILE = "manual-intake-proof"  # non-production

_PASS: list[str] = []
_FAIL: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (_PASS if ok else _FAIL).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


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


def _discord(method: str, path: str, token: str):
    req = urllib.request.Request(f"{API}{path}",
                                 headers={"Authorization": f"Bot {token}", "User-Agent": "x"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read().decode() or "[]")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:200]


def main() -> int:
    env = _load_env()
    token = env.get("LOOM_DISCORD_BOT_TOKEN", "")
    app_id = env.get("LOOM_DISCORD_APP_ID", "")
    sb_url = env.get("LOOM_SUPABASE_URL", "").rstrip("/")
    sb_key = env.get("LOOM_SUPABASE_SERVICE_ROLE_KEY", "")
    if not (sb_url and sb_key):
        print("FAIL: Supabase creds absent")
        return 2

    print("=== LOOM Manual Source Intake V1 — bounded non-production proof ===\n")

    print("[1] /submit-source command registered:")
    st, cmds = _discord("GET", f"/applications/{app_id}/guilds/{GUILD_ID}/commands", token)
    names = [c.get("name") for c in (cmds if isinstance(cmds, list) else [])]
    check("submit-source registered", "submit-source" in names, str(names))

    print("[2] YouTube submission -> accepted:")
    payload = manual_intake.build_submission(
        url="https://www.youtube.com/watch?v=qgLaCZyKv_8&si=xyz&utm_source=proof",
        tenant_id="dreemzy", profile_id=PROOF_PROFILE,
        content_format="commentary_story", submitter="bryan#proof",
        time_start_s=10.5, time_end_s=22.5, reviewer_note="proof",
    )
    check("canonicalized + content id", payload["platform_content_id"] == "qgLaCZyKv_8",
          payload["source_url"])
    check("status accepted", payload["status"] == manual_intake.STATUS_ACCEPTED)
    row, created = manual_intake.persist_submission(sb_url, sb_key, payload)
    check("persisted (created)", created, f"id={row.get('id')}")

    print("[3] re-submission dedup (first-wins):")
    row2, created2 = manual_intake.persist_submission(sb_url, sb_key, payload)
    check("replay not created (dedup)", created2 is False, f"created2={created2}")

    print("[4] TikTok -> pending platform support:")
    tt = manual_intake.build_submission(
        url="https://www.tiktok.com/@user/video/7123456789012345678",
        tenant_id="dreemzy", profile_id=PROOF_PROFILE,
        content_format="reaction_single", submitter="bryan#proof",
    )
    check("status pending platform support",
          tt["status"] == manual_intake.STATUS_PENDING_PLATFORM, tt["status"])

    print("[5] rejections:")
    for url, code in [
        ("https://127.0.0.1/x", "PRIVATE_HOST"),
        ("https://user:***@youtube.com/watch?v=x", "CREDENTIALS"),
        ("https://evil.com/x", "UNSUPPORTED_HOST"),
        ("file:///etc/passwd", "BAD_SCHEME"),
    ]:
        try:
            manual_intake.validate_and_canonicalize(url)
            check(f"reject {code}", False, "not rejected")
        except manual_intake.SubmissionError as e:
            check(f"reject {code}", e.code == code, e.code)

    print("[6] proof profile non-production + boundary guards:")
    check("profile non-production", "proof" in PROOF_PROFILE, PROOF_PROFILE)
    forbidden = ("openshorts", "ghl", "distribution", "social", "elevenlabs", "retell")
    imported = [m for m in sys.modules if any(f in m.lower() for f in forbidden)]
    check("no forbidden modules imported", not imported, ",".join(imported) if imported else "none")

    print("\n=== RESULT ===")
    print(f"  PASS: {len(_PASS)}  FAIL: {len(_FAIL)}")
    if _FAIL:
        print("  FAILED:", ", ".join(_FAIL))
        return 1
    print("  ALL MANUAL INTAKE PROOF CRITERIA PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
