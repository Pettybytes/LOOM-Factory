"""Seam tests: a RESUMED WHOLE VIDEO choice must reach acquisition, not re-offer.

The 2026-09-29 live defect (YouTube Short `U0cN4rdLxk4`):

    intake_received -> executor_start(resumed=false) -> choice_offered   ref=UtRWsYoIrS
    choice_click_received action=whole  ref=UtRWsYoIrS resume_state=present
    resume_resolved       action=whole  ref=UtRWsYoIrS
    executor_start(resumed=true)  -> choice_offered                      ref=VeTxX3jBb8

The click reached the backend and the ref resolved, but the executor could not tell a
RESUMED whole choice from a FRESH windowless submission, so it re-entered the
"no explicit intent -> offer the choice" branch and re-presented the chooser. Whole
Video could therefore never reach acquisition at all: the operator saw the chooser
come back and no progress message.

These tests drive the REAL choice worker, the REAL executor and the REAL gate. Only
transport (channel lookup, tenant store, download, durable upload, ffprobe) is stubbed,
so the contract under test is the production one.

Run:  python3 -m unittest discover -s infra/discord-review/tests -v
"""

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
for _p in (str(ROOT / "infra" / "discord-review"), str(ROOT / "infra"),
           str(ROOT / "infra" / "acquisition")):
    sys.path.insert(0, _p)

import acquisition  # noqa: E402,F401  (package must exist before adapter imports it)
import acquisition_ops as ao  # noqa: E402
import adapter  # noqa: E402
import manual_media_intake as mmi  # noqa: E402
import source_window as sw  # noqa: E402

YT_SHORT = "https://www.youtube.com/shorts/U0cN4rdLxk4"
YT_SHORT_ID = "U0cN4rdLxk4"
TW_VOD = "https://www.twitch.tv/videos/2852456270"

RESOLVED_OK = {
    "platform": "youtube", "content_id": YT_SHORT_ID,
    "canonical_url": "https://www.youtube.com/watch?v=" + YT_SHORT_ID,
    "kind": "short", "capability": {}, "window": None, "working_source": {},
    "acquisition_mode": "full_item", "identity_reconciliation": "unverified",
}


class _FakeHandler:
    """Records the two surfaces the worker publishes to (message + status)."""

    def __init__(self):
        self.patches = []
        self.stage_log = []
        self.choice_calls = 0

    def _patch_original(self, application_id, interaction_token, **kw):
        self.patches.append({"app": application_id, "token": interaction_token, **kw})

    def _mi_status(self, application_id, interaction_token, text, *, stage=""):
        self.stage_log.append((stage, text))
        self._patch_original(application_id, interaction_token, content=text)

    def _mi_present_choice(self, *args, **kwargs):
        """Count the offer, then drive the PRODUCTION control-row builder."""
        self.choice_calls += 1
        return adapter.Handler._mi_present_choice(self, *args, **kwargs)

    def _reviewer_name(self, interaction):
        return "operator"

    def components_rows(self):
        return [p["components"] for p in self.patches if p.get("components")]


def _interaction():
    return {"application_id": "app", "token": "tok", "channel": {"id": "chan1"},
            "member": {"user": {"username": "bryan"}},
            "data": {"components": []}, "message": {"id": "m1"}}


def _drive_execute(parsed, *, handler=None, acquire=None, duration=20.0):
    """Run the REAL `_manual_intake_execute` with transport stubbed."""
    handler = handler or _FakeHandler()
    acquire = acquire or (lambda *a, **kw: _acquired())
    found = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
    found.write(b"\x00" * 4096)
    found.close()
    patches = [
        mock.patch.object(adapter, "resolve_manual_intake_channel",
                          return_value="chan1"),
        mock.patch.object(adapter.tenant_routing, "list_active_tenants",
                          return_value={"dreemzy"}),
        mock.patch.object(mmi, "probe_source_duration", return_value=duration),
        mock.patch.object(mmi, "acquire_url_media", side_effect=acquire),
        mock.patch.object(mmi, "probe_media",
                          return_value={"has_video": True, "duration_seconds": 20.0,
                                        "sha256": "a" * 64, "width": 1080,
                                        "height": 1920}),
        mock.patch.object(mmi, "verify_artifact_integrity", return_value={"ok": True}),
        mock.patch.object(mmi, "fetch_source_metadata", return_value={}),
        mock.patch.object(mmi, "find_existing_media", return_value=None),
        mock.patch.object(mmi, "store_durable_media",
                          side_effect=mmi.MediaIntakeError(
                              "DURABLE_STORE_FAILED", "stop after acquisition")),
    ]
    buf = io.StringIO()
    for patch in patches:
        patch.start()
    try:
        with contextlib.redirect_stdout(buf):
            adapter.Handler._manual_intake_execute(handler, _interaction(), parsed, {})
    finally:
        for patch in reversed(patches):
            patch.stop()
    return buf.getvalue(), handler


