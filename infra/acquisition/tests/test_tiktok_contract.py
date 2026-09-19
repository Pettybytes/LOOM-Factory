"""TikTok acquisition contract.

Covers the full TikTok surface: URL identity, actor-specific request/response
translation, the two independent ceilings, and the shared media path.

Run:  python3 -m unittest discover -s infra/acquisition/tests
"""

import hashlib
import io as _io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_REPO_ROOT = Path(__file__).resolve().parents[3]
for _d in ("acquisition", "local-helper", "discord-review"):
    sys.path.insert(0, str(_REPO_ROOT / "infra" / _d))

import apify_client  # noqa: E402
import tiktok_actors as ta  # noqa: E402
import tiktok_ceiling as tc  # noqa: E402
import tiktok_media as tm  # noqa: E402
import tiktok_url as tu  # noqa: E402
import twitch_url  # noqa: E402

TARGET = "https://www.tiktok.com/@thoughtswthot/video/7598767615980408095"
TARGET_PARAMS = (TARGET + "?is_from_webapp=1&sender_device=pc"
                 "&web_id=7686631454795040274")
CID = "7598767615980408095"
CLASH_ID = "759876761598"          # valid for BOTH tiktok and twitch (5-12 digits)
VIDEO_CDN = "https://v16-webapp-prime.tiktokcdn.com/video/abc.mp4"
AUDIO_CDN = "https://v16-webapp-prime.tiktokcdn.com/audio/abc.mp3"


