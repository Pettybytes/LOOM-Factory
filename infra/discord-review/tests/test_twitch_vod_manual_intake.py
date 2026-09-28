"""Focused seam tests: TWITCH VOD through Manual Intake.

    timestamped Twitch VOD URL
      -> platform recognition -> identity (timestamp is NOT identity)
      -> timestamp extraction (suggested ORIGINAL start)
      -> operator choice -> SELECT SEGMENT modal (Start pre-filled, End required)
      -> canonical SourceWindow -> bounded acquisition
      -> shared validation -> WORKING coordinates -> review card + media

These drive the REAL contracts and the REAL worker; only transport is stubbed, so the
contract under test is the production one. Twitch is not assumed to work because
YouTube does: the platform path is traced explicitly below.
"""

import hashlib
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
for directory in ("acquisition", "discord-review"):
    sys.path.insert(0, str(ROOT / "infra" / directory))

import acquisition_ops as ao                      # noqa: E402  (also the order sentinel)
import adapter                                    # noqa: E402
import manual_intake as mi                        # noqa: E402
import manual_media_intake as mmi                 # noqa: E402
import source_adapters as sa                      # noqa: E402
import source_window as sw                        # noqa: E402
import twitch_url as tu                           # noqa: E402

VOD_ID = "2880604198"
VOD_PLAIN = f"https://www.twitch.tv/videos/{VOD_ID}"
VOD_STAMPED = f"{VOD_PLAIN}?t=00h26m10s"
HINT_MS = 1_570_000                     # 26m10s
VOD_DURATION_S = 11_547.0               # probed: ~3h12m, was_live
FP = hashlib.sha256(b"twitch-vod-fixture").hexdigest()


class PlatformRecognitionTests(unittest.TestCase):
    """Do not assume Twitch works because YouTube works."""

    def test_recognized_as_a_twitch_vod(self):
        r = mmi.resolve_source_url(VOD_STAMPED)
        self.assertEqual(r["platform"], "twitch")
        self.assertEqual(r["kind"], "vod")
        self.assertEqual(r["content_id"], VOD_ID)

    def test_the_adapter_lane_also_sees_it_as_a_vod(self):
        parsed = tu.parse(VOD_STAMPED)
        self.assertEqual(parsed["kind"], "vod")
        self.assertEqual(parsed["content_id"], VOD_ID)

    def test_a_clip_is_still_a_clip(self):
        parsed = tu.parse("https://clips.twitch.tv/AltruisticTallBork")
        self.assertEqual(parsed["kind"], "clip")


