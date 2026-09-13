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

Reaction-lane identity (Segment 6 pre-distribution):
    review_stage='moment'             -> approve enters the reaction queue.
    review_stage='final_publication'  -> approve may mark media publication-approved
                                         (guarded: exactly one matching non-fixture
                                         loom_media row, matching tenant/clip/asset_version).

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
import secrets
import subprocess
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone, timedelta
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
EDITOR_BASE_URL     = _env("LOOM_EDITOR_BASE_URL", "http://localhost:5173")  # dev scaffold; final = Discord Activity
EDITOR_ORIGIN       = _env("LOOM_EDITOR_ORIGIN", "http://localhost:5173")  # CORS allow for the dev editor

DISCORD_API = "https://discord.com/api/v10"

REVIEW_STAGES = ("moment", "final_publication")

REJECTION_CATEGORIES = [
    "hook_issue", "caption_issue", "content_issue", "audio_issue",
    "visual_issue", "context_issue", "rights_risk", "other",
]

# Deterministic explicit-language terms (display-mask only). Lowercase;
# matched on full word boundaries. The RAW text is always preserved in the
# payload / Supabase for traceability — this only masks what is shown on the
# Discord review card. Never used to silently change a decision.
EXPLICIT_TERMS = frozenset({
    # racial/ethnic slurs
    "nigga", "niggaz", "nigger", "niggers", "nigguh",
    # homophobic slurs
    "faggot", "faggots", "fag", "fags",
    # ableist slurs
    "retard", "retards", "retarded",
    # sexist/sexual slurs
    "cunt", "cunts", "bitch", "bitches",
    # strong profanity
    "fuck", "fucking", "fucker", "fucked", "fucks",
    "shit", "shits", "shitty",
})


def mask_explicit(text):
    """Return (masked_text, found_terms). Deterministic: replaces any matched
    explicit term with its first letter followed by asterisks, preserving the
    raw text everywhere else. No model call; no decision change."""
    if not text:
        return text, []
    found = []
    seen = set()

    def _repl(m):
        word = m.group(0)
        key = word.lower()
        if key in EXPLICIT_TERMS:
            if key not in seen:
                seen.add(key)
                found.append(key)
            return word[0] + "*" * (len(word) - 1)
        return word

    masked = re.sub(r"[A-Za-z]+", _repl, text)
    return masked, found

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
# Opaque review tokens (durable, Supabase-backed)
# ---------------------------------------------------------------------------
# custom_id carries only {decision, token}; the full identity (tenant_id,
# clip_id, asset_version, review_stage) is resolved from a durable Supabase
# ledger at decision time — never trusted from the client, never truncated.
# Only the token HASH is stored; the raw token lives only in the ephemeral
# custom_id. Restart-survivable: an adapter restart does NOT invalidate a
# pending card.
# ---------------------------------------------------------------------------

REVIEW_TOKEN_TTL_SECONDS = 72 * 3600  # 72h. Expired cards revert to manual Hold (never auto-approve).
EDITOR_TOKEN_TTL_SECONDS = 15 * 60     # 15 min single-use editor session (hash-only).

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def register_review_token(token: str, identity: dict, discord_message_id=None):
    """Create a durable review-token row. Raw token is used only for the
    custom_id; the DB stores only its SHA-256 hash."""
    token_hash = hash_token(token)
    expires_at = (datetime.now(timezone.utc) + timedelta(seconds=REVIEW_TOKEN_TTL_SECONDS)).isoformat()
    payload = {
        "token_hash": token_hash,
        "tenant_id": identity["tenant_id"],
        "clip_id": identity["clip_id"],
        "asset_version": identity["asset_version"],
        "review_stage": identity["review_stage"],
        "discord_message_id": discord_message_id,
        "status": "pending",
        "expires_at": expires_at,
    }
    url = f"{SUPABASE_URL}/rest/v1/loom_review_tokens"
    data = json.dumps(payload).encode()
    headers = _supabase_headers()
    headers["Content-Type"] = "application/json"
    headers["Prefer"] = "return=representation"
    req = Request(url, data=data, headers=headers, method="POST")
    with urlopen(req, timeout=15) as resp:
        return resp.status


