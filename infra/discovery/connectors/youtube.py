"""YouTube/search + competitor-channel discovery connector (free RSS path).

Compliant access: YouTube's public RSS feeds (``/feeds/videos.xml?channel_id=``)
require no API key and no scraping workaround — they are a first-party,
supported surface. This connector uses RSS for channel-discovery and
video-list discovery.

``youtube/search`` keyword search (YouTube Data API v3) is DECLARED here but not
wired to a live credential: it requires ``YOUTUBE_DATA_API_V3_KEY`` (name only,
never a value). When that credential is absent, ``discover`` raises
``AuthRequiredError`` deterministically rather than falling back to an unsafe
scrape. The RSS path does not need any credential.

No media is ever downloaded. No publication is authorized.
"""

from __future__ import annotations

import datetime as _dt
import time
import xml.etree.ElementTree as ET
import urllib.parse
import urllib.request
import urllib.error
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
    duplicate_fingerprint,
    global_fingerprint,
    normalize_url,
    youtube_channel_id,
    youtube_video_id,
)

_ATOM = "{http://www.w3.org/2005/Atom}"
_YT = "{http://www.youtube.com/xml/schemas/2015}"
_MEDIA = "{http://search.yahoo.com/mrss/}"

_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"


class YouTubeConnector(Connector):
    """YouTube discovery via first-party RSS (no credential) + declared Data API
    search (credential ``YOUTUBE_DATA_API_V3_KEY``)."""

    name = "youtube"
    version = "1.0.0"
    platforms = ("youtube",)
    # Data API v3 search would need this credential (name only). RSS needs none.
    required_credentials: tuple[str, ...] = ()
    policy_restrictions = (
        "no_download",
        "no_publication",
        "no_scraping_workaround",
        "rss_first_party_only",
    )
    rate_limit = RateLimit(requests_per_window=60, window_seconds=60)

    FEED_URL = "https://www.youtube.com/feeds/videos.xml"

    def __init__(self, timeout: float = 20.0) -> None:
        self._timeout = timeout

    # ------------------------------------------------------------------
    def health(self) -> ConnectorHealth:
        return ConnectorHealth(ok=True, detail="youtube rss (first-party, no credential)")

    def cost_report(self) -> dict[str, Any]:
        return {"connector": self.name, "version": self.version, "estimated_cost_usd": 0.0}

    # ------------------------------------------------------------------
    def discover(self, request: DiscoveryRequest) -> Iterator[DiscoveryRecord]:
        """Dispatch by query shape.

        * ``channel:<channel_id_or_handle_or_url>`` → competitor-channel feed
          discovery (RSS, no credential).
        * anything else → keyword search, which requires
          ``YOUTUBE_DATA_API_V3_KEY`` (Data API v3). Without that credential we
          raise ``AuthRequiredError`` deterministically — never scrape.
        """
        query = (request.query or "").strip()
        if query.startswith("channel:"):
            yield from self._discover_channel(request, query[len("channel:"):].strip())
            return
        # Keyword search: requires Data API v3 credential (name only).
        self.check_credentials(set())  # no RSS credential; search needs a key
        # check_credentials passes for RSS (empty required set) — so explicitly
        # require the search credential for non-channel queries.
        raise AuthRequiredError("YOUTUBE_DATA_API_V3_KEY")

    # ------------------------------------------------------------------
    def _discover_channel(self, request: DiscoveryRequest, channel_ref: str) -> Iterator[DiscoveryRecord]:
        channel_id = self._resolve_channel_id(channel_ref)
        if not channel_id:
            raise ConnectorError(
                "INVALID_CHANNEL",
                f"cannot resolve YouTube channel id from {channel_ref!r}",
                retryable=False,
            )

        params = urllib.parse.urlencode({"channel_id": channel_id})
        url = f"{self.FEED_URL}?{params}"
        root = self._fetch_feed(url)

        entries = list(root.iter(f"{_ATOM}entry"))
        if not entries:
            raise ConnectorError("NO_ENTRIES", f"no videos in feed for {channel_id}", retryable=True)

        feed_title = _child_text(root, f"{_ATOM}title") or channel_id
        for entry in entries:
            record = self._entry_to_record(request, entry, feed_title)
            if record is not None:
                yield record

    # ------------------------------------------------------------------
    def _resolve_channel_id(self, ref: str) -> str:
        ref = (ref or "").strip()
        if not ref:
            return ""
        # Already a UC... id.
        if ref.startswith("UC") and len(ref) == 24:
            return ref
        # A full channel URL.
        if "://" in ref:
            return youtube_channel_id(ref)
        # A handle like @channel.
        if ref.startswith("@"):
            return ref
        # Otherwise assume a channel id (pass through).
        return ref

    # ------------------------------------------------------------------
    def _fetch_feed(self, url: str) -> ET.Element:
        req = urllib.request.Request(url, headers={
            "User-Agent": _UA,
            "Accept": "application/atom+xml,application/xml,text/xml,*/*",
        })
        # Bounded retry: YouTube RSS is intermittently 404/5xx under
        # rate-limiting. Retry transient failures with deterministic backoff.
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
                if e.code < 500 and e.code != 404:
                    raise ConnectorError("FEED_HTTP_ERROR", f"HTTP {e.code} fetching feed",
                                        retryable=False) from e
                if attempt < 2:
                    time.sleep(self.retry_backoff_seconds(attempt))
            except urllib.error.URLError as e:
                last_exc = e
                if attempt < 2:
                    time.sleep(self.retry_backoff_seconds(attempt))
        if isinstance(last_exc, urllib.error.HTTPError):
            raise ConnectorError("FEED_HTTP_ERROR", f"HTTP {last_exc.code} fetching feed",
                                retryable=True) from last_exc
        raise ConnectorError("FEED_NETWORK_ERROR", str(last_exc), retryable=True) from last_exc

    # ------------------------------------------------------------------
    def _entry_to_record(self, request: DiscoveryRequest, entry: ET.Element, feed_title: str) -> DiscoveryRecord | None:
        video_id = _child_text(entry, f"{_YT}videoId")
        if not video_id:
            return None

        channel_id = _child_text(entry, f"{_YT}channelId")
        title = _child_text(entry, f"{_ATOM}title") or ""
        published = _child_text(entry, f"{_ATOM}published") or ""
        updated = _child_text(entry, f"{_ATOM}updated") or published

        # Author / creator identity.
        author_name = ""
        author_uri = ""
        author_el = entry.find(f"{_ATOM}author")
        if author_el is not None:
            author_name = _child_text(author_el, f"{_ATOM}name") or ""
            author_uri = _child_text(author_el, f"{_ATOM}uri") or ""

        # Observed metrics (views) from media:statistics, when present.
        metrics: dict[str, Any] = {"content_type": "video"}
        views = _entry_views(entry)
        if views is not None:
            metrics["views"] = views

        canonical = f"https://www.youtube.com/watch?v={video_id}"
        canonical = normalize_url(canonical)

        observed_at = _dt.datetime.now(_dt.timezone.utc).isoformat()

        return DiscoveryRecord(
            tenant_id=request.brief.tenant_id,
            source_brief_version=request.brief.version,
            profile_id=request.brief.profile_id,
            platform="youtube",
            canonical_url=canonical,
            platform_content_id=video_id,
            creator_channel_identity=author_name or channel_id,
            creator_channel_url=author_uri or (f"https://www.youtube.com/channel/{channel_id}" if channel_id else ""),
            title=title,
            publication_timestamp=published or updated,
            observed_metrics=metrics,
            metric_observation_timestamp=observed_at,
            metric_history=[],
            discovery_query=request.query,
            discovery_reason=(
                f"youtube channel feed '{feed_title}' (channel_id={channel_id})"
            ),
            evidence_confidence=1.0,
            provenance_rights_evidence=(
                f"first-party RSS feed; canonical={canonical}; "
                f"channel={author_name or channel_id}; observed_at={observed_at}"
            ),
            duplicate_fingerprint=duplicate_fingerprint(request.brief.tenant_id, "youtube", video_id),
            global_fingerprint=global_fingerprint("youtube", video_id),
            processing_eligibility="manual_ingest_only",
            qualification_status="pending",
            rejection_reason="",
            connector_name=self.name,
            connector_version=self.version,
        )


def _child_text(parent: ET.Element, tag: str) -> str:
    el = parent.find(tag)
    return (el.text or "").strip() if el is not None and el.text else ""


def _entry_views(entry: ET.Element) -> int | None:
    """Extract view count from media:group/media:community/media:statistics."""
    for stats in entry.iter(f"{_MEDIA}statistics"):
        views = stats.get("views")
        if views is not None:
            try:
                return int(views)
            except ValueError:
                return None
    return None