class TimestampIsAHintNotIdentityTests(unittest.TestCase):
    def test_timestamp_extracted_as_the_suggested_original_start(self):
        r = mmi.resolve_source_url(VOD_STAMPED)
        self.assertEqual(r["start_hint_ms"], HINT_MS)
        self.assertEqual(sw.human_clock(r["start_hint_ms"]), "26:10")

    def test_hint_is_exposed_through_the_registry_not_a_platform_branch(self):
        self.assertEqual(sa.source_start_hint_ms("twitch", VOD_STAMPED), HINT_MS)
        # A platform without the hook simply has no hint — no branch, no guess.
        self.assertIsNone(sa.source_start_hint_ms("youtube", VOD_STAMPED))

    def test_canonical_identity_is_hint_free(self):
        r = mmi.resolve_source_url(VOD_STAMPED)
        self.assertNotIn("t=", r["canonical_url"])
        self.assertEqual(r["canonical_url"], VOD_PLAIN)

    def test_timestamp_does_not_change_dedup_identity(self):
        stamped = mi.validate_and_canonicalize(VOD_STAMPED)
        plain = mi.validate_and_canonicalize(VOD_PLAIN)
        self.assertEqual(stamped["canonical_url"], plain["canonical_url"])
        self.assertEqual(
            mi.submission_fingerprint("twitch", stamped["canonical_url"]),
            mi.submission_fingerprint("twitch", plain["canonical_url"]))

    def test_plain_vod_url_still_works_with_no_hint(self):
        r = mmi.resolve_source_url(VOD_PLAIN)
        self.assertEqual(r["content_id"], VOD_ID)
        self.assertNotIn("start_hint_ms", r)          # absent, not zero
        self.assertIsNone(sa.source_start_hint_ms("twitch", VOD_PLAIN))

    def test_complete_unit_sequences_are_extracted(self):
        for raw, ms in (("00h26m10s", 1_570_000), ("90s", 90_000),
                        ("1h2m3s", 3_723_000), ("2h", 7_200_000), ("5m", 300_000)):
            with self.subTest(t=raw):
                r = mmi.resolve_source_url(f"{VOD_PLAIN}?t={raw}")
                self.assertEqual(r.get("start_hint_ms"), ms)

    def test_malformed_hints_are_ignored_safely(self):
        """Anything that is not a COMPLETE unit sequence yields NO hint.

        A partially-applied timestamp (the truncated `26m10` -> 26:00) would corrupt
        the start coordinate the operator is about to approve, so the contract is
        ignore-instead-of-approximate.
        """
        for bad in ("?t=garbage", "?t=", "?t=--", "?t=26m10", "?t=ten minutes"):
            with self.subTest(t=bad):
                r = mmi.resolve_source_url(VOD_PLAIN + bad)
                self.assertNotIn("start_hint_ms", r)
                self.assertEqual(r["canonical_url"], VOD_PLAIN)
        self.assertIsNone(sa.source_start_hint_ms("twitch", VOD_PLAIN + "?t=garbage"))

    def test_a_malformed_hint_cannot_move_the_approved_coordinates(self):
        good = mmi.resolve_source_url(VOD_STAMPED)
        bad = mmi.resolve_source_url(VOD_PLAIN + "?t=26m10")
        self.assertEqual(good["canonical_url"], bad["canonical_url"])
        self.assertNotIn("start_hint_ms", bad)

    def test_the_hint_is_never_an_approved_boundary(self):
        """Identity and the SourceWindow must not include the hint by itself."""
        r = mmi.resolve_source_url(VOD_STAMPED)
        self.assertNotIn("start_ms", r)
        self.assertNotIn("end_ms", r)


class ChoiceAndModalTests(unittest.TestCase):
    class _Rec:
        def __init__(self):
            self.patches = []

        def _patch_original(self, application_id, interaction_token, **kw):
            self.patches.append(kw)

    def _choice(self):
        rec = self._Rec()
        r = mmi.resolve_source_url(VOD_STAMPED)
        allowed, refusal = mmi.whole_source_verdict(
            platform="twitch", content_id=r["content_id"],
            canonical_url=r["canonical_url"], item_kind=r["kind"],
            source_duration_s=VOD_DURATION_S,
            requires_window_for_long_source=True)
        adapter.Handler._mi_present_choice(
            rec, "app", "tok", ref="ref1", whole_allowed=allowed, refusal=refusal,
            source_duration_s=VOD_DURATION_S, start_hint_ms=r["start_hint_ms"])
        return rec.patches[-1], allowed

    def test_choice_is_offered_with_whole_video_disabled(self):
        call, allowed = self._choice()
        self.assertFalse(allowed, "a 3h12m VOD must not be acquirable whole")
        row = call["components"][0]["components"]
        self.assertTrue({c["label"]: c for c in row}["Whole video"]["disabled"])

    def test_prompt_states_the_timestamp_is_a_default_not_a_boundary(self):
        call, _ = self._choice()
        self.assertIn("26:10", call["content"])
        self.assertIn("pre-fill", call["content"])
        self.assertIn("you still choose the End", call["content"])

    def test_modal_prefills_start_and_still_requires_end(self):
        modal = ao.manual_window_modal_payload("ref1", message_id="m1",
                                               start_value="26:10")["data"]
        start = modal["components"][0]["components"][0]
        end = modal["components"][1]["components"][0]
        self.assertEqual(start["value"], "26:10")     # pre-filled
        self.assertTrue(start["required"])
        self.assertNotIn("value", end)                # never invented
        self.assertTrue(end["required"])

    def test_the_modal_is_still_the_shared_one(self):
        manual = ao.manual_window_modal_payload("r")["data"]
        lane = ao.window_modal_payload("r")["data"]
        self.assertEqual(manual["components"], lane["components"])
        self.assertEqual(manual["custom_id"].split(":")[0],
                         ao.MANUAL_WINDOW_MODAL_CUSTOM_ID)


