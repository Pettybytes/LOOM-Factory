"""Human-readable title + filename contract.

Titles are for humans; ids and hashes preserve machine identity.
"""

import sys
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
for _d in ("acquisition", "local-helper", "discord-review"):
    sys.path.insert(0, str(_REPO_ROOT / "infra" / _d))

import title_contract as tc  # noqa: E402

CID = "7598767615980408095"
DIGEST = "0e9af235dacf129a93f64ef40b89eab3512c65d63f1f870f1c7cbc58cddf1d6c"


class TestDisplayTitlePriority(unittest.TestCase):
    def test_priority_1_platform_title_wins(self):
        self.assertEqual(
            tc.resolve_display_title(platform="youtube", content_id="h3Q_tAFWY5g",
                                     source_title="The Most Unhinged Marriage Proposal",
                                     caption="ignored", creator_handle="Lore Addict"),
            "The Most Unhinged Marriage Proposal")

    def test_priority_2_caption_when_no_title(self):
        self.assertEqual(
            tc.resolve_display_title(platform="tiktok", content_id=CID,
                                     source_title="", caption="the actual caption"),
            "the actual caption")

    def test_priority_2b_clip_name(self):
        self.assertEqual(
            tc.resolve_display_title(platform="twitch", content_id="Slug", source_title="",
                                     caption="", clip_name="Duke reacts to the pajamas"),
            "Duke reacts to the pajamas")

    def test_priority_3_stored_metadata(self):
        self.assertEqual(
            tc.resolve_display_title(platform="kick", content_id="clip_1", source_title="",
                                     caption="", stored_title="Free Trey"),
            "Free Trey")

    def test_priority_4_creator_and_content_label(self):
        self.assertEqual(
            tc.resolve_display_title(platform="instagram", content_id="DdQE_RUS0GJ",
                                     source_title="", creator_handle="coco_franklin_"),
            "coco_franklin_ · Instagram Reel")

    def test_priority_5_untitled_only_when_nothing_exists(self):
        self.assertEqual(tc.resolve_display_title(platform="tiktok"), "TikTok video")
        self.assertEqual(tc.resolve_display_title(platform="other", kind=""),
                         "untitled-clip")

    def test_placeholder_titles_are_not_treated_as_metadata(self):
        """A generic label that only restates identity is not a title."""
        self.assertTrue(tc.is_placeholder_title(f"TikTok video #{CID}", CID))
        self.assertTrue(tc.is_placeholder_title("Video by songpxngfit", "x"))
        self.assertTrue(tc.is_placeholder_title("", CID))
        self.assertTrue(tc.is_placeholder_title(CID, CID))
        self.assertFalse(tc.is_placeholder_title("Free Trey", "clip_1"))
        self.assertFalse(tc.is_placeholder_title(
            "James Brown - It's A Man's Man's Man's World", "ClhBXVygWbc"))
        # So the placeholder falls through to creator + label, not to the fake title.
        self.assertEqual(
            tc.resolve_display_title(platform="tiktok", content_id=CID,
                                     source_title=f"TikTok video #{CID}",
                                     creator_handle="thoughtswthot"),
            "thoughtswthot · TikTok video")
        self.assertEqual(
            tc.resolve_display_title(platform="instagram", content_id="DdZS1QRhrXE",
                                     source_title="Video by songpxngfit",
                                     creator_handle="songpxngfit"),
            "songpxngfit · Instagram Reel")

    def test_content_id_is_never_replaced_by_a_title(self):
        fields = tc.title_fields(platform="youtube", content_id="h3Q_tAFWY5g",
                                 digest=DIGEST, source_title="Some Great Title",
                                 creator_name="Lore Addict")
        self.assertEqual(fields["content_id"], "h3Q_tAFWY5g")
        self.assertIn("h3q-tafwy5g", fields["asset_filename"])
        self.assertEqual(fields["display_title"], "Some Great Title")

    def test_fields_are_preserved_separately(self):
        fields = tc.title_fields(platform="twitch", content_id="Slug123", digest=DIGEST,
                                 source_title="Duke reacts to the pajamas",
                                 creator_name="Duke", creator_handle="balpreezy")
        self.assertEqual(fields["source_title"], "Duke reacts to the pajamas")
        self.assertEqual(fields["display_title"], "Duke reacts to the pajamas")
        self.assertEqual(fields["creator_name"], "Duke")
        self.assertEqual(fields["creator_handle"], "balpreezy")
        self.assertEqual(fields["content_id"], "Slug123")
        self.assertTrue(fields["asset_filename"].endswith(".mp4"))


class TestCleaning(unittest.TestCase):
    def test_control_characters_and_separators_are_removed(self):
        self.assertEqual(tc.clean_title("a\u0000b\u202ec"), "abc")
        self.assertEqual(tc.clean_title("path/to\\file"), "path to file")
        self.assertEqual(tc.clean_title("line\nbreak\t tab"), "line break tab")

    def test_whitespace_is_normalized(self):
        self.assertEqual(tc.clean_title("  too    many   spaces  "), "too many spaces")

    def test_leading_and_trailing_dots_are_stripped(self):
        self.assertEqual(tc.clean_title("...hidden..."), "hidden")
        self.assertEqual(tc.clean_title("__snake__"), "snake")

    def test_unicode_is_folded_for_filenames(self):
        self.assertEqual(tc.slug("Café Münster"), "cafe-munster")


