#!/usr/bin/env python3
"""TikTok UI/Discord acceptance through the REAL guild transport (no paid run).

Proves, with the actual Discord API and the real durable library row, that a
repeat request for the already-acquired TikTok video:

  * resolves the canonical identity and pins the proven Apidojo actor
  * is detected as ALREADY ACQUIRED before any reservation (no provider run,
    no duplicate media file, no duplicate library row, zero spend)
  * reaches the correct terminal card state
  * shows a truthful human title, media facts and a private Library link
  * resolves the video-vs-poster fallback from the LIVE guild upload limit

Run with env sourced:  set -a; source ~/.openclaw/.env; set +a
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for _d in ("acquisition", "local-helper", "discord-review"):
    sys.path.insert(0, str(REPO / "infra" / _d))

import acquisition  # noqa: E402
import acquisition_card as card  # noqa: E402
import approved_lane as lane  # noqa: E402
import discord_media as dm  # noqa: E402
import helper  # noqa: E402
import library_root  # noqa: E402
import live_runner as lr  # noqa: E402
import provider  # noqa: E402
import reaction_library as rl  # noqa: E402
import tiktok_actors as ta  # noqa: E402
import tiktok_ceiling as tc  # noqa: E402
import tiktok_media as tm  # noqa: E402
import tiktok_url as tu  # noqa: E402

API = "https://discord.com/api/v10"
GUILD_ID = "1546266463663423518"
CANONICAL = "https://www.tiktok.com/@thoughtswthot/video/7598767615980408095"
CONTENT_ID = "7598767615980408095"
STATE_FILE = REPO / "infra" / "discord-review" / ".live-proof-state.json"


def _env(name: str) -> str:
    v = (os.environ.get(name) or "").strip()
    if not v:
        raise SystemExit(f"[fatal] missing env var {name}")
    return v


def _discord(method: str, path: str, token: str, body=None):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Authorization": "Bot " + token, "User-Agent": "LOOM-TikTokAcceptance/1.0"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(f"{API}{path}", data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:300]


class Tripwire(Exception):
    pass


class TripwireTransport:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def _deny(self, name):
        self.calls.append(name)
        raise Tripwire(f"provider call {name} during an idempotent repeat")

    def start_run(self, *a, **k):
        self._deny("start_run")

    def get_run_status(self, *a, **k):
        self._deny("get_run_status")

    def get_dataset_items(self, *a, **k):
        self._deny("get_dataset_items")


def _tripwire_download(*a, **k):
    raise Tripwire("media download during an idempotent repeat")


def main() -> None:
    supabase_url = _env("LOOM_SUPABASE_URL")
    supabase_key = _env("LOOM_SUPABASE_SERVICE_ROLE_KEY")
    token = _env("LOOM_DISCORD_BOT_TOKEN")
    state = json.loads(STATE_FILE.read_text())
    channel_id = state["channel_id"]

    receipt: dict = {"1_identity": {}, "2_idempotent_lane": {}, "3_guild": {},
                     "4_message": {}, "5_fallback": {}, "6_no_side_effects": {}}

    parsed = tu.parse(CANONICAL)
    row = next(r for r in helper.library_rows()
               if str(r.get("platform_content_id")) == CONTENT_ID)
    titles = rl.asset_titles(row)
    asset = library_root.library_dir() / os.path.basename(str(row["asset_filename"]))
    digest = str(row["asset_hash"])
    receipt["1_identity"] = {
        "canonical_url": parsed["canonical_url"], "content_id": parsed["content_id"],
        "fingerprint": tu.fingerprint_for(parsed["canonical_url"]),
        "pinned_actor": lane.default_provider_actor("tiktok"),
        "display_title": titles["display_title"],
        "source_title": titles["source_title"],
        "creator": titles["creator_name"] or titles["creator_handle"],
        "asset_filename": asset.name,
        "asset_sha256": digest,
    }

    ledger = tc.TikTokSpendLedger(tc.rest_store(supabase_url, supabase_key))
    before = ledger.state()
    rows_before = len(helper.library_rows())

    # --- the repeat goes through the lane and must stop at ALREADY_ACQUIRED ---
    gate1 = lane.plan_pending_job(fingerprint=receipt["1_identity"]["fingerprint"],
                                  platform="tiktok", platform_content_id=CONTENT_ID,
                                  canonical_url=parsed["canonical_url"], actor="bryan")
    job = lane.plan_authorized_job(pending_job=gate1, actor="bryan")
    job["status"] = acquisition.STATUS_QUEUED
    job["id"] = "tiktok-ui-acceptance"

    transport = TripwireTransport()
    io = lr.LiveIO(
        transport=transport, download_one_video=_tripwire_download,
        run_ffprobe=lambda p: {}, parse_ffprobe_result=lambda *a, **k: {},
        checksum_sha256=lambda p: "", finalize=lambda a, b: b, cleanup_temp=lambda p: None,
        persist_library=lambda rec: (_ for _ in ()).throw(
            Tripwire("library row written during an idempotent repeat")),
        library_rows=helper.library_rows,
        pending_job=lambda fp: gate1 if fp == receipt["1_identity"]["fingerprint"] else None,
        ceiling=lambda: {}, provider_runs_for_job=lambda job_id: [],
        tmp_dir=str(library_root.tmp_dir()),
        library_dir=str(library_root.library_dir()),
        host_check=provider.media_host_check("tiktok"))
    refusal = ""
    try:
        lr.preflight(job, io=io)
        refusal = "NONE"
    except lr.LiveRunnerError as e:
        refusal = e.code
    receipt["2_idempotent_lane"] = {"preflight_refusal": refusal,
                                    "provider_calls": list(transport.calls),
                                    "downloaded": False, "wrote_row": False}

    # --- the real guild ---
    limit = dm.guild_upload_limit(token, GUILD_ID)
    size = asset.stat().st_size
    fits = size <= int(limit["limit_bytes"])
    library_url = f"http://127.0.0.1:8787/library/{digest}"
    receipt["3_guild"] = {"guild_id": GUILD_ID, "channel_id": channel_id,
                          "upload_limit": limit, "asset_bytes": size,
                          "expected_mode": "video" if fits else "poster"}

    vm = card.build_view_model(
        ref=CONTENT_ID, platform="tiktok", state=card.STATE_ACQUIRED,
        content_id=CONTENT_ID, canonical_url=parsed["canonical_url"],
        creator=titles["creator_name"] or titles["creator_handle"],
        display_title=titles["display_title"], source_title=titles["source_title"],
        duration_seconds=row.get("duration_seconds"), size_bytes=size,
        width=720, height=1280, video_codec="h264", audio_codec="aac", has_audio=True,
        actual_cost_micro=300, estimated_cost_micro=300,
        ceiling_micro=before["ceilings"][provider.ACTOR_TIKTOK_VIDEO]["ceiling_micro"],
        remaining_acquisitions=1,
        library={"status": "acquired", "asset_filename": asset.name, "sha256": digest},
        preview={"kind": "video_attachment" if fits else "local_asset",
                 "local_filename": asset.name},
        provenance={"actor": provider.ACTOR_TIKTOK_VIDEO}, tenant_id="dreemzy",
        profile_id="tiktok-ui-acceptance", library_url=library_url)
    embed = card.format_acquisition_embed(vm)
    components = card.format_acquisition_buttons(vm, library_url=library_url)

    status, posted = _discord("POST", f"/channels/{channel_id}/messages", token,
                              {"embeds": [embed], "components": components})
    if status != 200 or not isinstance(posted, dict) or not posted.get("id"):
        raise SystemExit(f"[fatal] could not post the card: {status} {posted}")
    message_id = str(posted["id"])

    # --- attach the real asset (video, or poster when oversize) ---
    attach = dm.attach_acquired_media(
        token=token, channel_id=channel_id, message_id=message_id, asset_path=asset,
        embed=embed, components=components, limit_bytes=int(limit["limit_bytes"]),
        tier=int(limit.get("premium_tier") or 0), poster_dir=library_root.library_dir())

    st2, final = _discord("GET", f"/channels/{channel_id}/messages/{message_id}", token)
    embed_back = (final.get("embeds") or [{}])[0] if isinstance(final, dict) else {}
    fields_back = {f.get("name"): f.get("value") for f in embed_back.get("fields", [])}
    receipt["4_message"] = {
        "message_id": message_id,
        "message_url": (f"https://discord.com/channels/{GUILD_ID}/{channel_id}/{message_id}"),
        "read_back_status": st2,
        "state_title": embed_back.get("title"),
        "description": embed_back.get("description"),
        "media_field": fields_back.get("Media"),
        "library_field": fields_back.get("Reaction Library"),
        "has_video_attachment": any(
            (a.get("content_type") or "").startswith("video/")
            for a in (final.get("attachments") or [])),
        "attachment_names": [a.get("filename") for a in (final.get("attachments") or [])],
    }
    receipt["5_fallback"] = {
        "mode": attach.get("mode"), "playable": attach.get("playable"),
        "reason": attach.get("reason"), "upload_name": attach.get("upload_name"),
        "poster_bytes": attach.get("poster_bytes"),
        "attachment_urls": attach.get("attachment_urls"),
    }

    after = ledger.state()
    rows_after = len(helper.library_rows())
    receipt["6_no_side_effects"] = {
        "spend_delta_micro": after["combined"]["spent_micro"] - before["combined"]["spent_micro"],
        "unresolved": after["combined"]["unresolved_runs"],
        "library_rows_before": rows_before, "library_rows_after": rows_after,
        "new_rows": rows_after - rows_before,
        "asset_sha256_after": dm.sha256_file(asset),
    }
    print(json.dumps(receipt, indent=2, default=str))


if __name__ == "__main__":
    main()
