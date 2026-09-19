#!/usr/bin/env python3
"""One bounded TikTok actor proof (live, paid, hard-capped at $0.01).

    python3 infra/acquisition/prove_tiktok_actor.py <actor> [url]

Boundaries, enforced by construction:
  * ONE actor identity, ONE canonical URL, ONE item, ONE attempt, no retry;
  * the charge cap is a RUN PARAMETER (``maxTotalChargeUsd=0.01``), never actor input;
  * this actor's TikTok ceiling is reserved IN FULL before the run, under a
    provisional key that is atomically rebound to the real Apify run id;
  * reconciliation happens exactly once, keyed on that Apify run id;
  * a missing ``usageTotalUsd`` retains the reservation and reports COST_UNVERIFIED;
  * media travels the SHARED pipeline (download → HTML/format gate → ffprobe →
    normalize to MP4/H.264/AAC/yuv420p/fast-start → hash → atomic finalize →
    Reaction Library row);
  * signed media URLs are never printed or persisted — only a redacted host.

Run with env sourced:  set -a; source ~/.openclaw/.env; set +a
"""

from __future__ import annotations

import functools
import json
import os
import sys
import time
import urllib.error
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for _d in ("acquisition", "local-helper", "discord-review"):
    sys.path.insert(0, str(REPO / "infra" / _d))

import acquire as acq  # noqa: E402          (local-helper)
import apify_client  # noqa: E402
import assemble as asm  # noqa: E402          (local-helper)
import helper  # noqa: E402                  (local-helper)
import library_root  # noqa: E402
import media_filter  # noqa: E402
import media_normalize  # noqa: E402
import tiktok_actors as ta  # noqa: E402
import tiktok_ceiling as tc  # noqa: E402
import tiktok_media as tm  # noqa: E402
import tiktok_url as tu  # noqa: E402

DEFAULT_TARGET = "https://www.tiktok.com/@thoughtswthot/video/7598767615980408095"
MAX_BYTES = 100 * 1024 * 1024
MAX_DURATION = 300.0
POLL_TIMEOUT_S = 300


def _env(name: str) -> str:
    value = (os.environ.get(name) or "").strip()
    if not value:
        raise SystemExit(f"[fatal] missing env var {name}")
    return value


def _redact(url: str) -> str:
    return media_filter.redact_url(url) if url else ""


def poll(transport, actor: str, run_id: str, *, timeout: int = POLL_TIMEOUT_S) -> dict:
    deadline = time.time() + timeout
    last: dict = {}
    while time.time() < deadline:
        last = transport.get_run_status(actor, run_id) or {}
        if ta.is_terminal(last):
            return last
        time.sleep(3)
    return last


def build_media_io() -> tm.TikTokMediaIO:
    library_root.ensure_dirs()
    return tm.TikTokMediaIO(
        # The downloader's CDN allowlist is scoped to THIS platform's hosts; the
        # default (Meta/Instagram) allowlist would refuse TikTok's CDN outright.
        download_one_video=functools.partial(
            acq.download_one_video, host_check=ta.is_approved_media_host),
        classify_downloaded_file=acq.classify_downloaded_file,
        run_ffprobe=acq.run_ffprobe,
        parse_ffprobe_result=acq.parse_ffprobe_result,
        checksum_sha256=acq.checksum_sha256,
        finalize=acq.finalize,
        cleanup_temp=acq.cleanup_temp,
        run_ffmpeg=media_normalize.run_ffmpeg_command,
        run_ffprobe_json=media_normalize.run_ffprobe_json,
        mux_builder=asm.build_mux_args,
        tmp_dir=str(library_root.tmp_dir()),
        library_dir=str(library_root.library_dir()),
    )


