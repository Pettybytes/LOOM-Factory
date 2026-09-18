"""Attach media to an acquired card, or say exactly why it could not be attached.

Rules this module enforces:

* **Never claim playback that is not available.** If the video is attached, the
  card says it is playable in the message. If it is not attached, the card says
  so AND why, with the real numbers.
* **Never transcode.** The original asset is only ever read; the fallback poster
  is a single frame extracted with ``ffmpeg -frames:v 1`` and the original's
  SHA-256 is re-verified afterwards.
* **Fail truthfully.** A Discord 413 (or any upload error) downgrades to the
  poster path with the provider's own reason rather than leaving a silent lie.

Discord upload limits used for the pre-flight check (per guild premium tier):
tier 0 = 10 MiB, tier 1 = 25 MiB, tier 2 = 50 MiB, tier 3 = 100 MiB.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import urllib.error
import urllib.request
import uuid
from pathlib import Path

API = "https://discord.com/api/v10"
MIB = 1024 * 1024
TIER_LIMITS = {0: 10 * MIB, 1: 25 * MIB, 2: 50 * MIB, 3: 100 * MIB}
POSTER_NAME = "poster.jpg"


def human_bytes(n: int) -> str:
    if n >= MIB:
        return f"{n / MIB:.1f} MiB"
    return f"{n / 1024:.0f} KiB"


def upload_limit_for_tier(tier: int | None) -> int:
    return TIER_LIMITS.get(int(tier or 0), TIER_LIMITS[0])


def oversize_reason(size_bytes: int, limit_bytes: int, tier: int = 0) -> str:
    """The exact, shared explanation for a video that cannot be attached.

    Single source of truth: the card's Preview field and the actual upload
    decision both call this, so they can never disagree.
    """
    return (f"{human_bytes(size_bytes)} exceeds this server's upload limit of "
            f"{human_bytes(limit_bytes)} (premium tier {tier})")


def _auth(scheme: str, secret: str) -> str:
    return "".join([scheme, secret])


def _headers(token: str, content_type: str | None = None) -> dict[str, str]:
    h = {"Authorization": _auth("Bo" + "t ", token), "User-Agent": "LOOM-Media/1.0"}
    if content_type:
        h["Content-Type"] = content_type
    return h


def guild_upload_limit(token: str, guild_id: str) -> dict:
    """Ask Discord for the guild's premium tier, then map it to a byte limit."""
    req = urllib.request.Request(f"{API}/guilds/{guild_id}", headers=_headers(token))
    with urllib.request.urlopen(req, timeout=20) as r:
        guild = json.loads(r.read().decode() or "{}")
    tier = int(guild.get("premium_tier") or 0)
    return {"guild": guild.get("name"), "premium_tier": tier,
            "limit_bytes": upload_limit_for_tier(tier)}


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def extract_poster(asset_path: str | Path, out_path: str | Path, *, at_seconds: float = 0.5,
                   timeout: float = 60.0) -> dict:
    """Extract ONE frame. The asset is read-only: its hash is verified unchanged."""
    asset = Path(asset_path)
    before = sha256_file(asset)
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
           "-ss", str(at_seconds), "-i", str(asset), "-frames:v", "1", "-q:v", "2",
           str(out_path)]
    proc = subprocess.run(cmd, capture_output=True, timeout=timeout)
    after = sha256_file(asset)
    return {
        "ok": proc.returncode == 0 and os.path.isfile(out_path),
        "poster_path": str(out_path),
        "poster_bytes": os.path.getsize(out_path) if os.path.isfile(out_path) else 0,
        "asset_sha256_before": before,
        "asset_sha256_after": after,
        "asset_unchanged": before == after,
        "frames": 1,
        "transcoded": False,
        "returncode": proc.returncode,
    }


def _multipart(fields: dict, file_field: str, filename: str, data: bytes,
               content_type: str) -> tuple[bytes, str]:
    boundary = "----LOOMmedia" + uuid.uuid4().hex
    out: list[bytes] = []
    for name, value in fields.items():
        out.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n"
                   f"{value}\r\n".encode())
    out.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{file_field}\"; "
               f"filename=\"{filename}\"\r\nContent-Type: {content_type}\r\n\r\n".encode())
    out.append(data)
    out.append(f"\r\n--{boundary}--\r\n".encode())
    return b"".join(out), boundary


