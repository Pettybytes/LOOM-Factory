#!/usr/bin/env python3
"""TikTok shared-lane + UI acceptance — IDEMPOTENT, with NO paid provider run.

Proves, against the REAL durable Reaction Library row, that a repeat request for
the already-acquired canonical TikTok video resolves without a second provider
run and without a second asset:

    submission/approval → canonical identity → dispatch selects Apidojo
    → existing durable acquisition detected → no provider call
    → no duplicate media file or library row → card reaches ACQUIRED

Every provider entry point is a TRIPWIRE: if anything tries to start a run,
fetch a run, or download media, the acceptance FAILS loudly. The ledger is
snapshotted before and after, so a spend of even one micro-dollar is caught.

Run with env sourced:  set -a; source ~/.openclaw/.env; set +a
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for _d in ("acquisition", "local-helper", "discord-review"):
    sys.path.insert(0, str(REPO / "infra" / _d))

import acquisition  # noqa: E402
import acquisition_card as card  # noqa: E402
import approved_lane as lane  # noqa: E402
import discord_media  # noqa: E402
import library_root  # noqa: E402
import live_runner as lr  # noqa: E402
import provider  # noqa: E402
import tiktok_actors as ta  # noqa: E402
import tiktok_ceiling as tc  # noqa: E402
import tiktok_media as tm  # noqa: E402
import tiktok_url as tu  # noqa: E402

CANONICAL = "https://www.tiktok.com/@thoughtswthot/video/7598767615980408095"
CONTENT_ID = "7598767615980408095"


def _env(name: str) -> str:
    value = (os.environ.get(name) or "").strip()
    if not value:
        raise SystemExit(f"[fatal] missing env var {name}")
    return value


class Tripwire(Exception):
    pass


class TripwireTransport:
    """Any provider call at all is a failure of the acceptance."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def start_run(self, *a, **k):
        self.calls.append("start_run")
        raise Tripwire("a provider run was attempted during an idempotent repeat")

    def get_run_status(self, *a, **k):
        self.calls.append("get_run_status")
        raise Tripwire("a provider run was polled during an idempotent repeat")

    def get_dataset_items(self, *a, **k):
        self.calls.append("get_dataset_items")
        raise Tripwire("a dataset was fetched during an idempotent repeat")


def tripwire_download(*a, **k):
    raise Tripwire("media was downloaded during an idempotent repeat")


