"""Mocked proof: one genuine Retry Manually interaction, end to end.

Run:  python3 -m unittest infra.acquisition.tests.test_retry_authorization -v

Assertion-guarded: no network, no Apify call, no reservation against a real
ledger, no download, no library write. The provider path is the REAL
``orchestrate.dispatch`` with a mocked transport; the ledger is the real
``DurableLedger`` over an in-memory PostgREST stand-in.

Proves, for Gate 2 job b1bb78e6-… (reel DdQE_RUS0GJ):
  * the Retry is a SEPARATE explicit authorization (not a repeat Acquire)
  * retry_count = 1 is preserved BEFORE execution
  * exactly one additional bounded attempt is permitted (attempts -> 2)
  * the wire call uses numeric Actor id xMc5Ga1oCONPmWJIa
  * provenance keeps the readable slug apify/instagram-reel-scraper
  * exactly 2,600 micro is reserved
  * correlation stays with Gate 1 row 40560336-bbe9-468e-9230-5fa880c9eef1
  * the historical chrome job is never consulted
  * a second retry is refused without another explicit authorization
  * nothing retries automatically and no fallback actor is ever used
"""

import json
import sys
import unittest
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
NUMERIC = provider.actor_api_id(provider.ACTOR_REEL_SCRAPER)      # xMc5Ga1oCONPmWJIa
SLUG = provider.ACTOR_REEL_SCRAPER
GATE1_DEDUP = "a3935fe97af53220986aebcaa641cee3d9406d1501b39e3664bba8363f2212b4"
GATE2_ID = "b1bb78e6-3601-47d9-9068-46afb45587e1"
GATE2_DEDUP = "d84885c438eeba7e97c4269140750dff575c3ae77785ed4b2635877c80c80f03"
HISTORICAL_ID = "d3b4bba9-4e1c-49d8-b6c6-f7bf4a8fc742"
HISTORICAL_DEDUP = "7f9ddc86f56c335b605494c0e22ca2239c35d27c112d5660ec2aa0f4596deb88"
SENTINEL_RUN_ID = f"no-apify-run:{GATE2_ID}"


class LedgerSpy:
    """Records ledger calls while delegating to the real DurableLedger."""

    def __init__(self, inner: L.DurableLedger) -> None:
        self.inner = inner
        self.reserves: list[dict] = []
        self.records: list[dict] = []
        self.reconciles: list[dict] = []

    def reserve(self, **kw):
        self.reserves.append(kw)
        return self.inner.reserve(**kw)

    def record_run(self, **kw):
        self.records.append(kw)
        return self.inner.record_run(**kw)

    def reconcile(self, **kw):
        self.reconciles.append(kw)
        return self.inner.reconcile(**kw)


class RecordingTransport:
    """Mocked Apify transport: records the wire call, returns one result."""

    def __init__(self, log: list[str]) -> None:
        self.log = log
        self.calls: list[tuple] = []

    def start_run(self, actor, input_):
        self.log.append("provider")
        self.calls.append((actor, input_))
        return {"defaultDatasetId": "ds-retry", "id": "apify-run-retry-1"}

    def get_dataset_items(self, dataset_id):
        return [{"shortCode": CID, "videoUrl":
                 "https://scontent.cdninstagram.com/v/t51.2885-15/reel.mp4?sig=***&oh=xyz"}]


