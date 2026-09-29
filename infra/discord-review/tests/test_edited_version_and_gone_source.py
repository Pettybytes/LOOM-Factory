"""Seam tests from the 2026-09-29 live failures — two independent defects.

1. AN EDITED VERSION COULD NEVER BE REOPENED. The source-lineage bootstrap refused
   ANY requested version other than the source, so a registered `render-1.0` — whose
   media (`clean_source_*`) and trim (`loom_edit_corrections`) were both durable —
   returned `version_not_found` and the editor showed
   "No durable media or trim range for this clip version."

2. A SOURCE THAT NO LONGER EXISTS WAS TREATED AS "length not proven". The metadata
   probe returned `None` for a deleted Kick VOD, so the chooser was offered anyway and
   the operator spent a decision on an unfetchable source, only to receive a generic
   `DOWNLOAD_FAILED` later. The platform's OWN API answered 404 for that VOD.

Transport is stubbed only; no network.

Run:  python3 -m unittest discover -s infra/discord-review/tests -v
"""

import contextlib
import io
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
for _p in (str(ROOT / "infra" / "discord-review"), str(ROOT / "infra"),
           str(ROOT / "infra" / "acquisition")):
    sys.path.insert(0, _p)

import acquisition  # noqa: E402,F401
import adapter  # noqa: E402
import manual_media_intake as mmi  # noqa: E402
import source_review as sr  # noqa: E402
import ytdlp_adapter as ya  # noqa: E402

KICK_VOD = "https://kick.com/loomchannel/videos/01a08b9e-c000-7a12-9aa2-aff0312de3cc"
YT = "https://www.youtube.com/watch?v=U0cN4rdLxk4"

CANDIDATE = {
    "tenant_id": "dreemzy",
    "duplicate_fingerprint": "a" * 64,
    "title": "source title",
    "moment_evidence": {"lineage": {"media_reference": {
        "url": "https://cdn.example/working.mp4", "filename": "working.mp4",
        "duration_seconds": 20.108481},
        "media_sha256": "b" * 64}},
    "boundary_plan": {"recommended_clip_start": 0.0, "recommended_clip_end": 20.108481},
}

MEDIA_ROW = {
    "asset_version": "render-1.0",
    "previous_asset_version": "source-1.0",
    "publication_status": "final_review_pending",
    "storage_status": "not_applicable",
    "clean_source_url": "https://cdn.example/clean-prehook.mp4",
    "clean_source_hash": "c" * 64,
    "clean_source_size_bytes": 7273660,
    "clean_source_filename": "recut_clip_1.mp4",
    "duration_seconds": None,
}
CORRECTION_ROW = {"trim_start": 0, "trim_end": 20.108481,
                  "target_asset_version": "render-1.0"}


class EditedVersionReopenTests(unittest.TestCase):
    """1: a REGISTERED edited version hydrates from its own durable records."""

    def test_edited_version_bootstraps_with_media_and_the_recorded_trim(self):
        payload, reason = sr.edited_version_bootstrap(
            CANDIDATE, MEDIA_ROW, CORRECTION_ROW, "render-1.0")
        self.assertEqual(reason, "ok")
        self.assertEqual(payload["asset_version"], "render-1.0")
        self.assertEqual(payload["version_mode"], "edited_version")
        self.assertEqual(payload["media_url"], MEDIA_ROW["clean_source_url"])
        self.assertEqual(payload["source_range"], {"start": 0.0, "end": 20.108481})
        self.assertEqual(payload["duration_seconds"], 20.11)
        self.assertEqual(payload["media_size_bytes"], 7273660)
        self.assertEqual(payload["previous_asset_version"], "source-1.0")
        # platform metadata must never become renderable text
        self.assertEqual(payload["title"], "")
        self.assertEqual(payload["source_title"], "source title")

    def test_an_unknown_version_is_refused_and_never_falls_back_to_the_source(self):
        payload, reason = sr.edited_version_bootstrap(CANDIDATE, None, None, "render-9.9")
        self.assertIsNone(payload)
        self.assertEqual(reason, "version_not_found")

    def test_a_version_with_neither_media_nor_trim_is_refused(self):
        payload, reason = sr.edited_version_bootstrap(
            CANDIDATE, {"asset_version": "render-1.0"}, None, "render-1.0")
        self.assertIsNone(payload)
        self.assertEqual(reason, "missing_durable_fields")

    def test_the_source_version_path_is_untouched(self):
        payload, reason = sr.source_lineage_bootstrap(CANDIDATE, "source-1.0")
        self.assertEqual(reason, "ok")
        self.assertEqual(payload["version_mode"], "source_lineage")
        self.assertEqual(payload["asset_version"], "source-1.0")
        self.assertEqual(payload["media_url"], "https://cdn.example/working.mp4")

    def test_the_source_bootstrap_still_refuses_a_render_version_on_its_own(self):
        """The pure builder is unchanged; the adapter owns the edited-version route."""
        payload, reason = sr.source_lineage_bootstrap(CANDIDATE, "render-1.0")
        self.assertIsNone(payload)
        self.assertEqual(reason, "version_not_found")


