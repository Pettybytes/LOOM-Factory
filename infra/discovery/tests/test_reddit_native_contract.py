"""Offline RSS regressions: submission identity, video evidence, and bounds."""

import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
import xml.etree.ElementTree as ET

_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_ROOT / "infra"))

from discovery.connector import DiscoveryRequest
from discovery.connectors.reddit import RedditConnector
from discovery.source_brief import SourceBrief

_ATOM = "{http://www.w3.org/2005/Atom}"
_NATIVE_BODY = '''<table><tr><td>
 <a href="https://www.reddit.com/r/clips/comments/abc123/a_real_event/">
 <img src="https://external-preview.redd.it/poster.png?width=640&amp;s=preview" /></a>
 </td><td> submitted by <a href="https://www.reddit.com/user/creator">/u/creator</a>
 to <a href="https://www.reddit.com/r/clips/">r/clips</a><br/>
 <span><a href="https://v.redd.it/native123">[link]</a></span>
 <span><a href="https://www.reddit.com/r/clips/comments/abc123/a_real_event/">[comments]</a></span>
 </td></tr></table>'''


def entry(post_id="t3_abc123", body=_NATIVE_BODY, link=None):
    root = ET.fromstring('''<entry xmlns="http://www.w3.org/2005/Atom">
      <id/><title>A real event</title><published>2026-09-24T12:00:00Z</published>
      <author><name>/u/creator</name></author><category term="clips" label="r/clips"/>
      <content type="html"/><link/>
    </entry>''')
    root.find(f"{_ATOM}id").text = post_id
    root.find(f"{_ATOM}content").text = body
    root.find(f"{_ATOM}link").set(
        "href", link or f"https://www.reddit.com/r/clips/comments/{post_id.removeprefix('t3_')}/a_real_event/")
    return root


def feed(*entries):
    root = ET.Element(f"{_ATOM}feed")
    root.extend(entries)
    return root


