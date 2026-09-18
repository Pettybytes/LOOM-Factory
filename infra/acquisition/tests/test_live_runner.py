"""Deterministic tests for the LIVE Gate 2 runner (fail-closed matrix).

Run:  python3 -m unittest discover -s infra/acquisition/tests -v

Everything runs with fakes: no network, no Apify call, no download, no spend.
The provider path itself is the REAL ``orchestrate.dispatch`` with a mock
transport, so the wiring is genuinely exercised.
"""

import json
import sys
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
for d in ("acquisition", "discord-review", "local-helper"):
    sys.path.insert(0, str(_REPO_ROOT / "infra" / d))

import approved_lane as lane  # noqa: E402
import cost  # noqa: E402
import live_runner as lr  # noqa: E402
import orchestrate  # noqa: E402
import provider  # noqa: E402

CID = "DdZS1QRhrXE"
REEL = "https://www.instagram.com/reel/DdZS1QRhrXE/"
FP = CID
SIGNED = ("https://scontent.cdninstagram.com/v/t51.2885-15/reel.mp4"
          "?sig=***&oh=XYZ")

TOKEN_ENV = {"LOOM_APIFY_API_TOKEN": "***"}


def pending_job(**over):
    job = lane.plan_pending_job(fingerprint=FP, platform="instagram",
                                platform_content_id=CID, canonical_url=REEL,
                                actor="bryan")
    job.update(over)
    return job


def auth_job(*, pending=None, **over):
    p = pending or pending_job()
    job = lane.plan_authorized_job(pending_job=p, actor="bryan")
    job["id"] = "job-1"
    job.update(over)
    return job


class FakeTransport:
    def __init__(self, items=None):
        self.items = items if items is not None else [{"shortCode": CID, "videoUrl": SIGNED}]
        self.calls = []

    def start_run(self, actor, input_):
        self.calls.append(("run", actor, input_))
        return {"defaultDatasetId": "ds1", "id": "run1"}

    def get_dataset_items(self, dataset_id):
        self.calls.append(("items", dataset_id))
        return self.items


class RecordingLedger:
    """Fake ledger that records the call order (reserve -> provider -> reconcile)."""

    def __init__(self, log=None):
        self.log = log if log is not None else []
        self.reserve_calls = []
        self.run_rows = []
        self.reconciled = []
        self.fail_reserve = None

    def reserve(self, *, amount_micro, job_id=""):
        if self.fail_reserve:
            raise lr.LedgerErrorStub(self.fail_reserve, "refused") \
                if hasattr(lr, "LedgerErrorStub") else RuntimeError(self.fail_reserve)
        self.log.append("reserve")
        self.reserve_calls.append({"amount_micro": amount_micro, "job_id": job_id})
        return {"reserved_micro": amount_micro}

    def record_run(self, **kw):
        self.log.append("record_run")
        self.run_rows.append(kw)
        return {"created": True}

    def reconcile(self, **kw):
        self.log.append("reconcile")
        self.reconciled.append(kw)
        return {"applied": True, "idempotent": False, "status": "final"}


class FakeIO(lr.LiveIO):
    def __init__(self, **over):
        self.downloads = []
        self.cleaned = []
        self.persisted = []
        self.finalized = []
        self.order = []
        self.transport = _OrderedTransport(self.order)
        self.ledger = RecordingLedger(self.order)
        super().__init__(
            transport=self.transport,
            download_one_video=self._download,
            run_ffprobe=lambda path: {"streams": [], "format": {}},
            parse_ffprobe_result=self._parse,
            checksum_sha256=lambda path: "9b22f887b2ff6a3b78d91529c1d9c3105497af2a4298ba9241394686239ae096",
            finalize=self._finalize,
            cleanup_temp=self._cleanup,
            persist_library=self._persist,
            library_rows=lambda: [],
            pending_job=lambda fp: pending_job(),
            ceiling=lambda: {"ceiling_micro": 1_000_000, "lifetime_spent_micro": 2600,
                             "max_acquisitions": 10, "acquisitions_succeeded": 1,
                             "disabled": False},
            existing_run=lambda job_id: False,
            ledger=RecordingLedger(self.order),
            tmp_dir="/tmp",
            library_dir="/tmp/lib",
        )
        for k, v in over.items():
            setattr(self, k, v)

    # --- fakes -------------------------------------------------------------
    def _download(self, url, tmp_dir, *, max_bytes, timeout):
        self.downloads.append({"url": url, "max_bytes": max_bytes, "timeout": timeout})
        return ("/tmp/lib/part.mp4", 23721338, 200, "video/mp4")

    def _parse(self, ffprobe_json, *, actual_size_bytes, max_bytes, max_duration):
        return {"duration_seconds": 55.26, "width": 720, "height": 1280,
                "frame_rate": 30.0, "has_audio": True,
                "video_codec": "h264", "container": "mov,mp4"}

    def _finalize(self, tmp, final):
        self.finalized.append((tmp, final))
        return final

    def _cleanup(self, path):
        self.cleaned.append(path)

    def _persist(self, record):
        self.persisted.append(record)
        return {"id": "lib-1", **record}


