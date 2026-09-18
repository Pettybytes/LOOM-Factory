"""Deterministic tests for the bounded approved-job runner.

Run:  python3 -m unittest discover -s infra/acquisition/tests -v

The provider path is exercised end-to-end through the REAL orchestrator with a
mock transport / downloader / finalizer. No network, no Apify call, no spend, no
download. Proves: gate enforcement, one exact URL, one item, clamped bounds, one
attempt, no fallback, and no secret persistence.
"""

import sys
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
for d in ("acquisition", "discord-review"):
    sys.path.insert(0, str(_REPO_ROOT / "infra" / d))

import approved_lane as lane  # noqa: E402
import cost  # noqa: E402
import approved_runner as runner  # noqa: E402
import orchestrate  # noqa: E402
import provider  # noqa: E402

FP = "a" * 64
REEL = "https://www.instagram.com/reel/DdZS1QRhrXE/"
CID = "DdZS1QRhrXE"
SIGNED = ("https://scontent.cdninstagram.com/v/t51.2885-15/reel.mp4"
          "?sig=***&_nc_ht=scontent.cdninstagram.com&oh=XYZ")


def authorized_job(**over):
    job = lane.plan_authorized_job(pending_job=lane.plan_pending_job(
        fingerprint=FP, platform="instagram", platform_content_id=CID,
        canonical_url=REEL, actor="bryan"), actor="bryan")
    job.update(over)
    return job


class _Transport:
    def __init__(self, items=None, error=None):
        self.items = items if items is not None else []
        self.error = error
        self.calls = []

    def start_run(self, actor, input_):
        self.calls.append(("run", actor, input_))
        if self.error:
            raise self.error
        return {"defaultDatasetId": "ds1", "id": "run1"}

    def get_dataset_items(self, dataset_id):
        self.calls.append(("items", dataset_id))
        return self.items


def _ok_items():
    return [{"shortCode": CID, "videoUrl": SIGNED}]


def _download(_url):
    return {"path": "/tmp/x.mp4", "size_bytes": 23721338, "duration_seconds": 55.26}


def _finalize(_meta, _prov):
    return {"asset_filename": "instagram__DdZS1QRhrXE__9b22f887b2ff.mp4",
            "asset_hash": "9b22f887b2ff6a3b78d91529c1d9c3105497af2a4298ba9241394686239ae096",
            "size_bytes": 23721338,
            "storage_url": SIGNED}  # signed URL must NOT survive into the record


def run(job, *, transport=None, downloader=_download, finalizer=_finalize,
        already_dispatched=False, retry_authorized=False, **over):
    return runner.run_approved_job(
        job=job,
        lifetime_spent_micro=2600,
        acquisitions_succeeded=1,
        already_dispatched=already_dispatched,
        retry_authorized=retry_authorized,
        transport=transport or _Transport(_ok_items()),
        downloader=downloader,
        finalizer=finalizer,
        dispatch=orchestrate.dispatch,
        **over,
    )


class TestGateEnforcement(unittest.TestCase):
    def test_pending_job_cannot_run(self):
        job = lane.plan_pending_job(fingerprint=FP, platform="instagram",
                                    platform_content_id=CID, canonical_url=REEL,
                                    actor="bryan")
        with self.assertRaises(lane.LaneError) as e:
            run(job)
        self.assertEqual(e.exception.code, "NOT_SPEND_AUTHORIZED")

    def test_missing_job_cannot_run(self):
        with self.assertRaises(runner.RunnerError) as e:
            run(None)
        self.assertEqual(e.exception.code, "NO_JOB")

    def test_finished_job_is_refused(self):
        for status in ("acquired", "failed"):
            with self.assertRaises(runner.RunnerError) as e:
                run(authorized_job(status=status))
            self.assertEqual(e.exception.code, "ALREADY_FINISHED")

    def test_one_attempt_only(self):
        with self.assertRaises(runner.RunnerError) as e:
            run(authorized_job(retry_count=1))
        self.assertEqual(e.exception.code, "ATTEMPTS_EXHAUSTED")

    def test_explicit_retry_is_allowed_once(self):
        out = run(authorized_job(retry_count=1), retry_authorized=True)
        self.assertTrue(out["ok"])
        self.assertEqual(out["attempts"], 2)

    def test_already_dispatched_is_refused(self):
        with self.assertRaises(runner.RunnerError) as e:
            run(authorized_job(), already_dispatched=True)
        self.assertEqual(e.exception.code, "ALREADY_DISPATCHED")

    def test_inactive_fallback_actor_is_never_used(self):
        # The provider actor is PINNED per platform; a job's ``actor`` field is
        # the authorizing human and never reaches the actor allowlist.
        transport = _Transport(_ok_items())
        run(authorized_job(actor="bryan"), transport=transport)
        self.assertEqual(transport.calls[0][1], provider.ACTOR_REEL_SCRAPER)

    def test_platform_without_a_pinned_actor_is_refused(self):
        with self.assertRaises(lane.LaneError) as e:
            lane.default_provider_actor("tiktok")
        self.assertEqual(e.exception.code, "NO_PROVIDER_ACTOR")

    def test_unproven_platform_is_refused(self):
        job = authorized_job(platform="tiktok")
        with self.assertRaises(lane.LaneError):
            run(job)


