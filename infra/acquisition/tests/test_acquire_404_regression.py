"""Regression tests for the Acquire 404 incident and its corrections.

Run:  python3 -m unittest discover -s infra/acquisition/tests -v

Mocked HTTP only — no network, no provider, no spend. Each defect that caused the
live failure now has a test that would have caught it:

  1. Actor addressing: the REST call must use the numeric actor id, not the
     website slug (the slug path 404s).
  2. Settlement: a provider failure must be recorded durably (audit row +
     reconciliation) for ANY failure shape, including integer error codes.
  3. Outcome persistence: the job row must receive its terminal outcome BEFORE
     the card is updated, and a retry must stay distinguishable.
"""

import sys
import unittest
import urllib.error
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
for d in ("acquisition", "discord-review"):
    sys.path.insert(0, str(_REPO_ROOT / "infra" / d))
_TESTS_DIR = Path(__file__).resolve().parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

import acquisition_card as card  # noqa: E402
import acquisition_ops as ops  # noqa: E402
import apify_client  # noqa: E402
import approved_lane as lane  # noqa: E402
import cost  # noqa: E402
import ledger as L  # noqa: E402
import live_runner as lr  # noqa: E402
import orchestrate  # noqa: E402
import provider  # noqa: E402
from test_ledger import FakePostgrest  # noqa: E402

CID = "DdQE_RUS0GJ"
REEL = f"https://www.instagram.com/reel/{CID}/"
NUMERIC = provider.actor_api_id(provider.ACTOR_REEL_SCRAPER)


class SlugPathTransport:
    """Mimics the real API: 404 for a slug path, success for the numeric id."""

    def __init__(self):
        self.calls = []

    @staticmethod
    def path_for(actor: str) -> str:
        return f"{apify_client.BASE_URL}/acts/{actor}/runs"

    def start_run(self, actor: str, input_: dict) -> dict:
        self.calls.append((actor, self.path_for(actor)))
        if "/" in actor:
            raise urllib.error.HTTPError(self.path_for(actor), 404, "Not Found", None, None)
        return {"defaultDatasetId": "ds1", "id": "run1"}

    def get_dataset_items(self, dataset_id):
        raise AssertionError("dataset must not be fetched after a failed start")


class ProviderFailureTransport:
    """A post-fix failure the provider can still return (numeric path, HTTP 500)."""

    def __init__(self):
        self.calls = []

    def start_run(self, actor: str, input_: dict) -> dict:
        self.calls.append((actor, f"{apify_client.BASE_URL}/acts/{actor}/runs"))
        raise urllib.error.HTTPError("https://api.apify.com/v2/acts/X/runs", 500,
                                     "Internal Server Error", None, None)

    def get_dataset_items(self, dataset_id):  # pragma: no cover
        raise AssertionError("no dataset after a failed start")


def fake_io(transport, ledger_obj):
    import test_live_runner as base
    io = base.FakeIO()
    io.transport = transport
    io.ledger = ledger_obj
    return io


def authorized_job_from_gate1():
    gate1 = lane.plan_pending_job(fingerprint=CID, platform="instagram",
                                  platform_content_id=CID, canonical_url=REEL,
                                  actor="bryan")
    gate1["dedup_key"] = "a3935fe97af53220986aebcaa641cee3d9406d1501b39e3664bba8363f2212b4"
    job = lane.plan_authorized_job(pending_job=gate1, actor="bryan")
    job["id"] = "b1bb78e6-3601-47d9-9068-46afb45587e1"
    return gate1, job


def build_runner(io, gate1):
    io.pending_job = lambda fp: gate1
    return lr.build_live_runner(io=io, store=None,
                                environ={"LOOM_APIFY_API_TOKEN": "present-for-proof"},
                                dispatch=orchestrate.dispatch)


