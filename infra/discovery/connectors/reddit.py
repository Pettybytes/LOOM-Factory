"""Reddit connector — subreddit + bounded keyword discovery via first-party RSS.

Compliant access: Reddit's official Atom/RSS feeds (``/r/<sub>/.rss`` and
``/search.rss?q=<query>``). No credential, no scraping, no auth evasion. The
public JSON API (``.json``) returns 403 unauthenticated and the OAuth API
(``oauth.reddit.com``) requires an app registration — neither is used.

What RSS provides per entry:
  * post id (``t3_<id>``), canonical URL, subreddit, author, title,
    body HTML (excerpt), published/updated timestamps, embedded source URLs.

What RSS does NOT provide: score / ups / num_comments (those need the JSON or
OAuth API). ``observed_metrics`` carries post/media-type evidence and source
references, but never invents engagement metrics. Native media references stay
internal; the canonical URL always addresses the Reddit post.

No media is ever downloaded. No publication is authorized.
"""

from __future__ import annotations

import datetime as _dt
from html.parser import HTMLParser
from itertools import islice
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any, Iterator

from ..connector import (
    AuthRequiredError,
    Connector,
    ConnectorError,
    ConnectorHealth,
    DiscoveryRequest,
    RateLimit,
)
from ..discovery_record import DiscoveryRecord
from ..identity import (
    crosspost_key,
    duplicate_fingerprint,
    global_fingerprint,
    normalize_url,
)

_ATOM = "{http://www.w3.org/2005/Atom}"

_UA = "LOOM-discovery/1.0 (Bryan's LOOM content factory; first-party RSS only)"

# Reddit's rate limit for unauthenticated RSS is not published; keep a
# conservative declarative ceiling.
_RATE = RateLimit(requests_per_window=30, window_seconds=60)