class _Resp(_io.BytesIO):
    """Minimal urlopen() stand-in supporting the context-manager protocol."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    @property
    def status(self):
        return 200

    @property
    def headers(self):
        return {}


def _urlopen_returning(payload, sink=None):
    body = json.dumps(payload).encode()

    def opener(req, timeout=None):
        if sink is not None:
            sink["url"] = getattr(req, "full_url", "")
            sink["body"] = json.loads((req.data or b"{}").decode() or "{}")
        return _Resp(body)
    return opener


def _expect_code(case, exc_type, code, fn, *a, **kw):
    with case.assertRaises(exc_type) as cm:
        fn(*a, **kw)
    case.assertEqual(getattr(cm.exception, "code", None), code,
                     f"expected {code}, got {cm.exception}")
    return cm.exception


# ===========================================================================
# 1-11. URL identity (banked contract, re-asserted here as the TikTok suite)
# ===========================================================================
class TestTikTokUrlIdentity(unittest.TestCase):
    def test_01_canonical_video_url(self):
        got = tu.classify(TARGET_PARAMS)
        self.assertEqual(got["platform"], "tiktok")
        self.assertEqual(got["kind"], "video")
        self.assertEqual(got["handle"], "thoughtswthot")
        self.assertEqual(got["content_id"], CID)
        self.assertEqual(got["canonical_url"], TARGET)

    def test_02_tracking_parameters_are_ignored(self):
        self.assertEqual(tu.classify(TARGET)["content_id"],
                         tu.classify(TARGET_PARAMS)["content_id"])
        self.assertEqual(tu.classify(TARGET)["canonical_url"],
                         tu.classify(TARGET_PARAMS)["canonical_url"])
        self.assertEqual(tu.fingerprint_for(TARGET), tu.fingerprint_for(TARGET_PARAMS))
        # The wire request always carries the CANONICAL url, never the tracked one.
        inp = ta.build_actor_input(ta.ACTOR_CLOCKWORKS,
                                   tu.classify(TARGET_PARAMS)["canonical_url"])
        self.assertEqual(inp["postURLs"], [TARGET])
        self.assertNotIn("web_id", json.dumps(inp))

    def test_03_photo_slideshow_is_refused(self):
        photo = "https://www.tiktok.com/@thoughtswthot/photo/" + CID
        self.assertEqual(tu.classify(photo)["kind"], "slideshow")
        _expect_code(self, tu.TikTokUrlError, "SLIDESHOW_NOT_VIDEO", tu.require_video, photo)

    def test_04_unresolved_short_link_is_refused(self):
        _expect_code(self, tu.TikTokUrlError, "SHORT_LINK_UNRESOLVED",
                     tu.classify, "https://vm.tiktok.com/ZMabc123/")
        self.assertTrue(tu.is_short_link("https://vt.tiktok.com/ZSabc/"))
        # ... and resolving it IS accepted (identity is never guessed).
        got = tu.classify("https://vm.tiktok.com/ZMabc123/", resolved_url=TARGET)
        self.assertEqual(got["content_id"], CID)

    def test_05_invalid_video_id_is_refused(self):
        _expect_code(self, tu.TikTokUrlError, "BAD_VIDEO_ID",
                     tu.classify, "https://www.tiktok.com/@thoughtswthot/video/not-an-id")

    def test_06_profile_is_refused(self):
        _expect_code(self, tu.TikTokUrlError, "PROFILE_PAGE",
                     tu.classify, "https://www.tiktok.com/@thoughtswthot")

    def test_07_search_and_tag_pages_are_refused(self):
        _expect_code(self, tu.TikTokUrlError, "NOT_A_MEDIA_URL",
                     tu.classify, "https://www.tiktok.com/tag/fyp")
        _expect_code(self, tu.TikTokUrlError, "NOT_A_MEDIA_URL",
                     tu.classify, "https://www.tiktok.com/search?q=cats")

    def test_08_foreign_host_is_refused(self):
        _expect_code(self, tu.TikTokUrlError, "FOREIGN_HOST",
                     tu.classify, "https://www.example.com/@a/video/759876761598")

    def test_09_embedded_credentials_are_refused(self):
        _expect_code(self, tu.TikTokUrlError, "CREDENTIALS",
                     tu.classify, "https://user:pw@www.tiktok.com/@a/video/759876761598")

    def test_10_source_id_drift_is_refused(self):
        _expect_code(self, tu.TikTokUrlError, "SOURCE_ID_DRIFT",
                     tu.parse, TARGET, expected_content_id="111111111111")

    def test_11_cross_platform_fingerprints_are_separate(self):
        """The same numeric id on two platforms is two different identities."""
        tiktok_fp = tu.fingerprint_for(f"https://www.tiktok.com/@aa/video/{CLASH_ID}")
        twitch_fp = twitch_url.fingerprint_for(f"https://www.twitch.tv/videos/{CLASH_ID}")
        self.assertNotEqual(tiktok_fp, twitch_fp)
        self.assertEqual(len(tiktok_fp), 64)
        parsed = tu.classify(f"https://www.tiktok.com/@aa/video/{CLASH_ID}")
        self.assertEqual(parsed["content_id"], CLASH_ID)


# ===========================================================================
# 12. charge-cap placement
# ===========================================================================
class TestChargeCapPlacement(unittest.TestCase):
    def test_12a_charge_cap_is_not_actor_input(self):
        inp, params = ta.build_run_input(ta.ACTOR_CLOCKWORKS, TARGET)
        for key in ("maxTotalChargeUsd", "max_total_charge_usd", "maxTotalChargeUsdUsd"):
            self.assertNotIn(key, inp, "the charge cap must never be actor input")
        # LIVE FINDING: Apify's floor is $0.50, so the run parameter carries the
        # effective cap while the $0.01 authorization stays with the ledger.
        report = ta.charge_cap_report()
        self.assertEqual(report["authorized_usd"], 0.01)
        self.assertEqual(report["effective_run_parameter_usd"], 0.50)
        self.assertTrue(report["clamped"])
        self.assertEqual(params, {"max_total_charge_usd": 0.50})
        self.assertIn("0.50", report["reason"])

    def test_12a2_each_actor_gets_its_own_live_verified_input_shape(self):
        """Live-verified schemas: Clockworks=postURLs, Apidojo=startUrls+maxItems."""
        clockworks = ta.build_actor_input(ta.ACTOR_CLOCKWORKS, TARGET)
        self.assertEqual(clockworks, {"postURLs": [TARGET]})
        self.assertNotIn("startUrls", clockworks)
        apidojo = ta.build_actor_input(ta.ACTOR_APIDOJO, TARGET)
        self.assertEqual(apidojo, {"startUrls": [{"url": TARGET}], "maxItems": 1})
        # Neither shape may carry a paid add-on event.
        for inp in (clockworks, apidojo):
            self.assertEqual(ta.PAID_ADDON_FIELDS.intersection(inp), frozenset())
        self.assertIn("shouldDownloadVideos", ta.PAID_ADDON_FIELDS)
        self.assertIn("aiVideoSummary", ta.PAID_ADDON_FIELDS)
        # Exactly one URL, whatever the shape.
        self.assertEqual(len(clockworks["postURLs"]), 1)
        self.assertEqual(len(apidojo["startUrls"]), 1)

    def test_12b_charge_cap_travels_as_a_run_parameter(self):
        inp, params = ta.build_run_input(ta.ACTOR_APIDOJO, TARGET)
        sink = {}
        transport = apify_client.HttpTransport("token")
        with mock.patch("urllib.request.urlopen",
                        _urlopen_returning({"data": {"id": "run-1"}}, sink)):
            transport.start_run(ta.ACTOR_APIDOJO, inp, **params)
        self.assertIn("maxTotalChargeUsd=0.5", sink["url"])
        self.assertNotIn("maxTotalChargeUsd", sink["body"])
        self.assertEqual(sink["body"], inp)

    def test_12c_non_positive_charge_cap_is_rejected(self):
        transport = apify_client.HttpTransport("token")
        _expect_code(self, apify_client.ApifyError, "BAD_CHARGE_CAP",
                     transport.start_run, ta.ACTOR_APIDOJO, {}, max_total_charge_usd=-1)
        _expect_code(self, apify_client.ApifyError, "BAD_CHARGE_CAP",
                     transport.start_run, ta.ACTOR_APIDOJO, {}, max_total_charge_usd=0)
        # A request below the platform floor is raised to it, never accepted as-is.
        self.assertEqual(ta.effective_charge_cap_usd(0.01), 0.50)
        self.assertEqual(ta.effective_charge_cap_usd(0.25), 0.50)
        self.assertEqual(ta.effective_charge_cap_usd(2.00), 2.00)


# ===========================================================================
# 13-15. response translation refusals
# ===========================================================================
class TestResponseTranslation(unittest.TestCase):
    # --- evidence-based fixtures: the REAL observed item shapes (2026-09-18) ---
    @staticmethod
    def _clockworks_item(**over):
        """Live Clockworks shape: NO downloadable video (``mediaUrls`` is empty)."""
        item = {
            "id": CID, "webVideoUrl": TARGET, "isSlideshow": False,
            "videoMeta": {"duration": 8, "width": 720, "height": 1280,
                          "coverUrl": "https://p16-common-sign.tiktokcdn-eu.com/c.jpg"},
            "musicMeta": {"musicName": "original sound", "playUrl": AUDIO_CDN},
            "authorMeta": {"name": "thoughtswthot"},
            "mediaUrls": [],
        }
        item.update(over)
        return item

    @staticmethod
    def _apidojo_item(**over):
        """Live Apidojo shape: the downloadable video IS at ``video.url``."""
        item = {
            "id": CID, "postPage": TARGET, "inputSource": TARGET,
            "video": {"url": VIDEO_CDN, "duration": 8.869, "width": 720,
                      "height": 1280, "ratio": "720p",
                      "cover": "https://p16-common-sign.tiktokcdn.com/c.jpg"},
            "song": {"id": 1, "title": "original sound", "artist": "x",
                     "cover": "https://p16-common-sign.tiktokcdn.com/s.jpg"},
            "channel": {"username": "thoughtswthot", "name": "haleyzz"},
        }
        item.update(over)
        return item

    def test_13_identity_mismatch_is_refused(self):
        item = self._clockworks_item(id="111111111111111")
        _expect_code(self, ta.TikTokActorError, "IDENTITY_MISMATCH",
                     ta.translate, ta.ACTOR_CLOCKWORKS, [item],
                     expected_content_id=CID, canonical_url=TARGET)

    def test_14_zero_results_are_refused(self):
        _expect_code(self, ta.TikTokActorError, "ZERO_RESULT",
                     ta.translate, ta.ACTOR_CLOCKWORKS, [],
                     expected_content_id=CID, canonical_url=TARGET)

    def test_15_multiple_results_are_refused(self):
        item = self._clockworks_item()
        _expect_code(self, ta.TikTokActorError, "MULTIPLE_RESULTS",
                     ta.translate, ta.ACTOR_CLOCKWORKS, [item, dict(item)],
                     expected_content_id=CID, canonical_url=TARGET)

    def test_15b_each_actor_reads_its_own_verified_field(self):
        """Apidojo's video lives at ``video.url``; Clockworks has none at all."""
        got = ta.translate(ta.ACTOR_APIDOJO, [self._apidojo_item()],
                           expected_content_id=CID, canonical_url=TARGET)
        self.assertEqual(got["media_url"], VIDEO_CDN)
        self.assertEqual(got["field_map"]["video"], "video.url")
        self.assertEqual(got["duration_seconds"], 8.869)
        self.assertEqual(got["width"], 720.0)
        self.assertEqual(got["height"], 1280.0)
        self.assertEqual(got["handle"], "thoughtswthot")
        # The Apidojo shape is unreadable by the Clockworks path table...
        _expect_code(self, ta.TikTokActorError, "MISSING_MEDIA",
                     ta.translate, ta.ACTOR_CLOCKWORKS, [self._apidojo_item()],
                     expected_content_id=CID, canonical_url=TARGET)

    def test_15f_clockworks_base_result_carries_no_downloadable_media(self):
        """LIVE-VERIFIED blocker: ``mediaUrls: []`` and no video URL field.

        The base per-result charge therefore cannot deliver a video; only the paid
        ``shouldDownloadVideos ($)`` add-on could, and that is never sent.
        """
        item = self._clockworks_item()
        self.assertEqual(item["mediaUrls"], [])
        self.assertNotIn("videoUrl", item)
        self.assertEqual(sorted(item["videoMeta"]),
                         ["coverUrl", "duration", "height", "width"])
        _expect_code(self, ta.TikTokActorError, "MISSING_MEDIA",
                     ta.translate, ta.ACTOR_CLOCKWORKS, [item],
                     expected_content_id=CID, canonical_url=TARGET)
        self.assertEqual(ta.CLOCKWORKS_MEDIA_BLOCKER,
                         "paid_addon_required:shouldDownloadVideos")

    def test_15c_slideshow_and_missing_media_are_refused(self):
        _expect_code(self, ta.TikTokActorError, "SLIDESHOW_NOT_VIDEO",
                     ta.translate, ta.ACTOR_CLOCKWORKS,
                     [self._clockworks_item(isSlideshow=True)],
                     expected_content_id=CID, canonical_url=TARGET)
        bare = {"id": CID, "webVideoUrl": TARGET}
        _expect_code(self, ta.TikTokActorError, "MISSING_MEDIA",
                     ta.translate, ta.ACTOR_CLOCKWORKS, [bare],
                     expected_content_id=CID, canonical_url=TARGET)
        off_cdn = self._apidojo_item(
            video={"url": "https://evil.example.com/x.mp4", "duration": 8})
        _expect_code(self, ta.TikTokActorError, "MEDIA_HOST_NOT_ALLOWED",
                     ta.translate, ta.ACTOR_APIDOJO, [off_cdn],
                     expected_content_id=CID, canonical_url=TARGET)

    def test_15d_ready_states_are_actor_scoped_and_evidence_backed(self):
        self.assertEqual(ta.get_actor(ta.ACTOR_CLOCKWORKS).cost_micro_per_result, 1_700)
        self.assertEqual(ta.get_actor(ta.ACTOR_APIDOJO).cost_micro_per_result, 300)
        # Clockworks stayed UNPROVEN: its base result carries no media at all.
        clockworks = ta.get_actor(ta.ACTOR_CLOCKWORKS)
        self.assertFalse(clockworks.runtime_proven)
        self.assertEqual(clockworks.proven_modes, frozenset())
        self.assertEqual(
            clockworks.input_style, "postURLs")
        # Apidojo is proven for VIDEO ONLY, by a receipt recorded in code.
        apidojo = ta.get_actor(ta.ACTOR_APIDOJO)
        self.assertTrue(apidojo.runtime_proven)
        self.assertEqual(apidojo.proven_modes, frozenset({"video"}))
        self.assertNotIn("slideshow", apidojo.proven_modes)
        self.assertEqual(apidojo.input_style, "startUrls")
        receipt = ta.APIDOJO_PROOF_RECEIPT
        self.assertEqual(receipt["mode"], "video")
        self.assertEqual(receipt["content_id"], CID)
        self.assertEqual(receipt["media_field"], "video.url")
        self.assertEqual((receipt["video_codec"], receipt["audio_codec"],
                          receipt["pix_fmt"]), ("h264", "aac", "yuv420p"))
        self.assertTrue(receipt["has_audio"])
        self.assertTrue(receipt["faststart"])
        self.assertEqual(len(receipt["sha256"]), 64)
        self.assertEqual(ta.CLOCKWORKS_MEDIA_BLOCKER,
                         "paid_addon_required:shouldDownloadVideos")

    def test_15g_tiktok_capability_is_honest_and_still_fails_closed(self):
        import approved_lane as lane
        import platform_capabilities as caps
        self.assertEqual(caps.capability("tiktok"), caps.CAP_RUNTIME_PROVEN_PROVIDER)
        self.assertTrue(caps.can_acquire("tiktok"))
        reason = caps.capability_reason("tiktok")
        self.assertIn("apidojo~tiktok-scraper", reason)
        self.assertIn("clockworks~tiktok-scraper", reason)
        # Proven at the actor level, but NOT yet wired into the shared lane: the
        # paid lane must still refuse rather than guess an actor.
        _expect_code(self, lane.LaneError, "NO_PROVIDER_ACTOR",
                     lane.default_provider_actor, "tiktok")

    def test_15e_run_translation_and_error_mapping(self):
        run = {"data": {"id": "r1", "status": "SUCCEEDED", "usageTotalUsd": 0.0017,
                        "defaultDatasetId": "ds1", "maxTotalChargeUsd": 0.01}}
        self.assertTrue(ta.is_terminal(run))
        self.assertEqual(ta.run_id_of(run), "r1")
        self.assertEqual(ta.dataset_id_of(run), "ds1")
        self.assertEqual(ta.usage_total_usd(run), 0.0017)
        self.assertEqual(ta.charge_cap_seen(run), 0.01)
        self.assertEqual(ta.usage_total_usd({"data": {"status": "SUCCEEDED"}}), None)
        ta.require_terminal_success(run)
        _expect_code(self, ta.TikTokActorError, "RUN_FAILED",
                     ta.require_terminal_success, {"data": {"status": "FAILED"}})
        _expect_code(self, ta.TikTokActorError, "RUN_NOT_TERMINAL",
                     ta.require_terminal_success, {"data": {"status": "RUNNING"}})
        self.assertEqual(ta.map_provider_error(ta.TikTokActorError("ZERO_RESULT", "x")),
                         "ZERO_RESULT")
        self.assertEqual(ta.map_provider_error(URLError("boom")), "PROVIDER_UNREACHABLE")
        self.assertEqual(ta.map_provider_error(TimeoutError()), "PROVIDER_TIMEOUT")


