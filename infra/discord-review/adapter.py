#!/usr/bin/env python3
"""
LOOM Discord Review Adapter — native Discord review controls.

This is the *minimal verification adapter* between Discord's Interactions
Endpoint and n8n. It does exactly three things:

1.  Verify Discord's Ed25519 signature (X-Signature-Ed25519 +
    X-Signature-Timestamp) against the exact raw request body using
    LOOM_DISCORD_PUBLIC_KEY. n8n's webhook node cannot reliably expose the
    exact raw body, so this adapter owns signature verification and forwards
    only VERIFIED, normalized events to n8n.

2.  Serve the Interactions Endpoint protocol: PING/PONG, component button
    clicks (approve/reject/hold), and the reject modal.

3.  Send the candidate card AS THE BOT (interactive custom_id components
    require a bot message, not a webhook message): generate a temporary
    Docker-independent review proxy (H.264/AAC/yuv420p/faststart, <= ~23 MB,
    9:16 preserved), upload it as a Discord attachment, and post the card
    with Approve/Reject/Hold buttons.

Secrets are read from environment variables (never printed, never logged).
The service binds 127.0.0.1 only; Caddy fronts it via the Tailscale Funnel.

Endpoints:
    POST /discord/interactions   -> Discord Interactions Endpoint (Ed25519)
    POST /discord/card           -> internal card send (auth'd by Caddy)
    GET  /health                 -> liveness probe
"""

import base64
import hashlib
import hmac
import json
import os
import re
import subprocess
import tempfile
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

# ---------------------------------------------------------------------------
# Config (from env)
# ---------------------------------------------------------------------------

def _env(name, default=None, required=False):
    val = os.environ.get(name) or default
    if required and not val:
        raise RuntimeError(f"Missing required env var: {name}")
    return val

DISCORD_APP_ID      = _env("LOOM_DISCORD_APP_ID", required=True)
DISCORD_PUBLIC_KEY  = _env("LOOM_DISCORD_PUBLIC_KEY", required=True)
DISCORD_BOT_TOKEN   = _env("LOOM_DISCORD_BOT_TOKEN", required=True)
SUPABASE_URL        = _env("LOOM_SUPABASE_URL", required=True)
SUPABASE_KEY        = _env("LOOM_SUPABASE_SERVICE_ROLE_KEY", required=True)
N8N_REVIEW_WEBHOOK  = _env("LOOM_N8N_REVIEW_WEBHOOK",
                           "https://fns2.app.n8n.cloud/webhook/loom-review-decision")
OPENSHORTS_OUTPUT   = _env("LOOM_OPENSHORTS_OUTPUT",
                           "/Users/user/Desktop/LOOM-Factory/openshorts/output")
LISTEN_HOST         = _env("LOOM_DISCORD_LISTEN_HOST", "127.0.0.1")
LISTEN_PORT         = int(_env("LOOM_DISCORD_LISTEN_PORT", "8090"))
CLIP_REVIEW_CHANNEL_NAME = _env("LOOM_CLIP_REVIEW_CHANNEL", "clip-review")
PROXY_MAX_BYTES     = 23 * 1024 * 1024  # Discord upload ceiling (Boosts raise it; stay safe)

DISCORD_API = "https://discord.com/api/v10"

REJECTION_CATEGORIES = [
    "hook_issue", "caption_issue", "content_issue", "audio_issue",
    "visual_issue", "context_issue", "rights_risk", "other",
]

# ---------------------------------------------------------------------------
# Ed25519 verification (Discord spec)
# ---------------------------------------------------------------------------

def verify_signature(public_key_hex: str, signature_hex: str, timestamp: str, raw_body: bytes) -> bool:
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        from cryptography.exceptions import InvalidSignature
        pub = Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key_hex))
        message = timestamp.encode() + raw_body
        sig = bytes.fromhex(signature_hex)
        pub.verify(sig, message)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Opaque custom_id (no secrets). Compact: decision + clip_id only, so it stays
