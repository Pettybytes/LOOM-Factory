"""Deterministic tests for the Acquisition Boundary contract.

Run:  python3 -m unittest discover -s infra/acquisition/tests -v
"""

import sys
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO_ROOT / "infra" / "acquisition"))

import acquisition as acq  # noqa: E402
import platform_capabilities as caps  # noqa: E402

FP = "a" * 64


def _base(**kw):
    base = dict(
        fingerprint=FP, platform="youtube", platform_content_id="qgLaCZyKv_8",
        canonical_url="https://www.youtube.com/watch?v=qgLaCZyKv_8",
        authorization_type=acq.AUTH_ACQUIRE_FOR_REACTION, actor="bryan#1",
    )
    base.update(kw)
    return base


class TestDedupKey(unittest.TestCase):
    def test_deterministic(self):
        a = acq.dedup_key(FP, "acquire_for_reaction", "full_item", None, None)
        b = acq.dedup_key(FP, "acquire_for_reaction", "full_item", None, None)
        self.assertEqual(a, b)
        self.assertEqual(len(a), 64)

    def test_distinct_by_range_and_mode(self):
        base = acq.dedup_key(FP, "acquire_for_reaction", "full_item", None, None)
        self.assertNotEqual(base, acq.dedup_key(FP, "acquire_for_reaction", "bounded_segment", 1.0, 5.0))
        self.assertNotEqual(base, acq.dedup_key(FP, "approved_for_acquisition", "full_item", None, None))


class TestPlan(unittest.TestCase):
    def test_blocked_pending_adapter_by_default(self):
        # No platform is runtime-proven yet -> job is blocked, not queued.
        job = acq.plan_acquisition_job(**_base())
        self.assertEqual(job["status"], acq.STATUS_BLOCKED_PENDING_ADAPTER)
        self.assertTrue(job["status_reason"])
        self.assertEqual(job["destination"], "personal_reaction_library")

    def test_queued_when_runtime_proven(self):
        # Monkeypatch capability to simulate a proven adapter.
        orig = caps._CAPABILITY["youtube"]["state"]
        caps._CAPABILITY["youtube"]["state"] = caps.CAP_RUNTIME_PROVEN
        try:
            job = acq.plan_acquisition_job(**_base())
            self.assertEqual(job["status"], acq.STATUS_QUEUED)
            self.assertEqual(job["status_reason"], "")
        finally:
            caps._CAPABILITY["youtube"]["state"] = orig

    def test_bounded_requires_range(self):
        with self.assertRaises(acq.AcquisitionError) as cm:
            acq.plan_acquisition_job(**_base(acquisition_mode="bounded_segment"))
        self.assertEqual(cm.exception.code, "BAD_RANGE")

    def test_bounded_range_valid(self):
        job = acq.plan_acquisition_job(**_base(acquisition_mode="bounded_segment", start_s=1.0, end_s=5.0))
        self.assertEqual(job["acquisition_mode"], "bounded_segment")
        self.assertEqual(job["requested_start_s"], 1.0)
        self.assertEqual(job["requested_end_s"], 5.0)

    def test_bad_range_order(self):
        with self.assertRaises(acq.AcquisitionError):
            acq.plan_acquisition_job(**_base(acquisition_mode="bounded_segment", start_s=5.0, end_s=1.0))

    def test_rejects_collection_no_content_id(self):
        with self.assertRaises(acq.AcquisitionError) as cm:
            acq.plan_acquisition_job(**_base(platform_content_id=""))
        self.assertEqual(cm.exception.code, "AMBIGUOUS_COLLECTION")

    def test_rejects_bad_authorization(self):
        with self.assertRaises(acq.AcquisitionError) as cm:
            acq.plan_acquisition_job(**_base(authorization_type="publish"))
        self.assertEqual(cm.exception.code, "BAD_AUTHORIZATION")

    def test_rejects_bad_platform(self):
        with self.assertRaises(acq.AcquisitionError):
            acq.plan_acquisition_job(**_base(platform="myspace"))

    def test_unassigned_destination_allowed(self):
        job = acq.plan_acquisition_job(**_base(destination=""))
        self.assertEqual(job["destination"], "personal_reaction_library")


class TestCapabilities(unittest.TestCase):
    def test_no_platform_runtime_proven_yet(self):
        self.assertFalse(any(caps.can_acquire(p) for p in caps.PLATFORMS))

    def test_youtube_implemented_not_proven(self):
        self.assertEqual(caps.capability("youtube"), caps.CAP_IMPLEMENTED_NOT_PROVEN)

    def test_matrix_honest(self):
        m = caps.matrix()
        self.assertEqual(set(m), caps.PLATFORMS)
        for p, entry in m.items():
            self.assertIn(entry["state"], caps.CAPABILITY_STATES)
            self.assertTrue(entry["reason"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
