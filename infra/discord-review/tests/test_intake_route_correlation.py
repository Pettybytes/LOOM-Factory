"""Seam tests: ONE stable source identity across the whole Manual Intake route.

    INTAKE RECEIVED -> CHOICE OFFERED -> CHOICE CLICK RECEIVED -> RESUME RESOLVED
      -> EXECUTOR START -> ACQUISITION START -> DOWNLOAD/EXTRACT RESULT
      -> DURABLE STORE RESULT -> CANDIDATE RESULT

The 2026-09-28/29 failures were undiagnosable because the chooser split the route
in two — an offer, then a LATER interaction — and nothing carried the source
identity across the split. A click that never arrived and an executor that never
ran produced the SAME log (nothing at all).

These tests drive the REAL seams (the button handler, the choice worker, the pending
store) and assert the correlation line each one must emit. Transport is stubbed
only; no network.

Run:  python3 -m unittest discover -s infra/discord-review/tests -v
"""

import contextlib
import io
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
# Same three roots the other discord-review suites use: `infra` must be importable so
# `import acquisition` resolves to the acquisition PACKAGE (approved_lane and
# platform_capabilities both import it by name).
for _p in (str(ROOT / "infra" / "discord-review"), str(ROOT / "infra"),
           str(ROOT / "infra" / "acquisition")):
    sys.path.insert(0, _p)

import adapter  # noqa: E402

KICK_CLIP = "https://kick.com/deenthegreat/clips/clip_01M33XASMP29XTQ1QK0EV7TXDC"
KICK_ID = "clip_01M33XASMP29XTQ1QK0EV7TXDC"
# A source URL that carries a NON-identity hint plus a credential-looking secret:
# nothing in a trace line may ever quote it.
SECRET_URL = ("https://kick.com/x/clips/clip_01M33XASMP29XTQ1QK0EV7TXDC"
              "?t=1800&token=dummy-secret-that-must-never-be-logged")

# Every stage the route is required to publish.
REQUIRED_STAGES = (
    "intake_received",
    "executor_start",
    "choice_offered",
    "choice_click_received",
    "resume_resolved",
    "resume_miss",
    "acquisition_start",
    "durable_store_result",
    "candidate_result",
    "route_failed",
)


def _capture(fn):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        fn()
    return buf.getvalue()


def _trace_lines(text):
    return [line for line in text.splitlines() if line.startswith("[intake-trace]")]


class _FakeClickHandler:
    """Minimal handler for the button seam (no response body is inspected)."""

    def __init__(self):
        self.sent = []
        self.patches = []

    def _send_json(self, status, payload):
        self.sent.append((status, payload))

    def _patch_original(self, application_id, interaction_token, **kw):
        self.patches.append(kw)


def _interaction():
    """A minimal component interaction envelope for the resume seam."""
    return {"application_id": "app", "token": "tok",
            "data": {"components": []}, "message": {"id": "m1"}}


class _FakeWorkerHandler(_FakeClickHandler):
    def _reviewer_name(self, interaction):
        return "operator"

    def _manual_intake_execute(self, *a, **kw):  # resumed work is not under test here
        self.resumed = (a, kw)

    def _mi_status(self, application_id, interaction_token, text, *, stage=""):
        self.patches.append({"content": text})


class TracePrimitiveTests(unittest.TestCase):
    def test_a_trace_line_names_the_stage_and_the_source_identity(self):
        out = _capture(lambda: adapter._mi_trace(
            "choice_offered", platform="kick", source_id=KICK_ID,
            duration_s=30.0, whole_allowed=True, ref="abc123"))
        line = _trace_lines(out)[0]
        self.assertIn("stage=choice_offered", line)
        self.assertIn("platform=kick", line)
        self.assertIn(f"source_id={KICK_ID}", line)
        self.assertIn("ref=abc123", line)

    def test_empty_fields_are_omitted_rather_than_printed_as_blanks(self):
        out = _capture(lambda: adapter._mi_trace("intake_received", platform="",
                                                 source_id=None, mode="url"))
        line = _trace_lines(out)[0]
        self.assertEqual(line, "[intake-trace] stage=intake_received mode=url")

    def test_tracing_never_raises_even_on_a_hostile_field(self):
        _capture(lambda: adapter._mi_trace("x", platform=object(), ref="r"))

    def test_every_required_stage_is_published_somewhere_in_the_route(self):
        source = (ROOT / "infra" / "discord-review" / "adapter.py").read_text()
        for stage in REQUIRED_STAGES:
            self.assertIn(f'"{stage}"', source, f"route stage {stage} is never logged")


