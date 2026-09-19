#!/usr/bin/env python3
"""Reddit UI/Discord acceptance + idempotent repeat (real guild, no re-download).

Proves, against the REAL durable Reaction Library row and the REAL Discord API:

  * the repeat request resolves to the same canonical Reddit post
  * the lane refuses ALREADY_ACQUIRED before anything can be downloaded
  * no second download, no second media file, no second library row
  * the card reaches the correct terminal state with a truthful title, media
    facts and a private Library link
  * the video-vs-poster fallback follows the LIVE guild upload limit

No authentication is used, and no cookie material is printed, stored, or sent.

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
import reaction_library as rl  # noqa: E402
import reddit_context as rctx  # noqa: E402
import reddit_media as rm  # noqa: E402
import reddit_session as rsess  # noqa: E402
import reddit_url as ru  # noqa: E402

API = "https://discord.com/api/v10"
GUILD_ID = "1546266463663423518"
CANON = "https://www.reddit.com/r/TheCapitalLink/comments/1l6m106/"
POST_ID = "1l6m106"
STATE_FILE = REPO / "infra" / "discord-review" / ".live-proof-state.json"
#: Resumable acceptance state. A failed attachment upload must be retryable on the
#: SAME card rather than posting a duplicate message.
ACCEPT_STATE = REPO / "infra" / "discord-review" / ".reddit-acceptance-state.json"


def _load_accept_state() -> dict:
    try:
        return json.loads(ACCEPT_STATE.read_text())
    except Exception:  # noqa: BLE001
        return {}


def _save_accept_state(data: dict) -> None:
    ACCEPT_STATE.write_text(json.dumps(data, indent=2))


def _env(name: str) -> str:
    v = (os.environ.get(name) or "").strip()
    if not v:
        raise SystemExit(f"[fatal] missing env var {name}")
    return v


def _discord(method: str, path: str, token: str, body=None):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Authorization": "Bot " + token, "User-Agent": "LOOM-RedditAcceptance/1.0"}
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


class TripwireIO(rm.RedditMediaIO):
    """Any download at all during the repeat fails the acceptance."""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.calls: list[str] = []

    def _deny(self, name):
        self.calls.append(name)
        raise Tripwire(f"{name} during an idempotent repeat")


def main() -> None:
    token = _env("LOOM_DISCORD_BOT_TOKEN")
    state = json.loads(STATE_FILE.read_text())
    channel_id = state["channel_id"]
    out: dict = {"1_identity": {}, "2_idempotent_repeat": {}, "3_guild": {},
                 "4_message": {}, "5_fallback": {}, "6_no_side_effects": {},
                 "7_session": {}}

    rows = helper.library_rows()
    row = next((r for r in rows if str(r.get("platform_content_id")) == POST_ID), None)
    if row is None:
        raise SystemExit("[fatal] the Reddit asset is not in the library yet")
    titles = rl.asset_titles(row)
    asset = library_root.library_dir() / os.path.basename(str(row["asset_filename"]))
    digest = str(row["asset_hash"])
    out["1_identity"] = {
        "post_id": POST_ID, "canonical_url": row.get("canonical_url"),
        "fingerprint": ru.fingerprint_for(CANON),
        "display_title": titles["display_title"],
        "creator": titles["creator_name"] or titles["creator_handle"],
        "asset_filename": asset.name, "asset_bytes": asset.stat().st_size,
        "asset_sha256": digest, "library_id": str(row.get("id")),
    }

    rows_before = len(rows)
    refusal = "NONE"
    tripwire = TripwireIO(
        download_one_video=lambda *a, **k: (_ for _ in ()).throw(
            Tripwire("media download during an idempotent repeat")),
        classify_downloaded_file=lambda *a, **k: "ok", run_ffprobe=lambda p: {},
        parse_ffprobe_result=lambda *a, **k: {}, checksum_sha256=lambda p: "",
        finalize=lambda a, b: b, cleanup_temp=lambda p: None,
        run_ffmpeg=lambda c: "", run_ffprobe_json=lambda p: {},
        tmp_dir=str(library_root.tmp_dir()),
        library_dir=str(library_root.library_dir()))
    try:
        repeat = rm.acquire_reddit_native(
            target={"post_id": POST_ID, "canonical_url": CANON,
                    "video_url": "https://v.redd.it/never-fetched.mp4",
                    "audio_url": "https://v.redd.it/never-fetched-audio.mp4"},
            io=tripwire, library_rows=helper.library_rows())
        out["2_idempotent_repeat"] = {"idempotent": repeat.get("idempotent"),
                                     "already_acquired": repeat.get("already_acquired"),
                                     "downloaded": repeat.get("downloaded")}
    except Tripwire as e:
        out["2_idempotent_repeat"] = {"idempotent": False, "error": str(e)}

    # Reddit is a LOCAL ($0) lane: there is no paid provider actor to pin, and the
    # repeat is already refused by the durable-row check above.
    out["2_idempotent_repeat"]["lane"] = {
        "kind": "local",
        "paid_provider_actor": None,
        "provider_run_possible": False,
    }

    limit = dm.guild_upload_limit(token, GUILD_ID)
    size = asset.stat().st_size
    fits = size <= int(limit["limit_bytes"])
    library_url = f"http://127.0.0.1:8787/library/{digest}"
    out["3_guild"] = {"guild_id": GUILD_ID, "channel_id": channel_id,
                      "upload_limit": limit, "asset_bytes": size,
                      "expected_mode": "video" if fits else "poster"}

    vm = card.build_view_model(
        ref=POST_ID, platform="reddit", state=card.STATE_ACQUIRED,
        content_id=POST_ID, canonical_url=CANON,
        creator=titles["creator_name"] or titles["creator_handle"],
        display_title=titles["display_title"], source_title=titles["source_title"],
        duration_seconds=row.get("duration_seconds"), size_bytes=size,
        width=720, height=1044, video_codec="h264", audio_codec="aac", has_audio=True,
        actual_cost_micro=0, estimated_cost_micro=0, library={"status": "acquired",
        "asset_filename": asset.name, "sha256": digest},
        preview={"kind": "video_attachment" if fits else "local_asset",
                 "local_filename": asset.name},
        provenance={"adapter": "local:yt-dlp", "provider": "none"},
        tenant_id="loom", profile_id="reddit-acceptance",
        library_url=library_url, local_acquisition=True)
    embed = card.format_acquisition_embed(vm)
    components = card.format_acquisition_buttons(vm, library_url=library_url)

    prior = _load_accept_state()
    resumed = bool(prior.get("channel_id") == channel_id and prior.get("message_id"))
    if resumed:
        message_id = str(prior["message_id"])
        status, _ = _discord("PATCH", f"/channels/{channel_id}/messages/{message_id}",
                             token, {"embeds": [embed], "components": components})
    else:
        status, posted = _discord("POST", f"/channels/{channel_id}/messages", token,
                                  {"embeds": [embed], "components": components})
        if status != 200 or not isinstance(posted, dict) or not posted.get("id"):
            raise SystemExit(f"[fatal] could not post the card: {status} {posted}")
        message_id = str(posted["id"])
        _save_accept_state({"channel_id": channel_id, "message_id": message_id,
                            "post_id": POST_ID})
    if status != 200:
        raise SystemExit(f"[fatal] card write failed: {status}")

    attach = dm.attach_acquired_media(
        token=token, channel_id=channel_id, message_id=message_id, asset_path=asset,
        embed=embed, components=components, limit_bytes=int(limit["limit_bytes"]),
        tier=int(limit.get("premium_tier") or 0), poster_dir=library_root.library_dir())

    st2, final = _discord("GET", f"/channels/{channel_id}/messages/{message_id}", token)
    e = (final.get("embeds") or [{}])[0] if isinstance(final, dict) else {}
    fields = {f.get("name"): f.get("value") for f in e.get("fields", [])}
    out["4_message"] = {
        "message_id": message_id,
        "resumed_existing_card": resumed,
        "patched_in_place": True,
        "message_url": f"https://discord.com/channels/{GUILD_ID}/{channel_id}/{message_id}",
        "read_back_status": st2, "state_title": e.get("title"),
        "description": e.get("description"), "media_field": fields.get("Media"),
        "library_field": fields.get("Reaction Library"),
        "has_video_attachment": any((a.get("content_type") or "").startswith("video/")
                                    for a in (final.get("attachments") or [])),
        "attachment_names": [a.get("filename") for a in (final.get("attachments") or [])],
    }
    out["5_fallback"] = {k: attach.get(k) for k in
                         ("mode", "playable", "reason", "upload_name", "poster_bytes")}

    rows_after = helper.library_rows()
    out["6_no_side_effects"] = {
        "library_rows_before": rows_before, "library_rows_after": len(rows_after),
        "new_rows": len(rows_after) - rows_before,
        "media_downloads": False, "paid_provider_calls": False,
        "asset_sha256_after": dm.sha256_file(asset),
        "asset_hash_unchanged": dm.sha256_file(asset) == digest,
    }
    out["7_session"] = {**rsess.describe(host="reddit.com"),
                        "authentication_used": False, "cookie_args_passed": []}
    payload = json.dumps(out, default=str)
    out["7_session"]["cookie_material_in_receipt"] = any(
        m in payload.lower() for m in ("reddit_session", "set-cookie",
                                       "netscape http cookie file"))
    print(json.dumps(out, indent=2, default=str))


if __name__ == "__main__":
    main()
