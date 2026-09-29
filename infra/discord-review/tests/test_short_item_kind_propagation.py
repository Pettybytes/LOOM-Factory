"""Regression coverage for the short-item KIND seam and truthful failure reporting.

Defect under test: the platform adapter OWNS the clip-vs-long classification, and
Manual Intake merely propagates it to the one safety gate. When an adapter published
no `kind`, the intake erased it to "" and the gate could only see "length unproven",
so an operator surface decided whole-vs-segment from a missing value instead of from
the platform's own URL shape.

These tests drive the REAL resolver, the REAL gate and the REAL adapter classes.
Only network transport, the channel lookup and the durable upload helper are stubbed,
so the contract under test is the production one.
"""

import contextlib
import io
import ssl
import sys
import unittest
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError, URLError

ROOT = Path(__file__).resolve().parents[3]
for directory in ("acquisition", "discord-review"):
    sys.path.insert(0, str(ROOT / "infra" / directory))

# HARNESS: the shared `acquisition` package must be importable BEFORE `adapter`, or
# this file dies at collection.
import acquisition  # noqa: E402,F401
import adapter  # noqa: E402
import manual_media_intake as mmi  # noqa: E402
import source_adapters as sa  # noqa: E402
import source_window as sw  # noqa: E402

YT_SHORT = "https://www.youtube.com/shorts/dQw4w9WgXcQ"
YT_WATCH = "https://www.youtube.com/watch?v=59xxeJCbzaM"
YT_ID = "59xxeJCbzaM"
TW_CLIP = "https://www.twitch.tv/loomchannel/clip/ObservantSlug123"
TW_CLIP_OLD_HOST = "https://clips.twitch.tv/ObservantSlug123"
TW_VOD = "https://www.twitch.tv/videos/2852456270"
KICK_CLIP = "https://kick.com/loomchannel/clips/clip_01M33XASMP29XTQ1QK0EV7TXDC"

_TRANSIENT = URLError(ConnectionResetError(54, "Connection reset by peer"))


class _FakeHandler:
    """Records the surface the worker publishes to (message updates)."""

    def __init__(self, *, fail_on_choice=None):
        self.patches = []
        self.stage_log = []
        #: When set, ONLY the PATCH that carries the choice controls fails, so the
        #: failure is attributable to that interaction update alone.
        self.fail_on_choice = fail_on_choice

    def _patch_original(self, application_id, interaction_token, **kw):
        if kw.get("components") is not None and self.fail_on_choice is not None:
            raise self.fail_on_choice
        self.patches.append({"app": application_id, "token": interaction_token, **kw})

    def _mi_status(self, application_id, interaction_token, text, *, stage=""):
        self.stage_log.append((stage, text))
        self._patch_original(application_id, interaction_token, content=text)

    def _mi_present_choice(self, *args, **kwargs):
        """Drive the PRODUCTION method so the real control row is built here."""
        return adapter.Handler._mi_present_choice(self, *args, **kwargs)

    def _reviewer_name(self, interaction):
        return "operator"

    def components_rows(self):
        return [p["components"] for p in self.patches if p.get("components")]


def _offer(url, *, duration):
    """The REAL pipeline: resolve -> capability -> gate -> operator control row."""
    resolved = mmi.resolve_source_url(url)
    cap = mmi.resolve_platform_capability(resolved.get("platform", ""))
    allowed, refusal = mmi.whole_source_verdict(
        platform=cap["platform"] or resolved.get("platform", ""),
        content_id=str(resolved.get("content_id") or ""),
        canonical_url=resolved["canonical_url"],
        item_kind=str(resolved.get("kind") or ""),
        source_duration_s=duration,
        requires_window_for_long_source=bool(
            cap.get("requires_window_for_long_source")))
    handler = _FakeHandler()
    adapter.Handler._mi_present_choice(
        handler, "app", "tok", ref="ref1", whole_allowed=allowed, refusal=refusal,
        source_duration_s=duration, origin_label="source")
    row = handler.components_rows()[-1][0]["components"]
    return {c["label"]: c for c in row}, handler.patches[-1]["content"], resolved


def _drive_failure(error, *, url=YT_WATCH, duration=None, raise_on_choice_only=False):
    """Run the REAL `_manual_intake_execute` and return its captured log.

    `raise_on_choice_only` fails ONLY the PATCH that carries the choice controls, so
    the failure is attributable to that interaction update alone.
    """
    interaction = {"application_id": "app", "token": "tok",
                   "channel": {"id": "chan1"},
                   "member": {"user": {"username": "bryan"}}}
    parsed = {"url": url, "start_s": None, "end_s": None, "tenant": "dreemzy",
              "window_intent": False, "window_problem": ""}
    handler = _FakeHandler(fail_on_choice=error if raise_on_choice_only else None)
    patches = [
        mock.patch.object(adapter, "resolve_manual_intake_channel",
                          return_value="chan1"),
        mock.patch.object(mmi, "probe_source_duration", return_value=duration),
    ]
    if not raise_on_choice_only:
        patches.append(mock.patch.object(adapter.tenant_routing, "list_active_tenants",
                                         side_effect=error))
    buf = io.StringIO()
    for patch in patches:
        patch.start()
    try:
        with contextlib.redirect_stdout(buf):
            adapter.Handler._manual_intake_execute(handler, interaction, parsed, {})
    finally:
        for patch in reversed(patches):
            patch.stop()
    return buf.getvalue(), handler


