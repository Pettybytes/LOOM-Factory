"""Wiring tests: the adapter exposes the AQ route and fails closed by default.

Run:  python3 -m unittest discover -s infra/discord-review/tests -v

Importing the adapter is itself part of the proof: the module can only import if
the acquisition lane is wired into the real operator surface. No server is
started, no Discord call is made, no provider is touched.
"""

import os
import sys
import unittest
import unittest.mock
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
for d in ("discord-review", "acquisition"):
    sys.path.insert(0, str(_REPO_ROOT / "infra" / d))

# The adapter requires its configuration at import time; supply inert values.
for _k in ("LOOM_DISCORD_APP_ID", "LOOM_DISCORD_PUBLIC_KEY", "LOOM_DISCORD_BOT_TOKEN",
           "LOOM_SUPABASE_URL", "LOOM_SUPABASE_SERVICE_ROLE_KEY"):
    os.environ.setdefault(_k, "test-inert")

import adapter  # noqa: E402
import acquisition_card as card  # noqa: E402
import acquisition_ops as ops  # noqa: E402

CID = "DdZS1QRhrXE"


class TestRouteActivation(unittest.TestCase):
    def test_adapter_imports_the_acquisition_lane(self):
        self.assertTrue(hasattr(adapter, "acquisition_ops"))
        self.assertTrue(hasattr(adapter, "acquisition_route"))
        self.assertTrue(hasattr(adapter.Handler, "_handle_acquisition_button"))

    def test_aq_custom_ids_route_to_the_acquisition_lane(self):
        for action in (card.ACT_APPROVE, card.ACT_ACQUIRE, card.ACT_RETRY,
                       card.ACT_DENY, card.ACT_HOLD):
            cid = card.make_custom_id(action, CID)
            self.assertEqual(adapter.acquisition_route(cid), {"action": action, "ref": CID})

    def test_other_custom_ids_are_untouched_by_the_new_route(self):
        for cid in ("mi:submit:abc", "sa_" + "d" * 64, "not-a-custom-id", "", "AQ:zz:x"):
            self.assertIsNone(adapter.acquisition_route(cid))

    def test_health_surface_reports_the_lane(self):
        health = adapter.acquisition_health()
        self.assertEqual(health["route"], "AQ")
        self.assertTrue(health["route_active"])
        self.assertIn(health["live_runner"], ("enabled", "disabled"))
        self.assertEqual(health["bounds"]["max_results"], 1)
        self.assertEqual(health["bounds"]["max_attempts"], 1)
        self.assertFalse(health["bounds"]["fallback"])


class TestFailClosedDefaults(unittest.TestCase):
    def test_module_initialises_the_store_global_at_import(self):
        """Regression: the store global must exist at import time.

        A test that assigns ``adapter._ACQ_STORE`` in setUp would mask a missing
        module-level initialisation, which then only fails in the live process.
        """
        self.assertTrue(hasattr(adapter, "_ACQ_STORE"),
                        "adapter must declare _ACQ_STORE at module level")

    def test_health_degrades_instead_of_raising(self):
        with unittest.mock.patch.object(adapter, "_acquisition_store",
                                        side_effect=RuntimeError("boom")):
            health = adapter.acquisition_health()
        self.assertFalse(health["route_active"])
        self.assertEqual(health["route"], "AQ")
        self.assertEqual(health["error"], "RuntimeError")

    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in
                       ("LOOM_ACQUISITION_LIVE", "LOOM_ACQUISITION_DURABLE_STORE",
                        "LOOM_APIFY_API_TOKEN")}
        for k in self._saved:
            os.environ.pop(k, None)
        adapter._ACQ_STORE = None

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        adapter._ACQ_STORE = None

    def test_live_runner_is_absent_by_default(self):
        self.assertIsNone(adapter._acquisition_live_runner())

    def test_live_switch_alone_does_not_build_a_runner(self):
        os.environ["LOOM_ACQUISITION_LIVE"] = "1"
        self.assertIsNone(adapter._acquisition_live_runner())   # no APIFY token

    def test_store_defaults_to_memory_unless_durable_is_enabled(self):
        self.assertIsInstance(adapter._acquisition_store(), ops.MemoryLaneStore)
        self.assertEqual(adapter.acquisition_health()["store"], "memory")

    def test_health_reports_live_disabled(self):
        self.assertEqual(adapter.acquisition_health()["live_runner"], "disabled")


if __name__ == "__main__":
    unittest.main()
