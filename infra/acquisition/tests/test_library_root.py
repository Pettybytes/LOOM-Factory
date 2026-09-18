"""Reaction Library root: centralization, override, and relocation guards.

Run:  python3 -m unittest discover -s infra/acquisition/tests -v

These tests exist so the 2026-09-18 relocation cannot silently regress: the active
root must resolve through exactly one module, honour an environment override, and
no runtime module may hardcode the retired ~/.openclaw path again.
"""

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

_REPO_ROOT = Path(__file__).resolve().parents[3]
for d in ("acquisition", "discord-review"):
    sys.path.insert(0, str(_REPO_ROOT / "infra" / d))

import library_root as lr  # noqa: E402

RETIRED_FRAGMENT = ".openclaw/loom-reaction-library"
RETIRED_FRAGMENT_SLASHED = "\".openclaw\" / \"loom-reaction-library\""


class TestResolution(unittest.TestCase):
    def test_default_root_is_the_visible_movies_location(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(lr.ENV_VAR, None)
            self.assertEqual(lr.library_root(), Path.home() / "Movies" / "LOOM-Reaction-Library")

    def test_environment_override_wins(self):
        with mock.patch.dict(os.environ, {lr.ENV_VAR: "/tmp/loom-alt-root"}, clear=False):
            # library_root() canonicalizes, so /tmp resolves to /private/tmp on macOS
            expected = Path("/tmp/loom-alt-root").resolve()
            self.assertEqual(lr.library_root(), expected)
            self.assertEqual(lr.library_dir(), expected / "library")
            self.assertEqual(lr.tmp_dir(), expected / "tmp")
            self.assertTrue(lr.describe()["override_set"])

    def test_subpaths_derive_from_the_root(self):
        root = lr.library_root()
        self.assertEqual(lr.library_dir(), root / "library")
        self.assertEqual(lr.tmp_dir(), root / "tmp")

    def test_retired_and_backup_paths_are_not_the_active_root(self):
        self.assertNotEqual(lr.library_root(), lr.legacy_root())
        self.assertNotEqual(lr.library_root(), lr.backup_root())
        self.assertIn(".backup-2026-09-18", str(lr.backup_root()))

    def test_describe_reports_the_active_root_and_never_the_legacy_one(self):
        d = lr.describe()
        self.assertEqual(d["root"], str(lr.library_root()))
        self.assertFalse(d["legacy_root_active"])
        self.assertIn(d["env_var"], ("LOOM_REACTION_LIBRARY_ROOT",))


class TestNoHardcodedRetiredRoot(unittest.TestCase):
    """A repo-wide guard: runtime code must go through library_root only."""

    def _sources(self):
        infra = _REPO_ROOT / "infra"
        for p in sorted(infra.rglob("*.py")):
            rel = p.relative_to(_REPO_ROOT).as_posix()
            if rel.endswith("acquisition/library_root.py") or "/tests/" in rel:
                continue
            yield rel, p

    def test_no_runtime_module_hardcodes_the_retired_root(self):
        offenders = []
        for rel, p in self._sources():
            text = p.read_text(encoding="utf-8", errors="replace")
            if RETIRED_FRAGMENT in text or RETIRED_FRAGMENT_SLASHED in text:
                offenders.append(rel)
        self.assertEqual(offenders, [], f"still hardcoding the retired root: {offenders}")

    def test_library_root_module_owns_both_legacy_constants(self):
        text = (Path(__file__).resolve().parents[1] / "library_root.py").read_text(encoding="utf-8")
        self.assertIn("LEGACY_ROOT", text)
        self.assertIn("BACKUP_ROOT", text)


class TestGalleryRendering(unittest.TestCase):
    """The browsable index must show every required field and link to detail."""

    def setUp(self):
        helper_dir = _REPO_ROOT / "infra" / "local-helper"
        sys.path.insert(0, str(helper_dir))
        try:
            import helper  # noqa: F401
            self.helper = helper
        except Exception as e:  # noqa: BLE001
            self.skipTest(f"local helper not importable here: {type(e).__name__}")

    def test_gallery_lists_assets_with_metadata_and_detail_links(self):
        records = [{
            "asset_hash": "a" * 64, "asset_filename": "instagram__X__aaaa.mp4",
            "platform": "instagram", "duration_seconds": 77.6, "size_bytes": 16689411,
            "created_at": "2026-09-18T21:48:51.711423+00:00",
            "acquisition_job_id": "job-1",
        }]
        html = self.helper.render_gallery_page(records, {"job-1": {"status": "acquired"}}).decode()
        self.assertIn(f'href="/library/{"a" * 64}"', html)      # opens the detail page
        self.assertIn("instagram", html)
        self.assertIn("1:17", html)                              # duration rendering
        self.assertIn("15.9 MiB", html)
        self.assertIn("acquired", html)
        self.assertIn("2026-09-18 21:48:51", html)

    def test_empty_library_says_so_rather_than_rendering_nothing(self):
        html = self.helper.render_gallery_page([], {}).decode()
        self.assertIn("No assets in the library yet", html)

    def test_poster_is_referenced_only_when_present(self):
        base = {"asset_filename": "x.mp4", "platform": "instagram", "created_at": "2026-01-01"}
        with_poster = dict(base, asset_hash="b" * 64)
        html = self.helper.render_gallery_page([with_poster], {}).decode()
        self.assertIn(f'/library/{"b" * 64}/poster', html) if self.helper.poster_path_for(with_poster) else \
            self.assertIn("no poster", html)


if __name__ == "__main__":
    unittest.main()
