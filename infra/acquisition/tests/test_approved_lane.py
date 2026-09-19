"""Deterministic tests for the approved acquisition lane (two human gates).

Run:  python3 -m unittest discover -s infra/acquisition/tests -v

No I/O, no provider call, no spend. Proves the properties the lane must hold:
gate separation, one pending job, one authorized job, clamped pilot bounds,
runtime-proven-only dispatch, one attempt, and record hygiene.
"""

import sys
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
for d in ("acquisition", "discord-review"):
    sys.path.insert(0, str(_REPO_ROOT / "infra" / d))

import acquisition  # noqa: E402
import approved_lane as lane  # noqa: E402

FP = "a" * 64
REEL = "https://www.instagram.com/reel/DdZS1QRhrXE/"
CID = "DdZS1QRhrXE"


def pending(**over):
    kw = dict(fingerprint=FP, platform="instagram", platform_content_id=CID,
              canonical_url=REEL, actor="bryan")
    kw.update(over)
    return lane.plan_pending_job(**kw)


class TestGateOne(unittest.TestCase):
    def test_pending_job_is_qualified_but_not_spend_authorized(self):
        job = pending()
        self.assertEqual(job["authorization_type"], acquisition.AUTH_APPROVED_FOR_ACQUISITION)
        self.assertEqual(job["status"], acquisition.STATUS_QUEUED)
        self.assertEqual(job["status_reason"], lane.REASON_PENDING_AUTHORIZATION)
        self.assertFalse(lane.is_spend_authorized(job))
        with self.assertRaises(lane.LaneError) as e:
            lane.require_spend_authorization(job)
        self.assertEqual(e.exception.code, "NOT_SPEND_AUTHORIZED")

    def test_pending_job_is_deterministic_and_single(self):
        self.assertEqual(pending()["dedup_key"], pending()["dedup_key"])

    def test_pending_job_carries_pilot_bounds(self):
        job = pending()
        self.assertEqual(job["max_bytes"], lane.PILOT_MAX_BYTES)
        self.assertEqual(job["max_duration_seconds"], lane.PILOT_MAX_DURATION_SECONDS)
        self.assertEqual(job["acquisition_mode"], acquisition.MODE_FULL_ITEM)

    def test_predicted_cost_is_not_part_of_authorization(self):
        # Gate 1 records qualification only: no asset, no cost, no download.
        job = pending()
        self.assertIsNone(job["asset_provenance"])
        self.assertEqual(job["retry_count"], 0)

    def test_ambiguous_source_rejected(self):
        with self.assertRaises(acquisition.AcquisitionError) as e:
            pending(platform_content_id="")
        self.assertEqual(e.exception.code, "AMBIGUOUS_COLLECTION")


class TestGateTwo(unittest.TestCase):
    def test_authorized_job_requires_a_pending_job(self):
        with self.assertRaises(lane.LaneError) as e:
            lane.plan_authorized_job(pending_job=None, actor="bryan")
        self.assertEqual(e.exception.code, "NO_PENDING_JOB")

    def test_authorized_job_rejects_non_pending_input(self):
        bad = dict(pending())
        bad["authorization_type"] = acquisition.AUTH_ACQUIRE_FOR_REACTION
        with self.assertRaises(lane.LaneError) as e:
            lane.plan_authorized_job(pending_job=bad, actor="bryan")
        self.assertEqual(e.exception.code, "BAD_PENDING_JOB")

    def test_two_gates_are_separate_records(self):
        p = pending()
        a = lane.plan_authorized_job(pending_job=p, actor="bryan")
        self.assertEqual(a["authorization_type"], acquisition.AUTH_ACQUIRE_FOR_REACTION)
        self.assertNotEqual(a["dedup_key"], p["dedup_key"])          # two distinct rows
        self.assertEqual(a["correlation_id"], p["dedup_key"])         # gate 2 -> gate 1
        self.assertTrue(lane.is_spend_authorized(a))
        lane.require_spend_authorization(a)                           # does not raise

    def test_authorized_job_is_idempotent(self):
        p = pending()
        a1 = lane.plan_authorized_job(pending_job=p, actor="bryan")
        a2 = lane.plan_authorized_job(pending_job=p, actor="bryan")
        self.assertEqual(a1["dedup_key"], a2["dedup_key"])

    def test_authorized_job_keeps_pilot_bounds(self):
        a = lane.plan_authorized_job(pending_job=pending(), actor="bryan")
        self.assertEqual(a["max_bytes"], lane.PILOT_MAX_BYTES)
        self.assertEqual(a["max_duration_seconds"], lane.PILOT_MAX_DURATION_SECONDS)


class TestBounds(unittest.TestCase):
    def test_missing_bounds_are_set_to_the_pilot_bound(self):
        r = lane.enforce_pilot_bounds({})
        self.assertEqual(r["patch"]["max_bytes"], lane.PILOT_MAX_BYTES)
        self.assertEqual(r["patch"]["max_duration_seconds"], lane.PILOT_MAX_DURATION_SECONDS)

    def test_oversized_bounds_are_clamped_and_flagged(self):
        r = lane.enforce_pilot_bounds({"max_bytes": 5 * 1024 ** 3,
                                       "max_duration_seconds": 3600})
        self.assertTrue(r["clamped"])
        self.assertEqual(r["patch"]["max_bytes"], lane.PILOT_MAX_BYTES)
        self.assertEqual(r["patch"]["max_duration_seconds"], lane.PILOT_MAX_DURATION_SECONDS)

    def test_tighter_bounds_are_preserved(self):
        r = lane.enforce_pilot_bounds({"max_bytes": 10 * 1024 * 1024,
                                       "max_duration_seconds": 30})
        self.assertFalse(r["clamped"])
        self.assertNotIn("max_bytes", r["patch"])

    def test_absurd_values_are_replaced(self):
        r = lane.enforce_pilot_bounds({"max_bytes": -1, "max_duration_seconds": "x"})
        self.assertEqual(r["patch"]["max_bytes"], lane.PILOT_MAX_BYTES)
        self.assertEqual(r["patch"]["max_duration_seconds"], lane.PILOT_MAX_DURATION_SECONDS)


