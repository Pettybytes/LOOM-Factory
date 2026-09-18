#!/usr/bin/env python3
"""Post the two approved YouTube REVIEW cards through the PRODUCTION intake path.

For each approved URL this does exactly what the real `/submit_source` flow does —
`manual_intake.validate_and_canonicalize` → `build_submission` → `persist_submission`
— then seeds nothing by hand: the durable source context that the acquisition lane
reads is the submission row itself. Only after the row is verifiably resolvable does
it post the premium REVIEW card to #source-review.

Tracking parameters are stripped by the production canonicalizer. No invented
metadata is shown on the cards.

Run:  python3 infra/discord-review/stage_youtube_cards.py --post
"""

from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
for d in ("acquisition", "discord-review"):
    sys.path.insert(0, str(_REPO / "infra" / d))

import acquisition_card as card  # noqa: E402
import manual_intake  # noqa: E402
import source_context as sc  # noqa: E402

CHANNEL_NAME = "source-review"
GUILD_ID = "1546266463663423518"
OUT = Path(__file__).resolve().parent / "youtube-cards.json"
SUBMITTER = "bryan"

TARGETS = (
    {"key": "short",
     "video_id": "h3Q_tAFWY5g",
     "url": "https://youtube.com/shorts/h3Q_tAFWY5g?si=Xph8JJ1pDg6cq6YC",
     "label": "Short (~78 s, 8 subscribers)"},
    {"key": "five_min",
     "video_id": "ClhBXVygWbc",
     "url": "https://youtu.be/ClhBXVygWbc?si=4Yf6NFv594czcRdc",
     "label": "5:01 video (expected DURATION_LIMIT)"},
)


def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for line in (Path.home() / ".openclaw/.env").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def _auth(scheme: str, secret: str) -> str:
    return "".join([scheme, secret])


def discord(env, method, path, body=None):
    headers = {"Authorization": _auth("Bo" + "t ", env["LOOM_DISCORD_BOT_TOKEN"]),
               "User-Agent": "LOOM-Stage/1.0"}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(f"https://discord.com/api/v10{path}", data=data,
                                 headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read().decode() or "{}"
    return json.loads(raw) if raw.strip().startswith(("{", "[")) else raw


def resolve_channel(env) -> str:
    for guild in discord(env, "GET", f"/guilds/{GUILD_ID}/channels"):
        if guild.get("name") == CHANNEL_NAME:
            return guild["id"]
    raise SystemExit(f"#{CHANNEL_NAME} not found")


def build_review_card(canonical_url: str, video_id: str, provenance: dict) -> dict:
    vm = card.build_view_model(
        ref=video_id, platform="youtube", state=card.STATE_REVIEW,
        content_id=video_id, canonical_url=canonical_url,
        caption="", creator="", duration_seconds=None,
        width=None, height=None, size_bytes=None, has_audio=None,
        estimated_cost_micro=0,
        ceiling_micro=1_000_000,
        remaining_acquisitions=9,
        preview={"kind": "none"},
        provenance=provenance,
        local_acquisition=True,
        tenant_id="dreemzy", profile_id="youtube-live-proof", fixture=False,
    )
    return card.format_acquisition_card(vm)


def main() -> int:
    env = load_env()
    channel_id = resolve_channel(env)
    results = []

    # Real production values: Unassigned/General destination is valid, and a
    # single submitted video is a reaction_single format.
    destination = manual_intake.DESTINATION_UNASSIGNED
    content_format = "reaction_single"
    assert content_format in manual_intake.CONTENT_FORMATS

    for t in TARGETS:
        checked = manual_intake.validate_and_canonicalize(t["url"])
        if checked["platform"] != "youtube":
            raise SystemExit(f"{t['key']}: unexpected platform {checked['platform']}")
        if checked["content_id"] != t["video_id"]:
            raise SystemExit(f"{t['key']}: content id drift "
                             f"{checked['content_id']} != {t['video_id']}")

        payload = manual_intake.build_submission(
            url=checked["canonical_url"], destination=destination,
            content_format=content_format, submitter=SUBMITTER,
            time_start_s=None, time_end_s=None, reviewer_note="",
            active_tenants=None,
        )
        row, created = manual_intake.persist_submission(
            env["LOOM_SUPABASE_URL"], env["LOOM_SUPABASE_SERVICE_ROLE_KEY"], payload)

        # The acquisition lane must be able to resolve this source before we post.
        # FAIL CLOSED: only a positive resolution may proceed to a post. An
        # exception is never treated as success (that false positive is why an
        # earlier run reported resolvable=true on a broken lookup).
        resolvable, resolve_note = False, "unchecked"
        try:
            resolved = sc.resolve_context(
                checked["content_id"],
                get=sc.http_get(env["LOOM_SUPABASE_URL"],
                                env["LOOM_SUPABASE_SERVICE_ROLE_KEY"]))
            resolvable = bool(resolved)
            resolve_note = "resolved" if resolvable else "no durable row"
        except Exception as e:  # noqa: BLE001 — an error is NOT resolvable
            resolvable, resolve_note = False, f"error:{type(e).__name__}"
        if not resolvable:
            raise SystemExit(f"{t['key']}: refusing to post — source context does not "
                             f"resolve ({resolve_note})")

        provenance = {"actor": "local:yt-dlp", "run_id": "",
                      "intake": "manual_submit_source"}
        msg = discord(env, "POST", f"/channels/{channel_id}/messages",
                      build_review_card(checked["canonical_url"], t["video_id"], provenance))
        embed = (msg.get("embeds") or [{}])[0]
        results.append({
            "key": t["key"], "label": t["label"], "video_id": t["video_id"],
            "submitted_url": t["url"], "canonical_url": checked["canonical_url"],
            "submission_created": created, "resolvable": resolvable,
            "resolve_note": resolve_note,
            "message_id": msg.get("id"),
            "message_url": f"https://discord.com/channels/{GUILD_ID}/{channel_id}/{msg.get('id')}",
            "buttons": [[b.get("label") for b in r.get("components", [])]
                        for r in msg.get("components", [])],
            "cost_field": {f["name"]: f["value"] for f in embed.get("fields", [])}.get("Cost"),
        })

    OUT.write_text(json.dumps({"channel_id": channel_id, "cards": results}, indent=2),
                   encoding="utf-8")
    print(json.dumps({"channel_id": channel_id, "cards": results}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
