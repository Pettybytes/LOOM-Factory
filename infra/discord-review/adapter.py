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
import ssl
import subprocess
import tempfile
import threading
import traceback
import time
import uuid
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import discord_media  # tier-aware upload limit + poster fallback (shared with the acquisition path)
from urllib.request import Request, urlopen
from urllib.parse import urlparse
from urllib.error import HTTPError, URLError

import source_review  # noqa: E402  (same-dir module; Source Review Inbox)
import manual_intake  # noqa: E402  (same-dir module; Manual Source Intake)
import manual_media_intake  # noqa: E402  (same-dir module; V1.2 supplied-media ingress)
import manual_intake_intelligence  # noqa: E402  (same-dir module; Content Intelligence on supplied media)
import tenant_routing  # noqa: E402  (same-dir module; tenant assignment/reassignment)
import intake_card  # noqa: E402  (same-dir module; card-based intake wizard)
import acquisition_ops  # noqa: E402
import library_root as _library_root  # noqa: E402  (approved acquisition lane operator surface)
import capcut_runner  # noqa: E402  (OPTIONAL "Finish in CapCut" lane; never required)


def _source_digest() -> str:
    """SHA-256 of THIS file as the running process loaded it.

    Runtime provenance: a claim that the live service is serving CURRENT HEAD is
    otherwise only inferable from a PID's age. Reported by /health, it is checkable
    from outside — compare it with `shasum -a 256 adapter.py` on disk and equality
    proves the running process loaded exactly that file.
    """
    try:
        import hashlib as _hashlib
        with open(__file__, "rb") as _fh:
            return _hashlib.sha256(_fh.read()).hexdigest()
    except Exception:  # noqa: BLE001 — provenance must never break startup
        return ""


SOURCE_SHA256 = _source_digest()
LOADED_AT = datetime.now(timezone.utc).isoformat()

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
# GHL Media Storage (filesafe CDN) — the canonical durable media store for LOOM.
# register_clip_media persists the clean pre-hook/pre-subtitle source + the burned
# final here at ingest time so hook replacement survives OpenShorts job eviction.
# LOOM_GHL_TOKEN is a single-location Private Integration Token (PIT).
GHL_TOKEN           = _env("LOOM_GHL_TOKEN", required=False) or None
GHL_LOCATION_ID     = _env("LOOM_GHL_LOCATION_ID", "GNhV1OyhFLzcl67XwTBV")
GHL_MEDIA_API       = "https://services.leadconnectorhq.com"

# Truthful storage fact for an EDITED version: the edit path registers no
# media at all (no canonical url, no GHL media id), because later versions
# re-render from the durable clean source. Recording the column default
# ("uploaded") would be a storage claim that was never made.
STORAGE_NOT_APPLICABLE = "not_applicable"
LISTEN_HOST         = _env("LOOM_DISCORD_LISTEN_HOST", "127.0.0.1")
LISTEN_PORT         = int(_env("LOOM_DISCORD_LISTEN_PORT", "8090"))
CLIP_REVIEW_CHANNEL_NAME = _env("LOOM_CLIP_REVIEW_CHANNEL", "clip-review")
# Manual Intelligence Intake V1.2 — ingress channel is distinct from #source-review:
# supplied media is dropped here, review candidates still appear in #source-review.
MANUAL_INTAKE_CHANNEL_NAME = _env("LOOM_MANUAL_INTAKE_CHANNEL", "manual-intake")
# Bound by ID (preferred): the display name is decorative and may carry an emoji.
MANUAL_INTAKE_CHANNEL_ID = _env("LOOM_MANUAL_INTAKE_CHANNEL_ID", "")
MANUAL_MEDIA_COMMAND = "intake-media"           # MESSAGE context-menu command
MANUAL_MEDIA_TENANT_DEFAULT = _env("LOOM_MANUAL_INTAKE_TENANT", "dreemzy")
MANUAL_MEDIA_FOLDER_ID = _env("LOOM_GHL_INTAKE_FOLDER_ID", "")
# Canonical GHL Media Storage folder for LOOM-created assets — the SAME setting as
# above (one source of truth, no second env var). The PROVEN folder-targeting
# contract for POST /medias/upload-file is the multipart form field `parentId`
# carrying the target folder id. `folderId` is NOT honoured: GHL answers HTTP 201,
# silently ignores it, and stores the asset in My Media root (probed live
# 2026-09-26 against the LOOM folder 6aa61446df2d015553434919).
GHL_MEDIA_PARENT_ID = MANUAL_MEDIA_FOLDER_ID
# Post-Content-Intelligence manual candidates are CONTENT/CANDIDATE review, not
# source-acquisition review: they must never land in #source-review. Reuse the
# existing clip-review destination (or an explicit override id).
MANUAL_REVIEW_CHANNEL_ID = _env("LOOM_MANUAL_REVIEW_CHANNEL_ID", "")
MANUAL_MEDIA_MAX_BYTES = int(_env("LOOM_MANUAL_MEDIA_MAX_BYTES", str(100 * 1024 * 1024)))
PROXY_MAX_BYTES     = 23 * 1024 * 1024  # Discord upload ceiling (Boosts raise it; stay safe)
EDITOR_BASE_URL     = _env("LOOM_EDITOR_BASE_URL", "http://localhost:5173")  # dev scaffold (legacy); critical path = external HTTPS /edit
EDITOR_PUBLIC_URL   = _env("LOOM_EDITOR_PUBLIC_URL", "")  # external HTTPS editor origin for the Discord Edit deep-link (NOT localhost, NOT the Activity)
EDITOR_ORIGIN       = _env("LOOM_EDITOR_ORIGIN", "http://localhost:5173")  # dev CORS allow (legacy; exact allowlist below)
# Exact-origin CORS allowlist for the editor / OAuth endpoints. Comma-separated.
# Discord interactions are server-to-server (no CORS); only the browser editor
# needs cross-origin access in the dev scaffold. In production the Activity is
# same-origin behind the funnel, so CORS is effectively unused.
EDITOR_CORS_ORIGINS = tuple(
    o.strip() for o in _env("LOOM_EDITOR_CORS_ORIGINS", "http://localhost:5173").split(",") if o.strip()
)
# Discord OAuth2 client secret — used ONLY server-side for the authorization-code
# exchange. Never sent to the browser; never embedded in the frontend bundle.
DISCORD_CLIENT_SECRET = _env("LOOM_DISCORD_CLIENT_SECRET", "") or None
# OAuth2 redirect URI registered in the Discord Developer Portal for the Activity.
DISCORD_OAUTH_REDIRECT_URI = _env("LOOM_DISCORD_OAUTH_REDIRECT_URI", "") or None

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
EDITOR_TOKEN_TTL_SECONDS = 15 * 60     # 15 min single-use LAUNCH window (pending -> authenticated).
EDITOR_SESSION_TTL_SECONDS = 4 * 3600  # 4h authenticated editor session (draft stays resumable).

# ---------------------------------------------------------------------------
# Operator-facing intake failure classification.
#
# Raw provider stderr stays in adapter.log; Discord gets ONE stable code plus a
# plain-language reason. This exists because the previous error ("supplied file
# has no video stream") pointed the operator at their input when the real fault
# was a truncated download.
# ---------------------------------------------------------------------------
_INTAKE_FAILURE_REASONS: dict[str, str] = {
    "DOWNLOAD_INCOMPLETE":
        "the download did not finish — an incomplete transfer was detected.",
    "DOWNLOAD_FAILED":
        "the source could not be downloaded.",
    "SPLIT_STREAM_UNFINISHED":
        "the video and audio streams downloaded but never merged into one file.",
    "NO_FINALIZED_ARTIFACT":
        "the extractor produced no finalized media file.",
    "MERGE_FAILED":
        "the downloaded streams could not be merged.",
    "MEDIA_INTEGRITY_FAILED":
        "the media is truncated or corrupt, so it was not stored.",
    "NO_VIDEO_STREAM":
        "the source has no video stream LOOM can use.",
    "BAD_DURATION":
        "the media has no usable duration, so it cannot be reasoned about.",
    "WINDOW_REJECTED":
        "the requested start/end window was refused (over the clip limit or outside the source).",
    "WINDOW_INCOMPLETE":
        "a bounded intake needs BOTH a start and an end.",
    "WINDOW_MALFORMED":
        "a window was requested but could not be understood — supply a valid START and END.",
    "WINDOW_EXTRACTION_FAILED":
        "the requested window could not be extracted from the supplied file.",
    "WINDOW_MISMATCH":
        "the extracted media does not match the requested window.",
    "IDENTITY_MISMATCH":
        "the source identity conflictually differs from the identity submitted.",
    "MEDIA_EXCEEDS_LIMIT":
        "the media exceeds the intake size limit, even at the lowest quality tried.",
    "LONG_FORM_REQUIRES_WINDOW":
        "this source is longer than the clip limit, so choose SELECT SEGMENT and give "
        "a start/end window — the whole source is never downloaded.",
    "DURABLE_STORE_FAILED":
        "the durable copy could not be stored (storage transport failed). The working "
        "source was kept, so re-running the command is safe.",
    "TRANSPORT_FAILED":
        "a network transport call failed (timeout/reset/TLS) — this is a connection "
        "problem, not a problem with the media. Nothing was acquired; re-run the "
        "command.",
    "PROBE_FAILED":
        "the media could not be inspected.",
    "UNKNOWN":
        "an unexpected error occurred; the adapter log has the detail.",
}


def _is_transient_transport_error(exc: Exception) -> bool:
    """Did this failure come from the NETWORK transport rather than the media?

    A Discord interaction PATCH (or any other HTTP call) can fail on a TLS handshake
    timeout, a connection reset or a broken pipe. Those are transient CONNECTION
    failures and are deliberately NOT classified as media problems. ``HTTPError`` is
    excluded: a real HTTP status is a response from the server, not a transport
    failure, and it keeps its own classification.
    """
    seen: set[int] = set()
    current: Any = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, HTTPError):
            return False
        if isinstance(current, (URLError, TimeoutError, ConnectionError, ssl.SSLError)):
            return True
        current = getattr(current, "reason", None) or getattr(current, "__cause__", None)
    return False


def classify_intake_failure(exc: Exception, stage: str = "") -> tuple[str, str]:
    """(code, operator_reason) — concise, truthful, never a raw stderr dump."""
    code = str(getattr(exc, "code", "") or "")
    if code in _INTAKE_FAILURE_REASONS:
        return code, _INTAKE_FAILURE_REASONS[code]
    if code == "TOO_LARGE":
        return "MEDIA_EXCEEDS_LIMIT", _INTAKE_FAILURE_REASONS["MEDIA_EXCEEDS_LIMIT"]
    if code in ("YTDLP_FAILED", "NO_OUTPUT", "NO_RESULTS"):
        return "DOWNLOAD_FAILED", _INTAKE_FAILURE_REASONS["DOWNLOAD_FAILED"]
    if code in ("FFPROBE_FAILED", "BAD_METADATA", "NO_STREAMS", "BAD_DURATION"):
        return "PROBE_FAILED", _INTAKE_FAILURE_REASONS["PROBE_FAILED"]
    # A transport failure is a CONNECTION failure, never a media failure and never a
    # generic UNKNOWN. Checked only after the explicit code-based classes above, so
    # no classified media refusal is re-labelled here.
    if _is_transient_transport_error(exc):
        return "TRANSPORT_FAILED", _INTAKE_FAILURE_REASONS["TRANSPORT_FAILED"]
    text = str(exc).lower()
    if "exceeds cap" in text:
        return "MEDIA_EXCEEDS_LIMIT", _INTAKE_FAILURE_REASONS["MEDIA_EXCEEDS_LIMIT"]
    if "no video stream" in text:
        return "NO_VIDEO_STREAM", _INTAKE_FAILURE_REASONS["NO_VIDEO_STREAM"]
    if "integrity" in text:
        return "MEDIA_INTEGRITY_FAILED", _INTAKE_FAILURE_REASONS["MEDIA_INTEGRITY_FAILED"]
    if isinstance(exc, ValueError) or "ffprobe" in text:
        return "PROBE_FAILED", _INTAKE_FAILURE_REASONS["PROBE_FAILED"]
    return "UNKNOWN", _INTAKE_FAILURE_REASONS["UNKNOWN"]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Manual Intake — segment choice (WHOLE VIDEO / SELECT SEGMENT)
#
# A long-form source must not be silently acquired whole, so the moment LOOM knows a
# window is required it offers the operator an explicit choice instead of failing or
# guessing. The button/modal interactions that follow carry NO source context of
# their own, so the context is remembered under a short opaque ref and only that ref
# travels in the custom_id — never the URL, never a local path, so no source identity
# (and no filesystem detail) is exposed in a Discord identifier.
# ---------------------------------------------------------------------------
MI_CHOICE_PREFIX = "MI"
MI_ACT_SEGMENT = "seg"
MI_ACT_WHOLE = "whole"

# The gate's OWN refusal vocabulary, rendered for an operator. The choice surface never
# invents a second reason: it explains the refusal the contract actually produced.
_MI_REFUSAL_REASONS = {
    "LONG_FORM_REQUIRES_WINDOW":
        "it is longer than the working-window limit, so only a bounded segment "
        "can be taken",
    "UNKNOWN_SOURCE_DURATION":
        "its length could not be proven, so it cannot be taken whole",
}
_MI_REFUSAL_DEFAULT = "the safety contract does not allow this source whole"

# Stages that are genuinely TRANSIENT to re-run, so "Retry safe: yes" is honest. A
# refusal whose cause is the input itself needs the input changed first, not a retry.
_MI_RETRY_SAFE_CODES = frozenset({
    "DURABLE_STORE_FAILED", "DOWNLOAD_FAILED", "PROBE_FAILED", "UNKNOWN",
    "MEDIA_INTEGRITY_FAILED", "REVIEW_CARD_FAILED", "TRANSPORT_FAILED",
})

#: Stages whose failure is an INTERACTION UPDATE delivery problem, not acquisition.
#: Named distinctly so a Discord transport error can never be reported as an
#: acquisition failure.
MI_STAGE_INTERACTION_PATCH = "interaction_patch"
_MI_PATCH_STAGES = frozenset({MI_STAGE_INTERACTION_PATCH})

_MI_PENDING: dict[str, dict] = {}
_MI_PENDING_TTL = 15 * 60
_MI_PENDING_LOCK = threading.Lock()


def parse_manual_choice_custom_id(custom_id: str) -> tuple[str, str] | None:
    """(action, ref) for a Manual Intake choice button, else None."""
    parts = (custom_id or "").split(":")
    if len(parts) == 3 and parts[0] == MI_CHOICE_PREFIX and parts[1] and parts[2]:
        return parts[1], parts[2]
    return None


def _mi_range_text(parsed: dict) -> str:
    """The operator's ORIGINAL range, for a status line. Empty when whole-source."""
    start_s, end_s = parsed.get("start_s"), parsed.get("end_s")
    if isinstance(start_s, (int, float)) and isinstance(end_s, (int, float)):
        return (f" {int(start_s) // 60:02d}:{int(start_s) % 60:02d}"
                f"–{int(end_s) // 60:02d}:{int(end_s) % 60:02d}")
    return ""


def _mi_remember_pending(*, url: str, tenant: str, submitter: str,
                         channel_id: str,
                         attachment: dict | None = None,
                         local_path: str = "",
                         start_hint_ms: int = 0,
                         platform: str = "",
                         source_id: str = "",
                         item_kind: str = "") -> str:
    """Remember the operator's source so a later button/modal can resume it.

    ``local_path`` lets a SUPPLIED file resume without re-downloading it: the file is
    already the operator's own material, so it is kept until the choice resolves and
    the transient copy is released when the entry expires.

    ``start_hint_ms`` is the source URL's NON-IDENTITY start timestamp (Twitch's
    ``?t=``), carried so the segment modal can PRE-FILL Start. It is a default the
    operator can overwrite, never an approved boundary and never part of identity.

    ``platform``/``source_id``/``item_kind`` are the source's PUBLIC identity (the same
    id the durable filename already carries). They are remembered so the later
    interaction half of the route can log the SAME identity its offer logged — without
    them, a lost click and an unentered executor look identical in the log.
    """
    ref = secrets.token_urlsafe(8).replace("-", "").replace("_", "")[:10]
    now = time.time()
    with _MI_PENDING_LOCK:
        for stale in [r for r, v in _MI_PENDING.items()
                      if now - v.get("created_at", 0) > _MI_PENDING_TTL]:
            entry = _MI_PENDING.pop(stale, None)
            _mi_release_local(entry)
        _MI_PENDING[ref] = {"url": url, "tenant": tenant, "submitter": submitter,
                            "channel_id": channel_id,
                            "attachment": dict(attachment or {}),
                            "local_path": local_path,
                            "start_hint_ms": int(start_hint_ms or 0),
                            "platform": platform,
                            "source_id": source_id,
                            "item_kind": item_kind,
                            "created_at": now}
    return ref


def _mi_trace(stage: str, *, platform: str = "", source_id: str = "",
              **fields: object) -> None:
    """One `[intake-trace]` line for ONE stage of the Manual Intake route.

    The chooser split the route in two — an offer, then a LATER interaction — and the
    only link between the halves is an opaque ref. Correlating the halves by hand was
    impossible, which is why "the click did nothing" could not be told apart from "the
    executor never ran". This prints the SAME public source identity at every stage, so
    "where did it stop?" is answerable from the log alone.

    Never logs credentials, API keys, session tokens, auth headers, signed private URLs
    or the full source URL: the identity is the platform's PUBLIC content id, the same
    one the durable filename already carries. Never raises — a trace must not break the
    work it reports on.
    """
    try:
        parts = ["[intake-trace]", f"stage={stage}"]
        if platform:
            parts.append(f"platform={platform}")
        if source_id:
            parts.append(f"source_id={source_id}")
        for key, value in fields.items():
            if value is None or value == "":
                continue
            parts.append(f"{key}={value}")
        print(" ".join(parts), flush=True)
    except Exception:  # noqa: BLE001 — a trace must never break intake
        pass


def _mi_identity(parsed: dict) -> tuple[str, str]:
    """(platform, stable public source id) for a submission, else ("", "").

    Diagnostic only: this performs the SAME local canonicalisation the intake contract
    performs, and it never reports a refusal — the classified error is raised later, by
    the one authority that owns it. Never raises.
    """
    url = str((parsed or {}).get("url") or "")
    if not url:
        return "", ""
    try:
        resolved = manual_intake.validate_and_canonicalize(url)
        return (str(resolved.get("platform") or ""),
                str(resolved.get("content_id") or ""))
    except Exception:  # noqa: BLE001 — identity is diagnostic; the real error comes later
        return "", ""


def _mi_release_local(entry: dict | None) -> None:
    """Release a retained supplied-file copy. Never raises, never touches the master."""
    path = (entry or {}).get("local_path") or ""
    if not path:
        return
    try:
        parent = os.path.dirname(path)
        if parent and os.path.basename(parent).startswith("loom-manual-intake-"):
            manual_media_intake.cleanup(parent)
        else:
            manual_media_intake.cleanup(path)
    except Exception:  # noqa: BLE001 — cleanup is best-effort
        pass


def _mi_pending(ref: str) -> dict | None:
    with _MI_PENDING_LOCK:
        entry = _MI_PENDING.get(ref)
        if not entry:
            return None
        if time.time() - entry.get("created_at", 0) > _MI_PENDING_TTL:
            _MI_PENDING.pop(ref, None)
            _mi_release_local(entry)
            return None
        return dict(entry)


def _mi_forget_pending(ref: str, *, release: bool = False) -> None:
    with _MI_PENDING_LOCK:
        entry = _MI_PENDING.pop(ref, None)
    if release:
        _mi_release_local(entry)


def allowed_cors_origin(request_origin: str):
    """Exact-origin CORS allowlist. Returns the request Origin if (and only if)
    it exactly matches a configured origin; otherwise None (no CORS header is
    emitted, so the browser blocks the cross-origin read). Never '*'."""
    if not request_origin:
        return None
    for allowed in EDITOR_CORS_ORIGINS:
        if request_origin == allowed:
            return allowed
    return None


def discord_oauth_exchange(code: str):
    """Server-side Discord OAuth2 authorization-code exchange. Returns the
    access_token, or None. The client secret never leaves this function."""
    if not DISCORD_CLIENT_SECRET:
        return None
    redirect = DISCORD_OAUTH_REDIRECT_URI
    data = {
        "client_id": DISCORD_APP_ID,
        "client_secret": DISCORD_CLIENT_SECRET,
        "grant_type": "authorization_code",
        "code": code,
    }
    if redirect:
        data["redirect_uri"] = redirect
    # Discord's token endpoint requires form-encoded, NOT JSON.
    from urllib.parse import urlencode
    body = urlencode(data).encode()
    headers = {"Content-Type": "application/x-www-form-urlencoded", "User-Agent": "LOOMReviewAdapter/1.0"}
    req = Request(f"{DISCORD_API}/oauth2/token", data=body, headers=headers, method="POST")
    try:
        with urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read().decode() or "{}")
    except HTTPError as e:
        return None
    except Exception:
        return None
    return result.get("access_token")


def discord_user_lookup(access_token: str):
    """Resolve an OAuth2 access_token to the authenticated Discord user id
    (server-side, via Discord /users/@me). Returns the numeric user id string or
    None. The user id is NEVER trusted from the client — it is always derived
    here from the token that Discord issued."""
    headers = {"Authorization": f"Bearer {access_token}", "User-Agent": "LOOMReviewAdapter/1.0"}
    req = Request(f"{DISCORD_API}/users/@me", headers=headers, method="GET")
    try:
        with urlopen(req, timeout=15) as resp:
            user = json.loads(resp.read().decode() or "{}")
    except Exception:
        return None
    uid = user.get("id")
    return str(uid) if uid else None


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def token_fp(token: str) -> str:
    """One-way SHA-256 fingerprint prefix of a token for diagnostics ONLY.
    Never logs or returns the raw token — just the first 12 hex chars of its
    hash, enough to compare two requests without exposing the secret."""
    if not token:
        return "none"
    return hashlib.sha256(token.encode()).hexdigest()[:12]


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


def register_editor_token(token: str, identity: dict, reviewer: str,
                        launch_user_id=None, launch_guild_id=None, launch_channel_id=None):
    """Create a single-use editor-session row. The raw token is used only for
    the editor launch; the DB stores only its SHA-256 hash. 15-minute expiry,
    replay-protected (status flips from 'active' on first use).

    When launched via LAUNCH_ACTIVITY (type 12), the raw token is NOT placed in
    a URL — the session is instead bound to the authenticated Discord user by
    `launch_user_id` (plus guild/channel context) so the Activity can resolve
    its pending edit after OAuth."""
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
    if launch_user_id:
        payload["launch_user_id"] = launch_user_id
    if launch_guild_id:
        payload["launch_guild_id"] = launch_guild_id
    if launch_channel_id:
        payload["launch_channel_id"] = launch_channel_id
    url = f"{SUPABASE_URL}/rest/v1/loom_edit_sessions"
    data = json.dumps(payload).encode()
    headers = _supabase_headers()
    headers["Content-Type"] = "application/json"
    headers["Prefer"] = "return=representation"
    req = Request(url, data=data, headers=headers, method="POST")
    with urlopen(req, timeout=15) as resp:
        body = json.loads(resp.read().decode() or "[]")
        return resp.status, body


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
        "launch_guild_id": row.get("launch_guild_id"),
        "launch_channel_id": row.get("launch_channel_id"),
        # Durable session/version state, so the editor can tell whether this
        # session already produced a version (and must therefore not start a
        # second one). Additive: no caller's behaviour depends on their absence.
        "status": row.get("status"),
        "target_asset_version": row.get("target_asset_version"),
        "draft": row.get("draft"),
    }


def resolve_edit_session(edit_session_id: str):
    """Resolve an editor session (active OR completed) to its identity. A
    completed session is resolved for idempotent completion replay — never
    reopened for new mutations. Requires the launch token to have been consumed
    (consumed_at set). Returns the session identity + status + target, or None."""
    url = (f"{SUPABASE_URL}/rest/v1/loom_edit_sessions"
           f"?id=eq.{edit_session_id}&select=*")
    req = Request(url, headers=_supabase_headers(), method="GET")
    with urlopen(req, timeout=15) as resp:
        rows = json.loads(resp.read().decode() or "[]")
    if not rows:
        return None
    row = rows[0]
    if not row.get("consumed_at"):
        return None  # launch token was never exchanged — reject
    return {
        "edit_session_id": row.get("id"),
        "tenant_id": row.get("tenant_id"),
        "clip_id": row.get("clip_id"),
        "source_asset_version": row.get("source_asset_version"),
        "status": row.get("status"),
        "target_asset_version": row.get("target_asset_version"),
    }


MAX_EDITOR_BODY_BYTES = 1024 * 1024  # 1 MB — subtitle/reframe/rerender bodies are tiny


def mint_session_token(edit_session_id: str):
    """Mint a short-lived opaque editor session token (returned to the editor at
    exchange; its SHA-256 is stored server-side). The raw token is the editor's
    credential for authenticated /api/* calls.

    Minting marks the moment the editor becomes AUTHENTICATED. We extend
    expires_at to the (longer) session TTL here, so the 15-minute LAUNCH window
    never terminates an already-open editor — the durable draft remains
    resumable until it completes or is explicitly expired.

    NEVER rebinds an existing credential. Returns the token, or None."""
    token, _code = mint_session_token_diag(edit_session_id)
    return token


def mint_session_token_diag(edit_session_id: str):
    """Mint a credential and report WHY it could not be minted.

    Returns (token_or_None, code) with code in:
      ok                    — a NEW credential was issued (never a rebind)
      already_authenticated — this session already holds a live credential;
                              no second one is issued, so the open editor's
                              token can never be orphaned
      not_active            — the session row is not active (terminal)
      lookup_failed         — the store could not be read/written

    Two credentials for one session are supported through the append-only
    credential table; the legacy single-column binding is written ONLY when it
    is still NULL (write-once), which is what removes the rebinding defect."""
    if not edit_session_id:
        return None, "not_active"
    token = secrets.token_urlsafe(24)  # ~32 chars, opaque
    token_hash = hash_token(token)
    session_expires_at = (datetime.now(timezone.utc)
                          + timedelta(seconds=EDITOR_SESSION_TTL_SECONDS)).isoformat()

    # 1) Append-only credential table (preferred: concurrent editors each keep
    #    their own credential).
    append = _append_editor_token(edit_session_id, token_hash, session_expires_at)
    if append == "ok":
        return token, "ok"
    if append == "error":
        return None, "lookup_failed"

    # 2) Legacy fallback: bind the single column ONLY if it is unbound.
    url = (f"{SUPABASE_URL}/rest/v1/loom_edit_sessions"
           f"?id=eq.{edit_session_id}&status=eq.{SESSION_STATUS_ACTIVE}"
           f"&session_token_hash=is.null")
    data = json.dumps({"session_token_hash": token_hash,
                       "expires_at": session_expires_at}).encode()
    headers = _supabase_headers()
    headers["Content-Type"] = "application/json"
    headers["Prefer"] = "return=representation"
    req = Request(url, data=data, headers=headers, method="PATCH")
    try:
        with urlopen(req, timeout=15) as resp:
            bound = json.loads(resp.read().decode() or "[]")
    except HTTPError as e:
        return None, "lookup_failed" if e.code >= 500 else "not_active"
    except URLError:
        return None, "lookup_failed"
    if isinstance(bound, list) and bound:
        return token, "ok"
    # Zero rows: either the session is not active, or a credential is already
    # bound. Distinguish the two so the caller can answer truthfully.
    return None, ("already_authenticated" if editor_session_has_credential(edit_session_id)
                  else "not_active")