def _acquired():
    handle = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
    handle.write(b"\x00" * 4096)     # non-zero, so the size guard is satisfied
    handle.close()
    return handle.name, dict(RESOLVED_OK)


class WholeChoiceResumeRouteTests(unittest.TestCase):
    """1: the resumed WHOLE choice IS carried as an explicit decision."""

    def setUp(self):
        adapter._MI_PENDING.clear()

    def _resume(self, action, components=None):
        ref = adapter._mi_remember_pending(
            url=YT_SHORT, tenant="dreemzy", submitter="bryan", channel_id="chan1",
            platform="youtube", source_id=YT_SHORT_ID, item_kind="short")
        captured = {}

        class _Worker(_FakeHandler):
            def _manual_intake_execute(self, interaction, parsed, attachment,
                                       supplied_path=""):
                captured.update(parsed)

        interaction = _interaction()
        interaction["data"] = {"components": components or []}
        adapter.Handler._mi_choice_worker(_Worker(), interaction, action, ref)
        return captured

    def test_whole_resume_marks_the_operator_decision(self):
        parsed = self._resume(adapter.MI_ACT_WHOLE)
        self.assertTrue(parsed.get("whole_chosen"),
                        "a resumed WHOLE choice must be distinguishable from a fresh "
                        "windowless submission")
        self.assertIsNone(parsed.get("start_s"))
        self.assertIsNone(parsed.get("end_s"))

    def test_segment_resume_does_not_mark_a_whole_decision(self):
        parsed = self._resume(adapter.MI_ACT_SEGMENT, [
            {"components": [{"custom_id": ao.WINDOW_MODAL_START, "value": "00:02"},
                            {"custom_id": ao.WINDOW_MODAL_END, "value": "00:12"}]}])
        self.assertNotIn("whole_chosen", parsed)
        self.assertEqual((parsed.get("start_s"), parsed.get("end_s")), (2.0, 12.0))

    def test_a_text_submission_can_never_mark_a_whole_decision(self):
        """Only the resume worker may set the marker."""
        parsed = mmi.parse_intake_message(f"<@1> {YT_SHORT}")
        self.assertNotIn("whole_chosen", parsed)


class NoChooserLoopTests(unittest.TestCase):
    """2-3: the executor must not re-offer, and must reach acquisition."""

    def test_whole_chosen_reaches_acquisition_and_never_re_offers(self):
        parsed = {"url": YT_SHORT, "start_s": None, "end_s": None, "tenant": "dreemzy",
                  "window_intent": False, "window_problem": "", "whole_chosen": True}
        out, handler = _drive_execute(parsed)
        # the chooser is NOT presented again...
        self.assertEqual(handler.choice_calls, 0,
                         "the executor re-presented the chooser for an answered choice")
        self.assertEqual(handler.components_rows(), [])
        self.assertNotIn("choice_offered", out)
        self.assertIn("whole source chosen by the operator", out)
        # ...and the route went on to acquisition
        self.assertIn("stage=acquisition_start", out)
        self.assertIn("stage=executor_start", out)
        stages = [stage for stage, _ in handler.stage_log]
        self.assertIn("acquire_media", stages)
        self.assertIn("durable_store_ghl", stages)
        self.assertIn("stage=route_failed", out)

    def test_a_fresh_windowless_submission_still_offers_the_chooser(self):
        parsed = {"url": YT_SHORT, "start_s": None, "end_s": None, "tenant": "dreemzy",
                  "window_intent": False, "window_problem": ""}
        out, handler = _drive_execute(parsed)
        self.assertEqual(handler.choice_calls, 1)
        self.assertIn("stage=choice_offered", out)
        self.assertNotIn("stage=acquisition_start", out)
        row = handler.components_rows()[-1][0]["components"]
        labels = {c["label"]: c for c in row}
        self.assertFalse(labels["Whole video"]["disabled"])

    def test_the_marker_does_not_bypass_the_shared_acquisition_mode_gate(self):
        """Defence in depth: a whole decision is still re-validated downstream."""
        with self.assertRaises(Exception) as caught:
            sw.validate_acquisition_mode(
                platform="twitch", content_id="2852456270", canonical_url=TW_VOD,
                source_duration_s=None, window=None, item_kind="vod",
                requires_window_for_long_source=True)
        self.assertIn("LONG_FORM_REQUIRES_WINDOW",
                      str(getattr(caught.exception, "code", "") or caught.exception))


if __name__ == "__main__":
    unittest.main()
