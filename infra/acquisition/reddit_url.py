"""Reddit exact-post identity — one canonical post, or a classified refusal.

Layer 1 of the Reddit contract. This module owns IDENTITY ONLY: which post is
this, and is it a single lawful Reddit post? It never touches media.

Supported forms
---------------
  https://www.reddit.com/r/<subreddit>/comments/<post_id>/<slug>/
  https://old.reddit.com/...        https://np.reddit.com/...
  https://redd.it/<post_id>
  https://www.reddit.com/r/<subreddit>/s/<share_code>     (opaque — must resolve)

Share links carry NO identity: the share code is opaque and is never treated as a
post id. A share link must be RESOLVED (strict hop limit, Reddit hosts only) and the
post id is derived from the FINAL canonical destination. Anything else is refused.

The `v.redd.it` media id is NOT the Reddit post identity. They genuinely differ —
post `1l6m106` is media `61i3p09gjr5f1` — so conflating them would break
reconciliation.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit

PLATFORM = "reddit"

HOSTS = ("reddit.com", "www.reddit.com", "old.reddit.com", "np.reddit.com",
         "new.reddit.com", "m.reddit.com", "redd.it", "www.redd.it")

#: Reddit media / manifest hosts. Runtime-proven: every native stream and manifest
#: URL came from `v.redd.it`; posters/thumbnails come from `*.redd.it`.
REDDIT_MEDIA_SUFFIXES = (".redd.it", ".redditmedia.com")
REDDIT_MEDIA_EXACT = frozenset({"redd.it", "redditmedia.com"})

SUBREDDIT_RE = re.compile(r"^[A-Za-z0-9_]{2,21}$")
POST_ID_RE = re.compile(r"^[A-Za-z0-9]{5,10}$")
SHARE_CODE_RE = re.compile(r"^[A-Za-z0-9]{6,20}$")

POST_PATH_RE = re.compile(r"^/r/(?P<sub>[A-Za-z0-9_]{2,21})/comments/(?P<id>[A-Za-z0-9]{5,10})"
                          r"(?:/(?P<slug>[^/]*))?(?:/)?$")
SHARE_PATH_RE = re.compile(r"^/r/(?P<sub>[A-Za-z0-9_]{2,21})/s/(?P<code>[A-Za-z0-9]{6,20})/?$")
SHORT_PATH_RE = re.compile(r"^/(?P<id>[A-Za-z0-9]{5,10})/?$")

#: A path that is explicitly NOT one post.
NON_POST_PREFIXES = (
    "/user/", "/u/", "/search", "/r/all", "/r/popular", "/r/friends",
    "/message", "/settings", "/prefs", "/login", "/register", "/submit",
    "/wiki", "/about", "/gold", "/coins", "/chat",
)
#: Subreddit-level pages that look close to a post but are not one.
SUBREDDIT_VIEW_SEGMENTS = frozenset({
    "top", "new", "hot", "rising", "controversial", "best", "gilded",
    "wiki", "about", "sidebar", "mod", "modqueue", "wiki", "collection",
    "collections", "m", "s",  # `s` handled explicitly as a share link
})

MAX_HOPS = 5

#: Failure codes (evidence-backed; only used with reliable evidence).
REDDIT_URL_UNSUPPORTED = "REDDIT_URL_UNSUPPORTED"
REDDIT_SHARE_LINK_UNRESOLVED = "REDDIT_SHARE_LINK_UNRESOLVED"
REDDIT_POST_ID_MISSING = "REDDIT_POST_ID_MISSING"
SOURCE_ID_DRIFT = "SOURCE_ID_DRIFT"


class RedditUrlError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message


def _reject(code: str, message: str):
    raise RedditUrlError(code, message)


def is_reddit_host(hostname: str) -> bool:
    h = (hostname or "").lower().rstrip(".")
    return h in HOSTS


def is_reddit_media_host(hostname: str) -> bool:
    """The platform-scoped Reddit CDN predicate (suffix match, no lookalikes)."""
    h = (hostname or "").lower().rstrip(".")
    if h in REDDIT_MEDIA_EXACT:
        return True
    return any(h.endswith(s) for s in REDDIT_MEDIA_SUFFIXES)


def is_share_link(url: str) -> bool:
    return bool(SHARE_PATH_RE.match(urlsplit((url or "").strip()).path or ""))


def _split(url: str) -> tuple[str, str]:
    parts = urlsplit((url or "").strip())
    return (parts.hostname or "").lower(), parts.path or ""


def _validate_shell(url: str) -> str:
    if not (url or "").strip():
        _reject(REDDIT_URL_UNSUPPORTED, "no URL supplied")
    parts = urlsplit(url.strip())
    if parts.scheme.lower() not in ("http", "https"):
        _reject(REDDIT_URL_UNSUPPORTED, f"unsupported scheme {parts.scheme!r}")
    if parts.username or parts.password:
        _reject(REDDIT_URL_UNSUPPORTED, "URL credentials are not accepted")
    host = (parts.hostname or "").lower()
    if not host:
        _reject(REDDIT_URL_UNSUPPORTED, "no host in URL")
    if not is_reddit_host(host):
        _reject(REDDIT_URL_UNSUPPORTED, f"{host!r} is not a Reddit host")
    return host


def classify(url: str) -> dict[str, str]:
    """Parse an ALREADY-CANONICAL Reddit URL into a post identity.

    A share link is refused here (`REDDIT_SHARE_LINK_UNRESOLVED`): only `resolve()`
    may turn a share code into identity, and it does so from the final destination.
    """
    host = _validate_shell(url)
    _, path = _split(url)

    lowered = path.lower()
    for prefix in NON_POST_PREFIXES:
        if lowered.startswith(prefix):
            _reject(REDDIT_URL_UNSUPPORTED, f"{path!r} is not a single post")
    if lowered.startswith("/r/") and lowered.count("/") >= 3:
        segs = [s for s in lowered.split("/") if s]
        if len(segs) >= 3 and segs[2] in SUBREDDIT_VIEW_SEGMENTS and segs[2] != "comments":
            if segs[2] == "s":
                _reject(REDDIT_SHARE_LINK_UNRESOLVED,
                        "a share link must be resolved before identity is known")
            _reject(REDDIT_URL_UNSUPPORTED, f"{path!r} is a subreddit listing page")

    if SHARE_PATH_RE.match(path):
        _reject(REDDIT_SHARE_LINK_UNRESOLVED,
                "a share link must be resolved before identity is known")

    m = POST_PATH_RE.match(path)
    if m:
        return {"platform": PLATFORM, "kind": "post", "subreddit": m.group("sub"),
                "post_id": m.group("id"), "canonical_url": canonical_url(m.group("sub"),
                                                                        m.group("id"))}

    if host.endswith("redd.it"):
        s = SHORT_PATH_RE.match(path)
        if s:
            # A short link gives the id but no subreddit: identity is complete
            # enough to resolve, so report it as a resolvable short form.
            return {"platform": PLATFORM, "kind": "short_link", "subreddit": "",
                    "post_id": s.group("id"),
                    "canonical_url": f"https://www.reddit.com/comments/{s.group('id')}/"}
        _reject(REDDIT_URL_UNSUPPORTED, f"{path!r} is not a Reddit post short link")

    if lowered.startswith("/comments/"):
        segs = [s for s in path.split("/") if s]
        if len(segs) >= 2 and POST_ID_RE.match(segs[1]):
            return {"platform": PLATFORM, "kind": "post", "subreddit": "",
                    "post_id": segs[1],
                    "canonical_url": f"https://www.reddit.com/comments/{segs[1]}/"}

    if lowered.startswith("/r/"):
        _reject(REDDIT_URL_UNSUPPORTED, f"{path!r} is a subreddit page, not one post")
    _reject(REDDIT_URL_UNSUPPORTED, f"{path!r} is not a supported Reddit post URL")


def canonical_url(subreddit: str, post_id: str) -> str:
    if subreddit:
        return f"https://www.reddit.com/r/{subreddit}/comments/{post_id}/"
    return f"https://www.reddit.com/comments/{post_id}/"


def strip_tracking(url: str) -> str:
    """Drop query/fragment: tracking and share parameters never change identity."""
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/") + "/", "", ""))


def resolve(url: str, resolver, *, max_hops: int = MAX_HOPS) -> dict[str, str]:
    """Resolve a share/short link to ONE canonical Reddit post.

    ``resolver(url)`` must return ``(status, location_or_none, final_url)`` and is
    injected so the hop/loop/foreign-host rules are testable without the network.
    Bounded hops, Reddit hosts only, and the post id comes from the FINAL URL.
    """
    current = (url or "").strip()
    seen: list[str] = []
    for _hop in range(max_hops + 1):
        if current in seen:
            _reject("REDDIT_REDIRECT_LOOP", "the redirect chain loops")
        seen.append(current)
        _validate_shell(current)                      # Reddit hosts only, every hop
        status, location, final_url = resolver(current)
        if status is None:
            _reject(REDDIT_SHARE_LINK_UNRESOLVED,
                    "the share link could not be resolved to a Reddit post")
        if status == 429:
            _reject("REDDIT_RATE_LIMITED", "Reddit rate-limited the resolution")
        if status in (301, 302, 303, 307, 308):
            if not location:
                _reject(REDDIT_SHARE_LINK_UNRESOLVED, "redirect without a destination")
            _validate_shell(location)                 # foreign destinations refused
            current = location
            continue
        if status != 200:
            _reject(REDDIT_POST_ID_MISSING,
                    f"resolution ended with HTTP {status} and no post identity")
        # Final destination must itself identify one canonical post.
        parsed = classify(final_url or current)
        if parsed.get("post_id") and parsed.get("subreddit"):
            parsed["canonical_url"] = canonical_url(parsed["subreddit"], parsed["post_id"])
        parsed["resolved_from"] = url
        parsed["hops"] = str(len(seen) - 1)
        return parsed
    _reject(REDDIT_SHARE_LINK_UNRESOLVED,
            f"share link exceeded the {max_hops}-hop limit")


def parse(url: str, *, expected_post_id: str = "", resolver=None) -> dict[str, str]:
    """Full identity path: share links resolve, canonical forms parse directly."""
    if is_share_link(url):
        if resolver is None:
            _reject(REDDIT_SHARE_LINK_UNRESOLVED,
                    "a share link needs a resolver; no opaque code is ever an id")
        parsed = resolve(url, resolver)
    else:
        parsed = classify(url)
    post_id = parsed.get("post_id") or ""
    if not post_id:
        _reject(REDDIT_POST_ID_MISSING, "no Reddit post id in the URL")
    if expected_post_id and post_id != expected_post_id:
        _reject(SOURCE_ID_DRIFT,
                f"post id {post_id!r} != expected {expected_post_id!r}")
    return parsed


def fingerprint_for(url: str, *, resolver=None) -> str:
    """Stable identity fingerprint: platform + Reddit post id (tracking-free)."""
    import hashlib
    parsed = parse(url, resolver=resolver)
    raw = f"{PLATFORM}\x1f{parsed['post_id']}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
