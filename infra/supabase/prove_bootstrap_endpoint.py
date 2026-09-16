#!/usr/bin/env python3
"""Prove the LIVE bootstrap endpoint (restarted adapter, new code) returns the
fresh fixture's reachable clean_source_url.

Creates an active edit session for loom-clean-fixture:0 @ render-1.0 with a
known session token, then hits GET /discord/editor/bootstrap (the exact handler
Caddy fronts publicly) and asserts the clean_source_url is returned + reachable.
"""
import hashlib
import json
import os
import secrets
import urllib.request

# Load LOOM_* env from ~/.openclaw/.env if not already present (names only).
_ENV_PATH = os.path.expanduser("~/.openclaw/.env")
for _line in open(_ENV_PATH):
    _line = _line.strip()
    if not _line or _line.startswith("#") or "=" not in _line:
        continue
    _k, _, _v = _line.partition("=")
    _k = _k.strip(); _v = _v.strip().strip('"').strip("'")
    if _k.startswith("LOOM_") and _k not in os.environ:
        os.environ[_k] = _v

SB_URL = os.environ["LOOM_SUPABASE_URL"]
SB_KEY = os.environ["LOOM_SUPABASE_SERVICE_ROLE_KEY"]
BOOTSTRAP = "http://127.0.0.1:8090/discord/editor/bootstrap"

def sb_headers(extra=None):
    h = {"apikey": SB_KEY, "Authorization": f"Bearer {SB_KEY}"}
    if extra:
        h.update(extra)
    return h

def post(path, payload):
    url = SB_URL + "/rest/v1/" + path
    body = json.dumps(payload).encode()
    h = sb_headers({"Content-Type": "application/json", "Prefer": "return=representation"})
    req = urllib.request.Request(url, data=body, headers=h, method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.status, json.loads(r.read().decode() or "[]")

raw = "clean-fixture-proof-" + secrets.token_hex(8)
tok_hash = hashlib.sha256(raw.encode()).hexdigest()

# Active session for the fresh fixture (source_asset_version = render-1.0).
from datetime import datetime, timezone, timedelta
exp = (datetime.now(timezone.utc) + timedelta(hours=4)).isoformat()
status, rows = post("loom_edit_sessions", {
    "tenant_id": "dreemzy",
    "clip_id": "loom-clean-fixture:0",
    "source_asset_version": "render-1.0",
    "reviewer": "bentley-endpoint-proof",
    "token_hash": tok_hash,
    "session_token_hash": tok_hash,
    "status": "active",
    "expires_at": exp,
    "consumed_at": datetime.now(timezone.utc).isoformat(),
})
print(f"session insert: HTTP {status}")

# Hit the live bootstrap endpoint with the raw session token.
req = urllib.request.Request(BOOTSTRAP, method="GET")
req.add_header("Authorization", f"Bearer {raw}")
try:
    with urllib.request.urlopen(req, timeout=30) as r:
        body = r.read().decode()
        code = r.status
except urllib.error.HTTPError as e:
    body = e.read().decode()
    code = e.code
print(f"bootstrap endpoint: HTTP {code}")
data = json.loads(body)
print("media_url        :", data.get("media_url"))
print("clean_source_url :", data.get("clean_source_url"))
print("has_burned_hook  :", data.get("has_burned_hook"))
print("has_burned_capts :", data.get("has_burned_captions"))
print("duration         :", data.get("duration_seconds"))

assert code == 200, f"expected 200, got {code}"
assert data.get("clean_source_url"), "clean_source_url missing from live bootstrap"
cs = data["clean_source_url"]
req = urllib.request.Request(cs, method="GET")
req.add_header("Range", "bytes=0-1023")
req.add_header("User-Agent", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124.0 Safari/537.36")
with urllib.request.urlopen(req, timeout=20) as r:
    ok = r.status in (200, 206)
print(f"clean_source_url reachable: {ok} (HTTP {r.status})")
assert ok
print("\n✅ LIVE bootstrap endpoint returns the fresh fixture's reachable clean_source_url.")
