"""Focused regression: Kick VOD start hint through the SHARED adapter contract.

Kick URLs carry `?t=<seconds>` (e.g. `?t=82901` -> 23:01:41). The adapter already
parsed that value; this coverage pins that it is SURFACED through the one shared
contract and reused by Manual Intake — not re-parsed, not Kick-specific.

No fake acquisition is asserted here: this is the hint/identity seam only. A Kick VOD
LIVE PASS requires the operator path plus runtime/persistence/storage evidence.
"""

import hashlib
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
for directory in ("acquisition", "discord-review"):
    sys.path.insert(0, str(ROOT / "infra" / directory))

import acquisition_ops  # noqa: E402,F401  (order sentinel for approved_lane)
import acquisition_ops as ao                      # noqa: E402
import kick_url as ku                             # noqa: E402
import manual_intake as mi                        # noqa: E402
import manual_media_intake as mmi                 # noqa: E402
import source_adapters as sa                      # noqa: E402
import source_window as sw                        # noqa: E402
import twitch_url as tu                           # noqa: E402

UUID = "01a0a884-c688-7da0-b21d-9913754d421f"
BASE = f"https://kick.com/deenthegreat/videos/{UUID}"
STAMPED = f"{BASE}?t=82901"
HINT_MS = 82_901_000                     # 82,901 s = 23:01:41
TWITCH_STAMPED = "https://www.twitch.tv/videos/2880604198?t=00h26m10s"
YT = "https://www.youtube.com/watch?v=59xxeJCbzaM"


class HintSeamTests(unittest.TestCase):
    def test_timestamp_is_parsed_as_82901000_ms(self):
        self.assertEqual(int(ku.parse(STAMPED)["start_hint_ms"]), HINT_MS)
        self.assertEqual(sw.human_clock(HINT_MS), "23:01:41")

    def test_shared_registry_exposes_the_hint_for_kick(self):
        self.assertEqual(sa.source_start_hint_ms("kick", STAMPED), HINT_MS)

    def test_the_hook_reuses_the_existing_parser_not_a_second_one(self):
        """The surfaced value must BE the adapter's own parse result."""
        self.assertEqual(sa.source_start_hint_ms("kick", STAMPED),
                         int(ku.parse(STAMPED)["start_hint_ms"]))
        src = (ROOT / "infra" / "acquisition" / "source_adapters.py").read_text()
        for reinvented in ("_time_hint", "start_hint_ms =", "re.compile(r\"(\\d+)([hms])\""):
            self.assertNotIn(reinvented, src,
                             "the shared layer must not re-implement timestamp parsing")

    def test_operator_path_sees_the_hint(self):
        self.assertEqual(mmi.resolve_source_url(STAMPED).get("start_hint_ms"), HINT_MS)

    def test_modal_receives_230141_and_end_is_not_invented(self):
        hint = mmi.resolve_source_url(STAMPED)["start_hint_ms"]
        modal = ao.manual_window_modal_payload(
            "ref1", message_id="m1", start_value=sw.human_clock(hint))["data"]
        start = modal["components"][0]["components"][0]
        end = modal["components"][1]["components"][0]
        self.assertEqual(start["value"], "23:01:41")
        self.assertTrue(start["required"])
        self.assertNotIn("value", end)          # never invented
        self.assertTrue(end["required"])        # operator must supply it

    def test_the_modal_is_still_the_one_shared_modal(self):
        manual = ao.manual_window_modal_payload("r")["data"]
        lane = ao.window_modal_payload("r")["data"]
        self.assertEqual(manual["components"], lane["components"])
        self.assertEqual(manual["custom_id"].split(":")[0],
                         ao.MANUAL_WINDOW_MODAL_CUSTOM_ID)


class IdentityTests(unittest.TestCase):
    def test_canonical_url_is_hint_free(self):
        self.assertEqual(mi.validate_and_canonicalize(STAMPED)["canonical_url"], BASE)
        self.assertEqual(mmi.resolve_source_url(STAMPED)["canonical_url"], BASE)

    def test_dedup_identity_is_identical_with_and_without_the_hint(self):
        a = mi.validate_and_canonicalize(STAMPED)
        b = mi.validate_and_canonicalize(BASE)
        self.assertEqual(a["canonical_url"], b["canonical_url"])
        self.assertEqual(mi.submission_fingerprint("kick", a["canonical_url"]),
                         mi.submission_fingerprint("kick", b["canonical_url"]))

    def test_the_original_url_keeps_the_hint_as_non_identity_provenance(self):
        payload = mi.build_submission(url=STAMPED, destination="dreemzy",
                                      content_format="reaction_single",
                                      submitter="bryan", active_tenants={"dreemzy"})
        self.assertEqual(payload["source_url"], BASE)      # identity: hint-free
        self.assertEqual(payload["original_url"], STAMPED)  # provenance: verbatim

    def test_identity_is_the_vod_uuid_and_kind_is_vod(self):
        r = mmi.resolve_source_url(STAMPED)
        self.assertEqual(r["kind"], "vod")
        self.assertEqual(r["content_id"], UUID)


class MalformedAndRegressionTests(unittest.TestCase):
    def test_malformed_hint_fails_safely(self):
        for bad in ("?t=notanumber", "?t=", "?t=--", "?t=zzz"):
            with self.subTest(t=bad):
                url = f"{BASE}{bad}"
                self.assertIsNone(sa.source_start_hint_ms("kick", url))
                self.assertEqual(mi.validate_and_canonicalize(url)["canonical_url"], BASE)

    def test_a_repeated_param_takes_the_first_value(self):
        """A duplicate `t` is not malformed: the operator's FIRST value is the hint.

        Pinned deliberately so a future change cannot silently start honouring the
        trailing junk instead of the value the operator actually shared.
        """
        self.assertEqual(
            sa.source_start_hint_ms("kick", f"{BASE}?t=1h2m3s&t=zzz"), 3_723_000)

    def test_a_hint_cannot_move_the_canonical_identity(self):
        self.assertEqual(sa.source_start_hint_ms("kick", BASE), None)
        self.assertEqual(mi.validate_and_canonicalize(BASE)["canonical_url"], BASE)

    def test_twitch_behaviour_is_unchanged(self):
        self.assertEqual(sa.source_start_hint_ms("twitch", TWITCH_STAMPED), 1_570_000)
        self.assertEqual(sw.human_clock(1_570_000), "26:10")
        self.assertEqual(mi.validate_and_canonicalize(TWITCH_STAMPED)["canonical_url"],
                         "https://www.twitch.tv/videos/2880604198")

    def test_youtube_behaviour_is_unchanged(self):
        self.assertIsNone(sa.source_start_hint_ms("youtube", YT))
        self.assertEqual(mmi.resolve_source_url(YT)["canonical_url"], YT)
        self.assertNotIn("start_hint_ms", mmi.resolve_source_url(YT))

    def test_a_clip_with_a_hint_still_carries_no_identity_change(self):
        clip = "https://kick.com/deenthegreat/clips/clip_01M33XASMP29XTQ1QK0EV7TXDC?t=90"
        parsed = ku.parse(clip)
        self.assertEqual(parsed["kind"], "clip")
        self.assertNotIn("t=", parsed["canonical_url"])
        self.assertEqual(sa.source_start_hint_ms("kick", clip), 90_000)


if __name__ == "__main__":
    unittest.main(verbosity=2)
