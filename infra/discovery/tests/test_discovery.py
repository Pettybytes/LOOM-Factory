"""Deterministic tests for the Source Discovery V1 foundation.

Run:  python3 -m unittest discover -s infra/discovery/tests -v
      (or from repo root)  python3 -m unittest infra.discovery.tests -v
"""

import json
import sys
import unittest
from pathlib import Path

# Ensure the repo's infra/ dir is on the path so `import discovery` resolves.
_REPO_ROOT = Path(__file__).resolve().parents[3]
for _p in (str(_REPO_ROOT / "infra"), str(_REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from discovery.source_brief import SourceBrief, SourceBriefError, SCHEMA_VERSION as SB_SCHEMA  # noqa: E402
from discovery.discovery_record import DiscoveryRecord, DiscoveryRecordError  # noqa: E402
from discovery.identity import (  # noqa: E402
    classify_duplicate,
    duplicate_fingerprint,
    extract_content_id,
    global_fingerprint,
    near_duplicate_key,
    normalize_creator,
    normalize_url,
    title_key,
    youtube_channel_id,
    youtube_video_id,
)
from discovery.qualification import QualificationGate  # noqa: E402
from discovery.store import DiscoveryStore  # noqa: E402
from discovery.connector import AuthRequiredError, DiscoveryRequest  # noqa: E402
from discovery.connectors.youtube import YouTubeConnector  # noqa: E402


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def load_brief(name: str) -> SourceBrief:
    with open(FIXTURES / name, "r", encoding="utf-8") as fh:
        return SourceBrief.from_dict(json.load(fh))


class TestSourceBrief(unittest.TestCase):
    def test_all_fixtures_load(self):
        for name in ("brief_bryan_face_reaction.json", "brief_pets_ranking.json", "brief_wild_moments.json"):
            brief = load_brief(name)
            self.assertTrue(brief.tenant_id)
            self.assertTrue(brief.profile_id)
            self.assertEqual(brief.schema_version, SB_SCHEMA)

    def test_missing_field_rejected(self):
        with self.assertRaises(SourceBriefError):
            SourceBrief(tenant_id="t", profile_id="p", version="1")

    def test_roundtrip(self):
        brief = load_brief("brief_bryan_face_reaction.json")
        d = brief.to_dict()
        again = SourceBrief.from_dict(d)
        self.assertEqual(d, again.to_dict())

    def test_bad_platform_rejected(self):
        b = load_brief("brief_pets_ranking.json").to_dict()
        b["target_platforms"] = []
        with self.assertRaises(SourceBriefError):
            SourceBrief.from_dict(b)


class TestIdentity(unittest.TestCase):
    def test_youtube_video_id_watch(self):
        self.assertEqual(youtube_video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ"), "dQw4w9WgXcQ")

    def test_youtube_video_id_shorts(self):
        self.assertEqual(youtube_video_id("https://www.youtube.com/shorts/dQw4w9WgXcQ"), "dQw4w9WgXcQ")

    def test_youtube_video_id_youtu_be(self):
        self.assertEqual(youtube_video_id("https://youtu.be/dQw4w9WgXcQ?si=abc"), "dQw4w9WgXcQ")

    def test_youtube_video_id_embed(self):
        self.assertEqual(youtube_video_id("https://www.youtube.com/embed/dQw4w9WgXcQ"), "dQw4w9WgXcQ")

    def test_youtube_video_id_none(self):
        self.assertEqual(youtube_video_id("https://www.youtube.com/"), "")

    def test_channel_id(self):
        self.assertEqual(
            youtube_channel_id("https://www.youtube.com/channel/UC_x5XG1OV2P6uZZ5FSM9Ttw"),
            "UC_x5XG1OV2P6uZZ5FSM9Ttw",
        )

    def test_extract_content_id(self):
        self.assertEqual(
            extract_content_id("youtube", "https://www.youtube.com/watch?v=dQw4w9WgXcQ"),
            "dQw4w9WgXcQ",
        )

    def test_normalize_url_strips_tracking(self):
        a = normalize_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ&si=XYZ&utm_source=foo")
        b = normalize_url("https://youtube.com/watch?v=dQw4w9WgXcQ")
        self.assertEqual(a, b)

    def test_normalize_url_strips_fragment_and_trailing_slash(self):
        a = normalize_url("https://www.example.com/Path/?utm_medium=x#frag")
        self.assertEqual(a, "https://example.com/Path")

    def test_normalize_creator(self):
        self.assertEqual(normalize_creator("  Bryan Petty  "), "bryan petty")

    def test_fingerprints_tenant_scoped(self):
        f1 = duplicate_fingerprint("tenantA", "youtube", "abc123")
        f2 = duplicate_fingerprint("tenantB", "youtube", "abc123")
        self.assertNotEqual(f1, f2)

    def test_global_fingerprint_cross_tenant_stable(self):
        g1 = global_fingerprint("youtube", "abc123")
        g2 = global_fingerprint("youtube", "abc123")
        self.assertEqual(g1, g2)

    def test_title_key_normalizes(self):
        self.assertEqual(title_key("  Hello, WORLD!!!  "), "hello world")

    def test_classify_repost(self):
        kind, reason = classify_duplicate(
            "t", "youtube", "abc123",
            seen_global_ids={global_fingerprint("youtube", "abc123")},
        )
        self.assertEqual(kind, "repost")

    def test_classify_near_duplicate(self):
        key = near_duplicate_key("youtube", "Bryan Petty", "Hello World!")
        kind, reason = classify_duplicate(
            "t", "youtube", "newid",
            seen_near_keys={key},
            creator_channel_identity="Bryan Petty",
            title="Hello World!",
        )
        self.assertEqual(kind, "near")

    def test_classify_new(self):
        kind, reason = classify_duplicate("t", "youtube", "abc123")
        self.assertEqual(kind, "new")


class TestDiscoveryRecord(unittest.TestCase):
    def _record(self, **overrides):
        data = {
            "tenant_id": "t",
            "source_brief_version": "1",
            "platform": "youtube",
            "canonical_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "platform_content_id": "dQw4w9WgXcQ",
            "creator_channel_identity": "Bryan Petty",
            "creator_channel_url": "https://www.youtube.com/channel/UCx",
            "title": "Hello World",
            "publication_timestamp": "2026-09-15T00:00:00+00:00",
            "observed_metrics": {"views": 1000},
            "metric_observation_timestamp": "2026-09-16T00:00:00+00:00",
            "metric_history": [],
            "discovery_query": "channel:UCx",
            "discovery_reason": "test",
            "evidence_confidence": 1.0,
            "provenance_rights_evidence": "first-party RSS",
            "duplicate_fingerprint": "fp",
            "global_fingerprint": "gp",
            "processing_eligibility": "manual_ingest_only",
            "qualification_status": "pending",
            "connector_name": "youtube",
            "connector_version": "1.0.0",
        }
        data.update(overrides)
        return DiscoveryRecord(**data)

    def test_valid(self):
        self.assertEqual(self._record().platform, "youtube")

    def test_missing_field(self):
        with self.assertRaises(DiscoveryRecordError):
            DiscoveryRecord(tenant_id="t")


class TestQualification(unittest.TestCase):
    def setUp(self):
        self.gate = QualificationGate()
        self.brief = load_brief("brief_bryan_face_reaction.json")

    def _record(self, **overrides):
        rec = {
            "tenant_id": self.brief.tenant_id,
            "source_brief_version": self.brief.version,
            "platform": "youtube",
            "canonical_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "platform_content_id": "dQw4w9WgXcQ",
            "creator_channel_identity": "Bryan Petty",
            "creator_channel_url": "https://www.youtube.com/channel/UCx",
            "title": "Crazy viral moment",
            "publication_timestamp": "2026-09-15T00:00:00+00:00",
            "observed_metrics": {"views": 1000, "content_type": "video"},
            "metric_observation_timestamp": "2026-09-16T00:00:00+00:00",
            "metric_history": [],
            "discovery_query": "channel:UCx",
            "discovery_reason": "youtube channel feed",
            "evidence_confidence": 1.0,
            "provenance_rights_evidence": "first-party RSS",
            "duplicate_fingerprint": "fp1",
            "global_fingerprint": "gp1",
            "processing_eligibility": "manual_ingest_only",
            "qualification_status": "pending",
            "connector_name": "youtube",
            "connector_version": "1.0.0",
        }
        rec.update(overrides)
        return DiscoveryRecord(**rec)

    def test_manual_review_required(self):
        r = self.gate.evaluate(self._record(), self.brief)
        self.assertEqual(r.status, "manual_review")

    def test_reject_wrong_platform(self):
        r = self.gate.evaluate(self._record(platform="tiktok"), self.brief)
        self.assertEqual(r.status, "rejected")
        self.assertIn("platform", r.rejection_reason)

    def test_reject_blocklisted_creator(self):
        brief = load_brief("brief_pets_ranking.json")
        brief.creator_channel_blocklist = ["Bad Channel"]
        r = self.gate.evaluate(
            self._record(creator_channel_identity="Bad Channel", platform="youtube"),
            brief,
        )
        self.assertEqual(r.status, "rejected")

    def test_reject_duplicate_repost(self):
        from discovery.identity import global_fingerprint as _gfp
        r = self.gate.evaluate(
            self._record(),
            self.brief,
            seen_global_ids={_gfp("youtube", "dQw4w9WgXcQ")},
        )
        self.assertEqual(r.status, "rejected")
        self.assertIn("repost", r.rejection_reason)

    def test_reject_missing_provenance(self):
        brief = load_brief("brief_pets_ranking.json")
        r = self.gate.evaluate(
            self._record(provenance_rights_evidence=""),
            brief,
        )
        self.assertEqual(r.status, "rejected")
        self.assertIn("provenance", r.rejection_reason)

    def test_reject_exclusion(self):
        brief = load_brief("brief_pets_ranking.json")
        r = self.gate.evaluate(
            self._record(title="Graphic animal cruelty footage", discovery_reason="graphic"),
            brief,
        )
        self.assertEqual(r.status, "rejected")


class TestStore(unittest.TestCase):
    def test_upsert_idempotent(self):
        store = DiscoveryStore(":memory:")
        brief = load_brief("brief_pets_ranking.json")
        store.upsert_brief(brief)

        rec = {
            "tenant_id": "t", "source_brief_version": "1", "platform": "youtube",
            "canonical_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "platform_content_id": "dQw4w9WgXcQ",
            "creator_channel_identity": "Bryan Petty",
            "creator_channel_url": "https://www.youtube.com/channel/UCx",
            "title": "Hello", "publication_timestamp": "2026-09-15T00:00:00+00:00",
            "observed_metrics": {"views": 1},
            "metric_observation_timestamp": "2026-09-16T00:00:00+00:00",
            "metric_history": [], "discovery_query": "q", "discovery_reason": "r",
            "evidence_confidence": 1.0, "provenance_rights_evidence": "e",
            "duplicate_fingerprint": "fpX", "global_fingerprint": "gpX",
            "processing_eligibility": "manual_ingest_only",
            "qualification_status": "pending", "connector_name": "youtube",
            "connector_version": "1.0.0",
        }
        r1 = DiscoveryRecord(**rec)
        self.assertEqual(store.upsert_source(r1), "inserted")
        self.assertEqual(store.upsert_source(r1), "updated")
        self.assertEqual(store.count(), 1)
        self.assertTrue(store.contains_fingerprint("fpX"))
        self.assertTrue(store.contains_global("gpX"))
        store.close()


class TestYouTubeConnector(unittest.TestCase):
    def test_keyword_search_requires_credential(self):
        conn = YouTubeConnector()
        brief = load_brief("brief_pets_ranking.json")
        req = DiscoveryRequest(brief=brief, query="cute puppies")
        with self.assertRaises(AuthRequiredError):
            list(conn.discover(req))

    def test_channel_discovery_no_credential_needed(self):
        # channel: queries use RSS (no credential); a network error is NOT an
        # AuthRequiredError.
        conn = YouTubeConnector()
        brief = load_brief("brief_pets_ranking.json")
        req = DiscoveryRequest(brief=brief, query="channel:UCbad")
        # Should raise ConnectorError (network/resolve), not AuthRequiredError.
        from discovery.connector import ConnectorError
        with self.assertRaises(ConnectorError):
            list(conn.discover(req))


if __name__ == "__main__":
    unittest.main(verbosity=2)