# under Discord's 100-char custom_id limit. Tenant, asset version and reviewer
# are resolved from the ledger + interaction payload at decision time, never
# embedded here.
# ---------------------------------------------------------------------------

def make_custom_id(clip_id: str, decision: str) -> str:
    payload = {"d": decision, "c": clip_id}
    raw = json.dumps(payload, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def parse_custom_id(custom_id: str):
    try:
        pad = "=" * (-len(custom_id) % 4)
        raw = base64.urlsafe_b64decode(custom_id + pad)
        return json.loads(raw.decode())
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Small HTTP helper
# ---------------------------------------------------------------------------

def http_json(method, url, token=None, body=None, timeout=15):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json",
               "User-Agent": "LOOMReviewAdapter/1.0 (DiscordBot)"}
    if token:
        headers["Authorization"] = f"Bot {token}"
    req = Request(url, data=data, headers=headers, method=method)
    with urlopen(req, timeout=timeout) as resp:
        return resp.status, json.loads(resp.read().decode() or "{}")


def supabase_get(table, clip_id):
    url = f"{SUPABASE_URL}/rest/v1/{table}?clip_id=eq.{clip_id}&select=*"
    req = Request(url, headers={
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "User-Agent": "LOOMReviewAdapter/1.0",
    }, method="GET")
    with urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode() or "[]")


def supabase_upsert(table, payload, ignore_duplicates=False):
    url = f"{SUPABASE_URL}/rest/v1/{table}?on_conflict=clip_id"
    data = json.dumps(payload).encode()
    prefer = ("resolution=ignore-duplicates,return=representation" if ignore_duplicates
              else "resolution=merge-duplicates,return=representation")
    req = Request(url, data=data, headers={
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "User-Agent": "LOOMReviewAdapter/1.0",
        "Prefer": prefer,
    }, method="POST")
    with urlopen(req, timeout=15) as resp:
        return resp.status, json.loads(resp.read().decode() or "{}")


# ---------------------------------------------------------------------------
# Review proxy generation (Docker-independent, master untouched)
# ---------------------------------------------------------------------------

def probe_duration(path: str) -> float:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", path],
            capture_output=True, timeout=30)
        return float(out.stdout.decode().strip() or 0)
    except Exception:
        return 0.0


def generate_review_proxy(master_path: str, out_dir: str) -> str:
    """Re-encode the master into a compact Discord-safe MP4 (H.264/AAC,
    yuv420p, faststart) preserving 9:16 dimensions and the already-burned
    hook + captions (exactly once — they are a single layer in the master).
    Target ~< 23 MB using a bitrate budget derived from duration."""
    duration = probe_duration(master_path)
    if duration <= 0:
        duration = 60.0
    # Budget: 21 MB total, minus 128 kbps audio, leaves the video budget.
    target_bits = int(21 * 8 * 1024 * 1024)
    audio_bps = 128_000
    video_bps = max(400_000, int(target_bits / duration) - audio_bps)
    video_bps = min(video_bps, 8_000_000)

    proxy_path = os.path.join(out_dir, f"review_{uuid.uuid4().hex[:12]}.mp4")
    cmd = [
        "ffmpeg", "-y", "-i", master_path,
        "-c:v", "libx264", "-preset", "fast",
        "-b:v", str(video_bps), "-maxrate", str(int(video_bps * 1.2)),
        "-bufsize", str(int(video_bps * 2)),
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        proxy_path,
    ]
    proc = subprocess.run(cmd, capture_output=True, timeout=600)
    if proc.returncode != 0 or not os.path.exists(proxy_path):
        raise RuntimeError(f"ffmpeg proxy generation failed: {proc.stderr.decode()[-500:]}")
    size = os.path.getsize(proxy_path)
    if size > PROXY_MAX_BYTES:
        os.remove(proxy_path)
        raise RuntimeError(f"Proxy too large ({size} bytes > {PROXY_MAX_BYTES})")
    return proxy_path