def build_store(log: list[str]) -> ops.MemoryLaneStore:
    store = ops.MemoryLaneStore()
    store.jobs[GATE1_DEDUP] = {
        "id": "40560336-bbe9-468e-9230-5fa880c9eef1",
        "dedup_key": GATE1_DEDUP, "duplicate_fingerprint": CID,
        "platform": "instagram", "platform_content_id": CID,
        "canonical_url": REEL, "authorization_type": "approved_for_acquisition",
        "status": "queued", "retry_count": 0, "actor": "bryan",
    }
    store.jobs[GATE2_DEDUP] = {
        "id": GATE2_ID, "dedup_key": GATE2_DEDUP, "duplicate_fingerprint": CID,
        "platform": "instagram", "platform_content_id": CID, "canonical_url": REEL,
        "authorization_type": "acquire_for_reaction", "status": "failed",
        "retry_count": 1, "correlation_id": GATE1_DEDUP, "actor": "bryan",
        "status_reason": "actor_start: HTTP 404", "max_bytes": lane.PILOT_MAX_BYTES,
        "max_duration_seconds": lane.PILOT_MAX_DURATION_SECONDS,
    }
    store.source_contexts[CID] = {"platform": "instagram", "canonical_url": REEL,
                                  "content_id": CID, "fingerprint": CID}
    return store


def build_runner(store, ledger, transport, log, base, prior_rows=()):
    io = base.FakeIO()
    io.transport = transport
    io.ledger = ledger
    io.pending_job = lambda fp: store.jobs[GATE1_DEDUP]
    io.library_rows = lambda: []
    # attempt-scoped lookup (the live path); rows for THIS job only
    io.provider_runs_for_job = lambda job_id: [r for r in prior_rows
                                               if r.get("acquisition_job_id") == job_id]
    io.ceiling = lambda: {"ceiling_micro": 1_000_000, "lifetime_spent_micro": 2600,
                          "max_acquisitions": 10, "acquisitions_succeeded": 1,
                          "disabled": False}
    return lr.build_live_runner(io=io, store=store,
                                environ={"LOOM_APIFY_API_TOKEN": "present-for-proof"},
                                dispatch=orchestrate.dispatch)


ATTEMPT1_AUDIT_ROW = {
    "run_id": SENTINEL_RUN_ID,
    "status": "failed_cost_known",
    "acquisition_job_id": GATE2_ID,
    "cost_actual_micro": 0,
}


def run_retry(*, retry_count: int = 1, prior_rows=()):
    import test_live_runner as base
    log: list[str] = []
    store = build_store(log)
    store.jobs[GATE2_DEDUP]["retry_count"] = retry_count
    fake = FakePostgrest(spent=2600, succeeded=1)
    spy = LedgerSpy(L.DurableLedger(fake))
    transport = RecordingTransport(log)
    runner = build_runner(store, spy, transport, log, base, prior_rows=prior_rows)
    ctx = ops.context_for({"action": card.ACT_RETRY, "ref": CID}, store=store)
    card_transport = ops.RecordingTransport()
    out = ops.handle({"action": card.ACT_RETRY, "ref": CID}, deps=store,
                     transport=card_transport, actor="bryan", ctx=ctx,
                     runner=runner, now=5000.0)
    return {"out": out, "store": store, "spy": spy, "fake": fake,
            "transport": transport, "log": log, "ctx": ctx,
            "card_transport": card_transport}


