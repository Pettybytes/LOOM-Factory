"""Seed/query yield instrumentation: counters, isolation, cost, replay safety."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from discovery.moment_queries import MomentQuery, compose_creator_moment  # noqa: E402
from discovery.seed_yield import COUNTERS, accumulate, derived, event  # noqa: E402


class SeedKeyTests(unittest.TestCase):
    def test_seed_key_is_stable_and_case_insensitive(self):
        self.assertEqual(MomentQuery(text="crazy fall", category="public_human").seed_key,
                         MomentQuery(text="Crazy Fall", category="public_human").seed_key)

    def test_creator_seed_has_its_own_key(self):
        a = MomentQuery(text="crazy fall", category="public_human")
        b = compose_creator_moment("Kai Cenat", "crazy fall")
        self.assertNotEqual(a.seed_key, b.seed_key)


class AccumulateTests(unittest.TestCase):
    def test_counters_accumulate(self):
        row = accumulate(None, [event(discovered=10, market_passed=4, market_rejected=6)],
                         {"tenant_id": "dreemzy", "platform": "youtube", "provider": "rss",
                          "seed_key": "public_human::crazy fall"})
        self.assertEqual(row["discovered"], 10)
        self.assertEqual(row["market_passed"], 4)

    def test_tenant_and_platform_separation(self):
        a = accumulate(None, [event(discovered=5)], {"tenant_id": "dreemzy", "platform": "youtube", "seed_key": "k"})
        b = accumulate(None, [event(discovered=9)], {"tenant_id": "other", "platform": "tiktok", "seed_key": "k"})
        self.assertEqual((a["discovered"], b["discovered"]), (5, 9))
        self.assertNotEqual((a["tenant_id"], a["platform"]), (b["tenant_id"], b["platform"]))

    def test_provider_cost_and_credits_accumulate(self):
        row = accumulate(None, [event(discovered=2, provider_cost_usd=0.01, vidiq_credits=5),
                               event(discovered=3, provider_cost_usd=0.02, vidiq_credits=0)])
        self.assertEqual(row["discovered"], 5)
        self.assertAlmostEqual(row["provider_cost_usd"], 0.03, places=6)
        self.assertEqual(row["vidiq_credits"], 5)

    def test_replay_of_the_same_batch_double_counts_so_callers_send_events_once(self):
        """Documented contract: one real event is sent once. A naive replay DOES
        add again, which is why callers must dedupe by event identity."""
        once = accumulate(None, [event(discovered=1)], {"tenant_id": "t", "platform": "p", "seed_key": "k"})
        twice = accumulate(once, [event(discovered=1)])
        self.assertEqual(twice["discovered"], 2)

    def test_unknown_field_rejected(self):
        with self.assertRaises(KeyError):
            event(discovered=1, made_up_counter=5)

    def test_all_counters_default_to_zero(self):
        row = accumulate(None, [], {"tenant_id": "t", "platform": "p", "seed_key": "k"})
        for c in COUNTERS:
            self.assertEqual(row[c], 0, c)


class DerivedTests(unittest.TestCase):
    def test_rates_and_cost_per_outcome(self):
        row = accumulate(None, [event(discovered=10, market_passed=4, market_rejected=6,
                                      tenant_passed=2, tenant_rejected=2, fire_passed=1,
                                      carded=1, approved=1, provider_cost_usd=0.05)],
                         {"tenant_id": "t", "platform": "p", "seed_key": "k"})
        d = derived(row)
        self.assertEqual(d["market_pass_rate"], 0.4)
        self.assertEqual(d["tenant_pass_rate"], 0.5)
        self.assertEqual(d["fire_yield"], 0.1)
        self.assertEqual(d["approval_rate"], 1.0)
        self.assertAlmostEqual(d["cost_per_fire"], 0.05, places=6)
        self.assertAlmostEqual(d["cost_per_approved_moment"], 0.05, places=6)

    def test_rate_is_none_not_zero_without_denominator(self):
        row = accumulate(None, [event(discovered=3)], {"tenant_id": "t", "platform": "p", "seed_key": "k"})
        d = derived(row)
        self.assertIsNone(d["market_pass_rate"])
        self.assertIsNone(d["cost_per_fire"])


if __name__ == "__main__":
    unittest.main()


class IdempotencyTests(unittest.TestCase):
    """Replay safety: the same event applied twice must count once (SS19)."""

    def _event(self):
        return event(discovered=10, market_passed=3, market_rejected=7,
                     provider_cost_usd=0.01, vidiq_credits=0)

    def test_same_event_replayed_twice_increments_once(self):
        from discovery.seed_yield import accumulate_idempotent, event_id
        ident = {"tenant_id": "dreemzy", "platform": "youtube", "provider": "rss", "seed_key": "k1"}
        ev = self._event()
        row, ids = accumulate_idempotent(None, [dict(ev, **ident)], ident, applied_ids=None)
        self.assertEqual(row["discovered"], 10)
        row2, ids2 = accumulate_idempotent(row, [dict(ev, **ident)], ident, applied_ids=ids)
        self.assertEqual(row2["discovered"], 10)          # NOT 20
        self.assertEqual(len(ids2), 1)
        self.assertEqual(event_id(dict(ev, **ident)), event_id(dict(ev, **ident)))

    def test_distinct_events_still_accumulate(self):
        from discovery.seed_yield import accumulate_idempotent
        ident = {"tenant_id": "dreemzy", "platform": "youtube", "provider": "rss", "seed_key": "k2"}
        a = dict(event(discovered=5), **ident)
        b = dict(event(discovered=7), **ident)
        row, ids = accumulate_idempotent(None, [a, b], ident)
        self.assertEqual(row["discovered"], 12)
        self.assertEqual(len(ids), 2)

    def test_replay_reports_no_new_ids(self):
        from discovery.seed_yield import accumulate_idempotent
        ident = {"tenant_id": "t", "platform": "p", "provider": "x", "seed_key": "k3"}
        ev = dict(self._event(), **ident)
        row, ids = accumulate_idempotent(None, [ev], ident)
        _, again = accumulate_idempotent(row, [ev], ident, applied_ids=ids)
        self.assertEqual(again, ids)