def run_live(job, *, io=None, store=None, environ=None, retry_authorized=False):
    io = io or FakeIO()
    env = TOKEN_ENV if environ is None else environ
    runner = lr.build_live_runner(io=io, store=store, environ=env,
                                  dispatch=orchestrate.dispatch)
    return runner(job=job, retry_authorized=retry_authorized), io


class _OrderedTransport(FakeTransport):
    """Fake transport that records when the provider call actually happens."""

    def __init__(self, log):
        super().__init__()
        self._log = log

    def start_run(self, actor, input_):
        self._log.append("provider")
        return super().start_run(actor, input_)


class TestLedgerIntegration(unittest.TestCase):
    def test_reservation_happens_before_the_provider_call(self):
        io = FakeIO()
        out, io = run_live(auth_job(), io=io)
        self.assertTrue(out["ok"])
        self.assertIn("reserve", io.order)
        self.assertIn("provider", io.order)
        self.assertLess(io.order.index("reserve"), io.order.index("provider"))
        self.assertEqual(io.ledger.reserve_calls[0]["amount_micro"],
                         cost.REEL_SCRAPER_RESULT_MICRO)

    def test_success_reconciles_with_the_actual_charge(self):
        out, io = run_live(auth_job())
        rec = io.ledger.reconciled[-1]
        self.assertEqual(rec["actual_micro"], cost.REEL_SCRAPER_RESULT_MICRO)
        self.assertTrue(rec["succeeded"])
        self.assertEqual(len(io.ledger.run_rows), 1)

    def test_failure_reconciles_as_unknown_and_keeps_the_reservation(self):
        io = FakeIO()
        io.transport = _OrderedTransport(io.order)
        io.transport.items = []
        out, io = run_live(auth_job(), io=io)
        self.assertFalse(out["ok"])
        rec = io.ledger.reconciled[-1]
        self.assertIsNone(rec["actual_micro"])      # never assumed to be $0
        self.assertFalse(rec["succeeded"])
        self.assertEqual(len(io.ledger.run_rows), 1)  # the attempt is auditable

    def test_missing_ledger_refuses_without_spending(self):
        io = FakeIO()
        io.ledger = None
        out, io = run_live(auth_job(), io=io)
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"]["code"], "LEDGER_UNAVAILABLE")
        self.assertEqual(io.transport.calls, [])
        self.assertEqual(io.downloads, [])

    def test_breaker_refusal_prevents_the_provider_call(self):
        io = FakeIO()
        io.ledger = RecordingLedger(io.order)

        def refuse(*, amount_micro, job_id=""):
            raise RuntimeError("CEILING_REACHED")

        io.ledger.reserve = refuse
        out, io = run_live(auth_job(), io=io)
        self.assertFalse(out["ok"])
        self.assertEqual(io.transport.calls, [])