class TestRedditNativeContract(unittest.TestCase):
    def setUp(self):
        fixture = _ROOT / "infra/discovery/fixtures/brief_pets_ranking.json"
        self.brief = SourceBrief.from_dict(json.loads(fixture.read_text()))
        self.conn = RedditConnector()
        self.request = DiscoveryRequest(self.brief, "search:requested intent", limit=2)

    def record(self, rss_entry):
        return self.conn._entry_to_record(self.request, rss_entry, "clips")

    def test_only_submission_ids_become_candidates(self):
        for value in ("t5_community", "t1_comment", "abc123", "t3_", "", "t3_bad/id"):
            with self.subTest(value=value):
                self.assertIsNone(self.record(entry(post_id=value)))

    def test_native_submission_carries_typed_internal_evidence(self):
        rec = self.record(entry())
        self.assertIs(rec.observed_metrics["is_video"], True)
        self.assertEqual(rec.observed_metrics["media_type"], "video")
        self.assertIn("first-party RSS submission link", rec.observed_metrics["media_type_evidence"])
        self.assertEqual(rec.observed_metrics["internal_media_url_references"],
                         ["https://v.redd.it/native123"])
        self.assertEqual(rec.canonical_url, "https://reddit.com/r/clips/comments/abc123/a_real_event")
        self.assertNotIn("source_url", rec.observed_metrics)
        self.assertNotIn("source_url_references", rec.observed_metrics)
        self.assertNotIn("views", rec.observed_metrics)
        self.assertNotIn("likes", rec.observed_metrics)

    def test_images_galleries_articles_and_text_do_not_prove_video(self):
        for url in ("https://i.redd.it/image123.jpg", "https://www.reddit.com/gallery/abc123",
                    "https://publisher.example/article", "https://www.reddit.com/r/clips/comments/abc123/"):
            with self.subTest(url=url):
                rec = self.record(entry(body=f'<a href="{url}">[link]</a>'))
                self.assertIsNot(rec.observed_metrics.get("is_video"), True)
                self.assertNotIn("internal_media_url_references", rec.observed_metrics)

    def test_external_article_reference_is_preserved(self):
        rec = self.record(entry(body='<p><a href="https://publisher.example/article">article</a></p>'))
        self.assertEqual(rec.observed_metrics["source_url_references"],
                         ["https://publisher.example/article"])
        self.assertIsNot(rec.observed_metrics.get("is_video"), True)

    def test_selftext_cannot_promote_embedded_video_link(self):
        body = ('<!-- SC_OFF --><div class="md"><p>Here is '
                '<a href="https://v.redd.it/native123">[link]</a></p></div><!-- SC_ON -->'
                '<a href="https://www.reddit.com/r/clips/comments/abc123/">[link]</a>')
        for selftext in (body, body.replace("<!-- SC_ON -->", "")):
            with self.subTest(selftext=selftext):
                rec = self.record(entry(body=selftext))
                self.assertIsNot(rec.observed_metrics.get("is_video"), True)

    def test_thumbnail_and_ordinary_native_hyperlink_are_not_submission_evidence(self):
        for body in ('<img src="https://v.redd.it/native123"/>',
                     '<a href="https://v.redd.it/native123">another clip</a>'):
            with self.subTest(body=body):
                self.assertIsNot(self.record(entry(body=body)).observed_metrics.get("is_video"), True)

    def test_internal_reference_rejects_spoofed_hosts_and_stream_paths(self):
        for url in ("https://v.redd.it.evil.example/native123", "https://evil.example/v.redd.it/native123",
                    "https://user@v.redd.it/native123", "https://v.redd.it/native123/DASH_720.mp4",
                    "http://v.redd.it/native123", "javascript:https://v.redd.it/native123"):
            with self.subTest(url=url):
                rec = self.record(entry(body=f'<a href="{url}">[link]</a>'))
                self.assertIsNot(rec.observed_metrics.get("is_video"), True)

    def test_rss_html_single_quotes_entities_and_duplicate_links(self):
        body = ("<span><a href='https://v.redd.it/native123/?unused=1&amp;unused2=2'>[link]</a></span>"
                "<span><a href='https://v.redd.it/native123'>[link]</a></span>")
        rec = self.record(entry(body=body))
        self.assertEqual(rec.observed_metrics["internal_media_url_references"],
                         ["https://v.redd.it/native123"])

    def test_canonical_is_post_even_if_atom_link_is_internal_or_wrong(self):
        for url in ("https://v.redd.it/native123", "https://cdn.example/preview.mp4?token=private",
                    "https://www.reddit.com/r/clips/comments/wrongid/",
                    "https://reddit.com.evil.example/r/clips/comments/abc123/"):
            with self.subTest(url=url):
                rec = self.record(entry(link=url))
                self.assertEqual(rec.canonical_url, "https://reddit.com/r/clips/comments/abc123")

    def test_canonical_prefers_matching_post_over_enclosure_and_drops_queries(self):
        item = entry(link="https://v.redd.it/native123")
        ET.SubElement(item, f"{_ATOM}link", {
            "rel": "alternate", "href": "https://www.reddit.com/r/clips/comments/abc123/event/?context=3#anchor"})
        self.assertEqual(self.record(item).canonical_url,
                         "https://reddit.com/r/clips/comments/abc123/event")

    def test_search_filters_communities_and_limits_valid_posts(self):
        root = feed(entry("t5_community"), entry("t3_first1"), entry("t3_second2"), entry("t3_third3"))
        with patch.object(self.conn, "_fetch", return_value=root) as fetch:
            recs = list(self.conn.discover(self.request))
        self.assertEqual([r.platform_content_id for r in recs], ["first1", "second2"])
        params = parse_qs(urlsplit(fetch.call_args.args[0]).query)
        self.assertEqual(params["limit"], ["2"])
        self.assertEqual(params["type"], ["link"])
        self.assertEqual(params["q"], ["requested intent"])

    def test_subreddit_honors_limit_when_feed_returns_more(self):
        req = DiscoveryRequest(self.brief, "subreddit:clips", limit=1)
        with patch.object(self.conn, "_fetch", return_value=feed(entry(), entry("t3_other2"))) as fetch:
            self.assertEqual(len(list(self.conn.discover(req))), 1)
        self.assertEqual(parse_qs(urlsplit(fetch.call_args.args[0]).query)["limit"], ["1"])

    def test_nonpositive_limits_do_not_fetch(self):
        for limit in (0, -1):
            with self.subTest(limit=limit), patch.object(self.conn, "_fetch") as fetch:
                self.assertEqual(list(self.conn.discover(DiscoveryRequest(
                    self.brief, "search:requested intent", limit=limit))), [])
                fetch.assert_not_called()

    def test_connector_builder_preserves_tenant_isolation(self):
        first = self.record(entry())
        data = self.brief.to_dict()
        data.update(tenant_id="another-tenant", profile_id="another-profile")
        second = self.conn._entry_to_record(DiscoveryRequest(
            SourceBrief.from_dict(data), "search:another intent"), entry(), "clips")
        self.assertEqual(second.tenant_id, "another-tenant")
        self.assertEqual(second.profile_id, "another-profile")
        self.assertEqual(second.discovery_query, "search:another intent")
        self.assertNotEqual(first.duplicate_fingerprint, second.duplicate_fingerprint)
        self.assertEqual(first.global_fingerprint, second.global_fingerprint)


if __name__ == "__main__":
    unittest.main()
