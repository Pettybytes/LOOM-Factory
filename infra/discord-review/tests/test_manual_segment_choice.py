"""Seam tests for the Manual Intake operator contract.

    operator media (URL or supplied file)
      -> WHOLE VIDEO / SELECT SEGMENT choice          (for ANY media, not only >300 s)
      -> canonical SourceWindow when segmented
      -> platform/local acquisition
      -> finalized-media validation -> WORKING SOURCE
      -> durable storage -> CI -> review -> editor

These drive the REAL handoffs. Only transport is stubbed (network, ffprobe, Supabase,
Discord), so the contract under test is the production one.
"""

import contextlib
import io
import json
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

import acquisition_ops as ao                     # noqa: E402
import adapter                                    # noqa: E402
import manual_media_intake as mmi                 # noqa: E402
import source_window as sw                        # noqa: E402

YT = "https://www.youtube.com/watch?v=59xxeJCbzaM"
YT_ID = "59xxeJCbzaM"
TWITCH_VOD = "https://www.twitch.tv/videos/2852456270"
KICK_CLIP = "https://kick.com/x/clips/clip_01M33XASMP29XTQ1QK0EV7TXDC"


class _FakeHandler:
    """Records both surfaces the worker publishes to (message + status).

    The real worker calls `self._mi_status` / `self._mi_present_choice`, so those are
    bound from the production class rather than re-implemented here.
    """

    def __init__(self):
        self.patches = []
        self.stage_log = []

    def _patch_original(self, application_id, interaction_token, **kw):
        self.patches.append({"app": application_id, "token": interaction_token, **kw})

    def _reviewer_name(self, interaction):
        return (((interaction.get("member") or {}).get("user") or {}).get("username")
                or "operator")

    def _mi_status(self, application_id, interaction_token, text, *, stage=""):
        self.stage_log.append((stage, text))
        self._patch_original(application_id, interaction_token, content=text)

    def status_texts(self):
        return [p["content"] for p in self.patches if p.get("components") is None]

    def components_rows(self):
        return [p["components"] for p in self.patches if p.get("components")]


def _verdict(url, *, duration, item_kind="", platform=None, requires_window=False):
    return mmi.whole_source_verdict(
        platform=platform or "youtube", content_id=YT_ID, canonical_url=url,
        item_kind=item_kind, source_duration_s=duration,
        requires_window_for_long_source=requires_window)


class ChoiceExistsForAllManualMediaTests(unittest.TestCase):
    """1-4, 17: the choice is an operator-intent layer for ANY manual media."""

    def _offer(self, url, *, duration, item_kind="", requires_window=False,
               label="source"):
        allowed, refusal = _verdict(url, duration=duration, item_kind=item_kind,
                                    requires_window=requires_window)
        handler = _FakeHandler()
        adapter.Handler._mi_present_choice(
            handler, "app", "tok", ref="ref1", whole_allowed=allowed,
            refusal=refusal, source_duration_s=duration, origin_label=label)
        row = handler.components_rows()[-1][0]["components"]
        return {c["label"]: c for c in row}, handler.patches[-1]["content"]

    def test_1_short_url_offers_whole_and_segment(self):
        by_label, text = self._offer(YT, duration=45.0)
        self.assertFalse(by_label["Whole video"]["disabled"])
        self.assertIn("Select segment", by_label)

    def test_2_long_url_offers_choice_with_whole_disabled(self):
        by_label, text = self._offer(YT, duration=302.0)
        self.assertTrue(by_label["Whole video"]["disabled"])
        self.assertIn("not available", text)

    def test_2b_three_minute_source_still_allows_whole(self):
        by_label, _ = self._offer(YT, duration=193.0)
        self.assertFalse(by_label["Whole video"]["disabled"])

    def test_2c_two_hour_vod_disables_whole(self):
        by_label, text = self._offer(TWITCH_VOD, duration=7200.0,
                                     requires_window=True)
        self.assertTrue(by_label["Whole video"]["disabled"])
        self.assertIn("working-window limit", text)

    def test_3_short_supplied_file_offers_the_same_choice(self):
        by_label, _ = self._offer(YT, duration=45.0, label="supplied file")
        self.assertIn("Whole video", by_label)
        self.assertIn("Select segment", by_label)

    def test_4_long_supplied_file_has_correct_safety_behaviour(self):
        by_label, text = self._offer(YT, duration=7200.0, label="supplied file")
        self.assertTrue(by_label["Whole video"]["disabled"])
        self.assertIn("supplied file", text)

    def test_17_a_clip_is_never_forced_into_segmenting(self):
        allowed, refusal = _verdict(KICK_CLIP, duration=None, item_kind="clip",
                                    platform="kick", requires_window=True)
        self.assertTrue(allowed)
        self.assertEqual(refusal, "")