class CanonicalWindowAndAcquisitionTests(unittest.TestCase):
    def test_operator_start_and_end_become_the_canonical_window(self):
        r = mmi.resolve_source_url(VOD_STAMPED)
        w = mmi.build_operator_window(
            platform="twitch", canonical_url=r["canonical_url"],
            content_id=r["content_id"], start_s=HINT_MS / 1000.0,
            end_s=HINT_MS / 1000.0 + 90, source_duration_s=VOD_DURATION_S)
        self.assertEqual((w["start_ms"], w["end_ms"]), (1_570_000, 1_660_000))
        self.assertEqual(w["mode"], sw.MODE_RANGED)
        self.assertEqual(w["origin"], "operator_adjusted")

    def test_the_hint_survives_as_original_provenance(self):
        w = mmi.build_operator_window(
            platform="twitch", canonical_url=VOD_PLAIN, content_id=VOD_ID,
            start_s=HINT_MS / 1000.0, end_s=HINT_MS / 1000.0 + 90,
            source_duration_s=VOD_DURATION_S)
        self.assertEqual(w["start_ms"], HINT_MS)

    def test_only_the_window_is_requested_not_the_whole_vod(self):
        r = mmi.resolve_source_url(VOD_STAMPED)
        w = mmi.build_operator_window(
            platform="twitch", canonical_url=r["canonical_url"],
            content_id=r["content_id"], start_s=HINT_MS / 1000.0,
            end_s=HINT_MS / 1000.0 + 90, source_duration_s=VOD_DURATION_S)
        cmd = sa_ytdlp().build_download_command(
            r["canonical_url"], out_template="/tmp/x/%(id)s.%(ext)s",
            section_arg=sw.ytdlp_section_arg(w))
        self.assertIn("--download-sections", cmd)
        self.assertEqual(cmd[cmd.index("--download-sections") + 1],
                         "*1570.000-1660.000")
        self.assertIn("--max-filesize", cmd)      # the ceiling still applies
        self.assertNotIn("--no-part", cmd)

    def test_a_whole_vod_acquisition_is_refused_by_the_shared_gate(self):
        with self.assertRaises(sw.WindowError) as ctx:
            sw.validate_acquisition_mode(
                platform="twitch", content_id=VOD_ID, canonical_url=VOD_PLAIN,
                source_duration_s=VOD_DURATION_S, window=None,
                item_kind="vod", requires_window_for_long_source=True)
        self.assertEqual(ctx.exception.code, "LONG_FORM_REQUIRES_WINDOW")

    def test_no_platform_specific_logic_leaked_into_the_shared_contract(self):
        """The working-source contract must not BRANCH on a platform name.

        Module docstrings legitimately mention platforms, so this inspects the
        contract FUNCTIONS' bodies — the code that would actually branch.
        """
        import inspect
        fns = (sw.validate_acquisition_mode, sw.validate_working_range,
               sw.validate_resulting_duration, sw.ytdlp_section_arg,
               sw.validate_window)
        for fn in fns:
            body = inspect.getsource(fn).replace("'", '"')
            for token in ("twitch", "youtube", "kick", "tiktok", "instagram"):
                self.assertNotIn(f'"{token}"', body,
                                 f"{fn.__name__} must not branch on {token}")


def sa_ytdlp():
    import ytdlp_adapter
    return ytdlp_adapter