class URLError(Exception):
    """Stand-in named like the stdlib error, for stable error mapping."""


# ===========================================================================
# 16-20. ceilings and ledger guards
# ===========================================================================
class TestTikTokCeilings(unittest.TestCase):
    def _ledger(self, **kw):
        return tc.TikTokSpendLedger(tc.MemoryTikTokStore(), **kw)

    def test_16_ceilings_are_separate_records_isolated_from_instagram(self):
        records = tc.ceiling_records()
        self.assertEqual(records[ta.ACTOR_CLOCKWORKS]["ceiling_micro"], 10_000)
        self.assertEqual(records[ta.ACTOR_APIDOJO]["ceiling_micro"], 10_000)
        self.assertEqual(records[tc.COMBINED_KEY]["ceiling_micro"], 20_000)
        self.assertEqual(records[tc.COMBINED_KEY]["scope"], "combined")
        iso = tc.instagram_isolation()
        self.assertTrue(iso["isolated"])
        self.assertEqual(iso["instagram_rows_written"], 0)
        self.assertNotIn(tc.INSTAGRAM_CEILING_TABLE, iso["tiktok_ceiling_keys"])

    def test_16b_no_actor_can_consume_the_others_allowance(self):
        led = self._ledger()
        led.reserve(actor=ta.ACTOR_CLOCKWORKS, run_id="run-cw")
        cw = led.actor_state(ta.ACTOR_CLOCKWORKS)
        ap = led.actor_state(ta.ACTOR_APIDOJO)
        self.assertEqual(cw["outstanding_micro"], 10_000)
        self.assertEqual(ap["outstanding_micro"], 0)
        self.assertEqual(ap["spent_micro"], 0)
        self.assertEqual(ap["remaining_micro"], 10_000)
        # The other actor cannot reconcile (so cannot release or spend) it.
        _expect_code(self, tc.TikTokLedgerError, "ACTOR_MISMATCH",
                     led.reconcile, actor=ta.ACTOR_APIDOJO, run_id="run-cw",
                     usage_total_usd=0.0017)
        self.assertEqual(led.actor_state(ta.ACTOR_CLOCKWORKS)["outstanding_micro"], 10_000)

    def test_17_reservation_is_full_exposure(self):
        led = self._ledger()
        out = led.reserve(actor=ta.ACTOR_CLOCKWORKS, run_id="run-cw")
        self.assertEqual(out["reserved_micro"], tc.CEILING_CLOCKWORKS_MICRO)
        self.assertEqual(out["reserved_micro"], 10_000)
        self.assertEqual(out["actor_state"]["remaining_micro"], 0)
        # No second paid run while an earlier reservation is unresolved.
        _expect_code(self, tc.TikTokLedgerError, "UNRESOLVED_RESERVATION",
                     led.reserve, actor=ta.ACTOR_APIDOJO, run_id="run-ap")
        # ... and the same run id cannot be reserved twice.
        _expect_code(self, tc.TikTokLedgerError, "DUPLICATE_RUN_ID",
                     led.reserve, actor=ta.ACTOR_CLOCKWORKS, run_id="run-cw")

    def test_17b_retry_reserves_only_the_remaining_allowance(self):
        """A partial charge must not make a legitimate retry unaffordable, and a
        retry must still be unable to breach the $0.01 actor cap."""
        led = self._ledger()
        led.reserve(actor=ta.ACTOR_CLOCKWORKS, run_id="run-1")
        led.reconcile(actor=ta.ACTOR_CLOCKWORKS, run_id="run-1",
                      usage_total_usd=0.0037, succeeded=False)   # provider defect
        self.assertEqual(led.actor_state(ta.ACTOR_CLOCKWORKS)["remaining_micro"], 6_300)
        retry = led.reserve(actor=ta.ACTOR_CLOCKWORKS, run_id="run-2")
        self.assertEqual(retry["reserved_micro"], 6_300)        # remainder, not 10,000
        # Worst case: the retry consumes its whole reservation — still exactly the cap.
        led.reconcile(actor=ta.ACTOR_CLOCKWORKS, run_id="run-2",
                      usage_total_usd=0.0063, succeeded=True)
        state = led.actor_state(ta.ACTOR_CLOCKWORKS)
        self.assertEqual(state["spent_micro"], 10_000)
        self.assertLessEqual(state["spent_micro"], state["ceiling_micro"])
        _expect_code(self, tc.TikTokLedgerError, "ACTOR_CEILING_REACHED",
                     led.reserve, actor=ta.ACTOR_CLOCKWORKS, run_id="run-3")

    def test_18_reconciliation_is_exactly_once(self):
        led = self._ledger()
        led.reserve(actor=ta.ACTOR_APIDOJO, run_id="run-ap")
        first = led.reconcile(actor=ta.ACTOR_APIDOJO, run_id="run-ap",
                              usage_total_usd=0.0003)
        self.assertTrue(first["applied"])
        self.assertEqual(first["actual_micro"], 300)
        self.assertEqual(first["released_micro"], 9_700)
        self.assertEqual(first["status"], tc.STATUS_RECONCILED)
        second = led.reconcile(actor=ta.ACTOR_APIDOJO, run_id="run-ap",
                               usage_total_usd=0.0003)
        self.assertFalse(second["applied"])
        self.assertTrue(second["idempotent"])
        state = led.actor_state(ta.ACTOR_APIDOJO)
        self.assertEqual(state["spent_micro"], 300)          # charged once
        self.assertEqual(state["outstanding_micro"], 0)      # released once
        self.assertEqual(state["remaining_micro"], 9_700)
        self.assertEqual(tc.actual_micro_from_usage(0.0036), 3_600)
        self.assertIsNone(tc.actual_micro_from_usage(None))

    def test_18b_run_id_binding_is_atomic_and_exactly_once(self):
        led = self._ledger()
        provisional = "tiktok:provisional:1"
        led.reserve(actor=ta.ACTOR_CLOCKWORKS, run_id=provisional)
        bound = led.bind_run_id(actor=ta.ACTOR_CLOCKWORKS,
                                provisional_run_id=provisional, apify_run_id="apify-run-9")
        self.assertTrue(bound["bound"])
        self.assertEqual(bound["run_id"], "apify-run-9")
        # The reservation is still open under the REAL run id...
        out = led.reconcile(actor=ta.ACTOR_CLOCKWORKS, run_id="apify-run-9",
                            usage_total_usd=0.0017)
        self.assertTrue(out["applied"])
        self.assertEqual(out["actual_micro"], 1_700)
        # ...and the provisional key is gone, so it cannot be reconciled twice.
        _expect_code(self, tc.TikTokLedgerError, "UNKNOWN_RUN",
                     led.reconcile, actor=ta.ACTOR_CLOCKWORKS,
                     run_id=provisional, usage_total_usd=0.0017)
        _expect_code(self, tc.TikTokLedgerError, "DUPLICATE_RUN_ID",
                     led.bind_run_id, actor=ta.ACTOR_CLOCKWORKS,
                     provisional_run_id=provisional, apify_run_id="apify-run-9")

    def test_18c_provisional_keys_are_attempt_scoped(self):
        """A refused attempt leaves a reconciled $0 row and must not block a retry."""
        led = self._ledger()
        first = led.provisional_run_id(actor=ta.ACTOR_CLOCKWORKS, content_id="c1")
        self.assertEqual(first, f"tiktok:{ta.ACTOR_CLOCKWORKS}:c1:1")
        led.reserve(actor=ta.ACTOR_CLOCKWORKS, run_id=first)
        led.reconcile(actor=ta.ACTOR_CLOCKWORKS, run_id=first,
                      usage_total_usd=0.0, succeeded=False)
        second = led.provisional_run_id(actor=ta.ACTOR_CLOCKWORKS, content_id="c1")
        self.assertEqual(second, f"tiktok:{ta.ACTOR_CLOCKWORKS}:c1:2")
        led.reserve(actor=ta.ACTOR_CLOCKWORKS, run_id=second)
        self.assertEqual(led.actor_state(ta.ACTOR_CLOCKWORKS)["outstanding_micro"], 10_000)
        self.assertEqual(led.actor_state(ta.ACTOR_CLOCKWORKS)["spent_micro"], 0)

    def test_19_missing_usage_retains_the_reservation(self):
        led = self._ledger()
        led.reserve(actor=ta.ACTOR_CLOCKWORKS, run_id="run-cw")
        out = led.reconcile(actor=ta.ACTOR_CLOCKWORKS, run_id="run-cw",
                            usage_total_usd=None)
        self.assertEqual(out["status"], tc.STATUS_COST_UNVERIFIED)
        self.assertFalse(out["verified"])
        self.assertEqual(out["released_micro"], 0)
        state = led.actor_state(ta.ACTOR_CLOCKWORKS)
        self.assertEqual(state["outstanding_micro"], 10_000)   # money stays blocked
        self.assertEqual(state["spent_micro"], 0)
        # An unverified charge blocks every further paid run, whichever actor.
        _expect_code(self, tc.TikTokLedgerError, "UNRESOLVED_RESERVATION",
                     led.reserve, actor=ta.ACTOR_APIDOJO, run_id="run-ap")

    def test_19b_an_unverified_charge_is_settled_once_it_becomes_known(self):
        led = self._ledger()
        led.reserve(actor=ta.ACTOR_APIDOJO, run_id="run-u")
        led.reconcile(actor=ta.ACTOR_APIDOJO, run_id="run-u", usage_total_usd=None)
        self.assertEqual(led.actor_state(ta.ACTOR_APIDOJO)["outstanding_micro"], 10_000)
        # Nothing moves while the charge is still unknown...
        still = led.settle_unverified(actor=ta.ACTOR_APIDOJO, run_id="run-u",
                                      usage_total_usd=None)
        self.assertFalse(still["applied"])
        self.assertEqual(still["reason"], "COST_STILL_UNVERIFIED")
        # ...and once it IS known, exactly one verified settlement releases the rest.
        out = led.settle_unverified(actor=ta.ACTOR_APIDOJO, run_id="run-u",
                                    usage_total_usd=0.0003)
        self.assertTrue(out["applied"])
        self.assertEqual(out["actual_micro"], 300)
        self.assertEqual(out["released_micro"], 9_700)
        state = led.actor_state(ta.ACTOR_APIDOJO)
        self.assertEqual(state["spent_micro"], 300)
        self.assertEqual(state["outstanding_micro"], 0)
        self.assertFalse(led.settle_unverified(actor=ta.ACTOR_APIDOJO, run_id="run-u",
                                               usage_total_usd=0.0003)["applied"])

    def test_20_combined_tiktok_ceiling_is_enforced(self):
        led = self._ledger(combined_ceiling_micro=10_000)
        led.reserve(actor=ta.ACTOR_CLOCKWORKS, run_id="run-cw")
        led.reconcile(actor=ta.ACTOR_CLOCKWORKS, run_id="run-cw",
                      usage_total_usd=0.0017)
        _expect_code(self, tc.TikTokLedgerError, "COMBINED_CEILING_REACHED",
                     led.reserve, actor=ta.ACTOR_APIDOJO, run_id="run-ap")
        # With the real $0.02 authorization both actors fit.
        led2 = self._ledger()
        led2.reserve(actor=ta.ACTOR_CLOCKWORKS, run_id="c")
        led2.reconcile(actor=ta.ACTOR_CLOCKWORKS, run_id="c", usage_total_usd=0.0017)
        led2.reserve(actor=ta.ACTOR_APIDOJO, run_id="a")
        combined = led2.combined_state()
        self.assertTrue(combined["within_ceiling"])
        self.assertLessEqual(combined["committed_micro"],
                             tc.CEILING_TIKTOK_COMBINED_MICRO)
        self.assertLessEqual(led2.actor_state(ta.ACTOR_CLOCKWORKS)["spent_micro"], 10_000)
        self.assertLessEqual(led2.actor_state(ta.ACTOR_APIDOJO)["spent_micro"], 10_000)