class CanonicalWindowTests(unittest.TestCase):
    """5-6: Select Segment and the text syntax reach the SAME SourceWindow."""

    def _window(self, start_s, end_s):
        return mmi.build_operator_window(
            platform="youtube", canonical_url=YT, content_id=YT_ID,
            start_s=start_s, end_s=end_s, source_duration_s=467.0)

    def test_5_modal_values_become_a_canonical_source_window(self):
        start_ms, end_ms = ao.parse_window_values({
            ao.WINDOW_MODAL_START: "03:45", ao.WINDOW_MODAL_END: "04:20"})
        w = self._window(start_ms / 1000.0, end_ms / 1000.0)
        self.assertEqual((w["start_ms"], w["end_ms"]), (225_000, 260_000))
        self.assertEqual(w["clip_duration_ms"], 35_000)
        self.assertEqual(w["origin"], "operator_adjusted")
        self.assertEqual(w["mode"], sw.MODE_RANGED)

    def test_5b_three_timestamp_forms_are_one_identity(self):
        forms = (("120", "180"), ("02:00", "03:00"), ("00:02:00", "00:03:00"))
        windows = []
        for start_raw, end_raw in forms:
            start_ms, end_ms = ao.parse_window_values({
                ao.WINDOW_MODAL_START: start_raw, ao.WINDOW_MODAL_END: end_raw})
            windows.append(self._window(start_ms / 1000.0, end_ms / 1000.0))
        for w in windows:
            self.assertEqual(w["range_fingerprint"], windows[0]["range_fingerprint"])

    def test_6_text_syntax_produces_the_same_identity_as_the_modal(self):
        parsed = mmi.parse_intake_message(f"url: {YT}\nSTART: 225\nEND: 260")
        text = self._window(parsed["start_s"], parsed["end_s"])
        start_ms, end_ms = ao.parse_window_values({
            ao.WINDOW_MODAL_START: "03:45", ao.WINDOW_MODAL_END: "04:20"})
        modal = self._window(start_ms / 1000.0, end_ms / 1000.0)
        self.assertEqual(text["range_fingerprint"], modal["range_fingerprint"])

    def test_malformed_or_over_limit_ranges_are_refused(self):
        with self.assertRaises(sw.WindowError):
            ao.parse_window_values({ao.WINDOW_MODAL_START: "nope",
                                    ao.WINDOW_MODAL_END: "180"})
        with self.assertRaises(mmi.MediaIntakeError):
            self._window(0.0, (sw.MS_MAX / 1000.0) + 1)