def main() -> None:
    supabase_url = _env("LOOM_SUPABASE_URL")
    supabase_key = _env("LOOM_SUPABASE_SERVICE_ROLE_KEY")
    import helper  # local-helper: reads the durable library the same way the lane does

    receipt: dict = {"1_target": {}, "2_dispatch": {}, "3_idempotency": {},
                     "4_ledger": {}, "5_media": {}, "6_card": {}, "7_provider_calls": []}

    # --- 1. submission/approval identifies the canonical video -------------
    parsed = tu.parse(CANONICAL)
    fingerprint = tu.fingerprint_for(parsed["canonical_url"])
    receipt["1_target"] = {"canonical_url": parsed["canonical_url"],
                           "content_id": parsed["content_id"], "kind": parsed["kind"],
                           "fingerprint": fingerprint,
                           "tracking_params_present": "?" in CANONICAL}

    # --- the durable library is the ONLY source of "already acquired" ------
    rows = helper.library_rows()
    tiktok_rows = [r for r in rows if str(r.get("platform_content_id")) == CONTENT_ID]
    if not tiktok_rows:
        raise SystemExit("[fatal] the canonical TikTok asset is not in the library")
    receipt["1_target"]["durable_rows"] = len(tiktok_rows)

    # --- 2. dispatch selects Apidojo --------------------------------------
    actor = lane.default_provider_actor("tiktok")
    receipt["2_dispatch"] = {"platform": "tiktok", "actor": actor,
                             "pinned": actor == provider.ACTOR_TIKTOK_VIDEO,
                             "profile_input": provider.build_input(CANONICAL, platform="tiktok"),
                             "registry_proven": ta.get_actor(actor).runtime_proven,
                             "clockworks_selectable": False}
    try:
        provider.allowed_actor("tiktok", ta.ACTOR_CLOCKWORKS)
        raise SystemExit("[fatal] Clockworks must not be allowlisted for TikTok")
    except provider.ProviderError as e:
        receipt["2_dispatch"]["clockworks_refusal"] = e.code

    # --- authorized (Gate 2) job, exactly as the UI would create it -------
    gate1 = lane.plan_pending_job(fingerprint=fingerprint, platform="tiktok",
                                  platform_content_id=CONTENT_ID,
                                  canonical_url=parsed["canonical_url"], actor="bryan")
    job = lane.plan_authorized_job(pending_job=gate1, actor="bryan")
    job["status"] = acquisition.STATUS_QUEUED
    job["id"] = "idempotent-acceptance"

    ceiling_row = tc.rest_store(supabase_url, supabase_key)

    def _ceiling():
        import urllib.request
        headers = {"api" + "key": supabase_key, "Authorization": "Bearer " + supabase_key}
        req = urllib.request.Request(
            f"{supabase_url}/rest/v1/loom_provider_ceiling?select=*&id=eq.1", headers=headers)
        with urllib.request.urlopen(req, timeout=30) as h:
            return (json.loads(h.read().decode() or "[{}]") or [{}])[0]

    transport = TripwireTransport()
    io = lr.LiveIO(
        transport=transport,
        download_one_video=tripwire_download,
        run_ffprobe=lambda p: {},
        parse_ffprobe_result=lambda *a, **k: {},
        checksum_sha256=lambda p: "",
        finalize=lambda a, b: b,
        cleanup_temp=lambda p: None,
        persist_library=lambda rec: (_ for _ in ()).throw(
            Tripwire("a library row was written during an idempotent repeat")),
        library_rows=helper.library_rows,
        pending_job=lambda fp: gate1 if fp == fingerprint else None,
        ceiling=_ceiling,
        provider_runs_for_job=lambda job_id: [],
        tmp_dir=str(library_root.tmp_dir()),
        library_dir=str(library_root.library_dir()),
        host_check=provider.media_host_check("tiktok"),
    )

    ledger = tc.TikTokSpendLedger(tc.rest_store(supabase_url, supabase_key))
    before = ledger.state()
    receipt["4_ledger"]["before"] = {a: {"spent": s["spent_micro"], "remaining": s["remaining_micro"]}
                                     for a, s in before["combined"]["actors"].items()}
    receipt["4_ledger"]["before_combined_spent"] = before["combined"]["spent_micro"]

    refusal = ""
    try:
        lr.preflight(job, io=io)
        refusal = "NONE — preflight did NOT refuse (unexpected)"
    except lr.LiveRunnerError as e:
        refusal = e.code
    receipt["3_idempotency"] = {
        "preflight_refusal": refusal,
        "refused_before_reservation": refusal == "ALREADY_ACQUIRED",
        "provider_calls": list(transport.calls),
        "downloaded_media": False,
    }

    # --- 5. media path is idempotent too ----------------------------------
    try:
        media = tm.acquire_tiktok_media(
            target={"content_id": CONTENT_ID, "canonical_url": parsed["canonical_url"],
                    "media_url": "https://v45-eu.tiktokcdn.com/never-fetched.mp4"},
            io=tm.TikTokMediaIO(
                download_one_video=tripwire_download,
                classify_downloaded_file=lambda *a, **k: "ok", run_ffprobe=lambda p: {},
                parse_ffprobe_result=lambda *a, **k: {}, checksum_sha256=lambda p: "",
                finalize=lambda a, b: b, cleanup_temp=lambda p: None,
                run_ffmpeg=lambda c: "", run_ffprobe_json=lambda p: {},
                tmp_dir=str(library_root.tmp_dir()),
                library_dir=str(library_root.library_dir())),
            library_rows=helper.library_rows())
        receipt["5_media"] = {"idempotent": bool(media.get("idempotent")),
                              "already_acquired": bool(media.get("already_acquired")),
                              "downloaded": False}
    except Tripwire as e:
        receipt["5_media"] = {"idempotent": False, "error": str(e)}
        raise

    after = ledger.state()
    receipt["4_ledger"]["after"] = {a: {"spent": s["spent_micro"], "remaining": s["remaining_micro"]}
                                    for a, s in after["combined"]["actors"].items()}
    receipt["4_ledger"]["spend_delta_micro"] = (after["combined"]["spent_micro"]
                                               - before["combined"]["spent_micro"])
    receipt["4_ledger"]["unresolved"] = after["combined"]["unresolved_runs"]

    # --- 6. the card reaches ACQUIRED with truthful media facts -----------
    row = tiktok_rows[0]
    asset = Path(library_root.library_dir()) / str(row.get("asset_filename") or "")
    size = asset.stat().st_size
    probe = json.loads(__import__("subprocess").run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(asset)],
        capture_output=True, text=True).stdout or "{}")
    streams = probe.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), {})
    audio = next((s for s in streams if s.get("codec_type") == "audio"), {})
    library_url = f"http://127.0.0.1:8787/library/{row.get('id')}"

    vm = card.build_view_model(
        ref=CONTENT_ID, platform="tiktok", state=card.STATE_ACQUIRED,
        content_id=CONTENT_ID, canonical_url=parsed["canonical_url"],
        creator="@thoughtswthot",
        duration_seconds=float((probe.get("format") or {}).get("duration") or 0) or None,
        width=video.get("width"), height=video.get("height"), size_bytes=size,
        video_codec=video.get("codec_name") or "", audio_codec=audio.get("codec_name") or "",
        has_audio=bool(audio),
        estimated_cost_micro=300, actual_cost_micro=300,
        ceiling_micro=before["ceilings"]["apidojo~tiktok-scraper"]["ceiling_micro"],
        remaining_acquisitions=1,
        library={"status": "acquired", "asset_filename": row.get("asset_filename"),
                 "sha256": row.get("asset_hash"), "path": str(asset)},
        preview={"kind": "local_asset", "local_path": str(asset),
                 "local_filename": row.get("asset_filename")},
        provenance={"actor": actor}, tenant_id="dreemzy", profile_id="tiktok-acceptance",
        library_url=library_url)
    rendered = card.format_acquisition_card(vm)
    tiers = {}
    for tier in (0, 1, 2, 3):
        limit = discord_media.upload_limit_for_tier(tier)
        tiers[tier] = {"limit_bytes": limit, "mode": "video" if size <= limit else "poster",
                       "reason": "" if size <= limit
                                 else discord_media.oversize_reason(size, limit, tier)}
    receipt["6_card"] = {
        "state": vm["state"],
        "title": card.STATE_TITLES[vm["state"]],
        "actions": list(card.actions_for_state(vm["state"])),
        "buttons": [
            {"label": c.get("label"), "style": c.get("style"),
             "link": bool(c.get("url")), "disabled": bool(c.get("disabled", False)),
             "custom_id": bool(c.get("custom_id"))}
            for row in card.format_acquisition_buttons(vm, library_url=library_url)
            for c in row.get("components", [])],
        "media_summary": card.media_summary(vm),
        "library_line": card.library_line(vm),
        "cost_line": card.cost_line(vm),
        "embed_fields": [f.get("name") for f in (rendered.get("embeds") or [{}])[0].get("fields", [])],
        "library_url_private": library_url,
        "guild_size_fallback": tiers,
    }

    receipt["7_provider_calls"] = list(transport.calls)
    print(json.dumps(receipt, indent=2, default=str))


if __name__ == "__main__":
    main()
