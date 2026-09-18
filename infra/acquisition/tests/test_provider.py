"""Deterministic tests for the paid-provider acquisition boundary (Phase 2).

Run:  python3 -m unittest discover -s infra/acquisition/tests -v
"""

import sys
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
for d in ("acquisition", "discord-review"):
    sys.path.insert(0, str(_REPO_ROOT / "infra" / d))

import apify_client  # noqa: E402
import cost  # noqa: E402
import orchestrate  # noqa: E402
import provider  # noqa: E402
from manual_intake import SubmissionError, validate_and_canonicalize  # noqa: E402

REEL = "https://www.instagram.com/reel/DdZS1QRhrXE/"
CID = "DdZS1QRhrXE"
SIGNED = ("https://scontent.cdninstagram.com/v/t51.2885-15/reel.mp4"
          "?sig=SECRET&_nc_ht=scontent.cdninstagram.com&oh=XYZ")


def _job(**over):
    j = {"canonical_url": REEL, "status": "queued", "dedup_key": "k"}
    j.update(over)
    return j


class _MockTransport:
    def __init__(self, items=None, error=None):
        self.items = items or []
        self.error = error
        self.calls = []

    def start_run(self, actor, input_):
        self.calls.append(("run", actor, input_))
        if self.error:
            raise self.error
        return {"defaultDatasetId": "ds1", "id": "run1"}

    def get_dataset_items(self, dataset_id):
        self.calls.append(("items", dataset_id))
        if self.error:
            raise self.error
        return self.items


class _MockDownloader:
    def __init__(self, meta=None, error=None):
        self.meta = meta or {"duration_seconds": 10.0, "has_audio": True, "sha256": "a" * 64}
        self.error = error
        self.url = None

    def __call__(self, url):
        self.url = url
        if self.error:
            raise self.error
        return self.meta


def _finalizer(meta, provenance):
    return {"filename": "instagram__DdZS1QRhrXE__abc.mp4", "provenance": provenance}


def _dispatch(**over):
    kw = dict(
        job=_job(), canonical_url=REEL, content_id=CID, platform="instagram",
        actor=provider.ACTOR_REEL_SCRAPER, lifetime_spent_micro=0,
        acquisitions_succeeded=0, already_dispatched=False,
        transport=_MockTransport(items=[{"shortCode": CID, "videoUrl": SIGNED,
                                         "videoDuration": 30.0}]),
        downloader=_MockDownloader(), finalizer=_finalizer,
        estimate_micro=cost.REEL_SCRAPER_RESULT_MICRO, is_exact=True,
    )
    kw.update(over)
    return orchestrate.dispatch(**kw)


class TestToken(unittest.TestCase):
    def test_missing_token_without_disclosure(self):
        with self.assertRaises(apify_client.ApifyError) as cm:
            apify_client.load_token({})
        self.assertEqual(cm.exception.code, "TOKEN_MISSING")
        self.assertEqual(cm.exception.message, "LOOM_APIFY_API_TOKEN is not configured")
        self.assertNotIn("sk-", str(cm.exception))
        self.assertNotIn("=", cm.exception.message)

    def test_token_present_bool(self):
        self.assertFalse(apify_client.token_present({}))
        self.assertTrue(apify_client.token_present({apify_client.TOKEN_ENV: "x"}))


class TestAuthorization(unittest.TestCase):
    def test_unauthorized_rejection(self):
        r = _dispatch(job=None)
        self.assertFalse(r["ok"])
        self.assertEqual(r["error"], "UNAUTHORIZED")

    def test_url_mismatch(self):
        r = _dispatch(job=_job(canonical_url="https://www.instagram.com/reel/OTHER/"))
        self.assertFalse(r["ok"])
        self.assertEqual(r["error"], "URL_MISMATCH")

    def test_wrong_status_not_authorized(self):
        r = _dispatch(job=_job(status="acquired"))
        self.assertFalse(r["ok"])
        self.assertEqual(r["error"], "NOT_AUTHORIZED")


