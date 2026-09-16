"""Deterministic identity + duplicate handling.

No paid model, no AI. Pure normalization + SHA-256 fingerprinting, so the same
source always maps to the same identity across runs, queries, and tenants
(policy permitting).

Responsibilities:
  * canonical URL normalization (scheme/host/port normalization, tracking-param
    removal, trailing-slash handling, fragment stripping);
  * platform content-ID extraction (YouTube video/short/channel IDs, etc.);
  * creator/channel normalization;
  * tenant-scoped fingerprint and global fingerprint;
  * repost vs near-duplicate classification boundaries.

A "repost" = same global content ID re-uploaded/re-shared (same platform, same
content id). A "near-duplicate" = distinct content id whose normalized title
matches an existing record (cheap, deterministic — not perceptual hashing,
which is a later download-stage concern).
"""

from __future__ import annotations

import hashlib
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# ---------------------------------------------------------------------------
# Tracking / irrelevant query parameters removed during canonicalization.
# ---------------------------------------------------------------------------
_TRACKING_PARAMS: frozenset[str] = frozenset({
    "fbclid", "gclid", "gclsrc", "dclid", "msclkid", "mc_cid", "mc_eid",
    "igshid", "igsh", "ref", "ref_src", "source", "utm_source", "utm_medium",
    "utm_campaign", "utm_term", "utm_content", "utm_id", "si", "feature",
    "embeds_referring_euri", "sharing", "share", "mibextid", "sc_channel",
    "srsltid", "hmac", "wbraid", "gbraid", "twclid", "campaign_id",
})

# YouTube host forms that all canonicalize to the same identity.
_YOUTUBE_HOSTS: frozenset[str] = frozenset({
    "www.youtube.com", "youtube.com", "m.youtube.com", "music.youtube.com",
    "youtu.be",
})

_YOUTUBE_VIDEO_RE = re.compile(
    r"(?:^|[/?=&])(?P<id>[A-Za-z0-9_-]{11})(?:[/?&#]|$)"
)


def normalize_url(url: str) -> str:
    """Canonicalize a URL for identity comparison.

    Lowercases scheme + host, strips a default port, removes the fragment and
    known tracking parameters, sorts the remaining query params (stable order),
    and strips a trailing slash from the path. ``https`` stays https; ``http``
    is left as-is (callers may prefer to upgrade explicitly).
    """
    if not url:
        return ""
    url = url.strip()
    # YouTube youtu.be shorts share the same video id as watch?v=.
    parts = urlsplit(url)
    scheme = parts.scheme.lower() or "https"
    netloc = parts.netloc.lower()
    # Drop default ports.
    if netloc.endswith(":80") and scheme == "http":
        netloc = netloc[:-3]
    elif netloc.endswith(":443") and scheme == "https":
        netloc = netloc[:-4]
    # Drop leading www.
    if netloc.startswith("www."):
        netloc = netloc[4:]

    path = parts.path
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")

    # Filter + sort query params deterministically.
    qs = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
          if k.lower() not in _TRACKING_PARAMS]
    qs.sort(key=lambda kv: kv[0].lower())
    query = urlencode(qs)

    return urlunsplit((scheme, netloc, path, query, ""))


def youtube_video_id(url: str) -> str:
    """Extract the 11-char YouTube video ID from any standard YouTube URL form
    (watch, shorts, youtu.be, embed, live). Returns '' if not recognizable."""
    if not url:
        return ""
    parts = urlsplit(url)
    host = parts.netloc.lower()
    host = host[4:] if host.startswith("www.") else host

    if host in _YOUTUBE_HOSTS:
        # youtu.be/<id>, /shorts/<id>, /embed/<id>, /live/<id>, /v/<id>
        m = re.search(r"/(shorts|embed|live|v)/([A-Za-z0-9_-]{11})(?:[/?#]|$)", parts.path)
        if m:
            return m.group(2)
        if host == "youtu.be":
            m = re.search(r"^/([A-Za-z0-9_-]{11})(?:[/?#]|$)", parts.path)
            if m:
                return m.group(1)
        # watch?v=<id>
        qs = dict(parse_qsl(parts.query))
        if "v" in qs and re.fullmatch(r"[A-Za-z0-9_-]{11}", qs["v"]):
            return qs["v"]
    return ""


def youtube_channel_id(url: str) -> str:
    """Extract a YouTube channel ID (UC...) or handle (@...) from a URL."""
    if not url:
        return ""
    parts = urlsplit(url)
    host = parts.netloc.lower()
    host = host[4:] if host.startswith("www.") else host
    if host not in _YOUTUBE_HOSTS and host != "youtube.com":
        return ""
    m = re.search(r"/channel/(UC[A-Za-z0-9_-]{22})(?:[/?#]|$)", parts.path)
    if m:
        return m.group(1)
    m = re.search(r"/@([A-Za-z0-9_.-]+)", parts.path)
    if m:
        return "@" + m.group(1)
    return ""