# ===========================================================================
# Platform-scoped download allowlist
# ===========================================================================
class _FakeBody:
    """urlopen stand-in: valid host, but an empty body (no network)."""

    status = 200
    headers = {}

    def read(self, _n=-1):
        return b""

    def close(self):
        pass


class _FakeOpener:
    """Minimal opener object (``download_one_video`` calls ``opener.open``)."""

    def open(self, req, timeout=None):
        return _FakeBody()


class TestPlatformScopedDownloadAllowlist(unittest.TestCase):
    """The downloader's CDN gate must be per-platform, not Instagram-only."""

    def test_default_gate_is_unchanged_for_instagram(self):
        import validate as v
        self.assertEqual(v.validate_media_url("https://scontent.cdninstagram.com/v/a.mp4"),
                         "https://scontent.cdninstagram.com/v/a.mp4")
        _expect_code(self, v.ValidationError, "HOST_NOT_ALLOWED",
                     v.validate_media_url, VIDEO_CDN)

    def test_tiktok_predicate_admits_the_tiktok_cdn(self):
        import validate as v
        self.assertEqual(
            v.validate_media_url(VIDEO_CDN, host_check=ta.is_approved_media_host),
            VIDEO_CDN)
        self.assertTrue(ta.is_approved_media_host("v45-eu.tiktokcdn.com"))
        self.assertTrue(ta.is_approved_media_host("v16m.tiktokcdn-us.com"))
        self.assertTrue(ta.is_approved_media_host("v45.tiktokcdn-eu.com"))

    def test_tiktok_predicate_still_refuses_lookalikes_and_foreign_hosts(self):
        import validate as v
        for bad in ("https://tiktokcdn.com.evil.com/v/a.mp4",
                    "https://v45-eu.tiktokcdn.com.evil.com/v/a.mp4",
                    "https://evil.com/v/a.mp4"):
            _expect_code(self, v.ValidationError, "HOST_NOT_ALLOWED",
                         v.validate_media_url, bad,
                         host_check=ta.is_approved_media_host)
        # Credentials are refused BEFORE the host gate, on every platform.
        _expect_code(self, v.ValidationError, "CREDENTIALS",
                     v.validate_media_url,
                     "https://user:pw@v45-eu.tiktokcdn.com/v/a.mp4",
                     host_check=ta.is_approved_media_host)

    def test_download_one_video_enforces_the_platform_predicate(self):
        """A foreign host is refused before any I/O; the TikTok CDN is admitted."""
        import acquire as acq
        import validate as v
        with tempfile.TemporaryDirectory() as d:
            _expect_code(self, v.ValidationError, "HOST_NOT_ALLOWED",
                         acq.download_one_video, "https://evil.com/v.mp4", d,
                         max_bytes=1024, timeout=5,
                         host_check=ta.is_approved_media_host)
            # Admitted by the host gate -> fails later on the empty body, which
            # proves the platform predicate took effect without touching the net.
            _expect_code(self, acq.DownloadError, "EMPTY_DOWNLOAD",
                         acq.download_one_video, VIDEO_CDN, d,
                         max_bytes=1024, timeout=5,
                         opener=_FakeOpener(),
                         host_check=ta.is_approved_media_host)

    def test_the_tiktok_media_path_binds_the_platform_predicate(self):
        """The probe's LiveIO wiring is what supplies the predicate."""
        src = (Path(__file__).resolve().parents[1] / "prove_tiktok_actor.py").read_text()
        self.assertIn("host_check=ta.is_approved_media_host", src)
        self.assertIn("functools.partial", src)


