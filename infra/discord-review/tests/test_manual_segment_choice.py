"""Seam tests for the Manual Intake WHOLE VIDEO / SELECT SEGMENT choice.

These drive the REAL handoffs, not re-implementations:

    operator URL (no window)
      -> the shared gate decides a window is required      (window_is_required)
      -> the choice is presented with the correct control   (_mi_present_choice)
      -> SELECT SEGMENT opens the ONE modal                 (acquisition_ops)
      -> the modal's values are read by the CANONICAL parser (parse_window_values)
      -> a SourceWindow identical to the text syntax's      (build_operator_window)
      -> acquisition is refused/allowed by that same gate   (validate_acquisition_mode)

No real network, no paid provider, no database: only the transport is stubbed, so the
contract being asserted is the production one.
"""

import json
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
for directory in ("acquisition", "discord-review"):
    sys.path.insert(0, str(ROOT / "infra" / directory))

import acquisition_ops as ao                     # noqa: E402
import adapter                                    # noqa: E402
import manual_media_intake as mmi                 # noqa: E402
import source_window as sw                        # noqa: E402

YT = "https://www.youtube.com/watch?v=59xxeJCbzaM"
YT_ID = "59xxeJCbzaM"
LONG_SOURCE_S = 467.0


class ChoicePresentationTests(unittest.TestCase):
    """The control surface must never imply an option the gate will refuse."""

    class _Recorder:
        def __init__(self):
            self.calls = []

        def _patch_original(self, application_id, interaction_token, **kw):
            self.calls.append({"app": application_id, "token": interaction_token, **kw})

    def _present(self, *, requires_window, duration=LONG_SOURCE_S):
        rec = self._Recorder()
        adapter.Handler._mi_present_choice(
            rec, "app1", "tok1", ref="ref123",
            requires_window=requires_window, source_duration_s=duration)
        return rec.calls[-1]

    def test_whole_video_is_disabled_when_a_segment_is_required(self):
        call = self._present(requires_window=True)
        row = call["components"][0]["components"]
        by_label = {c["label"]: c for c in row}
        self.assertTrue(by_label["Whole video"]["disabled"])
        self.assertFalse(by_label["Select segment"].get("disabled", False))

    def test_whole_video_is_enabled_when_the_source_may_be_taken_whole(self):
        call = self._present(requires_window=False, duration=34.0)
        row = call["components"][0]["components"]
        by_label = {c["label"]: c for c in row}
        self.assertFalse(by_label["Whole video"]["disabled"])

    def test_controls_are_the_manual_intake_namespace_only(self):
        call = self._present(requires_window=True)
        ids = [c["custom_id"] for c in call["components"][0]["components"]]
        for cid in ids:
            self.assertTrue(cid.startswith(f"{adapter.MI_CHOICE_PREFIX}:"))
            self.assertIsNotNone(adapter.parse_manual_choice_custom_id(cid))
        # ...and no source identity (URL) is exposed in an identifier.
        for cid in ids:
            self.assertNotIn("http", cid)
            self.assertNotIn(YT_ID, cid)

    def test_prompt_names_the_real_limit(self):
        call = self._present(requires_window=True)
        self.assertIn(str(sw.MS_MAX // 1000), call["content"])


class GateIsTheOneAuthorityTests(unittest.TestCase):
    def _ask(self, duration):
        return mmi.window_is_required(
            platform="youtube", content_id=YT_ID, canonical_url=YT,
            item_kind="", source_duration_s=duration,
            requires_window_for_long_source=False)

    def test_long_source_requires_a_window(self):
        self.assertTrue(self._ask(LONG_SOURCE_S))

    def test_short_source_does_not(self):
        self.assertFalse(self._ask(34.3))

    def test_unknown_duration_on_youtube_is_not_forced(self):
        self.assertFalse(self._ask(None))

    def test_long_form_platform_requires_a_window_even_when_duration_unknown(self):
        self.assertTrue(mmi.window_is_required(
            platform="twitch", content_id="2852456270",
            canonical_url="https://www.twitch.tv/videos/2852456270",
            item_kind="vod", source_duration_s=None,
            requires_window_for_long_source=True))

    def test_a_short_clip_still_acquires_whole(self):
        """The proven whole-clip lane must not be broken by the window rule."""
        self.assertFalse(mmi.window_is_required(
            platform="kick", content_id="clip_01M33XASMP29XTQ1QK0EV7TXDC",
            canonical_url="https://kick.com/x/clips/clip_01M33XASMP29XTQ1QK0EV7TXDC",
            item_kind="clip", source_duration_s=None,
            requires_window_for_long_source=True))


class ModalEqualsTextSyntaxTests(unittest.TestCase):
    """The modal and the text syntax must reach the SAME SourceWindow."""

    def _window(self, start_s, end_s):
        return mmi.build_operator_window(
            platform="youtube", canonical_url=YT, content_id=YT_ID,
            start_s=start_s, end_s=end_s, source_duration_s=LONG_SOURCE_S)

    def _text_window(self, message):
        parsed = mmi.parse_intake_message(message)
        return self._window(parsed["start_s"], parsed["end_s"])

    def _modal_window(self, start_raw, end_raw):
        start_ms, end_ms = ao.parse_window_values({
            ao.WINDOW_MODAL_START: start_raw, ao.WINDOW_MODAL_END: end_raw})
        return self._window(start_ms / 1000.0, end_ms / 1000.0)

    def test_ss_mmss_hhmmss_all_normalize_identically(self):
        for start_raw, end_raw in (("120", "180"), ("02:00", "03:00"),
                                   ("00:02:00", "00:03:00")):
            with self.subTest(start=start_raw, end=end_raw):
                self.assertEqual(self._modal_window(start_raw, end_raw),
                                 self._modal_window("120", "180"))

    def test_modal_and_text_produce_the_same_identity(self):
        modal = self._modal_window("02:00", "03:00")
        text = self._text_window(f"url: {YT}\nSTART: 120\nEND: 180")
        self.assertEqual(modal["range_fingerprint"], text["range_fingerprint"])
        self.assertEqual(modal["start_ms"], text["start_ms"])
        self.assertEqual(modal["end_ms"], text["end_ms"])
        self.assertEqual(modal["mode"], sw.MODE_RANGED)

    def test_malformed_modal_range_is_refused_by_the_canonical_parser(self):
        for bad in ({"": ""}, {ao.WINDOW_MODAL_START: "abc",
                               ao.WINDOW_MODAL_END: "180"}):
            with self.subTest(bad=bad):
                with self.assertRaises(sw.WindowError):
                    ao.parse_window_values(bad)

    def test_window_longer_than_the_limit_is_refused(self):
        # The shared contract refuses an over-limit range, and build_operator_window
        # deliberately translates that into the OPERATOR-facing class carrying the
        # original code, so the card can explain it. Assert the real contract.
        with self.assertRaises(mmi.MediaIntakeError) as ctx:
            self._window(0.0, (sw.MS_MAX / 1000.0) + 1)
        self.assertEqual(ctx.exception.code, "WINDOW_REJECTED")
        self.assertIn("RANGE_TOO_LONG", str(getattr(ctx.exception, "detail", "")))


class ModalNamespaceRoutingTests(unittest.TestCase):
    """A manual modal submit must never be routed into the acquisition lane."""

    def test_manual_and_lane_namespaces_do_not_cross(self):
        manual = ao.manual_window_modal_payload("ref1", message_id="m1")
        lane = ao.window_modal_payload("ref2", message_id="m2")
        manual_cid = manual["data"]["custom_id"]
        lane_cid = lane["data"]["custom_id"]

        self.assertEqual(
            ao.parse_window_modal_custom_id(
                manual_cid, prefix=ao.MANUAL_WINDOW_MODAL_CUSTOM_ID), ("ref1", "m1"))
        self.assertIsNone(
            ao.parse_window_modal_custom_id(
                manual_cid, prefix=ao.WINDOW_MODAL_CUSTOM_ID))
        self.assertEqual(
            ao.parse_window_modal_custom_id(lane_cid), ("ref2", "m2"))
        self.assertIsNone(
            ao.parse_window_modal_custom_id(
                lane_cid, prefix=ao.MANUAL_WINDOW_MODAL_CUSTOM_ID))

    def test_the_modal_is_the_same_shape_as_the_production_one(self):
        manual = ao.manual_window_modal_payload("ref1")["data"]
        lane = ao.window_modal_payload("ref1")["data"]
        self.assertEqual(manual["components"], lane["components"])
        self.assertEqual(manual["title"], "Select segment")
        self.assertLessEqual(len(manual["title"]), 45)


class PendingChoiceTests(unittest.TestCase):
    """Resume context: opaque ref only, expiring, single-use."""

    def test_round_trip_and_forget(self):
        ref = adapter._mi_remember_pending(
            url=YT, tenant="dreemzy", submitter="bryan", channel_id="chan1")
        self.assertTrue(ref)
        self.assertNotIn("http", ref)
        self.assertNotIn(YT_ID, ref)
        entry = adapter._mi_pending(ref)
        self.assertEqual(entry["url"], YT)
        self.assertEqual(entry["tenant"], "dreemzy")
        adapter._mi_forget_pending(ref)
        self.assertIsNone(adapter._mi_pending(ref))

    def test_unknown_ref_is_none_not_an_exception(self):
        self.assertIsNone(adapter._mi_pending("nosuchref"))


class NoAcquisitionBeforeAChoiceTests(unittest.TestCase):
    """The choice must not fetch any media."""

    def test_presenting_a_choice_runs_no_download(self):
        # The choice is built from a metadata-only probe, so no downloader exists on
        # this path at all: presenting a choice cannot acquire.
        rec = ChoicePresentationTests._Recorder()
        adapter.Handler._mi_present_choice(
            rec, "app1", "tok1", ref="ref1", requires_window=True,
            source_duration_s=LONG_SOURCE_S)
        self.assertEqual(len(rec.calls), 1)

    def test_long_form_without_a_window_creates_no_artifact(self):
        """The gate refuses BEFORE a bounded artifact could exist."""
        with self.assertRaises(sw.WindowError) as ctx:
            sw.validate_acquisition_mode(
                platform="youtube", content_id=YT_ID, canonical_url=YT,
                source_duration_s=LONG_SOURCE_S, window=None)
        self.assertEqual(ctx.exception.code, "LONG_FORM_REQUIRES_WINDOW")


class DurableStoreFailureIsRetrySafeTests(unittest.TestCase):
    """A storage failure must be its own class, and must keep the working copy."""

    def test_failure_class_is_named_not_unknown(self):
        code, reason = adapter.classify_intake_failure(
            mmi.MediaIntakeError("DURABLE_STORE_FAILED", "x", detail="y"))
        self.assertEqual(code, "DURABLE_STORE_FAILED")
        self.assertIn("re-running", reason.lower())

    def test_window_refusal_has_an_operator_sentence(self):
        code, reason = adapter.classify_intake_failure(
            mmi.MediaIntakeError("LONG_FORM_REQUIRES_WINDOW", "x"))
        self.assertEqual(code, "LONG_FORM_REQUIRES_WINDOW")
        self.assertIn("SEGMENT", reason.upper())


if __name__ == "__main__":
    unittest.main(verbosity=2)
