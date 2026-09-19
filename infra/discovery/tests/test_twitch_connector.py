"""Twitch connector tests — offline only.

No network is touched: every test injects a fake transport. Anything that
would require live Twitch credentials is asserted to fail deterministically
with an ``AuthRequiredError`` naming the credential (never its value).

Run:  python3 -m unittest discover -s tests
      (from inside infra/discovery)
"""

import datetime as _dt
import json
import os
import sys
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock

# Make ``discovery`` importable exactly as the other suites do.
_INFRA = Path(__file__).resolve().parents[2]
if str(_INFRA) not in sys.path:
    sys.path.insert(0, str(_INFRA))

from discovery.connector import (  # noqa: E402
    AuthRequiredError,
    ConnectorError,
    DiscoveryRequest,
)
from discovery.connectors import get_connector  # noqa: E402
from discovery.connectors.twitch import (  # noqa: E402
    TwitchConnector,
    sign_eventsub_headers,
    verify_eventsub_signature,
)
from discovery.discovery_record import DiscoveryRecord  # noqa: E402
from discovery.identity import duplicate_fingerprint, global_fingerprint  # noqa: E402
from discovery.source_brief import SourceBrief  # noqa: E402

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"

# Test-only credential placeholders. Never real values.
_TEST_ENV = {
    "LOOM_TWITCH_CLIENT_ID": "test-client-id",
    "LOOM_TWITCH_CLIENT_SECRET": "test-client-secret",
}
_TEST_SECRET = "test-subscription-secret"

_CLIP = {
    "id": "AwkwardHelplessSalamanderSwiftRage",
    "url": "https://clips.twitch.tv/AwkwardHelplessSalamanderSwiftRage",
    "embed_url": "https://clips.twitch.tv/embed?clip=AwkwardHelplessSalamanderSwiftRage",
    "broadcaster_id": "555",
    "broadcaster_name": "bananennanen",
    "creator_id": "999",
    "creator_name": "cliplicious",
    "game_id": "509658",
    "language": "en",
    "title": "the moment everything went wrong",
    "view_count": 12345,
    "created_at": "2026-09-18T21:03:11Z",
    "duration": 22.4,
}


def load_brief(name: str = "brief_wild_moments.json") -> SourceBrief:
    with open(FIXTURES / name, "r", encoding="utf-8") as fh:
        return SourceBrief.from_dict(json.load(fh))


class FakeTransport:
    """Offline transport. Records calls and replays canned JSON."""

    def __init__(self, clips=None, token="app-token", clip_lookup=None):
        self.calls: list[tuple[str, str]] = []
        self._clips = clips if clips is not None else [_CLIP]
        self._clip_lookup = clip_lookup
        self._token = token
        self.token_posts = 0

    def get_json(self, url, headers, timeout):
        self.calls.append(("GET", url))
        if "/clips" in url:
            rows = self._clip_lookup if self._clip_lookup is not None else self._clips
            return {"data": list(rows)}
        return {"data": []}

    def post_form_json(self, url, body, timeout):
        self.token_posts += 1
        self.calls.append(("POST", url))
        return {"access_token": self._token, "expires_in": 3600}


def eventsub_body(clip_id="AwkwardHelplessSalamanderSwiftRage", event_type="channel.clip.created"):
    return json.dumps({
        "subscription": {"type": event_type, "id": "sub-1"},
        "event": {"id": clip_id, "broadcaster_user_id": "555"},
    }).encode()


def _now_iso():
    return _dt.datetime.now(_dt.timezone.utc).isoformat().replace("+00:00", "Z")