class TestFailClosed(unittest.TestCase):
    """Every listed fail-closed condition returns a classified refusal and
    performs NO provider call."""

    def _refuse(self, job, *, io=None, environ=None, code=None):
        io = io or FakeIO()
        out, _io = run_live(job, io=io, environ=environ)
        self.assertFalse(out["ok"])
        self.assertEqual(io.transport.calls, [])          # nothing dispatched
        self.assertEqual(io.downloads, [])                # nothing downloaded
        if code:
            self.assertEqual(out["error"]["code"], code)
        return out

    def test_missing_job(self):
        self._refuse(None, code="NO_JOB")

    def test_gate1_only_job_is_refused(self):
        self._refuse(pending_job(), code="NOT_SPEND_AUTHORIZED")

    def test_gate1_absent_is_refused(self):
        io = FakeIO()
        io.pending_job = lambda fp: None
        self._refuse(auth_job(), io=io, code="NO_PENDING_JOB")

    def test_gate1_mismatch_is_refused(self):
        io = FakeIO()
        p = pending_job()
        p["dedup_key"] = "different"
        io.pending_job = lambda fp: p
        self._refuse(auth_job(), io=io, code="GATE1_MISMATCH")

    def test_changed_url_is_refused(self):
        io = FakeIO()
        p = pending_job()
        p["canonical_url"] = "https://www.instagram.com/reel/SOMETHINGELSE/"
        io.pending_job = lambda fp: p
        self._refuse(auth_job(), io=io, code="URL_CHANGED")

    def test_changed_content_id_is_refused(self):
        io = FakeIO()
        p = pending_job()
        p["platform_content_id"] = "OTHER"
        io.pending_job = lambda fp: p
        self._refuse(auth_job(), io=io, code="CONTENT_ID_CHANGED")

    def test_already_acquired_is_refused(self):
        io = FakeIO()
        io.library_rows = lambda: [{"platform_content_id": CID, "duplicate_fingerprint": FP}]
        self._refuse(auth_job(), io=io, code="ALREADY_ACQUIRED")

    def test_in_flight_is_refused(self):
        self._refuse(auth_job(status="running"), code="IN_FLIGHT")

    def test_active_attempt_is_refused(self):
        # Legacy injected boolean: "a run exists" now means an ACTIVE attempt, and
        # the refusal is classified by the attempt-scoped lookup.
        io = FakeIO()
        io.existing_run = lambda job_id: True
        self._refuse(auth_job(), io=io, code="ATTEMPT_IN_FLIGHT")

    def test_missing_token_is_refused(self):
        self._refuse(auth_job(), environ={}, code="TOKEN_MISSING")

    def test_ceiling_exceeded_is_refused(self):
        io = FakeIO()
        io.ceiling = lambda: {"ceiling_micro": 1_000_000, "lifetime_spent_micro": 1_000_000,
                              "max_acquisitions": 10, "acquisitions_succeeded": 3}
        self._refuse(auth_job(), io=io, code="CEILING_REACHED")

    def test_max_acquisitions_is_refused(self):
        io = FakeIO()
        io.ceiling = lambda: {"ceiling_micro": 1_000_000, "lifetime_spent_micro": 2600,
                              "max_acquisitions": 1, "acquisitions_succeeded": 1}
        self._refuse(auth_job(), io=io, code="MAX_ACQUISITIONS_REACHED")

    def test_disabled_breaker_is_refused(self):
        io = FakeIO()
        io.ceiling = lambda: {"ceiling_micro": 1_000_000, "lifetime_spent_micro": 0,
                              "max_acquisitions": 10, "acquisitions_succeeded": 0,
                              "disabled": True}
        self._refuse(auth_job(), io=io, code="BREAKER_DISABLED")

    def test_unproven_platform_is_refused(self):
        self._refuse(auth_job(platform="tiktok"), code="NOT_RUNTIME_PROVEN")

    def test_fallback_actor_is_never_used(self):
        # The pinned actor is the only one the runner will select; asking for the
        # inactive fallback cannot smuggle it in because job["actor"] never
        # reaches the allowlist. At the wire boundary the pinned actor is sent as
        # its NUMERIC REST id (the slug path 404s).
        out, io = run_live(auth_job(actor=provider.ACTOR_INSTAGRAM_SCRAPER))
        self.assertTrue(out["ok"])
        sent = io.transport.calls[0][1]
        self.assertEqual(sent, provider.actor_api_id(provider.ACTOR_REEL_SCRAPER))
        self.assertNotEqual(sent, provider.actor_api_id(provider.ACTOR_INSTAGRAM_SCRAPER))