# ---------------------------------------------------------------------------
# Defect 1 — actor addressing
# ---------------------------------------------------------------------------
class TestActorAddressing(unittest.TestCase):
    def test_the_wrong_path_is_what_the_slug_produces(self):
        """The malformed URL the live attempt used (structure only)."""
        transport = SlugPathTransport()
        path = transport.path_for(provider.ACTOR_REEL_SCRAPER)
        self.assertEqual(path, "https://api.apify.com/v2/acts/apify/instagram-reel-scraper/runs")
        self.assertIn("/acts/apify/", path)
        self.assertNotIn(NUMERIC, path)

    def test_dispatch_classifies_the_404_exactly_as_seen_live(self):
        _gate1, job = authorized_job_from_gate1()
        transport = SlugPathTransport()
        out = orchestrate.dispatch(
            job=job, canonical_url=REEL, content_id=CID, platform="instagram",
            actor=provider.ACTOR_REEL_SCRAPER,          # the slug, as the old wiring sent it
            lifetime_spent_micro=2600, acquisitions_succeeded=1,
            already_dispatched=False, transport=transport,
            downloader=lambda url: {}, finalizer=lambda m, p: {},
            estimate_micro=2600, is_exact=True)
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], 404)
        self.assertEqual(out["message"], "HTTP Error 404: Not Found")
        self.assertEqual(len(transport.calls), 1)

    def test_live_runner_sends_the_numeric_actor_id(self):
        """The fix: the wire call uses the numeric id while provenance keeps the slug."""
        fake = FakePostgrest(spent=2600, succeeded=1)
        gate1, job = authorized_job_from_gate1()
        io = fake_io(SlugPathTransport(), L.DurableLedger(fake))
        out = build_runner(io, gate1)(job=job)
        sent_actor = io.transport.calls[0][0]
        self.assertEqual(sent_actor, NUMERIC)
        self.assertNotIn("/", sent_actor)
        self.assertTrue(out["ok"] or not out["ok"])          # reached the provider
        self.assertEqual(io.transport.calls[0][1],
                         f"{apify_client.BASE_URL}/acts/{NUMERIC}/runs")


# ---------------------------------------------------------------------------
# Defect 2 — settlement for any failure shape
# ---------------------------------------------------------------------------
class TestSettlement(unittest.TestCase):
    def test_provider_failure_records_the_attempt_and_keeps_the_reservation(self):
        fake = FakePostgrest(spent=2600, succeeded=1)
        gate1, job = authorized_job_from_gate1()
        io = fake_io(ProviderFailureTransport(), L.DurableLedger(fake))
        out = build_runner(io, gate1)(job=job)

        self.assertFalse(out["ok"])
        self.assertEqual(out["failure_stage"], "actor_start")   # no run id captured
        self.assertEqual(len(io.transport.calls), 1)            # exactly one attempt
        self.assertEqual(len(fake.runs), 1)                     # attempt IS auditable
        self.assertEqual(fake.runs[0]["status"], L.STATUS_FAILED_UNKNOWN)
        self.assertIsNone(fake.runs[0]["cost_actual_micro"])     # unknown, never $0
        self.assertEqual(fake.ceiling["lifetime_spent_micro"],
                         2600 + cost.REEL_SCRAPER_RESULT_MICRO)   # reservation held
        self.assertEqual(fake.ceiling["acquisitions_succeeded"], 1)
        self.assertEqual(io.persisted, [])
        self.assertEqual(io.finalized, [])
        self.assertFalse(out["ledger"].get("settlement_failed"))

    def test_settlement_executes_for_an_integer_error_code(self):
        """The exact shape that used to raise TypeError and swallow settlement."""
        fake = FakePostgrest(spent=2600, succeeded=1)
        led = L.DurableLedger(fake)
        led.record_run(run_id="r1", actor_id=provider.ACTOR_REEL_SCRAPER,
                       job_id="job-1", estimate_micro=2600)
        led.reserve(amount_micro=2600, job_id="job-1")
        result = lr._settle(fake_io(SlugPathTransport(), led),
                            {"run_id": "r1"}, "fallback", {"actor": provider.ACTOR_REEL_SCRAPER,
                                                           "estimate_micro": 2600},
                            "job-1", ok=False, actual=0, reason=404)     # ← int, not dict
        self.assertFalse(result.get("settlement_failed"))
        self.assertEqual(result["status"], L.STATUS_FAILED_KNOWN)
        self.assertEqual(fake.ceiling["lifetime_spent_micro"], 2600)     # released
        self.assertEqual(fake.runs[0]["cost_actual_micro"], 0)

    def test_settlement_never_silently_swallows_a_failure(self):
        class BoomLedger:
            def record_run(self, **kw):
                raise RuntimeError("db down")

            def reconcile(self, **kw):  # pragma: no cover
                raise AssertionError("must not be reached")

        result = lr._settle(fake_io(SlugPathTransport(), BoomLedger()),
                            {}, "fallback", {"actor": "x", "estimate_micro": 2600},
                            "job-1", ok=False, actual=None, reason={"code": 500})
        self.assertTrue(result["settlement_failed"])
        self.assertEqual(result["error"], "RuntimeError")

    def test_no_retry_after_the_failure(self):
        fake = FakePostgrest(spent=2600, succeeded=1)
        gate1, job = authorized_job_from_gate1()
        io = fake_io(ProviderFailureTransport(), L.DurableLedger(fake))
        runner = build_runner(io, gate1)
        runner(job=job)
        out2 = runner(job=dict(job, status="failed"))
        self.assertFalse(out2["ok"])
        self.assertEqual(len(io.transport.calls), 1)