class EditorTokenTableUnavailable(Exception):
    pass


def _append_editor_token(edit_session_id, token_hash, expires_at):
    """INSERT one credential. Returns 'ok', 'unavailable' (table not deployed)
    or 'error'."""
    url = f"{SUPABASE_URL}/rest/v1/{EDITOR_TOKEN_TABLE}"
    data = json.dumps({
        "edit_session_id": edit_session_id,
        "token_hash": token_hash,
        "expires_at": expires_at,
    }).encode()
    headers = _supabase_headers()
    headers["Content-Type"] = "application/json"
    headers["Prefer"] = "return=representation"
    req = Request(url, data=data, headers=headers, method="POST")
    try:
        with urlopen(req, timeout=15) as resp:
            return "ok" if resp.status in (200, 201, 204) else "error"
    except HTTPError as e:
        if e.code in (404, 400):
            return "unavailable"
        return "error"
    except URLError:
        return "error"


def editor_session_has_credential(edit_session_id):
    """True when the session already holds a bound credential (append-only
    table or the legacy column). Used to answer 'already authenticated'
    instead of silently rebinding and orphaning the open editor."""
    try:
        tokens = _supabase_get_json(
            f"{SUPABASE_URL}/rest/v1/{EDITOR_TOKEN_TABLE}"
            f"?edit_session_id=eq.{edit_session_id}&select=token_hash&limit=1")
        if tokens:
            return True
    except SupabaseLookupError as e:
        if e.code not in (404, 400, 401, 403):
            raise
    rows = fetch_session_by_id(edit_session_id)
    return bool(rows and rows[0].get("session_token_hash"))


def resolve_pending_launch(launch_user_id: str):
    """Resolve the pending (active) edit session bound to a Discord user id.
    Returns the most recent active session identity (tenant/clip/asset_version)
    for that user, or None. This is how the Activity maps an authenticated
    Discord user back to the exact clip they clicked Edit on — WITHOUT any token
    or job id ever appearing in a URL."""
    if not launch_user_id:
        return None
    url = (f"{SUPABASE_URL}/rest/v1/loom_edit_sessions"
           f"?launch_user_id=eq.{launch_user_id}&status=eq.active"
           f"&select=*&order=created_at.desc&limit=1")
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
                return None
        except Exception:
            pass
    return {
        "edit_session_id": row.get("id"),
        "tenant_id": row.get("tenant_id"),
        "clip_id": row.get("clip_id"),
        "source_asset_version": row.get("source_asset_version"),
        "status": row.get("status"),
        "draft": row.get("draft"),
    }


def resolve_editor_session_token(session_token: str):
    """Validate a short-lived editor session token. Returns the session identity
    (tenant/clip/asset_version/job/clip_index) or None. Active sessions only —
    a completed/expired session cannot drive further /api/* calls."""
    session, _reason = resolve_editor_session_token_diag(session_token)
    return session


# Terminal session statuses and the truthful reason each one reports. The
# editor credential is validated against the session row's status, and a single
# bucket ("inactive") used to hide WHICH terminal state was hit — which made an
# expired credential indistinguishable from a completed one in the logs.
SESSION_STATUS_ACTIVE = "active"
_TERMINAL_REASONS = {
    "expired": "expired",
    "completed": "completed",
    "revoked": "revoked",
    "cancelled": "revoked",
    "canceled": "revoked",
}

# Append-only editor credentials. The legacy single-column binding
# (loom_edit_sessions.session_token_hash) is REBINDABLE: minting a second token
# for the same session overwrote the first one's hash and orphaned a legitimate,
# still-open editor (every later call reported hash_mismatch). This table stores
# one row per minted credential so tokens are never rebound or revoked by a
# later mint. It is optional at runtime: when it is not deployed the adapter
# falls back to a WRITE-ONCE binding of the legacy column.
EDITOR_TOKEN_TABLE = "loom_editor_session_tokens"


def _terminal_reason(status: str) -> str:
    return _TERMINAL_REASONS.get(str(status or "").strip().lower(), "inactive")


def _parse_expiry(expires_at):
    if not expires_at:
        return None
    try:
        return datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
    except Exception:
        return None


def _session_expired(row) -> bool:
    exp = _parse_expiry((row or {}).get("expires_at"))
    return bool(exp and exp < datetime.now(timezone.utc))


def _session_identity(row):
    """The editor's authorised identity, or None when the row is unusable."""
    if not isinstance(row, dict):
        return None
    clip_id = row.get("clip_id") or ""
    if not clip_id or not row.get("id"):
        return None
    job_id, _, clip_index = clip_id.rpartition(":")
    return {
        "edit_session_id": row.get("id"),
        "tenant_id": row.get("tenant_id"),
        "clip_id": clip_id,
        "source_asset_version": row.get("source_asset_version"),
        "job_id": job_id,
        "clip_index": clip_index,
        # Durable version state for the editor's one-time auto-start decision.
        "status": row.get("status"),
        "target_asset_version": row.get("target_asset_version"),
        "draft": row.get("draft"),
    }


def _supabase_get_json(url):
    """GET a Supabase REST url. Raises SupabaseLookupError on any non-2xx so a
    transport/permission failure is never mistaken for 'no such row'."""
    req = Request(url, headers=_supabase_headers(), method="GET")
    try:
        with urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode() or "[]")
    except HTTPError as e:
        raise SupabaseLookupError(e.code, url) from e
    except URLError as e:
        raise SupabaseLookupError(0, url) from e


class SupabaseLookupError(Exception):
    def __init__(self, code, url):
        super().__init__(f"supabase lookup failed (status {code})")
        self.code = code
        self.url = url


def fetch_session_by_id(edit_session_id):
    return _supabase_get_json(
        f"{SUPABASE_URL}/rest/v1/loom_edit_sessions?id=eq.{edit_session_id}&select=*")


def fetch_sessions_by_session_token_hash(token_hash):
    return _supabase_get_json(
        f"{SUPABASE_URL}/rest/v1/loom_edit_sessions"
        f"?session_token_hash=eq.{token_hash}&select=*")


def lookup_editor_token(token_hash):
    """Resolve a presented session-token hash to its session row.

    Order: the append-only credential table first (a token stays valid even
    after another token is minted for the same session), then the legacy
    write-once column. Returns (row_or_None, source) where source is one of
    'token_table', 'legacy'. Raises SupabaseLookupError when the lookup itself
    failed (permission/transport) — that is NOT the same as an unknown token."""
    if not token_hash:
        return None, "legacy"
    try:
        tokens = _supabase_get_json(
            f"{SUPABASE_URL}/rest/v1/{EDITOR_TOKEN_TABLE}"
            f"?token_hash=eq.{token_hash}&select=*&limit=1")
    except SupabaseLookupError as e:
        if e.code not in (404, 400, 401, 403):
            raise
        tokens = None  # table not deployed / not readable -> legacy path
    if tokens:
        sid = (tokens[0] or {}).get("edit_session_id")
        if sid:
            rows = fetch_session_by_id(sid)
            return ((rows[0] if rows else None), "token_table")
        return None, "token_table"
    rows = fetch_sessions_by_session_token_hash(token_hash)
    return ((rows[0] if rows else None), "legacy")


def resolve_editor_session_token_diag(session_token: str):
    """Validate the editor session token and CLASSIFY the rejection reason for
    diagnostics. Returns (session_or_None, reason) where reason is one of:
    missing_token, hash_mismatch, lookup_failed, expired, completed, revoked,
    inactive, ok.

    Only an ACTIVE, unexpired session is accepted; every terminal state is
    rejected with its OWN reason so the editor can tell an expired credential
    (re-acquire) from a completed one (already finished) from a genuinely
    unknown token, and from a lookup failure that is not an auth verdict."""
    if not session_token:
        return None, "missing_token"
    token_hash = hash_token(session_token)
    try:
        row, source = lookup_editor_token(token_hash)
    except SupabaseLookupError as e:
        print(f"[session] lookup_failed status={e.code} fp={token_fp(session_token)}",
              flush=True)
        return None, "lookup_failed"
    if not row:
        return None, "hash_mismatch"
    status = str(row.get("status") or "").strip()
    if status != SESSION_STATUS_ACTIVE:
        return None, _terminal_reason(status)
    if _session_expired(row):
        return None, "expired"
    session = _session_identity(row)
    if not session:
        return None, "hash_mismatch"
    session["token_source"] = source
    return session, "ok"


def normalize_ready_file(ready_file, job_id):
    """Normalize a rendered-output reference to a safe, existing basename within
    the job's output directory. Rejects path traversal, separators, unexpected
    origins, and files outside the job directory. Returns the basename or None."""
    if not ready_file or not job_id:
        return None
    from urllib.parse import urlparse, unquote
    s = str(ready_file).strip()
    # If a URL/path was passed (defense in depth), reduce to its pathname basename.
    if s.startswith("/") or "://" in s:
        parsed = urlparse(s)
        if parsed.scheme and parsed.netloc and parsed.netloc.split(":")[0] not in ("127.0.0.1", "localhost"):
            return None
        s = os.path.basename(unquote(parsed.path))
    base = os.path.basename(s)
    if (not base or base in (".", "..") or base != s
            or "/" in base or "\\" in base):
        return None
    full = os.path.join(OPENSHORTS_OUTPUT, job_id, base)
    if not os.path.exists(full):
        return None
    return base


def check_existing_card(tenant_id, clip_id, asset_version):
    """Return existing final_publication review-token rows (with delivery
    status) for the exact tenant/clip/asset_version — used to prevent a
    duplicate review token / Discord card on replay."""
    url = (f"{SUPABASE_URL}/rest/v1/loom_review_tokens"
           f"?tenant_id=eq.{tenant_id}&clip_id=eq.{clip_id}"
           f"&asset_version=eq.{asset_version}&review_stage=eq.final_publication"
           f"&select=id,status,discord_message_id")
    req = Request(url, headers=_supabase_headers(), method="GET")
    with urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode() or "[]")


def _stable_json(value):
    """Canonical JSON for fingerprinting: key order and default encoding must
    not change the identity of the same submission."""
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    except Exception:
        return json.dumps(str(value))


def submission_fingerprint(body: dict) -> str:
    """Identity of ONE edit submission — what the editor asked to persist.

    Keyed to the SUBMISSION's own content (rendered file hash/size, trim range,
    caption/reframe changes, instruction, render metadata), never merely to
    job_id + clip_index. That is what lets a replay of the SAME submission be
    recognised as a no-op while a genuinely DIFFERENT submission from the same
    clip still allocates the next asset version."""
    payload = {
        "content_type": (body or {}).get("content_type") or "video/mp4",
        "file_hash": (body or {}).get("file_hash"),
        "size_bytes": (body or {}).get("size_bytes"),
        "trim_start": (body or {}).get("trim_start"),
        "trim_end": (body or {}).get("trim_end"),
        "caption_changes": (body or {}).get("caption_changes"),
        "crop_reframe_changes": (body or {}).get("crop_reframe_changes"),
        "instruction": (body or {}).get("instruction") or "",
        "edl_metadata": (body or {}).get("edl_metadata"),
    }
    return hashlib.sha256(_stable_json(payload).encode()).hexdigest()


def stored_submission_fingerprint(edit_session_id):
    """The fingerprint recorded when this session last completed, or None.
    Read from the immutable correction log (which already carries
    edit_session_id + edl_metadata), so no schema change is required."""
    if not edit_session_id:
        return None
    try:
        rows = supabase_filter_get("loom_edit_corrections",
                                   {"edit_session_id": edit_session_id})
    except Exception as e:  # noqa: BLE001
        print(f"[complete] submission fingerprint lookup failed: {type(e).__name__}",
              flush=True)
        return None
    for row in (rows or []):
        if not isinstance(row, dict):
            continue
        meta = row.get("edl_metadata") or {}
        if isinstance(meta, dict) and meta.get("submission_fingerprint"):
            return meta["submission_fingerprint"]
    return None


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


def mark_edited_storage_status(tenant_id, clip_id, asset_version):
    """Record the TRUTHFUL storage fact for a freshly registered EDITED version.

    ``register_edited_asset`` stamps the new lineage row with the ``loom_media``
    column default ('uploaded'), but the edit/publication path deliberately
    registers no media — no ``canonical_url``, no ``ghl_media_id``. Left alone,
    the row (and the review card that renders ``storage_status``) claims a
    storage outcome that never happened.

    So: when the row carries neither a canonical url nor a GHL media id, record
    ``not_applicable`` instead. Rows that DO carry either are left untouched —
    this never asserts anything about real storage, it only stops asserting a
    false one. Non-fatal: a failure here must never block the completion.
    """
    try:
        rows = supabase_filter_get("loom_media", {
            "tenant_id": tenant_id, "clip_id": clip_id, "asset_version": asset_version,
        })
        if not rows:
            return False
        row = rows[0]
        if row.get("canonical_url") or row.get("ghl_media_id"):
            return False  # something really was stored; leave the fact alone
        if row.get("storage_status") == STORAGE_NOT_APPLICABLE:
            return False
        supabase_patch_media(row["id"], {"storage_status": STORAGE_NOT_APPLICABLE})
        print(f"[media] storage_status {clip_id}@{asset_version} -> "
              f"{STORAGE_NOT_APPLICABLE} (no media registered on this path)", flush=True)
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[media] storage_status correction failed for "
              f"{clip_id}@{asset_version}: {type(e).__name__}: {e}", flush=True)
        return False


def supabase_patch_media(media_id, updates):
    url = f"{SUPABASE_URL}/rest/v1/loom_media?id=eq.{media_id}"
    data = json.dumps(updates).encode()
    headers = _supabase_headers()
    headers["Content-Type"] = "application/json"
    headers["Prefer"] = "return=representation"
    req = Request(url, data=data, headers=headers, method="PATCH")
    with urlopen(req, timeout=15) as resp:
        return resp.status, json.loads(resp.read().decode() or "{}")


ASSET_VERSION_MODE_PINNED = "pinned"
ASSET_VERSION_MODE_LATEST = "latest"
ASSET_VERSION_LATEST_TOKENS = ("", "latest", "*")


def parse_asset_version(version):
    """'render-1.12' -> ('render', 1, 12); None when unparseable. Comparison is
    NUMERIC, so render-1.10 sorts after render-1.9 (a string compare would put
    '1.10' below '1.9' and silently pick the stale version)."""
    m = re.match(r"^([A-Za-z_-]+)-(\d+)\.(\d+)$", str(version or "").strip())
    if not m:
        return None
    return (m.group(1), int(m.group(2)), int(m.group(3)))


def newest_media_version(media_rows):
    """Deterministically newest parseable asset_version in the durable rows."""
    best, best_key = None, None
    for r in (media_rows or []):
        if not isinstance(r, dict):
            continue
        key = parse_asset_version(r.get("asset_version"))
        if key is None:
            continue
        if best_key is None or key > best_key:
            best, best_key = r.get("asset_version"), key
    return best


def resolve_asset_version_selection(media_rows, requested_version):
    """Choose WHICH asset version to hydrate, and say why.

    Contract (an older version is NEVER silently substituted):
      * a specific version -> mode 'pinned': hydrate THAT version. When it is
        absent from the durable records the caller must fail truthfully
        (reason 'version_not_found') rather than fall back.
      * '', None, 'latest' -> mode 'latest': the newest parseable version in the
        durable records, chosen deterministically.

    Returns (version_or_None, mode, reason).
    """
    rows = [r for r in (media_rows or []) if isinstance(r, dict)]
    known = {r.get("asset_version") for r in rows}
    req = str(requested_version or "").strip()
    if req in ASSET_VERSION_LATEST_TOKENS:
        newest = newest_media_version(rows)
        if not newest:
            return None, ASSET_VERSION_MODE_LATEST, "no_versions"
        return newest, ASSET_VERSION_MODE_LATEST, "ok"
    if req not in known:
        return None, ASSET_VERSION_MODE_PINNED, "version_not_found"
    return req, ASSET_VERSION_MODE_PINNED, "ok"


# Clip sub-resources the editor reads through the gateway. These are the ones
# that have a durable answer when the live (rehydrated) job is gone.
CLIP_SUBRESOURCE_RE = re.compile(r'^/api/clip/([^/]+)/([^/]+)/(transcript|edl|scenes)$')


def durable_clip_payload(kind, durable):
    """The durable answer for a clip sub-resource the live backend cannot serve.

    transcript -> the word blocks recorded on this version's correction (the
                  same blocks the bootstrap ships). Empty when none were ever
                  persisted: that is the truthful answer, never invented words.
    scenes     -> [] — the ACCEPTED contract default. There is no durable scene
                  store, so an empty list is the true answer, never fabricated
                  scene data (which is why the editor must accept []).
    edl        -> the durable recipe (canonical range + framing) that the editor
                  already builds from the bootstrap.
    """
    out = {"source": "durable", "asset_version": durable.get("asset_version")}
    if kind == "scenes":
        out["scenes"] = []
        return out
    if kind == "transcript":
        captions = []
        for c in (durable.get("captions") or []):
            if not isinstance(c, dict):
                continue
            captions.append({
                "text": c.get("text") or c.get("word") or "",
                "startMs": c.get("startMs"),
                "endMs": c.get("endMs"),
            })
        out["captions"] = captions
        out["durationSec"] = durable.get("duration_seconds")
        return out
    src = durable.get("source_range") or {}
    out["canonical_range"] = {"start": src.get("start"), "end": src.get("end")}
    out["source"] = {"duration": durable.get("duration_seconds")}
    out["framing"] = durable.get("framing") or "auto"
    out["durable"] = True
    return out


_SOURCE_CLIP_RE = re.compile(r"^src-([0-9a-f]{64}):0$")


def resolve_source_lineage_bootstrap(tenant_id, clip_id, source_asset_version):
    """Bootstrap a source-review launch from the candidate row.

    Does not read loom_media and does not download. A tenant mismatch or a
    malformed clip id fails closed.
    """
    match = _SOURCE_CLIP_RE.fullmatch(str(clip_id or ""))
    if not match or not tenant_id:
        return None, "missing_identity"
    try:
        candidate = source_review.fetch_candidate_by_fingerprint(
            SUPABASE_URL, SUPABASE_KEY, match.group(1))
    except Exception as e:  # noqa: BLE001
        print(f"[bootstrap] source candidate lookup failed: {type(e).__name__}", flush=True)
        return None, "lookup_failed"
    if not candidate:
        return None, "missing_durable_fields"
    if (candidate.get("tenant_id") or "") != tenant_id:
        return None, "missing_identity"
    return source_review.source_lineage_bootstrap(candidate, source_asset_version)


def resolve_durable_bootstrap_diag(tenant_id, clip_id, source_asset_version):
    """(durable_or_None, reason) — distinguishes WHY a bootstrap could not be
    resolved, so a 404 can be told apart from an auth failure or a lookup
    failure. reason is one of: missing_identity, lookup_failed, no_versions,
    version_not_found, missing_durable_fields, ok."""
    if not (tenant_id and clip_id):
        return None, "missing_identity"
    # Source-review launches are not loom_media rows. Resolve them from the
    # candidate lineage before the rendered-clip lookup, and do not download.
    if str(clip_id).startswith("src-"):
        return resolve_source_lineage_bootstrap(tenant_id, clip_id, source_asset_version)
    try:
        media_rows = supabase_filter_get("loom_media",
                                         {"tenant_id": tenant_id, "clip_id": clip_id})
    except Exception as e:  # noqa: BLE001
        print(f"[bootstrap] media lookup failed for {clip_id}: {type(e).__name__}", flush=True)
        return None, "lookup_failed"
    version, _mode, reason = resolve_asset_version_selection(media_rows, source_asset_version)
    if not version:
        return None, reason
    durable = resolve_durable_bootstrap(tenant_id, clip_id, version)
    if not durable:
        return None, "missing_durable_fields"
    return durable, "ok"


def resolve_durable_bootstrap(tenant_id, clip_id, source_asset_version):
    """Resolve the durable editor bootstrap from LOOM media/version records —
    independent of the transient OpenShorts /api/status runtime state. Returns
    a dict with the durable preview/media URL, duration, trim/source range,
    framing, title, hook, social caption, spoken captions, transcript and
    scenes; or None if the REQUIRED durable fields (a media URL + a valid
    trim/source range) do not exist.

    The version to hydrate is PINNED by the caller (a Discord Edit is tied to
    one asset_version). Only 'latest'/'*'/empty selects the newest version, and
    that choice is deterministic. An unknown pinned version is NOT substituted."""
    if not (tenant_id and clip_id and source_asset_version):
        return None

    # 1) Media lineage: walk previous_asset_version from the version being
    #    edited back to the original render to find a durable canonical_url.
    media_rows = supabase_filter_get("loom_media", {
        "tenant_id": tenant_id, "clip_id": clip_id,
    })
    version, version_mode, version_reason = resolve_asset_version_selection(
        media_rows, source_asset_version)
    if not version:
        print(f"[bootstrap] version selection failed for {clip_id}: "
              f"requested={source_asset_version!r} reason={version_reason}", flush=True)
        return None
    source_asset_version = version
    by_version = {r.get("asset_version"): r for r in (media_rows or []) if isinstance(r, dict)}
    canonical_url = None
    # Identity of the bytes AT canonical_url: captured from the SAME row the URL
    # came from, so a later verifier compares against the object it actually
    # points at (a version that re-renders from an ancestor keeps the ancestor's
    # hash, never the current row's).
    canonical_hash = None
    canonical_size = None
    source_filename = None
    clean_source_url = None
    media_duration = None
    cur = source_asset_version
    seen = set()
    while cur and cur in by_version and cur not in seen:
        seen.add(cur)
        row = by_version[cur]
        if not canonical_url and row.get("canonical_url"):
            canonical_url = row.get("canonical_url")
            canonical_hash = row.get("file_hash")
            canonical_size = row.get("size_bytes")
        source_filename = source_filename or row.get("source_filename")
        media_duration = media_duration or row.get("duration_seconds")
        # The clean pre-hook/pre-subtitle input master is a durable property of
        # the ORIGINAL render (previous_asset_version IS NULL); walking the
        # lineage back surfaces it. NULL = honestly no durable clean source.
        clean_source_url = clean_source_url or row.get("clean_source_url")
        cur = row.get("previous_asset_version")

    # 2) The correction that PRODUCED this version carries the trim/source
    #    range + caption blocks + framing (durable edit metadata).
    corrections = supabase_filter_get("loom_edit_corrections", {
        "tenant_id": tenant_id, "clip_id": clip_id,
        "target_asset_version": source_asset_version,
    })
    correction = (corrections or [{}])[0] if corrections else {}
    trim_start = correction.get("trim_start")
    trim_end = correction.get("trim_end")
    caption_changes = correction.get("caption_changes") or {}
    crop_reframe = correction.get("crop_reframe_changes") or {}
    framing = crop_reframe.get("framing") or "auto"

    # 3) Durable suggested copy (hook + social caption).
    scores = supabase_get("loom_scores", clip_id) or []
    score = (scores or [{}])[0] if scores else {}
    hook = score.get("suggested_hook") or ""
    social_caption = score.get("suggested_caption") or ""

    # Spoken captions: word-level blocks (clip-relative timings).
    blocks = caption_changes.get("blocks") or []
    captions = []
    for b in (blocks if isinstance(blocks, list) else []):
        if isinstance(b, dict):
            captions.append({
                "text": b.get("text") or b.get("word") or "",
                "startMs": b.get("startMs"),
                "endMs": b.get("endMs"),
            })

    # REQUIRED durable fields: a media URL AND a valid source range. The source
    # range comes from the correction trim when this version was edited; for a
    # fresh (unedited) clip there is no correction, so the FULL clip range (0 →
    # media duration) is the source range. Never fabricate a 0/0 placeholder.
    range_start = None
    range_end = None
    try:
        if trim_start is not None and trim_end is not None and float(trim_end) > float(trim_start):
            range_start, range_end = float(trim_start), float(trim_end)
        elif media_duration is not None and float(media_duration) > 0:
            range_start, range_end = 0.0, float(media_duration)
    except (TypeError, ValueError):
        range_start = range_end = None
    if not canonical_url or range_start is None or range_end is None:
        return None

    # Burned-layer detection from the lineage source filename (the same
    # OpenShorts convention: subtitled_<ts>_ = captions are the last layer,
    # hooked_<ts>_/hook_ = a burned hook). Drives replacement-vs-stack
    # semantics in the editor: a clip with a burned hook must re-render from
    # the CLEAN pre-hook ancestor, never overlay the new hook on the burned one.
    sf = source_filename or ""
    has_burned_captions = bool(re.match(r'^subtitled_\d+_', sf))
    stripped = re.sub(r'^subtitled_\d+_', '', sf)
    has_burned_hook = bool(re.match(r'^(?:hooked_\d+_|hook_)', stripped))

    return {
        "media_url": canonical_url,
        # Durable identity of the working asset handed to a consumer: the
        # sha256/size of exactly the object at media_url. Descriptive only; an
        # absent record stays absent rather than being guessed from a path.
        "media_sha256": canonical_hash or "",
        "media_size_bytes": canonical_size,
        "clean_source_url": clean_source_url,
        "duration_seconds": round(range_end - range_start, 2),
        "source_range": {"start": range_start, "end": range_end},
        "framing": framing,
        # `title` is the human-readable DISPLAY title (source metadata), NOT
        # renderable copy: a consumer must never draw it on the timeline. The
        # explicit renderable contract is `renderable_text` below.
        "title": "",
        # Renderable copy, by explicit contract. A rendered clip's hook is copy
        # LOOM generated for this clip, so it may appear on screen; there is no
        # operator-set title overlay on this lane, so title stays empty rather
        # than borrowing the source metadata title.
        "renderable_text": {"hook": hook, "title": ""},
        "source_filename": source_filename or "",
        "hook": hook,
        "has_burned_hook": has_burned_hook,
        "has_burned_captions": has_burned_captions,
        "social_caption": social_caption,
        "caption_text": caption_changes.get("text") or "",
        "captions": captions,
        "transcript": captions,
        "scenes": [],
        # The version actually hydrated, and how it was chosen. Lets a caller
        # (and a test) prove that no silent substitution happened.
        "asset_version": source_asset_version,
        "version_mode": version_mode,
    }