class TestBoundedRun(unittest.TestCase):
    def test_preflight_bundle_is_bounded(self):
        pre = lr.preflight(auth_job(), io=FakeIO(), environ=TOKEN_ENV)
        self.assertEqual(pre["job"]["max_bytes"], lane.PILOT_MAX_BYTES)
        self.assertEqual(pre["job"]["max_duration_seconds"], lane.PILOT_MAX_DURATION_SECONDS)
        self.assertEqual(pre["estimate_micro"], cost.REEL_SCRAPER_RESULT_MICRO)
        self.assertEqual(pre["actor"], provider.ACTOR_REEL_SCRAPER)
        self.assertEqual(pre["canonical_url"], REEL)

    def test_query_string_is_stripped_from_the_dispatched_url(self):
        job = auth_job()
        job["canonical_url"] = REEL + "?utm_source=ig_web_copy_link"
        out, io = run_live(job)
        self.assertTrue(out["ok"])
        sent = io.transport.calls[0][2]
        self.assertNotIn("utm_source", str(sent))
        self.assertEqual(sent["resultsLimit"], 1)

    def test_successful_mocked_acquisition(self):
        out, io = run_live(auth_job())
        self.assertTrue(out["ok"])
        self.assertEqual(out["status"], "acquired")
        self.assertEqual(out["attempts"], 1)
        self.assertFalse(out["auto_retry"])
        self.assertFalse(out["fallback_used"])
        self.assertEqual(len(io.downloads), 1)
        self.assertEqual(io.downloads[0]["max_bytes"], lane.PILOT_MAX_BYTES)
        self.assertEqual(io.finalized[0][1],
                         "/tmp/lib/instagram__DdZS1QRhrXE__9b22f887b2ff.mp4")
        self.assertEqual(len(io.persisted), 1)
        rec = io.persisted[0]
        self.assertEqual(rec["asset_hash"],
                         "9b22f887b2ff6a3b78d91529c1d9c3105497af2a4298ba9241394686239ae096")
        self.assertEqual(rec["destination"], "personal_reaction_library")

    def test_one_attempt_only_and_no_fallback_on_failure(self):
        io = FakeIO()
        io.transport = FakeTransport(items=[])
        out, io = run_live(auth_job(), io=io)
        self.assertFalse(out["ok"])
        self.assertEqual(out["status"], "failed")
        self.assertEqual(out["error"]["code"], "EMPTY_RESULT")
        runs = [c for c in io.transport.calls if c[0] == "run"]
        self.assertEqual(len(runs), 1)
        self.assertEqual(io.persisted, [])

    def test_retry_is_explicit_and_single_shot(self):
        io = FakeIO()
        out, io = run_live(auth_job(retry_count=1), io=io, retry_authorized=True)
        self.assertTrue(out["ok"])
        self.assertEqual(out["attempts"], 2)
        io2 = FakeIO()
        out2, _ = run_live(auth_job(retry_count=2), io=io2, retry_authorized=True)
        self.assertFalse(out2["ok"])
        self.assertEqual(out2["error"]["code"], "ATTEMPTS_EXHAUSTED")
        self.assertEqual(io2.transport.calls, [])


class TestMediaValidation(unittest.TestCase):
    def _with_parse(self, parse):
        io = FakeIO()
        io.parse_ffprobe_result = parse
        return io

    def test_size_limit_failure_is_classified(self):
        def parse(*a, **kw):
            raise lr.LiveRunnerError("SIZE_LIMIT", "too big")
        io = self._with_parse(parse)
        out, io = run_live(auth_job(), io=io)
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"]["code"], "SIZE_LIMIT")
        self.assertEqual(io.cleaned, ["/tmp/lib/part.mp4"])   # temp cleaned on failure
        self.assertEqual(io.finalized, [])

    def test_missing_audio_fails_closed(self):
        io = FakeIO()
        base = io.parse_ffprobe_result

        def parse(*a, **kw):
            meta = base(*a, **kw)
            meta["has_audio"] = False
            return meta
        io.parse_ffprobe_result = parse
        out, io = run_live(auth_job(), io=io)
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"]["code"], "AUDIO_MISSING")
        self.assertEqual(io.persisted, [])

    def test_missing_codec_fails_closed(self):
        io = FakeIO()
        base = io.parse_ffprobe_result

        def parse(*a, **kw):
            meta = base(*a, **kw)
            meta["video_codec"] = None
            return meta
        io.parse_ffprobe_result = parse
        out, io = run_live(auth_job(), io=io)
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"]["code"], "MEDIA_INVALID")

    def test_download_failure_is_cleaned_up(self):
        io = FakeIO()

        def boom(url, tmp_dir, *, max_bytes, timeout):
            io.cleaned.append("part")
            raise lr.LiveRunnerError("BYTE_LIMIT", "ceiling hit")
        io.download_one_video = boom
        out, _io = run_live(auth_job(), io=io)
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"]["code"], "BYTE_LIMIT")


class TestHygiene(unittest.TestCase):
    def test_no_signed_url_or_secret_in_the_outcome_or_library_record(self):
        out, io = run_live(auth_job())
        blob = (json.dumps(out, default=str) + json.dumps(io.persisted, default=str)).lower()
        for marker in ("sig=", "oh=", "bearer", "authorization", "token="):
            self.assertNotIn(marker, blob)

    def test_persisted_record_has_only_schema_fields(self):
        _out, io = run_live(auth_job())
        rec = io.persisted[0]
        allowed = {"duplicate_fingerprint", "platform", "platform_content_id", "canonical_url",
                   "acquisition_job_id", "destination", "asset_filename", "asset_hash",
                   "size_bytes", "duration_seconds", "content_type", "storage_url",
                   "retention_days", "provenance", "rights_usage_note"}
        self.assertTrue(set(rec.keys()) <= allowed, set(rec.keys()) - allowed)
        self.assertEqual(rec["storage_url"], "")

    def test_provenance_carries_no_signed_url(self):
        out, _io = run_live(auth_job())
        prov = str(out.get("provenance", {})).lower()
        self.assertNotIn("sig=", prov)
        self.assertNotIn("oh=", prov)


if __name__ == "__main__":
    unittest.main()