class TestUrl(unittest.TestCase):
    def test_malformed_url_rejected(self):
        with self.assertRaises(SubmissionError):
            validate_and_canonicalize("not a url")

    def test_off_platform_rejected(self):
        with self.assertRaises(provider.ProviderError) as cm:
            provider.allowed_actor("youtube", provider.ACTOR_REEL_SCRAPER)
        self.assertEqual(cm.exception.code, "UNKNOWN_ACTOR")


class TestInput(unittest.TestCase):
    def test_one_url_max_results_one(self):
        inp = provider.build_input(REEL)
        self.assertEqual(inp, {"username": [REEL], "resultsLimit": 1})

    def test_expansion_fields_impossible(self):
        inp = provider.build_input(REEL)
        self.assertEqual(set(inp.keys()), {"username", "resultsLimit"})
        self.assertNotIn("profiles", inp)
        self.assertNotIn("hashtags", inp)
        self.assertNotIn("search", inp)
        self.assertNotIn("includeDownloadedVideo", inp)  # no premium stored-file event


class TestActorAllowlist(unittest.TestCase):
    def test_pinned_identity(self):
        e = provider.allowed_actor("instagram", provider.ACTOR_REEL_SCRAPER)
        self.assertEqual(e["actor"], provider.ACTOR_REEL_SCRAPER)
        self.assertTrue(e["active"])

    def test_unknown_actor_rejected(self):
        with self.assertRaises(provider.ProviderError) as cm:
            provider.allowed_actor("instagram", "apify/evil-actor")
        self.assertEqual(cm.exception.code, "UNKNOWN_ACTOR")

    def test_fallback_inactive(self):
        # the fallback actor must never be auto-called
        with self.assertRaises(provider.ProviderError) as cm:
            provider.actor_active("instagram", provider.ACTOR_INSTAGRAM_SCRAPER)
        self.assertEqual(cm.exception.code, "INACTIVE_ACTOR")

    def test_actor_api_id_is_numeric(self):
        # the REST API needs the numeric id, not the website slug (slash form)
        self.assertEqual(provider.actor_api_id(provider.ACTOR_REEL_SCRAPER), "xMc5Ga1oCONPmWJIa")
        self.assertEqual(provider.actor_api_id(provider.ACTOR_INSTAGRAM_SCRAPER), "shu8hvrXbJbY3Eb9W")
        self.assertNotIn("/", provider.actor_api_id(provider.ACTOR_REEL_SCRAPER))


class TestOutput(unittest.TestCase):
    def _result(self, **over):
        r = {"shortCode": CID, "videoUrl": SIGNED, "videoDuration": 30.0}
        r.update(over)
        return r

    def test_zero_results(self):
        with self.assertRaises(provider.ProviderError) as cm:
            provider.validate_output([], content_id=CID)
        self.assertEqual(cm.exception.code, "EMPTY_RESULT")

    def test_multiple_results(self):
        with self.assertRaises(provider.ProviderError) as cm:
            provider.validate_output([self._result(), self._result()], content_id=CID)
        self.assertEqual(cm.exception.code, "MULTIPLE_RESULTS")

    def test_content_mismatch(self):
        with self.assertRaises(provider.ProviderError) as cm:
            provider.validate_output([self._result(shortCode="OTHER123")], content_id=CID)
        self.assertEqual(cm.exception.code, "CONTENT_MISMATCH")

    def test_missing_video_url(self):
        with self.assertRaises(provider.ProviderError) as cm:
            provider.validate_output([self._result(videoUrl="")], content_id=CID)
        self.assertEqual(cm.exception.code, "MISSING_VIDEO_URL")

    def test_malformed_video_url(self):
        with self.assertRaises(provider.ProviderError) as cm:
            provider.validate_output([self._result(videoUrl="http://evil.com/x.mp4")], content_id=CID)
        self.assertEqual(cm.exception.code, "BAD_VIDEO_URL")

    def test_credential_bearing_video_url(self):
        with self.assertRaises(provider.ProviderError) as cm:
            provider.validate_output([self._result(videoUrl="https://user:pass@scontent.cdninstagram.com/x.mp4")], content_id=CID)
        self.assertEqual(cm.exception.code, "CREDENTIALS")

    def test_duration_ceiling(self):
        with self.assertRaises(provider.ProviderError) as cm:
            provider.validate_output([self._result(videoDuration=301)], content_id=CID)
        self.assertEqual(cm.exception.code, "DURATION_LIMIT")


