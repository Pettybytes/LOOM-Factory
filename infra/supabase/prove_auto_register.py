#!/usr/bin/env python3
"""Proof: the PRODUCTION ingest path auto-persists the durable clean artifact.

Calls adapter.register_clip_media() — the function send_review_card() now invokes
for every freshly rendered clip (moment review) — against a fresh clip laid down
on the OpenShorts output disk. Asserts the loom_media row is created AUTOMATICALLY
with clean_source_url/hash/size/filename + canonical_url, all reachable, with NO
manual SQL/script population. Uses a brand-new clip_id (loom-auto-proof:*), never
touching the DDG fixture or the synthetic fixture.
"""
import json
import os
import sys
import urllib.request

_ENV_PATH = os.path.expanduser("~/.openclaw/.env")
for _line in open(_ENV_PATH):
    _line = _line.strip()
    if not _line or _line.startswith("#") or "=" not in _line:
        continue
    _k, _, _v = _line.partition("=")
    _k = _k.strip(); _v = _v.strip().strip('"').strip("'")
    if _k.startswith("LOOM_") and _k not in os.environ:
        os.environ[_k] = _v

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_REPO, "infra", "discord-review"))
import adapter  # noqa: E402

CLIP_ID = "loom-auto-proof:0"
TENANT = "dreemzy"
VERSION = "render-1.0"
JOB = "loom-auto-proof-job"
READY = "subtitled_1789524199_hooked_1789524199_loom_auto_proof_source_clip_1.mp4"
MASTER = "loom_auto_proof_source_clip_1.mp4"


def reachable(url):
    if not url:
        return False
    req = urllib.request.Request(url, method="GET")
    req.add_header("Range", "bytes=0-1023")
    req.add_header("User-Agent", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124.0 Safari/537.36")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status in (200, 206)
    except Exception:
        return False


print(f"register_clip_media({TENANT}, {CLIP_ID}, {VERSION}, {JOB}, {READY}, {MASTER})")
row = adapter.register_clip_media(TENANT, CLIP_ID, VERSION, JOB, READY, MASTER)
assert row, "register_clip_media returned None"

for k in ("canonical_url", "clean_source_url", "clean_source_hash",
          "clean_source_size_bytes", "clean_source_filename", "file_hash", "size_bytes"):
    v = row.get(k)
    print(f"  {k:24s}: {v if not str(v).startswith('http') else v[:70]}")
    assert v, f"missing {k}"

# Both URLs must be reachable (durable filesafe CDN), independent of OpenShorts.
assert reachable(row["canonical_url"]), "canonical_url not reachable"
assert reachable(row["clean_source_url"]), "clean_source_url not reachable"
print("  canonical_url reachable: True")
print("  clean_source_url reachable: True")

# The clean-source identity must match the actual clean file's hash/size.
clean_path = os.path.join(adapter.OPENSHORTS_OUTPUT, JOB, MASTER)
import hashlib
h = hashlib.sha256()
with open(clean_path, "rb") as f:
    for chunk in iter(lambda: f.read(1024 * 1024), b""):
        h.update(chunk)
assert row["clean_source_hash"] == h.hexdigest(), "clean_source_hash mismatch"
assert int(row["clean_source_size_bytes"]) == os.path.getsize(clean_path), "clean_source_size mismatch"
assert row["clean_source_filename"] == MASTER, "clean_source_filename mismatch"
print("  clean_source_hash/size/filename match the on-disk clean file")

print("\n✅ PROOF PASSED: the production ingest path (register_clip_media, invoked by")
print("   send_review_card) auto-populates canonical_url + clean_source_url + hash +")
print("   size + filename for a fresh normal clip — no manual fixture registration.")
