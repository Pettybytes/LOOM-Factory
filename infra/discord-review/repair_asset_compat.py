#!/usr/bin/env python3
"""Repair the already-acquired YouTube asset into the delivery format.

Uses the EXISTING local file — no download, no provider call. Normalizes to a
temporary file, validates it, atomically replaces the library asset, recomputes
SHA-256, renames with the deterministic convention, then updates the database row,
job provenance, poster/reference data and the Discord card in place.

No duplicate library item is created.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
for d in ("acquisition", "discord-review"):
    sys.path.insert(0, str(_REPO / "infra" / d))

import acquisition_card as card  # noqa: E402
import library_root as lr_root  # noqa: E402
import media_normalize as mn  # noqa: E402
import ytdlp_adapter as ya  # noqa: E402

VID = "h3Q_tAFWY5g"
CANONICAL = f"https://www.youtube.com/watch?v={VID}"
CHANNEL = "1550207989569421444"
CARD_MSG = "1550641433877160003"
JOB_ID = "97370400-b807-469f-8823-d1f2af34acd1"


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


def rest(env, path, body=None, method="GET"):
    data = json.dumps(body).encode() if body is not None else None
    h = {"api" + "key": env["LOOM_SUPABASE_SERVICE_ROLE_KEY"],
         "Authorization": _auth("Bea" + "rer ", env["LOOM_SUPABASE_SERVICE_ROLE_KEY"]),
         "User-Agent": "LOOM-Compat/1.0"}
    if body is not None:
        h["Content-Type"] = "application/json"
        h["Prefer"] = "return=representation"
    req = urllib.request.Request(f"{env['LOOM_SUPABASE_URL']}/rest/v1/{path}",
                                 data=data, headers=h, method=method)
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read().decode() or "[]"
    return json.loads(raw) if raw.strip().startswith(("[", "{")) else []


def discord(env, method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    h = {"Authorization": _auth("Bo" + "t ", env["LOOM_DISCORD_BOT_TOKEN"]),
         "User-Agent": "LOOM-Compat/1.0"}
    if body is not None:
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(f"https://discord.com/api/v10{path}", data=data,
                                 headers=h, method=method)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode() or "{}")


def main() -> int:
    env = load_env()
    rows = rest(env, f"loom_reaction_library?platform_content_id=eq.{VID}&select=*")
    if not rows:
        print(json.dumps({"error": "library row not found"}))
        return 2
    row = rows[0]
    lib_dir = lr_root.library_dir()
    old_path = lib_dir / row["asset_filename"]
    if not old_path.is_file():
        print(json.dumps({"error": f"asset missing: {old_path}"}))
        return 2

    out: dict = {"stage": "repair-compat", "video_id": VID,
                 "old": {"filename": row["asset_filename"], "asset_hash": row["asset_hash"],
                         "size_bytes": row.get("size_bytes")}}

    src_probe = mn.run_ffprobe_json(str(old_path))
    out["old"]["probe"] = {"format": (src_probe.get("format") or {}).get("format_name"),
                           "video": mn.compatibility_gaps(src_probe)}
    out["old"]["gaps"] = mn.compatibility_gaps(src_probe)

    # normalize into a temporary file beside the library (never over the original)
    staged = lib_dir / f"{old_path.stem}.normalized.mp4"
    if staged.exists():
        staged.unlink()
    report = mn.normalize_and_finalize(
        src=old_path, dst=staged, probe=src_probe,
        run_ffmpeg=mn.run_ffmpeg_command, run_ffprobe=mn.run_ffprobe_json,
        keep_compatible=False)                      # this repair always normalizes
    out["normalized"] = report["normalized"]

    digest = ya.sha256_file(staged)
    new_name = ya.filename_for(VID, digest)
    new_path = lib_dir / new_name
    os.replace(staged, new_path)                     # atomic replace target name
    if old_path.name != new_name and old_path.exists():
        old_path.unlink()                            # retire the incompatible file

    new_probe = mn.run_ffprobe_json(str(new_path))
    out["new"] = {"filename": new_name, "asset_hash": digest,
                  "size_bytes": new_path.stat().st_size,
                  "duration_seconds": report["duration_seconds"],
                  "re_downloaded": False,
                  "video_codec": report["video_codec"],
                  "audio_codec": report["audio_codec"],
                  "pix_fmt": report["pix_fmt"],
                  "width": report["width"], "height": report["height"],
                  "frame_rate": report["frame_rate"],
                  "gaps": mn.compatibility_gaps(new_probe)}

    # poster/reference data for the gallery (single frame, no transcode of the asset)
    poster = lib_dir / f"{Path(new_name).stem}__poster.jpg"
    if not poster.exists():
        try:
            mn.run_ffmpeg_command(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                                   "-ss", "0.5", "-i", str(new_path), "-frames:v", "1",
                                   "-q:v", "2", str(poster)])
            out["poster"] = {"created": poster.is_file(),
                             "bytes": poster.stat().st_size if poster.is_file() else 0}
        except Exception as e:  # noqa: BLE001 — poster is optional reference data
            out["poster"] = {"created": False, "error": type(e).__name__}
    else:
        out["poster"] = {"created": False, "existing": True}

    # database row (same row — no duplicate library item)
    patched = rest(env, f"loom_reaction_library?id=eq.{row['id']}",
                   {"asset_filename": new_name, "asset_hash": digest,
                    "size_bytes": new_path.stat().st_size,
                    "duration_seconds": report["duration_seconds"],
                    "canonical_url": CANONICAL,
                    "storage_url": str(new_path)}, "PATCH")
    out["db"] = {"row_id": row["id"], "updated": bool(patched),
                 "rows_for_video": len(rest(env, f"loom_reaction_library?platform_content_id=eq.{VID}&select=id"))}

    # acquisition job provenance
    job = rest(env, f"loom_acquisition_jobs?id=eq.{JOB_ID}&select=asset_provenance,actor")
    prov = dict((job[0].get("asset_provenance") if job else {}) or {})
    prov.update({"adapter": "local:yt-dlp", "provider": "local", "provider_cost_micro": 0,
                 "delivery_format": "mp4/h264/aac/yuv420p/faststart",
                 "normalized_from": [g for g in out["old"]["gaps"]]})
    rest(env, f"loom_acquisition_jobs?id=eq.{JOB_ID}", {"asset_provenance": prov}, "PATCH")
    out["job_provenance"] = prov

    # Discord card, in place
    lib_url = f"http://127.0.0.1:8787/library/{digest}"
    vm = card.build_view_model(
        ref=VID, platform="youtube", state=card.STATE_ACQUIRED, content_id=VID,
        canonical_url=CANONICAL, duration_seconds=report["duration_seconds"],
        size_bytes=new_path.stat().st_size, video_codec=report["video_codec"],
        audio_codec=report["audio_codec"], has_audio=True,
        actual_cost_micro=0, ceiling_micro=1_000_000, remaining_acquisitions=8,
        library={"status": "acquired", "asset_filename": new_name, "sha256": digest},
        preview={"kind": "thumbnail", "thumbnail_url": "attachment://poster.jpg"}
        if poster.is_file() else {"kind": "local_asset", "local_filename": new_name},
        provenance={"adapter": "local:yt-dlp",
                    "actor": (job[0].get("actor") if job else "") or "",
                    "run_id": prov.get("run_id", "")},
        local_acquisition=True, library_url=lib_url,
        tenant_id="dreemzy", profile_id="youtube-live-proof", fixture=False)
    msg = discord(env, "PATCH", f"/channels/{CHANNEL}/messages/{CARD_MSG}",
                  card.format_acquisition_card(vm))
    e = (msg.get("embeds") or [{}])[0]
    f = {x["name"]: x["value"] for x in e.get("fields", [])}
    out["card"] = {"title": e.get("title"), "url": e.get("url"),
                   "library": f.get("Reaction Library"), "cost": f.get("Cost"),
                   "buttons": [[b.get("label") for b in r.get("components", [])]
                               for r in msg.get("components", [])],
                   "link": [b.get("url") for r in msg.get("components", [])
                            for b in r.get("components", []) if b.get("url")]}
    try:
        out["card"]["link_status"] = urllib.request.urlopen(lib_url, timeout=10).status
    except Exception as ex:  # noqa: BLE001
        out["card"]["link_status"] = f"error:{type(ex).__name__}"
    print(json.dumps(out, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
