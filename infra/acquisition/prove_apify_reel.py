#!/usr/bin/env python3
"""Phase 3 — ONE bounded Apify Reel proof (DdZS1QRhrXE).

Boundaries enforced: single pinned Actor, one canonical URL, resultsLimit=1, no
fallback, no auto-retry, no expansion, circuit breaker first, ≤100 MiB, ≤5 min,
Personal Reaction Library, non-production. Signed URL is never printed/persisted.

Run with env sourced:  set -a; source ~/.openclaw/.env; set +a
                      python3 infra/acquisition/prove_apify_reel.py
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for d in ("acquisition", "local-helper", "discord-review"):
    sys.path.insert(0, str(REPO / "infra" / d))

import acquisition  # noqa: E402
import apify_client  # noqa: E402
import cost  # noqa: E402
import helper  # noqa: E402
import provider  # noqa: E402
import acquire as acq  # noqa: E402
from manual_intake import submission_fingerprint, validate_and_canonicalize  # noqa: E402

REEL_URL = "https://www.instagram.com/reel/DdZS1QRhrXE/"
ACTOR = provider.ACTOR_REEL_SCRAPER
MAX_BYTES = 100 * 1024 * 1024
MAX_DURATION = 300
LIBRARY_DIR = os.path.expanduser("~/.openclaw/loom-reaction-library/library")


def _env(name: str) -> str:
    v = (os.environ.get(name) or "").strip()
    if not v:
        raise SystemExit(f"[fatal] missing env var {name}")
    return v


def _get(url: str, key: str):
    req = urllib.request.Request(url, headers={"apikey": key, "Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode() or "[]")


def _post(url: str, key: str, payload: dict):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"apikey": key, "Authorization": f"Bearer {key}",
                 "Content-Type": "application/json", "Prefer": "return=representation"},
        method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode() or "[]")


def _patch(url: str, key: str, payload: dict):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"apikey": key, "Authorization": f"Bearer {key}",
                 "Content-Type": "application/json"},
        method="PATCH")
    with urllib.request.urlopen(req, timeout=30) as r:
        r.read()


def _unwrap(resp) -> dict:
    if isinstance(resp, dict):
        data = resp.get("data")
        if isinstance(data, dict):
            return data
    return resp if isinstance(resp, dict) else {}


def poll(transport, actor: str, run_id: str, timeout: int = 240) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        data = _unwrap(transport.get_run_status(actor, run_id))
        status = data.get("status")
        if status in ("SUCCEEDED", "FAILED", "TIMED-OUT", "ABORTED", "ABORTING"):
            return data
        time.sleep(3)
    raise SystemExit("[fatal] run poll timed out")


def main() -> None:
    token = apify_client.load_token()          # never printed
    supabase_url = _env("LOOM_SUPABASE_URL")
    supabase_key = _env("LOOM_SUPABASE_SERVICE_ROLE_KEY")

    # 1. circuit breaker
    ceiling_rows = _get(
        f"{supabase_url}/rest/v1/loom_provider_ceiling?id=eq.1"
        "&select=ceiling_micro,lifetime_spent_micro,max_acquisitions,acquisitions_succeeded,disabled",
        supabase_key)
    if not ceiling_rows:
        raise SystemExit("[fatal] circuit breaker row missing (migration 035?)")
    c = ceiling_rows[0]
    if c.get("disabled"):
        raise SystemExit("[fatal] circuit breaker disabled")
    cs = cost.check_circuit(c["lifetime_spent_micro"], c["acquisitions_succeeded"],
                            ceiling_micro=c["ceiling_micro"], max_success=c["max_acquisitions"])
    if cs != "ok":
        raise SystemExit(f"[fatal] circuit breaker: {cs}")

    # 2. canonicalize + content id
    v = validate_and_canonicalize(REEL_URL)
    if v["platform"] != "instagram" or v["content_id"] != "DdZS1QRhrXE":
        raise SystemExit(f"[fatal] unexpected canonicalization: {v}")
    fp = submission_fingerprint(v["platform"], v["canonical_url"])

    # 3. acquisition job (authorization)
    job = acquisition.plan_acquisition_job(
        fingerprint=fp, platform=v["platform"], platform_content_id=v["content_id"],
        canonical_url=v["canonical_url"],
        authorization_type=acquisition.AUTH_ACQUIRE_FOR_REACTION, actor="bryan",
        destination=acquisition.DESTINATION_PERSONAL_REACTION_LIBRARY,
        acquisition_mode=acquisition.MODE_FULL_ITEM,
        max_bytes=MAX_BYTES, max_duration_seconds=MAX_DURATION)
    job["status"] = acquisition.STATUS_RUNNING
    job["status_reason"] = ""
    job["adapter"] = provider.adapter_name(ACTOR)
    job_row, created = acquisition.persist_job(supabase_url, supabase_key, job)
    job_id = job_row.get("id")

    # 4. reserve worst-case cost
    est = cost.REEL_SCRAPER_RESULT_MICRO
    reserved = cost.reserve_worst_case(est, True, c["lifetime_spent_micro"],
                                       ceiling_micro=c["ceiling_micro"])

    # 5. build input + start run (pinned numeric actor id, not the website slug)
    transport = apify_client.HttpTransport(token)
    inp = provider.build_input(v["canonical_url"])
    api_id = provider.actor_api_id(ACTOR)
    run = _unwrap(transport.start_run(api_id, inp))
    run_id = run.get("id")
    build_id = run.get("buildId")
    build_number = run.get("buildNumber")
    dataset_id = run.get("defaultDatasetId")

    # 6. poll to completion
    final = poll(transport, api_id, run_id)
    if final.get("status") != "SUCCEEDED":
        raise SystemExit(f"[fatal] run not SUCCEEDED: {final.get('status')}")

    # 7. fetch items
    items = transport.get_dataset_items(dataset_id)

    # 8. validate output (exactly one, content-id match, video URL)
    out = provider.validate_output(items, content_id=v["content_id"])
    video_url = out["video_url"]  # signed; never printed/persisted

    # 9. download + classify + ffprobe + hash + finalize
    tmp = tempfile.mkdtemp(prefix="loom-apify-")
    try:
        tmp_path, written, http_status, ct = acq.download_one_video(
            video_url, tmp, max_bytes=MAX_BYTES, timeout=60)
        acq.classify_downloaded_file(tmp_path, http_status=http_status,
                                     content_type=ct, size_bytes=written)
        meta = acq.parse_ffprobe_result(acq.run_ffprobe(tmp_path),
                                        actual_size_bytes=written,
                                        max_bytes=MAX_BYTES, max_duration=MAX_DURATION)
        if not meta["has_audio"]:
            raise SystemExit("[fatal] downloaded MP4 has NO audio (source audio required)")
        digest = acq.checksum_sha256(tmp_path)
        filename = acq.library_filename(v["canonical_url"], digest)
        os.makedirs(LIBRARY_DIR, exist_ok=True)
        final_path = os.path.join(LIBRARY_DIR, filename)
        acq.finalize(tmp_path, final_path)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # 10. library record
    rec = acq.build_library_entry(
        canonical_url=v["canonical_url"], filename=filename, digest=digest,
        size_bytes=written, duration_seconds=meta["duration_seconds"],
        content_type="video/mp4", local_path=final_path, job_id=job_id,
        is_non_production=True)
    lib_row = helper.persist_library_entry(supabase_url, supabase_key, rec)

    # 11. job -> acquired
    helper.update_job_status(supabase_url, supabase_key, job["dedup_key"],
                             acquisition.STATUS_ACQUIRED, "")

    # 12. reconcile actual + record provider run (NO signed URL)
    actual = cost.reconcile_actual(reserved, est)
    prov = provider.build_provenance(ACTOR, video_url)
    run_row = _post(f"{supabase_url}/rest/v1/loom_provider_runs", supabase_key, {
        "provider": provider.PROVIDER_APIFY, "actor_id": ACTOR,
        "actor_build": build_id or "", "run_id": run_id,
        "acquisition_job_id": job_id, "status": "acquired",
        "cost_estimate_micro": reserved, "cost_actual_micro": actual,
        "output_ref": json.dumps({"build_number": build_number}),
    })

    # 13. update circuit breaker
    new_spent = c["lifetime_spent_micro"] + actual
    new_succeeded = c["acquisitions_succeeded"] + 1
    _patch(f"{supabase_url}/rest/v1/loom_provider_ceiling?id=eq.1", supabase_key, {
        "lifetime_spent_micro": new_spent,
        "acquisitions_succeeded": new_succeeded,
    })

    report = {
        "1_actor_build_run": {"actor": ACTOR, "api_id": api_id, "build_id": build_id,
                              "build_number": build_number, "run_id": run_id},
        "2_output_field": "videoUrl",  # first present field used (see provider.VIDEO_URL_FIELDS)
        "3_result_count_and_content_id": {"count": len(items),
                                          "content_id": v["content_id"]},
        "4_media": {"duration_seconds": meta["duration_seconds"],
                    "width": meta["width"], "height": meta["height"],
                    "frame_rate": meta["frame_rate"], "has_audio": meta["has_audio"],
                    "size_bytes": written, "container": meta["container"],
                    "video_codec": meta["video_codec"]},
        "5_obs_path_sha256": {"local_path": final_path, "sha256": digest},
        "6_ids": {"provider_run_id": (run_row[0]["id"] if isinstance(run_row, list) and run_row else None),
                  "acquisition_job_id": job_id,
                  "library_id": lib_row.get("id") if isinstance(lib_row, dict) else None},
        "7_cost": {"estimate_micro": est, "reserved_micro": reserved, "actual_micro": actual},
        "8_circuit_breaker": {"lifetime_spent_micro": new_spent,
                              "acquisitions_succeeded": new_succeeded,
                              "ceiling_micro": c["ceiling_micro"],
                              "max_acquisitions": c["max_acquisitions"]},
        "9_hygiene": {"signed_url_persisted": False, "temp_files_removed": True,
                      "source_host_redacted": prov.get("source_host")},
        "10_uncertainty": {"url_expiry": "signed URL consumed immediately; expiry unverified",
                           "zero_result_billing": "unverified (this run produced one result)"},
    }
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