class SourceUnavailableTests(unittest.TestCase):
    """2: a gone source is reported as gone, at the stage that finds out."""

    def test_the_live_kick_404_is_classified_as_unavailable(self):
        observed = ("[YTDLP_FAILED] yt-dlp exited 1: ERROR: [kick:vod] "
                    "01a08b9e-c000-7a12-9aa2-aff0312de3cc: Unable to download JSON "
                    "metadata: HTTP Error 404: Not Found (caused by <HTTPError 404: "
                    "Not Found>)")
        self.assertTrue(ya.is_source_unavailable(observed))
        self.assertTrue(ya.is_source_unavailable(
            'No query results for model [App\\Models\\Video].'))

    def test_ordinary_transport_and_bot_failures_are_not_unavailable(self):
        for text in (
            "<urlopen error [Errno 32] Broken pipe>",
            "[youtube] 59xxeJCbzaM: Sign in to confirm you're not a bot.",
            "<urlopen error _ssl.c:1064: The handshake operation timed out>",
            "Requested format is not available",
        ):
            self.assertFalse(ya.is_source_unavailable(text), text)

    def test_the_diag_probe_keeps_unknown_and_gone_distinguishable(self):
        with mock.patch.object(mmi, "_acq") as acq:
            acq.return_value = ya
            with mock.patch.object(ya, "run_ytdlp_command",
                                   side_effect=ya.YtDlpError(
                                       "YTDLP_FAILED", "HTTP Error 404: Not Found")):
                duration, unavailable = mmi.probe_source_duration_diag(KICK_VOD)
            self.assertIsNone(duration)
            self.assertTrue(unavailable)
            with mock.patch.object(ya, "run_ytdlp_command",
                                   side_effect=ya.YtDlpError(
                                       "YTDLP_FAILED", "handshake operation timed out")):
                duration, unavailable = mmi.probe_source_duration_diag(KICK_VOD)
            self.assertIsNone(duration)
            self.assertEqual(unavailable, "")


class NoChoiceForAGoneSourceTests(unittest.TestCase):
    """2: the chooser is not offered for a source that cannot be fetched."""

    class _Handler:
        def __init__(self):
            self.patches = []
            self.choice_calls = 0

        def _patch_original(self, application_id, interaction_token, **kw):
            self.patches.append(kw)

        def _mi_status(self, application_id, interaction_token, text, *, stage=""):
            self._patch_original(application_id, interaction_token, content=text)

        def _mi_present_choice(self, *a, **kw):
            self.choice_calls += 1

        def _reviewer_name(self, interaction):
            return "operator"

    def test_a_deleted_vod_fails_truthfully_without_offering_a_choice(self):
        handler = self._Handler()
        interaction = {"application_id": "app", "token": "***",
                       "channel": {"id": "chan1"},
                       "member": {"user": {"username": "bryan"}}}
        parsed = {"url": KICK_VOD, "start_s": None, "end_s": None,
                  "tenant": "dreemzy", "window_intent": False, "window_problem": ""}
        patches = [
            mock.patch.object(adapter, "resolve_manual_intake_channel",
                              return_value="chan1"),
            mock.patch.object(adapter.tenant_routing, "list_active_tenants",
                              return_value={"dreemzy"}),
            mock.patch.object(mmi, "probe_source_duration_diag",
                              return_value=(None, "HTTP Error 404: Not Found")),
        ]
        buf = io.StringIO()
        for patch in patches:
            patch.start()
        try:
            with contextlib.redirect_stdout(buf):
                adapter.Handler._manual_intake_execute(
                    handler, interaction, parsed, {})
        finally:
            for patch in reversed(patches):
                patch.stop()
        out = buf.getvalue()
        self.assertEqual(handler.choice_calls, 0,
                         "a source that no longer exists must not be offered as a choice")
        self.assertIn("SOURCE_EXTERNALLY_UNAVAILABLE", out)
        self.assertIn("stage=route_failed", out)
        self.assertFalse(any(p.get("components") for p in handler.patches))
        # it is NOT retry-safe: the cause is the input, not the transport
        self.assertIn("Retry safe: no", handler.patches[-1]["content"])

    def test_the_unavailable_code_is_not_advertised_as_retry_safe(self):
        self.assertNotIn("SOURCE_EXTERNALLY_UNAVAILABLE", adapter._MI_RETRY_SAFE_CODES)


if __name__ == "__main__":
    unittest.main()
