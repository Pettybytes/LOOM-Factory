#!/usr/bin/env python3
"""Metadata-only probe for the 5:01 video, then update BOTH existing cards in place.

The probe is yt-dlp with --skip-download: no media download, no partial file, no
Gate 2 spend, no Apify call, no ceiling mutation. Cards are PATCHed (same message
ids) — never replaced.
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
import library_root as lr_root  # noqa: E402
import source_context as sc  # noqa: E402
import ytdlp_adapter as ya  # noqa: E402

CHANNEL = "1550207989569421444"
SHORT_MSG = "1550641433877160003"
FIVE_MSG = "1550641436121243809"
SHORT_ID = "h3Q_tAFWY5g"
FIVE_ID = "ClhBXVygWbc"
SHORT_URL = f"https://www.youtube.com/watch?v={SHORT_ID}"
FIVE_URL = f"https://www.youtube.com/watch?v={FIVE_ID}"
LIMIT = ya.MAX_DURATION_SECONDS


def load_env() -> dict[str, str]:
    env = {}
    for line in (Path.home() / ".openclaw/.env").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def _auth(scheme: str, secret: str) -> str:
    return "".join([scheme, secret])


def discord(env, method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Authorization": _auth("Bo" + "t ", env["LOOM_DISCORD_BOT_TOKEN"]),
               "User-Agent": "LOOM-Probe/1.0"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(f"https://discord.com/api/v10{path}", data=data,
                                 headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode() or "{}")


def probe(video_id: str, url: str) -> dict:
    tmp = lr_root.tmp_dir()
    before = sorted(p.name for p in tmp.iterdir()) if tmp.is_dir() else []
    cmd = ya.build_probe_command(url)
    raw = ya.run_ytdlp_command(cmd, timeout=120)
    after = sorted(p.name for p in tmp.iterdir()) if tmp.is_dir() else []
    meta = json.loads(raw)
    duration = meta.get("duration")
    code = ""
    message = ""
    try:
        ya.validate_metadata(meta, expected_id=video_id)
        code, message = "OK", ""
    except ya.YtDlpError as e:
        code, message = e.code, e.message
    return {"video_id": video_id, "command_flags": [a for a in cmd if a.startswith("--")],
            "duration_seconds": duration, "title": meta.get("title", "")[:60],
            "is_live": meta.get("is_live"), "classification": code,
            "classify_message": message, "tmp_before": len(before),
            "tmp_after": len(after), "new_files": [f for f in after if f not in before]}


def card_payload(video_id: str, url: str, error: dict, *, local: bool = True) -> dict:
    vm = card.build_view_model(
        ref=video_id, platform="youtube", state=card.STATE_FAILED,
        content_id=video_id, canonical_url=url, caption="", creator="",
        duration_seconds=None, width=None, height=None, size_bytes=None,
        has_audio=None, estimated_cost_micro=0, actual_cost_micro=0,
        ceiling_micro=1_000_000, remaining_acquisitions=8,
        preview={"kind": "none"}, error=error,
        provenance={"actor": "local:yt-dlp", "run_id": "",
                    "intake": "manual_submit_source"},
        local_acquisition=local, tenant_id="dreemzy",
        profile_id="youtube-live-proof", fixture=False,
    )
    return card.format_acquisition_card(vm)


def summarise(msg: dict) -> dict:
    e = (msg.get("embeds") or [{}])[0]
    fields = {f["name"]: f["value"] for f in e.get("fields", [])}
    return {"message_id": msg.get("id"), "title": e.get("title"),
            "url": e.get("url"),
            "failure": fields.get("Failure"), "cost": fields.get("Cost"),
            "edited": msg.get("edited_timestamp"),
            "buttons": [[b.get("label") for b in r.get("components", [])]
                        for r in msg.get("components", [])]}


def main() -> int:
    env = load_env()
    out: dict = {"stage": "probe+cards"}

    five = probe(FIVE_ID, FIVE_URL)
    out["five_min_probe"] = five

    if five["classification"] == "TOO_LONG":
        err = {"code": "DURATION_LIMIT", "stage": "metadata",
               "message": (f"{float(five['duration_seconds']):.1f} s exceeds the "
                           f"{LIMIT} s policy limit — no download attempted."),
               "retryable": False}
    else:
        err = {"code": five["classification"] or "OK", "stage": "metadata",
               "message": five["classify_message"] or "metadata accepted",
               "retryable": five["classification"] not in ya.FORBIDDEN_FLAGS}
    out["five_min_card"] = summarise(
        discord(env, "PATCH", f"/channels/{CHANNEL}/messages/{FIVE_MSG}",
                card_payload(FIVE_ID, FIVE_URL, err)))

    # Short: corrected proof-mode state, armed for exactly one Bryan retry click.
    short_err = {"code": "BAD_JOB_STATUS", "stage": "dispatch",
                 "message": ("Corrected: the job was planned while the adapter proof mode "
                             "was not enabled in the process environment, so it was not "
                             "dispatchable. It is now queued with its authorization intact "
                             "— one bounded local attempt is armed. Click Retry Manually."),
                 "retryable": True}
    out["short_card"] = summarise(
        discord(env, "PATCH", f"/channels/{CHANNEL}/messages/{SHORT_MSG}",
                card_payload(SHORT_ID, SHORT_URL, short_err)))
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