class TestTwitchContract(unittest.TestCase):
    def test_declares_official_api_and_no_scraping(self):
        c = TwitchConnector(transport=FakeTransport())
        self.assertEqual(c.name, "twitch")
        self.assertIn("twitch", c.platforms)
        self.assertIn("official_api_only", c.policy_restrictions)
        self.assertIn("no_scraping_workaround", c.policy_restrictions)
        self.assertIn("eventsub_push_over_polling", c.policy_restrictions)
        # Credentials are declared by NAME only.
        self.assertEqual(
            c.required_credentials,
            ("LOOM_TWITCH_CLIENT_ID", "LOOM_TWITCH_CLIENT_SECRET"),
        )

    def test_cost_is_zero(self):
        c = TwitchConnector(transport=FakeTransport())
        self.assertEqual(c.cost_report()["estimated_cost_usd"], 0.0)

    def test_registered_in_registry(self):
        self.assertIsInstance(get_connector("twitch"), TwitchConnector)

    def test_health_reports_missing_credential_names_only(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            health = TwitchConnector(transport=FakeTransport()).health()
        self.assertFalse(health.ok)
        self.assertIn("LOOM_TWITCH_CLIENT_ID", health.detail)
        # No value may leak into health detail.
        self.assertNotIn("test-client-secret", health.detail)


class TestTwitchQueryParsing(unittest.TestCase):
    def setUp(self):
        self.brief = load_brief()

    def test_rejects_unknown_query_form(self):
        c = TwitchConnector(transport=FakeTransport())
        with self.assertRaises(ConnectorError) as ctx:
            list(c.discover(DiscoveryRequest(brief=self.brief, query="just a random string")))
        self.assertEqual(ctx.exception.code, "INVALID_QUERY")

    def test_rejects_empty_target(self):
        c = TwitchConnector(transport=FakeTransport())
        with self.assertRaises(ConnectorError) as ctx:
            list(c.discover(DiscoveryRequest(brief=self.brief, query="game:")))
        self.assertEqual(ctx.exception.code, "INVALID_QUERY")

    def test_missing_credentials_raise_auth_required_by_name(self):
        c = TwitchConnector(transport=FakeTransport())
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(AuthRequiredError) as ctx:
                list(c.discover(DiscoveryRequest(brief=self.brief, query="game:509658")))
        self.assertEqual(ctx.exception.credential_name, "LOOM_TWITCH_CLIENT_ID")


class TestTwitchListing(unittest.TestCase):
    def setUp(self):
        self.brief = load_brief()
        self.env = mock.patch.dict(os.environ, _TEST_ENV, clear=True)
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def test_listing_maps_clip_to_record(self):
        t = FakeTransport()
        c = TwitchConnector(transport=t)
        recs = list(c.discover(DiscoveryRequest(brief=self.brief, query="game:509658")))
        self.assertEqual(len(recs), 1)
        rec = recs[0]

        self.assertIsInstance(rec, DiscoveryRecord)
        self.assertEqual(rec.platform, "twitch")
        self.assertEqual(rec.platform_content_id, _CLIP["id"])
        self.assertEqual(rec.title, _CLIP["title"])
        self.assertEqual(rec.creator_channel_identity, "bananennanen")
        self.assertEqual(rec.creator_channel_url, "https://www.twitch.tv/bananennanen")
        self.assertEqual(rec.connector_name, "twitch")
        # Eligibility stays conservative — discovery never authorizes ingest.
        self.assertEqual(rec.processing_eligibility, "manual_ingest_only")
        self.assertEqual(rec.qualification_status, "pending")

    def test_listing_fingerprints_match_identity_helpers(self):
        c = TwitchConnector(transport=FakeTransport())
        rec = next(iter(c.discover(DiscoveryRequest(brief=self.brief, query="game:509658"))))
        self.assertEqual(
            rec.duplicate_fingerprint,
            duplicate_fingerprint(self.brief.tenant_id, "twitch", _CLIP["id"]),
        )
        self.assertEqual(
            rec.global_fingerprint, global_fingerprint("twitch", _CLIP["id"])
        )

    def test_observed_metrics_carry_only_reported_values(self):
        c = TwitchConnector(transport=FakeTransport())
        rec = next(iter(c.discover(DiscoveryRequest(brief=self.brief, query="game:509658"))))
        self.assertEqual(rec.observed_metrics["content_type"], "clip")
        self.assertEqual(rec.observed_metrics["view_count"], 12345)
        self.assertEqual(rec.observed_metrics["duration_seconds"], 22.4)

    def test_missing_metric_is_absent_not_fabricated(self):
        sparse = dict(_CLIP)
        sparse.pop("view_count")
        sparse.pop("duration")
        c = TwitchConnector(transport=FakeTransport(clips=[sparse]))
        rec = next(iter(c.discover(DiscoveryRequest(brief=self.brief, query="channel:555"))))
        self.assertNotIn("view_count", rec.observed_metrics)
        self.assertNotIn("duration_seconds", rec.observed_metrics)
        self.assertNotIn("view_count", rec.to_dict()["observed_metrics"])

    def test_token_is_cached_across_calls(self):
        t = FakeTransport()
        c = TwitchConnector(transport=t)
        list(c.discover(DiscoveryRequest(brief=self.brief, query="game:509658")))
        list(c.discover(DiscoveryRequest(brief=self.brief, query="game:509658")))
        self.assertEqual(t.token_posts, 1, "app token must be reused, not re-fetched per call")

    def test_no_media_is_ever_downloaded(self):
        t = FakeTransport()
        c = TwitchConnector(transport=t)
        list(c.discover(DiscoveryRequest(brief=self.brief, query="game:509658")))

        # Only these two hosts are legitimate: the OAuth token endpoint and the
        # Helix API. Anything else (clips.twitch.tv, www.twitch.tv, CDN hosts)
        # would mean the connector is fetching media or a clip page.
        allowed_hosts = {"id.twitch.tv", "api.twitch.tv"}
        self.assertTrue(t.calls, "transport should have been used")
        for method, url in t.calls:
            host = urllib.parse.urlparse(url).hostname or ""
            self.assertIn(host, allowed_hosts, f"unexpected host fetched: {host}")

        api_calls = [u for _m, u in t.calls if urllib.parse.urlparse(u).hostname == "api.twitch.tv"]
        self.assertTrue(api_calls, "helix must be the API path")
        for url in api_calls:
            self.assertIn("/helix/clips", url)
            # No media path is ever requested from Helix.
            self.assertNotIn("/videos/", url)
            self.assertNotIn("/download", url)


class TestEventSubSignature(unittest.TestCase):
    def test_valid_signature_verifies(self):
        body = eventsub_body()
        headers = sign_eventsub_headers("msg-1", _now_iso(), body, _TEST_SECRET)
        self.assertTrue(verify_eventsub_signature(headers, body, _TEST_SECRET))

    def test_tampered_body_fails(self):
        body = eventsub_body()
        headers = sign_eventsub_headers("msg-1", _now_iso(), body, _TEST_SECRET)
        self.assertFalse(
            verify_eventsub_signature(headers, body + b" ", _TEST_SECRET)
        )

    def test_wrong_secret_fails(self):
        body = eventsub_body()
        headers = sign_eventsub_headers("msg-1", _now_iso(), body, _TEST_SECRET)
        self.assertFalse(verify_eventsub_signature(headers, body, "other-secret"))

    def test_missing_headers_fail(self):
        self.assertFalse(verify_eventsub_signature({}, eventsub_body(), _TEST_SECRET))

    def test_stale_notification_is_rejected(self):
        body = eventsub_body()
        old = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(hours=2))
        headers = sign_eventsub_headers("msg-1", old.isoformat(), body, _TEST_SECRET)
        self.assertFalse(
            verify_eventsub_signature(headers, body, _TEST_SECRET),
            "replayed notifications outside the freshness window must be rejected",
        )

    def test_empty_secret_never_verifies(self):
        body = eventsub_body()
        headers = sign_eventsub_headers("msg-1", _now_iso(), body, _TEST_SECRET)
        self.assertFalse(verify_eventsub_signature(headers, body, ""))