class TestSignedUrlNonPersistence(unittest.TestCase):
    def test_provenance_excludes_signed_query(self):
        prov = provider.build_provenance(provider.ACTOR_REEL_SCRAPER, SIGNED)
        self.assertNotIn("sig=SECRET", str(prov))
        self.assertNotIn("oh=XYZ", str(prov))
        self.assertIn("source_host", prov)


class TestCost(unittest.TestCase):
    def test_reservation_exact(self):
        self.assertEqual(cost.reserve_worst_case(2600, True, 0), 2600)

    def test_reservation_worst_case_when_unknown(self):
        # unknown estimate -> reserve full remaining ceiling
        self.assertEqual(cost.reserve_worst_case(0, False, 0), 1_000_000)

    def test_reconcile_bounded(self):
        self.assertEqual(cost.reconcile_actual(2600, 2600), 2600)
        self.assertEqual(cost.reconcile_actual(2600, 999999), 2600)  # capped at reserve

    def test_no_budget(self):
        with self.assertRaises(cost.CostError) as cm:
            cost.reserve_worst_case(2600, True, 1_000_000)
        self.assertEqual(cm.exception.code, "NO_BUDGET")


class TestCircuitBreaker(unittest.TestCase):
    def test_dollar_ceiling(self):
        self.assertEqual(cost.check_circuit(1_000_000, 0), "CEILING_REACHED")

    def test_ten_success_ceiling(self):
        self.assertEqual(cost.check_circuit(0, 10), "MAX_ACQUISITIONS_REACHED")

    def test_under_limits_ok(self):
        self.assertEqual(cost.check_circuit(0, 0), "ok")


class TestDispatch(unittest.TestCase):
    def test_idempotent_dispatch(self):
        r = _dispatch(already_dispatched=True)
        self.assertFalse(r["ok"])
        self.assertEqual(r["error"], "ALREADY_DISPATCHED")

    def test_circuit_breaker_blocks(self):
        r = _dispatch(lifetime_spent_micro=1_000_000)
        self.assertFalse(r["ok"])
        self.assertEqual(r["error"], "CEILING_REACHED")

    def test_no_automatic_fallback(self):
        r = _dispatch(actor=provider.ACTOR_INSTAGRAM_SCRAPER)
        self.assertFalse(r["ok"])
        self.assertEqual(r["error"], "INACTIVE_ACTOR")

    def test_provider_timeout_failure(self):
        r = _dispatch(transport=_MockTransport(error=apify_client.ApifyError("TIMEOUT", "x")))
        self.assertFalse(r["ok"])
        self.assertEqual(r["error"], "TIMEOUT")

    def test_local_media_validation_failure(self):
        class Err(RuntimeError):
            code = "FFPROBE_FAILED"
        r = _dispatch(downloader=_MockDownloader(error=Err("bad")))
        self.assertFalse(r["ok"])
        self.assertEqual(r["error"], "FFPROBE_FAILED")

    def test_successful_finalization_through_mocks(self):
        dl = _MockDownloader()
        r = _dispatch(downloader=dl)
        self.assertTrue(r["ok"])
        self.assertEqual(r["status"], "acquired")
        self.assertIn("filename", r["library"])
        self.assertEqual(dl.url, SIGNED)
        self.assertNotIn("sig=SECRET", str(r["provenance"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
