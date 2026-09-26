"""Manual Intake engagement-metadata injection (2026-09-26).

Defect: an operator-supplied source reached #clip-review with
Views/Likes/Comments/age = Unavailable and Market 0/100, even though the platform
evidence was obtainable for free through the extractor path LOOM already runs.

Contract proven here, deterministically and offline:
  * the ingress captures provider engagement fields and maps them onto LOOM's
    CANONICAL metric keys (per platform), so the existing market layer reads them;
  * captured evidence is durable on the submission row AND in the candidate lineage;
  * a field the platform does not expose stays UNAVAILABLE — never a zero;
  * an observed zero stays a zero (it is a measurement);
  * no market score is presented as a measured 0/100 when no evidence was observed;
  * human arbitration is untouched: nothing here gates, filters or rejects.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

_REPO = Path(__file__).resolve().parents[3]
for _p in (str(_REPO / "infra" / "discord-review"), str(_REPO / "infra"),
           str(_REPO / "infra" / "acquisition")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import manual_media_intake as mmi  # noqa: E402
import manual_intake_intelligence as mii  # noqa: E402
import source_review  # noqa: E402
from content_intelligence.market_demand import assess_market_demand  # noqa: E402

# Provider-generic metadata as yt-dlp actually returns it (real observed shapes).
TIKTOK_META = {"id": "7667225763720023327", "uploader": "midflightclips",
               "timestamp": 1785165122, "view_count": 771600, "like_count": 13400,
               "comment_count": 173, "repost_count": 2632, "save_count": 5513}
INSTAGRAM_META = {"id": "DdQE_RUS0GJ", "timestamp": 1789353495,
                  "like_count": 18914, "comment_count": 1149}
KICK_META = {"id": "clip_01M30", "timestamp": 1789949025, "view_count": 2429,
             "like_count": 0}
REDDIT_META = {"id": "1l6m106", "timestamp": 1749414490.0, "like_count": 128,
               "comment_count": 16}


def _probe_module(meta):
    """Stub for acquisition.ytdlp_adapter: the EXISTING metadata probe path."""
    return mock.Mock(build_probe_command=lambda url, **kw: ["yt-dlp", "--", url],
                     run_ytdlp_command=lambda cmd, **kw: __import__("json").dumps(meta))


def _capture(meta, url="https://x/1", platform="tiktok"):
    with mock.patch.object(mmi, "_acq", lambda name: _probe_module(meta)):
        return mmi.fetch_source_metadata(url, platform)


class TestIngressCapture(unittest.TestCase):
    def test_tiktok_fields_map_to_canonical_keys_the_market_layer_reads(self):
        captured = _capture(TIKTOK_META)
        self.assertEqual(captured["metrics"],
                         {"views": 771600, "likes": 13400, "comments": 173,
                          "shares": 2632, "saves": 5513})
        self.assertEqual(captured["publication_timestamp"], "2026-07-27T15:12:02+00:00")
        self.assertEqual(captured["available"], ["comments", "likes", "saves", "shares", "views"])
        self.assertEqual(captured["unavailable"], [])

    def test_instagram_exposes_no_views_so_views_stays_absent_not_zero(self):
        captured = _capture(INSTAGRAM_META, platform="instagram")
        self.assertNotIn("views", captured["metrics"])
        self.assertEqual(captured["metrics"]["likes"], 18914)
        self.assertIn("views", captured["unavailable"])

    def test_observed_zero_is_carried_as_a_measurement(self):
        captured = _capture(KICK_META, platform="kick")
        self.assertEqual(captured["metrics"]["likes"], 0)
        self.assertIn("likes", captured["available"])
        self.assertNotIn("likes", captured["unavailable"])

    def test_reddit_uses_native_score_and_never_invents_views(self):
        captured = _capture(REDDIT_META, platform="reddit")
        # Reddit's market policy reads `score`/`num_comments`, so those are the keys
        # the capture must write — the same numbers must not be dropped by key name.
        self.assertEqual(captured["metrics"]["score"], 128)
        self.assertEqual(captured["metrics"]["num_comments"], 16)
        self.assertEqual(captured["available"], ["comments", "likes"])
        self.assertIn("views", captured["unsupported"])
        self.assertNotIn("views", captured["metrics"])

    def test_probe_failure_is_never_fatal_and_never_fabricates(self):
        with mock.patch.object(mmi, "_acq", lambda name: (_ for _ in ()).throw(RuntimeError("boom"))):
            captured = mmi.fetch_source_metadata("https://x/1", "tiktok")
        self.assertEqual(captured["metrics"], {})
        self.assertEqual(captured["publication_timestamp"], None)
        self.assertEqual(captured["provenance"], "UNAVAILABLE VIA CURRENT PATH")

    def test_extractor_returning_no_item_is_treated_as_unavailable(self):
        captured = _capture({"entries": []}, platform="tiktok")
        self.assertEqual(captured["metrics"], {})


def _submission(media_reference=None):
    return {"id": "sub-1", "tenant_id": "dreemzy", "platform": "tiktok",
            "source_url": "https://www.tiktok.com/@midflightclips/video/7667225763720023327",
            "platform_content_id": "7667225763720023327", "duplicate_fingerprint": "b" * 64,
            "submitter": "passportpetty",
            "media_reference": media_reference if media_reference is not None
            else {"url": "https://cdn/x.mp4"}}


PROBE = {"duration_seconds": 12.03, "width": 720, "height": 1280, "sha256": "c" * 64}


class TestRecordCarriesEvidence(unittest.TestCase):
    def test_measured_metrics_and_timestamp_reach_the_discovery_record(self):
        brief = mii.manual_brief("dreemzy")
        captured = _capture(TIKTOK_META)
        rec = mii.build_record(_submission(), PROBE, brief=brief,
                               media_url="https://cdn/x.mp4", source_metadata=captured)
        self.assertEqual(rec.observed_metrics["views"], 771600)
        self.assertEqual(rec.observed_metrics["likes"], 13400)
        self.assertEqual(rec.observed_metrics["comments"], 173)
        self.assertEqual(rec.publication_timestamp, "2026-07-27T15:12:02+00:00")

    def test_no_capture_leaves_the_record_exactly_as_before(self):
        brief = mii.manual_brief("dreemzy")
        rec = mii.build_record(_submission(), PROBE, brief=brief, media_url="https://cdn/x.mp4")
        # Unchanged from before the injection: no timestamp claim, no metric keys.
        self.assertFalse(rec.publication_timestamp)
        for key in ("views", "likes", "comments", "shares", "saves", "score"):
            self.assertNotIn(key, rec.observed_metrics)

    def test_absent_field_is_not_coerced_to_zero(self):
        brief = mii.manual_brief("dreemzy")
        rec = mii.build_record(_submission(), PROBE, brief=brief, media_url="https://cdn/x.mp4",
                               source_metadata=_capture(INSTAGRAM_META, platform="instagram"))
        self.assertNotIn("views", rec.observed_metrics)
        self.assertEqual(rec.observed_metrics["likes"], 18914)


class TestDurablePersistence(unittest.TestCase):
    def test_submission_row_keeps_the_captured_evidence(self):
        captured = _capture(TIKTOK_META)
        payload = mmi.build_media_submission(
            url="https://www.tiktok.com/@midflightclips/video/7667225763720023327",
            destination="dreemzy", content_format="hold_unassigned", submitter="passportpetty",
            media_reference={"url": "https://cdn/x.mp4"}, probe=PROBE,
            human_window=(None, None), source_metadata=captured)
        self.assertEqual(payload["media_reference"]["source_metadata"]["metrics"]["views"], 771600)

    def test_submission_row_without_capture_is_unchanged(self):
        payload = mmi.build_media_submission(
            url="https://www.tiktok.com/@midflightclips/video/7667225763720023327",
            destination="dreemzy", content_format="hold_unassigned", submitter="passportpetty",
            media_reference={"url": "https://cdn/x.mp4"}, probe=PROBE,
            human_window=(None, None))
        self.assertNotIn("source_metadata", payload["media_reference"])

    def test_candidate_lineage_carries_the_evidence_and_the_price_of_truth(self):
        captured = _capture(INSTAGRAM_META, platform="instagram")
        calls = []

        class _Store:
            def _request(self, method, path, body=None, prefer=None):
                calls.append((method, path, body))
                if method == "GET":
                    return [{"duplicate_fingerprint": "d" * 64, "moment_evidence": {}}]
                return [{}]

        out = mii.enrich_manual_candidate(
            submission=_submission(), probe=PROBE, media_url="https://cdn/x.mp4",
            candidate=None, store=_Store(), source_metadata=captured)
        self.assertTrue(out.get("ok"), out)
        row_patch = next(body for method, path, body in calls
                         if method == "PATCH" and path.startswith("loom_source_candidates"))
        lineage = row_patch["moment_evidence"]["lineage"]
        self.assertEqual(lineage["source_metadata"]["metrics"]["likes"], 18914)
        self.assertIn("views", lineage["source_metadata"]["unavailable"])


def _market_record(**metrics):
    return {"platform": "tiktok", "observed_metrics": dict(metrics),
            "publication_timestamp": "2026-07-27T09:52:02+00:00", "metric_history": []}


class TestMarketScoreIsNotFabricated(unittest.TestCase):
    def test_no_evidence_reports_unavailable_not_a_measured_zero(self):
        decision = assess_market_demand(_market_record())
        self.assertFalse(decision.evidence["evidence_available"])
        self.assertEqual(decision.evidence["evidence_coverage"], 0.0)
        self.assertEqual(decision.evidence["unavailable_metrics"],
                         ["comments", "likes", "saves", "shares", "views"])

    def test_partial_evidence_reports_coverage_and_stays_honest(self):
        decision = assess_market_demand(_market_record(likes=18914, comments=1149))
        self.assertTrue(decision.evidence["evidence_available"] is False)
        self.assertEqual(decision.evidence["evidence_coverage"], 0.4)
        self.assertIn("views", decision.evidence["unavailable_metrics"])

    def test_measured_evidence_is_marked_available(self):
        decision = assess_market_demand(_market_record(views=771600, likes=13400,
                                                       comments=173, shares=2632, saves=5513))
        self.assertTrue(decision.evidence["evidence_available"])
        self.assertEqual(decision.evidence["evidence_coverage"], 1.0)
        self.assertGreater(decision.score, 0)


def _field(embed, name):
    for f in embed.get("fields") or []:
        if f.get("name") == name:
            return str(f.get("value"))
    return ""


def _manual_card(market_evidence=None, lineage=None, intent=True):
    """A manual candidate as the live rows are shaped.

    `intent` present -> the mining renderer (the live normal case).
    `intent` absent  -> the CI-declined renderer, which is what a manual asset that
    Content Intelligence filters before materializing a moment actually renders as.
    """
    me = {"market_evidence": market_evidence or {},
          "lineage": lineage or {"origin": "manual_media_intake"}}
    if intent:
        me["intent"] = "operator-supplied content for human review"
    return {"duplicate_fingerprint": "a" * 64, "tenant_id": "dreemzy", "platform": "tiktok",
            "title": "t", "canonical_url": "https://x", "creator_channel_identity": "c",
            "market_opportunity_score": 0, "content_worthiness_score": 0,
            "source_selection_score": 0, "fire_passed": False, "moment_type": {},
            "boundary_plan": {}, "moment_evidence": me, "media_assessment": {},
            "profile_id": "manual-intake", "status": "scored",
            "rejection_reason": "MARKET_GATE_FAILED"}


class TestCardHonesty(unittest.TestCase):
    def test_manual_card_shows_available_evidence_and_not_just_zeros(self):
        card = _manual_card(market_evidence={"views": 771600, "likes": 13400, "comments": 173})
        text = _field(source_review.format_source_embed(card), "Market evidence")
        self.assertIn("Views/plays 771600", text)
        self.assertIn("Likes 13400", text)
        self.assertIn("Comments 173", text)

    def test_declined_manual_card_uses_captured_evidence(self):
        lineage = {"origin": "manual_media_intake",
                   "source_metadata": {"metrics": {"views": 2429, "likes": 0},
                                       "unavailable": ["comments"],
                                       "publication_timestamp": "2026-09-21T00:03:45+00:00"}}
        card = _manual_card(lineage=lineage, intent=False)
        text = _field(source_review.format_source_embed(card), "Source engagement")
        self.assertIn("Views/plays 2429", text)
        self.assertIn("Likes 0", text)
        self.assertIn("Comments Unavailable", text)
        self.assertIn("Published 2026-09-21", text)

    def test_one_missing_field_does_not_hide_the_evidence_section(self):
        card = _manual_card(market_evidence={"views": None, "likes": 18914}, intent=False)
        text = _field(source_review.format_source_embed(card), "Source engagement")
        self.assertIn("Views/plays Unavailable", text)
        self.assertIn("Likes 18914", text)

    def test_absent_evidence_adds_no_zero_and_keeps_the_human_override(self):
        card = _manual_card(intent=False)
        embed = source_review.format_source_embed(card)
        self.assertEqual(_field(embed, "Source engagement"), "")
        self.assertIn("LOOM informs", _field(embed, "Human decision required"))

    def test_market_line_is_unavailable_rather_than_a_zero_score(self):
        card = _manual_card()
        card["moment_evidence"]["market_evidence"] = {
            "evidence_available": False, "evidence_coverage": 0.0,
            "evidence_fields": {"views": False, "likes": False, "comments": False,
                                "shares": False, "saves": False}}
        text = _field(source_review.format_source_embed(card), "Market evidence")
        self.assertIn("Market **unavailable**", text)
        self.assertNotIn("0 / 100", text)
        self.assertIn("Coverage: 0/5", text)

    def test_market_line_reports_a_measured_score_with_coverage(self):
        card = _manual_card()
        card["market_opportunity_score"] = 78
        card["moment_evidence"]["market_evidence"] = {
            "views": 771600, "likes": 13400, "comments": 173,
            "evidence_available": True, "evidence_coverage": 0.6,
            "evidence_fields": {"views": True, "likes": True, "comments": True,
                                "shares": False, "saves": False}}
        text = _field(source_review.format_source_embed(card), "Market evidence")
        self.assertIn("Market **78 / 100**", text)
        self.assertIn("Coverage: 3/5", text)

    def test_unknown_market_keeps_the_legacy_shape_for_existing_rows(self):
        card = _manual_card(market_evidence={"views": None, "likes": None})
        self.assertIn("Market **0 / 100**",
                      _field(source_review.format_source_embed(card), "Market evidence"))


class TestHumanArbitrationUntouched(unittest.TestCase):
    def test_manual_evidence_never_disables_the_human_decision(self):
        card = _manual_card(market_evidence={"views": 3, "likes": 0}, intent=False)
        embed = source_review.format_source_embed(card)
        self.assertIn("Human decision required", [f["name"] for f in embed["fields"]])
        buttons = source_review.format_source_buttons("a" * 64, manual_media=True)
        labels = [b["label"] for row in buttons for b in row["components"]]
        self.assertIn("Approve Clip", labels)
        self.assertIn("Reject", labels)
        self.assertIn("Hold", labels)

    def test_market_capture_never_changes_fire_or_status_semantics(self):
        # The injection is evidence only: no FIRE input, no gate, no policy key.
        decision = assess_market_demand(_market_record(views=771600, likes=13400))
        self.assertNotIn("fire", decision.evidence)
        self.assertNotIn("adults_only", decision.evidence)


if __name__ == "__main__":
    unittest.main(verbosity=2)