# ---------------------------------------------------------------------------
# Discord channel resolution (via authenticated API, not a hardcoded ask)
# ---------------------------------------------------------------------------

_channel_cache = {"id": None, "ts": 0.0}

def resolve_clip_review_channel() -> str:
    """Resolve #clip-review by name through the bot's guild list."""
    if _channel_cache["id"] and (time.time() - _channel_cache["ts"]) < 600:
        return _channel_cache["id"]
    status, guilds = http_json("GET", f"{DISCORD_API}/users/@me/guilds", DISCORD_BOT_TOKEN)
    for g in guilds:
        gid = g.get("id")
        s2, channels = http_json("GET", f"{DISCORD_API}/guilds/{gid}/channels", DISCORD_BOT_TOKEN)
        for ch in channels:
            if ch.get("name") == CLIP_REVIEW_CHANNEL_NAME and ch.get("type") == 0:
                _channel_cache["id"] = ch["id"]
                _channel_cache["ts"] = time.time()
                return ch["id"]
    raise RuntimeError(f"Channel #{CLIP_REVIEW_CHANNEL_NAME} not found")


# ---------------------------------------------------------------------------
# Card send (bot message + attachment + buttons)
# ---------------------------------------------------------------------------

def send_review_card(payload: dict):
    """payload: clip_id, job_id, ready_file, master_asset, youtube_title,
    caption, hook, overall_score, decision, duration_seconds, source_url,
    tenant_id, run_id, captions_applied, hook_applied, no_speech,
    transcript_segment_count, render_version, asset_version."""
    clip_id = payload["clip_id"]
    job_id = payload.get("job_id", "")
    ready_file = payload.get("ready_file") or payload.get("master_asset")
    # Locate master on disk (renderer-declared final asset).
    master_path = payload.get("master_path")
    if not master_path and job_id and ready_file:
        master_path = os.path.join(OPENSHORTS_OUTPUT, job_id, os.path.basename(ready_file))
    if not master_path or not os.path.exists(master_path):
        raise RuntimeError(f"Master asset not found: {master_path}")

    channel_id = resolve_clip_review_channel()

    # 1) Generate review proxy (master untouched).
    tmpdir = tempfile.mkdtemp(prefix="loom-review-")
    try:
        proxy_path = generate_review_proxy(master_path, tmpdir)
        proxy_name = f"review_{clip_id.replace(':', '_')}.mp4"
        proxy_size = os.path.getsize(proxy_path)

        # 2) Upload as Discord attachment + post message with buttons.
        #    (multipart/form-data via urllib; file + payload_json)
        tenant = payload.get("tenant_id") or ""
        approve_id = make_custom_id(clip_id, "approve")
        reject_id  = make_custom_id(clip_id, "reject")
        hold_id    = make_custom_id(clip_id, "hold")

        embed = {
            "title": payload.get("youtube_title") or "Untitled candidate",
            "color": 0xC9A227,  # brass/gold
            "fields": [
                {"name": "Caption", "value": (payload.get("caption") or "—")[:1000], "inline": False},
                {"name": "Hook", "value": (payload.get("hook") or "—")[:400], "inline": False},
                {"name": "LOOM Score", "value": str(payload.get("overall_score", "—")), "inline": True},
                {"name": "Disposition", "value": str(payload.get("decision") or "—"), "inline": True},
                {"name": "Duration", "value": f"{payload.get('duration_seconds', '—')}s", "inline": True},
                {"name": "Source", "value": (payload.get("source_url") or "—")[:800], "inline": False},
                {"name": "Tenant", "value": str(payload.get("tenant_id") or "—"), "inline": True},
                {"name": "Run ID", "value": str(payload.get("run_id") or "—"), "inline": True},
                {"name": "Clip ID", "value": str(clip_id), "inline": True},
                {"name": "Captions", "value": f"expected={payload.get('captions_expected', '—')} applied={payload.get('captions_applied', '—')}", "inline": True},
                {"name": "Hook", "value": f"expected={payload.get('hook_expected', '—')} applied={payload.get('hook_applied', '—')}", "inline": True},
                {"name": "no_speech", "value": str(payload.get("no_speech", False)), "inline": True},
                {"name": "Versions", "value": f"master={payload.get('master_asset') or '—'} | render={payload.get('render_version') or '—'} | proxy={proxy_name}", "inline": False},
            ],
        }

        components = [{
            "type": 1,
            "components": [
                {"type": 2, "style": 3, "label": "Approve", "custom_id": approve_id},
                {"type": 2, "style": 4, "label": "Reject", "custom_id": reject_id},
                {"type": 2, "style": 2, "label": "Hold", "custom_id": hold_id},
            ],
        }]

        message_payload = {
            "content": f"🎬 **LOOM Candidate Review** — {clip_id}",
            "embeds": [embed],
            "components": components,
        }

        # Multipart upload: payload_json + files[0]
        boundary = f"----loom{uuid.uuid4().hex}"
        body = b""
        body += f"--{boundary}\r\n".encode()
        body += b'Content-Disposition: form-data; name="payload_json"\r\n'
        body += b"Content-Type: application/json\r\n\r\n"
        body += json.dumps(message_payload).encode()
        body += b"\r\n"
        body += f"--{boundary}\r\n".encode()
        body += f'Content-Disposition: form-data; name="files[0]"; filename="{proxy_name}"\r\n'.encode()
        body += b"Content-Type: video/mp4\r\n\r\n"
        with open(proxy_path, "rb") as f:
            body += f.read()
        body += f"\r\n--{boundary}--\r\n".encode()

        req = Request(
            f"{DISCORD_API}/channels/{channel_id}/messages",
            data=body,
            headers={
                "Authorization": f"Bot {DISCORD_BOT_TOKEN}",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "User-Agent": "LOOMReviewAdapter/1.0 (DiscordBot)",
            },
            method="POST",
        )
        with urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read().decode() or "{}")

        message_id = result.get("id")
        attachment_url = None
        for att in result.get("attachments", []):
            attachment_url = att.get("url")

        return {
            "channel_id": channel_id,
            "message_id": message_id,
            "attachment_url": attachment_url,
            "proxy_name": proxy_name,
            "proxy_size": proxy_size,
        }
    finally:
        # Delete only the local throwaway proxy; preserve master + metadata.
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Decision persistence (idempotent; first terminal decision wins)
# ---------------------------------------------------------------------------

