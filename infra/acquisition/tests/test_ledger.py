"""Deterministic tests for the durable spend ledger (migrations 034/035).

Run:  python3 -m unittest discover -s infra/acquisition/tests -v

No network, no provider, no spend. The fake PostgREST below enforces the same
compare-and-swap semantics the real service does: a conditional PATCH matches
zero rows when the observed state has changed underneath it.
"""

import sys
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
for d in ("acquisition", "discord-review"):
    sys.path.insert(0, str(_REPO_ROOT / "infra" / d))

import ledger as L  # noqa: E402


class FakePostgrest:
    """Minimal PostgREST with CAS semantics over one ceiling row + run rows."""

    def __init__(self, *, spent=0, succeeded=1, ceiling=1_000_000, max_success=10,
                 disabled=False):
        self.ceiling = {"id": 1, "ceiling_micro": ceiling, "lifetime_spent_micro": spent,
                        "max_acquisitions": max_success, "acquisitions_succeeded": succeeded,
                        "disabled": disabled, "updated_at": "t0"}
        self.runs: list[dict] = []
        self.log: list[tuple] = []
        self.hijack = None          # optional callable(state) run before each patch

    # -- helpers ------------------------------------------------------------
    @staticmethod
    def _split(path):
        table, _, qs = path.partition("?")
        filters = []
        for part in filter(None, qs.split("&")):
            if part.startswith("select=") or part.startswith("on_conflict="):
                continue
            col, _, rest = part.partition("=")
            op, _, val = rest.partition(".")
            filters.append((col, op, val))
        return table, filters, qs

    def _rows(self, table):
        return [self.ceiling] if table == L.CEILING_TABLE else self.runs

    def _match(self, row, filters):
        for col, op, val in filters:
            if col not in row:
                return False
            current = row[col]
            if op == "eq":
                if isinstance(current, bool):
                    if str(current).lower() != val.lower():
                        return False
                elif str(current) != val:
                    return False
            else:  # pragma: no cover - only eq is used
                return False
        return True

    # -- PostgREST surface --------------------------------------------------
    def get(self, path):
        table, filters, _qs = self._split(path)
        return [dict(r) for r in self._rows(table) if self._match(r, filters)]

    def patch(self, path, body):
        self.log.append(("patch", path, dict(body)))
        if self.hijack:
            self.hijack(self)
        table, filters, _qs = self._split(path)
        matched = [r for r in self._rows(table) if self._match(r, filters)]
        if not matched:
            return []
        for row in matched:
            row.update(body)
        return [dict(r) for r in matched]

    def post(self, path, body):
        self.log.append(("post", path, dict(body)))
        table, filters, qs = self._split(path)
        if "on_conflict=run_id" in qs or "on_conflict=run_id" in path:
            key = body.get("run_id")
            if any(r.get("run_id") == key for r in self.runs):
                return []                      # ignore-duplicates
        row = dict(body)
        row.setdefault("id", f"run-{len(self.runs) + 1}")
        self.runs.append(row)
        return [dict(row)]


def make(**kw):
    fake = FakePostgrest(**kw)
    return L.DurableLedger(fake), fake


class TestReserve(unittest.TestCase):
    def test_successful_reservation_increments_the_ceiling(self):
        led, fake = make()
        res = led.reserve(amount_micro=2600, job_id="job-1")
        self.assertEqual(res["reserved_micro"], 2600)
        self.assertEqual(res["observed_before"], 0)
        self.assertEqual(fake.ceiling["lifetime_spent_micro"], 2600)

    def test_refusals_are_classified(self):
        led, _ = make(disabled=True)
        with self.assertRaises(L.LedgerError) as e:
            led.reserve(amount_micro=2600)
        self.assertEqual(e.exception.code, "BREAKER_DISABLED")

        led2, _ = make(spent=1_000_000)
        with self.assertRaises(L.LedgerError) as e2:
            led2.reserve(amount_micro=2600)
        self.assertEqual(e2.exception.code, "CEILING_REACHED")

        led3, _ = make(succeeded=10, max_success=10)
        with self.assertRaises(L.LedgerError) as e3:
            led3.reserve(amount_micro=2600)
        self.assertEqual(e3.exception.code, "MAX_ACQUISITIONS_REACHED")

        led4, _ = make()
        with self.assertRaises(L.LedgerError) as e4:
            led4.reserve(amount_micro=-1)
        self.assertEqual(e4.exception.code, "BAD_AMOUNT")

    def test_missing_breaker_row_is_refused(self):
        fake = FakePostgrest()
        fake.ceiling = {}
        led = L.DurableLedger(fake)
        with self.assertRaises(L.LedgerError) as e:
            led.reserve(amount_micro=2600)
        self.assertEqual(e.exception.code, "CEILING_ROW_MISSING")

    def test_reservation_is_never_written_when_it_would_break_the_ceiling(self):
        led, fake = make(ceiling=5000, spent=3000)
        with self.assertRaises(L.LedgerError):
            led.reserve(amount_micro=2600)
        self.assertEqual(fake.ceiling["lifetime_spent_micro"], 3000)   # untouched


