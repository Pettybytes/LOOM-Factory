"""Twitch connector — official Helix clip discovery + EventSub push intake.

Compliant access only: Twitch's first-party Helix API (``/helix/clips``) for
clip discovery, and Twitch's official EventSub webhook transport for
``channel.clip.created`` notifications. No scraping, no undocumented internal
endpoints, no auth evasion.

Why both paths exist (and why this is NOT polling):

  * EventSub is push. Twitch notifies LOOM the moment a tracked broadcaster
    creates a clip. LOOM never asks "is there a new clip yet?" on a timer.
  * That notification carries the clip ID but not the full clip payload, so the
    documented pattern is one Helix ``GET /helix/clips?id=<id>`` lookup to
    hydrate it. One call per real event is event-driven, not polling.
  * ``discover()`` (Helix listing) remains for backfill and for bounded
    "top clips for this game/channel" queries where no event exists to push.

Both paths converge on ``_clip_to_record()`` so a clip discovered by push and
the same clip discovered by listing produce byte-identical records.

Credentials are referenced BY NAME ONLY and are never logged:
  * ``LOOM_TWITCH_CLIENT_ID``
  * ``LOOM_TWITCH_CLIENT_SECRET``

No media is downloaded. No publication is authorized.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
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
from ..identity import duplicate_fingerprint, global_fingerprint, normalize_url

# Helix app-access-token endpoint (client_credentials) and the API base.
_OAUTH_TOKEN_URL = "https://id.twitch.tv/oauth2/token"
_HELIX_BASE = "https://api.twitch.tv/helix"

# Twitch's documented app-token bucket is 800 points/minute; a clip listing page
# costs 1 point. Keep a conservative declarative ceiling well under it.
_RATE = RateLimit(requests_per_window=120, window_seconds=60)

# EventSub notifications older than this are rejected as replay attempts.
_MAX_NOTIFICATION_AGE_SECONDS = 600


class TwitchConnector(Connector):
    """Twitch discovery via Helix listing + EventSub push intake.

    Query forms accepted by :meth:`discover`:
      * ``game:<game_id>``         → top clips for a game
      * ``channel:<broadcaster_id>`` → clips for a broadcaster
    """

    name = "twitch"
    version = "1.0.0"
    platforms = ("twitch",)
    required_credentials = ("LOOM_TWITCH_CLIENT_ID", "LOOM_TWITCH_CLIENT_SECRET")
    policy_restrictions = (
        "no_download",
        "no_publication",
        "no_scraping_workaround",
        "official_api_only",
        "eventsub_push_over_polling",
        "credentials_by_name_only",
    )
    rate_limit = _RATE

    def __init__(self, transport: Any = None, timeout: float = 20.0) -> None:
        # ``transport`` is injectable so tests never touch the network. Its
        # presence in production is the default urllib implementation.
        self._transport = transport or _UrllibTransport(timeout=timeout)
        self._timeout = timeout
        self._token_cache: tuple[str, float] = ("", 0.0)

    # -- contract ---------------------------------------------------------

    def health(self) -> ConnectorHealth:
        have = _available_credential_names()
        missing = [c for c in self.required_credentials if c not in have]
        if missing:
            # Names only — never values.
            return ConnectorHealth(
                ok=False,
                detail=f"credentials not configured: {', '.join(missing)} (names only)",
            )
        return ConnectorHealth(ok=True, detail="twitch helix + eventsub (app token)")

    def cost_report(self) -> dict[str, Any]:
        # Helix is free; EventSub webhooks are free.
        return {"connector": self.name, "version": self.version, "estimated_cost_usd": 0.0}

    def discover(self, request: DiscoveryRequest) -> Iterator[DiscoveryRecord]:
        query = (request.query or "").strip()
        if query.startswith("game:"):
            # Human-readable first: 'Just Chatting' -> /helix/games?name= -> id.
            # A numeric id still passes straight through (backwards compatible).
            yield from self._discover_listing(
                request, "game_id", self._resolve_game_id(query[len("game:"):].strip())
            )
            return
        if query.startswith("channel:"):
            # 'kaicenat' -> /helix/users?login= -> broadcaster id.
            yield from self._discover_listing(
                request, "broadcaster_id",
                self._resolve_broadcaster_id(query[len("channel:"):].strip())
            )
            return
        raise ConnectorError(
            "INVALID_QUERY",
            "twitch query must start with 'game:' or 'channel:'",
            retryable=False,
        )

    # -- human-readable value -> numeric Helix id ------------------------

    @staticmethod
    def _is_numeric_id(value: str) -> bool:
        return bool(value) and value.isdigit()

    def _resolve_game_id(self, value: str) -> str:
        """Twitch game NAME -> numeric game id (numeric ids pass through).

        Helix clip listing takes a numeric game_id, so 'game:Just Chatting'
        previously reached Helix as a name and returned HTTP 400. This resolves
        it through the documented /helix/games endpoint first.
        """
        value = (value or "").strip()
        if not value:
            raise ConnectorError("INVALID_QUERY", "empty game", retryable=False)
        if self._is_numeric_id(value):
            return value
        url = f"{_HELIX_BASE}/games?{urllib.parse.urlencode({'name': value})}"
        rows = (self._helix_get(url).get("data") or [])
        if not rows or not isinstance(rows[0], dict) or not rows[0].get("id"):
            raise ConnectorError("UNKNOWN_GAME", f"no Twitch game named {value!r}", retryable=False)
        return str(rows[0]["id"])

    def _resolve_broadcaster_id(self, value: str) -> str:
        """Creator LOGIN -> numeric broadcaster id (numeric ids pass through)."""
        value = (value or "").strip()
        if not value:
            raise ConnectorError("INVALID_QUERY", "empty channel", retryable=False)
        if self._is_numeric_id(value):
            return value
        url = f"{_HELIX_BASE}/users?{urllib.parse.urlencode({'login': value})}"
        rows = (self._helix_get(url).get("data") or [])
        if not rows or not isinstance(rows[0], dict) or not rows[0].get("id"):
            raise ConnectorError("UNKNOWN_CHANNEL", f"no Twitch channel with login {value!r}", retryable=False)
        return str(rows[0]["id"])

    # -- discovery (Helix listing) ----------------------------------------

    def _discover_listing(
        self, request: DiscoveryRequest, param: str, value: str
    ) -> Iterator[DiscoveryRecord]:
        value = (value or "").strip()
        if not value:
            raise ConnectorError("INVALID_QUERY", f"empty {param}", retryable=False)

        limit = max(1, min(int(request.limit or 20), 100))
        params = urllib.parse.urlencode({param: value, "first": str(limit)})
        url = f"{_HELIX_BASE}/clips?{params}"
        payload = self._helix_get(url)

        rows = payload.get("data") or []
        if not isinstance(rows, list):
            raise ConnectorError("MALFORMED_RESPONSE", "helix clips data is not a list",
                                 retryable=False)
        for row in rows:
            if not isinstance(row, dict):
                continue
            rec = self._clip_to_record(
                request,
                row,
                discovery_reason=f"twitch helix clips {param}={value}",
            )
            if rec is not None:
                yield rec

    # -- EventSub push intake ---------------------------------------------

    def eventsub_verification_response(self, raw_body: bytes) -> dict[str, Any] | None:
        """Handle the EventSub webhook_callback_verification handshake.

        Returns the challenge response body, or None when the payload is not a
        verification request (in which case the caller processes it as an event).
        """
        payload = _loads(raw_body)
        if payload.get("subscription", {}).get("type") == "webhook_callback_verification":
            challenge = payload.get("challenge")
            if not isinstance(challenge, str) or not challenge:
                raise ConnectorError("MALFORMED_CHALLENGE", "missing challenge",
                                     retryable=False)
            return {"challenge": challenge}
        return None

    def record_from_eventsub_notification(
        self, request: DiscoveryRequest, raw_body: bytes
    ) -> DiscoveryRecord | None:
        """Convert a verified ``channel.clip.created`` notification into a record.

        Requires one bounded Helix lookup to hydrate the clip (the notification
        carries only the clip ID). Returns None for event types this connector
        does not handle.
        """
        payload = _loads(raw_body)
        sub = payload.get("subscription") or {}
        if sub.get("type") != "channel.clip.created":
            return None

        clip_id = str((payload.get("event") or {}).get("id") or "").strip()
        if not clip_id:
            raise ConnectorError("MALFORMED_EVENT", "clip.created without id",
                                 retryable=False)

        row = self._fetch_clip_by_id(clip_id)
        if row is None:
            # The event is real but the clip is not yet readable by Helix.
            # Surface it deterministically rather than inventing a record.
            raise ConnectorError(
                "CLIP_NOT_YET_READABLE",
                f"clip {clip_id} not returned by helix yet",
                retryable=True,
            )
        return self._clip_to_record(
            request, row, discovery_reason=f"twitch eventsub channel.clip.created {clip_id}"
        )

    def _fetch_clip_by_id(self, clip_id: str) -> dict[str, Any] | None:
        params = urllib.parse.urlencode({"id": clip_id})
        payload = self._helix_get(f"{_HELIX_BASE}/clips?{params}")
        rows = payload.get("data") or []
        if not rows:
            return None
        return rows[0] if isinstance(rows[0], dict) else None

    # -- shared mapping ---------------------------------------------------

    def _clip_to_record(
        self, request: DiscoveryRequest, row: dict[str, Any], discovery_reason: str
    ) -> DiscoveryRecord | None:
        clip_id = str(row.get("id") or "").strip()
        if not clip_id:
            return None

        title = str(row.get("title") or "").strip()
        broadcaster = str(row.get("broadcaster_name") or "").strip()
        broadcaster_id = str(row.get("broadcaster_id") or "").strip()
        creator = str(row.get("creator_name") or "").strip()
        created_at = str(row.get("created_at") or "").strip()
        url = str(row.get("url") or "").strip()
        game_id = str(row.get("game_id") or "").strip()
        duration = row.get("duration")
        views = row.get("view_count")
        language = str(row.get("language") or "").strip()

        observed_at = _dt.datetime.now(_dt.timezone.utc).isoformat()

        # Only real, observed values are recorded. Missing fields stay absent
        # rather than being back-filled with guesses.
        metrics: dict[str, Any] = {"content_type": "clip"}
        if isinstance(views, int):
            metrics["view_count"] = views
        if isinstance(duration, (int, float)):
            metrics["duration_seconds"] = float(duration)
        if game_id:
            metrics["game_id"] = game_id
        if language:
            metrics["language"] = language
        if creator:
            metrics["clip_creator"] = creator

        canonical = normalize_url(url or f"https://clips.twitch.tv/{clip_id}")

        provenance = (
            f"first-party Twitch Helix/EventSub; clip_id={clip_id}; "
            f"broadcaster_id={broadcaster_id or 'unknown'}; observed_at={observed_at}; "
            f"note: metrics are the values Helix returned at observation time"
        )

        return DiscoveryRecord(
            tenant_id=request.brief.tenant_id,
            source_brief_version=request.brief.version,
            profile_id=request.brief.profile_id,
            platform="twitch",
            canonical_url=canonical,
            platform_content_id=clip_id,
            creator_channel_identity=broadcaster,
            creator_channel_url=f"https://www.twitch.tv/{broadcaster}" if broadcaster else "",
            title=title,
            publication_timestamp=created_at,
            observed_metrics=metrics,
            metric_observation_timestamp=observed_at,
            metric_history=[],
            discovery_query=request.query,
            discovery_reason=discovery_reason,
            evidence_confidence=1.0,
            provenance_rights_evidence=provenance,
            duplicate_fingerprint=duplicate_fingerprint(request.brief.tenant_id, "twitch", clip_id),
            global_fingerprint=global_fingerprint("twitch", clip_id),
            processing_eligibility="manual_ingest_only",
            qualification_status="pending",
            rejection_reason="",
            connector_name=self.name,
            connector_version=self.version,
        )

    # -- transport --------------------------------------------------------

    def _helix_get(self, url: str) -> dict[str, Any]:
        token = self._app_access_token()
        client_id = _env("LOOM_TWITCH_CLIENT_ID")
        headers = {
            "Client-Id": client_id,
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }
        return self._transport.get_json(url, headers, self._timeout)

    def _app_access_token(self) -> str:
        """Client-credentials app token, cached until shortly before expiry.

        The cache exists to avoid a token round-trip per call; it is not a
        polling mechanism.
        """
        token, expires_at = self._token_cache
        if token and time.time() < expires_at - 60:
            return token

        client_id = _env("LOOM_TWITCH_CLIENT_ID")
        client_secret = _env("LOOM_TWITCH_CLIENT_SECRET")
        if not client_id or not client_secret:
            raise AuthRequiredError("LOOM_TWITCH_CLIENT_ID")

        body = urllib.parse.urlencode({
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "client_credentials",
        }).encode()
        payload = self._transport.post_form_json(_OAUTH_TOKEN_URL, body, self._timeout)
        token = str(payload.get("access_token") or "").strip()
        if not token:
            raise ConnectorError("TOKEN_MISSING", "oauth response carried no access_token",
                                 retryable=True)
        expires_in = payload.get("expires_in")
        try:
            ttl = float(expires_in) if expires_in is not None else 3600.0
        except (TypeError, ValueError):
            ttl = 3600.0
        self._token_cache = (token, time.time() + ttl)
        return token


# -- module-level helpers ---------------------------------------------------

def verify_eventsub_signature(
    headers: dict[str, str], raw_body: bytes, secret: str
) -> bool:
    """Verify Twitch's EventSub HMAC-SHA256 webhook signature.

    Twitch signs ``message_id + timestamp + body`` with the subscription secret.
    The comparison is constant-time. Never logs the secret or the signature.
    """
    if not secret:
        return False
    lowered = {str(k).lower(): v for k, v in headers.items()}
    message_id = lowered.get("twitch-eventsub-message-id")
    timestamp = lowered.get("twitch-eventsub-message-timestamp")
    signature = lowered.get("twitch-eventsub-message-signature")
    if not (message_id and timestamp and signature):
        return False

    # Reject replayed notifications outside the freshness window.
    if _notification_too_old(timestamp):
        return False

    if not signature.startswith("sha256="):
        return False
    expected = hmac.new(
        secret.encode("utf-8"),
        (message_id + timestamp).encode("utf-8") + raw_body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature[len("sha256="):])


def sign_eventsub_headers(
    message_id: str, timestamp: str, raw_body: bytes, secret: str
) -> dict[str, str]:
    """Produce the Twitch-style signature headers (used by tests and tooling)."""
    digest = hmac.new(
        secret.encode("utf-8"),
        (message_id + timestamp).encode("utf-8") + raw_body,
        hashlib.sha256,
    ).hexdigest()
    return {
        "Twitch-Eventsub-Message-Id": message_id,
        "Twitch-Eventsub-Message-Timestamp": timestamp,
        "Twitch-Eventsub-Message-Signature": f"sha256={digest}",
    }


def _notification_too_old(timestamp: str) -> bool:
    try:
        cleaned = timestamp.replace("Z", "+00:00")
        sent = _dt.datetime.fromisoformat(cleaned)
        if sent.tzinfo is None:
            sent = sent.replace(tzinfo=_dt.timezone.utc)
    except ValueError:
        return True
    age = (_dt.datetime.now(_dt.timezone.utc) - sent).total_seconds()
    return age > _MAX_NOTIFICATION_AGE_SECONDS


def _loads(raw_body: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except Exception as e:  # noqa: BLE001 - deterministic connector error
        raise ConnectorError("BAD_JSON", f"eventsub body is not JSON: {type(e).__name__}",
                             retryable=False) from e
    if not isinstance(payload, dict):
        raise ConnectorError("BAD_JSON", "eventsub body is not an object", retryable=False)
    return payload


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def _available_credential_names() -> set[str]:
    """Names of Twitch credentials present in the environment (values never read)."""
    return {n for n in ("LOOM_TWITCH_CLIENT_ID", "LOOM_TWITCH_CLIENT_SECRET") if _env(n)}


class _UrllibTransport:
    """Default network transport (stdlib only, no new dependency)."""

    def __init__(self, timeout: float = 20.0) -> None:
        self._timeout = timeout

    def get_json(self, url: str, headers: dict[str, str], timeout: float) -> dict[str, Any]:
        req = urllib.request.Request(url, headers=headers, method="GET")
        return self._send(req, timeout)

    def post_form_json(self, url: str, body: bytes, timeout: float) -> dict[str, Any]:
        req = urllib.request.Request(
            url, data=body, method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        return self._send(req, timeout)

    def _send(self, req: urllib.request.Request, timeout: float) -> dict[str, Any]:
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    raw = resp.read().decode("utf-8", "replace")
                parsed = json.loads(raw)
                if not isinstance(parsed, dict):
                    raise ConnectorError("MALFORMED_RESPONSE", "response is not an object",
                                         retryable=False)
                return parsed
            except urllib.error.HTTPError as e:
                last_exc = e
                retryable = e.code == 429 or e.code >= 500
                if retryable and attempt < 2:
                    time.sleep(float(min(60.0, 2.0 ** attempt)))
                    continue
                raise ConnectorError(
                    "TWITCH_HTTP_ERROR", f"HTTP {e.code}", retryable=retryable
                ) from e
            except urllib.error.URLError as e:
                last_exc = e
                if attempt < 2:
                    time.sleep(float(min(60.0, 2.0 ** attempt)))
                    continue
        raise ConnectorError("TWITCH_NETWORK_ERROR", str(last_exc), retryable=True) from last_exc