class TestBoundedRun(unittest.TestCase):
    def test_happy_path_acquisition(self):
        out = run(authorized_job())
        self.assertTrue(out["ok"])
        self.assertEqual(out["status"], "acquired")
        self.assertEqual(out["attempts"], 1)
        self.assertFalse(out["auto_retry"])
        self.assertFalse(out["fallback_used"])
        self.assertEqual(out["max_results"], 1)
        self.assertEqual(out["job_patch"]["status"], "acquired")
        self.assertEqual(out["library"]["asset_filename"],
                         "instagram__DdZS1QRhrXE__9b22f887b2ff.mp4")
        self.assertEqual(out["cost"]["actual_micro"], cost.REEL_SCRAPER_RESULT_MICRO)

    def test_signed_url_never_reaches_the_record(self):
        out = run(authorized_job())
        blob = str(out).lower()
        # The REDACTED source host is intentional (provider contract: origin +
        # path only). What must never survive is the signature or a credential.
        for marker in ("sig=", "oh=", "token=", "bearer", "authorization"):
            self.assertNotIn(marker, blob)

    def test_oversized_job_is_clamped_not_widened(self):
        out = run(authorized_job(max_bytes=5 * 1024 ** 3, max_duration_seconds=99999))
        self.assertTrue(out["bounds_clamped"])
        self.assertEqual(out["effective_max_bytes"], lane.PILOT_MAX_BYTES)
        self.assertEqual(out["effective_max_duration_seconds"], lane.PILOT_MAX_DURATION_SECONDS)

    def test_missing_bounds_are_set(self):
        job = authorized_job()
        job.pop("max_bytes", None)
        job.pop("max_duration_seconds", None)
        out = run(job)
        self.assertEqual(out["effective_max_bytes"], lane.PILOT_MAX_BYTES)

    def test_query_string_is_removed_from_the_url_before_dispatch(self):
        job = authorized_job()
        job["canonical_url"] = REEL + "?utm_source=ig_web_copy_link"
        transport = _Transport(_ok_items())
        out = run(job, transport=transport)
        self.assertTrue(out["ok"])
        sent_url = transport.calls[0][2]["username"][0] if transport.calls else ""
        self.assertNotIn("utm_source", str(sent_url))

    def test_single_item_input(self):
        transport = _Transport(_ok_items())
        run(authorized_job(), transport=transport)
        inp = transport.calls[0][2]
        self.assertEqual(inp.get("resultsLimit"), 1)
        self.assertEqual(len(inp.get("username", [])), 1)
        self.assertEqual(inp["username"][0], REEL)
        self.assertNotIn("includeDownloadedVideo", inp)   # no stored-file premium event


class TestFailureBehaviour(unittest.TestCase):
    def test_zero_results_fails_without_fallback(self):
        transport = _Transport(items=[])
        out = run(authorized_job(), transport=transport)
        self.assertFalse(out["ok"])
        self.assertEqual(out["status"], "failed")
        self.assertEqual(out["error"]["code"], "EMPTY_RESULT")
        self.assertEqual(out["job_patch"]["status"], "failed")
        self.assertEqual(out["job_patch"]["retry_count"], 1)
        runs = [c for c in transport.calls if c[0] == "run"]
        self.assertEqual(len(runs), 1)          # exactly one attempt, no fallback

    def test_multiple_results_fail(self):
        out = run(authorized_job(), transport=_Transport(
            items=[{"shortCode": CID, "videoUrl": SIGNED},
                   {"shortCode": CID, "videoUrl": SIGNED}]))
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"]["code"], "MULTIPLE_RESULTS")

    def test_content_id_mismatch_fails(self):
        out = run(authorized_job(), transport=_Transport(
            items=[{"shortCode": "SOMETHING_ELSE", "videoUrl": SIGNED}]))
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"]["code"], "CONTENT_MISMATCH")

    def test_download_failure_is_classified(self):
        def boom(_url):
            raise RuntimeError("network down")
        out = run(authorized_job(), downloader=boom)
        self.assertFalse(out["ok"])
        self.assertEqual(out["status"], "failed")

    def test_no_secrets_in_failure_record(self):
        out = run(authorized_job(), transport=_Transport(items=[]))
        self.assertNotIn("sig=", str(out).lower())


class TestBoundsDescription(unittest.TestCase):
    def test_description_is_honest_and_short(self):
        text = runner.describe_bounds(authorized_job())
        self.assertIn("1 URL", text)
        self.assertIn("1 item", text)
        self.assertIn("100 MiB", text)
        self.assertIn("5 min", text)
        self.assertIn("no fallback", text)

    def test_description_flags_clamping(self):
        self.assertIn("clamped", runner.describe_bounds(
            authorized_job(max_bytes=9 * 1024 ** 3)))


if __name__ == "__main__":
    unittest.main()