def update_review_token_message(token_hash: str, discord_message_id: str):
    url = f"{SUPABASE_URL}/rest/v1/loom_review_tokens?token_hash=eq.{token_hash}"
    data = json.dumps({"discord_message_id": discord_message_id}).encode()
    headers = _supabase_headers()
    headers["Content-Type"] = "application/json"
    req = Request(url, data=data, headers=headers, method="PATCH")
    with urlopen(req, timeout=15) as resp:
        return resp.status


def mark_token_expired(token_hash: str):
    url = f"{SUPABASE_URL}/rest/v1/loom_review_tokens?token_hash=eq.{token_hash}&status=eq.pending"
    data = json.dumps({"status": "expired"}).encode()
    headers = _supabase_headers()
    headers["Content-Type"] = "application/json"
    req = Request(url, data=data, headers=headers, method="PATCH")
    with urlopen(req, timeout=15) as resp:
        return resp.status


def resolve_review_token(token: str):
    """Resolve an opaque token to its identity from the durable ledger.
    Rejects unknown, expired, or consumed tokens. Returns {'token_hash',
    'identity'} or None."""
    token_hash = hash_token(token)
    url = (f"{SUPABASE_URL}/rest/v1/loom_review_tokens"
           f"?token_hash=eq.{token_hash}&status=eq.pending&select=*")
    req = Request(url, headers=_supabase_headers(), method="GET")
    with urlopen(req, timeout=15) as resp:
        rows = json.loads(resp.read().decode() or "[]")
    if not rows:
        return None
    row = rows[0]
    expires_at = row.get("expires_at")
    if expires_at:
        try:
            exp = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
            if exp < datetime.now(timezone.utc):
                mark_token_expired(token_hash)
                return None
        except Exception:
            pass
    return {
        "token_hash": token_hash,
        "identity": {
            "tenant_id": row.get("tenant_id"),
            "clip_id": row.get("clip_id"),
            "asset_version": row.get("asset_version"),
            "review_stage": row.get("review_stage"),
        },
    }


def consume_review_token(token_hash: str):
    """Atomically finalize a token (first terminal decision wins). PATCH only
    where status is still 'pending'; a competing action that already consumed
    it affects zero rows."""
    url = f"{SUPABASE_URL}/rest/v1/loom_review_tokens?token_hash=eq.{token_hash}&status=eq.pending"
    data = json.dumps({"status": "consumed", "consumed_at": now_iso()}).encode()
    headers = _supabase_headers()
    headers["Content-Type"] = "application/json"
    req = Request(url, data=data, headers=headers, method="PATCH")
    with urlopen(req, timeout=15) as resp:
        return resp.status


def register_editor_token(token: str, identity: dict, reviewer: str):
    """Create a single-use editor-session row. The raw token is used only for
    the editor launch; the DB stores only its SHA-256 hash. 15-minute expiry,
    replay-protected (status flips from 'active' on first use)."""
    token_hash = hash_token(token)
    expires_at = (datetime.now(timezone.utc) + timedelta(seconds=EDITOR_TOKEN_TTL_SECONDS)).isoformat()
    payload = {
        "token_hash": token_hash,
        "tenant_id": identity["tenant_id"],
        "clip_id": identity["clip_id"],
        "source_asset_version": identity["asset_version"],
        "reviewer": reviewer,
        "status": "active",
        "expires_at": expires_at,
    }
    url = f"{SUPABASE_URL}/rest/v1/loom_edit_sessions"
    data = json.dumps(payload).encode()
    headers = _supabase_headers()
    headers["Content-Type"] = "application/json"
    headers["Prefer"] = "return=representation"
    req = Request(url, data=data, headers=headers, method="POST")
    with urlopen(req, timeout=15) as resp:
        return resp.status


def consume_editor_token(token_hash: str):
    """Atomically consume a single-use editor token (first exchange wins).
    PATCH consumed_at only where status='active' AND consumed_at IS NULL; a
    competing/replayed exchange affects zero rows."""
    url = (f"{SUPABASE_URL}/rest/v1/loom_edit_sessions"
           f"?token_hash=eq.{token_hash}&status=eq.active&consumed_at=is.null")
    data = json.dumps({"consumed_at": now_iso()}).encode()
    headers = _supabase_headers()
    headers["Content-Type"] = "application/json"
    headers["Prefer"] = "return=representation"
    req = Request(url, data=data, headers=headers, method="PATCH")
    with urlopen(req, timeout=15) as resp:
        return resp.status, json.loads(resp.read().decode() or "[]")