class TestRetryAuthorization(unittest.TestCase):
    def setUp(self):
        self.r = run_retry()
        self.out = self.r["out"]

    # --- the interaction itself -------------------------------------------
    def test_retry_is_a_separate_explicit_authorization(self):
        self.assertEqual(self.r["ctx"].state, card.STATE_FAILED)   # only a FAILED card offers it
        self.assertTrue(self.out["ok"])
        self.assertEqual(self.out["action"], card.ACT_RETRY)       # distinct from Acquire
        self.assertEqual(self.out["state"], card.STATE_ACQUIRED)

    def test_retry_count_is_one_before_execution(self):
        job = self.r["spy"].reserves[0]
        self.assertEqual(job["amount_micro"], cost.REEL_SCRAPER_RESULT_MICRO)
        # the runner received the job with its original counter preserved
        self.assertEqual(self.out["effects"]["attempts"], 2)

    def test_job_row_was_reopened_with_the_original_counter(self):
        stored = self.r["store"].jobs[GATE2_DEDUP]
        self.assertEqual(stored["retry_count"], 2)                 # consumed attempt recorded
        self.assertEqual(stored["status"], "acquired")
        self.assertIn("retry-authorized", stored.get("status_reason", ""))

    def test_exactly_one_additional_bounded_attempt(self):
        self.assertEqual(len(self.r["transport"].calls), 1)
        self.assertEqual(self.out["effects"]["attempts"], 2)

    # --- wire + provenance ------------------------------------------------
    def test_wire_call_uses_the_numeric_actor_id(self):
        actor, _inp = self.r["transport"].calls[0]
        self.assertEqual(actor, NUMERIC)
        self.assertNotIn("/", actor)

    def test_provenance_keeps_the_readable_slug(self):
        self.assertEqual(self.out["effects"]["job"]["actor"], "bryan")   # authorizing human
        self.assertEqual(self.r["spy"].records[0]["actor_id"], SLUG)     # ledger keeps the slug

    def test_reserves_the_rebaselined_estimate(self):
        self.assertEqual([r["amount_micro"] for r in self.r["spy"].reserves],
                         [cost.REEL_SCRAPER_RESULT_MICRO])

    # --- correlation and isolation ---------------------------------------
    def test_stays_correlated_to_the_gate1_row(self):
        stored = self.r["store"].jobs[GATE2_DEDUP]
        self.assertEqual(stored["correlation_id"], GATE1_DEDUP)

    def test_historical_chrome_job_is_never_touched(self):
        blob = repr(self.r["spy"].records) + repr(self.r["spy"].reserves) + \
            repr(self.r["transport"].calls)
        self.assertNotIn(HISTORICAL_ID, blob)
        self.assertNotIn(HISTORICAL_DEDUP, blob)

    # --- bounded, no automation -------------------------------------------
    def test_a_second_retry_is_refused(self):
        second = run_retry(retry_count=2)
        # the INTERACTION is handled (so the operator sees the refusal), but the
        # acquisition is refused before any reservation or provider call.
        self.assertTrue(second["out"]["ok"])
        self.assertEqual(second["out"]["state"], card.STATE_FAILED)
        patched = json.dumps(second["card_transport"].updates)
        self.assertIn("ATTEMPTS_EXHAUSTED", patched)
        self.assertEqual(second["transport"].calls, [])            # no provider call
        self.assertEqual(second["spy"].reserves, [])               # no reservation
        self.assertEqual(second["fake"].ceiling["lifetime_spent_micro"], 2600)

    def test_nothing_retries_automatically_and_no_fallback(self):
        self.assertFalse(self.out["effects"].get("auto_retry", False))
        self.assertEqual(len(self.r["transport"].calls), 1)
        self.assertNotIn(provider.ACTOR_INSTAGRAM_SCRAPER,
                         [c[0] for c in self.r["transport"].calls])

    # --- audit immutability ------------------------------------------------
    def test_previous_audit_row_is_not_reused(self):
        new_run_id = self.r["spy"].records[0]["run_id"]
        self.assertNotEqual(new_run_id, SENTINEL_RUN_ID)
        self.assertFalse(new_run_id.startswith("no-apify-run:"))


