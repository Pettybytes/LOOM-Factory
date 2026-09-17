"""Deterministic tests for the Personal Reaction Library contract.

Run:  python3 -m unittest discover -s infra/acquisition/tests -v
"""

import sys
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO_ROOT / "infra" / "acquisition"))

import reaction_library as rl  # noqa: E402

FP = "a" * 64
DIGEST = "b" * 64


def _base(**kw):
    base = dict(
        duplicate_fingerprint=FP, platform="youtube", platform_content_id="qgLaCZyKv_8",
        canonical_url="https://www.youtube.com/watch?v=qgLaCZyKv_8",
        acquisition_job_id="00000000-0000-0000-0000-000000000000",
        filename=rl.filename_for("youtube", "qgLaCZyKv_8", DIGEST),
        digest=DIGEST, size_bytes=123456,
    )
    base.update(kw)
    return base


class TestFilename(unittest.TestCase):
    def test_deterministic_and_disambiguating(self):
        f = rl.filename_for("youtube", "qgLaCZyKv_8", DIGEST)
        self.assertTrue(f.startswith("youtube__qgLaCZyKv_8__"))
        self.assertTrue(f.endswith(".mp4"))
        self.assertIn(DIGEST[:12], f)

    def test_sanitizes_input(self):
        f = rl.filename_for("YouTube!", "../evil", DIGEST)
        self.assertNotIn("..", f)
        self.assertNotIn("!", f)


class TestPlanEntry(unittest.TestCase):
    def test_valid(self):
        rec = rl.plan_library_entry(**_base())
        self.assertEqual(rec["asset_hash"], DIGEST)
        self.assertEqual(rec["destination"], "personal_reaction_library")
        self.assertEqual(rec["retention_days"], rl.DEFAULT_RETENTION_DAYS)
        self.assertEqual(rec["provenance"]["duplicate_fingerprint"], FP)

    def test_rejects_bad_digest(self):
        with self.assertRaises(ValueError):
            rl.plan_library_entry(**_base(digest="short"))

    def test_rejects_bad_content_type(self):
        with self.assertRaises(ValueError):
            rl.plan_library_entry(**_base(content_type="text/html"))

    def test_rejects_bad_retention(self):
        with self.assertRaises(ValueError):
            rl.plan_library_entry(**_base(retention_days=0))

    def test_dedup_digest_is_hash(self):
        rec = rl.plan_library_entry(**_base())
        self.assertEqual(rl.dedup_digest(rec), DIGEST)


class TestHash(unittest.TestCase):
    def test_sha256(self):
        import hashlib
        self.assertEqual(rl.sha256_hex(b"abc"), hashlib.sha256(b"abc").hexdigest())


if __name__ == "__main__":
    unittest.main(verbosity=2)