def persist_decision(clip_id, decision, reviewer, category=None, instructions=None,
                     asset_url=None, tenant_id=None):
    """Persist a human decision to Supabase loom_reviews. Idempotent and
    first-terminal-decision-wins AT THE DATABASE LEVEL: the INSERT uses
    ON CONFLICT (clip_id) DO NOTHING, so a duplicate delivery or a second
    click cannot create a second decision or overwrite the first. Returns
    (row, created)."""
    final_status = {"approve": "approved", "reject": "rejected", "hold": "on_hold"}[decision]
    payload = {
        "clip_id": clip_id,
        "asset_url": asset_url,
        "tenant_id": tenant_id,
        "reviewer": reviewer,
        "decision": decision,
        "rejection_category": category if decision == "reject" else None,
        "reviewer_instructions": instructions,
        "final_status": final_status,
    }
    # ignore-duplicates: on conflict, PostgREST returns 201 with an EMPTY body
    # (DO NOTHING), so an empty result means "already decided" — first wins.
    status, rows = supabase_upsert("loom_reviews", payload, ignore_duplicates=True)
    inserted = isinstance(rows, list) and len(rows) > 0
    if inserted:
        return rows[0], True
    # Already decided: return the existing authoritative row without overwriting.
    existing = supabase_get("loom_reviews", clip_id)
    if existing:
        return existing[0], False
    return payload, True


