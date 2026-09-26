"""Manual Source Intake V1 — URL validation, canonicalization, dedup, persistence.

Accepts a manually-submitted source URL for a tenant/profile, validates it
against a STRICT platform allowlist, canonicalizes it deterministically (no
network fetch), deduplicates it, and persists it with full provenance/audit.

Security: rejects non-http(s) schemes, credential-bearing URLs, local/private/
link-local/reserved network targets, non-standard ports, and any host outside
the platform allowlist. No media is downloaded, no redirect is followed, no
content analysis is performed, and no score is ever fabricated.

Platform status:
  * youtube / reddit  -> ``accepted`` (may enter the existing qualification
                         pipeline later — NOT analyzed synchronously).
  * tiktok / instagram / twitch / kick -> ``accepted_pending_platform_support``
    (safely accepted; never marked analyzed; no fabricated score).

A manual submission is a CANDIDATE only — never approval for acquisition.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

# ---------------------------------------------------------------------------
# Platform allowlist (host -> platform). Strict: only these hosts are accepted.
# ---------------------------------------------------------------------------
PLATFORM_HOSTS: dict[str, frozenset[str]] = {
    "youtube": frozenset({"youtube.com", "www.youtube.com", "m.youtube.com",
                          "music.youtube.com", "youtu.be"}),
    "reddit": frozenset({"reddit.com", "www.reddit.com", "np.reddit.com",
                         "old.reddit.com", "new.reddit.com"}),
    "tiktok": frozenset({"tiktok.com", "www.tiktok.com", "m.tiktok.com",
                         "vm.tiktok.com", "vt.tiktok.com"}),
    "instagram": frozenset({"instagram.com", "www.instagram.com", "m.instagram.com"}),
    "twitch": frozenset({"twitch.tv", "www.twitch.tv", "m.twitch.tv", "clips.twitch.tv"}),
    "kick": frozenset({"kick.com", "www.kick.com"}),
}

HOST_TO_PLATFORM: dict[str, str] = {
    h: p for p, hs in PLATFORM_HOSTS.items() for h in hs
}

# Platforms with an existing qualification pipeline vs. pending adapter support.
SUPPORTED_PLATFORMS: frozenset[str] = frozenset({"youtube", "reddit"})
PENDING_PLATFORMS: frozenset[str] = frozenset({"tiktok", "instagram", "twitch", "kick"})

PLATFORM_DISPLAY_NAMES: dict[str, str] = {
    "youtube": "YouTube",
    "reddit": "Reddit",
    "tiktok": "TikTok",
    "instagram": "Instagram",
    "twitch": "Twitch",
    "kick": "Kick",
}

STATUS_ACCEPTED = "accepted"
STATUS_PENDING_PLATFORM = "accepted_pending_platform_support"
STATUS_AWAITING_TENANT = "awaiting_tenant_assignment"

COMMAND_NAME = "submit-source"

# Unassigned routing state: tenant_id == '' (NOT a tenant).
DESTINATION_UNASSIGNED = ""

# Discord-facing token/label for the Unassigned/General choice. Discord option
# choice values cannot be empty, so autocomplete returns this sentinel and the
# server normalizes it back to '' (see normalize_destination).
DESTINATION_UNASSIGNED_TOKEN = "unassigned"
DESTINATION_UNASSIGNED_LABEL = "Unassigned / General"

_NON_PRODUCTION_MARKERS = (
    "acceptance", "test", "proof", "example", "fixture",
    "demo", "sample", "sandbox", "staging", "dev",
)


def is_non_production_tenant(tenant_id: str) -> bool:
    return any(m in (tenant_id or "").lower() for m in _NON_PRODUCTION_MARKERS)


def normalize_destination(destination: str) -> str:
    """Map the human-facing 'unassigned' token (and blank) to '' (Unassigned)."""
    dest = (destination or "").strip()
    if dest == "" or dest.lower() == DESTINATION_UNASSIGNED_TOKEN:
        return DESTINATION_UNASSIGNED
    return dest


def validate_destination(destination: str, active_tenants: set[str]) -> str:
    """Validate a destination: Unassigned/General or an active, non-proof tenant.
    Returns the normalized destination ('' for Unassigned). Raises SubmissionError otherwise."""
    dest = normalize_destination(destination)
    if dest == DESTINATION_UNASSIGNED:
        return DESTINATION_UNASSIGNED
    if dest not in active_tenants:
        raise SubmissionError("INVALID_DESTINATION", f"{dest!r} is not an active tenant")
    if is_non_production_tenant(dest):
        raise SubmissionError("INVALID_DESTINATION", f"{dest!r} is a non-production tenant")
    return dest


def destination_choices(active_tenants: set[str]) -> list[dict[str, str]]:
    """Discord autocomplete choices: Unassigned/General first, then active tenants (sorted)."""
    choices = [{"name": DESTINATION_UNASSIGNED_LABEL, "value": DESTINATION_UNASSIGNED_TOKEN}]
    for tenant in sorted(t for t in active_tenants if not is_non_production_tenant(t)):
        choices.append({"name": tenant, "value": tenant})
    return choices


def routing_label(tenant_id: str) -> str:
    """Human-facing routing state label."""
    dest = normalize_destination(tenant_id)
    if dest == DESTINATION_UNASSIGNED:
        return "Awaiting tenant assignment"
    return f"Assigned to {dest}"


def platform_support_label(platform: str) -> str:
    """Human-facing platform support label."""
    name = PLATFORM_DISPLAY_NAMES.get(platform, platform.title())
    if platform in SUPPORTED_PLATFORMS:
        return "Supported"
    if platform in PENDING_PLATFORMS:
        return f"Pending {name} support"
    return "Unknown"

CONTENT_FORMATS: frozenset[str] = frozenset({
    "reaction_single", "reaction_compilation", "commentary_story",
    "ranking_top5", "stream_highlight", "hold_unassigned",
})

_TRACKING_PARAMS: frozenset[str] = frozenset({
    "fbclid", "gclid", "gclsrc", "dclid", "msclkid", "mc_cid", "mc_eid",
    "igshid", "igsh", "stkn", "ref", "ref_src", "source", "utm_source", "utm_medium",
    "utm_campaign", "utm_term", "utm_content", "utm_id", "si", "feature",
    "embeds_referring_euri", "sharing", "share", "mibextid", "sc_channel",
    "srsltid", "hmac", "wbraid", "gbraid", "twclid", "campaign_id",
})

_YOUTUBE_ID = re.compile(r"(?:^|[/?=&])(?P<id>[A-Za-z0-9_-]{11})(?:[/?&#]|$)")
_REDDIT_POST = re.compile(r"/comments/(?P<id>[A-Za-z0-9]+)(?:/|$)")


class SubmissionError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message


# ---------------------------------------------------------------------------
# URL validation + canonicalization (deterministic, no network)
# ---------------------------------------------------------------------------
def _is_private_host(host: str) -> bool:
    host = (host or "").lower().rstrip(".")
    if host == "localhost" or host.endswith(".localhost"):
        return True
    if host.endswith((".local", ".internal", ".lan", ".home", ".arpa")):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return (ip.is_private or ip.is_loopback or ip.is_link_local
            or ip.is_reserved or ip.is_multicast or ip.is_unspecified)


def canonicalize_url(url: str) -> str:
    """Deterministic canonicalization (NO network fetch, no redirect)."""
    parts = urllib.parse.urlsplit(url)
    scheme = (parts.scheme or "https").lower()
    netloc = (parts.netloc or "").lower()
    if netloc.endswith(":80") and scheme == "http":
        netloc = netloc[:-3]
    elif netloc.endswith(":443") and scheme == "https":
        netloc = netloc[:-4]

    host = netloc
    if "@" in host:
        host = host.split("@", 1)[1]

    # youtu.be/<id> -> youtube.com/watch?v=<id> (deterministic, known mapping).
    if host == "youtu.be":
        m = re.search(r"^/([A-Za-z0-9_-]{11})(?:[/?#]|$)", parts.path or "")
        if m:
            return f"https://www.youtube.com/watch?v={m.group(1)}"

    path = parts.path or ""
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    qs = [(k, v) for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
          if k.lower() not in _TRACKING_PARAMS]
    qs.sort(key=lambda kv: kv[0].lower())
    query = urllib.parse.urlencode(qs)
    return urllib.parse.urlunsplit((scheme, host, path, query, ""))


def _extract_content_id(platform: str, parts: urllib.parse.SplitResult) -> str:
    """Best-effort platform content id (empty when not deterministically known)."""
    path = parts.path or ""
    query = parts.query or ""
    if platform == "youtube":
        m = re.search(r"/(shorts|embed|live|v)/([A-Za-z0-9_-]{11})(?:[/?#]|$)", path)
        if m:
            return m.group(2)
        qs = dict(urllib.parse.parse_qsl(query))
        if "v" in qs and re.fullmatch(r"[A-Za-z0-9_-]{11}", qs["v"]):
            return qs["v"]
        m = re.search(r"^/([A-Za-z0-9_-]{11})(?:[/?#]|$)", path)
        if m and parts.hostname == "youtu.be":
            return m.group(1)
        return ""
    if platform == "reddit":
        m = _REDDIT_POST.search(path)
        return m.group("id") if m else ""
    if platform == "tiktok":
        m = re.search(r"/(?:video|v)/(\d+)", path)
        return m.group(1) if m else ""
    if platform == "instagram":
        m = re.search(r"/(?:p|reels?|tv)/([A-Za-z0-9_-]+)", path)
        return m.group(1) if m else ""
    if platform == "twitch":
        # Twitch canonical clip URLs use the singular /clip/<slug> form;
        # /clips/<id> and /videos/<id> remain accepted for compatibility.
        m = re.search(r"/(?:clip|clips|videos)/([A-Za-z0-9_-]+)", path)
        return m.group(1) if m else ""
    if platform == "kick":
        m = re.search(r"/(?:clip|video)/([A-Za-z0-9_-]+)", path)
        return m.group(1) if m else ""
    return ""


def validate_and_canonicalize(url: str) -> dict[str, str]:
    """Validate + canonicalize a submitted URL. Raises SubmissionError on any
    rejection. Returns {platform, canonical_url, content_id, status}."""
    url = (url or "").strip()
    if not url:
        raise SubmissionError("EMPTY_URL", "url is empty")
    if len(url) > 2048:
        raise SubmissionError("URL_TOO_LONG", "url exceeds 2048 chars")

    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError as e:
        raise SubmissionError("BAD_URL", f"unparseable url: {e}") from e

    scheme = (parts.scheme or "").lower()
    if scheme not in ("http", "https"):
        raise SubmissionError("BAD_SCHEME", f"scheme {scheme!r} not allowed (http/https only)")

    if parts.username or parts.password:
        raise SubmissionError("CREDENTIALS", "credential-bearing URL rejected")

    host = (parts.hostname or "").lower()
    if not host:
        raise SubmissionError("NO_HOST", "no host in URL")

    if _is_private_host(host):
        raise SubmissionError("PRIVATE_HOST", "local/private network target rejected")

    port = parts.port
    if port is not None and port not in (80, 443):
        raise SubmissionError("BAD_PORT", f"port {port} not allowed")

    platform = HOST_TO_PLATFORM.get(host)
    if platform is None:
        raise SubmissionError("UNSUPPORTED_HOST", f"host {host!r} not in platform allowlist")

    if platform == "youtube":
        # Strict exact-URL contract: one video, one canonical form, one identity
        # shared with the automated lane. Untracked params are stripped; anything
        # that could select different content is rejected.
        try:
            import youtube_url as _yt
        except ImportError:  # acquire the sibling package when running standalone
            import os as _os
            import sys as _sys
            _sys.path.insert(0, _os.path.join(
                _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "acquisition"))
            import youtube_url as _yt
        try:
            parsed = _yt.parse(url)
        except _yt.YouTubeUrlError as e:
            raise SubmissionError(e.code, e.message) from e
        canonical, content_id = parsed["canonical_url"], parsed["video_id"]
    elif platform == "kick":
        try:
            import kick_url as _kick
        except ImportError:
            import os as _os
            import sys as _sys
            _sys.path.insert(0, _os.path.join(
                _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "acquisition"))
            import kick_url as _kick
        try:
            parsed = _kick.parse(url)
        except _kick.KickUrlError as e:
            raise SubmissionError(e.code, e.message) from e
        canonical, content_id = parsed["canonical_url"], parsed["content_id"]
    else:
        canonical = canonicalize_url(url)
        content_id = _extract_content_id(platform, parts)
    status = STATUS_ACCEPTED if platform in SUPPORTED_PLATFORMS else STATUS_PENDING_PLATFORM

    return {
        "platform": platform,
        "canonical_url": canonical,
        "content_id": content_id,
        "status": status,
    }


def status_for_platform(platform: str) -> str:
    return STATUS_ACCEPTED if platform in SUPPORTED_PLATFORMS else STATUS_PENDING_PLATFORM


def submission_fingerprint(platform: str, canonical_url: str) -> str:
    """Deterministic dedup key (platform + canonical URL)."""
    raw = f"{platform}\x1f{canonical_url}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Submission payload + persistence
# ---------------------------------------------------------------------------
def build_submission(
    *,
    url: str,
    destination: str,
    content_format: str,
    submitter: str,
    time_start_s: float | None = None,
    time_end_s: float | None = None,
    reviewer_note: str = "",
    active_tenants: set[str] | None = None,
) -> dict[str, Any]:
    """Validate + build a full submission payload (raises SubmissionError).

    ``destination`` is a tenant id, or '' for Unassigned/General. ``profile_id``
    is derived server-side later (not asked from the human)."""
    if content_format not in CONTENT_FORMATS:
        raise SubmissionError("BAD_FORMAT", f"unknown content_format {content_format!r}")
    dest = normalize_destination(destination)
    if active_tenants is not None:
        dest = validate_destination(dest, active_tenants)
    if time_start_s is not None and time_start_s < 0:
        raise SubmissionError("BAD_TIME", "start timestamp must be >= 0")
    if time_end_s is not None and time_end_s < 0:
        raise SubmissionError("BAD_TIME", "end timestamp must be >= 0")
    if (time_start_s is not None and time_end_s is not None and time_end_s < time_start_s):
        raise SubmissionError("BAD_TIME", "end timestamp must be >= start timestamp")

    v = validate_and_canonicalize(url)
    if dest == DESTINATION_UNASSIGNED:
        status = STATUS_AWAITING_TENANT
    elif v["platform"] in SUPPORTED_PLATFORMS:
        status = STATUS_ACCEPTED
    else:
        status = STATUS_PENDING_PLATFORM

    return {
        "tenant_id": dest,
        "profile_id": "",  # derived server-side later
        "platform": v["platform"],
        "source_url": v["canonical_url"],
        "original_url": url.strip(),
        "platform_content_id": v["content_id"],
        "content_format": content_format,
        "time_start_s": time_start_s,
        "time_end_s": time_end_s,
        "reviewer_note": (reviewer_note or "").strip(),
        "submitter": submitter,
        "status": status,
        "qualification_required": True,
        "duplicate_fingerprint": submission_fingerprint(v["platform"], v["canonical_url"]),
    }


def _headers(key: str, body: bool = False) -> dict[str, str]:
    h = {"apikey": key, "Authorization": f"Bearer {key}", "User-Agent": "LOOM-ManualIntake/1.0"}
    if body:
        h["Content-Type"] = "application/json"
        h["Prefer"] = "resolution=ignore-duplicates,return=representation"
    return h


def is_duplicate(url: str, key: str, fingerprint: str, tenant_id: str = "") -> bool:
    q_fp = urllib.parse.quote(fingerprint)
    q_t = urllib.parse.quote(tenant_id)
    req = urllib.request.Request(
        f"{url}/rest/v1/loom_manual_submissions?duplicate_fingerprint=eq.{q_fp}&tenant_id=eq.{q_t}&select=id&limit=1",
        headers=_headers(key),
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            rows = json.loads(r.read().decode() or "[]")
        return isinstance(rows, list) and len(rows) > 0
    except urllib.error.HTTPError as e:
        raise SubmissionError("STORE_ERROR", f"HTTP {e.code}: {e.read().decode()[:200]}") from e


def persist_submission(url: str, key: str, payload: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Persist a submission (per-tenant first-wins dedup). Returns (row, created)."""
    req = urllib.request.Request(
        f"{url}/rest/v1/loom_manual_submissions?on_conflict=duplicate_fingerprint,tenant_id",
        data=json.dumps(payload).encode(),
        headers=_headers(key, body=True),
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            rows = json.loads(r.read().decode() or "[]")
    except urllib.error.HTTPError as e:
        raise SubmissionError("STORE_ERROR", f"HTTP {e.code}: {e.read().decode()[:200]}") from e
    inserted = isinstance(rows, list) and len(rows) > 0
    if inserted:
        return rows[0], True
    # Already submitted — return existing row (same fingerprint + tenant).
    q_fp = urllib.parse.quote(payload["duplicate_fingerprint"])
    q_t = urllib.parse.quote(payload.get("tenant_id", ""))
    req2 = urllib.request.Request(
        f"{url}/rest/v1/loom_manual_submissions?duplicate_fingerprint=eq.{q_fp}&tenant_id=eq.{q_t}&select=*&limit=1",
        headers=_headers(key),
    )
    with urllib.request.urlopen(req2, timeout=30) as r:
        existing = json.loads(r.read().decode() or "[]")
    if isinstance(existing, list) and existing:
        return existing[0], False
    return payload, True
