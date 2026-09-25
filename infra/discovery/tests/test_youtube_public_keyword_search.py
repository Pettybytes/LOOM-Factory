"""Offline contract tests for explicit, bounded public keyword discovery."""

import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_ROOT / "infra"))

from discovery.connector import AuthRequiredError, ConnectorError, DiscoveryRequest
from discovery.connectors.youtube import YouTubeConnector
from discovery.source_brief import SourceBrief


def video(video_id="AbCdEfGhI12", **values):
    return {"_type": "url", "id": video_id, "title": "A concrete event",
            "channel": "Example creator", "channel_id": "UC" + "a" * 22,
            "channel_url": "https://www.youtube.com/channel/UC" + "a" * 22,
            "url": "https://www.youtube.com/watch?v=" + video_id,
            "view_count": 200_000, "duration": 23, **values}


def completed(entries, **overrides):
    return SimpleNamespace(returncode=0, stdout=json.dumps({"entries": entries}),
                           stderr="", **overrides)


class TestYouTubePublicKeywordSearch(unittest.TestCase):
    def setUp(self):
        fixture = _ROOT / "infra/discovery/fixtures/brief_pets_ranking.json"
        self.brief = SourceBrief.from_dict(json.loads(fixture.read_text()))
        self.request = DiscoveryRequest(self.brief, "ytsearch: tenant intent", limit=20)
        self.conn = YouTubeConnector(timeout=10)

    def test_explicit_search_uses_bounded_metadata_only_process(self):
        with patch("discovery.connectors.youtube.subprocess.run", return_value=completed([video()])) as run:
            records = list(self.conn.discover(self.request))
        self.assertEqual(len(records), 1)
        args = run.call_args.args[0]
        for flag in ("--ignore-config", "--no-cache-dir", "--skip-download", "--simulate", "--flat-playlist"):
            self.assertIn(flag, args)
        self.assertEqual(args[-2:], ["--", "ytsearch20:tenant intent"])
        self.assertEqual(args[args.index("--playlist-end") + 1], "20")
        self.assertEqual(run.call_args.kwargs["timeout"], 30)
        self.assertFalse(run.call_args.kwargs.get("shell", False))
        self.assertNotIn("--cookies-from-browser", args)

    def test_each_result_uses_existing_builder_and_request_scope(self):
        with patch("discovery.connectors.youtube.subprocess.run", return_value=completed([video()])):
            with patch.object(self.conn, "metadata_to_record", wraps=self.conn.metadata_to_record) as build:
                result = list(self.conn.discover(self.request))[0]
        self.assertIs(build.call_args.args[0], self.request)
        self.assertEqual(result.tenant_id, self.brief.tenant_id)
        self.assertEqual(result.profile_id, self.brief.profile_id)
        self.assertEqual(result.discovery_query, self.request.query)
        self.assertEqual(result.canonical_url, "https://youtube.com/watch?v=AbCdEfGhI12")
        self.assertEqual(result.observed_metrics["views"], 200_000)
        self.assertEqual(result.observed_metrics["duration"], 23)

    def test_missing_engagement_counts_remain_unknown_not_zero(self):
        meta = video(view_count=None, like_count=None, comment_count=None, duration=None)
        with patch("discovery.connectors.youtube.subprocess.run", return_value=completed([meta])):
            result = list(self.conn.discover(self.request))[0]
        for key in ("views", "likes", "comments", "duration"):
            self.assertNotIn(key, result.observed_metrics)

    def test_observed_zero_is_preserved(self):
        with patch("discovery.connectors.youtube.subprocess.run", return_value=completed([
                video(view_count=0, like_count=0, comment_count=0)])):
            result = list(self.conn.discover(self.request))[0]
        self.assertEqual({k: result.observed_metrics[k] for k in ("views", "likes", "comments")},
                         {"views": 0, "likes": 0, "comments": 0})

    def test_response_larger_than_requested_is_not_overemitted(self):
        self.request.limit = 1
        with patch("discovery.connectors.youtube.subprocess.run", return_value=completed([
                video(), video("XyZ98765432")])):
            self.assertEqual(len(list(self.conn.discover(self.request))), 1)

    def test_nonvideo_malformed_and_duplicate_search_entries_are_skipped(self):
        entries = [None, "malformed", {"id": "invalid_id_too_long"},
                   video(_type="playlist"), video(), video(), video("XyZ98765432")]
        with patch("discovery.connectors.youtube.subprocess.run", return_value=completed(entries)):
            result = list(self.conn.discover(self.request))
        self.assertEqual([r.platform_content_id for r in result], ["AbCdEfGhI12", "XyZ98765432"])

    def test_no_media_urls_are_retained_in_normalized_records(self):
        meta = video(url="https://internal.example/stream?token=PRIVATE_MARKER",
                     formats=[{"url": "https://internal.example/video.m3u8?token=PRIVATE_MARKER"}])
        with patch("discovery.connectors.youtube.subprocess.run", return_value=completed([meta])):
            result = list(self.conn.discover(self.request))[0]
        self.assertNotIn("PRIVATE_MARKER", json.dumps(result.to_dict()))
        self.assertIn("youtube.com/watch?v=", result.canonical_url)

    def test_query_is_one_argument_and_cannot_enable_shell_or_downloader_options(self):
        query = 'quoted intent $(touch forbidden); --exec "echo forbidden"'
        self.request.query = "ytsearch: " + query
        with patch("discovery.connectors.youtube.subprocess.run", return_value=completed([])) as run:
            self.assertEqual(list(self.conn.discover(self.request)), [])
        self.assertEqual(run.call_args.args[0][-1], "ytsearch20:" + query)
        self.assertNotIn("--exec", run.call_args.args[0])
        self.assertFalse(run.call_args.kwargs.get("shell", False))

    def test_empty_query_and_nonpositive_limits_do_not_launch(self):
        with patch("discovery.connectors.youtube.subprocess.run") as run:
            with self.assertRaises(ConnectorError) as caught:
                list(self.conn.discover(DiscoveryRequest(self.brief, "ytsearch: ")))
            self.assertEqual(caught.exception.code, "INVALID_QUERY")
            for limit in (0, -1):
                self.assertEqual(list(self.conn.discover(DiscoveryRequest(
                    self.brief, "ytsearch: intent", limit=limit))), [])
            run.assert_not_called()

    def test_missing_dependency_and_timeout_are_typed_without_process_payload(self):
        for error, code in ((FileNotFoundError("PRIVATE_MARKER"), "SEARCH_DEPENDENCY_MISSING"),
                            (subprocess.TimeoutExpired("PRIVATE_MARKER", 30, output="PRIVATE_MARKER"), "SEARCH_TIMEOUT"),
                            (OSError("PRIVATE_MARKER"), "SEARCH_PROCESS_ERROR")):
            with self.subTest(code=code), patch("discovery.connectors.youtube.subprocess.run", side_effect=error):
                with self.assertRaises(ConnectorError) as caught:
                    list(self.conn.discover(self.request))
                self.assertEqual(caught.exception.code, code)
                self.assertNotIn("PRIVATE_MARKER", str(caught.exception))
                self.assertTrue(caught.exception.__suppress_context__)

    def test_provider_failures_are_typed_and_never_echo_provider_output(self):
        for message, code, retryable in (("HTTP 429 PRIVATE_MARKER", "SEARCH_RATE_LIMITED", True),
                                         ("Sign in PRIVATE_MARKER", "SEARCH_AUTH_REQUIRED", False),
                                         ("unknown PRIVATE_MARKER", "SEARCH_FAILED", False)):
            result = SimpleNamespace(returncode=1, stderr=message, stdout="PRIVATE_MARKER")
            with self.subTest(code=code), patch("discovery.connectors.youtube.subprocess.run", return_value=result):
                with self.assertRaises(ConnectorError) as caught:
                    list(self.conn.discover(self.request))
                self.assertEqual(caught.exception.code, code)
                self.assertEqual(caught.exception.retryable, retryable)
                self.assertNotIn("PRIVATE_MARKER", str(caught.exception))

    def test_empty_inventory_differs_from_malformed_provider_response(self):
        with patch("discovery.connectors.youtube.subprocess.run", return_value=completed([])):
            self.assertEqual(list(self.conn.discover(self.request)), [])
        for payload, code in (("PRIVATE_MARKER", "SEARCH_PARSE_ERROR"),
                              (json.dumps({"entries": None}), "SEARCH_SCHEMA_ERROR")):
            result = SimpleNamespace(returncode=0, stdout=payload, stderr="")
            with self.subTest(code=code), patch("discovery.connectors.youtube.subprocess.run", return_value=result):
                with self.assertRaises(ConnectorError) as caught:
                    list(self.conn.discover(self.request))
                self.assertEqual(caught.exception.code, code)
                self.assertNotIn("PRIVATE_MARKER", str(caught.exception))

    def test_legacy_rss_and_unprefixed_keyword_dispatch_are_unchanged(self):
        req = DiscoveryRequest(self.brief, "channel:UC" + "a" * 22)
        with patch.object(self.conn, "_discover_channel", return_value=iter([])) as rss:
            with patch("discovery.connectors.youtube.subprocess.run") as run:
                self.assertEqual(list(self.conn.discover(req)), [])
                rss.assert_called_once_with(req, "UC" + "a" * 22)
                with self.assertRaises(AuthRequiredError):
                    list(self.conn.discover(DiscoveryRequest(self.brief, "plain keyword query")))
                run.assert_not_called()

    def test_search_tenant_isolation_uses_existing_fingerprints(self):
        other_data = self.brief.to_dict()
        other_data.update(tenant_id="other-tenant", profile_id="other-profile")
        other = DiscoveryRequest(SourceBrief.from_dict(other_data), "ytsearch: other intent", limit=20)
        with patch("discovery.connectors.youtube.subprocess.run", return_value=completed([video()])):
            first = list(self.conn.discover(self.request))[0]
            second = list(self.conn.discover(other))[0]
        self.assertNotEqual(first.duplicate_fingerprint, second.duplicate_fingerprint)
        self.assertEqual(first.global_fingerprint, second.global_fingerprint)
        self.assertEqual(second.profile_id, "other-profile")
        self.assertEqual(second.discovery_query, "ytsearch: other intent")


if __name__ == "__main__":
    unittest.main()