class TestFilenameContract(unittest.TestCase):
    def _name(self, **over):
        base = dict(platform="tiktok", creator="thoughtswthot",
                    title="a short source title", content_id=CID, digest=DIGEST)
        base.update(over)
        return tc.build_filename(**base)

    def test_shape_and_lowercase(self):
        name = self._name()
        self.assertEqual(name,
                         f"tiktok__thoughtswthot__a-short-source-title__{CID}__0e9af235dacf.mp4")
        self.assertEqual(name, name.lower())

    def test_content_id_is_complete_and_hash_is_8_to_12(self):
        name = self._name(content_id="AltruisticTallBorkDancingBaby-gb-T9vlskqh7FCfl")
        parts = name.rsplit(".", 1)[0].split("__")
        self.assertEqual(parts[3], "altruistictallborkdancingbaby-gb-t9vlskqh7fcfl")
        self.assertGreaterEqual(len(parts[4]), 8)
        self.assertLessEqual(len(parts[4]), 12)

    def test_title_portion_is_truncated_to_about_60(self):
        long_title = "word " * 40
        name = self._name(title=long_title)
        title_slug = name.rsplit(".", 1)[0].split("__")[2]
        self.assertLessEqual(len(title_slug), tc.MAX_TITLE_SLUG)

    def test_platform_and_creator_are_preserved(self):
        name = self._name(platform="kick", creator="Looselipssinkships")
        self.assertTrue(name.startswith("kick__looselipssinkships__"))

    def test_extension_is_derived_from_validated_media(self):
        self.assertTrue(self._name(ext="mp4").endswith(".mp4"))
        self.assertTrue(self._name(ext="mov").endswith(".mov"))
        self.assertTrue(self._name(ext="MP4").endswith(".mp4"))
        self.assertTrue(self._name(ext="../../etc/passwd").endswith(".etcpasswd"))

    def test_reserved_names_are_prevented(self):
        self.assertEqual(tc.slug("con"), "con-asset")
        self.assertEqual(tc.slug("NUL"), "nul-asset")
        self.assertEqual(tc.slug("lpt1"), "lpt1-asset")
        name = self._name(creator="con")
        self.assertIn("con-asset", name)

    def test_no_leading_or_trailing_dots_in_any_segment(self):
        name = self._name(title="...dots...", creator=".weird.")
        self.assertFalse(name.startswith("."))
        for part in name.rsplit(".", 1)[0].split("__"):
            self.assertFalse(part.startswith(".") or part.endswith("."))
            self.assertNotIn("/", part)
            self.assertNotIn("\\", part)

    def test_missing_title_falls_back_and_never_fabricates(self):
        name = self._name(title="")
        self.assertIn(tc.FALLBACK_TITLE, name)

    def test_poster_keeps_the_video_base_name(self):
        video = self._name()
        poster = tc.poster_filename(video)
        self.assertEqual(poster, video.rsplit(".", 1)[0] + "__poster.jpg")
        # Same stem, same tokens, same order — only the poster suffix differs.
        self.assertEqual(poster.rsplit(".", 1)[0].rsplit("__poster", 1)[0],
                         video.rsplit(".", 1)[0])

    def test_collisions_resolve_deterministically(self):
        name = self._name()
        first = tc.disambiguate(name, set())
        self.assertEqual(first, name)
        second = tc.disambiguate(name, {name})
        third = tc.disambiguate(name, {name})
        self.assertEqual(second, third)              # deterministic
        self.assertNotEqual(second, name)
        self.assertTrue(second.endswith(".mp4"))
        self.assertEqual(tc.disambiguate(name, {name, second}).count("__"), 4)

    def test_a_bad_digest_is_refused(self):
        with self.assertRaises(tc.TitleError) as e:
            tc.build_filename(platform="tiktok", creator="x", title="y",
                              content_id=CID, digest="abc")
        self.assertEqual(e.exception.code, "BAD_DIGEST")

    def test_the_same_inputs_always_produce_the_same_filename(self):
        self.assertEqual(self._name(), self._name())


class TestIdentityIndependence(unittest.TestCase):
    def test_renaming_does_not_change_identity_inputs(self):
        """A title change alters the filename, never the content id or fingerprint."""
        import tiktok_url as tu
        url = "https://www.tiktok.com/@thoughtswthot/video/" + CID
        before = tu.fingerprint_for(url)
        a = tc.title_fields(platform="tiktok", content_id=CID, digest=DIGEST,
                            source_title="first title", creator_handle="thoughtswthot")
        b = tc.title_fields(platform="tiktok", content_id=CID, digest=DIGEST,
                            source_title="a completely different title",
                            creator_handle="thoughtswthot")
        self.assertNotEqual(a["asset_filename"], b["asset_filename"])
        self.assertEqual(a["content_id"], b["content_id"])
        self.assertEqual(tu.fingerprint_for(url), before)


if __name__ == "__main__":
    unittest.main()
