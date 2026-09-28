"""Focused tests: #clip-review MEDIA PARITY.

The human decision surface must show the candidate's working media alongside
Approve Clip / Edit / Reject / Hold / Open Source — reusing the media the candidate
already owns, and never blocking the decision when a preview cannot be rendered.

These drive the REAL `_post_source_card` / `_attach_working_media` seam. Only
transport is stubbed (Discord HTTP, the shared attach primitive), so the contract
under test is the production one.
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

# Import order matters in this repo: `adapter` pulls in `acquisition_ops`, which pulls
# in `approved_lane`, which resolves the `acquisition` module by name. Importing the
# acquisition-side module FIRST (as the sibling suites do) lets that resolve cleanly.
import acquisition_ops  # noqa: E402,F401  (path/order sentinel for approved_lane)
import adapter                                    # noqa: E402
import discord_media as dm                        # noqa: E402
import source_review                              # noqa: E402


def _handler():
    """A stand-in for `self` with the REAL publishing methods bound.

    `_post_source_card` calls `self._attach_working_media`, so binding the production
    method means the seam under test is production code, not a stand-in.
    """
    h = types.SimpleNamespace()
    h._attach_working_media = types.MethodType(
        adapter.Handler._attach_working_media, h)
    return h


def _candidate(**over):
    cand = {
        # A real tenant-scoped SHA-256 hex, because the card's custom_id contract
        # requires exactly that shape (a placeholder string is correctly rejected).
        "duplicate_fingerprint": hashlib.sha256(b"loom-parity-fixture").hexdigest(),
        "canonical_url": "https://www.youtube.com/watch?v=59xxeJCbzaM",
        "platform": "youtube",
        "platform_content_id": "59xxeJCbzaM",
        "title": "Luminous Was Pierced by Twilight",
        "tenant_id": "dreemzy",
        "profile_id": "manual-intake",
        "outcome": "manual_review",
        "recommended_format": "hold_unassigned",
        "boundary_plan": {"recommended_clip_start": 0.0,
                          "recommended_clip_end": 50.0,
                          "event_timestamp": 12.0},
        "moment_evidence": {
            "lineage": {"origin": "manual_media_intake",
                        "media_reference": "https://filesafe.example/working.mp4"},
            "intent": "operator_supplied",
            "why_selected": "operator chose this segment",
        },
        "completeness_verdict": "FAIL",
    }
    cand.update(over)
    return cand


class _Recorder:
    """Binds the production card seam onto a fake, like the worker does."""

    def __init__(self):
        self.http = []
        self.attach = []
        self.remembered = []

    def install(self):
        def _http_json(method, url, token, body=None, timeout=None):
            self.http.append({"method": method, "url": url, "body": body})
            # Faithful transport: a PATCH returns the EDITED message (its own id),
            # a POST returns the newly created one.
            if method == "PATCH":
                return 200, {"id": url.rstrip("/").split("/")[-1], "attachments": []}
            return 200, {"id": "msg1", "attachments": []}

        def _attach(**kw):
            self.attach.append(kw)
            return {"mode": "video", "attached": True, "playable": True,
                    "asset_bytes": 2048, "limit_bytes": kw.get("limit_bytes"),
                    "tier": kw.get("tier"), "upload_name": kw.get("upload_name"),
                    "message_id": kw.get("message_id")}

        self._patches = [
            mock.patch.object(adapter, "http_json", _http_json),
            mock.patch.object(adapter.discord_media, "attach_acquired_media", _attach),
            mock.patch.object(adapter, "_card_upload_limit", return_value=(10 * 1024 * 1024, 0)),
            mock.patch.object(source_review, "remember_card_message",
                              side_effect=lambda *a, **k: self.remembered.append(a)),
        ]
        for p in self._patches:
            p.start()
        return self

    def stop(self):
        for p in reversed(self._patches):
            p.stop()


class MediaReachesTheDecisionCardTests(unittest.TestCase):
    def setUp(self):
        self.rec = _Recorder().install()
        self.tmp = tempfile.mkdtemp(prefix="loom-parity-")
        self.media = os.path.join(self.tmp, "working.mp4")
        Path(self.media).write_bytes(b"v" * 4096)

    def tearDown(self):
        self.rec.stop()

    def _post(self, **kw):
        return adapter.Handler._post_source_card(_handler(), "chan1", _candidate(), **kw)

    def test_1_working_media_reaches_clip_review(self):
        posted = self._post(working_media_path=self.media)
        self.assertEqual(len(self.rec.attach), 1, "media must be attached to the card")
        kw = self.rec.attach[0]
        self.assertEqual(kw["message_id"], "msg1")
        self.assertEqual(kw["channel_id"], "chan1")
        self.assertEqual(kw["asset_path"], self.media)
        self.assertEqual(posted["id"], "msg1")

    def test_2_no_media_is_downloaded_uploaded_or_duplicated(self):
        self._post(working_media_path=self.media,
                   durable_url="https://filesafe.example/working.mp4")
        # Exactly ONE outbound HTTP call: creating the card. The durable media URL is
        # never fetched, and nothing is re-derived from the source.
        self.assertEqual([h["method"] for h in self.rec.http], ["POST"])
        self.assertIn("/channels/chan1/messages", self.rec.http[0]["url"])
        self.assertNotIn("filesafe", str(self.rec.http[0]["url"]))
        # The attach primitive is handed the LOCAL working file, not a URL.
        self.assertTrue(os.path.isfile(self.rec.attach[0]["asset_path"]))
        self.assertNotIn("http", self.rec.attach[0]["asset_path"])
        # No second storage path was introduced.
        self.assertFalse(hasattr(dm, "upload_durable_copy"))

    def test_3_decision_actions_are_unchanged(self):
        self._post(working_media_path=self.media)
        components = self.rec.attach[0]["components"]
        labels = [b["label"] for row in components for b in row["components"]]
        for expected in ("Approve Clip", "Reject", "Hold"):
            self.assertIn(expected, labels)
        # The SAME components row that was posted is what media re-renders against,
        # so a decision can never be lost by adding a preview.
        posted_components = self.rec.http[0]["body"]["components"]
        self.assertEqual(components, posted_components)

    def test_3b_edit_survives_alongside_media(self):
        cand = _candidate()
        components = source_review.format_source_buttons(
            cand["duplicate_fingerprint"], source_url=cand["canonical_url"],
            manual_media=True)
        labels = [b["label"] for row in components for b in row["components"]]
        self.assertIn("Edit", labels)
        self.assertIn("Open Source", labels)

    def test_4_missing_preview_fails_gracefully(self):
        posted = self._post()                      # no working media supplied
        self.assertEqual(self.rec.attach, [])
        self.assertEqual(posted["id"], "msg1")
        self.assertEqual(len(self.rec.http), 1)     # the card still posted

    def test_4b_unrenderable_preview_never_blocks_the_decision(self):
        with mock.patch.object(adapter.discord_media, "attach_acquired_media",
                               side_effect=RuntimeError("Discord refused the upload")):
            posted = self._post(working_media_path=self.media)
        self.assertEqual(posted["id"], "msg1")
        self.assertEqual(len(self.rec.http), 1)

    def test_4c_a_vanished_working_copy_is_not_an_error(self):
        posted = self._post(working_media_path=os.path.join(self.tmp, "gone.mp4"))
        self.assertEqual(self.rec.attach, [])
        self.assertEqual(posted["id"], "msg1")

    def test_6_full_source_candidate_without_kwargs_still_works(self):
        """The automated-discovery path calls this with no media arguments."""
        posted = self._post()
        self.assertEqual(posted["id"], "msg1")
        self.assertEqual(self.rec.attach, [])

    def test_existing_card_is_edited_and_media_lands_on_the_same_message(self):
        cand = _candidate()
        cand["moment_evidence"]["lineage"]["card_message_id"] = "oldcard"
        adapter.Handler._post_source_card(_handler(), "chan1", cand,
                                          working_media_path=self.media)
        self.assertEqual([h["method"] for h in self.rec.http], ["PATCH"])
        self.assertIn("/messages/oldcard", self.rec.http[0]["url"])
        self.assertEqual(self.rec.attach[0]["message_id"], "oldcard")


class CoordinateAndTerminologyTests(unittest.TestCase):
    """The two window-looking groups are different concepts and say so."""

    def _fields(self, cand):
        embed = source_review.format_source_embed(cand)
        return {f["name"]: f["value"] for f in embed.get("fields", [])}

    def test_5_working_media_window_is_labelled_as_the_working_clock(self):
        fields = self._fields(_candidate())
        self.assertIn("CI moment · working clock", fields)
        self.assertNotIn("Resolved window", fields)
        value = fields["CI moment · working clock"]
        self.assertIn("Start 00:00", value)      # WORKING 0-based, not source-absolute
        self.assertIn("Duration 50s", value)

    def test_5b_a_source_absolute_window_would_not_read_as_working(self):
        cand = _candidate(boundary_plan={"recommended_clip_start": 125.0,
                                         "recommended_clip_end": 175.0,
                                         "event_timestamp": 137.0})
        fields = self._fields(cand)
        # The label states the clock, so a non-zero working start is unambiguous.
        self.assertIn("Start 02:05", fields["CI moment · working clock"])

    def test_7_the_verdict_group_is_a_verdict_not_a_coordinate_range(self):
        fields = self._fields(_candidate())
        self.assertIn("CI completeness & boundary evidence", fields)
        self.assertNotIn("Completeness & boundary evidence", fields)
        value = fields["CI completeness & boundary evidence"]
        self.assertTrue(value.startswith("FAIL"))

    def test_7b_absent_reasons_render_unknown_not_the_literal_None(self):
        fields = self._fields(_candidate())
        value = fields["CI completeness & boundary evidence"]
        self.assertNotIn("None", value)
        self.assertIn("Unavailable", value)


if __name__ == "__main__":
    unittest.main(verbosity=2)