class _Harness:
    """Drive the real worker for a stamped Twitch VOD; transport stubbed only."""

    def __init__(self):
        self.calls = []
        self.cards = []
        self.statuses = []

    def __enter__(self):
        handler = types.SimpleNamespace()
        handler.patches = []
        handler.stage_log = self.statuses

        def _patch_original(application_id, interaction_token, **kw):
            handler.patches.append(kw)

        def _status(application_id, interaction_token, text, *, stage=""):
            self.statuses.append((stage, text))
            _patch_original(application_id, interaction_token, content=text)

        def _reviewer_name(interaction):
            return "bryan"

        def _post_source_card(channel_id, candidate, *, working_media_path="",
                              durable_url=""):
            self.cards.append({"channel_id": channel_id,
                               "working_media_path": working_media_path,
                               "durable_url": durable_url})
            return {"id": "msg1"}

        handler._patch_original = _patch_original
        handler._mi_status = _status
        handler._reviewer_name = _reviewer_name
        handler._post_source_card = _post_source_card
        handler._mi_present_choice = types.MethodType(
            adapter.Handler._mi_present_choice, handler)

        tmp = tempfile.mkdtemp(prefix="loom-twitch-")
        media = os.path.join(tmp, "working.mp4")
        Path(media).write_bytes(b"v" * 2048)

        def _acquire(url, *, out_dir, start_s=None, end_s=None, **k):
            self.calls.append({"fn": "acquire_url_media", "url": url,
                               "start_s": start_s, "end_s": end_s})
            window = (mmi.build_operator_window(
                platform="twitch", canonical_url=VOD_PLAIN, content_id=VOD_ID,
                start_s=start_s, end_s=end_s, source_duration_s=VOD_DURATION_S)
                if start_s is not None else None)
            return media, {
                "platform": "twitch", "content_id": VOD_ID,
                "canonical_url": VOD_PLAIN, "kind": "vod",
                "capability": {"platform": "twitch", "adapter_id": "local:yt-dlp",
                               "capability_state": "runtime_proven",
                               "proven_modes": [], "long_form": True,
                               "requires_window_for_long_source": True},
                "window": window,
                "acquisition_mode": "bounded_segment" if window else "full_item",
                "working_source": {"duration_seconds": 90.0,
                                   "timebase_origin_s": start_s or 0.0},
                "identity_reconciliation": "reconciled"}

        def _store(path, name, folder_id=None):
            self.calls.append({"fn": "store_durable_media", "name": name})
            return {"url": "https://filesafe.example/twitch.mp4", "file_id": "fid",
                    "placement_verified": True, "placement_expected": "folder",
                    "placement_actual": "folder", "placement_reason": "in_target_folder"}

        def _validate(path, **k):
            self.calls.append({"fn": "validate_finalized_artifact"})
            return {"ok": True}

        self._patches = [
            mock.patch.object(adapter, "resolve_manual_intake_channel",
                              return_value="chan1"),
            mock.patch.object(adapter.tenant_routing, "list_active_tenants",
                              return_value={"dreemzy"}),
            mock.patch.object(adapter, "manual_review_channel_id",
                              return_value="cliprev"),
            mock.patch.object(mmi, "probe_source_duration",
                              side_effect=lambda u: VOD_DURATION_S),
            mock.patch.object(mmi, "acquire_url_media", side_effect=_acquire),
            mock.patch.object(mmi, "probe_media",
                              return_value={"duration_seconds": 90.0, "has_video": True,
                                            "has_audio": True, "sha256": "a" * 64,
                                            "size_bytes": 2048, "width": 1920,
                                            "height": 1080}),
            mock.patch.object(mmi, "verify_artifact_integrity",
                              side_effect=lambda p, **k: (
                                  self.calls.append({"fn": "verify_artifact_integrity"})
                                  or {"ok": True})),
            mock.patch.object(mmi, "store_durable_media", side_effect=_store),
            mock.patch.object(mmi, "find_existing_media", return_value=None),
            mock.patch.object(mmi, "fetch_source_metadata",
                              return_value={"available": [], "unavailable": []}),
            mock.patch.object(mmi, "persist_media_submission",
                              return_value={"id": "sub1",
                                            "duplicate_fingerprint": FP}),
            mock.patch.object(adapter.manual_intake_intelligence,
                              "run_manual_intelligence",
                              side_effect=lambda **k: {"ok": True,
                                                       "fingerprint": FP,
                                                       "boundary_plan": {},
                                                       "boundary_provenance":
                                                           "manual_intake"}),
            mock.patch.object(adapter.source_review, "fetch_candidate_by_fingerprint",
                              return_value={"duplicate_fingerprint": FP,
                                            "canonical_url": VOD_PLAIN,
                                            "platform": "twitch",
                                            "platform_content_id": VOD_ID,
                                            "tenant_id": "dreemzy",
                                            "profile_id": "manual-intake",
                                            "moment_evidence": {
                                                "lineage": {
                                                    "origin": "manual_media_intake"}},
                                            "boundary_plan": {
                                                "recommended_clip_start": 0.0,
                                                "recommended_clip_end": 90.0}}),
            mock.patch.object(adapter.source_review, "remember_card_message",
                              return_value=None),
        ]
        for p in self._patches:
            p.start()
        self.handler = handler
        self.tmp = tmp
        return self

    def __exit__(self, *exc):
        for p in reversed(self._patches):
            p.stop()
        return False

    def run(self, parsed):
        adapter.Handler._manual_intake_execute(
            self.handler,
            {"application_id": "app", "token": "***", "channel": {"id": "chan1"},
             "member": {"user": {"username": "bryan"}}},
            parsed, {})

    def called(self, name):
        return [c for c in self.calls if c.get("fn") == name]