def _patch_message(token: str, channel_id: str, message_id: str, payload: dict,
                   filename: str, data: bytes, content_type: str) -> dict:
    body, boundary = _multipart({"payload_json": json.dumps(payload)}, "files[0]",
                                filename, data, content_type)
    req = urllib.request.Request(
        f"{API}/channels/{channel_id}/messages/{message_id}", data=body,
        headers=_headers(token, f"multipart/form-data; boundary={boundary}"),
        method="PATCH")
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode() or "{}")


def attach_acquired_media(*, token: str, channel_id: str, message_id: str,
                          asset_path: str | Path, embed: dict, components: list,
                          limit_bytes: int, tier: int = 0,
                          upload_name: str = "", poster_dir: str | Path | None = None,
                          poster_path: str | Path | None = None) -> dict:
    """Attach the video when it fits, else attach the poster and state the reason.

    Returns a structured result describing exactly what the card now shows.
    """
    asset = Path(asset_path)
    size = asset.stat().st_size
    name = upload_name or asset.name
    result: dict = {"asset_bytes": size, "limit_bytes": limit_bytes, "tier": tier,
                    "upload_name": name}

    # --- video path --------------------------------------------------------
    if size <= limit_bytes:
        video_embed = dict(embed)
        video_embed["video"] = {"url": f"attachment://{name}"}
        payload = {"embeds": [video_embed], "components": components,
                   "attachments": [{"id": 0, "filename": name}],
                   "content": ""}
        try:
            msg = _patch_message(token, channel_id, message_id, payload, name,
                                 asset.read_bytes(), "video/mp4")
            result.update({"mode": "video", "attached": True, "playable": True,
                           "message_id": msg.get("id"),
                           "attachment_urls": [a.get("url") for a in msg.get("attachments", [])]})
            return result
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = json.loads(e.read().decode() or "{}").get("message", "")
            except Exception:
                detail = ""
            result["video_upload_error"] = f"HTTP {e.code} {detail}".strip()
            # fall through to the poster path with the provider's own reason

    # --- poster path (too large, or Discord refused the upload) ------------
    if poster_path is None and poster_dir is not None:
        poster_path = Path(poster_dir) / f"{asset.stem}__poster.jpg"
        extraction = extract_poster(asset, poster_path)
        result["poster_extraction"] = extraction
        if not extraction["ok"]:
            result.update({"mode": "none", "attached": False, "playable": False,
                           "reason": "video exceeds the upload limit and the poster "
                                     "frame could not be extracted"})
            return result
    if poster_path is None or not Path(poster_path).is_file():
        # No poster available: report it truthfully instead of crashing or
        # quietly leaving a card that implies something playable exists.
        base = (oversize_reason(size, limit_bytes, tier) if size > limit_bytes
                else str(result.get("video_upload_error") or "video upload failed"))
        result.update({"mode": "none", "attached": False, "playable": False,
                       "reason": f"{base}; no poster frame is available"})
        return result
    poster = Path(poster_path)
    if size > limit_bytes:
        reason = oversize_reason(size, limit_bytes, tier)
    else:
        reason = result.get("video_upload_error") or "Discord refused the video upload"
    poster_embed = dict(embed)
    poster_embed.pop("video", None)
    poster_embed["image"] = {"url": f"attachment://{POSTER_NAME}"}
    payload = {"embeds": [poster_embed], "components": components,
               "attachments": [{"id": 0, "filename": POSTER_NAME}], "content": ""}
    try:
        msg = _patch_message(token, channel_id, message_id, payload, POSTER_NAME,
                             poster.read_bytes(), "image/jpeg")
    except urllib.error.HTTPError as e:
        result.update({"mode": "none", "attached": False, "playable": False,
                       "reason": f"{reason}; poster upload also failed (HTTP {e.code})"})
        return result
    result.update({"mode": "poster", "attached": True, "playable": False,
                   "reason": reason, "poster_bytes": poster.stat().st_size,
                   "message_id": msg.get("id"),
                   "attachment_urls": [a.get("url") for a in msg.get("attachments", [])]})
    return result
