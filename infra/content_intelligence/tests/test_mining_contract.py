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
        return resolve_inspected_moments(parsed, {"required_concepts": [["beach"]]},
                                        inspected_start=0, inspected_end=12, **kw)

    def test_native_short_uses_first_complete_subwindow_not_whole_asset(self):
        out, reasons = self.resolve(self.fixture(), duration=60)
        self.assertFalse(reasons)
        self.assertEqual((out[0]["recommended_clip_start"], out[0]["recommended_clip_end"]), (2, 8))

    def test_real_zero_whole_asset_window_preserved(self):
        p=self.fixture();p["moments"][0]["start"]=0
        out,_=self.resolve(p,duration=8)
        self.assertEqual((out[0]["recommended_clip_start"],out[0]["recommended_clip_end"]),(0,8))

    def test_unrelated_gates_still_fail_closed(self):
        for k,v in (("intent_match",False),("nudity",True),("explicit_sex",True)):
            p=self.fixture();p["qualification"][k]=v
            self.assertEqual(self.resolve(p)[0], [], (k,v))

    def test_age_inference_never_blocks_a_candidate(self):
        # Age is not a product gate (removed 2026-09-26, unauthorized requirement).
        # Ambiguous/minors metadata must not suppress a candidate that otherwise
        # qualifies, and must never add an age reason.
        for k,v in (("adult_status","ambiguous"),("adult_status","minors"),
                    ("adult_evidence",""),("adult_evidence","age uncertain")):
            p=self.fixture();p["qualification"][k]=v
            out, reasons = self.resolve(p)
            self.assertTrue(out, (k,v))
            self.assertNotIn("adult_status_unproven", reasons)

    def test_legacy_adults_only_key_is_inert(self):
        p=self.fixture()
        p["qualification"]["adult_status"]="ambiguous"
        out, reasons = resolve_inspected_moments(
            p, {"adults_only": True, "required_concepts": [["beach"]]},
            inspected_start=0, inspected_end=12)
        self.assertTrue(out)
        self.assertNotIn("adult_status_unproven", reasons)

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

    # --- Kick qualification: the stale clip-only rule (VODs were unqualifiable) ----
    KICK_VOD = "https://kick.com/deenthegreat/videos/05e14bff-67ca-492a-ba79-3f3248ca41a5"

    def media(self, platform, url, media_type="video"):
        return NS(platform=platform, canonical_url=url,
                  observed_metrics={"media_type": media_type})

    def test_kick_vod_path_qualifies_as_video(self):
        # The exact URL of the accepted Kick VOD intake: previously filtered_media /
        # video_type_unproven BEFORE scoring, purely because the branch was clip-only.
        self.assertEqual(media_qualification(self.media("kick", self.KICK_VOD)),
                         (True, "video_qualified"))

    def test_kick_clip_qualification_is_preserved(self):
        for url in ("https://kick.com/deenthegreat/clips/clip_01M33XASMP29XTQ1QK0EV7TXDC",
                    "https://kick.com/deenthegreat?clip=clip_01M33XASMP29XTQ1QK0EV7TXDC"):
            with self.subTest(url=url):
                self.assertEqual(
                    media_qualification(self.media("kick", url, media_type="clip")),
                    (True, "video_qualified"))

    def test_non_video_kick_urls_do_not_qualify_just_by_being_kick(self):
        for url in ("https://kick.com/deenthegreat",
                    "https://kick.com/deenthegreat/videos",
                    "https://kick.com/deenthegreat/videos/",
                    "https://kick.com/deenthegreat/about",
                    "https://kick.com/categories/games"):
            with self.subTest(url=url):
                self.assertEqual(
                    media_qualification(self.media("kick", url)),
                    (False, "video_type_unproven"))

    def test_a_videos_path_on_a_non_kick_host_does_not_qualify(self):
        self.assertEqual(
            media_qualification(self.media("kick", "https://notkick.example/videos/abc")),
            (False, "invalid_canonical_platform_url"))

    def test_kick_image_or_gallery_still_rejected(self):
        self.assertEqual(
            media_qualification(self.media("kick", self.KICK_VOD, media_type="image")),
            (False, "not_video"))

    def test_youtube_and_twitch_qualification_unchanged(self):
        self.assertEqual(
            media_qualification(self.media("youtube", "https://www.youtube.com/watch?v=59xxeJCbzaM")),
            (True, "video_qualified"))
        self.assertEqual(
            media_qualification(self.media("youtube", "https://www.youtube.com/shorts/abc")),
            (True, "video_qualified"))
        self.assertEqual(
            media_qualification(self.media("twitch", "https://www.twitch.tv/videos/2880604198")),
            (True, "video_qualified"))
        self.assertEqual(
            media_qualification(self.media("twitch", "https://clips.twitch.tv/SomeSlug", "clip")),
            (True, "video_qualified"))
        # ...and a non-video YouTube path is still not qualified.
        self.assertEqual(
            media_qualification(self.media("youtube", "https://www.youtube.com/@channel")),
            (False, "video_type_unproven"))