class TestEventSubIntake(unittest.TestCase):
    def setUp(self):
        self.brief = load_brief()
        self.env = mock.patch.dict(os.environ, _TEST_ENV, clear=True)
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def test_verification_handshake_returns_challenge(self):
        c = TwitchConnector(transport=FakeTransport())
        body = json.dumps({
            "subscription": {"type": "webhook_callback_verification"},
            "challenge": "abc123",
        }).encode()
        self.assertEqual(c.eventsub_verification_response(body), {"challenge": "abc123"})

    def test_non_verification_body_is_not_a_handshake(self):
        c = TwitchConnector(transport=FakeTransport())
        self.assertIsNone(c.eventsub_verification_response(eventsub_body()))

    def test_clip_created_notification_becomes_a_record(self):
        t = FakeTransport()
        c = TwitchConnector(transport=t)
        req = DiscoveryRequest(brief=self.brief, query="channel:555")
        rec = c.record_from_eventsub_notification(req, eventsub_body())

        self.assertIsNotNone(rec)
        self.assertEqual(rec.platform_content_id, _CLIP["id"])
        self.assertIn("eventsub", rec.discovery_reason)
        # Push and listing must agree on identity.
        listed = next(iter(c.discover(DiscoveryRequest(brief=self.brief, query="channel:555"))))
        self.assertEqual(rec.duplicate_fingerprint, listed.duplicate_fingerprint)
        self.assertEqual(rec.global_fingerprint, listed.global_fingerprint)

    def test_unrelated_event_type_is_ignored(self):
        c = TwitchConnector(transport=FakeTransport())
        req = DiscoveryRequest(brief=self.brief, query="channel:555")
        self.assertIsNone(
            c.record_from_eventsub_notification(req, eventsub_body(event_type="stream.online"))
        )

    def test_clip_not_yet_readable_is_retryable_not_fabricated(self):
        c = TwitchConnector(transport=FakeTransport(clip_lookup=[]))
        req = DiscoveryRequest(brief=self.brief, query="channel:555")
        with self.assertRaises(ConnectorError) as ctx:
            c.record_from_eventsub_notification(req, eventsub_body())
        self.assertEqual(ctx.exception.code, "CLIP_NOT_YET_READABLE")
        self.assertTrue(ctx.exception.retryable)

    def test_malformed_body_is_a_deterministic_error(self):
        c = TwitchConnector(transport=FakeTransport())
        req = DiscoveryRequest(brief=self.brief, query="channel:555")
        with self.assertRaises(ConnectorError) as ctx:
            c.record_from_eventsub_notification(req, b"not json")
        self.assertEqual(ctx.exception.code, "BAD_JSON")


if __name__ == "__main__":
    unittest.main()