class TestConcurrency(unittest.TestCase):
    def test_two_processes_cannot_double_spend_the_remaining_budget(self):
        fake = FakePostgrest(ceiling=1_000_000)
        a = L.DurableLedger(fake)
        b = L.DurableLedger(fake)
        a.reserve(amount_micro=600_000)
        with self.assertRaises(L.LedgerError) as e:
            b.reserve(amount_micro=600_000)
        self.assertEqual(e.exception.code, "CEILING_REACHED")
        self.assertEqual(fake.ceiling["lifetime_spent_micro"], 600_000)  # not 1_200_000

    def test_a_lost_cas_race_is_retried_not_double_counted(self):
        """A concurrent writer that wins first must not cause an over-count."""
        fake = FakePostgrest(ceiling=1_000_000)
        led = L.DurableLedger(fake)
        state = {"fired": False}

        def hijack(f):
            # Simulate another process reserving between our read and our PATCH.
            if not state["fired"]:
                state["fired"] = True
                f.ceiling["lifetime_spent_micro"] += 1000
        fake.hijack = hijack

        res = led.reserve(amount_micro=2600)
        self.assertEqual(res["reserved_micro"], 2600)
        # 1000 (other process) + 2600 (ours), counted exactly once each.
        self.assertEqual(fake.ceiling["lifetime_spent_micro"], 3600)

    def test_hopeless_race_raises_cas_conflict(self):
        fake = FakePostgrest()
        led = L.DurableLedger(fake)
        fake.hijack = lambda f: f.ceiling.update(updated_at=f"t{f.ceiling['lifetime_spent_micro']+1}")
        led_ceiling = fake.ceiling
        calls = {"n": 0}

        def always_change(f):
            calls["n"] += 1
            f.ceiling["lifetime_spent_micro"] = 999_999 - calls["n"]
        fake.hijack = always_change
        with self.assertRaises(L.LedgerError) as e:
            led.reserve(amount_micro=2600)
        self.assertIn(e.exception.code, ("CAS_CONFLICT", "CEILING_REACHED"))
        self.assertLessEqual(led_ceiling["lifetime_spent_micro"], 1_000_000)


class TestReconcile(unittest.TestCase):
    def _reserved(self, **kw):
        led, fake = make(**kw)
        led.reserve(amount_micro=2600, job_id="job-1")
        led.record_run(run_id="run-1", actor_id="apify/instagram-reel-scraper",
                       job_id="job-1", estimate_micro=2600)
        return led, fake

    def test_successful_run_reconciles_to_the_actual_charge(self):
        led, fake = self._reserved()
        out = led.reconcile(run_id="run-1", actual_micro=2600, succeeded=True)
        self.assertTrue(out["applied"])
        self.assertEqual(out["status"], L.STATUS_ACQUIRED)
        self.assertEqual(out["delta_micro"], 0)
        self.assertEqual(fake.ceiling["lifetime_spent_micro"], 2600)
        self.assertEqual(fake.ceiling["acquisitions_succeeded"], 2)   # was 1
        self.assertEqual(fake.runs[0]["cost_actual_micro"], 2600)
        self.assertEqual(fake.runs[0]["status"], L.STATUS_ACQUIRED)

    def test_cheaper_actual_credits_the_difference_back(self):
        led, fake = self._reserved()
        out = led.reconcile(run_id="run-1", actual_micro=1000, succeeded=True)
        self.assertEqual(out["delta_micro"], -1600)
        self.assertEqual(fake.ceiling["lifetime_spent_micro"], 1000)

    def test_billed_provider_failure_keeps_the_charge(self):
        led, fake = self._reserved()
        out = led.reconcile(run_id="run-1", actual_micro=2600, succeeded=False)
        self.assertEqual(out["status"], L.STATUS_FAILED_KNOWN)
        self.assertEqual(fake.ceiling["lifetime_spent_micro"], 2600)    # still charged
        self.assertEqual(fake.ceiling["acquisitions_succeeded"], 1)     # not a success

    def test_unknown_cost_failure_never_restores_the_reservation(self):
        led, fake = self._reserved()
        out = led.reconcile(run_id="run-1", actual_micro=None, succeeded=False)
        self.assertEqual(out["status"], L.STATUS_FAILED_UNKNOWN)
        self.assertTrue(out["cost_unknown"])
        self.assertIsNone(fake.runs[0]["cost_actual_micro"])            # classified, not $0
        self.assertEqual(fake.ceiling["lifetime_spent_micro"], 2600)    # reservation kept
        self.assertEqual(fake.ceiling["acquisitions_succeeded"], 1)

    def test_duplicate_reconciliation_is_idempotent(self):
        led, fake = self._reserved()
        first = led.reconcile(run_id="run-1", actual_micro=2600, succeeded=True)
        second = led.reconcile(run_id="run-1", actual_micro=2600, succeeded=True)
        self.assertTrue(first["applied"])
        self.assertFalse(second["applied"])
        self.assertTrue(second["idempotent"])
        self.assertEqual(fake.ceiling["lifetime_spent_micro"], 2600)    # counted once
        self.assertEqual(fake.ceiling["acquisitions_succeeded"], 2)     # counted once

    def test_replay_after_a_crash_cannot_double_charge(self):
        led, fake = self._reserved()
        led.reconcile(run_id="run-1", actual_micro=None, succeeded=False)
        # recovery replay with the true charge now known:
        again = led.reconcile(run_id="run-1", actual_micro=2600, succeeded=False)
        self.assertFalse(again["applied"])
        self.assertEqual(fake.ceiling["lifetime_spent_micro"], 2600)

    def test_run_row_records_no_secrets(self):
        led, fake = self._reserved()
        blob = str(fake.runs[0]).lower()
        for marker in ("token", "sig=", "bearer", "authorization", "cookie"):
            self.assertNotIn(marker, blob)


if __name__ == "__main__":
    unittest.main()