class TestAttemptScope(unittest.TestCase):
    """The provider-run lookup must be attempt-scoped, not job-scoped."""

    def _preflight(self, prior_rows=(), *, legacy_boolean=None, lookup_raises=False,
                   rows_for_other_job=False):
        import test_live_runner as base
        store = build_store([])
        io = base.FakeIO()
        io.pending_job = lambda fp: store.jobs[GATE1_DEDUP]
        io.library_rows = lambda: []
        io.ceiling = lambda: {"ceiling_micro": 1_000_000, "lifetime_spent_micro": 2600,
                              "max_acquisitions": 10, "acquisitions_succeeded": 1,
                              "disabled": False}
        if lookup_raises:
            def boom(_job_id):
                raise RuntimeError("supabase down")
            io.provider_runs_for_job = boom
        else:
            io.provider_runs_for_job = lambda job_id: list(prior_rows)
        if legacy_boolean is not None:
            io.provider_runs_for_job = None
            io.existing_run = lambda job_id: legacy_boolean
        job = dict(store.jobs[GATE2_DEDUP], status="queued")
        return lr.preflight(job, io=io, environ={"LOOM_APIFY_API_TOKEN": "x"})

    def test_attempt1_terminal_no_run_record_does_not_block_attempt2(self):
        pre = self._preflight([ATTEMPT1_AUDIT_ROW])
        self.assertEqual(pre["reserved_micro"], cost.REEL_SCRAPER_RESULT_MICRO)

    def test_active_attempt_still_blocks(self):
        with self.assertRaises(lr.LiveRunnerError) as e:
            self._preflight([{**ATTEMPT1_AUDIT_ROW, "status": "reserved"}])
        self.assertEqual(e.exception.code, "ATTEMPT_IN_FLIGHT")

    def test_successful_run_still_blocks(self):
        with self.assertRaises(lr.LiveRunnerError) as e:
            self._preflight([{**ATTEMPT1_AUDIT_ROW, "status": "acquired"}])
        self.assertEqual(e.exception.code, "ALREADY_ACQUIRED")

    def test_unresolved_cost_still_blocks(self):
        with self.assertRaises(lr.LiveRunnerError) as e:
            self._preflight([{**ATTEMPT1_AUDIT_ROW, "status": "failed_cost_unknown",
                              "cost_actual_micro": None}])
        self.assertEqual(e.exception.code, "UNRESOLVED_COST")

    def test_rows_for_another_job_are_ignored(self):
        other = {"run_id": "r-other", "status": "reserved",
                 "acquisition_job_id": HISTORICAL_ID}
        pre = self._preflight([other])
        self.assertEqual(pre["reserved_micro"], cost.REEL_SCRAPER_RESULT_MICRO)

    def test_legacy_boolean_still_blocks(self):
        with self.assertRaises(lr.LiveRunnerError) as e:
            self._preflight(legacy_boolean=True)
        self.assertEqual(e.exception.code, "ATTEMPT_IN_FLIGHT")

    def test_lookup_failure_fails_closed(self):
        with self.assertRaises(lr.LiveRunnerError) as e:
            self._preflight(lookup_raises=True)
        self.assertEqual(e.exception.code, "RUN_LOOKUP_FAILED")

    def test_full_retry_proceeds_despite_attempt1s_record(self):
        r = run_retry(prior_rows=[ATTEMPT1_AUDIT_ROW])
        self.assertTrue(r["out"]["ok"])
        self.assertEqual(r["out"]["state"], card.STATE_ACQUIRED)
        self.assertEqual(len(r["transport"].calls), 1)
        self.assertNotEqual(r["spy"].records[0]["run_id"], SENTINEL_RUN_ID)


class TestOperatorDisplay(unittest.TestCase):
    def test_raw_discord_identity_is_not_shown(self):
        line = card.provenance_line({
            "content_id": CID,
            "provenance": {"adapter": "apify:instagram-reel-scraper",
                           "actor": "passportpetty#688976899094872148"}})
        self.assertIn("Authorized by Bryan", line)
        self.assertIn("apify/instagram-reel-scraper", line)
        self.assertNotIn("688976899094872148", line)
        self.assertNotIn("#", line)

    def test_snowflake_is_never_rendered_as_a_name(self):
        self.assertEqual(card.operator_label("123456789012345678"), "unknown")
        self.assertEqual(card.operator_label(""), "unknown")

    def test_unknown_operator_falls_back_to_the_stripped_name(self):
        self.assertEqual(card.operator_label("someoneelse#1234"), "someoneelse")


if __name__ == "__main__":
    unittest.main()