# ---------------------------------------------------------------------------
# Review proxy generation (Docker-independent, master untouched)
# ---------------------------------------------------------------------------

def record_capcut_handoff(report, *, tenant_id, clip_id, candidate_id="",
                          correction=None):
    """Persist the immutable BEFORE state of a SUCCESSFUL CapCut handoff.

    PROVENANCE ONLY. Nothing on any operational path reads this table, and this
    helper touches nothing else: not the candidate, approval, scoring, rendering,
    acquisition, Content Intelligence, publishing, SourceWindow, GHL routing or
    the editor session.

    Append-only (a new handoff_id per success, rows never updated). It is called
    ONLY after the runner has confirmed a real project exists on disk, so a
    failed or refused handoff can never leave a success row.

    Returns the recorded row, or None when nothing was written. A write failure
    is logged and swallowed: the operator already has their project, so failing
    loudly here would break the lane to protect a provenance record.
    """
    if not isinstance(report, dict):
        return None
    spec = report.get("spec")
    media = report.get("media") or {}
    coords = report.get("coordinates") or {}
    if not isinstance(spec, dict) or not media.get("sha256"):
        return None
    correction = correction if isinstance(correction, dict) else {}
    payload = {
        "tenant_id": tenant_id,
        "candidate_id": candidate_id or None,
        "clip_id": clip_id,
        "asset_version": report.get("asset_version") or "",
        "source_asset_version": correction.get("source_asset_version"),
        "correction_id": correction.get("id"),
        "media_sha256": media.get("sha256"),
        "working_timebase_origin_s": coords.get("timebase_origin", 0.0) or 0.0,
        "spec": spec,
        "compiler": report.get("compiler") or {},
    }
    url = f"{SUPABASE_URL}/rest/v1/loom_capcut_handoffs"
    headers = _supabase_headers()
    headers["Content-Type"] = "application/json"
    headers["Prefer"] = "return=representation"
    req = Request(url, data=json.dumps(payload).encode(),
                  headers=headers, method="POST")
    try:
        with urlopen(req, timeout=15) as resp:
            rows = json.loads(resp.read().decode() or "[]")
    except Exception as exc:  # noqa: BLE001
        print(f"[capcut] provenance write failed: {type(exc).__name__}", flush=True)
        return None
    return rows[0] if isinstance(rows, list) and rows else None


def resolve_local_working_media(job_id, durable):
    """The durable working asset's path ON THIS HOST, or "" when there is none.

    The CapCut handoff needs real local bytes. It never re-downloads (that would
    be a second acquisition, and the lane must not become a downloader) and it
    never invents a location: it looks where this host actually keeps a clip's
    rendered output. "" is reported honestly as "no local media" — never worked
    around by fetching the remote URL.

    NOTE: "" here means "not on this host", NOT "no working media exists". When
    this returns "", the caller hands the durable record to
    ``materialize_local_working_media`` (below), which materialises the SAME
    durable object for transport. This function's own answer is unchanged.
    """
    name = os.path.basename(str((durable if isinstance(durable, dict) else {}).get(
        "source_filename") or ""))
    if not (job_id and name):
        return ""
    candidate = os.path.join(OPENSHORTS_OUTPUT, str(job_id), name)
    return candidate if os.path.isfile(candidate) else ""


# ---------------------------------------------------------------------------
# CapCut handoff transport media (durable -> local)
# ---------------------------------------------------------------------------
# The runner owns no durable credentials (deliberately), so materialising the
# durable object belongs HERE, on the side of the handoff that already holds the
# durable record and the downloader.
#
# LIFECYCLE POLICY — transport only, bounded, never canonical:
#   * The root is a bounded scratch root (LOOM_CAPCUT_HANDOFF_MEDIA_ROOT), NOT a
#     media store: nothing is registered from it, no LOOM identity points at it,
#     and deleting it loses nothing that cannot be re-materialised from the
#     durable record.
#   * One deterministic directory per (clip, version) — the identity token is
#     the sanitised job id + version + a hash of that identity, so the SAME
#     working asset always maps to the SAME path and retries reuse the copy.
#   * A directory holds AT MOST one file: a copy whose bytes match the durable
#     hash/size is reused as-is; anything else is removed before re-materialising
#     so transport copies cannot accumulate. Accumulation is bounded by the
#     number of distinct (clip, version) handoffs, not by the number of attempts.
#   * Canonical GHL/LOOM assets are never read-modify-written, moved or deleted.
#     Cleanup is confined to this root and can never reach a canonical asset.
CAPCUT_HANDOFF_MEDIA_ROOT = _env(
    "LOOM_CAPCUT_HANDOFF_MEDIA_ROOT",
    os.path.join(os.path.expanduser(capcut_runner.DEFAULT_HANDOFF_ROOT),
                 "transport"))


def _transport_identity_token(job_id, asset_version) -> str:
    """A deterministic, filesystem-safe, collision-free path component."""
    identity = f"{job_id}\x00{asset_version}"
    readable = re.sub(r"[^A-Za-z0-9._-]+", "_", f"{job_id}:{asset_version}")
    readable = readable.strip("._-")[:60] or "handoff"
    return f"{readable}-{hashlib.sha256(identity.encode()).hexdigest()[:12]}"


def capcut_handoff_transport_path(job_id, durable):
    """(directory, destination) for the transport copy of one durable asset.

    Deterministic in the durable identity, so the same working asset resolves to
    the same path on every attempt and a retry reuses a verified copy instead of
    downloading again.
    """
    durable = durable if isinstance(durable, dict) else {}
    version = str(durable.get("asset_version") or "")
    directory = os.path.join(CAPCUT_HANDOFF_MEDIA_ROOT,
                             _transport_identity_token(job_id, version))
    name = os.path.basename(str(durable.get("source_filename") or ""))
    if not name:
        name = os.path.basename(urlparse(str(durable.get("media_url") or "")).path)
    if not name or os.path.splitext(name)[1] == "":
        name = "working.mp4"
    return directory, os.path.join(directory, os.path.basename(name))


def verify_working_media_copy(path, durable):
    """True when ``path`` provably holds the durable working asset's bytes.

    Identity comes from the durable record (sha256, size where present) — never
    from the path or the transport directory. A record carrying neither is NOT
    verifiable and fails closed: an unverifiable object is exactly the thing the
    guard exists to stop.
    """
    durable = durable if isinstance(durable, dict) else {}
    if not path or not os.path.isfile(path):
        return False
    expected_sha = str(durable.get("media_sha256") or "").strip().lower()
    expected_size = durable.get("media_size_bytes")
    try:
        expected_size = int(expected_size) if expected_size is not None else None
    except (TypeError, ValueError):
        expected_size = None
    if not expected_sha and expected_size is None:
        return False
    if expected_size is not None and os.path.getsize(path) != expected_size:
        return False
    if expected_sha and _sha256_file(path) != expected_sha:
        return False
    return True


def materialize_local_working_media(job_id, durable):
    """A local path to the CURRENT working asset, or ("", reason).

    Adds a remote-only case to ``resolve_local_working_media`` WITHOUT changing
    it: when the host has no rendered copy, the durable record's own object is
    materialised into the bounded transport workspace and verified against the
    durable sha256 (and size where present) before it is offered to the handoff.

    This is NOT acquisition and NOT a second store. It fetches the durable GHL
    object this LOOM record already points at — the same bytes the editor plays
    — using the existing ``_download`` helper (chunked + browser UA, because the
    filesafe CDN rejects the default urllib UA). Nothing is re-encoded, nothing
    is re-acquired from the platform, and the ORIGINAL long source is never
    touched: the transport copy is the working asset, so CapCut keeps receiving
    working-relative coordinates and no second ORIGINAL offset can appear.

    Returns (path, how) where how is one of local_existing, transport_reused,
    materialized; or ("", reason) with one of: no_durable_record,
    no_durable_media_url, durable_media_identity_unavailable,
    durable_media_download_failed, durable_media_hash_mismatch,
    durable_media_size_mismatch.
    """
    local = resolve_local_working_media(job_id, durable)
    if local:
        return local, "local_existing"
    if not isinstance(durable, dict):
        return "", "no_durable_record"
    url = str(durable.get("media_url") or "")
    if not url.startswith("https://"):
        return "", "no_durable_media_url"

    expected_sha = str(durable.get("media_sha256") or "").strip().lower()
    expected_size = durable.get("media_size_bytes")
    try:
        expected_size = int(expected_size) if expected_size is not None else None
    except (TypeError, ValueError):
        expected_size = None
    if not expected_sha and expected_size is None:
        # No durable identity means no way to prove the bytes, and an unproven
        # transport copy would be a guess about what the operator is editing.
        return "", "durable_media_identity_unavailable"

    directory, dest = capcut_handoff_transport_path(job_id, durable)
    try:
        os.makedirs(directory, exist_ok=True)
    except OSError as exc:
        print(f"[capcut] transport workspace failed: {exc}", flush=True)
        return "", "durable_media_download_failed"

    if verify_working_media_copy(dest, durable):
        return dest, "transport_reused"

    # Clear stale transport copies in THIS directory before writing, so the
    # directory never holds more than the one file the handoff needs. Scoped to
    # the transport root: nothing outside it can be reached from here.
    try:
        for stale in os.listdir(directory):
            stale_path = os.path.join(directory, stale)
            if os.path.isfile(stale_path):
                os.remove(stale_path)
    except OSError as exc:
        print(f"[capcut] transport cleanup failed: {exc}", flush=True)

    if not _download(url, dest):
        return "", "durable_media_download_failed"
    if expected_size is not None and os.path.getsize(dest) != expected_size:
        print(f"[capcut] transport size mismatch for {job_id}: "
              f"got={os.path.getsize(dest)} want={expected_size}", flush=True)
        _discard_transport_copy(dest)
        return "", "durable_media_size_mismatch"
    if expected_sha and _sha256_file(dest) != expected_sha:
        print(f"[capcut] transport hash mismatch for {job_id}", flush=True)
        _discard_transport_copy(dest)
        return "", "durable_media_hash_mismatch"
    return dest, "materialized"


def _discard_transport_copy(path):
    """Remove a transport copy that failed verification. Transport root only.

    Confined by construction: the caller passes a path built by
    ``capcut_handoff_transport_path``, and a path outside the transport root is
    refused outright so a bug here can never reach a canonical asset.
    """
    try:
        root = os.path.realpath(CAPCUT_HANDOFF_MEDIA_ROOT)
        real = os.path.realpath(path)
        if os.path.commonpath([root, real]) != root:
            return
        os.remove(real)
    except (OSError, ValueError):
        pass


def handoff_acquisition_provenance(durable):
    """ORIGINAL provenance for the handoff report, or None.

    REPORTED, never applied: the runner uses these values only to describe which
    ORIGINAL range the working asset covers, so the coordinates it exports stay
    working-relative and no second ORIGINAL offset can be introduced. Records
    with no origin (every rendered-clip version) return None, which leaves that
    lane's report exactly as it was.
    """
    durable = durable if isinstance(durable, dict) else {}
    try:
        origin = float(durable.get("working_timebase_origin_s"))
    except (TypeError, ValueError):
        return None
    if origin < 0:
        return None
    acquisition = {"working_timebase_origin_s": origin}
    original = durable.get("original_window")
    if isinstance(original, dict):
        try:
            acquisition["original_start_s"] = float(original["start"])
            acquisition["original_end_s"] = float(original["end"])
        except (KeyError, TypeError, ValueError):
            pass
    return acquisition


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


_manual_channel_cache = {"id": None, "ts": 0.0}

def resolve_manual_intake_channel(create: bool = True) -> str:
    """Resolve #manual-intake by name; create it when missing.

    The ingress channel for operator-supplied media. Created on first use rather
    than hardcoded, so a fresh guild works without a manual id copy.
    """
    if _manual_channel_cache["id"] and (time.time() - _manual_channel_cache["ts"]) < 600:
        return _manual_channel_cache["id"]
    if MANUAL_INTAKE_CHANNEL_ID:
        # Operator-bound channel id: never depends on the display name.
        _manual_channel_cache.update(id=MANUAL_INTAKE_CHANNEL_ID, ts=time.time())
        return MANUAL_INTAKE_CHANNEL_ID
    status, guilds = http_json("GET", f"{DISCORD_API}/users/@me/guilds", DISCORD_BOT_TOKEN)
    for g in guilds or []:
        gid = g.get("id")
        s2, channels = http_json("GET", f"{DISCORD_API}/guilds/{gid}/channels", DISCORD_BOT_TOKEN)
        for ch in channels or []:
            cname = ch.get("name") or ""
            if ch.get("type") == 0 and (cname == MANUAL_INTAKE_CHANNEL_NAME
                                        or cname.startswith(MANUAL_INTAKE_CHANNEL_NAME)):
                _manual_channel_cache.update(id=ch["id"], ts=time.time())
                return ch["id"]
        if create:
            st, created = http_json("POST", f"{DISCORD_API}/guilds/{gid}/channels",
                                    DISCORD_BOT_TOKEN,
                                    {"name": MANUAL_INTAKE_CHANNEL_NAME, "type": 0,
                                     "topic": ("Manual intelligence intake: post the media file with the "
                                               "original URL (`url: https://…`), optional `start=`/`end=`, "
                                               "optional `tenant=`, then Apps → intake-media. Candidates "
                                               "appear in #source-review.").strip()})
            if isinstance(created, dict) and created.get("id"):
                _manual_channel_cache.update(id=created["id"], ts=time.time())
                print(f"[manual-intake] created #{MANUAL_INTAKE_CHANNEL_NAME} {created['id']}", flush=True)
                return created["id"]
    raise RuntimeError(f"Channel #{MANUAL_INTAKE_CHANNEL_NAME} not found and could not be created")


def manual_review_channel_id() -> str:
    """Destination for post-Content-Intelligence MANUAL candidates.

    Manually supplied media has already been discovered AND acquired by the
    operator, so it is content/candidate review — never source-acquisition
    review. Reuses the existing clip-review destination rather than inventing
    a channel; an explicit id override wins when configured.
    """
    if MANUAL_REVIEW_CHANNEL_ID:
        return MANUAL_REVIEW_CHANNEL_ID
    return resolve_clip_review_channel()


# ---------------------------------------------------------------------------
# Compact card presentation helpers (Framer-inspired, conditional)
# ---------------------------------------------------------------------------

def _compact_duration(seconds):
    try:
        s = float(seconds)
    except (TypeError, ValueError):
        return None
    if s < 60:
        return f"{s:g}s"
    m = int(s // 60)
    sec = s % 60
    return f"{m}:{sec:04.1f}"


def _short_version(asset_version):
    if not asset_version:
        return None
    v = str(asset_version)
    return ("v" + v[len("render-"):]) if v.startswith("render-") else v


def build_changes_summary(corrections):
    """Build the compact Changes line from actual corrections (conditional).
    Returns None when there is nothing meaningful to show."""
    if not corrections:
        return None
    parts = []
    ts = corrections.get("trim_start"); te = corrections.get("trim_end")
    if ts is not None and te is not None:
        try:
            parts.append(f"{float(ts):g}–{float(te):g}s")
        except (TypeError, ValueError):
            pass
    cap = corrections.get("caption_changes") or {}
    align = cap.get("alignment")
    changed = cap.get("changedCount")
    if align == "redistributed" and changed:
        parts.append(f"caption adjusted ({changed} words)")
    elif align == "realignment_required":
        parts.append("caption realignment required")
    style_bits = []
    pos = cap.get("position")
    if pos and pos != "bottom": style_bits.append(str(pos))
    sty = cap.get("style")
    if sty and sty != "classic": style_bits.append(str(sty))
    if style_bits: parts.append("/".join(style_bits))
    reframe = corrections.get("crop_reframe_changes") or {}
    framing = reframe.get("framing")
    if framing and framing != "auto": parts.append(str(framing))
    return " · ".join(parts) if parts else None


def card_color(payload):
    # Edited / final_publication = amber; new candidate = Discord blurple.
    return 0xF59E0B if (payload.get("review_stage") == "final_publication") else 0x5865F2


# ---------------------------------------------------------------------------
# Card send (bot message + attachment + buttons)
# ---------------------------------------------------------------------------

def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _strip_clean_filename(filename: str) -> str:
    """Walk `subtitled_<ts>_` and `hooked_<ts>_`/`hook_` prefixes back to the
    clean `<base>_clip_<n>.mp4` name (the pre-hook/pre-subtitle input master).
    Mirrors OpenShorts _strip_burned_captions/_strip_burned_hook naming."""
    name = os.path.basename(filename or "")
    while True:
        m = re.match(r'^subtitled_\d+_(.+)$', name)
        if not m:
            break
        name = m.group(1)
    while True:
        m = re.match(r'^(?:hooked_\d+_|hook_)(.+)$', name)
        if not m:
            break
        name = m.group(1)
    return name


# Ingest-time media registration uploads the burned final AND the clean
# pre-hook source. A single multipart POST is not reliable enough: the CDN has
# been observed closing the connection mid-flight ("Broken pipe") on an
# otherwise healthy link, and because register_clip_media() is all-or-nothing
# a single flake loses the clean source — which silently disables Edit for that
# clip. Retry with bounded exponential backoff instead.
GHL_UPLOAD_ATTEMPTS = int(_env("LOOM_GHL_UPLOAD_ATTEMPTS", "5"))


def _ghl_list_media_files(*, parent_id: str | None = None, limit: int = 200) -> list[dict]:
    """List durable GHL media files for this location, optionally ONE folder.

    Used for placement read-back. Never raises — verification must not be able to
    break an upload that already succeeded.
    """
    from urllib.parse import quote
    query = f"?locationId={quote(GHL_LOCATION_ID)}&type=file&limit={int(limit)}"
    if parent_id:
        query += f"&parentId={quote(parent_id)}"
    req = Request(f"{GHL_MEDIA_API}/medias/files{query}", method="GET")
    req.add_header("Authorization", f"Bearer {GHL_TOKEN}")
    req.add_header("Version", "v3")
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", "LOOMReviewAdapter/1.0")
    try:
        with urlopen(req, timeout=20) as resp:
            body = json.loads(resp.read().decode() or "{}")
        rows = body.get("files")
        return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []
    except Exception:  # noqa: BLE001
        return []


def ghl_verify_placement(file_id: str, expected_parent_id: str | None) -> dict:
    """Confirm a durable media record's REAL folder placement.

    GHL can answer HTTP 201 while silently ignoring an unrecognised folder target
    and storing the file at My Media root, so HTTP success is NOT placement
    evidence. The only proof is the stored record's own ``parentId``.
    """
    expected = expected_parent_id or ""
    if not (GHL_TOKEN and file_id):
        return {"verified": False, "expected": expected, "actual": None,
                "reason": "no_token_or_file_id"}
    if expected:
        if any(r.get("_id") == file_id for r in _ghl_list_media_files(parent_id=expected)):
            return {"verified": True, "expected": expected, "actual": expected,
                    "reason": "in_target_folder"}
        if any(r.get("_id") == file_id for r in _ghl_list_media_files()):
            return {"verified": False, "expected": expected, "actual": "",
                    "reason": "landed_at_my_media_root"}
        return {"verified": False, "expected": expected, "actual": None,
                "reason": "not_listed_yet"}
    row = next((r for r in _ghl_list_media_files() if r.get("_id") == file_id), None)
    if row is None:
        return {"verified": False, "expected": "", "actual": None,
                "reason": "not_listed_yet"}
    return {"verified": True, "expected": "", "actual": row.get("parentId", ""),
            "reason": "root_by_configuration"}


# Diagnostic-only: the reason for the most recent upload failure ON THIS THREAD.
# The documented return contract stays `dict on success / None on failure` (callers
# and tests depend on it), but a caller that needs to CLASSIFY the failure honestly
# (instead of reporting UNKNOWN) can read the reason that belongs to its own call.
_GHL_UPLOAD_DIAG = threading.local()


def ghl_last_upload_error() -> str:
    """Why did this thread's last `ghl_upload_file` call fail? "" when it did not."""
    return str(getattr(_GHL_UPLOAD_DIAG, "error", "") or "")


def _set_ghl_upload_error(reason: str) -> None:
    _GHL_UPLOAD_DIAG.error = str(reason or "")


def ghl_upload_file(path: str, name: str, parent_id: str | None = None):
    """Upload a local file to GHL Media Storage (filesafe CDN). Returns
    {'fileId': ..., 'url': ..., placement fields} or None on failure (never raises).

    ``parent_id`` is the target folder id; when omitted it falls back to the
    configured LOOM folder, so future LOOM uploads land there. With no folder
    configured the request is byte-identical to the previous behaviour (root).
    The PROVEN targeting contract is the multipart form field ``parentId``.

    Retries a bounded number of times with exponential backoff. Non-retryable
    preconditions (no token, missing file) fail immediately without retrying.
    The file is read once and re-framed per attempt so a retry never re-reads
    from disk and each attempt gets a fresh multipart boundary.

    Placement is VERIFIED by reading the durable record back: folder success is
    only ever reported when the stored ``parentId`` matches the target. A mismatch
    is surfaced loudly and never represented as folder success.
    """
    if not GHL_TOKEN or not os.path.exists(path):
        _set_ghl_upload_error("NO_TOKEN" if not GHL_TOKEN else "MISSING_FILE")
        return None

    target_parent = GHL_MEDIA_PARENT_ID if parent_id is None else parent_id

    with open(path, "rb") as f:
        payload = f.read()

    last_error = "unknown"
    attempts = max(1, GHL_UPLOAD_ATTEMPTS)
    for attempt in range(attempts):
        boundary = f"----loom{uuid.uuid4().hex}"
        body = b""
        body += f"--{boundary}\r\n".encode()
        body += f'Content-Disposition: form-data; name="file"; filename="{name}"\r\n'.encode()
        body += b"Content-Type: video/mp4\r\n\r\n"
        body += payload
        body += b"\r\n"
        body += f"--{boundary}\r\n".encode()
        body += b'Content-Disposition: form-data; name="name"\r\n\r\n'
        body += name.encode()
        body += b"\r\n"
        body += f"--{boundary}\r\n".encode()
        body += b'Content-Disposition: form-data; name="hosted"\r\n\r\nfalse\r\n'
        if target_parent:
            # PROVEN contract: the target folder is expressed as `parentId`.
            body += f"--{boundary}\r\n".encode()
            body += b'Content-Disposition: form-data; name="parentId"\r\n\r\n'
            body += str(target_parent).encode()
            body += b"\r\n"
        body += f"--{boundary}--\r\n".encode()
        req = Request(f"{GHL_MEDIA_API}/medias/upload-file", data=body, method="POST")
        req.add_header("Authorization", f"Bearer {GHL_TOKEN}")
        req.add_header("Version", "v3")
        req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
        req.add_header("User-Agent", "LOOMReviewAdapter/1.0")
        try:
            with urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read().decode() or "{}")
                if data.get("url"):
                    if attempt:
                        print(f"[media] GHL upload ok for {name} on attempt {attempt + 1}", flush=True)
                    # Read-back verification must NEVER be able to fail an upload that
                    # already succeeded: the durable-media lifecycle is unchanged.
                    try:
                        placement = ghl_verify_placement(data.get("fileId", ""), target_parent)
                    except Exception as e:  # noqa: BLE001
                        placement = {"verified": False, "expected": target_parent or "",
                                     "actual": None,
                                     "reason": f"verification_error:{type(e).__name__}"}
                    data["placement_verified"] = bool(placement["verified"])
                    data["placement_expected"] = placement["expected"]
                    data["placement_actual"] = placement["actual"]
                    data["placement_reason"] = placement["reason"]
                    if target_parent and not placement["verified"]:
                        print(f"[media] ROUTING FAILURE for {name}: expected parentId="
                              f"{target_parent} but the durable record reports "
                              f"parentId={placement['actual']!r} ({placement['reason']}). "
                              f"The asset IS stored, but NOT in the configured LOOM "
                              f"folder - do not treat this as folder success.", flush=True)
                    elif target_parent:
                        print(f"[media] folder placement verified for {name}: "
                              f"parentId={placement['actual']}", flush=True)
                    return data
                last_error = "response carried no url"
        except Exception as e:
            last_error = e

        _set_ghl_upload_error(str(last_error))
        if attempt < attempts - 1:
            backoff = min(8.0, 1.5 ** attempt)
            print(f"[media] GHL upload attempt {attempt + 1}/{attempts} failed for {name}: "
                  f"{last_error} - retrying in {backoff:.1f}s", flush=True)
            time.sleep(backoff)

    print(f"[media] GHL upload gave up for {name} after {attempts} attempts: {last_error}", flush=True)
    _set_ghl_upload_error(str(last_error))
    return None


