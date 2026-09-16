#!/usr/bin/env python3
"""Register the LOOM clean-source fresh fixture (idempotent).

Persists the clean pre-hook/pre-subtitle clip + its burned derivative as
immutable, tenant-scoped loom_media artifacts, plus the correction row the
durable bootstrap needs to resolve a valid trim/source range.
"""
import json
import os
import urllib.request

SB_URL = os.environ["SB_URL"]
SB_KEY = os.environ["SB_KEY"]

def headers(extra=None):
    h = {"apikey": SB_KEY, "Authorization": f"Bearer {SB_KEY}"}
    if extra:
        h.update(extra)
    return h

def post(path, payload, on_conflict=None, prefer=None):
    url = SB_URL + "/rest/v1/" + path
    if on_conflict:
        url += "?on_conflict=" + on_conflict
    body = json.dumps(payload).encode()
    h = headers({"Content-Type": "application/json"})
    h["Prefer"] = prefer or "return=representation"
    req = urllib.request.Request(url, data=body, headers=h, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.status, json.loads(resp.read().decode() or "[]")

def get(path, params):
    qs = "&".join(f"{k}=eq.{v}" for k, v in params.items())
    url = SB_URL + "/rest/v1/" + path + "?" + qs + "&select=*"
    req = urllib.request.Request(url, headers=headers(), method="GET")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode() or "[]")

CLIP = "loom-clean-fixture:0"
TENANT = "dreemzy"
BURNED_NAME = "subtitled_1789522129_hooked_1789522129_loom_clean_fixture_clip_1.mp4"

media = {
    "tenant_id": TENANT,
    "clip_id": CLIP,
    "asset_version": "render-1.0",
    "file_hash": "d114e167f8497c7f950c4f1c0d8e6ed7efb97d8564973e2bfe44f160ef4eb5d6",
    "size_bytes": 399148,
    "duration_seconds": 14.0,
    "ghl_location_id": "GNhV1OyhFLzcl67XwTBV",
    "ghl_media_id": "6aa9f0e598106dcc4e26896b",
    "ghl_filename": "loom_clean_fixture_clip_1.mp4",
    "canonical_url": "https://assets.cdn.filesafe.space/GNhV1OyhFLzcl67XwTBV/media/4c52eb41-1eba-4779-b6b8-88b0db480808.mp4",
    "clean_source_url": "https://assets.cdn.filesafe.space/GNhV1OyhFLzcl67XwTBV/media/97dae0b4-8e8c-4db1-9a90-7f7b101b222a.mp4",
    "clean_source_hash": "69e76d7b69dd54bd087e56a2c286e67a162e64bba80755a4af75adc54ae455cc",
    "clean_source_size_bytes": 374306,
    "clean_source_filename": "loom_clean_fixture_clip_1.mp4",
    "storage_status": "fixture",
    "clip_index": 0,
    "human_ordinal": 1,
    "source_filename": BURNED_NAME,
    "publication_status": "not_approved",
    "content_type": "video/mp4",
    "previous_asset_version": None,
}

correction = {
    "tenant_id": TENANT,
    "clip_id": CLIP,
    "source_asset_version": "render-1.0",
    "target_asset_version": "render-1.0",
    "reviewer": "bentley-clean-fixture",
    "trim_start": 0.0,
    "trim_end": 14.0,
    "caption_changes": {
        "text": "the clean source proof",
        "style": "classic",
        "position": "bottom",
        "blocks": [
            {"text": "the", "startMs": 0, "endMs": 500},
            {"text": "clean", "startMs": 500, "endMs": 1100},
            {"text": "source", "startMs": 1100, "endMs": 1800},
            {"text": "proof", "startMs": 1800, "endMs": 2600},
        ],
    },
    "crop_reframe_changes": {"framing": "auto"},
    "llm_assist_used": False,
    "edl_metadata": {"framing": "auto", "segments": [{"start": 0.0, "end": 14.0}], "caption_applied": True},
    "render_status": "success",
}

# 1) Upsert media (idempotent on the identity key).
status, rows = post(
    "loom_media", media,
    on_conflict="tenant_id,clip_id,asset_version",
    prefer="resolution=merge-duplicates,return=representation",
)
print(f"loom_media upsert: HTTP {status}")

# 2) Correction: insert only if not already present for this clip+target.
existing = get("loom_edit_corrections", {"tenant_id": TENANT, "clip_id": CLIP, "target_asset_version": "render-1.0"})
if existing:
    print(f"correction already present ({len(existing)} row(s)) — skipping insert")
else:
    status, rows = post("loom_edit_corrections", correction)
    print(f"loom_edit_corrections insert: HTTP {status}")

# 3) Verify.
media_rows = get("loom_media", {"tenant_id": TENANT, "clip_id": CLIP})
corr_rows = get("loom_edit_corrections", {"tenant_id": TENANT, "clip_id": CLIP})
print(f"\nverified media rows: {len(media_rows)}, corrections: {len(corr_rows)}")
for r in media_rows:
    print("  media:", r.get("asset_version"), "| canonical:", bool(r.get("canonical_url")), "| clean:", bool(r.get("clean_source_url")))