# ---------------------------------------------------------------------------
# Defect 3 — durable outcome before the card, retry distinguishable
# ---------------------------------------------------------------------------
class _OrderStore(ops.MemoryLaneStore):
    def __init__(self, log):
        super().__init__()
        self._log = log

    def update_job(self, job_id, patch):
        self._log.append("job_update")
        return super().update_job(job_id, patch)


class _OrderTransport(ops.RecordingTransport):
    def __init__(self, log):
        super().__init__()
        self._log = log

    def patch_original(self, payload):
        self._log.append("card_patch")
        return super().patch_original(payload)


class TestOutcomePersistence(unittest.TestCase):
    def _ctx(self):
        return ops.LaneContext(ref=CID, platform="instagram", content_id=CID,
                               canonical_url=REEL, state=card.STATE_APPROVED,
                               estimated_cost_micro=2600, ceiling_micro=1_000_000,
                               tenant_id="dreemzy", profile_id="acquisition-live-proof")

    def test_job_outcome_is_persisted_before_the_card_is_updated(self):
        log: list[str] = []
        store = _OrderStore(log)
        store.create_job(lane.plan_pending_job(fingerprint=CID, platform="instagram",
                                               platform_content_id=CID,
                                               canonical_url=REEL, actor="bryan"))

        def failing_runner(**kw):
            log.append("runner")
            return {"ok": False, "status": "failed", "attempts": 1,
                    "failure_stage": "actor_start",
                    "error": {"code": 404, "message": "HTTP Error 404: Not Found"},
                    "job_patch": {"status": "failed", "retry_count": 1,
                                  "status_reason": "actor_start: 404"}}

        out = ops.handle({"action": card.ACT_ACQUIRE, "ref": CID}, deps=store,
                         transport=_OrderTransport(log), actor="bryan", ctx=self._ctx(),
                         runner=failing_runner, now=10.0)
        self.assertTrue(out["ok"])
        self.assertEqual(out["state"], card.STATE_FAILED)
        self.assertIn("job_update", log)
        job_pos = [i for i, e in enumerate(log) if e == "job_update"]
        card_pos = [i for i, e in enumerate(log) if e == "card_patch"]
        # acquiring is shown BEFORE the work starts, and the durable outcome lands
        # BEFORE the terminal card update — the card can never outrun the ledger
        self.assertTrue(card_pos, "the card is updated for acquiring and terminal")
        self.assertLess(card_pos[0], log.index("runner"),
                        "the acquiring state must be visible before the runner runs")
        self.assertLess(job_pos[-1], card_pos[-1],
                        "the durable outcome must precede the terminal card update")
        persisted = next(row for row in store.jobs.values()
                         if row.get("authorization_type") == "acquire_for_reaction")
        self.assertEqual(persisted["status"], "failed")
        self.assertEqual(persisted["retry_count"], 1)

    def test_retry_is_reopened_and_stays_distinguishable(self):
        log: list[str] = []
        store = _OrderStore(log)
        pending = lane.plan_pending_job(fingerprint=CID, platform="instagram",
                                        platform_content_id=CID, canonical_url=REEL,
                                        actor="bryan")
        store.create_job(pending)
        job = lane.plan_authorized_job(pending_job=pending, actor="bryan")
        row, _created = store.create_job(job)
        row["status"] = "failed"
        row["retry_count"] = 1

        seen = {}

        def runner(**kw):
            seen.update(kw)
            return {"ok": True, "status": "acquired", "attempts": 2,
                    "library": {"asset_filename": "a.mp4", "asset_hash": "f" * 64},
                    "cost": {"actual_micro": 2600},
                    "job_patch": {"status": "acquired", "retry_count": 2}}

        live_ctx = ops.context_for({"action": card.ACT_RETRY, "ref": CID}, store=store)
        out = ops.handle({"action": card.ACT_RETRY, "ref": CID}, deps=store,
                         transport=_OrderTransport(log), actor="bryan", ctx=live_ctx,
                         runner=runner, now=99.0)
        self.assertTrue(out["ok"])
        self.assertTrue(seen.get("retry_authorized"))          # explicit, not automatic
        self.assertEqual(seen["job"]["status"], "queued")       # reopened for the retry
        self.assertEqual(seen["job"]["retry_count"], 1)         # counter never reset


if __name__ == "__main__":
    unittest.main()