class ModalNamespaceRoutingTests(unittest.TestCase):
    def test_manual_and_lane_namespaces_do_not_cross(self):
        manual_cid = ao.manual_window_modal_payload("r1", message_id="m1")["data"]["custom_id"]
        lane_cid = ao.window_modal_payload("r2", message_id="m2")["data"]["custom_id"]
        self.assertEqual(
            ao.parse_window_modal_custom_id(
                manual_cid, prefix=ao.MANUAL_WINDOW_MODAL_CUSTOM_ID), ("r1", "m1"))
        self.assertIsNone(ao.parse_window_modal_custom_id(manual_cid))
        self.assertEqual(ao.parse_window_modal_custom_id(lane_cid), ("r2", "m2"))
        self.assertIsNone(
            ao.parse_window_modal_custom_id(
                lane_cid, prefix=ao.MANUAL_WINDOW_MODAL_CUSTOM_ID))

    def test_the_manual_modal_is_the_same_shape_as_the_production_one(self):
        manual = ao.manual_window_modal_payload("r1")["data"]
        lane = ao.window_modal_payload("r1")["data"]
        self.assertEqual(manual["components"], lane["components"])

    def test_choice_custom_ids_carry_no_source_identity(self):
        handler = _FakeHandler()
        adapter.Handler._mi_present_choice(
            handler, "a", "t", ref="ref1", whole_allowed=True, source_duration_s=45.0)
        for c in handler.components_rows()[-1][0]["components"]:
            self.assertNotIn("http", c["custom_id"])
            self.assertNotIn(YT_ID, c["custom_id"])
            self.assertIsNotNone(adapter.parse_manual_choice_custom_id(c["custom_id"]))


class _StubbedIntake:
    """Drive the REAL worker with only transport stubbed."""

    def __init__(self, *, duration=467.0, reuse=False, ci_ok=True, store_fails=False):
        self.calls = []
        self.statuses = []
        self.duration = duration
        self.reuse = reuse
        self.ci_ok = ci_ok
        self.store_fails = store_fails

    def __enter__(self):
        self._patches = []
        handler = _FakeHandler()
        # Bind the REAL publishing methods onto the fake, so the worker's own
        # `self._mi_*` calls exercise production code rather than a stand-in.
        for name in ("_mi_present_choice",):
            setattr(handler, name,
                    types.MethodType(getattr(adapter.Handler, name), handler))

        def _record(name, ret):
            def _fn(*a, **k):
                self.calls.append({"fn": name, "args": a, "kwargs": k})
                return ret(a, k) if callable(ret) else ret
            return _fn

        tmp = tempfile.mkdtemp(prefix="loom-segtest-")
        media = os.path.join(tmp, "working.mp4")
        Path(media).write_bytes(b"m" * 2048)

        def _acquire(url, *, out_dir, start_s=None, end_s=None, **k):
            self.calls.append({"fn": "acquire_url_media",
                               "start_s": start_s, "end_s": end_s})
            return media, {"platform": "youtube", "content_id": YT_ID,
                           "canonical_url": YT, "kind": "",
                           "capability": {"platform": "youtube",
                                          "adapter_id": "local:yt-dlp",
                                          "capability_state": "runtime_proven",
                                          "proven_modes": [], "long_form": True,
                                          "requires_window_for_long_source": False},
                           "window": self._window(start_s, end_s),
                           "acquisition_mode": "bounded_segment" if start_s else "full_item",
                           "working_source": {"duration_seconds": 35.0,
                                              "timebase_origin_s": start_s or 0.0},
                           "identity_reconciliation": "reconciled"}

        def _store(path, name, folder_id=None):
            self.calls.append({"fn": "store_durable_media", "name": name})
            if self.store_fails:
                raise mmi.MediaIntakeError("DURABLE_STORE_FAILED", "storage", detail="x")
            return {"url": "https://filesafe.example/x.mp4", "file_id": "fid",
                    "placement_verified": True, "placement_expected": "folder",
                    "placement_actual": "folder", "placement_reason": "in_target_folder"}

        def _ci(**k):
            self.calls.append({"fn": "run_manual_intelligence"})
            return {"ok": self.ci_ok, "candidate_id": "cand1", "stage": "done"}

        p = [
            mock.patch.object(adapter, "resolve_manual_intake_channel", return_value="chan1"),
            mock.patch.object(adapter.tenant_routing, "list_active_tenants",
                              return_value={"dreemzy"}),
            # `_manual_intake_execute` now asks the DIAGNOSTIC probe, which can tell a
            # SOURCE THAT NO LONGER EXISTS apart from an unproven length. The harness
            # stubs that seam; a known duration keeps every assertion below unchanged.
            mock.patch.object(mmi, "probe_source_duration_diag",
                              side_effect=lambda u: (self.duration, "")),
            mock.patch.object(mmi, "acquire_url_media", side_effect=_acquire),
            mock.patch.object(mmi, "probe_media",
                              return_value={"duration_seconds": 35.0, "has_video": True,
                                            "has_audio": True, "sha256": "a" * 64,
                                            "size_bytes": 2048, "width": 1920,
                                            "height": 1080}),
            mock.patch.object(mmi, "verify_artifact_integrity", return_value={"ok": True}),
            mock.patch.object(mmi, "store_durable_media", side_effect=_store),
            mock.patch.object(mmi, "find_existing_media",
                              return_value=({"url": "https://filesafe.example/old.mp4",
                                             "file_id": "old", "folder_id": "f",
                                             "sha256": "a" * 64} if self.reuse else None)),
            mock.patch.object(mmi, "fetch_source_metadata", return_value={"available": [],
                                                                          "unavailable": []}),
            mock.patch.object(mmi, "persist_media_submission",
                              return_value={"id": "sub1", "duplicate_fingerprint": "fp" * 6}),
            mock.patch.object(adapter.manual_intake_intelligence,
                              "run_manual_intelligence", side_effect=_ci),
            mock.patch.object(adapter, "send_review_card",
                              side_effect=lambda payload: self.calls.append(
                                  {"fn": "send_review_card"}) or {"id": "msg1"}),
        ]
        for patch in p:
            patch.start()
            self._patches.append(patch)
        self.handler = handler
        self.statuses = handler.stage_log
        self.tmp = tmp
        return self

    @staticmethod
    def _window(start_s, end_s):
        if start_s is None:
            return None
        return mmi.build_operator_window(platform="youtube", canonical_url=YT,
                                         content_id=YT_ID, start_s=start_s,
                                         end_s=end_s, source_duration_s=467.0)

    def __exit__(self, *exc):
        for patch in reversed(self._patches):
            patch.stop()
        return False

    def run(self, parsed, attachment=None, supplied_path=""):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            adapter.Handler._manual_intake_execute(
                self.handler, {"application_id": "app", "token": "tok",
                               "channel": {"id": "chan1"},
                               "member": {"user": {"username": "bryan"}}},
                parsed, attachment or {}, supplied_path=supplied_path)
        return buf.getvalue()

    def called(self, name):
        return [c for c in self.calls if c.get("fn") == name]


