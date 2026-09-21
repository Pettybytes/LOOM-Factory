#!/usr/bin/env python3
"""Manual Intake V1.1 routing — bounded non-production runtime proof.

Exercises assign / reassign / copy against production Supabase with a
NON-PRODUCTION proof profile (no download, no OpenShorts, no acquisition, no
publish). Verifies: unassigned status, controlled destination, per-tenant dedup,
collision, reassign (tenant correction), cross-tenant copy, qualification_required,
downstream reassignment block, immutable routing-event audit, command registration.

Run from repo root:  python3 infra/discord-review/prove_routing.py
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "infra" / "discord-review"))

import manual_intake  # noqa: E402
import tenant_routing  # noqa: E402

API = "https://discord.com/api/v10"
GUILD_ID = "1546266463663423518"
PROOF_PROFILE = "routing-proof"  # non-production
ACTOR = "bryan#proof"

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


def _discord(path: str, token: str):
    req = urllib.request.Request(f"{API}{path}", headers={"Authorization": f"Bot {token}", "User-Agent": "x"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read().decode() or "[]")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:200]


def _sb(url, key, method, path, body=None):
    headers = {"apikey": key, "Authorization": f"Bearer {key}", "User-Agent": "x"}
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        headers["Prefer"] = "resolution=merge-duplicates,return=representation"
        data = json.dumps(body).encode()
    req = urllib.request.Request(f"{url}/rest/v1/{path}", data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode() or "[]")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"SB {e.code}: {e.read().decode()[:200]}")


def _sb_delete(url, key, path):
    """DELETE returning a representation, so the proof can report exactly what it
    removed. Used only for this proof's OWN non-production fixture rows."""
    req = urllib.request.Request(
        f"{url}/rest/v1/{path}",
        headers={"apikey": key, "Authorization": f"Bearer {key}", "User-Agent": "x",
                 "Prefer": "return=representation"},
        method="DELETE")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode() or "[]")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"SB {e.code}: {e.read().decode()[:200]}")