def resolve_editor_token(token: str):
    """Validate + consume a single-use editor token (one-time exchange). Returns
    the session identity or None. Consumes immediately."""
    token_hash = hash_token(token)
    status, rows = consume_editor_token(token_hash)
    if status not in (200, 204) or not isinstance(rows, list) or not rows:
        return None
    row = rows[0]
    expires_at = row.get("expires_at")
    if expires_at:
        try:
            exp = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
            if exp < datetime.now(timezone.utc):
                return None
        except Exception:
            pass
    return {
        "edit_session_id": row.get("id"),
        "tenant_id": row.get("tenant_id"),
        "clip_id": row.get("clip_id"),
        "source_asset_version": row.get("source_asset_version"),
    }


def resolve_edit_session(edit_session_id: str):
    """Pre-flight session validation before the version RPC. The session must
    exist, be 'active', and have its launch token already consumed (i.e. it went
    through the one-time exchange). Returns the session identity or None. The RPC
    re-validates under its own advisory lock; this is defense-in-depth so the
    adapter never blindly forwards an arbitrary edit_session_id."""
    url = (f"{SUPABASE_URL}/rest/v1/loom_edit_sessions"
           f"?id=eq.{edit_session_id}&status=eq.active&select=*")
    req = Request(url, headers=_supabase_headers(), method="GET")
    with urlopen(req, timeout=15) as resp:
        rows = json.loads(resp.read().decode() or "[]")
    if not rows:
        return None
    row = rows[0]
    if not row.get("consumed_at"):
        return None  # launch token was never exchanged — reject
    expires_at = row.get("expires_at")
    if expires_at:
        try:
            exp = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
            if exp < datetime.now(timezone.utc):
                return None
        except Exception:
            pass
    return {
        "edit_session_id": row.get("id"),
        "tenant_id": row.get("tenant_id"),
        "clip_id": row.get("clip_id"),
        "source_asset_version": row.get("source_asset_version"),
    }


def register_edited_asset_rpc(payload: dict):
    """Call the atomic register_edited_asset RPC (one transaction: next
    asset_version + lineage row + immutable correction log + session complete)."""
    url = f"{SUPABASE_URL}/rest/v1/rpc/register_edited_asset"
    data = json.dumps(payload).encode()
    headers = _supabase_headers()
    headers["Content-Type"] = "application/json"
    req = Request(url, data=data, headers=headers, method="POST")
    with urlopen(req, timeout=30) as resp:
        return resp.status, json.loads(resp.read().decode() or "{}")


# ---------------------------------------------------------------------------
# Opaque custom_id (no secrets). Compact: decision + opaque token only, so it
# stays well under Discord's 100-char custom_id limit. The full identity is
# resolved from the server-side token map, never embedded or trusted here.
# ---------------------------------------------------------------------------

def make_custom_id(token: str, decision: str) -> str:
    payload = {"d": decision, "t": token}
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


def _supabase_headers():
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "User-Agent": "LOOMReviewAdapter/1.0",
    }


def supabase_filter_get(table, filters):
    qs = "&".join(f"{k}=eq.{v}" for k, v in filters.items())
    url = f"{SUPABASE_URL}/rest/v1/{table}?{qs}&select=*"
    req = Request(url, headers=_supabase_headers(), method="GET")
    with urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode() or "[]")


def supabase_get(table, clip_id):
    """Single-clip lookup (loom_scores, which remains keyed by clip_id)."""
    return supabase_filter_get(table, {"clip_id": clip_id})


def supabase_get_review(identity):
    """Lookup an existing decision by the full reaction-lane identity."""
    return supabase_filter_get("loom_reviews", {
        "tenant_id": identity["tenant_id"],
        "clip_id": identity["clip_id"],
        "asset_version": identity["asset_version"],
        "review_stage": identity["review_stage"],
    })


def supabase_query_media(identity):
    """Find loom_media rows matching tenant + clip + asset_version."""
    return supabase_filter_get("loom_media", {
        "tenant_id": identity["tenant_id"],
        "clip_id": identity["clip_id"],
        "asset_version": identity["asset_version"],
    })