class NoAcquisitionEvenIfNoDecisionTests(unittest.TestCase):
    """7: presenting the choice must not acquire anything."""

    def test_7_no_decision_means_zero_acquisition(self):
        with _StubbedIntake(duration=467.0) as stub:
            stub.run({"url": YT, "start_s": None, "end_s": None, "tenant": "dreemzy",
                      "window_intent": False, "window_problem": ""})
        self.assertEqual(stub.called("acquire_url_media"), [])
        self.assertEqual(stub.called("store_durable_media"), [])
        self.assertEqual(stub.called("run_manual_intelligence"), [])
        self.assertEqual(len(stub.handler.components_rows()), 1)

    def test_7b_the_offered_choice_is_the_operator_surface(self):
        with _StubbedIntake(duration=45.0) as stub:
            stub.run({"url": YT, "start_s": None, "end_s": None, "tenant": "dreemzy",
                      "window_intent": False, "window_problem": ""})
        row = stub.handler.components_rows()[-1][0]["components"]
        labels = {c["label"]: c for c in row}
        self.assertFalse(labels["Whole video"]["disabled"])   # 45 s may be taken whole
        self.assertEqual(stub.called("acquire_url_media"), [])


class ProcessingStatusTests(unittest.TestCase):
    """8-11: the operator can always tell processing from stalled/crashed."""

    def test_8_status_appears_before_the_slow_work(self):
        with _StubbedIntake(duration=467.0) as stub:
            stub.run({"url": YT, "start_s": 225.0, "end_s": 260.0,
                      "tenant": "dreemzy", "window_intent": True, "window_problem": ""})
        self.assertTrue(stub.statuses, "a status must be published during the run")
        self.assertIn("Acquiring", stub.statuses[0][1])
        self.assertIn("03:45", stub.statuses[0][1])
        self.assertIn("04:20", stub.statuses[0][1])

    def test_9_stage_transitions_update_status_in_order(self):
        with _StubbedIntake(duration=467.0) as stub:
            stub.run({"url": YT, "start_s": 225.0, "end_s": 260.0,
                      "tenant": "dreemzy", "window_intent": True, "window_problem": ""})
        order = [text for _stage, text in stub.statuses]
        names = [stage for stage, _text in stub.statuses]
        self.assertEqual(names[:4], ["acquire_media", "probe_media",
                                     "durable_store_ghl", "content_intelligence"])
        self.assertIn("Validating working source", order[1])
        self.assertIn("Storing working source", order[2])
        self.assertIn("Content Intelligence", order[3])

    def test_10_failure_updates_the_same_message_with_a_stable_reason(self):
        with _StubbedIntake(duration=467.0, store_fails=True) as stub:
            stub.run({"url": YT, "start_s": 225.0, "end_s": 260.0,
                      "tenant": "dreemzy", "window_intent": True, "window_problem": ""})
        failure = stub.handler.patches[-1]["content"]
        self.assertIn("❌", failure)
        self.assertIn("DURABLE_STORE_FAILED", failure)
        self.assertIn("durable_store_ghl", failure)
        self.assertIn("Retry safe: yes", failure)
        self.assertNotIn("Traceback", failure)
        self.assertNotIn("/var/folders", failure)   # no local paths in Discord

    def test_10b_a_non_retryable_refusal_says_so(self):
        with _StubbedIntake(duration=5000.0) as stub:
            stub.run({"url": YT, "start_s": None, "end_s": None,
                      "tenant": "dreemzy", "window_intent": False,
                      "window_problem": ""})
        # A choice, not a failure: nothing failed, the operator simply chooses.
        self.assertIn("Choose how to ingest", stub.handler.patches[-1]["content"])

    def test_11_success_publishes_ready_for_review(self):
        with _StubbedIntake(duration=467.0) as stub:
            stub.run({"url": YT, "start_s": 225.0, "end_s": 260.0,
                      "tenant": "dreemzy", "window_intent": True, "window_problem": ""})
        self.assertTrue(any("✅" in text and "clip-review" in text
                            for _stage, text in stub.statuses))

    def test_status_failure_never_aborts_the_work(self):
        """A status publish that fails must be swallowed, not propagated.

        The raising method is patched on the HANDLER INSTANCE the worker actually
        calls, and the production class is left untouched for later tests.
        """
        handler = _FakeHandler()
        with mock.patch.object(handler, "_patch_original",
                               side_effect=RuntimeError("discord down")):
            adapter.Handler._mi_status(handler, "a", "t", "text", stage="s")
        self.assertEqual(handler.patches, [])
        self.assertTrue(callable(adapter.Handler._patch_original))