def register_clip_media(tenant_id, clip_id, asset_version, job_id, ready_file, master_asset=None, duration_seconds=None):
    """Persist the durable clean-source + burned canonical artifact for a freshly
    rendered clip (ingest-time media registration). Locates the burned final and
    the clean pre-hook/pre-subtitle input on disk, uploads both to GHL Media
    Storage, and upserts the loom_media row with canonical_url (burned) +
    clean_source_url/hash/size/filename. Idempotent (upsert on the identity key).
    Returns the persisted row dict, or None on failure (caller treats as
    non-fatal: the review card still sends)."""
    if not (tenant_id and clip_id and asset_version and job_id and ready_file):
        return None
    burned_path = os.path.join(OPENSHORTS_OUTPUT, job_id, os.path.basename(ready_file))
    if not os.path.exists(burned_path):
        print(f"[media] burned final not found: {burned_path}", flush=True)
        return None

    clean_name = os.path.basename(master_asset) if master_asset else _strip_clean_filename(ready_file)
    clean_path = os.path.join(OPENSHORTS_OUTPUT, job_id, clean_name)
    if not os.path.exists(clean_path):
        clean_name = _strip_clean_filename(ready_file)
        clean_path = os.path.join(OPENSHORTS_OUTPUT, job_id, clean_name)
    if not os.path.exists(clean_path):
        print(f"[media] clean source not found: {clean_path}", flush=True)
        return None

    burned_hash = _sha256_file(burned_path)
    burned_size = os.path.getsize(burned_path)
    clean_hash = _sha256_file(clean_path)
    clean_size = os.path.getsize(clean_path)

    burned_up = ghl_upload_file(burned_path, os.path.basename(ready_file))
    clean_up = ghl_upload_file(clean_path, clean_name)

    payload = {
        "tenant_id": tenant_id,
        "clip_id": clip_id,
        "asset_version": asset_version,
        "file_hash": burned_hash,
        "size_bytes": burned_size,
        "source_filename": os.path.basename(ready_file),
        "ghl_location_id": GHL_LOCATION_ID,
        "storage_status": "uploaded",
        "content_type": "video/mp4",
        "publication_status": "not_approved",
    }
    if duration_seconds is not None:
        payload["duration_seconds"] = duration_seconds
    if burned_up:
        payload["canonical_url"] = burned_up.get("url")
        payload["ghl_media_id"] = burned_up.get("fileId")
        payload["ghl_filename"] = os.path.basename(ready_file)
    if clean_up:
        payload["clean_source_url"] = clean_up.get("url")
        payload["clean_source_hash"] = clean_hash
        payload["clean_source_size_bytes"] = clean_size
        payload["clean_source_filename"] = clean_name
    if not payload.get("canonical_url") or not payload.get("clean_source_url"):
        print(f"[media] GHL upload incomplete for {clip_id} (burned={bool(burned_up)}, clean={bool(clean_up)})", flush=True)
        return None

    url = f"{SUPABASE_URL}/rest/v1/loom_media?on_conflict=tenant_id,clip_id,asset_version"
    data = json.dumps(payload).encode()
    headers = _supabase_headers()
    headers["Content-Type"] = "application/json"
    headers["Prefer"] = "resolution=merge-duplicates,return=representation"
    req = Request(url, data=data, headers=headers, method="POST")
    try:
        with urlopen(req, timeout=30) as resp:
            rows = json.loads(resp.read().decode() or "[]")
            row = rows[0] if isinstance(rows, list) and rows else None
            print(f"[media] registered {clip_id}@{asset_version} clean_source={bool(payload.get('clean_source_url'))}", flush=True)
            return row
    except Exception as e:
        print(f"[media] loom_media upsert failed for {clip_id}: {e}", flush=True)
        return None


def derive_clean_source_for_edit(job_id, ready_file):
    """The clean pre-hook/pre-subtitle sibling for a JUST-FINALIZED edit render.

    Returns the four `register_edited_asset` payload fields (p_clean_source_*)
    for the artifact the edited render was made from, or {} when no clean
    sibling genuinely exists in this job's output directory.

    It is the SAME derivation the ingest path already uses
    (`register_clip_media` -> `_strip_clean_filename`): the clean file is a
    sibling of the rendered file in `OPENSHORTS_OUTPUT/<job_id>/`, reachable by
    walking the `subtitled_<ts>_` / `hooked_<ts>_` / `hook_` prefixes back.

    HONESTY: an empty dict (NULL fields) is the correct answer when the sibling
    is not on disk — the version then stays truthfully replacement-unavailable.
    Nothing here fabricates a reference, guesses a path, or borrows another
    version's clean source; existence on disk is the evidence.
    """
    base = os.path.basename(str(ready_file or ""))
    if not (job_id and base):
        return {}
    clean_name = _strip_clean_filename(base)
    if not clean_name:
        return {}
    burned_prefix = bool(re.match(r'^(?:subtitled_\d+_|hooked_\d+_|hook_)', base))
    if clean_name == base and burned_prefix:
        # A burned derivative we could not walk back to a clean sibling: quite
        # explicitly NOT the burned bytes presented as clean.
        print(f"[complete] no clean sibling derivable from {base}", flush=True)
        return {}
    clean_path = os.path.join(OPENSHORTS_OUTPUT, job_id, clean_name)
    if not os.path.exists(clean_path):
        print(f"[complete] clean sibling absent: {clean_path}", flush=True)
        return {}
    up = ghl_upload_file(clean_path, clean_name)
    if not up or not up.get("url"):
        print(f"[complete] clean sibling upload failed: {clean_name} "
              f"({ghl_last_upload_error() or 'unknown'})", flush=True)
        return {}
    return {
        "p_clean_source_url": up.get("url"),
        "p_clean_source_hash": _sha256_file(clean_path),
        "p_clean_source_size_bytes": os.path.getsize(clean_path),
        "p_clean_source_filename": clean_name,
    }