# ===========================================================================
# Media path: shared pipeline connection
# ===========================================================================
_COMPLIANT_PROBE = {
    "format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2", "duration": "9.0"},
    "streams": [
        {"codec_type": "video", "codec_name": "h264", "pix_fmt": "yuv420p",
         "width": 1080, "height": 1920, "avg_frame_rate": "30/1"},
        {"codec_type": "audio", "codec_name": "aac"},
    ],
}
_SOURCE_PROBE = {
    "format": {"format_name": "matroska,webm", "duration": "9.0"},
    "streams": [
        {"codec_type": "video", "codec_name": "hevc", "pix_fmt": "yuv420p",
         "width": 1080, "height": 1920, "avg_frame_rate": "30/1"},
        {"codec_type": "audio", "codec_name": "opus"},
    ],
}
_FASTSTART_BYTES = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 200 + b"moov" + b"mdat"


class _FakeMediaIO:
    """Deterministic stand-in for download/ffprobe/ffmpeg/finalize."""

    def __init__(self, tmp, lib, *, has_audio=True, classify_error=None):
        self.tmp_dir = str(tmp)
        self.library_dir = str(lib)
        self.downloads = []
        self._has_audio = has_audio
        self._classify_error = classify_error
        self.io = tm.TikTokMediaIO(
            download_one_video=self.download_one_video,
            classify_downloaded_file=self.classify_downloaded_file,
            run_ffprobe=lambda path: dict(_SOURCE_PROBE),
            parse_ffprobe_result=self.parse_ffprobe_result,
            checksum_sha256=self.checksum,
            finalize=self.finalize,
            cleanup_temp=self.cleanup,
            run_ffmpeg=self.run_ffmpeg,
            run_ffprobe_json=lambda path: dict(_COMPLIANT_PROBE),
            mux_builder=lambda v, a, o: ["ffmpeg", "-i", v, "-i", a, o],
            tmp_dir=str(tmp),
            library_dir=str(lib),
        )

    # -- injected effects -------------------------------------------------
    def download_one_video(self, url, tmp_dir, *, max_bytes, timeout):
        self.downloads.append(url)
        if "audio" in url and not self._has_audio:
            raise AssertionError("audio must not be fetched for this case")
        path = os.path.join(tmp_dir, f"dl{len(self.downloads)}.mp4.part")
        with open(path, "wb") as f:
            f.write(b"\x00\x00\x00\x18ftypmp42" + b"\x11" * 64)
        return path, os.path.getsize(path), 200, "video/mp4"

    def classify_downloaded_file(self, path, *, http_status, content_type, size_bytes):
        if self._classify_error:
            raise self._classify_error
        return "ok"

    def parse_ffprobe_result(self, probe, *, actual_size_bytes, max_bytes, max_duration):
        return {"duration_seconds": 9.0, "width": 1080, "height": 1920,
                "frame_rate": "30/1", "has_audio": self._has_audio,
                "container": "matroska,webm", "video_codec": "hevc"}

    @staticmethod
    def checksum(path):
        return hashlib.sha256(open(path, "rb").read()).hexdigest()

    @staticmethod
    def finalize(tmp_path, final_path):
        os.replace(tmp_path, final_path)
        return final_path

    @staticmethod
    def cleanup(path):
        try:
            os.remove(path)
        except OSError:
            pass

    @staticmethod
    def run_ffmpeg(cmd):
        out = cmd[-1]
        with open(out, "wb") as f:
            f.write(_FASTSTART_BYTES)
        return ""