def supabase_upsert(table, payload, ignore_duplicates=False):
    conflict = ("tenant_id,clip_id,asset_version,review_stage" if table == "loom_reviews"
                else "clip_id")
    url = f"{SUPABASE_URL}/rest/v1/{table}?on_conflict={conflict}"
    data = json.dumps(payload).encode()
    prefer = ("resolution=ignore-duplicates,return=representation" if ignore_duplicates
              else "resolution=merge-duplicates,return=representation")
    headers = _supabase_headers()
    headers["Content-Type"] = "application/json"
    headers["Prefer"] = prefer
    req = Request(url, data=data, headers=headers, method="POST")
    with urlopen(req, timeout=15) as resp:
        return resp.status, json.loads(resp.read().decode() or "{}")


def supabase_patch_media(media_id, updates):
    url = f"{SUPABASE_URL}/rest/v1/loom_media?id=eq.{media_id}"
    data = json.dumps(updates).encode()
    headers = _supabase_headers()
    headers["Content-Type"] = "application/json"
    headers["Prefer"] = "return=representation"
    req = Request(url, data=data, headers=headers, method="PATCH")
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
    """payload: clip_id, job_id, ready_file, master_asset, source_title,
    proposed_shorts_title, caption, hook, overall_score, decision,
    duration_seconds, source_url, tenant_id, run_id, captions_applied,
    hook_applied, no_speech, transcript_segment_count, render_version,
    asset_version, review_stage, rights_policy (optional),
    publication_enabled (optional)."""
    clip_id = payload["clip_id"]
    job_id = payload.get("job_id", "")
    tenant_id = payload.get("tenant_id")
    asset_version = payload.get("asset_version")
    review_stage = payload.get("review_stage") or "moment"
    if review_stage not in REVIEW_STAGES:
        review_stage = "moment"
    ready_file = payload.get("ready_file") or payload.get("master_asset")
    # Locate master on disk (renderer-declared final asset).
    master_path = payload.get("master_path")
    if not master_path and job_id and ready_file:
        master_path = os.path.join(OPENSHORTS_OUTPUT, job_id, os.path.basename(ready_file))
    if not master_path or not os.path.exists(master_path):
        raise RuntimeError(f"Master asset not found: {master_path}")

    channel_id = resolve_clip_review_channel()

    # Register opaque token -> full identity (durable, server-side, never in
    # custom_id). Register BEFORE sending so the card is resolvable immediately.
    identity = {
        "tenant_id": tenant_id,
        "clip_id": clip_id,
        "asset_version": asset_version,
        "review_stage": review_stage,
    }
    token = secrets.token_urlsafe(9)  # ~12 chars, opaque, non-guessable
    register_review_token(token, identity)

    # 1) Generate review proxy (master untouched).
    tmpdir = tempfile.mkdtemp(prefix="loom-review-")
    try:
        proxy_path = generate_review_proxy(master_path, tmpdir)
        proxy_name = f"review_{clip_id.replace(':', '_')}.mp4"
        proxy_size = os.path.getsize(proxy_path)

        # 2) Upload as Discord attachment + post message with buttons.
        approve_id = make_custom_id(token, "approve")
        reject_id  = make_custom_id(token, "reject")
        hold_id    = make_custom_id(token, "hold")
        edit_id    = make_custom_id(token, "edit")

        # Source title vs proposed Shorts title: shown SEPARATELY. The
        # imported source-video title is NOT the candidate title.
        source_title = payload.get("source_title") or "Unavailable"
        proposed_title = payload.get("proposed_shorts_title") or "Not generated"

        # Stage-aware labeling (never show a moment approval as publication).
        is_final = review_stage == "final_publication"
        stage_heading = "Final Review — Approve for Publication" if is_final \
            else "Moment Review — Approve for Reaction Queue"
        approve_label = "Approve for Publication" if is_final \
            else "Approve for Reaction Queue"

        # Display safety: mask explicit terms on the card; preserve raw
        # internally (already persisted in Supabase by the pipeline).
        hook_raw = payload.get("hook") or ""
        caption_raw = payload.get("caption") or ""
        hook_display, hook_terms = mask_explicit(hook_raw)
        caption_display, caption_terms = mask_explicit(caption_raw)
        explicit_terms = sorted(set(hook_terms) | set(caption_terms))

        fields = [
            {"name": "Review", "value": stage_heading, "inline": False},
            {"name": "Source title", "value": (source_title or "Unavailable")[:1000], "inline": False},
            {"name": "Proposed Shorts title", "value": (proposed_title or "Not generated")[:1000], "inline": False},
            {"name": "Hook", "value": (hook_display or "—")[:400], "inline": False},
            {"name": "Caption", "value": (caption_display or "—")[:1000], "inline": False},
            {"name": "LOOM Score", "value": str(payload.get("overall_score", "—")), "inline": True},
            {"name": "Disposition", "value": str(payload.get("decision") or "—"), "inline": True},
            {"name": "Duration", "value": f"{payload.get('duration_seconds', '—')}s", "inline": True},
            {"name": "Source", "value": (payload.get("source_url") or "—")[:800], "inline": False},
            {"name": "Tenant", "value": str(tenant_id or "—"), "inline": True},
            {"name": "Run ID", "value": str(payload.get("run_id") or "—"), "inline": True},
            {"name": "Clip ID", "value": str(clip_id), "inline": True},
            {"name": "Asset version", "value": str(asset_version or "—"), "inline": True},
            {"name": "Captions", "value": f"expected={payload.get('captions_expected', '—')} applied={payload.get('captions_applied', '—')}", "inline": True},
            {"name": "Hook", "value": f"expected={payload.get('hook_expected', '—')} applied={payload.get('hook_applied', '—')}", "inline": True},
            {"name": "no_speech", "value": str(payload.get("no_speech", False)), "inline": True},
        ]
        # Explicit-language warning (visible, structured). Never converts a
        # policy-risk candidate into publishable; it is display-only.
        if explicit_terms:
            fields.append({"name": "⚠️ Explicit language",
                           "value": "Detected (masked): " + ", ".join(explicit_terms), "inline": False})
        # Rights/policy warning when already available (never invented here).
        rights = payload.get("rights_policy")
        if rights:
            fields.append({"name": "⚠️ Rights/policy", "value": str(rights)[:400], "inline": False})
        # Publication status (MVP: always disabled unless explicitly enabled).
        pub_enabled = bool(payload.get("publication_enabled"))
        fields.append({"name": "Publication", "value": "enabled" if pub_enabled else "disabled", "inline": True})
        fields.append({"name": "Versions", "value": f"master={payload.get('master_asset') or '—'} | render={payload.get('render_version') or '—'} | proxy={proxy_name}", "inline": False})
        if payload.get("lineage"):
            fields.append({"name": "Lineage", "value": str(payload["lineage"]), "inline": False})
        if payload.get("correction_summary"):
            fields.append({"name": "Corrections", "value": str(payload["correction_summary"])[:800], "inline": False})

        embed = {
            "title": (proposed_title or "LOOM Candidate")[:256],
            "color": 0xC9A227,  # brass/gold
            "fields": fields,
        }

        components = [{
            "type": 1,
            "components": [
                {"type": 2, "style": 3, "label": approve_label, "custom_id": approve_id},
                {"type": 2, "style": 4, "label": "Reject", "custom_id": reject_id},
                {"type": 2, "style": 2, "label": "Hold", "custom_id": hold_id},
                {"type": 2, "style": 1, "label": "Edit", "custom_id": edit_id},
            ],
        }]

        message_payload = {
            "content": f"🎬 **{stage_heading}** — {clip_id}",
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

        # Record the Discord message id for traceability (best-effort).
        update_review_token_message(hash_token(token), message_id)

        return {
            "channel_id": channel_id,
            "message_id": message_id,
            "attachment_url": attachment_url,
            "proxy_name": proxy_name,
            "proxy_size": proxy_size,
            "review_stage": review_stage,
        }
    finally:
        # Delete only the local throwaway proxy; preserve master + metadata.
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Decision persistence (idempotent; first terminal decision wins per stage)
# ---------------------------------------------------------------------------

def persist_decision(identity, decision, reviewer, category=None, instructions=None,
                     asset_url=None):
    """Persist a human decision to Supabase loom_reviews. Idempotent and
    first-terminal-decision-wins AT THE DATABASE LEVEL per reaction-lane stage:
    the INSERT uses ON CONFLICT (tenant_id,clip_id,asset_version,review_stage)
    DO NOTHING, so a duplicate delivery or a second click cannot create a
    second decision or overwrite the first. Returns (row, created)."""
    final_status = {"approve": "approved", "reject": "rejected", "hold": "on_hold"}[decision]
    payload = {
        "tenant_id": identity["tenant_id"],
        "clip_id": identity["clip_id"],
        "asset_version": identity["asset_version"],
        "review_stage": identity["review_stage"],
        "asset_url": asset_url,
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
    existing = supabase_get_review(identity)
    if existing:
        return existing[0], False
    return payload, True


def approve_publication(identity, review_row):
    """Media publication guard. Only a final_publication approval with exactly
    ONE matching non-fixture loom_media row may mark the asset
    publication-approved. Missing / mismatched / ambiguous / multiple / fixture
    matches are all rejected (no publication state change)."""
    tenant_id, clip_id, asset_version = (identity["tenant_id"], identity["clip_id"],
                                          identity["asset_version"])
    if not (tenant_id and clip_id and asset_version):
        return {"ok": False, "reason": "missing identity"}
    media = supabase_query_media(identity)
    if len(media) != 1:
        return {"ok": False, "reason": f"{len(media)} matching media rows (need exactly 1)"}
    row = media[0]
    if row.get("storage_status") == "fixture":
        return {"ok": False, "reason": "asset is a fixture (not publication-eligible)"}
    review_id = review_row.get("id")
    status, _ = supabase_patch_media(row["id"], {
        "publication_status": "approved",
        "publication_review_id": review_id,
        "publication_approved_at": datetime.now(timezone.utc).isoformat(),
    })
    ok = status in (200, 204)
    return {"ok": ok, "media_id": row["id"], "reason": None if ok else f"patch status {status}"}


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
        # Dev-scaffold CORS for the editor exchange. Discord interactions are
        # server-to-server and unaffected. Final Activity path uses the SDK.
        self.send_header("Access-Control-Allow-Origin", EDITOR_ORIGIN)
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", EDITOR_ORIGIN)
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", "0")
        self.end_headers()

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
        path = self.path.rstrip("/") or "/"
        if path == "/health":
            self._send_json(200, {"status": "ok"})
            return
        # Tailscale Funnel --set-path strips the public prefix before proxying,
        # so the adapter sees "/" for the public /discord/interactions route.
        # Accept both the bare root and the full path.
        if path in ("/discord/interactions", "/"):
            self._handle_interaction()
            return
        if path == "/discord/card":
            self._handle_card_send()
            return
        if path == "/discord/editor/exchange":
            self._handle_editor_exchange()
            return
        if path == "/discord/editor/complete":
            self._handle_editor_complete()
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

    def _resolve_identity(self, parsed):
        token = parsed.get("t")
        return resolve_review_token(token) if token else None

    def _handle_button(self, interaction):
        data = interaction.get("data", {})
        custom_id = data.get("custom_id", "")
        parsed = parse_custom_id(custom_id)
        if not parsed:
            self._send_json(200, {"type": 4, "data": {"content": "Invalid button.", "flags": 64}})
            return

        decision = parsed.get("d")
        resolved = self._resolve_identity(parsed)
        if decision not in ("approve", "reject", "hold", "edit") or not resolved:
            self._send_json(200, {"type": 4, "data": {"content": "Invalid action.", "flags": 64}})
            return
        identity = resolved["identity"]
        token_hash = resolved["token_hash"]

        reviewer = self._reviewer_name(interaction)

        if decision == "edit":
            self._handle_edit(interaction, identity, token_hash, reviewer)
            return

        if decision == "reject":
            # Open a modal to collect a structured category + optional notes.
            modal_custom_id = make_custom_id(parsed["t"], "reject")
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
        self._decide_and_update(interaction, identity, token_hash, decision, reviewer,
                                category=None, instructions=None)

    def _handle_edit(self, interaction, identity, token_hash, reviewer):
        """Edit is NOT a terminal review decision. Mint a single-use 15-minute
        editor token (the review token stays 'pending') and return the editor
        launch. Dev scaffold uses a deep-link; the final endpoint is a Discord
        Activity (same token validated server-side). Only the token hash is
        stored; the raw token is short-lived and single-use."""
        editor_token = secrets.token_urlsafe(18)  # ~24 chars, opaque
        status = register_editor_token(editor_token, identity, reviewer)
        if status not in (200, 201):
            self._send_json(200, {"type": 4, "data": {
                "content": "⚠️ Could not create an editor session (is migration 012 applied?).",
                "flags": 64}})
            return

        clip_id = identity["clip_id"]
        job_id, sep, clip_index = clip_id.rpartition(":")
        if not sep:
            job_id, clip_index = clip_id, "0"
        base = EDITOR_BASE_URL.rstrip("/")
        launch_url = f"{base}/edit?job_id={job_id}&clip_index={clip_index}&token={editor_token}"

        self._send_json(200, {"type": 4, "data": {
            "content": (
                f"✏️ **Edit session opened** (single-use, 15 min)\n"
                f"Tenant `{identity['tenant_id']}` · `{clip_id}` · `{identity['asset_version']}`\n"
                f"Open the editor: {launch_url}\n"
                f"_(The review stays open; the edited render becomes a NEW asset version for re-review.)_"
            ),
            "flags": 64}})

    def _handle_editor_exchange(self):
        """One-time launch-token exchange. Consumes the single-use token
        immediately and returns the editor session identity. The raw token never
        leaves this exchange; the editor strips it from its URL via
        history.replaceState and applies Referrer-Policy: no-referrer."""
        try:
            raw = self._read_body()
            body = json.loads(raw.decode() or "{}")
        except Exception:
            self._send_json(400, {"ok": False, "error": "bad json"})
            return
        token = body.get("token")
        if not token:
            self._send_json(400, {"ok": False, "error": "missing token"})
            return
        session = resolve_editor_token(token)
        if not session:
            self._send_json(403, {"ok": False, "error": "invalid, consumed, or expired token"})
            return
        clip_id = session["clip_id"]
        job_id, sep, clip_index = clip_id.rpartition(":")
        if not sep:
            job_id, clip_index = clip_id, "0"
        session["job_id"] = job_id
        session["clip_index"] = clip_index
        self._send_json(200, {"ok": True, "session": session})

    def _handle_editor_complete(self):
        """Finalize an edit: atomically register the new asset_version (RPC) and
        send a fresh final_publication review card for that exact version. The
        edited render is NOT promoted/approved/distributed — it is
        final_review_pending until the new card is approved."""
        try:
            raw = self._read_body()
            body = json.loads(raw.decode() or "{}")
        except Exception:
            self._send_json(400, {"ok": False, "error": "bad json"})
            return

        edit_session_id = body.get("edit_session_id")
        if not edit_session_id:
            self._send_json(400, {"ok": False, "error": "missing edit_session_id"})
            return

        # Pre-flight: validate the active, already-exchanged session before
        # invoking the RPC. The browser/Discord Activity NEVER calls the RPC
        # directly — only this endpoint does, after validation.
        session = resolve_edit_session(edit_session_id)
        if not session:
            self._send_json(403, {"ok": False, "error": "invalid or inactive editor session"})
            return

        rpc_payload = {
            "p_edit_session_id": edit_session_id,
            "p_content_type": body.get("content_type") or "video/mp4",
            "p_file_hash": body.get("file_hash"),
            "p_size_bytes": body.get("size_bytes"),
            "p_trim_start": body.get("trim_start"),
            "p_trim_end": body.get("trim_end"),
            "p_caption_changes": body.get("caption_changes"),
            "p_crop_reframe_changes": body.get("crop_reframe_changes"),
            "p_instruction": body.get("instruction"),
            "p_llm_assist_used": bool(body.get("llm_assist_used")),
            "p_edl_metadata": body.get("edl_metadata"),
        }
        try:
            status, result = register_edited_asset_rpc(rpc_payload)
        except Exception as e:
            self._send_json(502, {"ok": False, "error": f"registration RPC failed: {e}"})
            return
        if status not in (200, 201) or not isinstance(result, dict):
            self._send_json(502, {"ok": False, "error": f"registration failed (status {status})"})
            return

        # Idempotent replay: the session was already completed; no new card.
        if result.get("idempotent"):
            self._send_json(200, {"ok": True, "idempotent": True, "result": result})
            return

        new_version = result.get("asset_version")
        source_version = result.get("source_asset_version")
        clip_id = result.get("clip_id")
        tenant_id = result.get("tenant_id")
        job_id = body.get("job_id", "")
        ready_file = body.get("ready_file", "")

        try:
            card = send_review_card({
                "clip_id": clip_id,
                "job_id": job_id,
                "tenant_id": tenant_id,
                "asset_version": new_version,
                "review_stage": "final_publication",
                "ready_file": ready_file,
                "proposed_shorts_title": body.get("proposed_title") or "Edited candidate",
                "hook": body.get("hook") or "",
                "caption": body.get("caption") or "",
                "overall_score": body.get("overall_score") or "—",
                "duration_seconds": body.get("duration_seconds") or 0,
                "source_url": body.get("source_url") or "—",
                "lineage": f"{source_version} → {new_version}",
                "correction_summary": body.get("correction_summary") or "manual edit (trim/reframe/caption)",
            })
        except Exception as e:
            self._send_json(200, {"ok": True, "result": result, "card_error": str(e)})
            return

        self._send_json(200, {"ok": True, "result": result, "card": card})

    def _handle_modal_submit(self, interaction):
        data = interaction.get("data", {})
        custom_id = data.get("custom_id", "")
        parsed = parse_custom_id(custom_id)
        if not parsed or parsed.get("d") != "reject":
            self._send_json(200, {"type": 4, "data": {"content": "Invalid modal.", "flags": 64}})
            return

        resolved = self._resolve_identity(parsed)
        if not resolved:
            self._send_json(200, {"type": 4, "data": {"content": "Unknown candidate.", "flags": 64}})
            return
        identity = resolved["identity"]
        token_hash = resolved["token_hash"]

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
        self._decide_and_update(interaction, identity, token_hash, "reject", reviewer,
                                category=category, instructions=instructions)

    def _decide_and_update(self, interaction, identity, token_hash, decision, reviewer,
                           category, instructions):
        clip_id = identity["clip_id"]
        scores = supabase_get("loom_scores", clip_id)
        # tenant_id is authoritative from the token; fall back to the ledger.
        tenant_id = identity.get("tenant_id") or (scores[0].get("tenant_id") if scores else None)
        asset_url = (scores[0].get("asset_url") if scores else None)
        identity = {**identity, "tenant_id": tenant_id}

        row, created = persist_decision(
            identity, decision, reviewer, category=category,
            instructions=instructions, asset_url=asset_url)

        # Publication guard: only a final_publication approval may touch media
        # publication state; moment decisions never do.
        pub = None
        if decision == "approve" and identity["review_stage"] == "final_publication":
            pub = approve_publication(identity, row)

        # Atomically consume the token (first terminal decision wins).
        if token_hash:
            consume_review_token(token_hash)

        # Forward the verified normalized event to n8n (idempotent there too).
        forward_to_n8n({
            "clip_id": identity["clip_id"],
            "tenant_id": identity["tenant_id"],
            "asset_version": identity["asset_version"],
            "review_stage": identity["review_stage"],
            "decision": decision,
            "reviewer": reviewer,
            "rejection_category": category if decision == "reject" else None,
            "reviewer_instructions": instructions,
            "asset_url": asset_url,
            "publication_updated": bool(pub and pub.get("ok")),
        })

        # Build the "decided" card update: show reviewer/decision/timestamp,
        # disable controls. Never display a moment approval as "publication".
        ts = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
        final_status = row.get("final_status") or {
            "approve": "approved", "reject": "rejected", "hold": "on_hold"}[decision]
        is_final = identity["review_stage"] == "final_publication"
        if decision == "approve":
            decision_label = "approve (publication)" if is_final else "approve (reaction queue)"
        else:
            decision_label = f"{decision} ({final_status})"

        fields = [
            {"name": "Decision", "value": decision_label, "inline": True},
            {"name": "Review", "value": "Final (publication)" if is_final else "Moment", "inline": True},
            {"name": "Reviewer", "value": reviewer, "inline": True},
            {"name": "Timestamp", "value": ts, "inline": True},
        ]
        if pub is not None and not pub.get("ok"):
            fields.append({"name": "⚠️ Publication", "value": f"Not approved: {pub.get('reason')}", "inline": False})
        elif pub is not None and pub.get("ok"):
            fields.append({"name": "Publication", "value": "Media marked publication-approved", "inline": False})
        if decision == "reject" and category:
            fields.append({"name": "Category", "value": category, "inline": True})
        if instructions:
            fields.append({"name": "Instructions", "value": instructions, "inline": False})

        decided_embed = {
            "title": "✅ Decision recorded" if created else "ℹ️ Already decided",
            "color": 0x2ecc71 if decision == "approve" else (0xe74c3c if decision == "reject" else 0xf1c40f),
            "fields": fields,
        }

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
