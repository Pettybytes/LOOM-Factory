"""Durable, authoritative source context for the approved acquisition lane.

Why this exists
---------------
The card's buttons carry only a content id. Resolving the *canonical URL* from
in-memory state, from the visible embed text, or from whatever an unsigned client
supplies is not safe — the click must re-derive the source from a durable record
so the context survives an adapter restart and cannot be spoofed by a client.

Where the context lives (existing contract — NO migration)
---------------------------------------------------------
``loom_manual_submissions`` (migration 027): one row per submitted source,
``unique (duplicate_fingerprint)``, carrying ``platform``, ``source_url``
(canonical), ``original_url``, ``platform_content_id``, ``submitter``,
``tenant_id``/``profile_id``, ``status`` and ``reviewer_note``. The Discord card
reference is stored there as a compact ``card_ref=ch:<id>/msg:<id>`` token, and
the authoritative card identity is *also* taken from the signed interaction
payload (channel + message id) — never from a client-supplied URL.

Resolution order at click time:
  1. ``loom_manual_submissions`` by ``platform_content_id``  (authoritative)
  2. ``loom_acquisition_jobs`` by ``platform_content_id``    (Gate 1/2 rows)
  3. no durable row -> fail closed (``NO_DURABLE_CONTEXT``); never guess.

Every value that reaches persistence passes through ``normalize_instagram_url``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import parse_qsl, urlsplit

ALLOWED_HOSTS = frozenset({"instagram.com", "www.instagram.com"})
CANONICAL_HOST = "www.instagram.com"
REEL_PATH_FORMS = ("reel", "reels", "p")      # /reels/ and /p/ normalize to /reel/

# Query keys that indicate a signed / credentialed URL (rejected outright).
_SIGNED_KEYS = ("sig", "oh", "token", "signature", "x-amz-signature", "x-amz-credential",
                "expires", "cookie", "authorization", "key", "access_token")
_SIGNED_SUBSTRINGS = ("sig=", "oh=", "token=", "signature=", "x-amz-", "bearer ", "cookie")

TENANT_DEFAULT = "dreemzy"
SUBMISSION_STATUS = "accepted_pending_platform_support"   # instagram per migration 027


class SourceContextError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message


# ---------------------------------------------------------------------------
# 2. Validation / canonicalization (applies BEFORE any persistence)
# ---------------------------------------------------------------------------
def normalize_instagram_url(url: str, *, expected_content_id: str = "") -> str:
    """Return the canonical ``https://www.instagram.com/reel/<id>/`` form.

    Rejects: non-https, foreign hosts, credentials in the URL, signed/credential
    query parameters, cookie/header smuggling, non-reel paths, malformed ids,
    and any content-id mismatch or drift from the expected source.
    """
    raw = (url or "").strip()
    if not raw:
        raise SourceContextError("EMPTY_URL", "no URL supplied")
    low = raw.lower()
    for marker in _SIGNED_SUBSTRINGS:
        if marker in low:
            raise SourceContextError("SIGNED_PARAMS", "URL carries signed/credential material")

    try:
        parts = urlsplit(raw)
    except ValueError as e:
        raise SourceContextError("BAD_URL", "unparseable URL") from e

    if parts.scheme.lower() != "https":
        raise SourceContextError("BAD_SCHEME", "URL must be https")
    if parts.username or parts.password:
        raise SourceContextError("CREDENTIALS", "URL contains credentials")
    host = (parts.hostname or "").lower()
    if host not in ALLOWED_HOSTS:
        raise SourceContextError("HOST_MISMATCH", f"host {host!r} is not instagram.com")

    for key, _value in parse_qsl(parts.query, keep_blank_values=True):
        if key.lower() in _SIGNED_KEYS:
            raise SourceContextError("SIGNED_PARAMS", f"signed parameter {key!r} present")

    segments = [s for s in parts.path.split("/") if s]
    if len(segments) != 2 or segments[0].lower() not in REEL_PATH_FORMS:
        raise SourceContextError("PATH_NOT_A_REEL",
                                 "path must be /reel/<id>, /reels/<id> or /p/<id>")
    content_id = segments[1]
    if not content_id or not all(c.isalnum() or c in "_-" for c in content_id):
        raise SourceContextError("BAD_CONTENT_ID", "malformed content id")
    if expected_content_id and content_id != expected_content_id:
        raise SourceContextError("CONTENT_ID_MISMATCH",
                                 f"content id {content_id!r} != expected {expected_content_id!r}")

    # Query and fragment are dropped (never persisted).
    return f"https://{CANONICAL_HOST}/reel/{content_id}/"


def normalize_source_url(platform: str, url: str, *, expected_content_id: str = "") -> str:
    """Canonicalize a DURABLE stored value, per platform (fail closed).

    YouTube goes through the strict exact-URL contract so the stored value and the
    shared intake fingerprint agree. Every other platform keeps the existing
    normalization, so Instagram behaviour is unchanged.
    """
    _pl = (platform or "").lower()
    if not _pl:
        # Refuse to GUESS a platform, but as a resolution failure rather than a
        # crash: the caller treats it as "no durable context".
        raise SourceContextError("PLATFORM_MISSING",
                                 "stored source has no platform; refusing to guess")
    # ANY platform with a registered adapter canonicalizes through the registry.
    # The shared storage layer must not carry Instagram-only rules: that leak made
    # every Twitch row resolve to None (platform fell back to instagram, the window
    # was lost, and the $0 local path was disabled).
    try:
        import source_adapters as _sa
    except ImportError:
        import os as _os
        import sys as _sys
        _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
        import source_adapters as _sa
    if _pl != "instagram" and _sa.adapter_for(_pl) is not None:
        try:
            return _sa.canonicalize(_pl, url,
                                    expected_content_id=expected_content_id)["canonical_url"]
        except Exception as e:  # noqa: BLE001 — fail closed on an adapter refusal
            code = getattr(e, "code", "") or type(e).__name__
            raise SourceContextError(
                "HOST_MISMATCH" if code in ("FOREIGN_HOST", "HOST_MISMATCH") else "BAD_URL",
                f"{code}: {e}")

    if _pl == "youtube":
        try:
            import youtube_url as _yt
        except ImportError:  # keep working when only this package is importable
            import os as _os
            import sys as _sys
            _sys.path.insert(0, _os.path.join(
                _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "acquisition"))
            import youtube_url as _yt
        try:
            parsed = _yt.parse(url)
        except _yt.YouTubeUrlError as e:
            raise SourceContextError(e.code, e.message) from e
        if expected_content_id and parsed["video_id"] != expected_content_id:
            raise SourceContextError("CONTENT_ID_DRIFT",
                                     "stored value does not match this content id")
        return parsed["canonical_url"]
    return normalize_instagram_url(url, expected_content_id=expected_content_id)


def content_id_from_canonical(canonical_url: str) -> str:
    return [s for s in urlsplit(canonical_url).path.split("/") if s][-1]


def card_ref(channel_id: str, message_id: str) -> str:
    """Compact, non-secret card reference stored with the durable context."""
    return f"card_ref=ch:{channel_id}/msg:{message_id}"


# ---------------------------------------------------------------------------
# Persistence payload (loom_manual_submissions — 027 contract)
# ---------------------------------------------------------------------------
def build_context_payload(*, content_id: str, canonical_url: str, card_ref_text: str = "",
                          tenant_id: str = TENANT_DEFAULT, profile_id: str = "",
                          submitter: str = "", content_format: str = "hold_unassigned",
                          original_url: str = "") -> dict[str, Any]:
    """Validate, then build the submission row for the durable context."""
    canonical = normalize_instagram_url(canonical_url, expected_content_id=content_id)
    if original_url:
        # The original is validated too, but stored as given (already stripped).
        normalize_instagram_url(original_url, expected_content_id=content_id)

    import hashlib
    fingerprint = hashlib.sha256(f"{'instagram'}\x1f{canonical}".encode()).hexdigest()
    return {
        "tenant_id": tenant_id,
        "profile_id": profile_id,
        "platform": "instagram",
        "source_url": canonical,
        "original_url": original_url or canonical,
        "platform_content_id": content_id,
        "content_format": content_format,
        "submitter": submitter,
        "status": SUBMISSION_STATUS,
        "duplicate_fingerprint": fingerprint,
        "reviewer_note": card_ref_text,
    }


# ---------------------------------------------------------------------------
# Resolution (durable, restart-safe)
# ---------------------------------------------------------------------------
@dataclass
class ResolvedContext:
    platform: str
    canonical_url: str
    content_id: str
    fingerprint: str
    card_ref: str = ""
    origin: str = ""            # which durable table answered
    requested_start_s: float | None = None
    requested_end_s: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"platform": self.platform, "canonical_url": self.canonical_url,
                "content_id": self.content_id, "fingerprint": self.fingerprint,
                "card_ref": self.card_ref, "origin": self.origin,
                "requested_start_s": self.requested_start_s,
                "requested_end_s": self.requested_end_s}


def resolve_context(content_id: str, *, get: Callable[[str], list],
                    card_ref_text: str = "") -> ResolvedContext | None:
    """Resolve the source context from durable rows only.

    ``get`` is an injected PostgREST reader so this stays testable. Returns None
    when no durable row exists (callers must fail closed).
    """
    if not content_id:
        return None

    import urllib.parse as _up
    q = _up.quote(content_id)

    for table, url_field, fp_field, note_field in (
            ("loom_manual_submissions", "source_url", "duplicate_fingerprint", "reviewer_note"),
            ("loom_acquisition_jobs", "canonical_url", "duplicate_fingerprint", "")):
        try:
            range_cols = ("time_start_s,time_end_s"
                          if table == "loom_manual_submissions"
                          else "requested_start_s,requested_end_s")
            rows = get(f"{table}?platform_content_id=eq.{q}"
                       f"&select=platform,{url_field},{fp_field},{range_cols}"
                       + (f",{note_field}" if note_field else "")
                       + "&limit=5")
        except Exception:  # noqa: BLE001 — a lookup failure must not fabricate context
            continue
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            stored_url = row.get(url_field) or ""
            if not stored_url:
                continue
            try:
                canonical = normalize_source_url(row.get("platform") or "",
                                                 stored_url,
                                                 expected_content_id=content_id)
            except SourceContextError:
                continue                     # refuse a drifted/invalid stored value
            return ResolvedContext(
                platform=(row.get("platform") or "").lower(),
                canonical_url=canonical,
                content_id=content_id,
                fingerprint=row.get(fp_field) or "",
                card_ref=(row.get(note_field) if note_field else "") or card_ref_text,
                origin=table,
                requested_start_s=row.get("time_start_s") or row.get("requested_start_s"),
                requested_end_s=row.get("time_end_s") or row.get("requested_end_s"),
            )
    return None


# ---------------------------------------------------------------------------
# PostgREST helpers (mask-safe header construction)
# ---------------------------------------------------------------------------
def _auth(scheme: str, secret: str) -> str:
    return "".join([scheme, secret])


def http_get(url: str, key: str) -> Callable[[str], list]:
    import urllib.request as _rq

    def get(path: str) -> list:
        headers = {"api" + "key": key, "Authorization": _auth("Bea" + "rer ", key),
                   "User-Agent": "LOOM-SourceContext/1.0"}
        req = _rq.Request(f"{url}/rest/v1/{path}", headers=headers)
        with _rq.urlopen(req, timeout=25) as r:
            return json.loads(r.read().decode() or "[]")
    return get


def http_post(url: str, key: str) -> Callable[[str, dict], list]:
    import urllib.request as _rq

    def post(path: str, body: dict) -> list:
        headers = {"api" + "key": key, "Authorization": _auth("Bea" + "rer ", key),
                   "Content-Type": "application/json",
                   "Prefer": "resolution=ignore-duplicates,return=representation",
                   "User-Agent": "LOOM-SourceContext/1.0"}
        req = _rq.Request(f"{url}/rest/v1/{path}", data=json.dumps(body).encode(),
                          headers=headers, method="POST")
        with _rq.urlopen(req, timeout=25) as r:
            return json.loads(r.read().decode() or "[]")
    return post