class IdentityTests(unittest.TestCase):
    def test_identity_is_the_platform_public_id_not_the_url(self):
        platform, source_id = adapter._mi_identity({"url": SECRET_URL})
        self.assertEqual(platform, "kick")
        self.assertEqual(source_id, KICK_ID)
        self.assertNotIn("token", source_id)

    def test_identity_degrades_to_empty_and_never_raises(self):
        self.assertEqual(adapter._mi_identity({}), ("", ""))
        self.assertEqual(adapter._mi_identity({"url": "not a url"}), ("", ""))
        self.assertEqual(adapter._mi_identity({"url": "ftp://example.com/x.mp4"}), ("", ""))

    def test_pending_entry_carries_the_identity_across_the_choice_split(self):
        ref = adapter._mi_remember_pending(
            url=SECRET_URL, tenant="dreemzy", submitter="op", channel_id="chan",
            platform="kick", source_id=KICK_ID, item_kind="clip")
        entry = adapter._mi_pending(ref)
        self.assertEqual(entry["platform"], "kick")
        self.assertEqual(entry["source_id"], KICK_ID)
        self.assertEqual(entry["item_kind"], "clip")
        adapter._mi_forget_pending(ref, release=True)


class ClickSeamTests(unittest.TestCase):
    """CHOICE CLICK RECEIVED must be provable, and must not leak the source URL."""

    def setUp(self):
        adapter._MI_PENDING.clear()

    def test_a_click_with_no_resume_state_still_proves_it_arrived(self):
        handler = _FakeClickHandler()
        out = _capture(lambda: adapter.Handler._handle_button(handler, {
            "data": {"custom_id": "MI:seg:unknownref", "component_type": 2},
            "application_id": "app", "token": "tok", "message": {"id": "m1"}}))
        line = _trace_lines(out)[0]
        self.assertIn("stage=choice_click_received", line)
        self.assertIn("action=seg", line)
        self.assertIn("ref=unknownref", line)
        self.assertIn("resume_state=absent", line)

    def test_a_click_carries_the_same_identity_the_offer_logged(self):
        ref = adapter._mi_remember_pending(
            url=SECRET_URL, tenant="dreemzy", submitter="op", channel_id="chan",
            platform="kick", source_id=KICK_ID, item_kind="clip")
        handler = _FakeClickHandler()
        out = _capture(lambda: adapter.Handler._handle_button(handler, {
            "data": {"custom_id": f"MI:seg:{ref}", "component_type": 2},
            "application_id": "app", "token": "tok", "message": {"id": "m1"}}))
        line = _trace_lines(out)[0]
        self.assertIn(f"source_id={KICK_ID}", line)
        self.assertIn("platform=kick", line)
        self.assertIn("resume_state=present", line)
        adapter._mi_forget_pending(ref, release=True)

    def test_no_trace_line_ever_quotes_the_source_url_or_its_secret(self):
        ref = adapter._mi_remember_pending(
            url=SECRET_URL, tenant="dreemzy", submitter="op", channel_id="chan",
            platform="kick", source_id=KICK_ID, item_kind="clip")
        handler = _FakeClickHandler()
        out = _capture(lambda: adapter.Handler._handle_button(handler, {
            "data": {"custom_id": f"MI:seg:{ref}", "component_type": 2},
            "application_id": "app", "token": "tok", "message": {"id": "m1"}}))
        for line in _trace_lines(out):
            self.assertNotIn("http", line)
            self.assertNotIn("dummy-secret", line)
            self.assertNotIn("?t=", line)
        adapter._mi_forget_pending(ref, release=True)


class ResumeSeamTests(unittest.TestCase):
    def setUp(self):
        adapter._MI_PENDING.clear()

    def test_a_resume_miss_is_logged_and_offers_a_real_recovery(self):
        handler = _FakeWorkerHandler()
        out = _capture(lambda: adapter.Handler._mi_choice_worker(
            handler, _interaction(), adapter.MI_ACT_WHOLE, "gone"))
        self.assertIn("stage=resume_miss", out)
        self.assertIn("reason=resume_state_absent", out)
        self.assertIn("expired or was already used", handler.patches[-1]["content"])

    def test_a_resume_resolution_is_logged_with_its_identity(self):
        ref = adapter._mi_remember_pending(
            url=KICK_CLIP, tenant="dreemzy", submitter="op", channel_id="chan",
            platform="kick", source_id=KICK_ID, item_kind="clip")
        handler = _FakeWorkerHandler()
        out = _capture(lambda: adapter.Handler._mi_choice_worker(
            handler, _interaction(), adapter.MI_ACT_WHOLE, ref))
        self.assertIn("stage=resume_resolved", out)
        self.assertIn(f"source_id={KICK_ID}", out)
        self.assertIn("action=whole", out)
        # and the resume reached the PROVEN executor (it is stubbed, not skipped)
        self.assertTrue(hasattr(handler, "resumed"))
        _args, _kwargs = handler.resumed
        self.assertEqual(_args[1].get("_mi_ref"), ref)
        self.assertEqual(_kwargs.get("supplied_path"), "")


if __name__ == "__main__":
    unittest.main()