def _url_reachable(url: str, timeout: int = 8) -> bool:
    """True if the URL answers a byte-range GET (filesafe CDN rejects the default
    Python-urllib UA, so send a browser UA)."""
    if not url:
        return False
    req = Request(url, method="GET")
    req.add_header("Range", "bytes=0-0")
    req.add_header("User-Agent", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36")
    try:
        with urlopen(req, timeout=timeout) as resp:
            return resp.status in (200, 206)
    except Exception:
        return False


def resolve_clip_clean_source(tenant_id, clip_id):
    """Return the durable clean_source_url for a clip (the original render's
    loom_media row carries it), or None. Used to gate Edit readiness for
    final_publication cards whose clean source was persisted at moment review."""
    rows = supabase_filter_get("loom_media", {"tenant_id": tenant_id, "clip_id": clip_id})
    for r in (rows or []):
        if isinstance(r, dict) and r.get("clean_source_url"):
            return r.get("clean_source_url")
    return None


def _download(url: str, dest: str, timeout: int = 120) -> bool:
    """Download a URL to a local path (chunked, browser UA). Returns True on
    success. filesafe CDN rejects the default Python-urllib UA (403)."""
    try:
        req = Request(url, method="GET")
        req.add_header("User-Agent", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36")
        with urlopen(req, timeout=timeout) as resp, open(dest, "wb") as out:
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                out.write(chunk)
        return os.path.exists(dest) and os.path.getsize(dest) > 0
    except Exception as e:
        print(f"[rehydrate] download failed: {e}", flush=True)
        return False


def rehydrate_edit_job(edit_session_id, tenant_id, clip_id, source_asset_version):
    """Reconstruct a LIVE OpenShorts edit-job workspace from the durable clean
    source, so the existing OpenShorts editor/render endpoints (/api/clip/rerender,
    /api/hook, /api/subtitle, ...) work after the original job was evicted.

    The OpenShorts editor is NOT stateless — it expects an output/<job_id>/ dir
    with a <base>_metadata.json (shorts + transcript) and the clean clip file.
    This rebuilds exactly that from LOOM's durable records and registers it in
    the backend's in-memory jobs table (via /api/status recovery).

    Idempotent: the temp job id is derived deterministically from the edit
    session, and an existing workspace is reused. Returns (temp_job_id,
    clip_index) or (None, None) on failure. The client never chooses the temp id.
    """
    if not (edit_session_id and tenant_id and clip_id and source_asset_version):
        return None, None
    sid = str(edit_session_id).replace("-", "")
    temp_job_id = "loom-" + sid[:12]
    job_dir = os.path.join(OPENSHORTS_OUTPUT, temp_job_id)
    base_name = f"loom_{sid[:8]}"
    metadata_path = os.path.join(job_dir, f"{base_name}_metadata.json")
    clean_name = f"{base_name}_clip_1.mp4"
    clean_path = os.path.join(job_dir, clean_name)

    # Idempotent: reuse an already-rehydrated workspace.
    if os.path.exists(metadata_path) and os.path.exists(clean_path):
        return temp_job_id, 0

    # Source-review launches are not loom_media rows. Rendered clips keep the
    # existing resolver so a live workspace is rebuilt from loom_media.
    if str(clip_id).startswith("src-"):
        durable, reason = resolve_durable_bootstrap_diag(
            tenant_id, clip_id, source_asset_version)
    else:
        durable = resolve_durable_bootstrap(tenant_id, clip_id, source_asset_version)
        reason = "ok" if durable else "missing_durable_fields"
    if not durable:
        print(f"[rehydrate] no durable bootstrap for {clip_id}@{source_asset_version} "
              f"reason={reason}", flush=True)
        return None, None
    clean_url = durable.get("clean_source_url") or durable.get("media_url")
    if not clean_url:
        print(f"[rehydrate] no clean source for {clip_id}", flush=True)
        return None, None

    os.makedirs(job_dir, exist_ok=True)
    if not _download(clean_url, clean_path):
        return None, None

    # The clean source is the full reframed clip; its own timeline is [0, dur].
    dur = probe_duration(clean_path)
    if dur <= 0:
        dur = float(durable.get("duration_seconds") or 0)
    if dur <= 0:
        print(f"[rehydrate] unknown duration for {clip_id}", flush=True)
        return None, None

    # Reconstruct the transcript (Whisper shape) from the durable word blocks.
    # Always non-empty: the /api/subtitle endpoint requires a truthy transcript
    # in metadata (even when the caller supplies its own edited `words`).
    words = []
    for c in (durable.get("captions") or []):
        try:
            words.append({
                "word": str(c.get("text") or c.get("word") or ""),
                "start": float(c.get("startMs") or 0) / 1000.0,
                "end": float(c.get("endMs") or 0) / 1000.0,
            })
        except (TypeError, ValueError):
            continue
    transcript = ({"segments": [{"start": 0.0, "end": dur, "words": words}], "language": "en"}
                  if words else {"segments": [], "language": "en"})

    metadata = {
        "shorts": [{
            "start": 0.0,
            "end": dur,
            "video_title_for_youtube_short": durable.get("title") or "LOOM Edit",
            "recipe": {
                "v": 1,
                "segments": [{"start": 0.0, "end": dur}],
                "canonical_range": {"start": 0.0, "end": dur},
                "framing": durable.get("framing") or "auto",
            },
        }],
        "transcript": transcript,
        "output_format": "auto",
    }
    with open(metadata_path, "w") as f:
        json.dump(metadata, f)

    # Register the job in the backend's in-memory table: /api/status triggers
    # _recover_jobs_from_disk() (the editor endpoints require jobs[id], not just
    # files on disk).
    try:
        req = Request(f"http://127.0.0.1:8000/api/status/{temp_job_id}", method="GET")
        with urlopen(req, timeout=30) as resp:
            status = resp.status
    except HTTPError as e:
        status = e.code
    except Exception as e:
        print(f"[rehydrate] backend recovery probe failed: {e}", flush=True)
        return None, None
    if status != 200:
        print(f"[rehydrate] backend recovery returned {status} for {temp_job_id}", flush=True)
        return None, None
    print(f"[rehydrate] temp job {temp_job_id} ready for {clip_id}@{source_asset_version}", flush=True)
    return temp_job_id, 0


def _card_upload_limit(channel_id: str) -> tuple[int, int]:
    """(limit_bytes, tier) for the guild that owns the review channel.

    Falls back to the tier-0 limit when the lookup fails — the conservative
    (smaller) limit, so an unknown guild can never license an oversized upload.
    """
    try:
        _status, ch = http_json("GET", f"{DISCORD_API}/channels/{channel_id}", token=DISCORD_BOT_TOKEN)
        guild_id = (ch or {}).get("guild_id")
        if guild_id:
            info = discord_media.guild_upload_limit(DISCORD_BOT_TOKEN, str(guild_id))
            return (int(info.get("limit_bytes") or discord_media.TIER_LIMITS[0]),
                    int(info.get("premium_tier") or 0))
    except Exception as e:  # noqa: BLE001 - never block a card on this lookup
        print(f"[card] upload-limit lookup failed ({type(e).__name__}: {e}) — assuming tier 0", flush=True)
    return discord_media.TIER_LIMITS[0], 0


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

    # Persist the durable clean-source + burned canonical artifact at ingest time
    # (moment review = a freshly rendered clip). Non-fatal: the card still sends
    # if this fails, but the durable clean source is what lets hook replacement
    # re-render from clean after OpenShorts evicts the job.
    media_row = None
    if review_stage == "moment":
        try:
            media_row = register_clip_media(tenant_id, clip_id, asset_version, job_id, ready_file,
                                            payload.get("master_asset"),
                                            duration_seconds=payload.get("duration_seconds"))
        except Exception as e:
            print(f"[media] register_clip_media failed (non-fatal): {e}", flush=True)

    # Edit readiness: never advertise Edit without a VERIFIED durable clean
    # pre-hook source. A clip without one can only stack a new hook on the burned
    # derivative (or fail outright once the job is evicted). Verify presence AND
    # reachability; emit an alert with full identity — never silently accept.
    if review_stage == "moment":
        clean_source = (media_row or {}).get("clean_source_url")
    else:
        clean_source = resolve_clip_clean_source(tenant_id, clip_id)
    edit_ready = bool(clean_source) and _url_reachable(clean_source)
    if not edit_ready:
        print(f"[media] ALERT editor-unavailable tenant={tenant_id} job={job_id} "
              f"clip={clip_id}@{asset_version} stage={review_stage} "
              f"clean_source={clean_source or 'MISSING'}", flush=True)

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

        # Tier-aware oversize guard. Discord aborts an over-limit multipart POST
        # mid-flight, surfacing as a bare "Broken pipe" that loses the ENTIRE
        # review transaction (token stranded with no message, Edit unreachable).
        # Resolve the guild's real per-file limit BEFORE uploading and, when the
        # proxy does not fit, attach a single poster frame instead of the video —
        # the same shared primitive the acquisition path uses. The card still
        # posts, the token stays linked, Edit stays available when a clean source
        # exists, and the card states the real numbers. Never a silent failure.
        upload_limit, tier = _card_upload_limit(channel_id)
        attach_path, attach_name, attach_type = proxy_path, proxy_name, "video/mp4"
        fallback_reason = ""
        if proxy_size > upload_limit:
            try:
                poster_path = os.path.join(tmpdir, discord_media.POSTER_NAME)
                discord_media.extract_poster(master_path, poster_path, at_seconds=0.5)
                attach_path = poster_path
                attach_name = discord_media.POSTER_NAME
                attach_type = "image/jpeg"
                fallback_reason = discord_media.oversize_reason(proxy_size, upload_limit, tier)
                print(f"[card] oversize fallback for {clip_id}@{asset_version}: {fallback_reason}", flush=True)
            except Exception as e:  # noqa: BLE001
                print(f"[card] poster fallback failed for {clip_id}: {type(e).__name__}: {e} "
                      f"— attempting the video anyway", flush=True)

        # 2) Upload as Discord attachment + post message with buttons.
        approve_id = make_custom_id(token, "approve")
        reject_id  = make_custom_id(token, "reject")
        hold_id    = make_custom_id(token, "hold")
        edit_id    = make_custom_id(token, "edit")
        details_id = make_custom_id(token, "details")

        # Compact, conditional card (Framer-inspired hierarchy). Only fields
        # with meaningful data are shown — never "—"/"Unavailable"/debug states.
        is_final = review_stage == "final_publication"
        header = "🎬 FINAL REVIEW · EDITED CUT" if is_final else "🎬 MOMENT REVIEW · CANDIDATE"
        approve_label = "Approve for Publication" if is_final else "Approve for Reaction Queue"

        # Display safety: mask explicit terms on the card (display-only).
        hook_display, hook_terms = mask_explicit(payload.get("hook") or "")
        caption_display, caption_terms = mask_explicit(payload.get("caption") or "")
        explicit_terms = sorted(set(hook_terms) | set(caption_terms))

        # Candidate/source title (conditional, ~2 lines max).
        title = (payload.get("proposed_shorts_title") or payload.get("source_title") or "").strip()

        fields = []
        if hook_display.strip():
            fields.append({"name": "Hook", "value": hook_display[:120], "inline": False})
        if caption_display.strip():
            fields.append({"name": "Caption", "value": caption_display[:160], "inline": False})

        stats = []
        score = payload.get("overall_score")
        if score not in (None, "", "—"):
            stats.append(f"**{score}**")
        dur = _compact_duration(payload.get("duration_seconds"))
        if dur:
            stats.append(dur)
        ver = _short_version(asset_version)
        if ver:
            stats.append(ver)
        if stats:
            fields.append({"name": "Score · Duration · Version", "value": " · ".join(stats), "inline": False})

        changes = build_changes_summary(payload.get("corrections"))
        if changes:
            fields.append({"name": "Changes", "value": changes[:400], "inline": False})

        # Warnings (conditional, display-only).
        if explicit_terms:
            fields.append({"name": "⚠️ Explicit language", "value": "Detected (masked): " + ", ".join(explicit_terms), "inline": False})
        rights = payload.get("rights_policy")
        if rights:
            fields.append({"name": "⚠️ Rights/policy", "value": str(rights)[:400], "inline": False})
        # Reliability gate: never advertise Edit without a verified durable clean
        # source. Omit the Edit control and say so explicitly on the card.
        if not edit_ready:
            fields.append({"name": "⚠️ Editor source unavailable", "value": "Clean pre-hook source not persisted/reachable — editing (hook replacement) is disabled for this clip.", "inline": False})
        # Oversize fallback: say exactly what the card shows and why, with the
        # real numbers, instead of implying a playable preview that is not there.
        if fallback_reason:
            fields.append({"name": "⚠️ Preview not attached", "value": fallback_reason + ". A poster frame is attached instead.", "inline": False})

        # Footer: tenant · lineage · fixture badge · publication state.
        footer = []
        if tenant_id:
            footer.append(str(tenant_id))
        if payload.get("lineage"):
            footer.append(str(payload["lineage"]))
        if payload.get("is_fixture"):
            footer.append("🧪 Test fixture")
        if not payload.get("publication_enabled"):
            footer.append("Publication locked")
        footer_text = " · ".join(footer) if footer else None

        embed = {
            "title": header,
            "color": card_color(payload),
            "fields": fields,
        }
        if title:
            embed["description"] = title[:256]
        if footer_text:
            embed["footer"] = {"text": footer_text[:2048]}

        buttons = [
            {"type": 2, "style": 3, "label": approve_label, "custom_id": approve_id},
            {"type": 2, "style": 4, "label": "Reject", "custom_id": reject_id},
            {"type": 2, "style": 2, "label": "Hold", "custom_id": hold_id},
        ]
        if edit_ready:
            buttons.append({"type": 2, "style": 1, "label": "Edit", "custom_id": edit_id})
        buttons.append({"type": 2, "style": 2, "label": "Details", "custom_id": details_id})
        components = [{"type": 1, "components": buttons}]

        message_payload = {
            "content": f"🎬 **{header}**",
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
        body += f'Content-Disposition: form-data; name="files[0]"; filename="{attach_name}"\r\n'.encode()
        body += f"Content-Type: {attach_type}\r\n\r\n".encode()
        with open(attach_path, "rb") as f:
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

_ACQ_STORE = None


def _acquisition_store():
    """Durable acquisition store, opt-in; process-local by default.

    Creating jobs in the production database is enabled ONLY by an explicit
    ``LOOM_ACQUISITION_DURABLE_STORE=1``; the default keeps qualification local so
    no production row is written without an authorized decision.
    """
    global _ACQ_STORE
    if _ACQ_STORE is None:
        if (SUPABASE_URL and SUPABASE_KEY
                and os.environ.get("LOOM_ACQUISITION_DURABLE_STORE") == "1"):
            _ACQ_STORE = acquisition_ops.SupabaseLaneStore(SUPABASE_URL, SUPABASE_KEY)
        else:
            _ACQ_STORE = acquisition_ops.default_store()
    return _ACQ_STORE


def _acquisition_live_runner():
    """The paid spend path — built only when explicitly switched on.

    Returns ``None`` unless ``LOOM_ACQUISITION_LIVE=1`` AND every prerequisite is
    present; no runner means an Acquire click authorizes nothing and spends
    nothing. The runner itself re-checks all gates before dispatching.
    """
    return acquisition_ops.build_live_runner_from_env(
        store=_acquisition_store(),
        url=SUPABASE_URL, key=SUPABASE_KEY,
        library_dir=str(_library_root.library_dir()),
        tmp_dir=str(_library_root.tmp_dir()),
    )


def acquisition_route(custom_id: str):
    """Pure routing decision for approved-acquisition card buttons.

    Returns the parsed action/ref for an ``AQ:<code>:<ref>`` id, else None — so
    the route can be asserted without an HTTP server.
    """
    return acquisition_ops.parse_custom_id(custom_id)


def acquisition_health():
    """Readiness block surfaced by GET /health (never exposes secrets).

    Defensive: a liveness probe must never be taken down by a lane error, so a
    failure degrades to an honest ``route_active: false`` block instead of
    raising and closing the connection.
    """
    try:
        return acquisition_ops.acquisition_health(store=_acquisition_store())
    except Exception as e:  # noqa: BLE001
        print(f"[acquisition] health probe failed: {type(e).__name__}", flush=True)
        return {"route": "AQ", "route_active": False, "error": type(e).__name__}


def source_review_health():
    """Readiness block for the Source Review repairs (never exposes secrets).

    Reports the approve→acquisition handoff and whether the authoritative
    candidate table (migration 040) is live in the DEPLOYED schema — probing
    the running system, never the repo file.
    """
    block = {"route": "SR", "route_active": True, "approve_handoff": True,
             "duplicate_click_stored_decision": True, "open_source_button": True,
             "editor_launch": True,
             "worker_error_boundary": "identity_through_patch_v2"}
    try:
        block["candidates_table"] = bool(
            source_review._candidates_table_available(SUPABASE_URL, SUPABASE_KEY))
    except Exception as e:  # noqa: BLE001
        block["candidates_table"] = False
        block["probe_error"] = type(e).__name__
    return block


class Handler(BaseHTTPRequestHandler):
    server_version = "LOOMReviewAdapter/1.0"

    def _send_json(self, status_code, obj):
        data = json.dumps(obj).encode()
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self._emit_cors()
        self.end_headers()
        self.wfile.write(data)
        # Flush immediately so the response is on the wire NOW, never buffered
        # behind later network work (the Discord 3-second interaction deadline).
        try:
            self.wfile.flush()
        except Exception:
            pass

    def _emit_cors(self):
        """Emit exact-origin CORS headers only for allowlisted origins. The
        final Activity path uses the SDK + same-origin funnel (no CORS needed);
        this is strictly for the dev scaffold."""
        origin = allowed_cors_origin(self.headers.get("Origin") or "")
        if not origin:
            return
        self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")

    def do_OPTIONS(self):
        self.send_response(204)
        self._emit_cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", 0))
        return self.rfile.read(length) if length else b""

    def _log_request(self):
        """Minimal access log: method + path only. The query string is stripped
        (it can carry the single-use launch token); bodies are never read or
        logged, so no OAuth code / token / secret ever reaches the log."""
        path = (self.path or "").split("?", 1)[0] or "/"
        print(f"[{time.strftime('%H:%M:%S')}] {self.command} {path} <- {self.address_string()}", flush=True)

    def do_GET(self):
        self._log_request()
        if self.path == "/health":
            self._send_json(200, {"status": "ok",
                                  "build": {"source_sha256": SOURCE_SHA256,
                                            "loaded_at": LOADED_AT},
                                  "acquisition": acquisition_health(),
                                  "source_review": source_review_health()})
            return
        # DELIBERATELY PUBLIC, non-sensitive: /api/config reports server
        # FEATURE FLAGS (billing on/off, managed mode, job retention, local-LLM
        # availability). It carries no tenant, clip, media, token or secret
        # data, and the OpenShorts client fetches it BEFORE any editor session
        # exists (AuthContext on first load; MediaInput on the landing page) with
        # no Authorization header. It is therefore allow-listed here, ahead of
        # the editor gateway, instead of being answered with a misleading 401.
        # Everything else under /api/ stays session-authenticated.
        if self.path.split("?", 1)[0] == "/api/config":
            self._handle_public_config()
            return
        # Authenticated editor API (read endpoints).
        if self.path.startswith("/api/"):
            self._handle_editor_api("GET")
            return
        # Durable editor bootstrap (read) — resolves the review asset from
        # LOOM media/version records, not the transient /api/status state.
        if self.path == "/discord/editor/bootstrap":
            self._handle_editor_bootstrap()
            return
        # Never allow state-changing GET links.
        self._send_json(404, {"error": "not found"})

    def do_POST(self):
        self._log_request()
        path = self.path.rstrip("/") or "/"
        if path == "/health":
            self._send_json(200, {"status": "ok"})
            return
        # Authenticated editor API (write endpoints).
        if path.startswith("/api/"):
            self._handle_editor_api("POST")
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
        if path == "/discord/editor/oauth":
            self._handle_editor_oauth()
            return
        if path == "/discord/editor/pending":
            self._handle_editor_pending()
            return
        if path == "/discord/editor/draft":
            self._handle_editor_draft()
            return
        if path == "/discord/editor/capcut":
            self._handle_editor_capcut()
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

        if itype == 2:  # APPLICATION_COMMAND (slash command)
            self._handle_slash_command(interaction)
            return

        if itype == 3:  # MESSAGE_COMPONENT (button click)
            self._handle_button(interaction)
            return

        if itype == 4:  # APPLICATION_COMMAND_AUTOCOMPLETE (destination choices)
            self._handle_autocomplete(interaction)
            return

        if itype == 5:  # MODAL_SUBMIT (reject modal)
            self._handle_modal_submit(interaction)
            return

        # Acknowledge unknown interactions quickly.
        self._send_json(200, {"type": 4, "data": {"content": "Unsupported interaction.", "flags": 64}})

    def _resolve_identity(self, parsed):
        token = parsed.get("t")
        return resolve_review_token(token) if token else None

    # -- Source Review Inbox (slash command + buttons) ---------------------

    def _handle_slash_command(self, interaction):
        data = interaction.get("data", {})
        name = data.get("name", "")
        if name == source_review.COMMAND_NAME:
            self._handle_source_review_command(interaction)
            return
        if name == manual_intake.COMMAND_NAME:
            self._handle_submit_source_command(interaction)
            return
        if name == tenant_routing.ASSIGN_COMMAND_NAME:
            self._handle_assign_source_command(interaction)
            return
        if name == MANUAL_MEDIA_COMMAND or data.get("type") == 3:
            self._handle_manual_media_intake(interaction)
            return
        self._send_json(200, {"type": 4, "data": {"content": "Unknown command.", "flags": 64}})

    def _mi_status(self, application_id, interaction_token, text, *, stage=""):
        """Publish ONE truthful coarse stage on the SAME deferred message.

        Called only once a stage has actually BEGUN, so the operator can tell
        processing from stalled, crashed or timed out. Deliberately coarse: no
        invented percentages and no status spam, but no silent processing window.
        A status failure must never abort the work it is reporting on.
        """
        try:
            self._patch_original(application_id, interaction_token, content=text)
        except Exception as e:  # noqa: BLE001 — reporting must not break the work
            print(f"[manual-intake] status update failed stage={stage} "
                  f"error={type(e).__name__}", flush=True)

    def _mi_present_choice(self, application_id, interaction_token, *, ref,
                           whole_allowed: bool, refusal: str = "",
                           source_duration_s: float | None = None,
                           origin_label: str = "source",
                           start_hint_ms: int = 0):
        """Offer WHOLE VIDEO / SELECT SEGMENT for ANY manual media.

        An operator-INTENT layer above the shared working-source contract: it decides
        nothing itself. It asks the gate (through ``whole_source_verdict``) whether
        taking the source whole is legal, DISABLES the control when it is not, and
        explains the refusal in the gate's own vocabulary rather than inventing a
        second reason. Shared by every platform and by supplied/local media.
        """
        if isinstance(source_duration_s, (int, float)) and source_duration_s > 0:
            length = (f"~{int(source_duration_s // 60)}m {int(source_duration_s % 60)}s"
                      if source_duration_s >= 60 else f"~{source_duration_s:.0f}s")
        else:
            length = "length not proven"
        lines = [f"🎬 **Choose how to ingest this {origin_label}** — {length}."]
        if whole_allowed:
            lines.append("**Whole video** takes the source as it is. "
                         "**Select segment** bounds it to a start/end window.")
        else:
            why = _MI_REFUSAL_REASONS.get(refusal, _MI_REFUSAL_DEFAULT)
            lines.append(f"**Whole video is not available** — {why}. Choose "
                         "**Select segment** and give a start and an end time.")
        if start_hint_ms and int(start_hint_ms) > 0:
            # The URL's own timestamp is a DEFAULT, stated as such: it pre-fills Start
            # and the operator still chooses the End. Never an approved boundary.
            try:
                _clock = manual_media_intake._acq("source_window").human_clock(
                    int(start_hint_ms))
            except Exception:  # noqa: BLE001 — a hint must never break the prompt
                _clock = ""
            if _clock:
                lines.append(f"The link's timestamp (**{_clock}**) will pre-fill Start; "
                             "you still choose the End.")
        components = [{"type": 1, "components": [
            {"type": 2, "style": 1, "label": "Whole video",
             "custom_id": f"{MI_CHOICE_PREFIX}:{MI_ACT_WHOLE}:{ref}",
             "disabled": not whole_allowed},
            {"type": 2, "style": 3, "label": "Select segment",
             "custom_id": f"{MI_CHOICE_PREFIX}:{MI_ACT_SEGMENT}:{ref}"},
        ]}]
        self._patch_original(application_id, interaction_token,
                             content="\n".join(lines), components=components)

    def _mi_choice_worker(self, interaction, action, ref):
        """Resume a Manual Intake choice: WHOLE VIDEO, or a modal-supplied segment.

        Both outcomes converge on the SAME intake execution the text syntax uses, so
        there is exactly one acquisition path. Nothing is acquired until the operator
        has chosen, and a malformed range never reaches acquisition.
        """
        application_id = str(interaction.get("application_id") or "")
        interaction_token = str(interaction.get("token") or "")
        try:
            pending = _mi_pending(ref)
            # RESUME RESOLVED / RESUME MISS — the second half of the route. A MISS with a
            # CHOICE CLICK RECEIVED line above is a resume-state problem; a MISS (or
            # nothing at all) with no click line means the interaction never arrived.
            _mi_trace("resume_resolved" if pending else "resume_miss",
                      platform=str((pending or {}).get("platform") or ""),
                      source_id=str((pending or {}).get("source_id") or ""),
                      action=action, ref=ref,
                      reason="" if pending else "resume_state_absent")
            if not pending:
                self._patch_original(application_id, interaction_token, content=(
                    "⌛ This choice expired or was already used. Re-post the source URL "
                    "in #manual-intake and run `Apps → intake-media` again."))
                return
            parsed = {"url": pending["url"], "tenant": pending.get("tenant", ""),
                      "start_s": None, "end_s": None,
                      "window_intent": False, "window_problem": "",
                      # The choice ref travels with the resume so every later stage can
                      # log the SAME correlation id the offer logged.
                      "_mi_ref": ref}
            if action == MI_ACT_SEGMENT:
                values: dict[str, str] = {}
                for row in ((interaction.get("data") or {}).get("components") or []):
                    for comp in (row.get("components") or []):
                        values[str(comp.get("custom_id") or "")] = comp.get("value", "")
                # The CANONICAL parser (the same one text intake uses). A range it
                # cannot read is refused HERE, before any acquisition, never coerced.
                try:
                    start_ms, end_ms = acquisition_ops.parse_window_values(values)
                except Exception as exc:  # noqa: BLE001 — a bad range is refused early
                    self._patch_original(application_id, interaction_token, content=(
                        "❌ That segment was not understood. Use SS, MM:SS or HH:MM:SS "
                        "for BOTH times (e.g. `02:00` / `03:00`). "
                        f"[{getattr(exc, 'code', '') or 'BAD_TIMESTAMP'}]"))
                    return
                parsed["start_s"] = start_ms / 1000.0
                parsed["end_s"] = end_ms / 1000.0
                parsed["window_intent"] = True
            _supplied = pending.get("local_path") or ""
            try:
                self._manual_intake_execute(interaction, parsed,
                                            dict(pending.get("attachment") or {}),
                                            supplied_path=_supplied)
            finally:
                # The choice is consumed: release the retained supplied copy (if any).
                # A URL-only source retained nothing, so this is a no-op there.
                _mi_forget_pending(ref, release=True)
        except Exception as e:  # noqa: BLE001 — the ACK is not success
            frames = ";".join(f"{Path(f.filename).name}:{f.lineno}:{f.name}"
                              for f in traceback.extract_tb(e.__traceback__))
            print(f"[manual-intake] choice worker failed error={type(e).__name__} "
                  f"detail={str(e)[:200]!r} frames={frames}", flush=True)
            try:
                self._patch_original(application_id, interaction_token, content=(
                    "❌ The segment intake failed. Nothing was silently dropped — "
                    "re-running the command is safe."))
            except Exception:  # noqa: BLE001
                pass

    def _handle_manual_media_intake(self, interaction):
        """MESSAGE context-menu ingress for operator-supplied media.

        The operator posts the media file in #manual-intake with the original URL
        (plus optional start=/end=/tenant=), then invokes Apps -> intake-media.
        Nothing here touches the network before the ACK.
        """
        data = interaction.get("data", {}) or {}
        target_id = str(data.get("target_id") or "")
        resolved = ((data.get("resolved") or {}).get("messages") or {})
        message = resolved.get(target_id) or {}
        content = message.get("content") or ""
        attachments = message.get("attachments") or []
        channel_id = str(message.get("channel_id")
                         or (interaction.get("channel") or {}).get("id") or "")
        parsed = manual_media_intake.parse_intake_message(content)
        attachment = manual_media_intake.scan_for_video(attachments)
        problems = []
        if not parsed.get("url"):
            problems.append("no source URL in the message (add `url: https://…`)")
        # WINDOW INTENT THAT CANNOT BE HONOURED IS A REFUSAL, NOT A DOWNGRADE.
        # Silently doing this as a full-source acquisition is the defect this
        # guard exists to prevent. No window intent at all is still fine.
        if parsed.get("window_intent") and parsed.get("window_problem"):
            problems.append(
                "window intent detected but no valid START+END pair "
                f"(`{parsed['window_problem']}`) — supply BOTH, e.g. "
                "`START 120` / `END 180`. LOOM will not fall back to the whole source")
        # Two ingress modes, ONE downstream path:
        #   Mode A — supplied media + source URL (operator already has the bytes)
        #   Mode B — source URL only: LOOM resolves the platform and downloads with
        #            the EXISTING local/free acquisition path ($0).
        # Both converge at `durable media available`. Nothing here does network
        # work: mode B resolves/downloads in the worker, after the ACK.
        if problems:
            # Local-only check: no network work happens before any response.
            self._send_json(200, {"type": 4, "data": {
                "content": "❌ Cannot ingest: " + "; ".join(problems) + ".", "flags": 64}})
            return
        # Deferred CHANNEL MESSAGE (type 5) — the defer type an application
        # command accepts. Type 6 is for component interactions only; Discord
        # rejects it here, which the operator sees as "didn't respond in time".
        # Nothing after this point may run before this response is returned.
        self._send_json(200, {"type": 5, "data": {"flags": 64}})
        threading.Thread(target=self._manual_media_worker,
                         args=(interaction, dict(parsed), dict(attachment or {})),
                         daemon=True).start()

    def _manual_media_worker(self, interaction, parsed, attachment):
        """Entry point for the MESSAGE context-menu command (modes A and B)."""
        # INTAKE RECEIVED — the ingress point, before any identity is resolved (the
        # source has not been canonicalised yet, so only the mode and submitter exist).
        _mi_trace("intake_received", mode="upload" if attachment else "url",
                  submitter=self._reviewer_name(interaction))
        self._manual_intake_execute(interaction, parsed, attachment)

    def _manual_intake_execute(self, interaction, parsed, attachment,
                               supplied_path: str = ""):
        """Post-ACK work for the Manual Intake contract. Never raises; ACK is not success.

        Durable-first ordering: probe -> GHL durable copy -> submission row ->
        Content Intelligence -> review card. Failures name the exact stage and
        what durable state survived. Local working media is deleted only after a
        durable copy exists and analysis has finished, so no permanent duplicate
        source library is kept on this machine.
        """
        application_id = str(interaction.get("application_id") or "")
        interaction_token = str(interaction.get("token") or "")
        submitter = self._reviewer_name(interaction)
        stage = "channel_guard"
        temp_dir = ""
        durable_url = ""
        persisted = False
        # ONE stable source identity for the whole route, resolved locally (no network)
        # BEFORE any stage runs, so every later line — including a failure — names the
        # same source. Never logs the URL itself, only the platform's public id.
        _mi_platform, _mi_source_id = _mi_identity(parsed)
        _mi_mode = "upload" if (attachment or supplied_path) else "url"
        _mi_ref = str(parsed.get("_mi_ref") or "")
        try:
            # EXECUTOR START — proves the proven executor was ENTERED. Without it, "the
            # click did nothing" and "the executor never ran" are indistinguishable.
            _mi_trace("executor_start", platform=_mi_platform, source_id=_mi_source_id,
                      mode=_mi_mode, resumed="true" if _mi_ref else "false", ref=_mi_ref)
            # Everything below runs AFTER the acknowledgement: the channel guard
            # costs a network lookup and must never precede the defer.
            data_ctx = interaction.get("data", {}) or {}
            msg_ctx = ((data_ctx.get("resolved") or {}).get("messages") or {}).get(
                str(data_ctx.get("target_id") or "")) or {}
            channel_id = str(msg_ctx.get("channel_id")
                             or (interaction.get("channel") or {}).get("id") or "")
            try:
                expected = resolve_manual_intake_channel()
            except Exception as e:  # noqa: BLE001
                self._patch_original(application_id, interaction_token, content=(
                    f"❌ Manual intake channel unavailable ({type(e).__name__})."))
                return
            if channel_id and channel_id != expected:
                self._patch_original(application_id, interaction_token, content=(
                    f"❌ Run this on a post in <#{expected}> — that is where supplied "
                    f"media is ingested."))
                return
            stage = "resolve_tenant"
            # Defence in depth for the gate above: the worker refuses an
            # unhonourable window BEFORE any network work, so no path can turn an
            # intended bounded request into a full-source acquisition.
            if parsed.get("window_intent") and parsed.get("window_problem"):
                raise manual_media_intake.MediaIntakeError(
                    "WINDOW_MALFORMED" if parsed["window_problem"] == "window_unparsed"
                    else "WINDOW_INCOMPLETE",
                    "window intent detected but no valid START+END pair")
            active = tenant_routing.list_active_tenants(SUPABASE_URL, SUPABASE_KEY)
            tenant = manual_intake.validate_destination(
                parsed.get("tenant") or MANUAL_MEDIA_TENANT_DEFAULT, active)
            stage = "acquire_media"
            temp_dir = tempfile.mkdtemp(prefix="loom-manual-intake-")
            # A stage is published only once it has actually BEGUN, so the operator can
            # always tell processing from stalled/crashed/timed out.
            self._mi_status(
                application_id, interaction_token,
                f"⏳ Acquiring{_mi_range_text(parsed)}…", stage="acquire_media")
            # Acquisition provenance. Records the MECHANISM and keeps TWO
            # COORDINATE SYSTEMS explicitly distinct so neither is ever inferred
            # from the other: the ORIGINAL source timestamps the operator asked for,
            # and the WORKING asset's own 0-based timeline (working t=0 is
            # `working_timebase_origin_s` in the source).
            acq_prov: dict = {
                "acquisition_mode": "full_item",
                "acquisition_mechanism": "",
                "original_start_s": parsed.get("start_s"),
                "original_end_s": parsed.get("end_s"),
                "working_timebase_origin_s": 0.0,
                "working_source_duration_s": None,
                "window_seconds": None,
                "range_fingerprint": None,
                "platform_capability_state": None,
                "platform_proven_modes": None,
                "identity_reconciliation": None,
            }
            if attachment or supplied_path:
                # ACQUISITION START (supplied media) — the mechanism and the mode are
                # both decided HERE, so the next result line can be read against them.
                _mi_trace("acquisition_start", platform=_mi_platform,
                          source_id=_mi_source_id, mode=_mi_mode,
                          mechanism="local:file_upload",
                          window_mode=("bounded_segment"
                                       if (parsed.get("start_s") is not None
                                           or parsed.get("end_s") is not None)
                                       else "whole_item"))
                if supplied_path and os.path.exists(supplied_path):
                    # Resuming a choice for a file the operator ALREADY supplied: reuse
                    # the retained copy rather than fetching it a second time.
                    local_path = supplied_path
                    safe_name = re.sub(r"[^A-Za-z0-9._-]", "_",
                                       os.path.basename(supplied_path))[:120]
                    acq_prov["acquisition_mechanism"] = "local:file_upload"
                    print("[manual-intake] resuming with the retained supplied file",
                          flush=True)
                else:
                    safe_name = re.sub(r"[^A-Za-z0-9._-]", "_",
                                       attachment.get("filename") or "supplied.mp4")[:120]
                    local_path = os.path.join(temp_dir, safe_name)
                    manual_media_intake.download_attachment(attachment["url"], local_path)
                    acq_prov["acquisition_mechanism"] = "local:file_upload"
                # OPERATOR WINDOW (local upload): the supplied file may be far longer
                # than the moment wanted, so the EXTRACTED WINDOW becomes the working
                # source. Validated through the SHARED window contract, and it fails
                # closed — it is never downgraded to the whole file.
                if parsed.get("start_s") is not None or parsed.get("end_s") is not None:
                    if parsed.get("start_s") is None or parsed.get("end_s") is None:
                        raise RuntimeError("a bounded window needs both start and end")
                    _wres = manual_media_intake.resolve_source_url(parsed["url"])
                    _w = manual_media_intake.build_operator_window(
                        platform=_wres["platform"], canonical_url=_wres["canonical_url"],
                        content_id=_wres["content_id"],
                        start_s=parsed["start_s"], end_s=parsed["end_s"])
                    local_path = manual_media_intake.extract_local_window(
                        local_path, start_s=parsed["start_s"], end_s=parsed["end_s"],
                        out_dir=temp_dir)
                    _wext = os.path.splitext(local_path)[1] or ".mp4"
                    safe_name = re.sub(
                        r"[^A-Za-z0-9._-]", "_",
                        f"{_wres['platform']}__{_wres['content_id']}"
                        f"__window{int(_w['start_ms'])}-{int(_w['end_ms'])}{_wext}")[:150]
                    acq_prov.update({
                        "acquisition_mode": "bounded_segment",
                        "window_seconds": _w["clip_duration_ms"] / 1000.0,
                        "range_fingerprint": _w["range_fingerprint"],
                        "working_timebase_origin_s": _w["start_ms"] / 1000.0,
                    })
                    print(f"[manual-intake] local window extracted "
                          f"{_w['clip_duration_ms'] / 1000:.3f}s "
                          f"range={str(_w['range_fingerprint'])[:12]}", flush=True)
                else:
                    # NO EXPLICIT INTENT -> the operator chooses. The choice applies to
                    # SUPPLIED media exactly as it does to a URL: SELECT SEGMENT is not
                    # a >300 s recovery mechanism, it is how any manual media is scoped.
                    # Nothing is stored or extracted before the operator answers.
                    _res_a = manual_media_intake.resolve_source_url(parsed["url"])
                    _cap_a = manual_media_intake.resolve_platform_capability(
                        _res_a.get("platform", ""))
                    _dur_a = (manual_media_intake.probe_media(local_path) or {}).get(
                        "duration_seconds")
                    _allowed_a, _refuse_a = manual_media_intake.whole_source_verdict(
                        platform=_cap_a["platform"] or _res_a.get("platform", ""),
                        content_id=str(_res_a.get("content_id") or ""),
                        canonical_url=_res_a["canonical_url"],
                        item_kind=str(_res_a.get("kind") or ""),
                        source_duration_s=_dur_a,
                        requires_window_for_long_source=bool(
                            _cap_a.get("requires_window_for_long_source")))
                    _ref_a = _mi_remember_pending(
                        url=parsed["url"], tenant=tenant, submitter=submitter,
                        channel_id=channel_id, attachment=attachment,
                        local_path=local_path,
                        start_hint_ms=int(_res_a.get("start_hint_ms") or 0),
                        platform=str(_res_a.get("platform") or ""),
                        source_id=str(_res_a.get("content_id") or ""),
                        item_kind=str(_res_a.get("kind") or ""))
                    # The PATCH that carries the choice is an INTERACTION UPDATE, not
                    # an acquisition step: label it truthfully so a delivery failure
                    # is never reported as an acquisition failure.
                    stage = MI_STAGE_INTERACTION_PATCH
                    self._mi_present_choice(
                        application_id, interaction_token, ref=_ref_a,
                        whole_allowed=_allowed_a, refusal=_refuse_a,
                        source_duration_s=_dur_a, origin_label="supplied file",
                        start_hint_ms=int(_res_a.get("start_hint_ms") or 0))
                    print(f"[manual-intake] operator choice offered (supplied file) "
                          f"ref={_ref_a} duration={_dur_a} "
                          f"whole_allowed={_allowed_a} refusal={_refuse_a}", flush=True)
                    # CHOICE OFFERED — the identity every later CLICK/RESUME line must
                    # match. Together they locate the seam a lost interaction died at.
                    _mi_trace("choice_offered", platform=str(_res_a.get("platform") or ""),
                              source_id=str(_res_a.get("content_id") or ""),
                              mode="upload", kind=str(_res_a.get("kind") or ""),
                              duration_s=_dur_a, whole_allowed=_allowed_a,
                              refusal=_refuse_a, ref=_ref_a)
                    return
            else:
                # Mode B — URL only. EXISTING resolver + EXISTING bounded local
                # downloader ($0). Deliberately creates NO library row and NO
                # acquisition job: the durable copy is GHL's.
                #
                # LONG-FORM WITHOUT A WINDOW IS A CHOICE, NOT A DOWNLOAD. The shared
                # gate is the ONE authority on whether a whole-source acquisition is
                # allowed; when it refuses for lack of a window, the operator is
                # offered WHOLE VIDEO / SELECT SEGMENT instead of LOOM downloading a
                # long source and failing later on the size ceiling. Nothing is
                # acquired until the operator has chosen.
                if (parsed.get("start_s") is None and parsed.get("end_s") is None):
                    # NO EXPLICIT INTENT -> the operator chooses, for ANY source, short
                    # or long. Whole video is disabled only when the shared safety
                    # contract refuses it, and the refusal is explained in the gate's
                    # own vocabulary. Nothing is downloaded before the answer.
                    _res_choice = manual_media_intake.resolve_source_url(parsed["url"])
                    _cap_choice = manual_media_intake.resolve_platform_capability(
                        _res_choice.get("platform", ""))
                    _dur_choice = manual_media_intake.probe_source_duration(
                        _res_choice.get("canonical_url") or parsed["url"])
                    _allowed, _refuse = manual_media_intake.whole_source_verdict(
                        platform=_cap_choice["platform"]
                        or _res_choice.get("platform", ""),
                        content_id=str(_res_choice.get("content_id") or ""),
                        canonical_url=_res_choice["canonical_url"],
                        item_kind=str(_res_choice.get("kind") or ""),
                        source_duration_s=_dur_choice,
                        requires_window_for_long_source=bool(
                            _cap_choice.get("requires_window_for_long_source")))
                    _ref = _mi_remember_pending(
                        url=parsed["url"], tenant=tenant, submitter=submitter,
                        channel_id=channel_id,
                        start_hint_ms=int(_res_choice.get("start_hint_ms") or 0),
                        platform=str(_res_choice.get("platform") or ""),
                        source_id=str(_res_choice.get("content_id") or ""),
                        item_kind=str(_res_choice.get("kind") or ""))
                    # Nothing was acquired, so the transient working dir is not
                    # retained: an offered choice is not a failure.
                    manual_media_intake.cleanup(temp_dir)
                    temp_dir = ""
                    # The PATCH that carries the choice is an INTERACTION UPDATE, not
                    # an acquisition step: label it truthfully so a Discord transport
                    # failure is never reported as an acquisition failure.
                    stage = MI_STAGE_INTERACTION_PATCH
                    self._mi_present_choice(
                        application_id, interaction_token, ref=_ref,
                        whole_allowed=_allowed, refusal=_refuse,
                        source_duration_s=_dur_choice,
                        start_hint_ms=int(_res_choice.get("start_hint_ms") or 0))
                    print(f"[manual-intake] operator choice offered ref={_ref} "
                          f"duration={_dur_choice} whole_allowed={_allowed} "
                          f"refusal={_refuse}", flush=True)
                    # CHOICE OFFERED — the identity every later CLICK/RESUME line must
                    # match. Together they locate the seam a lost interaction died at.
                    _mi_trace("choice_offered",
                              platform=str(_res_choice.get("platform") or ""),
                              source_id=str(_res_choice.get("content_id") or ""),
                              mode="url", kind=str(_res_choice.get("kind") or ""),
                              duration_s=_dur_choice, whole_allowed=_allowed,
                              refusal=_refuse, ref=_ref)
                    return
                # ACQUISITION START — the mode, mechanism and window decision that the
                # very next result line has to be read against.
                _mi_trace("acquisition_start", platform=_mi_platform,
                          source_id=_mi_source_id, mode="url",
                          mechanism="local:yt-dlp",
                          window_mode=("bounded_segment"
                                       if parsed.get("start_s") is not None
                                       else "whole_item"))
                local_path, _resolved = manual_media_intake.acquire_url_media(
                    parsed["url"], out_dir=temp_dir,
                    start_s=parsed.get("start_s"), end_s=parsed.get("end_s"))
                _cap = _resolved.get("capability") or {}
                _win = _resolved.get("window")
                _work = _resolved.get("working_source") or {}
                acq_prov.update({
                    "acquisition_mode": _resolved.get("acquisition_mode") or "full_item",
                    "acquisition_mechanism": _cap.get("adapter_id") or "local:yt-dlp",
                    "platform_capability_state": _cap.get("capability_state"),
                    "platform_proven_modes": _cap.get("proven_modes"),
                    "identity_reconciliation": _resolved.get("identity_reconciliation"),
                    "working_source_duration_s": _work.get("duration_seconds"),
                    "working_timebase_origin_s": _work.get("timebase_origin_s", 0.0),
                    "window_seconds": (_win["clip_duration_ms"] / 1000.0) if _win else None,
                    "range_fingerprint": _win["range_fingerprint"] if _win else None,
                })
                # Name the durable copy from the RESOLVED identity so the GHL
                # filename is stable and human-meaningful. (Mode B has no
                # attachment filename; leaving this unset is what raised
                # UnboundLocalError at the durable-store seam.)
                _ext = os.path.splitext(local_path)[1] or ".mp4"
                safe_name = re.sub(r"[^A-Za-z0-9._-]", "_",
                                   f"{_resolved['platform']}__{_resolved['content_id']}{_ext}")[:120]
            size = os.path.getsize(local_path)
            if size <= 0:
                raise RuntimeError("attachment downloaded as 0 bytes")
            if size > MANUAL_MEDIA_MAX_BYTES:
                raise RuntimeError(f"attachment {size} bytes exceeds cap {MANUAL_MEDIA_MAX_BYTES}")
            stage = "probe_media"
            self._mi_status(application_id, interaction_token,
                            "⏳ Validating working source…", stage="probe_media")
            probe = manual_media_intake.probe_media(local_path)
            if not probe.get("has_video"):
                raise RuntimeError("supplied file has no video stream")
            # INTEGRITY GATE — before any durable write. ffprobe metadata alone is
            # explicitly NOT sufficient: a truncated artifact answered
            # has_video=True with a valid resolution and duration. Only decoding
            # part of the media proves it is usable, so a partial file can never
            # become a durable GHL asset or a review candidate.
            stage = "media_integrity"
            verdict = manual_media_intake.verify_artifact_integrity(local_path)
            if not verdict.get("ok"):
                print(f"[manual-intake] integrity REJECTED reason={verdict.get('reason')} "
                      f"detail={str(verdict.get('detail'))[:200]}", flush=True)
                raise RuntimeError(
                    f"media integrity failed ({verdict.get('reason')})")
            stage = "durable_store_ghl"
            # Retry safety: if these exact bytes were already ingested for this
            # source+tenant, reuse the durable copy instead of uploading twice.
            _cv = manual_intake.validate_and_canonicalize(parsed["url"])
            # Capture source engagement evidence ONCE, through the existing proven
            # free extractor path (metadata-only probe; no download, no cookies, no
            # paid provider). Enrichment only: a field the platform does not expose
            # through that path stays absent (unknown), never zero, and nothing here
            # can gate, filter or reject the operator's submission.
            stage = "platform_metadata"
            source_metadata = manual_media_intake.fetch_source_metadata(
                _cv["canonical_url"], _cv.get("platform", ""))
            print(f"[manual-intake] source metadata available={source_metadata.get('available')} "
                  f"unavailable={source_metadata.get('unavailable')} "
                  f"provenance={source_metadata.get('provenance')}", flush=True)
            stage = "durable_store_ghl"
            self._mi_status(application_id, interaction_token,
                            "⏳ Storing working source…", stage="durable_store_ghl")
            reused = manual_media_intake.find_existing_media(
                SUPABASE_URL, SUPABASE_KEY, _cv["platform"], _cv["canonical_url"],
                tenant, probe["sha256"])
            if reused:
                durable = {"url": reused["url"], "file_id": reused.get("file_id", ""),
                           "folder_id": reused.get("folder_id", "")}
                print("[manual-intake] durable media reused (same bytes)", flush=True)
            else:
                durable = manual_media_intake.store_durable_media(
                    local_path, safe_name, folder_id=MANUAL_MEDIA_FOLDER_ID or None)
            durable_url = durable["url"]
            _mi_trace("durable_store_result", platform=_mi_platform,
                      source_id=_mi_source_id, result="stored",
                      reused="true" if reused else "false")
            stage = "persist_submission"
            payload = manual_media_intake.build_media_submission(
                url=parsed["url"], destination=tenant,
                content_format=parsed.get("content_format") or "hold_unassigned",
                submitter=submitter, media_reference={**durable, "filename": safe_name},
                probe=probe, human_window=(parsed.get("start_s"), parsed.get("end_s")),
                reviewer_note=(parsed.get("note") or "")[:500], active_tenants=active,
                source_metadata=source_metadata,
                acquisition_provenance={
                    **acq_prov,
                    # The WORKING source duration is the durable asset's own length.
                    "working_source_duration_s": (acq_prov.get("working_source_duration_s")
                                                  or probe.get("duration_seconds")),
                })
            row, created = manual_media_intake.persist_media_submission(
                SUPABASE_URL, SUPABASE_KEY, payload)
            persisted = True
            submission = {**(row if isinstance(row, dict) else payload),
                          "duplicate_fingerprint": payload["duplicate_fingerprint"]}
            stage = "content_intelligence"
            self._mi_status(application_id, interaction_token,
                            "⏳ Running Content Intelligence…",
                            stage="content_intelligence")
            result = manual_intake_intelligence.run_manual_intelligence(
                submission=submission, media_path=local_path, probe=probe,
                media_url=durable_url,
                human_window=(parsed.get("start_s"), parsed.get("end_s")),
                source_metadata=source_metadata)
            lines = [f"📥 **Ingested** · {payload['platform']} · tenant `{tenant or 'unassigned'}` "
                     f"· submission {'created' if created else 'already existed'}",
                     f"source {payload['source_url']}",
                     f"durable media {durable_url}"]
            _cand_state = "not_produced"
            if result.get("ok"):
                stage = "review_card"
                self._mi_status(application_id, interaction_token,
                                "✅ Ready for review → #clip-review",
                                stage="review_card")
                # Resolve the durable candidate by the AUTHORITATIVE key Content
                # Intelligence itself proved (the pipeline assigns its own
                # candidate fingerprint per materialized moment, so the
                # submission fingerprint is NOT the candidate key — looking up by
                # it is how a real candidate surfaced as "durable row not found").
                _cand_fp = (result.get("fingerprint")
                            or payload["duplicate_fingerprint"])
                cand = source_review.fetch_candidate_by_fingerprint(
                    SUPABASE_URL, SUPABASE_KEY, _cand_fp)
                if cand:
                    _cand_state = "durable_row_found"
                    plan = result.get("boundary_plan") or {}
                    # Content/candidate review, NOT source-acquisition review.
                    # The WORKING media travels with the decision card: it already
                    # exists on disk at this point (the transient copy is released
                    # only after the durable copy lands), so the card can render it
                    # without downloading, transcoding or duplicating anything.
                    review_channel = manual_review_channel_id()
                    self._post_source_card(review_channel, cand,
                                           working_media_path=local_path,
                                           durable_url=durable_url)
                    lines.append(f"✅ Content Intelligence: window "
                                 f"{plan.get('recommended_clip_start')}–"
                                 f"{plan.get('recommended_clip_end')}s "
                                 f"({result.get('boundary_provenance')}) → review card in "
                                 f"<#{review_channel}>")
                else:
                    _cand_state = "durable_row_absent"
                    lines.append("⚠️ CI produced a candidate but its durable row was not found")
            else:
                lines.append(f"⚠️ Content Intelligence did not complete at "
                             f"`{result.get('stage')}`: {result.get('error')}")
            self._patch_original(application_id, interaction_token, content="\n".join(lines))
            _mi_trace("candidate_result", platform=_mi_platform,
                      source_id=_mi_source_id,
                      result="ok" if result.get("ok") else "declined",
                      ci_stage=str(result.get("stage") or ""),
                      candidate=_cand_state)
            print(f"[manual-intake] done fingerprint={payload['duplicate_fingerprint'][:12]} "
                  f"ci_ok={bool(result.get('ok'))}", flush=True)
        except Exception as e:  # noqa: BLE001
            frames = ";".join(f"{Path(f.filename).name}:{f.lineno}:{f.name}"
                              for f in traceback.extract_tb(e.__traceback__))
            code, operator_reason = classify_intake_failure(e, stage)
            # Full detail stays in the log; Discord gets a concise, actionable
            # reason. Never a raw stderr dump, never secrets.
            #
            # `MediaIntakeError.detail` is documented "for logs only" and carries the
            # PRESERVED underlying reason of a classified refusal (the gate's own code
            # for WINDOW_REJECTED, the GHL transport reason for DURABLE_STORE_FAILED).
            # `str(e)` is only the operator-facing message, so surfacing detail here is
            # what makes those codes explain themselves. It never reaches Discord.
            preserved = str(getattr(e, "detail", "") or "")
            # ROUTE FAILED — the identity plus the exact stage/code, so a failure can be
            # attributed to a source without reading the surrounding conversation.
            _mi_trace("route_failed", platform=_mi_platform, source_id=_mi_source_id,
                      mode=_mi_mode, failed_stage=stage, code=code,
                      error=type(e).__name__, ref=_mi_ref)
            print(f"[manual-intake] failed stage={stage} code={code} "
                  f"error={type(e).__name__} detail={str(e)[:300]!r}"
                  + (f" reason={preserved[:300]!r}" if preserved else "")
                  + f" frames={frames}", flush=True)
            note = [f"❌ Manual intake failed at **{stage}**\n**{code}** — {operator_reason}"]
            if durable_url:
                note.append(f"durable media kept: {durable_url}")
            if persisted:
                note.append("submission row persisted — no need to re-upload")
            if temp_dir and not durable_url:
                # Never print a local filesystem path into Discord.
                note.append("the working copy was kept locally (no durable copy yet)")
            note.append(f"Retry safe: {'yes' if code in _MI_RETRY_SAFE_CODES else 'no'}")
            note.append("Nothing was silently dropped.")
            try:
                self._patch_original(application_id, interaction_token,
                                     content="\n".join(note))
            except Exception as patch_error:  # noqa: BLE001
                print(f"[manual-intake] card_update_failed error={type(patch_error).__name__}",
                      flush=True)
        finally:
            # Delete local working material only once the durable copy exists.
            if temp_dir and durable_url:
                manual_media_intake.cleanup(temp_dir)

    def _handle_autocomplete(self, interaction):
        data = interaction.get("data", {})
        focused = None
        for opt in data.get("options", []) or []:
            if isinstance(opt, dict) and opt.get("focused"):
                focused = opt
                break
        if not focused:
            self._send_json(200, {"type": 8, "data": {"choices": []}})
            return

        opt_name = focused.get("name", "")
        if opt_name == "destination":
            try:
                active = tenant_routing.list_active_tenants(SUPABASE_URL, SUPABASE_KEY)
            except Exception as e:
                print(f"[autocomplete] list_active_tenants failed: {e}", flush=True)
                active = set()
            partial = (focused.get("value") or "").strip().lower()
            choices = manual_intake.destination_choices(active)
            if partial:
                choices = [c for c in choices
                           if partial in c["name"].lower() or partial in c["value"].lower()]
            self._send_json(200, {"type": 8, "data": {"choices": choices[:25]}})
            return
        self._send_json(200, {"type": 8, "data": {"choices": []}})

    def _handle_source_review_command(self, interaction):
        data = interaction.get("data", {})
        limit = source_review.DEFAULT_LIMIT
        for opt in data.get("options", []) or []:
            if opt.get("name") == "limit":
                try:
                    limit = max(1, min(source_review.MAX_LIMIT, int(opt.get("value", 5))))
                except (TypeError, ValueError):
                    pass
        channel_id = interaction.get("channel_id") or ""
        try:
            candidates = source_review.fetch_pending_candidates(SUPABASE_URL, SUPABASE_KEY, limit)
        except Exception as e:
            print(f"[source-review] fetch failed: {e}", flush=True)
            self._send_json(200, {"type": 4, "data": {"content": "Failed to fetch candidates.", "flags": 64}})
            return
        if not candidates:
            self._send_json(200, {"type": 4, "data": {"content": "No pending candidates.", "flags": 64}})
            return
        self._send_json(200, {"type": 4, "data": {"content": f"Pulled {len(candidates)} candidate(s) for review.", "flags": 64}})
        for c in candidates:
            self._post_source_card(channel_id, c)

    def _attach_working_media(self, channel_id, posted, embed, components, *,
                              working_media_path="", durable_url=""):
        """Attach the candidate's WORKING media to a decision card, or attach nothing.

        Media parity at the SHARED review-card seam: the same primitive the
        acquisition path uses (``discord_media.attach_acquired_media``) renders the
        working source, tier-aware, with its own poster fallback.

        Nothing is downloaded from the source, transcoded, or duplicated, and GHL
        stays the only durable store — the bytes attached are the working media the
        candidate already owns. When no preview can be rendered the card is left
        EXACTLY as posted: decoration must never block a human decision.
        """
        if not working_media_path or not os.path.isfile(working_media_path):
            # No working copy available (e.g. re-analysis long after ingest). The
            # durable reference is still the source of truth on the card; it is
            # simply not re-materialised here, which would mean a download.
            return posted
        try:
            message_id = str((posted or {}).get("id") or "")
            if not message_id:
                return posted
            limit_bytes, tier = _card_upload_limit(channel_id)
            poster_dir = os.path.dirname(working_media_path)
            upload_name = ("working_"
                           + re.sub(r"[^A-Za-z0-9._-]", "_",
                                    os.path.basename(working_media_path)))[:120]
            outcome = discord_media.attach_acquired_media(
                token=DISCORD_BOT_TOKEN, channel_id=channel_id,
                message_id=message_id, asset_path=working_media_path,
                embed=dict(embed), components=components,
                limit_bytes=limit_bytes, tier=tier, upload_name=upload_name,
                poster_dir=poster_dir)
            print(f"[source-review] media parity mode={outcome.get('mode')} "
                  f"playable={outcome.get('playable')} "
                  f"bytes={outcome.get('asset_bytes')} "
                  f"limit={outcome.get('limit_bytes')} durability=reused", flush=True)
            if outcome.get("reason"):
                print(f"[source-review] media not playable: {outcome['reason']}", flush=True)
        except Exception as e:  # noqa: BLE001 — the decision card must remain usable
            print(f"[source-review] media parity skipped ({type(e).__name__}: {e})",
                  flush=True)
        return posted

    def _post_source_card(self, channel_id, candidate, *, working_media_path="",
                          durable_url=""):
        lineage = ((candidate.get("moment_evidence") or {}).get("lineage") or {})
        manual_media = lineage.get("origin") == "manual_media_intake"
        embed = source_review.format_source_embed(candidate)
        components = source_review.format_source_buttons(
            candidate["duplicate_fingerprint"],
            source_url=candidate.get("canonical_url") or "",
            manual_media=manual_media)
        payload = {"embeds": [embed], "components": components}
        existing_card = str(lineage.get("card_message_id") or "")
        posted = None
        try:
            # ONE manual submission -> ONE active card: a re-analysis of the same
            # submission EDITS its existing card instead of minting a second one.
            if existing_card:
                code, updated = http_json(
                    "PATCH", f"{DISCORD_API}/channels/{channel_id}/messages/{existing_card}",
                    DISCORD_BOT_TOKEN, body=payload, timeout=15)
                if code in (200, 201) and updated.get("id"):
                    posted = updated
                else:
                    print(f"[source-review] card update fell back to post (code={code})",
                          flush=True)
            if posted is None:
                code, created = http_json(
                    "POST", f"{DISCORD_API}/channels/{channel_id}/messages",
                    DISCORD_BOT_TOKEN, body=payload, timeout=15)
                if code not in (200, 201) or not created.get("id"):
                    raise RuntimeError("source card post did not return a Discord message ID")
                posted = created
                if manual_media:
                    # Bookkeeping only: never let it fail a card that already posted.
                    source_review.remember_card_message(
                        SUPABASE_URL, SUPABASE_KEY,
                        candidate.get("duplicate_fingerprint", ""), str(posted["id"]))
        except Exception as e:
            print(f"[source-review] card post failed: {type(e).__name__}", flush=True)
            raise
        # Media parity, AFTER the decision surface exists and is usable.
        return self._attach_working_media(
            channel_id, posted, embed, components,
            working_media_path=working_media_path, durable_url=durable_url)

    def _handle_submit_source_command(self, interaction):
        data = interaction.get("data", {})
        opts: dict[str, Any] = {}
        for o in data.get("options", []) or []:
            if isinstance(o, dict) and o.get("name"):
                opts[o["name"]] = o.get("value")

        url = opts.get("url", "")
        submitter = self._reviewer_name(interaction)

        try:
            active = tenant_routing.list_active_tenants(SUPABASE_URL, SUPABASE_KEY)
        except Exception as e:
            print(f"[submit-source] list_active_tenants failed: {e}", flush=True)
            active = set()

        try:
            session = intake_card.new_session(url, submitter, active)
        except manual_intake.SubmissionError as e:
            self._send_json(200, {"type": 4, "data": {"content": f"❌ {e.message}", "flags": 64}})
            return

        card = intake_card.render_card(session)
        self._send_json(200, {"type": 4, "data": {**card, "flags": 64}})

    def _handle_intake_component(self, interaction, intake, component_type, data):
        action, sid = intake
        if component_type == 3:  # STRING_SELECT
            self._intake_select(interaction, action, sid, data.get("values") or [])
            return
        if action == "submit":
            self._intake_submit(interaction, sid)
        elif action == "cancel":
            self._intake_cancel(interaction, sid)
        elif action == "details":
            self._intake_details_modal(interaction, sid)
        else:
            self._send_json(200, {"type": 4, "data": {"content": "Unknown intake action.", "flags": 64}})

    def _intake_select(self, interaction, action, sid, values):
        session = intake_card.get_session(sid)
        if session is None:
            self._send_json(200, {"type": 4, "data": {"content": "This intake has expired or was already submitted.", "flags": 64}})
            return
        try:
            intake_card.apply_select(session, action, values)
        except ValueError as e:
            self._send_json(200, {"type": 4, "data": {"content": f"❌ {e}", "flags": 64}})
            return
        self._send_json(200, {"type": 7, "data": intake_card.render_card(session)})

    def _intake_submit(self, interaction, sid):
        session = intake_card.get_session(sid)
        if session is None:
            self._send_json(200, {"type": 4, "data": {"content": "This intake has expired or was already submitted.", "flags": 64}})
            return
        if not intake_card.is_submittable(session):
            self._send_json(200, {"type": 7, "data": intake_card.render_card(session)})
            return
        # Atomic consume: prevents double-submit / replay from creating duplicates.
        session = intake_card.consume_session(sid)
        if session is None:
            self._send_json(200, {"type": 4, "data": {"content": "This intake was already submitted.", "flags": 64}})
            return
        try:
            payload = manual_intake.build_submission(
                url=session["canonical_url"], destination=session["destination"],
                content_format=session["format"], submitter=session["submitter"],
                time_start_s=session.get("start_s"), time_end_s=session.get("end_s"),
                reviewer_note=session.get("note", ""),
                active_tenants=session["active_tenants"],
            )
            row, created = manual_intake.persist_submission(SUPABASE_URL, SUPABASE_KEY, payload)
        except manual_intake.SubmissionError as e:
            self._send_json(200, {"type": 4, "data": {"content": f"❌ {e.message}", "flags": 64}})
            return
        except Exception as e:
            print(f"[intake] persist failed: {e}", flush=True)
            self._send_json(200, {"type": 4, "data": {"content": "Failed to save submission.", "flags": 64}})
            return
        self._send_json(200, {"type": 7, "data": intake_card.render_confirmation(payload, row, created)})

    def _intake_cancel(self, interaction, sid):
        session = intake_card.consume_session(sid)
        if session is None:
            self._send_json(200, {"type": 4, "data": {"content": "This intake is no longer active.", "flags": 64}})
            return
        self._send_json(200, {"type": 7, "data": intake_card.render_cancelled(session)})

    def _intake_details_modal(self, interaction, sid):
        session = intake_card.get_session(sid)
        if session is None:
            self._send_json(200, {"type": 4, "data": {"content": "This intake has expired or was already submitted.", "flags": 64}})
            return
        self._send_json(200, {"type": 9, "data": intake_card.details_modal(sid, session)})

    def _intake_details_submit(self, interaction, sid, data):
        session = intake_card.get_session(sid)
        if session is None:
            self._send_json(200, {"type": 4, "data": {"content": "This intake has expired or was already submitted.", "flags": 64}})
            return
        values = {}
        for row in data.get("components", []) or []:
            for comp in row.get("components", []) or []:
                values[comp.get("custom_id")] = comp.get("value", "")
        try:
            intake_card.apply_details(session, values.get("mi_start", ""),
                                      values.get("mi_end", ""), values.get("mi_note", ""))
        except ValueError as e:
            self._send_json(200, {"type": 4, "data": {"content": f"❌ {e}", "flags": 64}})
            return
        self._send_json(200, {"type": 7, "data": intake_card.render_card(session)})

    def _handle_assign_source_command(self, interaction):
        data = interaction.get("data", {})
        opts: dict[str, Any] = {}
        for o in data.get("options", []) or []:
            if isinstance(o, dict) and o.get("name"):
                opts[o["name"]] = o.get("value")
        source = opts.get("source", "").strip()
        destination = opts.get("destination", "").strip()
        operation = opts.get("operation") or None
        actor = self._reviewer_name(interaction)

        try:
            sub = tenant_routing.find_submission_by_fingerprint(SUPABASE_URL, SUPABASE_KEY, source)
            if not sub:
                self._send_json(200, {"type": 4, "data": {"content": "❌ Source not found.", "flags": 64}})
                return
            old_dest = sub.get("tenant_id", "")
            op = operation or tenant_routing.decide_operation(old_dest)
            result = tenant_routing.route_submission(
                SUPABASE_URL, SUPABASE_KEY, source, old_dest, destination, op, actor)
        except Exception as e:
            self._send_json(200, {"type": 4, "data": {"content": f"❌ {e}", "flags": 64}})
            return

        action = result.get("action")
        dest = result.get("destination") or "Unassigned"
        if action == "collision":
            msg = f"⚠️ Collision: already assigned to `{dest}` (id={result.get('collision_id')})"
        elif action == "blocked":
            msg = f"⛔ Reassign blocked: {result.get('reason')}"
        elif action == "noop":
            msg = f"ℹ️ No change: {result.get('reason')}"
        else:
            msg = f"✅ {action} → `{dest}`"
        self._send_json(200, {"type": 4, "data": {"content": msg, "flags": 64}})

    def _handle_source_button(self, interaction, src):
        fp = src["fingerprint"]
        decision = src["decision"]
        reviewer = self._reviewer_name(interaction)
        try:
            candidate = source_review.fetch_candidate_by_fingerprint(SUPABASE_URL, SUPABASE_KEY, fp)
        except Exception as e:
            print(f"[source-review] candidate lookup failed: {e}", flush=True)
            candidate = None
        if not candidate:
            self._send_json(200, {"type": 4, "data": {"content": "Unknown candidate.", "flags": 64}})
            return
        payload = source_review.source_decision_payload(candidate, decision, reviewer)
        try:
            row, created = source_review.persist_decision(SUPABASE_URL, SUPABASE_KEY, payload)
        except Exception as e:
            print(f"[source-review] persist failed: {e}", flush=True)
            self._send_json(200, {"type": 4, "data": {"content": "Failed to record decision.", "flags": 64}})
            return
        # Duplicate clicks render the STORED winning decision, never the
        # attempted one (first terminal decision wins at the database level).
        stored = (row or {}).get("decision") if row is not None else None
        shown = stored if not created and stored else decision
        embed = source_review.decided_embed(shown, reviewer, created)
        self._send_json(200, {"type": 7, "data": {"embeds": [embed], "components": []}})

    def _source_button_worker(self, interaction, src):
        """Post-ACK work for a Source Review decision. Never raises.

        Receives DETACHED identity only: no handler, no socket, no response
        callback. Every later Discord call is an independent outbound one.

        APPROVE hands off to the approved acquisition lane: it plans the single
        PENDING job (gate 1, zero spend authorized). The acquire-for-reaction
        (gate 2, spend) step still requires Bryan's separate Acquire click.
        """
        application_id = interaction_token = ""
        fp = "unknown"
        stage = "identity"
        saved = False
        try:
            application_id = str(interaction.get("application_id") or "")
            interaction_token = str(interaction.get("token") or "")
            fp = src["fingerprint"]
            decision = src["decision"]
            reviewer = self._reviewer_name(interaction)
            stage = "candidate_lookup"
            candidate = source_review.fetch_candidate_by_fingerprint(
                SUPABASE_URL, SUPABASE_KEY, fp)
            if not candidate:
                self._patch_original(application_id, interaction_token,
                                     content="Unknown candidate.")
                return
            if decision == source_review.DECISION_EDIT:
                stage = "editor_launch"
                self._launch_source_editor(
                    interaction, candidate, application_id, interaction_token)
                return
            payload = source_review.source_decision_payload(candidate, decision, reviewer)
            stage = "persist_decision"
            row, created = source_review.persist_decision(
                SUPABASE_URL, SUPABASE_KEY, payload)
            if not isinstance(row, dict) or not row.get("decision"):
                raise RuntimeError("decision store returned no durable decision")
            saved = True
            # Duplicate clicks render the STORED winning decision, never the
            # attempted one (first terminal decision wins at the database level).
            stored = (row or {}).get("decision") if row is not None else None
            shown = stored if not created and stored else decision
            winner = row.get("reviewer") or reviewer
            decision_embed = source_review.decided_embed(shown, winner, created)
            # RICH render is BEST-EFFORT ONLY. The decision is already durable at
            # this point, so a rendering fault must never make a successful click
            # look like "nothing happened" (a stale renderer crashed here after
            # persisting, leaving the card unchanged). Fall back to a minimal card
            # that still shows the persisted decision.
            try:
                embed = source_review.format_source_embed(candidate)
                embed["fields"] = [*(embed.get("fields") or []),
                                   *(decision_embed.get("fields") or [])]
                embed["color"] = decision_embed.get("color", embed.get("color"))
            except Exception as render_error:  # noqa: BLE001
                print(f"[source-review] rich render failed fingerprint={fp} "
                      f"error={type(render_error).__name__} — minimal decision card",
                      flush=True)
                embed = source_review.minimal_decision_embed(decision_embed, candidate)
            content = None
            # A stored approval whose dispatch was interrupted still owes its
            # idempotent pending job. Never dispatch a losing attempted decision.
            stage = "pending_job"
            if shown == source_review.DECISION_APPROVE:
                content = self._source_approve_handoff(candidate, winner)
            stage = "card_update"
            self._patch_original(application_id, interaction_token,
                                 embed=embed, content=content)
            print(f"[source-review] completed fingerprint={fp} decision={shown} created={created}", flush=True)
        except Exception as e:  # noqa: BLE001
            # Frame locations make failures actionable without logging exception
            # values, interaction tokens, signed URLs, headers, or payloads.
            frames = ";".join(f"{Path(f.filename).name}:{f.lineno}:{f.name}"
                              for f in traceback.extract_tb(e.__traceback__))
            print(f"[source-review] failed fingerprint={fp} stage={stage} error={type(e).__name__} frames={frames}", flush=True)
            try:
                self._patch_original(application_id, interaction_token,
                                     content=("Decision saved; follow-up failed" if saved else
                                              "Failed to record decision") +
                                             f" at {stage} ({type(e).__name__}). Retry is idempotent.",
                                     preserve_controls=True)
            except Exception as patch_error:  # noqa: BLE001
                print(f"[source-review] card_update_failed fingerprint={fp} error={type(patch_error).__name__}", flush=True)

    def _source_approve_handoff(self, candidate: dict[str, Any], reviewer: str) -> str | None:
        """Plan + persist the single PENDING acquisition job for an approved
        source. Gate 1 only: never authorizes spending. Returns a one-line
        status for the card, or None when the handoff is unavailable."""
        # Supplied-media candidates already have durable media stored at intake:
        # never plan a paid acquisition job for bytes we already hold.
        if (((candidate.get("moment_evidence") or {}).get("lineage") or {})
                .get("media_already_acquired")):
            return ("📥 Media already stored at intake — no acquisition needed "
                    "(nothing to download).")
        import approved_lane as _lane
        store = _acquisition_store()
        plan = candidate.get("boundary_plan") or {}
        start_s = plan.get("recommended_clip_start")
        end_s = plan.get("recommended_clip_end")
        job = _lane.plan_pending_job(
            fingerprint=candidate.get("duplicate_fingerprint", ""),
            platform=candidate.get("platform", ""),
            platform_content_id=candidate.get("platform_content_id", "")
            or candidate.get("duplicate_fingerprint", ""),
            canonical_url=candidate.get("canonical_url", ""),
            actor=reviewer,
            correlation_id=candidate.get("duplicate_fingerprint", ""),
            rights_usage_note=json.dumps({"tenant_id": candidate.get("tenant_id"),
                "profile_id": candidate.get("profile_id"), "boundary_plan": plan,
                "lineage": (candidate.get("moment_evidence") or {}).get("lineage", {})}, sort_keys=True),
            requested_start_s=(float(start_s) if isinstance(start_s, (int, float)) else None),
            requested_end_s=(float(end_s) if isinstance(end_s, (int, float)) else None),
        )
        row, job_created = store.create_job(job)
        if not job_created:
            return "ℹ️ Acquisition job already pending for this source."
        return "📥 Queued for acquisition — approve the range, then click Acquire."

    def _source_editor_followup(self, application_id, interaction_token, content=None, url=None):
        """Ephemeral follow-up. Does not replace the review card."""
        data = {"flags": 64}
        if url:
            data["content"] = "Edit this clip — opens the secure editor. The review card is unchanged."
            data["components"] = [{
                "type": 1,
                "components": [{"type": 2, "style": 5, "label": "Open Editor", "url": url}],
            }]
        else:
            data["content"] = content or "Editor unavailable."
        req = Request(
            f"{DISCORD_API}/webhooks/{application_id}/{interaction_token}",
            data=json.dumps(data).encode(),
            headers={"Content-Type": "application/json",
                     "User-Agent": "LOOMReviewAdapter/1.0 (DiscordBot)"},
            method="POST")
        with urlopen(req, timeout=15) as resp:
            resp.read()

    def _restore_source_card(self, application_id, interaction_token, candidate):
        """Clear the deferred spinner by rewriting the same card in place."""
        lineage = ((candidate.get("moment_evidence") or {}).get("lineage") or {})
        payload = {
            "embeds": [source_review.format_source_embed(candidate)],
            "components": source_review.format_source_buttons(
                candidate["duplicate_fingerprint"],
                source_url=candidate.get("canonical_url") or "",
                manual_media=lineage.get("origin") == "manual_media_intake"),
        }
        req = Request(
            f"{DISCORD_API}/webhooks/{application_id}/{interaction_token}/messages/@original",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json",
                     "User-Agent": "LOOMReviewAdapter/1.0 (DiscordBot)"},
            method="PATCH")
        with urlopen(req, timeout=15) as resp:
            resp.read()

    def _launch_source_editor(self, interaction, candidate, application_id, interaction_token):
        """Open the existing editor for this candidate. Not a decision.

        Reuses register_editor_token and the /edit deep-link. Does not approve,
        acquire, download, or post another review card.
        """
        try:
            identity = source_review.resolve_source_editor_identity(candidate)
            if not identity:
                self._source_editor_followup(
                    application_id, interaction_token,
                    "Could not open the editor — this candidate has no durable media or window.")
                return
            if not application_id or not interaction_token:
                print("[edit] missing application_id/token for source launch", flush=True)
                return
            editor_token = secrets.token_urlsafe(18)
            status, _body = register_editor_token(
                editor_token,
                {"tenant_id": identity["tenant_id"],
                 "clip_id": identity["clip_id"],
                 "asset_version": identity["asset_version"]},
                self._reviewer_name(interaction),
                launch_user_id=self._reviewer_user_id(interaction),
                launch_guild_id=str(interaction.get("guild_id") or ""),
                launch_channel_id=str(interaction.get("channel_id") or ""))
            if status not in (200, 201):
                self._source_editor_followup(
                    application_id, interaction_token,
                    "Could not start the editor session.")
                return
            if not EDITOR_PUBLIC_URL:
                self._source_editor_followup(
                    application_id, interaction_token,
                    "Editor public URL is not configured.")
                return
            edit_url = f"{EDITOR_PUBLIC_URL.rstrip('/')}/edit?token={editor_token}"
            print(f"[edit] source editor link issued clip={identity['clip_id']}", flush=True)
            self._source_editor_followup(application_id, interaction_token, url=edit_url)
            self._restore_source_card(application_id, interaction_token, candidate)
        except Exception as e:  # noqa: BLE001
            print(f"[edit] source launch failed: {type(e).__name__}", flush=True)
            try:
                self._source_editor_followup(
                    application_id, interaction_token,
                    "Editor failed to start. The review card was not changed.")
            except Exception:
                pass

    def _patch_original(self, application_id, interaction_token, *,
                        content=None, embed=None, preserve_controls=False,
                        components=None):
        """PATCH the deferred interaction's original message. Outbound only —
        the interaction token (NOT the bot token) authorizes this endpoint.

        ``components`` carries interactive controls (the WHOLE VIDEO / SELECT SEGMENT
        choice). Without it the default clears components, so an existing control row
        is never left stale on a message that no longer accepts it.
        """
        data = {} if preserve_controls else {"components": []}
        if components is not None:
            data["components"] = components
        if embed is not None:
            data["embeds"] = [embed]
        if content is not None:
            data["content"] = content
        req = Request(
            f"{DISCORD_API}/webhooks/{application_id}/{interaction_token}/messages/@original",
            data=json.dumps(data).encode(),
            headers={"Content-Type": "application/json",
                     "User-Agent": "LOOMReviewAdapter/1.0 (DiscordBot)"},
            method="PATCH")
        with urlopen(req, timeout=15) as resp:
            resp.read()

    def _handle_button(self, interaction):
        data = interaction.get("data", {})
        custom_id = data.get("custom_id", "")
        component_type = data.get("component_type", 2)

        # Manual Intake segment choice (distinct MI: prefix). No store read and no
        # network work before the acknowledgement / modal response.
        mi_choice = parse_manual_choice_custom_id(custom_id)
        if mi_choice is not None:
            action, ref = mi_choice
            # CHOICE CLICK RECEIVED — emitted BEFORE any early return, so a click that
            # reaches the backend is ALWAYS provable, even when its resume state is gone.
            # A missing line here is positive evidence the interaction never arrived.
            _click_pending = _mi_pending(ref) or {}
            _mi_trace("choice_click_received",
                      platform=str(_click_pending.get("platform") or ""),
                      source_id=str(_click_pending.get("source_id") or ""),
                      action=action, ref=ref,
                      resume_state="present" if _click_pending else "absent")
            if action == MI_ACT_SEGMENT:
                # The modal is returned IMMEDIATELY, inside Discord's 3-second
                # deadline. Reuses the SAME modal builder the acquisition lane uses.
                # The pending entry is IN-PROCESS memory (no store, no network), so
                # reading it here costs nothing and lets a source URL's own timestamp
                # PRE-FILL Start. End is never invented — the operator supplies it.
                message_id = str((interaction.get("message") or {}).get("id") or "")
                prefill = ""
                hint_ms = int((_mi_pending(ref) or {}).get("start_hint_ms") or 0)
                if hint_ms > 0:
                    try:
                        prefill = manual_media_intake._acq(
                            "source_window").human_clock(hint_ms)
                    except Exception:  # noqa: BLE001 — a prefill must never break it
                        prefill = ""
                self._send_json(200, acquisition_ops.manual_window_modal_payload(
                    ref, message_id=message_id, start_value=prefill))
                return
            if action == MI_ACT_WHOLE:
                self._send_json(200, {"type": 6})
                threading.Thread(target=self._mi_choice_worker,
                                 args=(interaction, MI_ACT_WHOLE, ref),
                                 daemon=True).start()
                return
            self._send_json(200, {"type": 4, "data": {
                "content": "Unknown intake action.", "flags": 64}})
            return

        # Manual Source Intake wizard (select menus + buttons) — distinct prefix.
        intake = intake_card.parse_cid(custom_id)
        if intake is not None:
            self._handle_intake_component(interaction, intake, component_type, data)
            return

        # Source Review Inbox buttons use a distinct compact encoding.
        src = source_review.parse_source_custom_id(custom_id)
        if src is not None:
            # ACK inside Discord's 3-second deadline FIRST. This branch previously
            # did a Supabase read + write BEFORE responding, which is why real
            # presses answered "The application didn't respond in time". Same
            # discipline as the proven acquisition lane: no store read, no
            # Supabase, no network work before the acknowledgement.
            self._send_json(200, {"type": 6})
            threading.Thread(target=self._source_button_worker,
                             args=(interaction, dict(src)), daemon=True).start()
            return

        # Approved acquisition lane — distinct AQ: prefix (Instagram, gate 1/2).
        acq = acquisition_route(custom_id)
        if acq is not None:
            action = acq.get("action")
            if action == "adjust_window":
                # Respond with the modal IMMEDIATELY — inside Discord's 3-second
                # deadline. No store read, no Supabase, no network work first.
                message_id = str((interaction.get("message") or {}).get("id") or "")
                self._send_json(200, acquisition_ops.window_modal_payload(
                    acq["ref"], message_id=message_id))
                return
            if action == "approve_acquire":
                # ACK + flush first, then acquire on a worker (same discipline).
                self._send_json(200, {"type": 6})
                threading.Thread(target=self._one_click_worker,
                                 args=(interaction, acq, self._card_ref(interaction)),
                                 daemon=True).start()
                return
            self._handle_acquisition_button(interaction, acq)
            return
        parsed = parse_custom_id(custom_id)
        if not parsed:
            self._send_json(200, {"type": 4, "data": {"content": "Invalid button.", "flags": 64}})
            return

        decision = parsed.get("d")
        if decision not in ("approve", "reject", "hold", "edit", "details"):
            self._send_json(200, {"type": 4, "data": {"content": "Invalid action.", "flags": 64}})
            return

        # Edit is special: respond LAUNCH_ACTIVITY (type 12) IMMEDIATELY — within
        # Discord's 3-second deadline — and persist the pending session
        # asynchronously. No DB work may block the acknowledgement.
        if decision == "edit":
            self._handle_edit(interaction, parsed)
            return

        resolved = self._resolve_identity(parsed)
        if not resolved:
            self._send_json(200, {"type": 4, "data": {"content": "Invalid action.", "flags": 64}})
            return
        identity = resolved["identity"]
        token_hash = resolved["token_hash"]

        reviewer = self._reviewer_name(interaction)

        if decision == "details":
            self._handle_details(interaction, identity)
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

    def _acquisition_context(self, parsed, store):
        """Resolve the lane context — called AFTER the ACK has been sent.

        A resolution failure must never crash the interaction and never guess:
        the lane falls back to a REVIEW context carrying an explicit error, so the
        guard refuses the click and the operator is told why.
        """
        try:
            return acquisition_ops.context_for(parsed, store=store)
        except Exception as e:  # noqa: BLE001
            print(f"[acquisition] context failed: {type(e).__name__}", flush=True)
            return acquisition_ops.LaneContext(
                ref=parsed.get("ref", ""),
                state=acquisition_ops.card.STATE_REVIEW,
                error={"code": "CONTEXT_UNAVAILABLE",
                       "message": "Source context could not be resolved."})

    def _handle_acquisition_button(self, interaction, parsed):
        """Approved acquisition lane: acknowledge FIRST, then work, then update.

        Store: durable when explicitly enabled, else process-local. The SPEND path
        returns None until an authorized transport is wired, so a click on this
        surface can never spend money on its own.
        """
        store = _acquisition_store()
        acquisition_ops.handle_adapter_interaction(
            self, interaction, parsed, http_json=http_json,
            context_provider=lambda: self._acquisition_context(parsed, store),
            actor=self._reviewer_name(interaction),
            runner=_acquisition_live_runner(), store=store,
        )

    def _handle_details(self, interaction, identity):
        """Details is informational: return full metadata as an EPHEMERAL
        response. It never changes review state, never consumes the durable
        token, and never embeds raw metadata/secrets in the custom_id."""
        clip_id = identity["clip_id"]
        tenant_id = identity["tenant_id"]
        ver = identity["asset_version"]

        scores = supabase_get("loom_scores", clip_id)
        media = supabase_query_media(identity)
        corrections = supabase_filter_get("loom_edit_corrections", {
            "tenant_id": tenant_id, "clip_id": clip_id, "target_asset_version": ver,
        })

        lines = ["**Full metadata**",
                 f"tenant `{tenant_id}` · clip `{clip_id}` · version `{ver}` · stage `{identity.get('review_stage')}`"]
        s = scores[0] if scores else {}
        if s.get("suggested_caption"):
            lines.append(f"**Caption**\n{s['suggested_caption']}")
        if s.get("suggested_hook"):
            lines.append(f"**Hook**\n{s['suggested_hook']}")
        if s.get("overall_score") is not None:
            lines.append(f"**Score** {s.get('overall_score')} · disposition `{s.get('decision') or '—'}`")

        if media:
            m = media[0]
            lines.append("**Storage / publication**")
            lines.append(f"storage `{m.get('storage_status')}` · publication `{m.get('publication_status')}` · content_type `{m.get('content_type') or '—'}`")
            if m.get("canonical_url"):
                lines.append(f"canonical `{m['canonical_url']}`")
            if m.get("previous_asset_version"):
                lines.append(f"lineage `{m['previous_asset_version']} → {ver}`")
            if m.get("source_filename"):
                lines.append(f"source `{m['source_filename']}`")

        for c in (corrections if isinstance(corrections, list) else []):
            lines.append("**Corrections**")
            lines.append(f"`{c.get('source_asset_version')} → {c.get('target_asset_version')}` · trim `{c.get('trim_start')}–{c.get('trim_end')}` · render `{c.get('render_status')}`")
            if c.get('caption_changes'):
                lines.append(f"caption `{json.dumps(c['caption_changes'])[:600]}`")
            if c.get('crop_reframe_changes'):
                lines.append(f"reframe `{json.dumps(c['crop_reframe_changes'])[:300]}`")
            if c.get('written_instruction'):
                lines.append(f"instruction `{c['written_instruction']}`")
            if c.get('llm_assist_used'):
                lines.append("llm_assist `true`")

        content = "\n".join(lines)[:4000]
        self._send_json(200, {"type": 4, "data": {"content": content or "No additional metadata.", "flags": 64}})

    def _handle_edit(self, interaction, parsed):
        """Edit opens the secure EXTERNAL HTTPS editor (the /edit deep-link) —
        NOT the Discord Activity and NOT localhost. A single-use opaque token is
        the only thing placed in the URL; job/clip identity is resolved from that
        token server-side at exchange time (never trusted from a URL).

        The 3-second deadline is met with an IMMEDIATE ephemeral message (type 4)
        — a temporary "Preparing secure editor…" state. This is a COMPLETE,
        flushed response with ZERO database or network work on the ack path. The
        single-use token is then created ASYNCHRONOUSLY and the message is
        PATCHed to the "Open Editor" link button.

        The Discord Activity implementation (type 12 + OAuth + pending context) is
        preserved in code for later, but is NOT the critical path."""
        reviewer = self._reviewer_name(interaction)
        review_token = parsed.get("t") or ""
        launch_user_id = self._reviewer_user_id(interaction)
        launch_guild_id = str(interaction.get("guild_id") or "")
        launch_channel_id = str(interaction.get("channel_id") or "")
        application_id = str(interaction.get("application_id") or "")
        interaction_token = str(interaction.get("token") or "")

        # IMMEDIATE ack — the ONLY thing on the 3-second path. A complete type-4
        # ephemeral response (not deferred), flushed before any network work.
        self._send_json(200, {
            "type": 4,  # CHANNEL_MESSAGE_WITH_SOURCE
            "data": {"content": "⏳ Preparing secure editor…", "flags": 64},
        })

        if not application_id or not interaction_token:
            print("[edit] missing application_id/token for follow-up", flush=True)
            return

        # Token creation + message update happen ASYNCHRONOUSLY — never on the ack path.
        def _prepare():
            try:
                resolved = resolve_review_token(review_token)
                if not resolved:
                    self._edit_followup(application_id, interaction_token,
                                        "⚠️ Could not resolve this candidate for editing.")
                    return
                identity = resolved["identity"]
                editor_token = secrets.token_urlsafe(18)  # ~24 chars, opaque (hash-only)
                status, _body = register_editor_token(
                    editor_token, identity, reviewer,
                    launch_user_id=launch_user_id,
                    launch_guild_id=launch_guild_id,
                    launch_channel_id=launch_channel_id)
                if status not in (200, 201):
                    self._edit_followup(application_id, interaction_token,
                                        "⚠️ Could not start the editor session.")
                    return
                if not EDITOR_PUBLIC_URL:
                    self._edit_followup(application_id, interaction_token,
                                        "⚠️ Editor public URL is not configured (LOOM_EDITOR_PUBLIC_URL).")
                    return
                edit_url = f"{EDITOR_PUBLIC_URL.rstrip('/')}/edit?token={editor_token}"
                print(f"[edit] external editor link issued for clip {identity['clip_id']}", flush=True)
                self._edit_followup(application_id, interaction_token, None, edit_url)
            except Exception as e:
                print(f"[edit] async prepare error: {e}", flush=True)
                try:
                    self._edit_followup(application_id, interaction_token,
                                        "⚠️ Editor failed to start. Please try again.")
                except Exception:
                    pass

        threading.Thread(target=_prepare, daemon=True).start()

    def _edit_followup(self, application_id, interaction_token, content=None, url=None):
        """POST an ephemeral follow-up to a deferred interaction. The interaction
        token (NOT the bot token) authorizes this endpoint."""
        data = {"flags": 64}  # ephemeral: only the reviewer sees it
        if url:
            data["content"] = "✏️ **Edit this clip** — opens the secure editor."
            data["components"] = [{
                "type": 1,
                "components": [{
                    "type": 2, "style": 5, "label": "Open Editor", "url": url,
                }],
            }]
        else:
            data["content"] = content or "⚠️ Editor unavailable."
        req = Request(
            f"{DISCORD_API}/webhooks/{application_id}/{interaction_token}/messages/@original",
            data=json.dumps(data).encode(),
            headers={"Content-Type": "application/json",
                     "User-Agent": "LOOMReviewAdapter/1.0 (DiscordBot)"},
            method="PATCH")
        with urlopen(req, timeout=15) as resp:
            resp.read()

    def _handle_public_config(self):
        """Public, non-sensitive feature-flag passthrough (see do_GET).

        Contract: GET /api/config is answered WITHOUT an editor session
        credential. It exposes only server feature flags and is safe to read
        before any session exists. It is NOT an authentication bypass for any
        other route: every other /api/* endpoint still requires a valid, active
        editor session token."""
        self._forward_to_backend("GET", "/api/config", b"")

    def _handle_editor_bootstrap(self):
        """Durable editor bootstrap (GET, session-token auth). Resolves the
        exact review asset (media URL, duration, trim/source range, framing,
        title, hook, social caption, spoken captions, transcript/scenes) from
        LOOM media/version records — NOT the transient OpenShorts /api/status
        runtime state. Enforces ownership: the media URL is resolved strictly
        from the session's own tenant/job/clip/version; a client can never
        request another session's asset. Missing required durable fields
        return a recoverable 404 — never a 0:00 / 0/0 fallback."""
        auth = self.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            self._send_json(401, {"error": "missing session token"})
            return
        session, reason = resolve_editor_session_token_diag(auth[len("Bearer "):].strip())
        if not session:
            print(f"[bootstrap] reject={reason}", flush=True)
            self._send_json(401, {"error": "invalid or expired session", "reason": reason})
            return

        tenant_id = session.get("tenant_id")
        clip_id = session.get("clip_id")
        # The version this EDIT is tied to. It is honoured exactly (or resolved
        # deterministically when the session asks for 'latest'); an unknown
        # version is reported, never silently replaced by an older one.
        source_asset_version = session.get("source_asset_version")
        durable, reason = resolve_durable_bootstrap_diag(
            tenant_id, clip_id, source_asset_version)
        if not durable:
            print(f"[bootstrap] missing durable data for {clip_id}@"
                  f"{source_asset_version} reason={reason}", flush=True)
            self._send_json(404, {
                "error": "missing_data",
                "reason": reason,
                "requested_asset_version": source_asset_version,
                "detail": "No durable media or trim range for this clip version.",
            })
            return

        resolved_version = durable.get("asset_version")
        durable["tenant_id"] = tenant_id
        durable["clip_id"] = clip_id
        durable["asset_version"] = resolved_version
        durable["requested_asset_version"] = source_asset_version
        print(f"[bootstrap] durable data ok for {clip_id}@{resolved_version} "
              f"(requested={source_asset_version}, mode={durable.get('version_mode')})",
              flush=True)
        self._send_json(200, durable)

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
        print("[exchange] launch token -> editor session OK (clip-specific launch)", flush=True)
        clip_id = session["clip_id"]
        job_id, sep, clip_index = clip_id.rpartition(":")
        if not sep:
            job_id, clip_index = clip_id, "0"
        session["job_id"] = job_id
        session["clip_index"] = clip_index
        # Mint the short-lived editor session token (authenticates /api/* calls).
        # Minting NEVER rebinds an existing credential — a second mint for the
        # same session would orphan the editor that already holds the first one.
        session_token, code = mint_session_token_diag(session["edit_session_id"])
        if not session_token:
            if code == "already_authenticated":
                self._send_json(409, {
                    "ok": False,
                    "code": "already_authenticated",
                    "error": "this edit session is already authenticated — reuse the "
                             "open editor, or click Edit again on the review card for "
                             "a fresh session",
                })
                return
            self._send_json(502, {"ok": False, "code": code or "mint_failed",
                                  "error": "could not mint session token"})
            return
        session["session_token"] = session_token
        self._send_json(200, {"ok": True, "session": session})

    def _handle_editor_oauth(self):
        """Server-side Discord OAuth2 authorization-code exchange (Activity).
        The frontend obtains an authorization `code` via the Embedded App SDK's
        commands.authorize({response_type:'code'}), then POSTs it here. The
        adapter exchanges it for an access_token using the client secret — which
        NEVER leaves the server. Returns the token to the client so it can
        complete SDK auth (commands.authenticate)."""
        try:
            raw = self._read_body()
            body = json.loads(raw.decode() or "{}")
        except Exception:
            self._send_json(400, {"ok": False, "error": "bad json"})
            return
        code = body.get("code")
        if not code:
            self._send_json(400, {"ok": False, "error": "missing code"})
            return
        if not DISCORD_CLIENT_SECRET:
            self._send_json(503, {"ok": False, "error": "OAuth not configured (missing client secret)"})
            return
        access_token = discord_oauth_exchange(code)
        if not access_token:
            print("[oauth] exchange FAILED (no token returned)", flush=True)
            self._send_json(502, {"ok": False, "error": "token exchange failed"})
            return
        print("[oauth] exchange OK — access token obtained", flush=True)
        self._send_json(200, {"ok": True, "access_token": access_token})

    def _handle_editor_pending(self):
        """Resolve the pending (active) edit session for the authenticated
        Discord user. The client sends the OAuth access_token; the adapter
        derives the numeric user id SERVER-SIDE via Discord /users/@me (never
        trusts a client-supplied id), looks up the pending session, mints the
        short-lived editor session token, and returns the exact
        tenant/clip/asset_version. Returns pending:false when no session is
        bound to this user (generic App Launcher launch)."""
        try:
            raw = self._read_body()
            body = json.loads(raw.decode() or "{}")
        except Exception:
            self._send_json(400, {"ok": False, "error": "bad json"})
            return
        access_token = body.get("access_token")
        if not access_token:
            self._send_json(400, {"ok": False, "error": "missing access_token"})
            return
        launch_user_id = discord_user_lookup(access_token)
        if not launch_user_id:
            self._send_json(401, {"ok": False, "error": "could not resolve authenticated user"})
            return
        session = resolve_pending_launch(launch_user_id)
        if not session:
            print(f"[pending] no active session for user {launch_user_id}", flush=True)
            self._send_json(200, {"ok": True, "pending": False})
            return
        session_token, code = mint_session_token_diag(session["edit_session_id"])
        if not session_token:
            if code == "already_authenticated":
                # Truthful: there IS a pending edit, but this session already
                # holds a live credential. Handing out a second token would
                # break whichever editor holds the first one, so we report the
                # state instead of rebinding.
                print(f"[pending] already authenticated -> {session['clip_id']}", flush=True)
                self._send_json(200, {"ok": True, "pending": True,
                                      "already_authenticated": True,
                                      "session": session})
                return
            self._send_json(502, {"ok": False, "code": code or "mint_failed",
                                  "error": "could not mint session token"})
            return
        clip_id = session["clip_id"]
        job_id, sep, clip_index = clip_id.rpartition(":")
        if not sep:
            job_id, clip_index = clip_id, "0"
        session["job_id"] = job_id
        session["clip_index"] = clip_index
        session["session_token"] = session_token
        print(f"[pending] resolved clip-specific session for user {launch_user_id} -> {clip_id}", flush=True)
        self._send_json(200, {"ok": True, "pending": True, "session": session})

    def _handle_editor_draft(self):
        """Persist the trim draft server-side BEFORE render starts, so an
        Activity reload cannot erase an in-progress trim. Authenticated by the
        short-lived editor session token (Bearer). Writes only the session's
        own draft blob; never touches asset version / review state."""
        auth = self.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            self._send_json(401, {"ok": False, "error": "missing session token"})
            return
        session, reason = resolve_editor_session_token_diag(auth[len("Bearer "):].strip())
        if not session:
            print(f"[draft] reject={reason}", flush=True)
            self._send_json(401, {"ok": False, "error": "invalid or expired session",
                                  "reason": reason})
            return
        try:
            raw = self._read_body()
            body = json.loads(raw.decode() or "{}")
        except Exception:
            self._send_json(400, {"ok": False, "error": "bad json"})
            return
        edit_session_id = body.get("edit_session_id")
        draft = body.get("draft")
        if not edit_session_id or not isinstance(draft, dict):
            self._send_json(400, {"ok": False, "error": "missing edit_session_id or draft"})
            return
        if edit_session_id != session["edit_session_id"]:
            self._send_json(403, {"ok": False, "error": "session mismatch"})
            return
        url = (f"{SUPABASE_URL}/rest/v1/loom_edit_sessions"
               f"?id=eq.{edit_session_id}&status=eq.active")
        data = json.dumps({"draft": draft}).encode()
        headers = _supabase_headers()
        headers["Content-Type"] = "application/json"
        req = Request(url, data=data, headers=headers, method="PATCH")
        try:
            with urlopen(req, timeout=15) as resp:
                ok = resp.status in (200, 204)
        except Exception as e:
            self._send_json(502, {"ok": False, "error": f"draft save failed: {e}"})
            return
        if not ok:
            self._send_json(502, {"ok": False, "error": "draft save failed"})
            return
        print(f"[draft] saved for session {edit_session_id}", flush=True)
        self._send_json(200, {"ok": True})

    def _handle_editor_capcut(self):
        """Hand the CURRENT version to CapCut as an editable project.

        OPTIONAL lane ("Finish in CapCut"), authenticated by the short-lived
        editor session token exactly like the other editor endpoints.

        This endpoint is READ-ONLY with respect to LOOM: it reads the session's
        own durable version state and writes only to (a) its own scratch handoff
        workspace and (b) the local CapCut draft store. It never writes a version,
        approval, score, session or SourceWindow; never triggers acquisition, CI,
        a rerender or a publish; and never moves or deletes the working asset. On
        any failure it returns a reason and leaves LOOM exactly as it was.

        It is also optional in the strongest sense: with CapCut absent, or its
        CLI absent, this returns a clear "lane unavailable" and nothing else in
        LOOM changes behaviour.
        """
        auth = self.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            self._send_json(401, {"ok": False, "error": "missing session token"})
            return
        session, reason = resolve_editor_session_token_diag(auth[len("Bearer "):].strip())
        if not session:
            print(f"[capcut] reject={reason}", flush=True)
            self._send_json(401, {"ok": False, "error": "invalid or expired session",
                                  "reason": reason})
            return
        try:
            body = json.loads(self._read_body().decode() or "{}")
        except Exception:
            self._send_json(400, {"ok": False, "error": "bad json"})
            return
        if body.get("edit_session_id") != session["edit_session_id"]:
            self._send_json(403, {"ok": False, "error": "session mismatch"})
            return

        tenant_id = session.get("tenant_id")
        clip_id = session.get("clip_id")
        source_asset_version = session.get("source_asset_version")
        durable, reason = resolve_durable_bootstrap_diag(
            tenant_id, clip_id, source_asset_version)
        if not durable:
            self._send_json(404, {"ok": False, "lane": "capcut",
                                  "error": "missing_data", "reason": reason})
            return
        durable = dict(durable)
        resolved_version = durable.get("asset_version")
        job_id = session.get("job_id") or str(clip_id or "").rpartition(":")[0]
        # Local-first (unchanged), then the durable record's own object is
        # materialised+verified for transport when this host has no copy. The
        # runner is handed a path it can actually open; it still owns the
        # fail-closed guard, and it is never given a URL to fetch.
        media_path, media_source = materialize_local_working_media(job_id, durable)
        if not media_path:
            print(f"[capcut] working media unavailable for {clip_id}@"
                  f"{resolved_version}: {media_source}", flush=True)
            self._send_json(409, {"ok": False, "lane": "capcut",
                                  "error": "working_media_unavailable",
                                  "reason": media_source,
                                  "asset_version": resolved_version})
            return
        try:
            report = capcut_runner.run_handoff(
                bootstrap=durable,
                media_path=media_path,
                # ORIGINAL provenance only: the runner reports the origin and
                # never applies it (the range it receives is working-relative).
                # `working_source_duration_s` is deliberately NOT passed — it
                # only feeds bounds validation, and the window stays owned by
                # the durable candidate plan.
                acquisition=handoff_acquisition_provenance(durable),
                caption_blocks=durable.get("captions") or [],
                asset_version=resolved_version or "",
                # Renderable copy travels by its own explicit contract. The
                # bootstrap's `title` is source metadata and is never passed as
                # copy here.
                renderable=durable.get("renderable_text") or {},
                reveal=bool(body.get("reveal")),
            )
        except capcut_runner.ch.CapCutHandoffUnavailable as exc:
            print(f"[capcut] unavailable for {clip_id}@{resolved_version}: {exc}",
                  flush=True)
            self._send_json(409, {"ok": False, "lane": "capcut",
                                  "error": "handoff_unavailable", "reason": str(exc),
                                  "asset_version": resolved_version})
            return
        except Exception as exc:  # noqa: BLE001
            print(f"[capcut] unexpected failure for {clip_id}: {type(exc).__name__}",
                  flush=True)
            self._send_json(500, {"ok": False, "lane": "capcut",
                                  "error": "handoff_failed",
                                  "reason": f"{type(exc).__name__}"})
            return

        print(f"[capcut] draft '{report.get('draft_name')}' for {clip_id}@"
              f"{resolved_version}", flush=True)

        # Durable provenance for the BEFORE state — written ONLY now that a real
        # project exists on disk. Never fatal: a failure here must not take the
        # operator's project away, and it must never be reported as recorded.
        provenance = {"recorded": False}
        try:
            correction = {}
            try:
                rows = supabase_filter_get("loom_edit_corrections", {
                    "tenant_id": tenant_id, "clip_id": clip_id,
                    "target_asset_version": resolved_version})
                correction = capcut_runner.select_current_correction(
                    rows, resolved_version)
            except Exception:  # noqa: BLE001
                correction = {}
            recorded = record_capcut_handoff(
                report, tenant_id=tenant_id, clip_id=clip_id,
                candidate_id=durable.get("duplicate_fingerprint") or clip_id,
                correction=correction)
            if recorded:
                provenance = {"recorded": True,
                              "handoff_id": recorded.get("handoff_id")}
            else:
                provenance = {"recorded": False,
                              "reason": "provenance_write_failed"}
        except Exception as exc:  # noqa: BLE001
            provenance = {"recorded": False, "reason": type(exc).__name__}

        # The spec is the durable BEFORE artifact, not browser payload: it is
        # recorded above and stripped here so the editor never receives it.
        body = dict(report)
        body.pop("spec", None)
        body["provenance"] = provenance
        # Where the handed media actually came from (local copy vs materialised
        # durable transport copy). Reported for the operator; it never changes
        # which version was exported.
        body["media_source"] = media_source
        self._send_json(200, body)

    def _handle_editor_api(self, method):
        """Authenticated, allowlisted editor API gateway. Only the endpoints the
        editor needs are forwarded — and only for the session's own job/clip.
        Arbitrary job IDs, unrelated /api/* routes, and unauthenticated requests
        are rejected. The OpenShorts backend is never directly reachable."""
        auth = self.headers.get("Authorization", "")
        req_fp = "none"
        if not auth.startswith("Bearer "):
            print(f"[api-auth] {self.path} reject=missing_token", flush=True)
            self._send_json(401, {"error": "missing session token",
                                  "reason": "missing_token"})
            return
        raw_token = auth[len("Bearer "):].strip()
        req_fp = token_fp(raw_token)
        session, reason = resolve_editor_session_token_diag(raw_token)
        if not session:
            print(f"[api-auth] {self.path} reject={reason} fp={req_fp}", flush=True)
            # The reason is part of the response so the editor can tell an
            # expired credential (re-acquire) from a completed session (already
            # finished) from a lookup failure — never guessed from a bare 401.
            self._send_json(401, {"error": "invalid or expired session",
                                  "reason": reason})
            return
        print(f"[api-auth] {self.path} ok fp={req_fp} session={session['edit_session_id']}", flush=True)

        # Request-size limit (before reading the body).
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length > MAX_EDITOR_BODY_BYTES:
            self._send_json(413, {"error": "body too large"})
            return
        raw_body = self._read_body()

        # Rehydrate the live OpenShorts job from the durable clean source (the
        # original job was evicted). Idempotent per session; the temp job id is
        # server-derived, never client-supplied.
        temp_job_id, _temp_clip = rehydrate_edit_job(
            session["edit_session_id"], session["tenant_id"],
            session["clip_id"], session["source_asset_version"])
        clip_res = CLIP_SUBRESOURCE_RE.match(self.path) if method == "GET" else None
        if not temp_job_id:
            if clip_res:
                # No live workspace for this clip (evicted job, or a rehydration
                # that never materialised one). The three durable clip reads are
                # still answerable from LOOM's own records — serve those and say
                # so, instead of a bare failure that reads like an auth error.
                print(f"[api] no live workspace fp={req_fp}; serving durable "
                      f"{clip_res.group(3)}", flush=True)
                self._forward_clip_subresource(None, clip_res.group(3), session)
                return
            print(f"[api] rehydrate failed fp={req_fp}", flush=True)
            self._send_json(502, {"error": "could not rehydrate edit job",
                                  "code": "live_job_unavailable"})
            return

        target = self._authorize_editor_path(method, self.path, raw_body,
                                             session["job_id"], session["clip_index"],
                                             temp_job_id)
        if target is None:
            self._send_json(403, {"error": "forbidden"})
            return

        # Clip sub-resources get the truthful live-else-durable answer.
        if clip_res:
            self._forward_clip_subresource(target, clip_res.group(3), session)
            return

        # Rewrite the POST body's job identity to the rehydrated temp job
        # (clip index collapses to 0 — the temp workspace holds a single clip).
        new_body = raw_body
        if method == "POST" and raw_body:
            try:
                b = json.loads(raw_body.decode() or "{}")
                if b.get("job_id") == session["job_id"]:
                    b["job_id"] = temp_job_id
                b["clip_index"] = 0
                new_body = json.dumps(b).encode()
            except Exception:
                pass

        self._forward_to_backend(method, target, new_body)

    def _authorize_editor_path(self, method, path, raw_body, job_id, clip_index, temp_job_id):
        """Allowlist the exact editor endpoints + enforce job/clip ownership, then
        translate the durable job identity to the rehydrated temp job (clip index
        collapses to 0). Returns the backend path to forward, or None (reject)."""
        m = re.match(r'^/api/status/([^/]+)$', path)
        if m and method == "GET":
            return f"/api/status/{temp_job_id}" if m.group(1) == job_id else None
        m = re.match(r'^/api/source/([^/]+)$', path)
        if m and method == "GET":
            return f"/api/source/{temp_job_id}" if m.group(1) == job_id else None
        m = re.match(r'^/api/clip/([^/]+)/([^/]+)/(transcript|edl|scenes)$', path)
        if m and method == "GET":
            if m.group(1) == job_id and m.group(2) == str(clip_index):
                return f"/api/clip/{temp_job_id}/0/{m.group(3)}"
            return None
        m = re.match(r'^/api/render/([^/]+)$', path)
        if m and method == "GET":
            return path  # render status (read-only; render_id is a UUID)
        # POST body endpoints: job_id + clip_index must match the session.
        if method == "POST" and path in ("/api/subtitle", "/api/clip/rerender", "/api/clip/reframe", "/api/hook", "/api/edit", "/api/header"):
            try:
                body = json.loads(raw_body.decode() or "{}")
            except Exception:
                return None
            if body.get("job_id") == job_id and str(body.get("clip_index")) == str(clip_index):
                return path
            return None
        return None

    def _backend_request(self, method, target, raw_body):
        """One request to the loopback OpenShorts backend. Returns (status, body)
        or (None, None) when the backend could not be reached."""
        url = f"http://127.0.0.1:8000{target}"
        headers = {"Content-Type": self.headers.get("Content-Type", "application/json")}
        req = Request(url, data=(raw_body or None), headers=headers, method=method)
        try:
            with urlopen(req, timeout=300) as resp:
                return resp.status, resp.read()
        except HTTPError as e:
            return e.code, e.read()
        except Exception:
            return None, None

    def _send_bytes(self, status, body):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self._emit_cors()
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _forward_to_backend(self, method, target, raw_body):
        """Forward the authorized request to the loopback OpenShorts backend.
        (The backend is bound to 127.0.0.1:8000 only — never directly public.)"""
        status, body = self._backend_request(method, target, raw_body)
        if status is None:
            self._send_json(502, {"error": "backend unreachable"})
            return
        self._send_bytes(status, body)

    def _forward_clip_subresource(self, target, kind, session):
        """Serve a clip sub-resource (transcript|edl|scenes) for the editor.

        Prefers the live backend. When the live job cannot answer (404 — the
        render job was evicted, or a per-session rehydration never materialised a
        workspace) the DURABLE record answers instead and the response says so.
        The three outcomes are deliberately distinguishable:
          * live 200            -> live data
          * durable 200         -> {"source": "durable", ...}
          * 404 durable_data_missing -> legitimately absent (never auth)
        An auth failure still comes back as 401 from the gateway, before this point.
        """
        status, body = (self._backend_request("GET", target, b"") if target else (None, None))
        if status is not None and status != 404:
            self._send_bytes(status, body)
            return
        durable, reason = resolve_durable_bootstrap_diag(
            session.get("tenant_id"), session.get("clip_id"),
            session.get("source_asset_version"))
        if not durable:
            print(f"[api] {kind} unavailable: live={status} durable={reason} "
                  f"clip={session.get('clip_id')}", flush=True)
            self._send_json(404, {
                "error": "durable_data_missing",
                "kind": kind,
                "source": "durable",
                "reason": reason,
                "live_status": status,
            })
            return
        payload = durable_clip_payload(kind, durable)
        print(f"[api] {kind} served from durable record "
              f"({session.get('clip_id')}@{durable.get('asset_version')}, "
              f"live_status={status})", flush=True)
        self._send_json(200, payload)

    def _handle_editor_complete(self):
        """Finalize an edit: atomically register the new asset_version (RPC) and
        send a fresh final_publication review card for that exact version. The
        edited render is NOT promoted/approved/distributed — it is
        final_review_pending until the new card is approved. Replay-safe:
        completed sessions resolve idempotently and never duplicate a card."""
        # Authenticate BEFORE reading or validating the body. A bogus or absent
        # credential must never reach body validation: it previously produced a
        # 400 "missing edit_session_id" instead of an auth verdict, which
        # exposed request-shape behaviour to unauthenticated callers.
        auth = self.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            print("[complete] reject=missing_token", flush=True)
            self._send_json(401, {"ok": False, "error": "missing session token",
                                  "reason": "missing_token"})
            return
        try:
            token_row, _token_source = lookup_editor_token(
                hash_token(auth[len("Bearer "):].strip()))
        except SupabaseLookupError:
            print("[complete] reject=lookup_failed", flush=True)
            self._send_json(502, {"ok": False, "error": "credential lookup failed",
                                  "reason": "lookup_failed"})
            return
        if not token_row:
            # Unknown credential. A TERMINAL session still authenticates here:
            # its credential stays valid so an idempotent replay can resolve;
            # only a token matching no session is rejected.
            print("[complete] reject=hash_mismatch", flush=True)
            self._send_json(401, {"ok": False, "error": "invalid session token",
                                  "reason": "hash_mismatch"})
            return

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
        if token_row.get("id") != edit_session_id:
            print("[complete] reject=session_mismatch", flush=True)
            self._send_json(403, {"ok": False, "error": "session mismatch",
                                  "reason": "session_mismatch"})
            return

        # Resolve the session (active OR completed for idempotent replay).
        session = resolve_edit_session(edit_session_id)
        if not session:
            self._send_json(403, {"ok": False, "error": "invalid or inactive editor session"})
            return

        # Idempotency is keyed to the SUBMISSION's identity, not to the job/clip.
        # A replay of the same submission must add nothing, and a different
        # submission must never be mistaken for a replay.
        incoming_fp = submission_fingerprint(body)
        if session.get("status") == "completed" and session.get("target_asset_version"):
            recorded_fp = stored_submission_fingerprint(edit_session_id)
            completed_version = session.get("target_asset_version")
            if recorded_fp and recorded_fp == incoming_fp:
                existing = check_existing_card(session.get("tenant_id"),
                                               session.get("clip_id"),
                                               completed_version)
                delivered = ([c for c in existing if c.get("discord_message_id")]
                             if isinstance(existing, list) else [])
                print(f"[complete] idempotent replay of {edit_session_id} "
                      f"-> {completed_version}", flush=True)
                self._send_json(200, {
                    "ok": True,
                    "idempotent": True,
                    "submission": {
                        "edit_session_id": edit_session_id,
                        "fingerprint": incoming_fp,
                        "asset_version": completed_version,
                        "already_completed": True,
                    },
                    "card": ({"already_delivered": True,
                              "message_id": delivered[0].get("discord_message_id")}
                             if delivered else {"already_delivered": False}),
                })
                return
            # A DIFFERENT submission on an already-finalized session is a
            # conflict — never a second version for one session, and never a
            # silent no-op. A genuinely new submission arrives on a new session.
            print(f"[complete] submission_conflict for {edit_session_id} "
                  f"(completed as {completed_version})", flush=True)
            self._send_json(409, {
                "ok": False,
                "code": "submission_conflict",
                "error": ("this edit session already completed a different "
                          f"submission as {completed_version} — open a new edit "
                          "session to submit new changes"),
                "submission": {
                    "edit_session_id": edit_session_id,
                    "fingerprint": incoming_fp,
                    "asset_version": completed_version,
                },
            })
            return

        edl_metadata = body.get("edl_metadata")
        if not isinstance(edl_metadata, dict):
            edl_metadata = {} if edl_metadata is None else {"value": edl_metadata}
        edl_metadata = dict(edl_metadata)
        edl_metadata["submission_fingerprint"] = incoming_fp

        # The clean pre-hook/pre-subtitle sibling for THIS render, so the new
        # version carries the identity the (unchanged, still-strict) Edit
        # readiness gate requires. It already exists at finalize time: the same
        # local-output convention register_clip_media uses. Honest by
        # construction — {} (NULL fields) when it genuinely cannot be obtained,
        # so the version stays truthfully replacement-unavailable rather than
        # receiving a guessed, fabricated or borrowed reference.
        clean_fields = {}
        try:
            cs_job_id, _cs_index = rehydrate_edit_job(
                edit_session_id, session.get("tenant_id"),
                session.get("clip_id"), session.get("source_asset_version"))
            if cs_job_id:
                clean_fields = derive_clean_source_for_edit(
                    cs_job_id, normalize_ready_file(body.get("ready_file"), cs_job_id))
        except Exception as e:  # noqa: BLE001 — never block a registration on this
            print(f"[complete] clean-source derivation failed (non-fatal): "
                  f"{type(e).__name__}: {e}", flush=True)

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
            "p_edl_metadata": edl_metadata,
            # NULL when genuinely unobtainable — the version is then honestly
            # replacement-unavailable, and the Edit gate withholds Edit.
            **clean_fields,
        }
        try:
            status, result = register_edited_asset_rpc(rpc_payload)
        except Exception as e:
            self._send_json(502, {"ok": False, "error": f"registration RPC failed: {e}"})
            return
        if status not in (200, 201) or not isinstance(result, dict):
            self._send_json(502, {"ok": False, "error": f"registration failed (status {status})"})
            return

        target_version = result.get("asset_version")
        tenant_id = result.get("tenant_id") or session.get("tenant_id")
        clip_id = result.get("clip_id") or session.get("clip_id")

        # Truthful storage fact for the new version. The registration RPC stamps
        # 'uploaded' via the column default; this path stores nothing, so record
        # that plainly before the card renders the field.
        mark_edited_storage_status(tenant_id, clip_id, target_version)

        # Card dedup: if a final_publication card already exists for this exact
        # tenant/clip/asset_version, return the existing delivery — no duplicate.
        # Scoped to THIS version only, so a distinct submission that allocated a
        # new version is never suppressed by this guard.
        existing = check_existing_card(tenant_id, clip_id, target_version)
        delivered = [c for c in existing if c.get("discord_message_id")] if isinstance(existing, list) else []
        if delivered:
            self._send_json(200, {"ok": True, "idempotent": True,
                                 "submission": {"edit_session_id": edit_session_id,
                                                "fingerprint": incoming_fp,
                                                "asset_version": target_version},
                                 "card": {"already_delivered": True,
                                          "message_id": delivered[0].get("discord_message_id")},
                                 "result": result})
            return

        # Normalize + verify the rendered file exists before sending the card.
        # The rendered file lives in the rehydrated temp job (the original job
        # was evicted), so resolve the temp job identity here too.
        temp_job_id, _tc = rehydrate_edit_job(
            edit_session_id, session.get("tenant_id"),
            session.get("clip_id"), session.get("source_asset_version"))
        if not temp_job_id:
            self._send_json(502, {"ok": False, "error": "could not rehydrate edit job"})
            return
        ready_file = normalize_ready_file(body.get("ready_file"), temp_job_id)
        if not ready_file:
            self._send_json(502, {"ok": False, "error": "rendered file not found or invalid"})
            return

        source_version = result.get("source_asset_version") or session.get("source_asset_version")
        job_id = temp_job_id  # the edited master lives in the rehydrated workspace

        # Structured corrections for the compact Changes line.
        corrections = {
            "trim_start": body.get("trim_start"),
            "trim_end": body.get("trim_end"),
            "caption_changes": body.get("caption_changes"),
            "crop_reframe_changes": body.get("crop_reframe_changes"),
        }
        # Fixture badge when the source asset is a non-production fixture.
        src_rows = supabase_filter_get("loom_media", {
            "tenant_id": tenant_id, "clip_id": clip_id, "asset_version": source_version,
        })
        is_fixture = bool(src_rows and src_rows[0].get("storage_status") == "fixture")

        # Card delivery is the LAST step and the only one that leaves the host.
        # A transient upload failure here must not be reported as success, and
        # must not be retried blindly: send_review_card() is not idempotent
        # (it registers a fresh token per call), so a retry is guarded by a
        # re-check for an already-delivered card for this exact version. That
        # keeps "retry" from ever becoming "duplicate card".
        attempts = max(1, int(_env("LOOM_CARD_SEND_ATTEMPTS", "3")))
        card = None
        card_error = None
        for attempt in range(attempts):
            try:
                card = send_review_card({
                    "clip_id": clip_id,
                    "job_id": job_id,
                    "tenant_id": tenant_id,
                    "asset_version": target_version,
                    "review_stage": "final_publication",
                    "ready_file": ready_file,
                    "proposed_shorts_title": body.get("proposed_title") or "",
                    "hook": body.get("hook") or "",
                    "caption": body.get("caption") or "",
                    "overall_score": body.get("overall_score"),
                    "duration_seconds": body.get("duration_seconds"),
                    "lineage": f"{_short_version(source_version)} → {_short_version(target_version)}",
                    "corrections": corrections,
                    "is_fixture": is_fixture,
                })
                card_error = None
                break
            except Exception as e:  # noqa: BLE001
                card_error = f"{type(e).__name__}: {e}"
                # Guard against a duplicate: a previous attempt (or a lost
                # response) may already have delivered the card for this exact
                # clip@version.
                try:
                    already = check_existing_card(tenant_id, clip_id, target_version)
                    delivered = [c for c in (already or [])
                                 if isinstance(c, dict) and c.get("discord_message_id")]
                except Exception:  # noqa: BLE001 - the guard must never mask the real error
                    delivered = []
                if delivered:
                    card = {"already_delivered": True,
                            "message_id": delivered[0].get("discord_message_id")}
                    card_error = None
                    break
                if attempt < attempts - 1:
                    backoff = min(8.0, 1.5 ** attempt)
                    print(f"[card] attempt {attempt + 1}/{attempts} failed for "
                          f"{clip_id}@{target_version}: {card_error} - retrying in {backoff:.1f}s",
                          flush=True)
                    time.sleep(backoff)

        if card is None:
            # The asset_version IS registered (see `result`) but the re-review
            # card did NOT reach Discord. Report that truthfully: non-2xx and
            # ok:false, so no caller can mistake a failed delivery for a
            # delivered card (the old contract answered 200 + ok:true with the
            # reason buried in card_error, and the UI claimed "card sent").
            print(f"[card] GAVE UP for {clip_id}@{target_version}: {card_error}", flush=True)
            self._send_json(502, {
                "ok": False,
                "error": "card_delivery_failed",
                "detail": card_error,
                "asset_registered": True,
                "result": result,
            })
            return

        self._send_json(200, {"ok": True, "result": result, "card": card})

    def _card_ref(self, interaction):
        """Detached, serializable identity for post-ACK outbound calls.

        Captured while the request is still in scope, so background work never
        needs the handler, its wfile, or the connection closing behind the ACK.
        """
        return {
            "channel_id": str((interaction.get("channel") or {}).get("id") or ""),
            "message_id": str((interaction.get("message") or {}).get("id") or ""),
            "application_id": str(interaction.get("application_id") or ""),
            "interaction_token": str(interaction.get("token") or ""),
            "bot_token": DISCORD_BOT_TOKEN,
            "actor": self._reviewer_name(interaction),
        }

    def _window_modal_worker(self, interaction, window_ref, card_ref):
        """Post-ACK work for an Adjust Window submission. Never raises."""
        try:
            acquisition_ops.handle_window_modal_adapter(
                interaction, window_ref, http_json=http_json,
                store=_acquisition_store(), actor=card_ref.get("actor") or "unknown",
                bot_token=card_ref.get("bot_token") or DISCORD_BOT_TOKEN)
        except Exception as e:  # noqa: BLE001 — a worker must never kill the adapter
            acquisition_ops.notify_private(
                http_json, interaction,
                f"❌ Window adjustment failed: {type(e).__name__}: "
                f"{getattr(e, 'message', str(e))[:300]}")

    def _one_click_worker(self, interaction, acq, card_ref):
        """Post-ACK work for Approve & Acquire. Never raises.

        Receives DETACHED data and durable services only: no handler, no socket, no
        response callback. Every later Discord call is an independent outbound one.
        """
        try:
            store = _acquisition_store()
            acquisition_ops.handle_one_click_adapter(
                interaction, acq, http_json=http_json, store=store,
                detached=card_ref, actor=card_ref.get("actor") or "unknown",
                ctx=self._acquisition_context(acq, store),
                runner=_acquisition_live_runner(),
                required_fingerprint=(acq.get("fingerprint") or ""))
        except Exception as e:  # noqa: BLE001
            acquisition_ops.notify_private(
                http_json, interaction,
                f"❌ Acquisition failed: {type(e).__name__}: "
                f"{getattr(e, 'message', str(e))[:300]}")

    def _handle_modal_submit(self, interaction):
        data = interaction.get("data", {})
        custom_id = data.get("custom_id", "")

        # Manual Intake SELECT SEGMENT modal — its OWN namespace, so a manual submit
        # is never routed into the acquisition lane. ACK first (type 6), then the
        # SAME manual-intake execution runs on a worker.
        mi_window = acquisition_ops.parse_window_modal_custom_id(
            custom_id, prefix=acquisition_ops.MANUAL_WINDOW_MODAL_CUSTOM_ID)
        if mi_window is not None:
            self._send_json(200, {"type": 6})
            threading.Thread(target=self._mi_choice_worker,
                             args=(interaction, MI_ACT_SEGMENT, mi_window[0]),
                             daemon=True).start()
            return

        # Adjust Window: the message id travelled in the custom_id, so the SAME
        # card can be updated in place after the slow work.
        window_ref = acquisition_ops.parse_window_modal_custom_id(custom_id)
        if window_ref is not None:
            # WIRE-LEVEL ACK: write + FLUSH HTTP 200 (type 6) and return from this
            # request handler. Nothing slow may run before this line: no store, no
            # Supabase, no Discord call. Everything else happens on the worker.
            self._send_json(200, {"type": 6})
            threading.Thread(target=self._window_modal_worker,
                             args=(interaction, window_ref,
                                   self._card_ref(interaction)), daemon=True).start()
            return
        intake = intake_card.parse_cid(custom_id)
        if intake is not None and intake[0] == "details":
            self._intake_details_submit(interaction, intake[1], data)
            return
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
        return member.get("nick") or u.get("global_name") or u.get("username") or "unknown"

    def _reviewer_user_id(self, interaction):
        """Numeric Discord user id of the interacting user (from member or user
        payload). Used to bind a pending edit session for LAUNCH_ACTIVITY."""
        member = interaction.get("member") or {}
        user = interaction.get("user") or {}
        u = member.get("user") or user or {}
        return str(u.get("id")) if u.get("id") else ""

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
        # flush=True is REQUIRED: stdout is block-buffered when redirected to
        # adapter.log, so without it a response line sits in the buffer until a
        # LATER request flushes it. That makes a completed request look like one
        # that never responded (it cost real diagnosis time on the editor
        # completion path, which returned 200 while appearing silent).
        print(f"[adapter] {self.address_string()} - {fmt % args}", flush=True)


def main():
    server = ThreadingHTTPServer((LISTEN_HOST, LISTEN_PORT), Handler)
    print(f"LOOM Discord Review Adapter listening on {LISTEN_HOST}:{LISTEN_PORT} "
          f"source_sha256={SOURCE_SHA256[:12]} loaded_at={LOADED_AT}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
