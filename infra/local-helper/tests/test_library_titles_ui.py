"""Reaction Library pages: the human title leads, identity stays secondary.

Renderer-only tests with synthetic rows, so they need no database and no server.
"""

import sys
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
for _d in ("acquisition", "local-helper", "discord-review"):
    sys.path.insert(0, str(_REPO_ROOT / "infra" / _d))

import helper  # noqa: E402

DIGEST = "06ab279dcb134efc043bb99b068e7aa1aaf4f5fe21d99abef2a0dc13a334430a"
ROW = {
    "id": "row-1",
    "platform": "youtube",
    "platform_content_id": "h3Q_tAFWY5g",
    "canonical_url": "https://www.youtube.com/watch?v=h3Q_tAFWY5g",
    "asset_filename": "youtube__lore-addict__the-most-unhinged-marriage-proposal-in-mushoku-tensei__h3q-tafwy5g__06ab279dcb13.mp4",
    "asset_hash": DIGEST,
    "size_bytes": 19977430,
    "duration_seconds": 40.64,
    "content_type": "video/mp4",
    "acquisition_job_id": "",
    "created_at": "2026-09-18T19:20:00Z",
    "provenance": {
        "display_title": "The Most Unhinged Marriage Proposal in Mushoku Tensei",
        "source_title": "The Most Unhinged Marriage Proposal in Mushoku Tensei",
        "creator_name": "Lore Addict",
        "creator_handle": "",
    },
}
UNTITLED_ROW = dict(ROW, id="row-2", platform="tiktok",
                    asset_filename="tiktok__thoughtswthot__thoughtswthot-tiktok-video__1__0e9af235dacf.mp4",
                    provenance={"display_title": "thoughtswthot · TikTok video",
                                "source_title": "", "creator_name": "thoughtswthot",
                                "creator_handle": ""})


class TestGalleryPage(unittest.TestCase):
    def setUp(self):
        self.html = helper.render_gallery_page([ROW, UNTITLED_ROW], {}).decode()

    def test_human_title_is_the_primary_label(self):
        self.assertIn('class="title"', self.html)
        self.assertIn("The Most Unhinged Marriage Proposal in Mushoku Tensei", self.html)

    def test_creator_and_filename_are_shown(self):
        self.assertIn("Lore Addict", self.html)
        self.assertIn("youtube__lore-addict__", self.html)
        self.assertIn('class="file"', self.html)

    def test_the_machine_filename_is_no_longer_the_heading(self):
        self.assertNotIn('class="name"', self.html)

    def test_a_derived_title_still_renders(self):
        self.assertIn("thoughtswthot · TikTok video", self.html)

    def test_a_row_without_titles_falls_back_to_its_filename(self):
        bare = dict(ROW, id="r3", asset_filename="kick__clip__e42ae200bd3a.mp4",
                    provenance={})
        html = helper.render_gallery_page([bare], {}).decode()
        self.assertIn("kick__clip__e42ae200bd3a.mp4", html)


class TestDetailPage(unittest.TestCase):
    def setUp(self):
        self.html = helper.render_library_page(ROW, {}).decode()

    def test_the_title_is_the_page_heading(self):
        self.assertEqual(self.html.count("<h1>"), 1)
        self.assertIn("<h1>The Most Unhinged Marriage Proposal in Mushoku Tensei</h1>",
                      self.html)

    def test_identity_fields_remain_available(self):
        self.assertIn("Source title", self.html)
        self.assertIn("Creator", self.html)
        self.assertIn(DIGEST, self.html)
        self.assertIn("youtube__lore-addict__", self.html)

    def test_platform_and_creator_are_in_the_subtitle(self):
        self.assertIn("youtube · Lore Addict", self.html)

    def test_the_tab_title_uses_the_human_title(self):
        self.assertIn("<title>Reaction Library · The Most Unhinged", self.html)

    def test_html_is_escaped(self):
        row = dict(ROW, provenance={"display_title": "<script>alert(1)</script>"})
        html = helper.render_library_page(row, {}).decode()
        self.assertNotIn("<script>alert(1)</script>", html)


if __name__ == "__main__":
    unittest.main()