class TestRuntimeProvenAndAttempts(unittest.TestCase):
    def test_instagram_is_dispatchable(self):
        lane.require_runtime_proven({"platform": "instagram"})

    def test_unproven_platform_is_refused(self):
        # tiktok became runtime-proven on 2026-09-18 (apidojo provider proof), so a
        # platform without a bounded proof exercises this gate instead.
        with self.assertRaises(lane.LaneError) as e:
            lane.require_runtime_proven({"platform": "reddit"})
        self.assertEqual(e.exception.code, "NOT_RUNTIME_PROVEN")

    def test_tiktok_is_wired_to_the_proven_actor_only(self):
        import provider
        lane.require_runtime_proven({"platform": "tiktok"})
        self.assertEqual(lane.default_provider_actor("tiktok"),
                         provider.ACTOR_TIKTOK_VIDEO)
        # Clockworks is neither pinned nor allowlisted, so it can never be
        # auto-selected (and no fallback exists to reach it).
        with self.assertRaises(provider.ProviderError) as e:
            provider.allowed_actor("tiktok", "clockworks~tiktok-scraper")
        self.assertEqual(e.exception.code, "UNKNOWN_ACTOR")
        with self.assertRaises(lane.LaneError) as e2:
            lane.default_provider_actor("reddit")
        self.assertEqual(e2.exception.code, "NO_PROVIDER_ACTOR")

    def test_one_attempt_then_explicit_retry(self):
        self.assertTrue(lane.attempts_allowed({"retry_count": 0}))
        self.assertFalse(lane.attempts_allowed({"retry_count": 1}))
        self.assertTrue(lane.attempts_allowed({"retry_count": 1}, retry_authorized=True))
        self.assertFalse(lane.attempts_allowed({"retry_count": 2}, retry_authorized=True))

    def test_status_mapping(self):
        self.assertEqual(lane.next_status_after_result(True), acquisition.STATUS_ACQUIRED)
        self.assertEqual(lane.next_status_after_result(False), acquisition.STATUS_FAILED)


class TestRepeatAcquisition(unittest.TestCase):
    def test_library_match_by_content_id(self):
        rows = [{"platform_content_id": CID, "duplicate_fingerprint": "b" * 64}]
        self.assertTrue(lane.library_has_content(rows, platform_content_id=CID))
        self.assertFalse(lane.library_has_content(rows, platform_content_id="OTHER"))

    def test_library_match_by_fingerprint(self):
        rows = [{"platform_content_id": "", "duplicate_fingerprint": FP}]
        self.assertTrue(lane.library_has_content(rows, fingerprint=FP))

    def test_empty_library_never_matches(self):
        self.assertFalse(lane.library_has_content([], platform_content_id=CID))
        self.assertFalse(lane.library_has_content(None, fingerprint=FP))


class TestRecordHygiene(unittest.TestCase):
    def test_persistable_job_keeps_its_schema_columns(self):
        """authorization_type records which gate authorized the job — it is data,
        not a credential, and must survive scrubbing for the DB insert."""
        rec = lane.safe_record(lane.plan_pending_job(
            fingerprint=FP, platform="instagram", platform_content_id=CID,
            canonical_url=REEL, actor="bryan"))
        self.assertEqual(rec["authorization_type"], "approved_for_acquisition")
        self.assertIn("dedup_key", rec)
        self.assertIn("max_bytes", rec)

    def test_credential_shaped_fields_are_dropped(self):
        rec = lane.safe_record({"token": "abc", "cookie": "s=1", "authorization": "Bearer x",
                                "api_key": "k", "secret": "s", "status": "ok"})
        self.assertEqual(rec, {"status": "ok"})

    def test_query_strings_are_stripped(self):
        rec = lane.safe_record({"storage_url": "https://cdn.example/x.mp4?sig=abc&oh=1"})
        self.assertNotIn("storage_url", rec)  # value carried a signature marker -> dropped

    def test_plain_urls_keep_only_origin_and_path(self):
        rec = lane.safe_record({"canonical_url": REEL + "?utm_source=x"})
        self.assertEqual(rec["canonical_url"], REEL)

    def test_authorized_job_record_is_persistable_and_clean(self):
        a = lane.plan_authorized_job(pending_job=pending(), actor="bryan")
        rec = lane.safe_record(a)
        # The schema columns must survive (authorization_type records which human
        # gate authorized the job); credential CARRIERS must not.
        self.assertEqual(rec["authorization_type"], "acquire_for_reaction")
        for key in rec:
            self.assertNotIn(key.lower(), {"authorization", "token", "cookie",
                                           "secret", "api_key", "apikey", "bearer"})
        blob = str(rec).lower()
        for marker in ("sig=", "oh=", "bearer ", "x-amz-"):
            self.assertNotIn(marker, blob)


if __name__ == "__main__":
    unittest.main()