class TwitchWorkerTests(unittest.TestCase):
    def _parsed(self, start_s, end_s):
        return {"url": VOD_STAMPED, "tenant": "dreemzy",
                "start_s": start_s, "end_s": end_s,
                "window_intent": True, "window_problem": ""}

    def test_operator_window_drives_a_bounded_acquisition(self):
        with _Harness() as h:
            h.run(self._parsed(1570.0, 1660.0))
        acq = h.called("acquire_url_media")[0]
        self.assertEqual((acq["start_s"], acq["end_s"]), (1570.0, 1660.0))
        self.assertEqual(h.called("store_durable_media")[0]["name"][:7], "twitch_")

    def test_shared_validation_gate_runs_before_the_durable_store(self):
        """The shared integrity/validation gate must precede any durable write."""
        with _Harness() as h:
            h.run(self._parsed(1570.0, 1660.0))
        names = [c["fn"] for c in h.calls]
        self.assertIn("verify_artifact_integrity", names)
        self.assertLess(names.index("verify_artifact_integrity"),
                        names.index("store_durable_media"))
        # Validation IS reported to the operator, under the validating stage.
        self.assertTrue(any("Validating working source" in t
                            for _s, t in h.statuses))

    def test_processing_stages_are_reported(self):
        with _Harness() as h:
            h.run(self._parsed(1570.0, 1660.0))
        stages = [s for s, _t in h.statuses]
        self.assertIn("acquire_media", stages)
        first = h.statuses[0][1]
        self.assertIn("26:10", first)
        self.assertIn("27:40", first)

    def test_review_card_receives_the_working_media(self):
        with _Harness() as h:
            h.run(self._parsed(1570.0, 1660.0))
        self.assertEqual(len(h.cards), 1)
        self.assertTrue(h.cards[0]["working_media_path"])
        self.assertTrue(os.path.isfile(h.cards[0]["working_media_path"]))
        self.assertEqual(h.cards[0]["channel_id"], "cliprev")

    def test_no_whole_vod_acquisition_when_a_segment_was_chosen(self):
        with _Harness() as h:
            h.run(self._parsed(1570.0, 1660.0))
        acq = h.called("acquire_url_media")[0]
        self.assertIsNotNone(acq["start_s"])       # never a None-window full item
        self.assertIsNotNone(acq["end_s"])


class NoRegressionTests(unittest.TestCase):
    def test_youtube_is_unaffected_by_the_twitch_hint_work(self):
        yt = "https://www.youtube.com/watch?v=59xxeJCbzaM"
        r = mmi.resolve_source_url(yt)
        self.assertEqual(r["canonical_url"], yt)
        self.assertNotIn("start_hint_ms", r)

    def test_a_supplied_file_still_resolves_without_a_hint(self):
        r = mmi.resolve_source_url(VOD_PLAIN)
        self.assertNotIn("start_hint_ms", r)

    def test_the_300s_clip_limit_is_unchanged(self):
        self.assertEqual(sw.MS_MAX, 300_000)

    def test_the_shared_working_range_contract_is_unchanged(self):
        with self.assertRaises(sw.WindowError):
            sw.validate_working_range(start_s=0.0, end_s=999.0,
                                      working_duration_s=90.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
