"""Library titles: durable fields, migration planning, and honesty rules.

Covers the contract end-to-end at the module boundary: the record carries titles
distinct from identity, the migration derives names from stored metadata only, and
no numeric/placeholder value can become a human creator or title.
"""

import sys
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
for _d in ("acquisition", "local-helper", "discord-review"):
    sys.path.insert(0, str(_REPO_ROOT / "infra" / _d))

import migrate_library_titles as mig  # noqa: E402
import reaction_library as rl  # noqa: E402
import title_contract as tc  # noqa: E402

DIGEST = "0e9af235dacf129a93f64ef40b89eab3512c65d63f1f870f1c7cbc58cddf1d6c"


class TestDurableRecord(unittest.TestCase):
    def _record(self, **over):
        base = dict(duplicate_fingerprint="fp", platform="tiktok",
                    platform_content_id="7598767615980408095",
                    canonical_url="https://www.tiktok.com/@thoughtswthot/video/7598767615980408095",
                    acquisition_job_id="", filename="tiktok__x.mp4", digest=DIGEST,
                    size_bytes=10, display_title="thoughtswthot · TikTok video",
                    source_title="", creator_handle="thoughtswthot")
        base.update(over)
        return rl.plan_library_entry(**base)

    def test_titles_are_stored_durably_and_distinct_from_identity(self):
        rec = self._record()
        prov = rec["provenance"]
        self.assertEqual(prov["display_title"], "thoughtswthot · TikTok video")
        self.assertEqual(prov["creator_handle"], "thoughtswthot")
        # Identity is untouched by any title.
        self.assertEqual(rec["platform_content_id"], "7598767615980408095")
        self.assertEqual(rec["asset_hash"], DIGEST)

    def test_no_title_key_is_added_at_the_top_level(self):
        """The table has no title columns yet, so the record must not carry them."""
        rec = self._record()
        for field in rl.TITLE_FIELDS:
            self.assertNotIn(field, rec, f"{field} must live in provenance, not top level")

    def test_reader_handles_both_the_column_and_provenance_shapes(self):
        from_prov = rl.asset_titles({"platform_content_id": "x", "provenance": {
            "display_title": "From provenance", "creator_name": "A", "creator_handle": "a",
            "source_title": "S"}})
        self.assertEqual(from_prov["display_title"], "From provenance")
        self.assertEqual(from_prov["creator_name"], "A")
        # Migration 036 promotes them; a first-class column then wins.
        from_col = rl.asset_titles({"display_title": "From column",
                                    "provenance": {"display_title": "From provenance"}})
        self.assertEqual(from_col["display_title"], "From column")
        # A row with neither is empty, never invented.
        self.assertEqual(rl.asset_titles({"provenance": {}})["display_title"], "")

    def test_a_missing_title_falls_back_without_fabricating(self):
        rec = self._record(display_title="", source_title="", creator_name="",
                           creator_handle="", platform="tiktok")
        self.assertEqual(rec["provenance"]["display_title"], "TikTok video")


class TestMigrationPlanning(unittest.TestCase):
    def test_platform_is_derived_from_the_canonical_url(self):
        """Three live rows carried platform='youtube' while being Twitch/Kick."""
        self.assertEqual(mig.platform_from_url("https://clips.twitch.tv/SomeSlug", "youtube"),
                         "twitch")
        self.assertEqual(mig.platform_from_url("https://www.twitch.tv/videos/2852456270",
                                               "youtube"), "twitch")
        self.assertEqual(mig.platform_from_url("https://kick.com/x/clips/clip_1", "youtube"),
                         "kick")
        self.assertEqual(mig.platform_from_url("https://www.youtube.com/watch?v=abc",
                                               "youtube"), "youtube")
        # A suffix-confusion lookalike is NOT the platform.
        self.assertNotEqual(mig.platform_from_url("https://kick.com.evil.com/x", "youtube"),
                            "kick")

    def test_numeric_identities_are_never_creators(self):
        self.assertEqual(mig._human_identity("7555199644789113870"), "")
        self.assertEqual(mig._human_identity("123030043"), "")
        self.assertEqual(mig._human_identity("@64535476714"), "")
        self.assertEqual(mig._human_identity("balpreezy"), "balpreezy")
        self.assertEqual(mig._human_identity("Lore Addict"), "Lore Addict")

    def test_plan_is_pure_and_uses_stored_metadata(self):
        row = {"id": "row-1", "platform": "youtube", "platform_content_id": "2852456270",
               "canonical_url": "https://www.twitch.tv/videos/2852456270",
               "asset_filename": "twitch__2852456270__7c09e34a74da.mp4", "asset_hash": DIGEST}
        before = dict(row)
        mig.PLATFORM_META["2852456270"] = {"title": "EARLY STREAM! PLAYING CALL OF DUTY",
                                           "creator_name": "Duke", "creator_handle": ""}
        p = mig.plan(row, set())
        self.assertEqual(row, before)                     # no mutation
        self.assertEqual(p["platform_after"], "twitch")   # corrected from the URL
        self.assertTrue(p["platform_corrected"])
        self.assertEqual(p["title_source"], "platform")
        self.assertFalse(p["title_limited"])
        self.assertTrue(p["new_filename"].startswith("twitch__duke__early-stream"))
        # The old machine-only name is preserved for the CAS and the audit.
        self.assertEqual(p["old_filename"], "twitch__2852456270__7c09e34a74da.mp4")

    def test_a_placeholder_title_is_rejected_and_reported(self):
        mig.PLATFORM_META["7598767615980408095"] = {
            "title": "TikTok video #7598767615980408095", "creator_name": "thoughtswthot",
            "creator_handle": ""}
        row = {"id": "row-2", "platform": "tiktok",
               "platform_content_id": "7598767615980408095",
               "canonical_url": "https://www.tiktok.com/@thoughtswthot/video/7598767615980408095",
               "asset_filename": "tiktok__7598767615980408095__0e9af235dacf.mp4",
               "asset_hash": DIGEST}
        p = mig.plan(row, set())
        self.assertEqual(p["title_source"], "derived")
        self.assertTrue(p["title_limited"])               # reported, not hidden
        self.assertEqual(p["source_title"], "")           # the fake title is not kept
        self.assertIn("thoughtswthot", p["display_title"])

    def test_plan_is_reproducible(self):
        row = {"id": "r", "platform": "kick", "platform_content_id": "clip_1",
               "canonical_url": "https://kick.com/a/clips/clip_1",
               "asset_filename": "kick__clip_1__e42ae200bd3a.mp4", "asset_hash": DIGEST}
        mig.PLATFORM_META["clip_1"] = {"title": "Free Trey",
                                       "creator_name": "Looselipssinkships",
                                       "creator_handle": ""}
        self.assertEqual(mig.plan(row, set()), mig.plan(row, set()))

    def test_filename_keeps_the_complete_content_id_and_a_short_hash(self):
        fields = tc.title_fields(platform="twitch", content_id="2852456270", digest=DIGEST,
                                 source_title="EARLY STREAM", creator_name="Duke")
        parts = fields["asset_filename"].rsplit(".", 1)[0].split("__")
        self.assertEqual(parts[0], "twitch")
        self.assertIn("2852456270", fields["asset_filename"])
        self.assertGreaterEqual(len(parts[-1]), 8)
        self.assertLessEqual(len(parts[-1]), 12)


if __name__ == "__main__":
    unittest.main()