def main() -> None:
    actor = (sys.argv[1] if len(sys.argv) > 1 else ta.ACTOR_CLOCKWORKS).strip()
    url = (sys.argv[2] if len(sys.argv) > 2 else DEFAULT_TARGET).strip()

    spec = ta.get_actor(actor)
    token = apify_client.load_token()                     # never printed
    supabase_url = _env("LOOM_SUPABASE_URL")
    supabase_key = _env("LOOM_SUPABASE_SERVICE_ROLE_KEY")

    report: dict = {
        "1_actor": {"actor": spec.actor, "label": spec.label,
                    "cost_micro_per_result": spec.cost_micro_per_result,
                    "runtime_proven_before": spec.runtime_proven,
                    "proven_modes_before": sorted(spec.proven_modes),
                    "charge_cap_usd": ta.MAX_TOTAL_CHARGE_USD},
        "2_target": {}, "3_run": {}, "4_charge_cap": {}, "5_usage": {},
        "6_results": {}, "7_media_markers": {}, "8_download": {},
        "9_streams": {}, "10_artifact": {}, "11_ledger": {},
        "12_error": None, "13_retries": 0,
        "14_instagram_isolation": tc.instagram_isolation(),
    }

    # --- validate the target BEFORE any spend ----------------------------
    parsed = tu.parse(url)
    if parsed["kind"] != "video":
        raise SystemExit(f"[fatal] target is not a video: {parsed}")
    canonical = parsed["canonical_url"]
    content_id = parsed["content_id"]
    fingerprint = tu.fingerprint_for(canonical)
    report["2_target"] = {"input_url": url, "canonical_url": canonical,
                          "content_id": content_id, "kind": parsed["kind"],
                          "fingerprint": fingerprint,
                          "validated": True}

    ledger = tc.TikTokSpendLedger(tc.rest_store(supabase_url, supabase_key))
    report["11_ledger"]["before"] = ledger.state()

    # Idempotency: never re-acquire a source already in the library.
    try:
        library_rows = helper.library_rows()
    except Exception as e:  # noqa: BLE001
        library_rows = []
        report["11_ledger"]["library_lookup_error"] = type(e).__name__

    provisional = ledger.provisional_run_id(actor=actor, content_id=content_id)
    reservation = ledger.reserve(actor=actor, run_id=provisional)
    report["11_ledger"]["reservation"] = reservation
    report["11_ledger"]["after_reserve"] = ledger.state()

    transport = apify_client.HttpTransport(token)
    run: dict = {}
    run_id = ""
    started_at = time.time()

    try:
        actor_input, run_params = ta.build_run_input(actor, canonical)
        report["4_charge_cap"] = dict(ta.charge_cap_report())
        report["4_charge_cap"]["actor_input_keys"] = sorted(actor_input)
        report["4_charge_cap"]["cap_in_actor_input"] = any(
            "charge" in k.lower() for k in actor_input)

        run = ta.unwrap_run(transport.start_run(actor, actor_input, **run_params))
        run_id = str(run.get("id") or "")
        if not run_id:
            raise ta.TikTokActorError("NO_RUN_ID", "start_run returned no run id")

        ledger.bind_run_id(actor=actor, provisional_run_id=provisional,
                           apify_run_id=run_id)
        report["3_run"] = {
            "run_id": run_id,
            "actor_id": str(run.get("actId") or ""),
            "build_id": str(run.get("buildId") or ""),
            "build_number": str(run.get("buildNumber") or ""),
            "dataset_id": str(run.get("defaultDatasetId") or ""),
            "started_at_epoch": started_at,
        }
        report["4_charge_cap"]["seen_usd"] = ta.charge_cap_seen(run)

        final = poll(transport, actor, run_id)
        report["3_run"]["terminal_status"] = ta.terminal_state(final)
        report["3_run"]["finished_at_epoch"] = time.time()
        report["3_run"]["duration_seconds"] = round(time.time() - started_at, 1)

        usage = ta.usage_total_usd(final)
        report["5_usage"] = {"usage_total_usd": usage,
                             "actual_micro": tc.actual_micro_from_usage(usage)}

        dataset_id = ta.dataset_id_of(final) or report["3_run"]["dataset_id"]
        items = transport.get_dataset_items(dataset_id) if dataset_id else []
        report["6_results"]["item_count"] = len(items)

        try:
            ta.require_terminal_success(final)
            target = ta.translate(actor, items, expected_content_id=content_id,
                                  canonical_url=canonical)
        except ta.TikTokActorError as e:
            report["12_error"] = {"code": e.code, "message": e.message,
                                  "classified": True}
            report["11_ledger"]["reconcile"] = ledger.reconcile(
                actor=actor, run_id=run_id, usage_total_usd=usage, succeeded=False)
            report["11_ledger"]["after"] = ledger.state()
            print(json.dumps(report, indent=2))
            return

        report["6_results"].update({
            "translated_content_id": target["content_id"],
            "canonical_url_matched": target["canonical_url"] == canonical,
            "kind": target["kind"], "handle": target["handle"],
            "duration_seconds": target["duration_seconds"],
            "width": target["width"], "height": target["height"],
            "expires_at": target.get("expires_at") or "",
        })
        report["7_media_markers"] = {
            "field_map": target["field_map"],
            "media_host": _redact(target["media_url"]),
            "audio_url_present": bool(target["audio_url"]),
            "audio_host": _redact(target["audio_url"]),
        }

        media_io = build_media_io()
        try:
            asset = tm.acquire_tiktok_media(
                target={**target, "job_id": ""},
                io=media_io, library_rows=library_rows,
                persist_library=lambda rec: helper.persist_library_entry(
                    supabase_url, supabase_key, rec) or {},
                max_bytes=MAX_BYTES, max_duration=MAX_DURATION)
        except Exception as e:  # noqa: BLE001
            # ANY media-stage failure reconciles with the charge this run ACTUALLY
            # reported — the run already succeeded, so its cost is known and must
            # not be re-classified as unverified by a downstream local defect.
            detail = getattr(e, "detail", None) or None
            report["12_error"] = {
                "code": getattr(e, "code", None) or ta.map_provider_error(e),
                "message": str(getattr(e, "message", e))[:200],
                "type": type(e).__name__, "classified": True, "stage": "media",
                "run_created": True, "run_id": run_id,
                "artifact": ({"asset_filename": detail.get("asset_filename"),
                              "asset_hash": detail.get("asset_hash"),
                              "local_path": detail.get("local_path")}
                             if detail else None),
            }
            report["11_ledger"]["reconcile"] = ledger.reconcile(
                actor=actor, run_id=run_id, usage_total_usd=usage, succeeded=False)
            report["11_ledger"]["after"] = ledger.state()
            print(json.dumps(report, indent=2))
            return

        report["8_download"] = {
            "http_status": asset.get("http_status"),
            "content_type": asset.get("downloaded_content_type"),
            "size_bytes": asset.get("size_bytes"),
            "idempotent": asset.get("idempotent"),
        }
        report["9_streams"] = {
            "duration_seconds": asset.get("duration_seconds"),
            "width": asset.get("width"), "height": asset.get("height"),
            "frame_rate": asset.get("frame_rate"),
            "video_codec": asset.get("video_codec"),
            "audio_codec": asset.get("audio_codec"),
            "pix_fmt": asset.get("pix_fmt"),
            "container": asset.get("container"),
            "has_audio": bool(asset.get("audio_codec")),
            "nan_flags": [k for k, v in asset.items()
                          if isinstance(v, float) and v != v],
        }
        report["10_artifact"] = {
            "asset_filename": asset.get("asset_filename"),
            "sha256": asset.get("asset_hash"),
            "local_path": asset.get("local_path"),
            "normalized": asset.get("normalized"),
            "remuxed": asset.get("remuxed"),
            "muxed": asset.get("muxed"),
            "gaps_before": asset.get("gaps"),
            "library_id": (asset.get("library_row") or {}).get("id", ""),
            "library_row_sanitized": True,
        }

        report["11_ledger"]["reconcile"] = ledger.reconcile(
            actor=actor, run_id=run_id, usage_total_usd=usage, succeeded=True)
        report["11_ledger"]["after"] = ledger.state()

    except Exception as e:  # noqa: BLE001 — a refusal is a classified outcome
        code = ta.map_provider_error(e)
        err = {"code": code, "message": str(getattr(e, "message", e))[:200],
               "type": type(e).__name__, "classified": True,
               "run_created": bool(run_id), "run_id": run_id}
        if isinstance(e, urllib.error.HTTPError) and 400 <= int(e.code or 0) < 500:
            err["no_run_possible"] = True
        report["12_error"] = err
        # Reserve-then-reconcile honesty: a run that was never created is a
        # verified zero charge. Anything else keeps the reservation standing.
        usage = 0.0 if err.get("no_run_possible") and not run_id else None
        key = run_id or provisional
        try:
            report["11_ledger"]["reconcile"] = ledger.reconcile(
                actor=actor, run_id=key, usage_total_usd=usage, succeeded=False)
        except Exception as e2:  # noqa: BLE001
            report["11_ledger"]["reconcile_error"] = type(e2).__name__
        report["11_ledger"]["after"] = ledger.state()

    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
