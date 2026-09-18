"""Strict YouTube exact-URL contract (shared by BOTH intake lanes).

One video, one identity, one canonical form. Everything that could turn a single
video request into "some other content" is rejected here, before any download:

Rejected: playlists, mixes, channels/handles/profiles, searches, feeds, live and
upcoming streams, unsupported path forms (embed/), malformed ids, id drift between
path and query, more than one requested video, playlist/selection parameters (even
attached to a watch URL), foreign hosts, URL credentials and non-http(s) schemes.

Canonical form (single, stable):  https://www.youtube.com/watch?v=<video_id>

The fingerprint is the same construction the manual intake uses
(``sha256("youtube\\x1f" + canonical_url)``), so a URL discovered automatically and
the same URL submitted manually resolve to ONE content identity and can never
produce duplicate work or duplicate downloads.
"""

from __future__ import annotations

import hashlib
import re
import urllib.parse

PLATFORM = "youtube"
CANONICAL_TEMPLATE = "https://www.youtube.com/watch?v={video_id}"

SUPPORTED_HOSTS = frozenset({
    "youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "www.youtu.be",
})

ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{11}$")

# Query parameters that only carry tracking/context — stripped, never identity.
TRACKING_PARAMS = frozenset({
    "si", "feature", "utm_source", "utm_medium", "utm_campaign", "utm_term",
    "utm_content", "utm_id", "fbclid", "gclid", "dclid", "msclkid", "mc_cid",
    "mc_eid", "igshid", "share", "share_id", "referrer", "ref", "source",
    "app", "ab_channel", "themeRefresh", "pp", "embeds_referring_origin",
})

# Parameters that expand the selection beyond the one requested video.
SELECTION_PARAMS = frozenset({
    "list", "index", "playnext", "start_radio", "shuffle", "loop", "playlist",
    "collection", "mix", "radio", "autoplay", "next",
})

# Paths that are never a single video.
REJECTED_PATH_PREFIXES = (
    "/playlist", "/channel", "/c/", "/user/", "/@", "/results", "/feed",
    "/gaming", "/premium", "/account", "/upload", "/podcasts", "/hashtag",
    "/live", "/embed", "/v/", "/e/", "/clip", "/post", "/about", "/redirect",
    "/attribution_link", "/watch_videos", "/videoseries", "/browse",
)


class YouTubeUrlError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message


def fingerprint_for(canonical_url: str) -> str:
    """Same construction as manual_intake.submission_fingerprint."""
    return hashlib.sha256(f"{PLATFORM}\x1f{canonical_url}".encode("utf-8")).hexdigest()


def _reject(code: str, message: str):
    raise YouTubeUrlError(code, message)


def parse(url: str) -> dict[str, str]:
    """Validate + canonicalize an exact YouTube video URL.

    Returns ``{platform, canonical_url, video_id, fingerprint, source_form}``.
    Raises ``YouTubeUrlError`` with a stable code on any rejection.
    """
    raw = (url or "").strip()
    if not raw:
        _reject("EMPTY_URL", "url is empty")
    if len(raw) > 2048:
        _reject("URL_TOO_LONG", "url exceeds 2048 chars")

    try:
        parts = urllib.parse.urlsplit(raw)
    except ValueError as e:
        _reject("BAD_URL", f"unparseable url: {e}")

    scheme = (parts.scheme or "").lower()
    if scheme not in ("http", "https"):
        _reject("BAD_SCHEME", f"scheme {scheme!r} not allowed (http/https only)")
    if parts.username or parts.password:
        _reject("CREDENTIALS", "credential-bearing URL rejected")

    host = (parts.hostname or "").lower()
    if not host:
        _reject("NO_HOST", "no host in URL")
    if host not in SUPPORTED_HOSTS:
        _reject("UNSUPPORTED_HOST", f"host {host!r} is not a YouTube video host")

    port = parts.port
    if port is not None and port not in (80, 443):
        _reject("BAD_PORT", f"port {port} not allowed")

    path = urllib.parse.unquote(parts.path or "")
    lowered = path.lower()
    for prefix in REJECTED_PATH_PREFIXES:
        if lowered == prefix.rstrip("/") or lowered.startswith(prefix):
            _reject("UNSUPPORTED_PATH",
                    f"path {prefix!r} is not a single-video URL")

    raw_qs = urllib.parse.parse_qsl(parts.query or "", keep_blank_values=True)
    selection = sorted({k.lower() for k, _ in raw_qs if k.lower() in SELECTION_PARAMS})
    if selection:
        _reject("SELECTION_PARAM",
                f"selection/playlist parameter(s) {', '.join(selection)} rejected")

    # --- exactly one video id, with no drift between path and query ---------
    path_id = ""
    source_form = ""
    segments = [s for s in path.split("/") if s]
    if host in ("youtu.be", "www.youtu.be"):
        if len(segments) != 1:
            _reject("AMBIGUOUS_PATH", "short link must contain exactly one video id")
        path_id, source_form = segments[0], "short_link"
    elif len(segments) == 1 and segments[0].lower() == "shorts":
        _reject("MALFORMED_ID", "shorts URL is missing its video id")
    elif len(segments) == 2 and segments[0].lower() == "shorts":
        path_id, source_form = segments[1], "shorts"
    elif len(segments) == 1 and segments[0].lower() == "watch":
        source_form = "watch"
    elif len(segments) == 0:
        source_form = "watch"          # bare host with ?v=
    else:
        _reject("UNSUPPORTED_PATH", f"path {path!r} is not a single-video URL")

    query_ids = [v for k, v in raw_qs if k.lower() == "v"]
    if len(query_ids) > 1:
        _reject("MULTIPLE_VIDEOS", "more than one requested video")
    query_id = query_ids[0] if query_ids else ""

    if path_id and query_id and path_id != query_id:
        _reject("CONTENT_ID_DRIFT", "path video id and ?v= disagree")
    video_id = path_id or query_id
    if not video_id:
        _reject("MISSING_VIDEO_ID", "no video id in URL")
    if not ID_PATTERN.match(video_id):
        _reject("MALFORMED_ID", "video id must be exactly 11 URL-safe characters")

    canonical = CANONICAL_TEMPLATE.format(video_id=video_id)
    return {
        "platform": PLATFORM,
        "canonical_url": canonical,
        "video_id": video_id,
        "fingerprint": fingerprint_for(canonical),
        "source_form": source_form or "watch",
    }


def try_parse(url: str) -> dict[str, str] | None:
    """Non-raising variant for intake paths that classify rather than reject."""
    try:
        return parse(url)
    except YouTubeUrlError:
        return None


def is_youtube_url(url: str) -> bool:
    """Cheap host check — the full contract still runs through parse()."""
    try:
        host = (urllib.parse.urlsplit((url or "").strip()).hostname or "").lower()
    except ValueError:
        return False
    return host in SUPPORTED_HOSTS