def _developer_log(error, **kwargs):
    """Just the captured developer log."""
    return _drive_failure(error, **kwargs)[0]


class KindOwnershipTests(unittest.TestCase):
    """The adapter owns the kind; Manual Intake must RECEIVE it, not lose it."""

    def test_adapter_publishes_youtube_short_kind(self):
        self.assertEqual(sa.YouTubeAdapter().canonicalize(YT_SHORT)["kind"], "short")

    def test_adapter_publishes_youtube_long_kind(self):
        self.assertEqual(sa.YouTubeAdapter().canonicalize(YT_WATCH)["kind"], "video")

    def test_adapter_publishes_twitch_clip_kind(self):
        self.assertEqual(sa.TwitchAdapter().canonicalize(TW_CLIP)["kind"], "clip")

    def test_resolver_carries_the_kind_through(self):
        self.assertEqual(mmi.resolve_source_url(YT_SHORT).get("kind"), "short")
        self.assertEqual(mmi.resolve_source_url(TW_CLIP).get("kind"), "clip")
        self.assertEqual(mmi.resolve_source_url(KICK_CLIP).get("kind"), "clip")

    def test_short_kind_is_the_gate_vocabulary(self):
        """The published short kind is one the gate already treats as short."""
        self.assertIn("short", sw.SHORT_ITEM_KINDS)
        self.assertIn("clip", sw.SHORT_ITEM_KINDS)
        self.assertNotIn("video", sw.SHORT_ITEM_KINDS)
        self.assertNotIn("", sw.SHORT_ITEM_KINDS)


class ShortItemWholeVideoTests(unittest.TestCase):
    """1-2: a SHORT item permits WHOLE VIDEO with no operator window."""

    def test_1_short_twitch_clip_permits_whole_video(self):
        for url in (TW_CLIP, TW_CLIP_OLD_HOST):
            by_label, _, resolved = _offer(url, duration=None)
            self.assertEqual(resolved.get("kind"), "clip")
            self.assertFalse(by_label["Whole video"]["disabled"],
                             f"{url} must permit whole video")
            self.assertIn("Select segment", by_label)

    def test_2_short_youtube_item_permits_whole_video(self):
        by_label, text, resolved = _offer(YT_SHORT, duration=None)
        self.assertEqual(resolved.get("kind"), "short")
        self.assertFalse(by_label["Whole video"]["disabled"])
        self.assertIn("Select segment", by_label)
        self.assertNotIn("not available", text)

    def test_2b_short_item_kick_clip_permits_whole_video(self):
        by_label, _, resolved = _offer(KICK_CLIP, duration=None)
        self.assertEqual(resolved.get("kind"), "clip")
        self.assertFalse(by_label["Whole video"]["disabled"])


class LongSourceStillGatedTests(unittest.TestCase):
    """3-4: the long-source safety gate is UNWEAKENED."""

    def test_3_long_vod_requires_select_segment(self):
        by_label, text, resolved = _offer(TW_VOD, duration=None)
        self.assertEqual(resolved.get("kind"), "vod")
        self.assertTrue(by_label["Whole video"]["disabled"])
        self.assertIn("Select segment", by_label)
        self.assertIn("not available", text)

    def test_3b_long_vod_with_proven_long_duration_is_refused_whole(self):
        by_label, _, _ = _offer(TW_VOD, duration=7200.0)
        self.assertTrue(by_label["Whole video"]["disabled"])

    def test_4_long_source_is_still_refused_whole(self):
        for url in (YT_WATCH, TW_VOD):
            allowed, refusal = mmi.whole_source_verdict(
                platform="youtube" if url == YT_WATCH else "twitch",
                content_id=YT_ID if url == YT_WATCH else "2852456270",
                canonical_url=url, item_kind="", source_duration_s=467.0,
                requires_window_for_long_source=(url == TW_VOD))
            self.assertFalse(allowed, f"{url} must not be acquirable whole")
            self.assertEqual(refusal, "LONG_FORM_REQUIRES_WINDOW")

    def test_4b_gate_itself_still_refuses_a_windowless_long_source(self):
        with self.assertRaises(sw.WindowError) as cm:
            sw.validate_acquisition_mode(
                platform="twitch", content_id="2852456270", canonical_url=TW_VOD,
                source_duration_s=None, window=None, item_kind="vod",
                requires_window_for_long_source=True)
        self.assertEqual(cm.exception.code, "LONG_FORM_REQUIRES_WINDOW")

    def test_4c_an_unproven_long_form_is_not_short_by_accident(self):
        """`video` is not a short kind, so a long item cannot gain whole access."""
        with self.assertRaises(sw.WindowError) as cm:
            sw.validate_acquisition_mode(
                platform="twitch", content_id="2852456270", canonical_url=TW_VOD,
                source_duration_s=None, window=None, item_kind="video",
                requires_window_for_long_source=True)
        self.assertEqual(cm.exception.code, "LONG_FORM_REQUIRES_WINDOW")


