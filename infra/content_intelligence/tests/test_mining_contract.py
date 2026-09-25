import sys
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace as NS
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from content_intelligence.mining_contract import media_qualification, resolve_inspected_moments
from content_intelligence.tenant_fit import tenant_fit


class MiningContractTests(unittest.TestCase):
    def brief(self, concepts):
        return NS(mining_requirements={"required_concepts": concepts})

    def test_generic_funny_does_not_admit_unrelated_theme(self):
        brief = self.brief([["dog", "dogs"]])
        self.assertTrue(tenant_fit(title="funny golf shot", brief=brief).failed)
        self.assertTrue(tenant_fit(title="funny dogs playing", brief=brief).passed)

    def test_each_lane_concept_required_and_no_cross_tenant_state(self):
        a, b = self.brief([["beach"], ["bikini"]]), self.brief([["dog"]])
        self.assertTrue(tenant_fit(title="funny beach bikini moments", brief=a).passed)
        self.assertTrue(tenant_fit(title="funny beach bikini moments", brief=b).failed)
        self.assertTrue(tenant_fit(title="funny beach moments", brief=a).failed)
        self.assertTrue(tenant_fit(title="funny beach bikini moments", brief=a).passed)

    def test_target_creator_exact_match(self):
        b = NS(mining_requirements={"creator_identities": ["verified-id"]})
        self.assertTrue(tenant_fit(creator="verified-id-fan", brief=b).failed)
        self.assertTrue(tenant_fit(creator="verified-id", brief=b).passed)

    def fixture(self):
        return {"qualification": {"intent_match": True, "adult_status": "confirmed_adults",
                  "adult_evidence": "identified adult performer", "nudity": False, "explicit_sex": False,
                  "concept_evidence": [{"concept": "beach", "observation": "event on sand"}]},
                "moments": [{"start": 2, "event_timestamp": 5, "what_happens": "a concrete event",
                    "why_reaction_worthy": "an unexpected reversal", "hook_0_3s": "setup visible",
                    "payoff": "recovery and reaction", "reaction_angle": "explain reversal",
                    "start_evidence": "setup begins", "boundaries": [
                        {"end": 6, "complete": False, "evidence": "reaction unresolved"},
                        {"end": 8, "complete": True, "evidence": "reaction resolves"},
                        {"end": 12, "complete": True, "evidence": "unrelated next scene"}]}]}

    def resolve(self, parsed, **kw):
        return resolve_inspected_moments(parsed, {"adults_only": True, "required_concepts": [["beach"]]},
                                        inspected_start=0, inspected_end=12, **kw)

    def test_native_short_uses_first_complete_subwindow_not_whole_asset(self):
        out, reasons = self.resolve(self.fixture(), duration=60)
        self.assertFalse(reasons)
        self.assertEqual((out[0]["recommended_clip_start"], out[0]["recommended_clip_end"]), (2, 8))

    def test_real_zero_whole_asset_window_preserved(self):
        p=self.fixture();p["moments"][0]["start"]=0
        out,_=self.resolve(p,duration=8)
        self.assertEqual((out[0]["recommended_clip_start"],out[0]["recommended_clip_end"]),(0,8))

    def test_unknown_age_misleading_title_and_explicit_media_fail_closed(self):
        for k,v in (("adult_status","ambiguous"),("adult_status","minors"),("intent_match",False),
                    ("nudity",True),("explicit_sex",True)):
            p=self.fixture();p["qualification"][k]=v
            self.assertEqual(self.resolve(p)[0], [], (k,v))

    def test_uninspected_or_incomplete_end_selects_none(self):
        p=self.fixture();p["moments"][0]["boundaries"]=[{"end":15,"complete":True,"evidence":"unseen"}]
        self.assertEqual(self.resolve(p)[0],[])
        p["moments"][0]["boundaries"]=[{"end":8,"complete":False,"evidence":"cut off"}]
        self.assertEqual(self.resolve(p)[0],[])

    def test_media_gates_reject_galleries_and_text_even_with_video_title(self):
        for platform, url in (("reddit", "https://www.reddit.com/r/x/comments/abc/title/"),
                              ("instagram", "https://www.instagram.com/reel/abc/")):
            r=NS(platform=platform,canonical_url=url,observed_metrics={"is_video":False,"is_gallery":True})
            self.assertFalse(media_qualification(r)[0])

    def test_narrated_setup_is_not_the_actual_event(self):
        p = self.fixture()
        p['qualification']['event_directly_observed'] = False
        out, reasons = resolve_inspected_moments(p, {'require_direct_event': True},
                                                inspected_start=0, inspected_end=12)
        self.assertEqual(out, [])
        self.assertIn('no_directly_observed_event', reasons)

    def test_canonical_link_rejects_cdn(self):
        r=NS(platform="twitch",canonical_url="https://cdn.example.com/video.m3u8",observed_metrics={})
        self.assertFalse(media_qualification(r)[0])

    def test_payoff_must_be_inside_first_complete_selected_boundary(self):
        p = self.fixture()
        p['moments'][0]['payoff_timestamp'] = 10
        out, _ = self.resolve(p)
        self.assertEqual(out[0]['recommended_clip_end'], 12)
        p['moments'][0]['payoff_timestamp'] = 13
        self.assertEqual(self.resolve(p)[0], [])

    def test_required_timed_payoff_rejects_missing_invalid_or_before_event(self):
        for payoff in (None, '8', float('nan'), float('inf'), 4):
            p = self.fixture()
            p['moments'][0]['payoff_timestamp'] = payoff
            out, _ = resolve_inspected_moments(p, {'require_timed_payoff': True},
                                              inspected_start=0, inspected_end=12)
            self.assertEqual(out, [], payoff)
