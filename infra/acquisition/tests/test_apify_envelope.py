"""Regression: Apify v2 response envelope (the KeyError that failed attempt 2).

Run:  python3 -m unittest discover -s infra/acquisition/tests -v

Mocked HTTP only — the real ``HttpTransport`` is exercised with a patched
``urlopen``, so the envelope handling is genuinely tested without a network call.

Apify v2 wraps single resources as ``{"data": {...}}`` while list endpoints return
a bare array. Reading ``run["defaultDatasetId"]`` at the top level raised
``KeyError`` and sank the attempt *after* a real run had been created.
"""

import io
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

_REPO_ROOT = Path(__file__).resolve().parents[3]
for d in ("acquisition", "discord-review"):
    sys.path.insert(0, str(_REPO_ROOT / "infra" / d))

import apify_client  # noqa: E402
import approved_lane as lane  # noqa: E402
import live_runner as lr  # noqa: E402
import orchestrate  # noqa: E402
import provider  # noqa: E402

CID = "DdQE_RUS0GJ"
REEL = f"https://www.instagram.com/reel/{CID}/"
NUMERIC = provider.actor_api_id(provider.ACTOR_REEL_SCRAPER)
RUN = {"id": "apify-run-abc", "defaultDatasetId": "ds-abc", "status": "SUCCEEDED",
       "actId": NUMERIC, "usageTotalUsd": 0.0026}
ITEM = {"shortCode": CID,
        "videoUrl": "https://scontent.cdninstagram.com/v/t51.2885-15/reel.mp4?sig=***&oh=xyz"}


class _Resp(io.BytesIO):
    """Minimal urlopen() stand-in supporting the context-manager protocol."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    @property
    def status(self):
        return 200

    @property
    def headers(self):
        return {}


def _urlopen_returning(payload):
    body = json.dumps(payload).encode()

    def opener(_req, timeout=None):
        return _Resp(body)
    return opener


class TestEnvelopeUnwrap(unittest.TestCase):
    def test_start_run_unwraps_the_data_envelope(self):
        t = apify_client.HttpTransport("t")
        with mock.patch("urllib.request.urlopen", _urlopen_returning({"data": RUN})):
            run = t.start_run(NUMERIC, {"username": [REEL]})
        self.assertEqual(run["defaultDatasetId"], "ds-abc")     # top level, no KeyError
        self.assertEqual(run["id"], "apify-run-abc")

    def test_run_status_unwraps(self):
        t = apify_client.HttpTransport("t")
        with mock.patch("urllib.request.urlopen",
                        _urlopen_returning({"data": {"id": "r1", "status": "SUCCEEDED"}})):
            st = t.get_run_status(NUMERIC, "r1")
        self.assertEqual(st["status"], "SUCCEEDED")

    def test_dataset_items_accept_bare_and_enveloped_lists(self):
        t = apify_client.HttpTransport("t")
        with mock.patch("urllib.request.urlopen", _urlopen_returning([ITEM])):
            self.assertEqual(t.get_dataset_items("ds-abc"), [ITEM])
        with mock.patch("urllib.request.urlopen", _urlopen_returning({"data": [ITEM]})):
            self.assertEqual(t.get_dataset_items("ds-abc"), [ITEM])

    def test_dispatch_survives_the_enveloped_shape(self):
        """The exact live failure: no KeyError, and the single item is acquired."""
        _gate1, job = self._authorized_job()
        t = apify_client.HttpTransport("t")
        calls = {"n": 0}

        def opener(req, timeout=None):
            calls["n"] += 1
            if calls["n"] == 1:
                return _Resp(json.dumps({"data": RUN}).encode())      # start run
            return _Resp(json.dumps([ITEM]).encode())                 # dataset items

        with mock.patch("urllib.request.urlopen", opener):
            out = orchestrate.dispatch(
                job=job, canonical_url=REEL, content_id=CID, platform="instagram",
                actor=provider.ACTOR_REEL_SCRAPER, lifetime_spent_micro=2600,
                acquisitions_succeeded=1, already_dispatched=False, transport=t,
                downloader=lambda url: {"path": "/tmp/x.mp4", "size_bytes": 10,
                                        "duration_seconds": 30},
                finalizer=lambda meta, prov: {"asset_filename": "x.mp4",
                                              "asset_hash": "f" * 64},
                estimate_micro=2600, is_exact=True)
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["status"], "acquired")

    @staticmethod
    def _authorized_job():
        gate1 = lane.plan_pending_job(fingerprint=CID, platform="instagram",
                                      platform_content_id=CID, canonical_url=REEL,
                                      actor="bryan")
        gate1["dedup_key"] = "a3935fe97af53220986aebcaa641cee3d9406d1501b39e3664bba8363f2212b4"
        job = lane.plan_authorized_job(pending_job=gate1, actor="bryan")
        job["id"] = "b1bb78e6-3601-47d9-9068-46afb45587e1"
        return gate1, job


class TestRunIdCapture(unittest.TestCase):
    def test_captures_the_run_id_from_an_enveloped_response(self):
        class T:
            def start_run(self, actor, input_):
                return {"data": RUN}

            def get_dataset_items(self, dataset_id):  # pragma: no cover
                return []

        sink: dict[str, str] = {}
        lr._RunIdCapture(T(), sink).start_run(provider.ACTOR_REEL_SCRAPER, {})
        self.assertEqual(sink["run_id"], "apify-run-abc")
        self.assertEqual(sink["dataset_id"], "ds-abc")

    def test_captures_from_a_bare_response_too(self):
        class T:
            def start_run(self, actor, input_):
                return RUN

            def get_dataset_items(self, dataset_id):  # pragma: no cover
                return []

        sink: dict[str, str] = {}
        lr._RunIdCapture(T(), sink).start_run(provider.ACTOR_REEL_SCRAPER, {})
        self.assertEqual(sink["run_id"], "apify-run-abc")

    def test_no_run_id_means_the_actor_start_stage(self):
        """A start that returns nothing usable still leaves an honest stage label."""
        class T:
            def start_run(self, actor, input_):
                return {"unexpected": True}

            def get_dataset_items(self, dataset_id):  # pragma: no cover
                return []

        sink: dict[str, str] = {}
        lr._RunIdCapture(T(), sink).start_run(provider.ACTOR_REEL_SCRAPER, {})
        self.assertEqual(sink["run_id"], "")


if __name__ == "__main__":
    unittest.main()
