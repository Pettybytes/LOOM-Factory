"""A registered EDITED asset must not claim a storage outcome that never happened.

The edit/publication path deliberately registers no media (no canonical url, no
GHL media id) — later versions re-render from the durable clean source. The
registration RPC stamps the new lineage row with the loom_media column default
('uploaded') regardless, so the record (and the review card that renders the
field) asserted storage that was never performed.

mark_edited_storage_status() is the correction: record 'not_applicable' when the
row carries neither a canonical url nor a GHL media id, and never touch a row
that genuinely carries either. These tests pin that contract, including the
non-fatal behaviour, without touching Supabase.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import adapter  # noqa: E402


class _FakeSB:
    """Stand-in for the two Supabase calls the helper makes."""

    def __init__(self, rows=None, get_error=None):
        self.rows = rows or []
        self.get_error = get_error
        self.patched = []

    def get(self, table, filters):
        if self.get_error:
            raise self.get_error
        return [dict(r) for r in self.rows]

    def patch(self, media_id, updates):
        self.patched.append((media_id, dict(updates)))
        return 204, []


class EditedStorageStatusTests(unittest.TestCase):
    def setUp(self):
        self._get, self._patch = adapter.supabase_filter_get, adapter.supabase_patch_media

    def tearDown(self):
        adapter.supabase_filter_get, adapter.supabase_patch_media = self._get, self._patch

    def _run(self, fake):
        adapter.supabase_filter_get = fake.get
        adapter.supabase_patch_media = fake.patch
        return adapter.mark_edited_storage_status("dreemzy", "clip:0", "render-1.6")

    def test_an_edited_row_with_no_media_records_not_applicable(self):
        fake = _FakeSB([{"id": "row-1", "storage_status": "uploaded",
                         "canonical_url": None, "ghl_media_id": None}])
        self.assertTrue(self._run(fake))
        self.assertEqual(fake.patched,
                         [("row-1", {"storage_status": adapter.STORAGE_NOT_APPLICABLE})])

    def test_a_row_that_really_stored_media_is_left_alone(self):
        for stored in ({"canonical_url": "https://cdn/x.mp4", "ghl_media_id": None},
                       {"canonical_url": None, "ghl_media_id": "abc123"}):
            fake = _FakeSB([dict({"id": "row-1", "storage_status": "uploaded"}, **stored)])
            self.assertFalse(self._run(fake), stored)
            self.assertEqual(fake.patched, [], "must not overwrite a real storage fact")

    def test_an_already_correct_row_is_not_rewritten(self):
        fake = _FakeSB([{"id": "row-1", "storage_status": adapter.STORAGE_NOT_APPLICABLE,
                         "canonical_url": None, "ghl_media_id": None}])
        self.assertFalse(self._run(fake))
        self.assertEqual(fake.patched, [], "idempotent: no gratuitous write")

    def test_a_missing_row_is_a_no_op(self):
        fake = _FakeSB([])
        self.assertFalse(self._run(fake))
        self.assertEqual(fake.patched, [])

    def test_a_backend_failure_is_non_fatal(self):
        self.assertFalse(self._run(_FakeSB(get_error=RuntimeError("supabase down"))))


if __name__ == "__main__":
    unittest.main()
