#!/usr/bin/env python3
"""Full integration proof — real review path (NOT a direct function call).

Posts a fresh non-production clip's card payload to the REAL /discord/card
endpoint (through Caddy with the tunnel token, exactly as n8n does). This drives
the adapter's HTTP handler -> send_review_card() -> register_clip_media() ->
GHL upload + loom_media upsert -> Discord card. Then verifies loom_media,
evicts the transient job, and proves the durable bootstrap loads the clean
source for the Edit path. Prints the Discord message id for manual acceptance.
"""
import hashlib
import json
import os
import shutil
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CARD_URL = "http://127.0.0.1:8081/discord/card"

# Load LOOM_* env.
for line in open(os.path.expanduser("~/.openclaw/.env")):
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    k, _, v = line.partition("=")
    k = k.strip(); v = v.strip().strip('"').strip("'")
    if k.startswith("LOOM_") and k not in os.environ:
        os.environ[k] = v

SB_URL = os.environ["LOOM_SUPABASE_URL"]
SB_KEY = os.environ["LOOM_SUPABASE_SERVICE_ROLE_KEY"]

# Tunnel token from infra/tunnel-proxy/.env (the Caddy card-route auth).
tunnel_token = None
for line in open(os.path.join(REPO, "infra", "tunnel-proxy", ".env")):
    if line.startswith("LOOM_TUNNEL_TOKEN="):
        tunnel_token = line.split("=", 1)[1].strip()
assert tunnel_token, "tunnel token not found"

JOB = "loom-e2e-proof-job"
CLIP = JOB + ":0"
READY = "subtitled_1789524911_hooked_1789524911_loom_e2e_proof_source_clip_1.mp4"
MASTER = "loom_e2e_proof_source_clip_1.mp4"

payload = {
    "clip_id": CLIP,
    "tenant_id": "dreemzy",
    "asset_version": "render-1.0",
    "review_stage": "moment",
    "job_id": JOB,
    "ready_file": READY,
    "master_asset": MASTER,
    "source_title": "LOOM E2E Proof Source",
    "proposed_shorts_title": "E2E Integration Proof",
    "caption": "E2E integration proof clip",
    "hook": "WAIT FOR THE ENDING",
    "overall_score": 78,
    "decision": "repairable",
    "duration_seconds": 6.0,
    "source_url": "https://example.com/non-production-e2e",
    "run_id": "loom-run-e2e-proof",
    "rights_policy": None,
    "publication_enabled": False,
    "captions_applied": True,
    "hook_applied": True,
    "no_speech": False,
    "transcript_segment_count": 4,
    "render_version": "1.0",
}

print("=== 1) POST real review card (Caddy + tunnel token -> adapter -> send_review_card) ===")
body = json.dumps(payload).encode()
req = urllib.request.Request(CARD_URL, data=body, method="POST")
req.add_header("Authorization", "Bearer " + tunnel_token)
req.add_header("Content-Type", "application/json")
try:
    with urllib.request.urlopen(req, timeout=120) as r:
        code = r.status
        resp = json.loads(r.read().decode() or "{}")
except urllib.error.HTTPError as e:
    code = e.code
    resp = json.loads(e.read().decode() or "{}")
print("HTTP", code)
print("message_id:", resp.get("message_id"))
print("channel_id:", resp.get("channel_id"))
print("proxy_size:", resp.get("proxy_size"))
assert code == 200 and resp.get("message_id"), f"card send failed: {resp}"
message_id = resp["message_id"]

print("\n=== 2) Verify loom_media was auto-populated (burned + clean) ===")
q = f"tenant_id=eq.dreemzy&clip_id=eq.{CLIP}&select=*"
req = urllib.request.Request(SB_URL + "/rest/v1/loom_media?" + q)
req.add_header("apikey", SB_KEY); req.add_header("Authorization", "Bearer " + SB_KEY)
with urllib.request.urlopen(req, timeout=30) as r:
    rows = json.loads(r.read().decode() or "[]")
assert rows, "no loom_media row for the clip"
row = rows[0]
for k in ("canonical_url", "clean_source_url", "clean_source_hash",
          "clean_source_size_bytes", "clean_source_filename", "file_hash",
          "size_bytes", "duration_seconds"):
    print(f"  {k}: {row.get(k)}")
    assert row.get(k), f"missing {k}"
assert row["clean_source_filename"] == MASTER
assert float(row["duration_seconds"]) == 6.0
print("  ✓ burned + clean URLs, hash/size/filename + duration all populated")

print("\n=== 3) Verify both URLs reachable ===")
for label, k in (("burned (canonical)", "canonical_url"), ("clean source", "clean_source_url")):
    u = row[k]
    req = urllib.request.Request(u, method="GET")
    req.add_header("Range", "bytes=0-0")
    req.add_header("User-Agent", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124.0 Safari/537.36")
    with urllib.request.urlopen(req, timeout=20) as r:
        assert r.status in (200, 206), f"{k} unreachable: {r.status}"
    print(f"  ✓ {label} reachable: {u[:70]}")

print("\n=== 4) Evict transient job state (remove render output dir) ===")
job_dir = os.path.join(REPO, "openshorts", "output", JOB)
if os.path.isdir(job_dir):
    shutil.rmtree(job_dir)
print("  removed:", job_dir, "| still exists:", os.path.exists(job_dir))

print("\n=== 5) Edit path: bootstrap must load the clean-source editor post-eviction ===")
raw = "e2e-edit-" + os.urandom(8).hex()
tok = hashlib.sha256(raw.encode()).hexdigest()
from datetime import datetime, timezone, timedelta
exp = (datetime.now(timezone.utc) + timedelta(hours=4)).isoformat()
sess = {"tenant_id": "dreemzy", "clip_id": CLIP, "source_asset_version": "render-1.0",
        "reviewer": "bentley-e2e", "token_hash": tok, "session_token_hash": tok,
        "status": "active", "expires_at": exp,
        "consumed_at": datetime.now(timezone.utc).isoformat()}
body = json.dumps(sess).encode()
req = urllib.request.Request(SB_URL + "/rest/v1/loom_edit_sessions", data=body, method="POST")
req.add_header("apikey", SB_KEY); req.add_header("Authorization", "Bearer " + SB_KEY)
req.add_header("Content-Type", "application/json"); req.add_header("Prefer", "return=representation")
with urllib.request.urlopen(req, timeout=30) as r:
    pass

PUBLIC = "https://users-macbook-pro-1.taild000b2.ts.net/discord/editor/bootstrap"
req = urllib.request.Request(PUBLIC, method="GET")
req.add_header("Authorization", "Bearer " + raw)
with urllib.request.urlopen(req, timeout=30) as r:
    b = json.loads(r.read().decode())
print("  bootstrap media_url:", b.get("media_url"))
print("  bootstrap clean_source_url:", b.get("clean_source_url"))
print("  bootstrap duration:", b.get("duration_seconds"), "range:", b.get("source_range"))
print("  bootstrap has_burned_hook:", b.get("has_burned_hook"))
assert b.get("clean_source_url") == row["clean_source_url"]
assert b.get("duration_seconds") == 6.0
assert b.get("source_range") == {"start": 0.0, "end": 6.0}

print("\n" + "=" * 70)
print("✅ FULL INTEGRATION PASSED (real review path, not a direct function call)")
print("   Discord message id:", message_id)
print("   clip_id:", CLIP)
print("=" * 70)