class PreservedDetailTests(unittest.TestCase):
    """5-6: the refused classes log their REAL preserved detail."""

    def test_5_window_rejected_logs_its_real_detail(self):
        with self.assertRaises(mmi.MediaIntakeError) as cm:
            mmi.build_operator_window(
                platform="youtube", canonical_url=YT_WATCH, content_id=YT_ID,
                start_s=0.0, end_s=999_999.0, source_duration_s=467.0)
        error = cm.exception
        self.assertEqual(error.code, "WINDOW_REJECTED")
        self.assertTrue(error.detail, "the real underlying reason must be preserved")

        log, _handler = _drive_failure(error)
        self.assertIn("code=WINDOW_REJECTED", log)
        self.assertIn(f"reason={error.detail[:300]!r}", log)

    def test_6_durable_store_failed_logs_its_real_detail(self):
        transport = "<urlopen error [Errno 32] Broken pipe>"
        with mock.patch.object(adapter, "ghl_upload_file", return_value=None), \
                mock.patch.object(adapter, "ghl_last_upload_error",
                                  return_value=transport):
            with self.assertRaises(mmi.MediaIntakeError) as cm:
                mmi.store_durable_media("/tmp/loom-not-written.mp4", "x.mp4")
        error = cm.exception
        self.assertEqual(error.code, "DURABLE_STORE_FAILED")
        self.assertIn("Broken pipe", error.detail)

        log, _handler = _drive_failure(error)
        self.assertIn("code=DURABLE_STORE_FAILED", log)
        self.assertIn(f"reason={error.detail[:300]!r}", log)


class PatchStageClassificationTests(unittest.TestCase):
    """7-8: a Discord interaction-PATCH failure is a TRANSPORT failure."""

    def test_7_patch_transport_failure_is_not_acquire_media_or_unknown(self):
        code, _reason = adapter.classify_intake_failure(
            _TRANSIENT, adapter.MI_STAGE_INTERACTION_PATCH)
        self.assertEqual(code, "TRANSPORT_FAILED")
        self.assertNotEqual(code, "UNKNOWN")
        self.assertNotIn(code, ("DOWNLOAD_FAILED", "MEDIA_INTEGRITY_FAILED",
                                "PROBE_FAILED", "MEDIA_EXCEEDS_LIMIT"))
        self.assertIn(code, adapter._MI_RETRY_SAFE_CODES)
        self.assertNotEqual(adapter.MI_STAGE_INTERACTION_PATCH, "acquire_media")

    def test_7b_tls_handshake_failure_is_transport(self):
        code, _ = adapter.classify_intake_failure(
            URLError(ssl.SSLError("handshake timeout")),
            adapter.MI_STAGE_INTERACTION_PATCH)
        self.assertEqual(code, "TRANSPORT_FAILED")

    def test_7c_a_real_http_status_is_not_a_transport_failure(self):
        """An HTTP response is not a transport failure and must not be relabelled."""
        http_error = HTTPError(YT_WATCH, 404, "Not Found", {}, None)
        code, _ = adapter.classify_intake_failure(
            http_error, adapter.MI_STAGE_INTERACTION_PATCH)
        self.assertNotEqual(code, "TRANSPORT_FAILED")

    def test_8_the_patch_stage_is_truthful_and_never_acquire_media(self):
        log, _handler = _drive_failure(_TRANSIENT, url=YT_SHORT, duration=45.0,
                                       raise_on_choice_only=True)
        self.assertIn(f"stage={adapter.MI_STAGE_INTERACTION_PATCH}", log)
        self.assertIn("code=TRANSPORT_FAILED", log)
        self.assertNotIn("stage=acquire_media", log)
        self.assertNotIn("code=UNKNOWN", log)

    def test_8b_the_transport_detail_never_reaches_discord(self):
        """The transport reason is logged for the engineer, never sent to Discord."""
        log, handler = _drive_failure(_TRANSIENT, url=YT_SHORT, duration=45.0,
                                      raise_on_choice_only=True)
        self.assertIn("Connection reset by peer", log)
        operator_text = "\n".join(
            str(p.get("content") or "") for p in handler.patches)
        self.assertIn("TRANSPORT_FAILED", operator_text)
        self.assertNotIn("Connection reset by peer", operator_text)
        self.assertNotIn("urlopen", operator_text)


if __name__ == "__main__":
    unittest.main()