def forward_to_n8n(normalized_event: dict):
    """Forward the verified normalized event to n8n's review-decision webhook
    (fire-and-forget; the adapter has already persisted authoritatively)."""
    try:
        http_json("POST", N8N_REVIEW_WEBHOOK, body=normalized_event, timeout=10)
    except Exception as e:
        print(f"⚠️ n8n forward failed (non-fatal): {e}")


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = "LOOMReviewAdapter/1.0"

    def _send_json(self, status_code, obj):
        data = json.dumps(obj).encode()
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", 0))
        return self.rfile.read(length) if length else b""

    def do_GET(self):
        if self.path == "/health":
            self._send_json(200, {"status": "ok"})
            return
        # Never allow state-changing GET links.
        self._send_json(404, {"error": "not found"})

    def do_POST(self):
        if self.path == "/health":
            self._send_json(200, {"status": "ok"})
            return
        if self.path == "/discord/interactions":
            self._handle_interaction()
            return
        if self.path == "/discord/card":
            self._handle_card_send()
            return
        self._send_json(404, {"error": "not found"})

    # -- Interactions Endpoint --------------------------------------------

    def _handle_interaction(self):
        raw = self._read_body()
        signature = self.headers.get("X-Signature-Ed25519") or ""
        timestamp = self.headers.get("X-Signature-Timestamp") or ""

        # Verify Ed25519 signature over the exact raw body.
        if not signature or not timestamp:
            self._send_json(401, {"error": "missing signature headers"})
            return
        if not verify_signature(DISCORD_PUBLIC_KEY, signature, timestamp, raw):
            self._send_json(401, {"error": "invalid signature"})
            return

        try:
            interaction = json.loads(raw.decode())
        except Exception:
            self._send_json(400, {"error": "bad json"})
            return

        itype = interaction.get("type")
        if itype == 1:
            # PING -> PONG
            self._send_json(200, {"type": 1})
            return

        if itype == 3:  # MESSAGE_COMPONENT (button click)
            self._handle_button(interaction)
            return

        if itype == 5:  # MODAL_SUBMIT (reject modal)
            self._handle_modal_submit(interaction)
            return

        # Acknowledge unknown interactions quickly.
        self._send_json(200, {"type": 4, "data": {"content": "Unsupported interaction.", "flags": 64}})

    def _handle_button(self, interaction):
        data = interaction.get("data", {})
        custom_id = data.get("custom_id", "")
        parsed = parse_custom_id(custom_id)
        if not parsed:
            self._send_json(200, {"type": 4, "data": {"content": "Invalid button.", "flags": 64}})
            return

        decision = parsed.get("d")
        clip_id = parsed.get("c")
        if decision not in ("approve", "reject", "hold") or not clip_id:
            self._send_json(200, {"type": 4, "data": {"content": "Invalid action.", "flags": 64}})
            return

        # Resolve + validate the candidate exists in the ledger.
        scores = supabase_get("loom_scores", clip_id)
        if not scores:
            self._send_json(200, {"type": 4, "data": {"content": "Unknown candidate.", "flags": 64}})
            return

        reviewer = self._reviewer_name(interaction)

        if decision == "reject":
            # Open a modal to collect a structured category + optional notes.
            modal_custom_id = make_custom_id(clip_id, "reject")
            self._send_json(200, {
                "type": 9,
                "data": {
                    "custom_id": modal_custom_id,
                    "title": "Reject candidate",
                    "components": [
                        {"type": 1, "components": [{
                            "type": 4, "custom_id": "reject_category", "style": 1,
                            "label": "Category", "required": True,
                            "placeholder": " | ".join(REJECTION_CATEGORIES),
                            "min_length": 3, "max_length": 40,
                        }]},
                        {"type": 1, "components": [{
                            "type": 4, "custom_id": "reject_instructions", "style": 2,
                            "label": "Instructions (optional)", "required": False,
                            "placeholder": "Optional fix instructions for the team",
                            "max_length": 1000,
                        }]},
                    ],
                },
            })
            return

        # approve / hold: persist, then update the card to "decided".
        self._decide_and_update(interaction, clip_id, decision, reviewer,
                                category=None, instructions=None)

    def _handle_modal_submit(self, interaction):
        data = interaction.get("data", {})
        custom_id = data.get("custom_id", "")
        parsed = parse_custom_id(custom_id)
        if not parsed or parsed.get("d") != "reject":
            self._send_json(200, {"type": 4, "data": {"content": "Invalid modal.", "flags": 64}})
            return

        clip_id = parsed.get("c")
        values = {}
        for row in data.get("components", []):
            for comp in row.get("components", []):
                values[comp.get("custom_id")] = comp.get("value", "")

        category = (values.get("reject_category") or "").strip()
        if category not in REJECTION_CATEGORIES:
            # Structured category required — ask again via ephemeral message.
            self._send_json(200, {"type": 4, "data": {
                "content": f"Unknown category. Use one of: {', '.join(REJECTION_CATEGORIES)}",
                "flags": 64,
            }})
            return

        instructions = (values.get("reject_instructions") or "").strip() or None
        reviewer = self._reviewer_name(interaction)
        self._decide_and_update(interaction, clip_id, "reject", reviewer,
                                category=category, instructions=instructions)

    def _decide_and_update(self, interaction, clip_id, decision, reviewer,
                           category, instructions):
        scores = supabase_get("loom_scores", clip_id)
        tenant = (scores[0].get("tenant_id") if scores else None)
        asset_url = (scores[0].get("asset_url") if scores else None)

        row, created = persist_decision(
            clip_id, decision, reviewer, category=category,
            instructions=instructions, asset_url=asset_url, tenant_id=tenant)

        # Forward the verified normalized event to n8n (idempotent there too).
        forward_to_n8n({
            "clip_id": clip_id,
            "decision": decision,
            "reviewer": reviewer,
            "rejection_category": category if decision == "reject" else None,
            "reviewer_instructions": instructions,
            "tenant_id": tenant,
            "asset_url": asset_url,
        })

        # Build the "decided" card update: show reviewer/decision/timestamp,
        # disable controls.
        ts = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
        final_status = row.get("final_status") or {
            "approve": "approved", "reject": "rejected", "hold": "on_hold"}[decision]
        decided_embed = {
            "title": "✅ Decision recorded" if created else "ℹ️ Already decided",
            "color": 0x2ecc71 if decision == "approve" else (0xe74c3c if decision == "reject" else 0xf1c40f),
            "fields": [
                {"name": "Decision", "value": f"{decision} ({final_status})", "inline": True},
                {"name": "Reviewer", "value": reviewer, "inline": True},
                {"name": "Timestamp", "value": ts, "inline": True},
            ],
        }
        if decision == "reject" and category:
            decided_embed["fields"].append({"name": "Category", "value": category, "inline": True})
        if instructions:
            decided_embed["fields"].append({"name": "Instructions", "value": instructions, "inline": False})

        update_data = {
            "embeds": [decided_embed],
            "components": [],  # controls disabled/removed
        }
        self._send_json(200, {"type": 7, "data": update_data})

    def _reviewer_name(self, interaction):
        member = interaction.get("member") or {}
        user = interaction.get("user") or {}
        u = member.get("user") or user or {}
        name = u.get("username") or u.get("global_name") or "unknown"
        uid = u.get("id") or ""
        return f"{name}#{uid}" if uid else name

    # -- Card send ---------------------------------------------------------

    def _handle_card_send(self):
        raw = self._read_body()
        try:
            payload = json.loads(raw.decode())
        except Exception:
            self._send_json(400, {"error": "bad json"})
            return
        try:
            result = send_review_card(payload)
            self._send_json(200, {"success": True, **result})
        except Exception as e:
            self._send_json(500, {"success": False, "error": str(e)})

    def log_message(self, fmt, *args):
        print(f"[adapter] {self.address_string()} - {fmt % args}")


def main():
    server = ThreadingHTTPServer((LISTEN_HOST, LISTEN_PORT), Handler)
    print(f"LOOM Discord Review Adapter listening on {LISTEN_HOST}:{LISTEN_PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