class RedditConnector(Connector):
    """Reddit discovery via first-party RSS (no credential, no scraping).

    Query forms:
      * ``subreddit:<name>`` → ``/r/<name>/.rss``
      * ``search:<query>`` → ``/search.rss?q=<query>&restrict_sr=1``
    """

    name = "reddit"
    version = "1.0.0"
    platforms = ("reddit",)
    required_credentials: tuple[str, ...] = ()
    policy_restrictions = (
        "no_download",
        "no_publication",
        "no_scraping_workaround",
        "rss_first_party_only",
        "no_auth_evasion",
    )
    rate_limit = _RATE

    RSS_BASE = "https://www.reddit.com"

    def __init__(self, timeout: float = 20.0) -> None:
        self._timeout = timeout

    def health(self) -> ConnectorHealth:
        return ConnectorHealth(ok=True, detail="reddit rss (first-party, no credential)")

    def cost_report(self) -> dict[str, Any]:
        return {"connector": self.name, "version": self.version, "estimated_cost_usd": 0.0}

    def discover(self, request: DiscoveryRequest) -> Iterator[DiscoveryRecord]:
        limit = max(0, int(request.limit))
        if not limit:
            return
        query = (request.query or "").strip()
        if query.startswith("subreddit:"):
            yield from islice(self._discover_subreddit(
                request, query[len("subreddit:"):].strip()), limit)
            return
        if query.startswith("search:"):
            yield from islice(self._discover_search(
                request, query[len("search:"):].strip()), limit)
            return
        raise ConnectorError(
            "INVALID_QUERY",
            "reddit query must start with 'subreddit:' or 'search:'",
            retryable=False,
        )

    # ------------------------------------------------------------------
    def _discover_subreddit(self, request: DiscoveryRequest, sub: str) -> Iterator[DiscoveryRecord]:
        sub = (sub or "").strip().lstrip("r/").strip("/")
        if not sub:
            raise ConnectorError("INVALID_SUBREDDIT", "empty subreddit name", retryable=False)
        params = urllib.parse.urlencode({"limit": min(int(request.limit), 100)})
        url = f"{self.RSS_BASE}/r/{sub}/.rss?{params}"
        root = self._fetch(url)
        entries = list(root.iter(f"{_ATOM}entry"))
        if not entries:
            raise ConnectorError("NO_ENTRIES", f"no posts in r/{sub}", retryable=True)
        for entry in entries:
            rec = self._entry_to_record(request, entry, sub)
            if rec is not None:
                yield rec

    def _discover_search(self, request: DiscoveryRequest, query: str) -> Iterator[DiscoveryRecord]:
        query = (query or "").strip()
        if not query:
            raise ConnectorError("INVALID_QUERY", "empty search query", retryable=False)
        params = urllib.parse.urlencode({
            "q": query, "restrict_sr": "1", "type": "link",
            "limit": min(int(request.limit), 100),
        })
        url = f"{self.RSS_BASE}/search.rss?{params}"
        root = self._fetch(url)
        entries = list(root.iter(f"{_ATOM}entry"))
        for entry in entries:
            rec = self._entry_to_record(request, entry, subreddit="")
            if rec is not None:
                yield rec

    # ------------------------------------------------------------------
    def _fetch(self, url: str) -> ET.Element:
        req = urllib.request.Request(url, headers={
            "User-Agent": _UA,
            "Accept": "application/atom+xml,application/xml,text/xml,*/*",
        })
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                    raw = resp.read().decode("utf-8", "replace")
                try:
                    return ET.fromstring(raw)
                except ET.ParseError as e:
                    raise ConnectorError("FEED_PARSE_ERROR", str(e), retryable=False) from e
            except urllib.error.HTTPError as e:
                last_exc = e
                # 429 rate limit and 5xx are retryable; 403/404 are not.
                if e.code == 429 or e.code >= 500:
                    if attempt < 2:
                        time.sleep(self.retry_backoff_seconds(attempt))
                        continue
                raise ConnectorError(
                    "FEED_HTTP_ERROR", f"HTTP {e.code} fetching feed", retryable=(e.code in (429,) or e.code >= 500)
                ) from e
            except urllib.error.URLError as e:
                last_exc = e
                if attempt < 2:
                    time.sleep(self.retry_backoff_seconds(attempt))
                    continue
        raise ConnectorError("FEED_NETWORK_ERROR", str(last_exc), retryable=True) from last_exc

    # ------------------------------------------------------------------
    def _entry_to_record(self, request: DiscoveryRequest, entry: ET.Element, subreddit: str) -> DiscoveryRecord | None:
        post_id = _child_text(entry, f"{_ATOM}id")
        # Search feeds can include t5_ communities (and other non-post things).
        # Only a Reddit submission ID establishes a source candidate.
        if not re.fullmatch(r"t3_[a-z0-9]+", post_id):
            return None
        post_id = post_id[3:]

        title = _child_text(entry, f"{_ATOM}title") or ""
        published = _child_text(entry, f"{_ATOM}published") or ""
        updated = _child_text(entry, f"{_ATOM}updated") or published

        author = _author_name(entry)

        # Subreddit: prefer the category term, else the passed sub.
        cat_term = _category_term(entry)
        sub = cat_term or subreddit or ""

        # Body excerpt (strip HTML, bounded).
        body = _body_excerpt(entry)

        # Source URL references embedded in the body (external links).
        source_urls = _source_urls(entry)
        native_urls = _native_video_urls(entry)

        observed_at = _dt.datetime.now(_dt.timezone.utc).isoformat()
        metrics: dict[str, Any] = {"content_type": "post"}
        if source_urls:
            metrics["source_url_references"] = source_urls
        if native_urls:
            metrics.update({
                "is_video": True,
                "media_type": "video",
                "media_type_evidence": "first-party RSS submission link to v.redd.it",
                "internal_media_url_references": native_urls,
            })

        canonical = _canonical_post_url(entry, post_id, sub)

        provenance = (
            f"first-party Reddit RSS; subreddit=r/{sub}; "
            f"post_id=t3_{post_id}; observed_at={observed_at}; "
            f"note: RSS omits score/ups/comments (JSON/OAuth API required — not used)"
        )

        return DiscoveryRecord(
            tenant_id=request.brief.tenant_id,
            source_brief_version=request.brief.version,
            profile_id=request.brief.profile_id,
            platform="reddit",
            canonical_url=canonical,
            platform_content_id=post_id,
            creator_channel_identity=author,
            creator_channel_url=f"https://www.reddit.com/user/{author.lstrip('/u/')}" if author else "",
            title=title,
            publication_timestamp=published or updated,
            observed_metrics=metrics,
            metric_observation_timestamp=observed_at,
            metric_history=[],
            discovery_query=request.query,
            discovery_reason=f"reddit subreddit r/{sub}" if subreddit else "reddit search",
            evidence_confidence=1.0,
            provenance_rights_evidence=provenance,
            duplicate_fingerprint=duplicate_fingerprint(request.brief.tenant_id, "reddit", post_id),
            global_fingerprint=global_fingerprint("reddit", post_id),
            processing_eligibility="manual_ingest_only",
            qualification_status="pending",
            rejection_reason="",
            connector_name=self.name,
            connector_version=self.version,
            body_excerpt=body,
        )


