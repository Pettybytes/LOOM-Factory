#!/usr/bin/env python3
"""Controlled **Gate 1 only** runtime proof for the approved acquisition lane.

What this does (and nothing more):
  1. Posts exactly ONE premium Instagram acquisition card to the designated
     LOOM proof/review Discord channel (channel named ``source-review``),
     using the retained proof record for reel ``DdZS1QRhrXE`` plus its real
     extracted poster frame.
  2. Drives ONE Approve interaction through the REAL handler
     (``acquisition_ops.handle``) with the durable Supabase store, measuring the
     acknowledgement and the update-in-place.
  3. Re-drives Approve (inside and outside the duplicate window) to prove a
     repeat click cannot create a second pending job.
  4. Verifies zero spend: no provider run rows, no cost reservation, unchanged
     ceiling counters, no new downloads, and an unchanged asset sha256.

Honest limits of this harness (documented in the report):
  * Discord's Ed25519 signature cannot be reproduced locally, so the interaction
    is delivered straight into the handler rather than over the interactions
    endpoint. Every other step is the production path.
  * The card is updated in place with the BOT token
    (``PATCH /channels/{id}/messages/{id}``) instead of the interaction-token
    webhook route; the visible Discord outcome is the same.

Not authorized / never performed here: Acquire for Reaction, the live runner,
any Apify call, any download, any migration, any commit or push.

Run:  python3 infra/discord-review/prove_gate1_lane.py
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import library_root  # noqa: E402

_REPO = Path(__file__).resolve().parents[2]
for d in ("acquisition", "discord-review"):
    sys.path.insert(0, str(_REPO / "infra" / d))

import acquisition_card as card  # noqa: E402
import acquisition_ops as ops  # noqa: E402
import approved_lane as lane  # noqa: E402

API = "https://discord.com/api/v10"
GUILD_ID = "1546266463663423518"
CHANNEL_NAME = "source-review"
REVIEWER = "gate1-runtime-proof"          # honest attribution: driven by this harness
CID = card.ACQUIRED_REEL_FIXTURE["ref"]
POSTER = (_REPO / "infra" / "acquisition" / "card-previews" / "posters"
          / "instagram__DdZS1QRhrXE__poster.jpg")
ASSET = (library_root.library_dir()
         / "instagram__DdZS1QRhrXE__9b22f887b2ff.mp4")
STATE_FILE = Path(__file__).resolve().parent / ".gate1-proof-state.json"
REPORT_FILE = Path(__file__).resolve().parent / "gate1-proof-result.json"

PROOF_PROFILE = "acquisition-card-proof"   # non-production marker
POPPED_ATTACHMENT = "attachment://poster.jpg"


# ---------------------------------------------------------------------------
# env + Discord I/O
# ---------------------------------------------------------------------------
def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    p = Path.home() / ".openclaw/.env"
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


class DiscordError(RuntimeError):
    pass


def _request(method: str, path: str, token: str, *, body: bytes | None = None,
             headers: dict[str, str] | None = None) -> tuple[int, object]:
    h = {"Authorization": f"Bot {token}", "User-Agent": "LOOM-Gate1Proof/1.0"}
    if body is not None and not headers:
        h["Content-Type"] = "application/json"
    if headers:
        h.update(headers)
    req = urllib.request.Request(f"{API}{path}", data=body, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read().decode() or "{}"
            return r.status, (json.loads(raw) if raw.strip().startswith(("{", "[")) else raw)
    except urllib.error.HTTPError as e:
        raise DiscordError(f"HTTP {e.code}: {e.read().decode()[:300]}") from e


def resolve_channel(token: str) -> str:
    _st, guilds = _request("GET", "/users/@me/guilds", token)
    for g in guilds if isinstance(guilds, list) else []:
        if str(g.get("id")) != GUILD_ID:
            continue
        _st2, chans = _request("GET", f"/guilds/{GUILD_ID}/channels", token)
        for c in chans if isinstance(chans, list) else []:
            if c.get("name") == CHANNEL_NAME and c.get("type") in (0, 5):
                return str(c["id"])
    raise DiscordError(f"channel #{CHANNEL_NAME} not found in guild {GUILD_ID}")


def post_card_with_poster(token: str, channel_id: str, payload: dict) -> dict:
    """Multipart post so the card can reference the real poster attachment."""
    boundary = "----LOOMgate1" + uuid.uuid4().hex
    parts: list[bytes] = []

    def add_field(name: str, value: str) -> None:
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n"
                     f"{value}\r\n".encode())

    add_field("payload_json", json.dumps(payload))
    parts.append(
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"files[0]\"; "
        f"filename=\"poster.jpg\"\r\nContent-Type: image/jpeg\r\n\r\n".encode())
    parts.append(POSTER.read_bytes())
    parts.append(f"\r\n--{boundary}--\r\n".encode())
    body = b"".join(parts)
    _st, msg = _request("POST", f"/channels/{channel_id}/messages", token, body=body,
                        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    return msg if isinstance(msg, dict) else {}


# ---------------------------------------------------------------------------
# Transports
# ---------------------------------------------------------------------------
class BotTokenTransport:
    """Real update-in-place via the bot token; records ACK/update timings."""

    def __init__(self, token: str, channel_id: str, message_id: str) -> None:
        self._token = token
        self._channel = channel_id
        self._message = message_id
        self.ack_payload: dict | None = None
        self.ack_at: float | None = None
        self.patched: dict | None = None
        self.patch_at: float | None = None
        self.ephemerals: list[str] = []

    def ack(self, payload: dict) -> None:
        self.ack_payload = payload
        self.ack_at = time.monotonic()

    def ephemeral(self, content: str) -> None:
        self.ephemerals.append(content)

    def patch_original(self, payload: dict) -> None:
        _request("PATCH", f"/channels/{self._channel}/messages/{self._message}",
                 self._token, body=json.dumps(payload).encode())
        self.patched = payload
        self.patch_at = time.monotonic()


class RecordingTransport(ops.RecordingTransport):
    """In-memory transport for the duplicate-click replay."""


# ---------------------------------------------------------------------------
# Proof context (retained record facts)
# ---------------------------------------------------------------------------
def proof_ctx(state: str) -> ops.LaneContext:
    f = card.ACQUIRED_REEL_FIXTURE
    return ops.LaneContext(
        ref=CID, platform="instagram", content_id=CID,
        canonical_url=f["canonical_url"], state=state,
        caption=f["caption"],
        duration_seconds=f["duration_seconds"], width=f["width"], height=f["height"],
        size_bytes=f["size_bytes"], video_codec=f["video_codec"],
        audio_codec=f["audio_codec"], has_audio=f["has_audio"],
        estimated_cost_micro=f["estimated_cost_micro"],
        ceiling_micro=f["ceiling_micro"], remaining_acquisitions=f["remaining_acquisitions"],
        preview={"kind": "thumbnail", "thumbnail_url": POPPED_ATTACHMENT,
                 "local_filename": f["library_filename"]},
        provenance={"actor": f["actor"], "run_id": f["run_id"]},
        tenant_id="dreemzy", profile_id=PROOF_PROFILE, fixture=True,
    )


# ---------------------------------------------------------------------------
# Supabase read-only checks (service key by name; never printed)
# ---------------------------------------------------------------------------
class SupaRead:
    def __init__(self, url: str, key: str) -> None:
        self._url = url
        self._key = key

    def get(self, path: str):
        req = urllib.request.Request(f"{self._url}/rest/v1/{path}", headers={
            "apikey": self._key,
            "Authorization": f"Bearer {self._key}",
            "User-Agent": "LOOM-Gate1Proof/1.0",
        })
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode() or "[]")


def sha256_file(path: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    env = load_env()
    token = env.get("LOOM_DISCORD_BOT_TOKEN", "")
    if not token:
        print("FAIL: no bot token in ~/.openclaw/.env")
        return 2

    asset_hash_before = sha256_file(ASSET)
    asset_mtime_before = ASSET.stat().st_mtime

    supa = SupaRead(env["LOOM_SUPABASE_URL"], env["LOOM_SUPABASE_SERVICE_ROLE_KEY"])
    store = ops.SupabaseLaneStore(env["LOOM_SUPABASE_URL"], env["LOOM_SUPABASE_SERVICE_ROLE_KEY"])

    ceiling_before = supa.get("loom_provider_ceiling?select=*&id=eq.1")
    runs_before = supa.get("loom_provider_runs?select=id")
    library_before = supa.get("loom_reaction_library?select=id,asset_hash")

    result: dict = {"reel": CID, "asset_sha256_before": asset_hash_before}
    cid = card.make_custom_id(card.ACT_APPROVE, CID)

    # --- 1. Post exactly one card (idempotent guard against double posting) ---
    state = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
    if state.get("message_id"):
        print(f"NOTE: a proof card already exists (message {state['message_id']}); "
              f"reusing it instead of posting a second card.")
        message = {"id": state["message_id"], "channel_id": state["channel_id"]}
    else:
        channel_id = resolve_channel(token)
        vm = card.fixture_view_model(card.STATE_REVIEW, poster_url=POPPED_ATTACHMENT)
        vm["fixture"] = True
        payload = card.format_acquisition_card(vm)
        message = post_card_with_poster(token, channel_id, payload)
        STATE_FILE.write_text(json.dumps({"channel_id": channel_id,
                                          "message_id": message.get("id"),
                                          "posted_at": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                                     time.gmtime())}, indent=2))
    result["channel_id"] = message.get("channel_id") or state.get("channel_id")
    result["message_id"] = message.get("id")
    result["message_url"] = (f"https://discord.com/channels/{GUILD_ID}/"
                             f"{result['channel_id']}/{result['message_id']}")

    # --- 2. Drive ONE Approve through the real handler -----------------------
    transport = BotTokenTransport(token, result["channel_id"], result["message_id"])
    t_start = time.monotonic()
    base_now = time.time()
    out = ops.handle({"action": card.ACT_APPROVE, "ref": CID}, deps=store,
                     transport=transport, actor=REVIEWER, ctx=proof_ctx(card.STATE_REVIEW),
                     now=base_now)
    ack_ms = round(((transport.ack_at or time.monotonic()) - t_start) * 1000, 1)
    update_ms = (round(((transport.patch_at or time.monotonic()) - t_start) * 1000, 1)
                 if transport.patch_at else None)
    result["approve"] = {
        "ok": out.get("ok"),
        "ack_type": (transport.ack_payload or {}).get("type"),
        "ack_ms": ack_ms,
        "update_ms": update_ms,
        "updated_title": ((transport.patched or {}).get("embeds") or [{}])[0].get("title"),
        "effects": {k: v for k, v in (out.get("effects") or {}).items()
                    if k in ("created", "decision", "label")},
    }

    # --- 3. Duplicate protection -------------------------------------------
    t2 = BotTokenTransport(token, result["channel_id"], result["message_id"])
    dup_inside = ops.handle({"action": card.ACT_APPROVE, "ref": CID}, deps=store,
                            transport=t2, actor=REVIEWER, ctx=proof_ctx(card.STATE_REVIEW),
                            now=base_now + 2)
    t3 = BotTokenTransport(token, result["channel_id"], result["message_id"])
    dup_outside = ops.handle({"action": card.ACT_APPROVE, "ref": CID}, deps=store,
                             transport=t3, actor=REVIEWER, ctx=proof_ctx(card.STATE_REVIEW),
                             now=base_now + 120)
    result["duplicate_protection"] = {
        "inside_window": {"ok": dup_inside.get("ok"), "guard": dup_inside.get("guard"),
                          "ephemeral": t2.ephemerals[:1], "patched": t2.patched is not None},
        "outside_window": {"ok": dup_outside.get("ok"),
                           "created": (dup_outside.get("effects") or {}).get("created"),
                           "patched": t3.patched is not None},
    }

    # --- 4. Exact durable rows + zero-spend evidence ------------------------
    jobs = supa.get(f"loom_acquisition_jobs?duplicate_fingerprint=eq.{CID}&select=*")
    result["jobs"] = [{
        "id": j.get("id"), "dedup_key": j.get("dedup_key"),
        "authorization_type": j.get("authorization_type"), "status": j.get("status"),
        "status_reason": j.get("status_reason"),
        "canonical_url": j.get("canonical_url"), "platform": j.get("platform"),
        "platform_content_id": j.get("platform_content_id"),
        "destination": j.get("destination"), "actor": j.get("actor"),
        "max_bytes": j.get("max_bytes"),
        "max_duration_seconds": j.get("max_duration_seconds"),
        "retry_count": j.get("retry_count"), "asset_provenance": j.get("asset_provenance"),
    } for j in (jobs if isinstance(jobs, list) else [])]

    job_ids = [j["id"] for j in result["jobs"]]
    runs_after = supa.get("loom_provider_runs?select=id,acquisition_job_id")
    library_after = supa.get("loom_reaction_library?select=id,asset_hash")
    ceiling_after = supa.get("loom_provider_ceiling?select=*&id=eq.1")

    result["zero_spend"] = {
        "provider_runs_before": len(runs_before if isinstance(runs_before, list) else []),
        "provider_runs_after": len(runs_after if isinstance(runs_after, list) else []),
        "provider_runs_for_this_job": [r for r in (runs_after if isinstance(runs_after, list) else [])
                                       if r.get("acquisition_job_id") in job_ids],
        "ceiling_before": ceiling_before,
        "ceiling_after": ceiling_after,
        "library_rows_before": len(library_before if isinstance(library_before, list) else []),
        "library_rows_after": len(library_after if isinstance(library_after, list) else []),
    }
    result["asset_sha256_after"] = sha256_file(ASSET)
    result["asset_unchanged"] = (
        result["asset_sha256_after"] == asset_hash_before
        and ASSET.stat().st_mtime == asset_mtime_before)

    # --- 5. Stored URL hygiene ---------------------------------------------
    urls = [j.get("canonical_url") or "" for j in result["jobs"]]
    result["url_hygiene"] = {
        "urls": urls,
        "no_signed_params": all(("?" not in u and "#" not in u) for u in urls),
        "no_credentials": all(("sig=" not in u.lower() and "oh=" not in u.lower()
                               and "token" not in u.lower() and "bearer" not in u.lower())
                              for u in urls),
    }

    REPORT_FILE.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