def extract_content_id(platform: str, url: str) -> str:
    """Platform-specific content-ID extraction. Returns '' when unknown."""
    platform = (platform or "").strip().lower()
    if platform in ("youtube", "youtube/search", "youtube-shorts"):
        return youtube_video_id(url) or youtube_channel_id(url)
    return ""


def normalize_creator(identity: str) -> str:
    """Normalize a creator/channel identity string (lowercase, trim, collapse
    whitespace). Does not strip '@' — callers decide handle semantics."""
    if not identity:
        return ""
    return re.sub(r"\s+", " ", identity.strip().lower())


def _sha256_hex(*parts: str) -> str:
    joined = "\x1f".join(parts)  # unit separator — cannot collide across fields
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def tenant_fingerprint(tenant_id: str, platform: str, content_id: str) -> str:
    """Tenant-scoped fingerprint: same content in a different tenant gets a
    different fingerprint (isolation). Empty content_id -> ''."""
    if not content_id:
        return ""
    return _sha256_hex("tenant", tenant_id, platform, content_id)


def global_fingerprint(platform: str, content_id: str) -> str:
    """Global fingerprint across tenants for the same platform content."""
    if not content_id:
        return ""
    return _sha256_hex("global", platform, content_id)


def duplicate_fingerprint(tenant_id: str, platform: str, content_id: str) -> str:
    """Alias of tenant_fingerprint — the value used for duplicate detection."""
    return tenant_fingerprint(tenant_id, platform, content_id)


def title_key(title: str) -> str:
    """Deterministic normalized title key for near-duplicate detection."""
    if not title:
        return ""
    t = title.strip().lower()
    t = re.sub(r"[^\w\s]", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def near_duplicate_key(platform: str, creator_channel_identity: str, title: str) -> str:
    """A deterministic key grouping likely near-duplicates: same platform +
    same creator + same normalized title. Perceptual hashing is out of scope
    here (it needs a downloaded frame) — this is the cheap text boundary."""
    return _sha256_hex("near", platform, normalize_creator(creator_channel_identity), title_key(title))


def crosspost_key(platform: str, title: str) -> str:
    """Deterministic cross-post key: same platform + same normalized title,
    author-agnostic. Reddit cross-posts share a title but a DIFFERENT post ID
    and (usually) a different author, so the author-aware near_duplicate_key
    would miss them. This key is a documented heuristic boundary (no API
    crosspost_parent field available over RSS), not perceptual identity."""
    return _sha256_hex("crosspost", platform, title_key(title))


def classify_duplicate(
    tenant_id: str,
    platform: str,
    content_id: str,
    seen_fingerprints: set[str] | None = None,
    seen_global_ids: set[str] | None = None,
    seen_near_keys: set[str] | None = None,
    seen_crosspost_keys: set[str] | None = None,
    creator_channel_identity: str = "",
    title: str = "",
) -> tuple[str, str]:
    """Classify a candidate against previously seen identities.

    Returns ``(kind, reason)`` where kind is one of:
      * ``"exact"`` — same tenant/platform/content-id already seen
        (cross-query duplicate within a tenant);
      * ``"repost"`` — same global content id already seen (cross-tenant/cross
        query repost boundary);
      * ``"crosspost"`` — same platform + normalized title, different content id
        (author-agnostic cross-post heuristic, no API crosspost_parent field
        available over RSS);
      * ``"near"`` — same creator + normalized title (near-duplicate policy
        boundary);
      * ``"new"`` — no collision.

    ``seen_fingerprints`` / ``seen_global_ids`` / ``seen_near_keys`` /
    ``seen_crosspost_keys`` are caller-maintained sets so the classification is
    explicit and testable.
    """
    seen_fingerprints = seen_fingerprints or set()
    seen_global_ids = seen_global_ids or set()
    seen_near_keys = seen_near_keys or set()
    seen_crosspost_keys = seen_crosspost_keys or set()

    tenant_fp = duplicate_fingerprint(tenant_id, platform, content_id)
    global_fp = global_fingerprint(platform, content_id)
    near_key = near_duplicate_key(platform, creator_channel_identity, title)
    xpost_key = crosspost_key(platform, title)

    if content_id and tenant_fp in seen_fingerprints:
        return "exact", "tenant-scoped fingerprint already observed (exact duplicate)"

    if content_id and global_fp in seen_global_ids:
        return "repost", "global content id already observed (repost boundary)"

    if xpost_key and xpost_key in seen_crosspost_keys:
        return "crosspost", "cross-post (same platform + normalized title, different id)"

    if near_key and near_key in seen_near_keys:
        return "near", "near-duplicate (same creator + normalized title)"

    return "new", ""