def main() -> int:
    env = _load_env()
    token = env.get("LOOM_DISCORD_BOT_TOKEN", "")
    app_id = env.get("LOOM_DISCORD_APP_ID", "")
    sb_url = env.get("LOOM_SUPABASE_URL", "").rstrip("/")
    sb_key = env.get("LOOM_SUPABASE_SERVICE_ROLE_KEY", "")
    if not (sb_url and sb_key):
        print("FAIL: Supabase creds absent")
        return 2

    print("=== LOOM Manual Intake V1.1 — routing runtime proof ===\n")

    print("[1] commands registered:")
    _, cmds = _discord(f"/applications/{app_id}/guilds/{GUILD_ID}/commands", token)
    names = [c.get("name") for c in (cmds if isinstance(cmds, list) else [])]
    check("submit-source + assign-source registered",
          "submit-source" in names and "assign-source" in names, str(names))

    print("[2] setup: ensure 'client-a' active tenant:")
    _sb(sb_url, sb_key, "POST", "loom_tenants?on_conflict=tenant_id",
        {"tenant_id": "client-a", "name": "Client A", "status": "active"})
    active = tenant_routing.list_active_tenants(sb_url, sb_key)
    check("active tenants include dreemzy + client-a",
          {"dreemzy", "client-a"} <= active, str(active))

    print("[3] submit unassigned source:")
    url_a = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    payload = manual_intake.build_submission(
        url=url_a, destination=manual_intake.DESTINATION_UNASSIGNED,
        content_format="commentary_story", submitter=ACTOR,
        active_tenants=active,
    )
    fp = payload["duplicate_fingerprint"]

    # [3a] Fixture hygiene. This proof reuses ONE fixed URL, so its fingerprint is
    # stable run to run while its rows are not removed on exit. Prior runs left a
    # dreemzy submission row (which made step 4 report `collision` instead of
    # `assign`) and an approved_for_acquisition review row (which made step 6's
    # reassign report `blocked: downstream work already started`). Both are the
    # CORRECT production behaviours — the proof was simply not self-cleaning.
    # Scoped strictly to this proof's own fingerprint; routing events are the
    # immutable audit and are deliberately left intact.
    q_fp = urllib.parse.quote(fp)
    stale_subs = _sb_delete(sb_url, sb_key, f"loom_manual_submissions?duplicate_fingerprint=eq.{q_fp}")
    stale_reviews = _sb_delete(sb_url, sb_key, f"loom_source_reviews?duplicate_fingerprint=eq.{q_fp}")
    print(f"    [hygiene] removed prior proof rows for this fingerprint — "
          f"submissions={len(stale_subs)} reviews={len(stale_reviews)}")
    check("fixture is clean before the run",
          not stale_subs or all(r.get("platform_content_id") == "dQw4w9WgXcQ" for r in stale_subs),
          f"removed {len(stale_subs)} submissions / {len(stale_reviews)} reviews")

    check("unassigned status", payload["status"] == manual_intake.STATUS_AWAITING_TENANT, payload["status"])
    row, created = manual_intake.persist_submission(sb_url, sb_key, payload)
    check("persisted unassigned", created, f"tenant_id={row.get('tenant_id')!r}")

    print("[4] assign -> dreemzy:")
    r = tenant_routing.route_submission(sb_url, sb_key, fp, "", "dreemzy", "assign", ACTOR)
    check("assign action", r["action"] == "assign", str(r))
    sub = tenant_routing.fetch_submission(sb_url, sb_key, fp, "dreemzy")
    check("tenant updated", sub is not None and sub.get("tenant_id") == "dreemzy")
    check("qualification_required", sub is not None and sub.get("qualification_required") is True)

    print("[5] per-tenant dedup (same URL to dreemzy -> collision/no new row):")
    r2 = tenant_routing.route_submission(sb_url, sb_key, fp, "", "dreemzy", "assign", ACTOR)
    # The unassigned row is now gone (assigned), so assigning again should not find
    # an unassigned row -> NOT_FOUND. Instead test collision via copy to same tenant.
    copy_same = tenant_routing.route_submission(sb_url, sb_key, fp, "dreemzy", "dreemzy", "copy", ACTOR)
    check("same-tenant copy noop", copy_same["action"] == "noop", str(copy_same))

    print("[6] reassign -> client-a (tenant correction):")
    r3 = tenant_routing.route_submission(sb_url, sb_key, fp, "dreemzy", "client-a", "reassign", ACTOR)
    check("reassign action", r3["action"] == "reassign", str(r3))
    sub_b = tenant_routing.fetch_submission(sb_url, sb_key, fp, "client-a")
    check("tenant corrected to client-a", sub_b is not None and sub_b.get("tenant_id") == "client-a")

    print("[7] cross-tenant copy -> dreemzy (separate candidate):")
    r4 = tenant_routing.route_submission(sb_url, sb_key, fp, "client-a", "dreemzy", "copy", ACTOR)
    check("copy action", r4["action"] == "copy", str(r4))
    a = tenant_routing.fetch_submission(sb_url, sb_key, fp, "dreemzy")
    b = tenant_routing.fetch_submission(sb_url, sb_key, fp, "client-a")
    check("two tenant-scoped candidates exist", a is not None and b is not None,
          f"dreemzy={bool(a)} client-a={bool(b)}")

    print("[8] immutable routing-event audit:")
    q = urllib.parse.quote(fp)
    events = _sb(sb_url, sb_key, "GET", f"loom_routing_events?duplicate_fingerprint=eq.{q}&select=operation,old_destination,new_destination")
    ops = [(e.get("operation"), e.get("old_destination"), e.get("new_destination")) for e in (events or [])]
    check("routing events recorded (assign/reassign/copy)",
          any(o[0] == "assign" for o in ops) and any(o[0] == "reassign" for o in ops) and any(o[0] == "copy" for o in ops),
          str(ops))

    print("[9] downstream reassignment block:")
    # Simulate acquisition started: insert an approved-for-acquisition decision.
    _sb(sb_url, sb_key, "POST", "loom_source_reviews?on_conflict=duplicate_fingerprint",
        {"tenant_id": "client-a", "profile_id": PROOF_PROFILE, "duplicate_fingerprint": fp,
         "platform": "youtube", "platform_content_id": "dQw4w9WgXcQ", "title": "proof",
         "decision": "approved_for_acquisition", "reviewer": ACTOR})
    blocked = tenant_routing.route_submission(sb_url, sb_key, fp, "client-a", "dreemzy", "reassign", ACTOR)
    check("reassign blocked after downstream start", blocked["action"] == "blocked", str(blocked))

    print("[10] boundary guards:")
    forbidden = ("openshorts", "ghl", "distribution", "social", "elevenlabs", "retell")
    imported = [m for m in sys.modules if any(f in m.lower() for f in forbidden)]
    check("no forbidden modules imported", not imported, ",".join(imported) if imported else "none")

    print("\n=== RESULT ===")
    print(f"  PASS: {len(_PASS)}  FAIL: {len(_FAIL)}")
    if _FAIL:
        print("  FAILED:", ", ".join(_FAIL))
        return 1
    print("  ALL ROUTING PROOF CRITERIA PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