def _child_text(parent: ET.Element, tag: str) -> str:
    el = parent.find(tag)
    return (el.text or "").strip() if el is not None and el.text else ""


def _author_name(entry: ET.Element) -> str:
    author_el = entry.find(f"{_ATOM}author")
    if author_el is not None:
        name = _child_text(author_el, f"{_ATOM}name") or ""
        return name.lstrip("/u/").lstrip("u/")
    return ""


def _entry_link(entry: ET.Element) -> str:
    for el in entry.iter(f"{_ATOM}link"):
        href = el.get("href")
        if href:
            return href
    return ""


def _canonical_post_url(entry: ET.Element, post_id: str, sub: str) -> str:
    """Keep malformed/enclosure links out of the operator's Open Source URL."""
    for link in entry.iter(f"{_ATOM}link"):
        try:
            parts = urllib.parse.urlsplit(link.get("href") or "")
        except ValueError:
            continue
        if (parts.scheme == "https" and
                parts.netloc.lower() in {"reddit.com", "www.reddit.com", "old.reddit.com"} and
                re.match(rf"^/(?:r/[A-Za-z0-9_]+/)?comments/{post_id}(?:/|$)", parts.path)):
            # Reddit query/fragment values do not establish the post identity.
            return normalize_url(urllib.parse.urlunsplit(
                (parts.scheme, parts.netloc, parts.path, "", "")))
    prefix = f"r/{sub}/" if re.fullmatch(r"[A-Za-z0-9_]+", sub) else ""
    return normalize_url(f"https://www.reddit.com/{prefix}comments/{post_id}/")


class _AnchorParser(HTMLParser):
    """Read RSS HTML anchors without treating thumbnail URLs as source media."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self.href = ""
        self.label: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.href = dict(attrs).get("href") or ""
            self.label = []

    def handle_data(self, data):
        if self.href:
            self.label.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self.href:
            self.links.append((self.href, "".join(self.label).strip()))
            self.href = ""
            self.label = []


def _native_video_urls(entry: ET.Element) -> list[str]:
    """Only Reddit's submission [link], not a link in selftext, proves video."""
    content = _child_text(entry, f"{_ATOM}content")
    # Self-posts may quote/link a video but are not themselves native videos.
    content = re.sub(
        r"<!--\s*SC_OFF\s*-->.*?(?:<!--\s*SC_ON\s*-->|$)", "", content, flags=re.S)
    parser = _AnchorParser()
    parser.feed(content)
    urls = []
    for href, label in parser.links:
        try:
            parts = urllib.parse.urlsplit(href)
        except ValueError:
            continue
        if (label == "[link]" and parts.scheme == "https" and
                parts.netloc.lower() == "v.redd.it" and
                re.fullmatch(r"/[A-Za-z0-9]+/?", parts.path)):
            url = urllib.parse.urlunsplit(("https", "v.redd.it", parts.path.rstrip("/"), "", ""))
            if url not in urls:
                urls.append(url)
    return urls


def _category_term(entry: ET.Element) -> str:
    for el in entry.iter(f"{_ATOM}category"):
        term = el.get("term")
        if term:
            return term
    return ""


def _body_excerpt(entry: ET.Element, max_chars: int = 500) -> str:
    content_el = entry.find(f"{_ATOM}content")
    if content_el is None or not content_el.text:
        return ""
    text = content_el.text
    # Strip the SC_OFF marker and HTML tags.
    text = text.replace("<!-- SC_OFF -->", "").replace("<!-- SC_ON -->", "")
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_chars]


def _source_urls(entry: ET.Element, max_urls: int = 5) -> list[str]:
    """External URLs embedded in the post body (media/source references)."""
    content_el = entry.find(f"{_ATOM}content")
    if content_el is None or not content_el.text:
        return []
    hrefs = re.findall(r'href="([^"]+)"', content_el.text)
    # Skip Reddit-internal links.
    urls = []
    for h in hrefs:
        if "reddit.com" in h or "redd.it" in h:
            continue
        if h not in urls:
            urls.append(h)
        if len(urls) >= max_urls:
            break
    return urls
