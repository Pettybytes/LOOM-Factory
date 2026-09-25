"""Acceptance regressions for tenant isolation and cheap preinspection gates.

All pipeline runs use in-memory stores and providers. Transcript/media/network
entry points are guarded so a gate regression cannot trigger external work.
"""

import json
import sys
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

_ROOT = Path(__file__).resolve().parents[3]
for _path in (str(_ROOT / "infra"), str(Path(__file__).parent)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from content_intelligence import pipeline
from content_intelligence.pipeline import Pass1Config, Pass1Pipeline
from discovery.source_brief import SourceBrief
from test_pipeline import _FakeProvider, _FakeStore, _FakeTopic, _Record


def _brief_data():
    data = json.loads(
        (_ROOT / "infra/discovery/fixtures/brief_bryan_face_reaction.json").read_text()
    )
    data.update(
        tenant_id="tenant-alpha",
        profile_id="acceptance-alpha",
        topics=["funny beach bikini moments"],
        keywords=["beach", "bikini"],
        entities=[],
        mining_requirements={
            "intent": "funny beach bikini moments",
            "required_concepts": [["beach", "seaside"], ["bikini"]],
            "adults_only": True,
            "creator_identities": [],
        },
    )
    return data


def _record(brief, **changes):
    record = _Record(title="Funny beach bikini moment", cid="scope-test")
    record.tenant_id = brief.tenant_id
    record.profile_id = brief.profile_id
    record.creator_channel_identity = "Example adult creator"
    record.body_excerpt = "An unexpected beach mishap."
    for key, value in changes.items():
        setattr(record, key, value)
    return record


class TenantIsolationAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.brief = SourceBrief.from_dict(_brief_data())

    def run_pipeline(self, record, *, topic=None, threshold=40,
                     allow_transcript=False, brief=None):
        provider, store = _FakeProvider(), _FakeStore()
        topic = topic if topic is not None else _FakeTopic(90)
        pipe = Pass1Pipeline(
            Pass1Config(market_shortlist_threshold=threshold, enable_fire_gate=False),
            brief or self.brief,
            topic,
            provider=provider,
        )
        with ExitStack() as stack:
            fetch = stack.enter_context(mock.patch.object(pipeline, "fetch_transcript"))
            if allow_transcript:
                fetch.return_value = ([{
                    "start_s": 0.0, "end_s": 5.0,
                    "text": "A beach mishap and the adults' surprised reaction.",
                }], "auto_generated")
            else:
                fetch.side_effect = AssertionError("must reject before transcript retrieval")
            media = stack.enter_context(mock.patch.object(
                pipeline, "fetch_media_and_signals",
                side_effect=AssertionError("test must never retrieve media"),
            ))
            build = stack.enter_context(mock.patch.object(
                pipeline, "build_provider",
                side_effect=AssertionError("test must never build a live provider"),
            ))
            result = pipe.run([record], store)
        media.assert_not_called()
        build.assert_not_called()
        return result.candidates[0], provider, store, fetch

    def assert_rejected_before_market(self, record, expected_status, reason):
        topic = mock.Mock()
        topic.assess.side_effect = AssertionError("market must not see wrong tenant/media/theme")
        candidate, provider, store, fetch = self.run_pipeline(record, topic=topic)
        self.assertEqual(candidate.status, expected_status)
        self.assertIn(reason, candidate.rejection_reason)
        topic.assess.assert_not_called()
        fetch.assert_not_called()
        self.assertEqual(provider.calls, 0)
        self.assertEqual(store.assessments, {})
        self.assertEqual(store.selections, {})

    def test_other_tenant_record_is_rejected_before_market_or_provider(self):
        self.assert_rejected_before_market(
            _record(self.brief, tenant_id="tenant-beta"),
            "filtered_tenant", "tenant_or_profile_mismatch",
        )

    def test_other_profile_record_is_rejected_before_market_or_provider(self):
        self.assert_rejected_before_market(
            _record(self.brief, profile_id="acceptance-beta"),
            "filtered_tenant", "tenant_or_profile_mismatch",
        )

    def test_same_theme_gallery_is_rejected_before_market(self):
        record = _record(self.brief)
        record.observed_metrics.update(is_gallery=True, media_type="gallery")
        self.assert_rejected_before_market(record, "filtered_media", "not_video")

    def test_generic_funny_or_partial_theme_never_reaches_market(self):
        for title in ("Funny golf moment", "Funny beach moment", "Funny bikini indoors"):
            with self.subTest(title=title):
                self.assert_rejected_before_market(
                    _record(self.brief, title=title, body_excerpt=""),
                    "filtered_tenant", "required_lane_concept_missing",
                )

    def test_wrong_creator_rejected_before_market_even_when_theme_matches(self):
        self.brief.mining_requirements["creator_identities"] = ["verified-creator"]
        self.assert_rejected_before_market(
            _record(self.brief, creator_channel_identity="verified-creator-fan"),
            "filtered_tenant", "target_creator_mismatch",
        )

    def test_strict_market_74_rejected_despite_lower_legacy_threshold(self):
        candidate, provider, store, fetch = self.run_pipeline(
            _record(self.brief), topic=_FakeTopic(74), threshold=0,
        )
        self.assertEqual(candidate.status, "skipped_market")
        self.assertEqual(candidate.market_score, 74)
        fetch.assert_not_called()
        self.assertEqual(provider.calls, 0)
        self.assertEqual(store.assessments, {})

    def test_strict_market_75_reaches_inspection_despite_higher_legacy_threshold(self):
        candidate, provider, store, fetch = self.run_pipeline(
            _record(self.brief), topic=_FakeTopic(75), threshold=99,
            allow_transcript=True,
        )
        self.assertEqual(candidate.market_score, 75)
        self.assertTrue(candidate.market_gate_passed)
        self.assertEqual(candidate.status, "scored", candidate.error)
        fetch.assert_called_once()
        self.assertEqual(provider.calls, 1)
        self.assertEqual(len(store.assessments), 1)

    def test_from_dict_deep_copies_nested_mining_requirements(self):
        source = _brief_data()
        brief = SourceBrief.from_dict(source)
        source["mining_requirements"]["required_concepts"][0].append("golf")
        source["mining_requirements"]["creator_identities"].append("another-creator")
        self.assertEqual(brief.mining_requirements["required_concepts"],
                         [["beach", "seaside"], ["bikini"]])
        self.assertEqual(brief.mining_requirements["creator_identities"], [])

    def test_human_review_can_inspect_below_threshold_without_raising_score(self):
        self.brief.mining_requirements['review_policy'] = {'mode': 'human_curated'}
        candidate, provider, store, fetch = self.run_pipeline(
            _record(self.brief), topic=_FakeTopic(60), allow_transcript=True)
        self.assertEqual(candidate.market_score, 60)
        fetch.assert_called_once()
        self.assertEqual(provider.calls, 1)

    def test_human_review_does_not_relax_theme_or_tenant(self):
        self.brief.mining_requirements['review_policy'] = {'mode': 'human_curated'}
        self.assert_rejected_before_market(
            _record(self.brief, title='funny golf', body_excerpt='golf course'),
            'filtered_tenant', 'required_lane_concept_missing')

    def test_serialized_round_trip_and_second_tenant_have_no_shared_nested_state(self):
        exported = self.brief.to_dict()
        second = SourceBrief.from_dict(exported)
        second.tenant_id, second.profile_id = "tenant-beta", "acceptance-beta"
        second.mining_requirements["required_concepts"][0][:] = ["dog"]
        second.mining_requirements["creator_identities"].append("beta-creator")
        self.assertEqual(exported["mining_requirements"]["required_concepts"][0],
                         ["beach", "seaside"])
        exported["mining_requirements"]["required_concepts"][1].append("golf")
        self.assertEqual(self.brief.mining_requirements["required_concepts"],
                         [["beach", "seaside"], ["bikini"]])
        self.assertEqual(self.brief.mining_requirements["creator_identities"], [])
        self.assertEqual(second.mining_requirements["required_concepts"][1], ["bikini"])


if __name__ == "__main__":
    unittest.main()
