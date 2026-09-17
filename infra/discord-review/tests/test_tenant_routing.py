"""Deterministic tests for tenant routing (assign / reassign / copy).

Run:  python3 -m unittest discover -s infra/discord-review/tests -v
"""

import sys
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO_ROOT / "infra" / "discord-review"))

from tenant_routing import (  # noqa: E402
    RoutingError,
    plan_routing,
    decide_operation,
    build_routing_event,
    OP_ASSIGN,
    OP_REASSIGN,
    OP_COPY,
)

ACTIVE = {"dreemzy", "client-a"}


class TestPlanRouting(unittest.TestCase):
    def test_assign_from_unassigned(self):
        d = plan_routing(OP_ASSIGN, "", "dreemzy", ACTIVE)
        self.assertEqual(d["action"], OP_ASSIGN)
        self.assertEqual(d["destination"], "dreemzy")

    def test_reassign_tenant_correction(self):
        d = plan_routing(OP_REASSIGN, "client-a", "dreemzy", ACTIVE)
        self.assertEqual(d["action"], OP_REASSIGN)

    def test_cross_tenant_copy(self):
        d = plan_routing(OP_COPY, "client-a", "dreemzy", ACTIVE)
        self.assertEqual(d["action"], OP_COPY)

    def test_same_tenant_duplicate_noop(self):
        d = plan_routing(OP_COPY, "dreemzy", "dreemzy", ACTIVE)
        self.assertEqual(d["action"], "noop")
        d2 = plan_routing(OP_REASSIGN, "dreemzy", "dreemzy", ACTIVE)
        self.assertEqual(d2["action"], "noop")

    def test_assignment_collision(self):
        d = plan_routing(OP_ASSIGN, "", "dreemzy", ACTIVE, collision=True)
        self.assertEqual(d["action"], "collision")

    def test_downstream_reassign_block(self):
        d = plan_routing(OP_REASSIGN, "client-a", "dreemzy", ACTIVE, downstream_started=True)
        self.assertEqual(d["action"], "blocked")

    def test_invalid_operation(self):
        with self.assertRaises(RoutingError):
            plan_routing("nonsense", "", "dreemzy", ACTIVE)

    def test_invalid_destination(self):
        with self.assertRaises(RoutingError):
            plan_routing(OP_ASSIGN, "", "unknown-tenant", ACTIVE)

    def test_proof_destination_rejected(self):
        with self.assertRaises(RoutingError):
            plan_routing(OP_ASSIGN, "", "proof-tenant", {"proof-tenant"})


class TestHelpers(unittest.TestCase):
    def test_decide_operation(self):
        self.assertEqual(decide_operation(""), OP_ASSIGN)
        self.assertEqual(decide_operation("client-a"), OP_REASSIGN)
        self.assertEqual(decide_operation("client-a", explicit=OP_COPY), OP_COPY)

    def test_build_routing_event(self):
        e = build_routing_event("f" * 64, "", "dreemzy", OP_ASSIGN, "bryan#1",
                                reason="nice clip", correlation_id="corr-1")
        self.assertEqual(e["old_destination"], "")
        self.assertEqual(e["new_destination"], "dreemzy")
        self.assertEqual(e["operation"], OP_ASSIGN)
        self.assertEqual(e["actor"], "bryan#1")
        self.assertEqual(e["correlation_id"], "corr-1")


if __name__ == "__main__":
    unittest.main(verbosity=2)
