"""GHL folder routing — proven contract, and the fail-loud placement guard.

Live probing (2026-09-26) established the real targeting contract for
``POST /medias/upload-file``:

  * the multipart form field is ``parentId`` carrying the target folder id;
  * ``folderId`` is NOT honoured — GHL answers HTTP 201, silently ignores it, and
    stores the asset in My Media root.

Because an HTTP success can therefore mean "stored in the wrong place", HTTP
success is never treated as placement evidence: the durable record is read back
and its ``parentId`` must match the configured folder.

Offline and deterministic: no network, no GHL, no secrets in output.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock

_REPO = Path(__file__).resolve().parents[3]
for _p in (str(_REPO / "infra" / "discord-review"), str(_REPO / "infra"),
           str(_REPO / "infra" / "acquisition")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import adapter  # noqa: E402
import manual_media_intake as mmi  # noqa: E402

LOOM_ID = "6aa61446df2d015553434919"
FILE_ID = "6ab85b55974a9da6eee866d4"


class _Resp:
    def __init__(self, payload: dict) -> None:
        self._body = json.dumps(payload).encode()

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeGhl:
    """Fake transport: records uploads, serves one listing shape per scenario."""

    def __init__(self, *, placement: str) -> None:
        self.placement = placement          # "in_folder" | "at_root" | "absent"
        self.bodies: list[bytes] = []
        self.list_queries: list[str] = []

    def __call__(self, req, timeout=None):
        url = req.full_url
        if "upload-file" in url:
            self.bodies.append(req.data or b"")
            return _Resp({"fileId": FILE_ID, "url": "https://cdn/x.mp4"})
        if "/medias/files" in url:
            self.list_queries.append(url)
            parent = urllib.parse.parse_qs(urllib.parse.urlparse(url).query).get("parentId", [""])[0]
            if self.placement == "in_folder" and parent == LOOM_ID:
                return _Resp({"files": [{"_id": FILE_ID, "parentId": LOOM_ID}]})
            if self.placement == "at_root" and not parent:
                return _Resp({"files": [{"_id": FILE_ID, "parentId": ""}]})
            if self.placement == "absent":
                return _Resp({"files": []})
            return _Resp({"files": []})
        raise AssertionError(f"unexpected URL {url}")


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
        self.tmp.write(b"fake-video-bytes")
        self.tmp.close()
        self.addCleanup(os.unlink, self.tmp.name)
        self._patch(mock.patch.object(adapter, "GHL_TOKEN", "test-token"))
        self._patch(mock.patch.object(adapter, "GHL_LOCATION_ID", "loc-1"))

    def _patch(self, p):
        p.start()
        self.addCleanup(p.stop)
        return p

    def _run(self, placement, **kw):
        fake = _FakeGhl(placement=placement)
        with mock.patch.object(adapter, "urlopen", fake):
            result = adapter.ghl_upload_file(self.tmp.name, "probe.mp4", **kw)
        return fake, result


class TestProvenContract(_Base):
    def test_configured_folder_is_sent_as_the_parentid_multipart_field(self):
        self._patch(mock.patch.object(adapter, "MANUAL_MEDIA_FOLDER_ID", LOOM_ID))
        self._patch(mock.patch.object(adapter, "GHL_MEDIA_PARENT_ID", LOOM_ID))
        fake, result = self._run("in_folder")
        body = fake.bodies[0]
        self.assertIn(b'name="parentId"', body)
        self.assertIn(LOOM_ID.encode(), body)
        # The silently-ignored field must NOT be used as the targeting mechanism.
        self.assertNotIn(b'name="folderId"', body)
        self.assertEqual(result["fileId"], FILE_ID)

    def test_an_explicit_folder_argument_wins_over_the_default(self):
        self._patch(mock.patch.object(adapter, "GHL_MEDIA_PARENT_ID", LOOM_ID))
        other = "1" * 24
        fake, _ = self._run("in_folder", parent_id=other)
        self.assertIn(other.encode(), fake.bodies[0])
        self.assertNotIn(LOOM_ID.encode(), fake.bodies[0])


class TestPlacementVerification(_Base):
    def test_matching_parentid_is_reported_as_verified(self):
        self._patch(mock.patch.object(adapter, "GHL_MEDIA_PARENT_ID", LOOM_ID))
        _, result = self._run("in_folder")
        self.assertTrue(result["placement_verified"])
        self.assertEqual(result["placement_actual"], LOOM_ID)
        self.assertEqual(result["placement_reason"], "in_target_folder")

    def test_http_201_with_root_placement_fails_loudly(self):
        self._patch(mock.patch.object(adapter, "GHL_MEDIA_PARENT_ID", LOOM_ID))
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            _, result = self._run("at_root")
        # The upload itself still succeeded (durable media lifecycle preserved)...
        self.assertEqual(result["url"], "https://cdn/x.mp4")
        # ...but placement is NOT represented as success.
        self.assertFalse(result["placement_verified"])
        self.assertEqual(result["placement_actual"], "")
        self.assertEqual(result["placement_reason"], "landed_at_my_media_root")
        self.assertIn("ROUTING FAILURE", buf.getvalue())
        self.assertIn(LOOM_ID, buf.getvalue())

    def test_record_not_yet_listed_is_not_claimed_as_success(self):
        self._patch(mock.patch.object(adapter, "GHL_MEDIA_PARENT_ID", LOOM_ID))
        _, result = self._run("absent")
        self.assertFalse(result["placement_verified"])
        self.assertIsNone(result["placement_actual"])
        self.assertEqual(result["placement_reason"], "not_listed_yet")

    def test_verification_failure_never_raises_or_breaks_the_upload(self):
        self._patch(mock.patch.object(adapter, "GHL_MEDIA_PARENT_ID", LOOM_ID))
        with mock.patch.object(adapter, "_ghl_list_media_files",
                               side_effect=RuntimeError("listing exploded")):
            fake = _FakeGhl(placement="in_folder")
            with mock.patch.object(adapter, "urlopen", fake):
                result = adapter.ghl_upload_file(self.tmp.name, "probe.mp4")
        # The upload still succeeds and stays durable...
        self.assertEqual(result["url"], "https://cdn/x.mp4")
        # ...but placement is NOT claimed, and the reason is diagnostic.
        self.assertFalse(result["placement_verified"])
        self.assertTrue(result["placement_reason"].startswith("verification_error:"))


class TestMissingConfiguration(_Base):
    def test_no_configured_folder_sends_no_parentid_and_verifies_root(self):
        self._patch(mock.patch.object(adapter, "GHL_MEDIA_PARENT_ID", ""))
        self._patch(mock.patch.object(adapter, "MANUAL_MEDIA_FOLDER_ID", ""))
        fake = _FakeGhl(placement="at_root")
        with mock.patch.object(adapter, "urlopen", fake):
            result = adapter.ghl_upload_file(self.tmp.name, "probe.mp4")
        self.assertNotIn(b'name="parentId"', fake.bodies[0])
        self.assertTrue(result["placement_verified"])
        self.assertEqual(result["placement_reason"], "root_by_configuration")

    def test_missing_token_or_file_still_returns_none_without_uploading(self):
        with mock.patch.object(adapter, "GHL_TOKEN", None):
            self.assertIsNone(adapter.ghl_upload_file(self.tmp.name, "probe.mp4"))
        with mock.patch.object(adapter, "GHL_TOKEN", "test-token"):
            self.assertIsNone(adapter.ghl_upload_file("/nonexistent/none.mp4", "x.mp4"))


class TestNoRegressionToTheUploadShape(_Base):
    def test_existing_multipart_fields_are_unchanged(self):
        self._patch(mock.patch.object(adapter, "GHL_MEDIA_PARENT_ID", LOOM_ID))
        fake, _ = self._run("in_folder")
        body = fake.bodies[0]
        for part in (b'name="file"', b'name="name"', b'name="hosted"'):
            self.assertIn(part, body)
        self.assertIn(b"fake-video-bytes", body)

    def test_store_durable_media_propagates_placement_evidence(self):
        self._patch(mock.patch.object(adapter, "MANUAL_MEDIA_FOLDER_ID", LOOM_ID))
        self._patch(mock.patch.object(adapter, "GHL_MEDIA_PARENT_ID", LOOM_ID))
        fake = _FakeGhl(placement="in_folder")
        with mock.patch.object(adapter, "urlopen", fake):
            ref = mmi.store_durable_media(self.tmp.name, "probe.mp4", folder_id=LOOM_ID)
        self.assertEqual(ref["folder_id"], LOOM_ID)
        self.assertTrue(ref["placement_verified"])
        self.assertEqual(ref["placement_actual"], LOOM_ID)

    def test_store_durable_media_keeps_the_asset_but_flags_root_placement(self):
        self._patch(mock.patch.object(adapter, "MANUAL_MEDIA_FOLDER_ID", LOOM_ID))
        self._patch(mock.patch.object(adapter, "GHL_MEDIA_PARENT_ID", LOOM_ID))
        import io
        import contextlib
        fake = _FakeGhl(placement="at_root")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), mock.patch.object(adapter, "urlopen", fake):
            ref = mmi.store_durable_media(self.tmp.name, "probe.mp4", folder_id=LOOM_ID)
        # The durable pointer is still returned (media lifecycle not broken)...
        self.assertEqual(ref["file_id"], FILE_ID)
        # ...while the routing failure is explicit and diagnostic.
        self.assertFalse(ref["placement_verified"])
        self.assertIn("ROUTING FAILURE", buf.getvalue())


if __name__ == "__main__":
    unittest.main(verbosity=2)