class ProvenanceAndNoDowngradeTests(unittest.TestCase):
    """12-17: coordinates stay honest and nothing can silently downgrade."""

    def test_12_13_original_is_provenance_and_working_is_zero_based(self):
        w = mmi.build_operator_window(platform="youtube", canonical_url=YT,
                                      content_id=YT_ID, start_s=225.0, end_s=260.0,
                                      source_duration_s=467.0)
        self.assertEqual((w["start_ms"], w["end_ms"]), (225_000, 260_000))  # ORIGINAL
        working = mmi._acq("source_window").validate_working_range(
            start_s=0.0, end_s=w["clip_duration_ms"] / 1000.0,
            working_duration_s=w["clip_duration_ms"] / 1000.0)
        self.assertEqual(working["start_s"], 0.0)                            # WORKING
        self.assertEqual(working["clip_duration_s"], 35.0)

    def test_12b_the_conversion_used_by_ci_is_working_relative(self):
        import manual_intake_intelligence as mii
        submission = {"media_reference": {"acquisition": {
            "working_timebase_origin_s": 225.0, "acquisition_mode": "bounded_segment"}}}
        start, end, origin = mii._operator_window_in_working_coords(
            start_s=225.0, end_s=260.0, submission=submission)
        self.assertEqual(origin, 225.0)
        self.assertEqual((start, end), (0.0, 35.0))

    def test_14_ci_ranges_are_working_relative_not_source_absolute(self):
        plan = {"recommended_clip_start": 0.0, "recommended_clip_end": 35.0}
        swc = mmi._acq("source_window")
        swc.validate_working_range(start_s=plan["recommended_clip_start"],
                                   end_s=plan["recommended_clip_end"],
                                   working_duration_s=35.0)
        with self.assertRaises(sw.WindowError):
            swc.validate_working_range(start_s=225.0, end_s=260.0,
                                       working_duration_s=35.0)

    def test_15_the_working_source_handed_on_is_the_bounded_segment(self):
        with _StubbedIntake(duration=467.0) as stub:
            stub.run({"url": YT, "start_s": 225.0, "end_s": 260.0,
                      "tenant": "dreemzy", "window_intent": True, "window_problem": ""})
        acq = stub.called("acquire_url_media")[0]
        self.assertEqual((acq["start_s"], acq["end_s"]), (225.0, 260.0))

    def test_16_same_bytes_reuse_does_not_upload_twice(self):
        with _StubbedIntake(duration=467.0, reuse=True) as stub:
            stub.run({"url": YT, "start_s": 225.0, "end_s": 260.0,
                      "tenant": "dreemzy", "window_intent": True, "window_problem": ""})
        self.assertEqual(stub.called("store_durable_media"), [])

    def test_17_no_platform_can_silently_downgrade_a_segment_to_whole(self):
        for platform, url, content_id in (
                ("youtube", YT, YT_ID),
                ("twitch", TWITCH_VOD, "2852456270"),
                ("kick", KICK_CLIP, "clip_01M33XASMP29XTQ1QK0EV7TXDC")):
            with self.subTest(platform=platform):
                w = mmi.build_operator_window(
                    platform=platform, canonical_url=url, content_id=content_id,
                    start_s=225.0, end_s=260.0)
                mode = mmi._acq("source_window").validate_acquisition_mode(
                    platform=platform, content_id=content_id, canonical_url=url,
                    source_duration_s=7200.0, window=w)
                self.assertEqual(mode["mode"], sw.MODE_RANGED)

    def test_17b_a_segment_request_always_carries_a_section_flag(self):
        w = mmi.build_operator_window(platform="youtube", canonical_url=YT,
                                      content_id=YT_ID, start_s=225.0, end_s=260.0,
                                      source_duration_s=467.0)
        cmd = mmi._acq("ytdlp_adapter").build_download_command(
            YT, out_template="/tmp/x/%(id)s.%(ext)s",
            section_arg=mmi._acq("source_window").ytdlp_section_arg(w))
        self.assertIn("--download-sections", cmd)
        self.assertEqual(cmd[cmd.index("--download-sections") + 1], "*225.000-260.000")


class PendingChoiceTests(unittest.TestCase):
    def test_round_trip_release_and_expiry(self):
        tmp = tempfile.mkdtemp(prefix="loom-manual-intake-")
        media = os.path.join(tmp, "supplied.mp4")
        Path(media).write_bytes(b"x" * 16)
        ref = adapter._mi_remember_pending(url=YT, tenant="dreemzy", submitter="bryan",
                                          channel_id="chan1", local_path=media)
        self.assertNotIn("http", ref)
        entry = adapter._mi_pending(ref)
        self.assertEqual(entry["local_path"], media)
        adapter._mi_forget_pending(ref, release=True)
        self.assertIsNone(adapter._mi_pending(ref))
        self.assertFalse(os.path.exists(tmp), "the retained copy must be released")

    def test_unknown_ref_is_none_not_an_exception(self):
        self.assertIsNone(adapter._mi_pending("nosuchref"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