def _target(**over):
    t = {"content_id": CID, "canonical_url": TARGET, "platform": "tiktok",
         "media_url": VIDEO_CDN, "audio_url": AUDIO_CDN, "kind": "video"}
    t.update(over)
    return t


class TestSharedMediaPath(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._lib = tempfile.TemporaryDirectory()
        self.tmp, self.lib = Path(self._tmp.name), Path(self._lib.name)

    def tearDown(self):
        self._tmp.cleanup()
        self._lib.cleanup()

    def test_media_normalizes_finalizes_hashes_and_records(self):
        fake = _FakeMediaIO(self.tmp, self.lib)
        persisted = {}
        out = tm.acquire_tiktok_media(
            target=_target(), io=fake.io, library_rows=[],
            persist_library=lambda rec: (persisted.update(rec), rec)[1])
        self.assertTrue(out["ok"], out)
        self.assertTrue(out["normalized"])                 # hevc/opus -> h264/aac
        self.assertEqual(out["video_codec"], "h264")
        self.assertEqual(out["audio_codec"], "aac")
        self.assertEqual(out["pix_fmt"], "yuv420p")
        self.assertEqual(len(out["asset_hash"]), 64)
        self.assertTrue(Path(out["local_path"]).is_file())
        self.assertEqual(persisted["platform_content_id"], CID)
        self.assertEqual(persisted["platform"], "tiktok")
        self.assertEqual(persisted["content_type"], "video/mp4")
        # Signed URLs never reach the persisted record.
        self.assertNotIn(VIDEO_CDN, json.dumps(persisted))
        self.assertNotIn("tiktokcdn.com", json.dumps(persisted))

    def test_media_record_omits_an_empty_uuid_job_id(self):
        """PostgREST answers 400 (22P02) for an empty string in a uuid column."""
        fake = _FakeMediaIO(self.tmp, self.lib)
        seen: dict = {}
        tm.acquire_tiktok_media(target=_target(), io=fake.io, library_rows=[],
                                persist_library=lambda rec: (seen.update(rec), rec)[1])
        self.assertNotIn("acquisition_job_id", seen)
        real = "b1bb78e6-3601-47d9-9068-46afb45587e1"
        seen2: dict = {}
        tm.acquire_tiktok_media(target=_target(job_id=real), io=fake.io,
                                library_rows=[],
                                persist_library=lambda rec: (seen2.update(rec), rec)[1])
        self.assertEqual(seen2["acquisition_job_id"], real)

    def test_media_persist_failure_preserves_the_finalized_artifact(self):
        """A failed durable row must not lose evidence of the real asset."""
        fake = _FakeMediaIO(self.tmp, self.lib)

        def boom(_rec):
            raise RuntimeError("HTTP 400")

        with self.assertRaises(tm.TikTokMediaError) as cm:
            tm.acquire_tiktok_media(target=_target(), io=fake.io, library_rows=[],
                                    persist_library=boom)
        self.assertEqual(cm.exception.code, "LIBRARY_PERSIST_FAILED")
        detail = cm.exception.detail
        self.assertTrue(detail["asset_filename"].endswith(".mp4"))
        self.assertEqual(len(detail["asset_hash"]), 64)
        self.assertTrue(Path(detail["local_path"]).is_file())
        self.assertNotIn("acquisition_job_id", detail["record"])
        self.assertNotIn("tiktokcdn.com", json.dumps(detail["record"]))

    def test_media_is_idempotent_for_the_same_canonical_source(self):
        fake = _FakeMediaIO(self.tmp, self.lib)
        rows = [{"platform_content_id": CID, "canonical_url": TARGET, "id": "row-1"}]
        out = tm.acquire_tiktok_media(target=_target(), io=fake.io, library_rows=rows)
        self.assertTrue(out["idempotent"])
        self.assertTrue(out["already_acquired"])
        self.assertEqual(out["library_row"]["id"], "row-1")
        self.assertEqual(fake.downloads, [])               # nothing was downloaded

    def test_media_rejects_html_or_error_payloads(self):
        fake = _FakeMediaIO(self.tmp, self.lib)
        fake._classify_error = type("E", (Exception,), {})("HTML_RESPONSE")
        fake._classify_error.code = "HTML_RESPONSE"
        fake._classify_error.message = "downloaded an HTML error body"
        _expect_code(self, tm.TikTokMediaError, "HTML_RESPONSE",
                     tm.acquire_tiktok_media, target=_target(), io=fake.io,
                     library_rows=[])

    def test_media_refuses_when_the_video_carries_no_audio(self):
        fake = _FakeMediaIO(self.tmp, self.lib, has_audio=False)
        _expect_code(self, tm.TikTokMediaError, "AUDIO_MISSING",
                     tm.acquire_tiktok_media,
                     target=_target(audio_url=""), io=fake.io, library_rows=[])

    def test_media_requires_a_media_url(self):
        fake = _FakeMediaIO(self.tmp, self.lib)
        _expect_code(self, tm.TikTokMediaError, "MISSING_MEDIA",
                     tm.acquire_tiktok_media,
                     target=_target(media_url=""), io=fake.io, library_rows=[])

    def test_media_refuses_to_finalize_without_a_library_root(self):
        """A missing library root must NOT silently write into the CWD."""
        fake = _FakeMediaIO(self.tmp, self.lib)
        fake.io.library_dir = ""
        _expect_code(self, tm.TikTokMediaError, "NO_LIBRARY_DIR",
                     tm.acquire_tiktok_media, target=_target(), io=fake.io,
                     library_rows=[])
        self.assertEqual(fake.downloads, [])      # refused before any download

    def test_media_enforces_the_shared_size_limit(self):
        fake = _FakeMediaIO(self.tmp, self.lib)
        _expect_code(self, tm.TikTokMediaError, "TOO_LARGE",
                     tm.acquire_tiktok_media, target=_target(), io=fake.io,
                     library_rows=[], max_bytes=8)


if __name__ == "__main__":
    unittest.main()
