"""Deterministic tests for the approved-lane operator surface (Discord glue).

Run:  python3 -m unittest discover -s infra/discord-review/tests -v

No Discord, no Supabase, no provider. Proves the interaction behaviour Discord
requires: acknowledge FIRST (inside the ~3 s deadline), update the original card
in place, keep the two human gates separate, block duplicate clicks and repeat
acquisitions, and never leak a credential into a payload.
"""

import json
import sys
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
for d in ("acquisition", "discord-review"):
    sys.path.insert(0, str(_REPO_ROOT / "infra" / d))

import acquisition_card as card  # noqa: E402
import acquisition_ops as ops  # noqa: E402

REEL = "https://www.instagram.com/reel/DdZS1QRhrXE/"
CID = "DdZS1QRhrXE"


class OrderLog:
    """Shared log proving the acknowledgement precedes any store work."""

    def __init__(self) -> None:
        self.events: list[str] = []


class SpyStore(ops.MemoryLaneStore):
    def __init__(self, log: OrderLog) -> None:
        super().__init__()
        self._log = log

    def create_job(self, job):
        self._log.events.append("job:" + job["authorization_type"])
        return super().create_job(job)

    def library_rows(self):
        self._log.events.append("library")
        return super().library_rows()


class SpyTransport(ops.RecordingTransport):
    def __init__(self, log: OrderLog) -> None:
        super().__init__()
        self._log = log

    def ack(self, payload):
        self._log.events.append(f"ack:{payload.get('type')}")
        return super().ack(payload)

    def patch_original(self, payload):
        self._log.events.append("patch")
        return super().patch_original(payload)


def make_ctx(state=card.STATE_REVIEW, **over):
    kw = dict(ref=CID, platform="instagram", content_id=CID, canonical_url=REEL,
              state=state, caption="A vertical Reel.",
              estimated_cost_micro=2600, ceiling_micro=1_000_000,
              remaining_acquisitions=9, tenant_id="dreemzy",
              profile_id="acquisition-lane-proof",
              provenance={"actor": "instagram-reel-scraper", "run_id": "0DPOKk2bEwxqVTS0i"})
    kw.update(over)
    return ops.LaneContext(**kw)


def ok_runner(**_kw):
    return {"ok": True, "status": "acquired", "attempts": 1,
            "library": {"asset_filename": "instagram__DdZS1QRhrXE__9b22f887b2ff.mp4",
                        "asset_hash": "9b22f887b2ff6a3b78d91529c1d9c3105497af2a4298ba9241394686239ae096"},
            "cost": {"reserved_micro": 2600, "actual_micro": 2600}}


def fail_runner(**_kw):
    return {"ok": False, "status": "failed", "attempts": 1,
            "error": {"code": "EMPTY_RESULT", "message": "provider returned zero results"}}


def click(action, *, state=card.STATE_REVIEW, store=None, transport=None,
          ctx=None, runner=None, now=1000.0, retry_authorized=None):
    log = OrderLog()
    store = store or SpyStore(log)
    transport = transport or SpyTransport(log)
    ctx = ctx or make_ctx(state)
    out = ops.handle({"action": action, "ref": CID}, deps=store,
                     transport=transport, actor="bryan", ctx=ctx,
                     runner=runner, now=now)
    return out, store, transport, log


class TestAckFirst(unittest.TestCase):
    def test_approve_acknowledges_before_any_store_work(self):
        out, store, transport, log = click(card.ACT_APPROVE)
        self.assertTrue(out["ok"])
        self.assertEqual(log.events[0], "ack:6")          # deferred ACK is FIRST
        self.assertLess(log.events.index("ack:6"),
                        log.events.index("job:approved_for_acquisition"))

    def test_acquire_acknowledges_before_any_store_work(self):
        log = OrderLog()
        store = SpyStore(log)
        store.create_job(ops.lane.plan_pending_job(
            fingerprint=CID, platform="instagram", platform_content_id=CID,
            canonical_url=REEL, actor="bryan"))
        log.events.clear()
        out = ops.handle({"action": card.ACT_ACQUIRE, "ref": CID}, deps=store,
                         transport=SpyTransport(log), actor="bryan",
                         ctx=make_ctx(card.STATE_APPROVED), runner=ok_runner, now=7.0)
        self.assertTrue(out["ok"])
        self.assertEqual(log.events[0], "ack:6")           # deferred ACK is FIRST
        self.assertLess(log.events.index("ack:6"),
                        log.events.index("job:acquire_for_reaction"))

    def test_ack_type_is_deferred_update_for_real_state_changes(self):
        out, store, transport, log = click(card.ACT_HOLD)
        self.assertTrue(out["ok"])
        self.assertEqual(log.events[0], "ack:6")            # ack FIRST, then the write
        self.assertEqual(transport.updates and transport.updates[-1]["components"], [])

    def test_guard_rejection_is_the_only_ephemeral_case(self):
        """Distinction: invalid/duplicate attempts are ephemeral; real decisions are not."""
        _o1, store, _t, _l = click(card.ACT_APPROVE)
        out2, _s, transport2, _l2 = click(card.ACT_APPROVE, store=store, now=1001.0)
        self.assertFalse(out2["ok"])
        self.assertTrue(transport2.ephemerals)               # courtesy message only
        self.assertEqual(transport2.updates, [])             # no state written at all
        self.assertEqual(store.decisions, [])
        self.assertEqual(len(store.jobs), 1)                 # and no duplicate job


class TestGateOneFromTheCard(unittest.TestCase):
    def test_approve_creates_exactly_one_pending_job_and_never_spends(self):
        calls = []

        def spy_runner(**_kw):
            calls.append(1)
            return ok_runner()

        out, store, transport, _l = click(card.ACT_APPROVE, runner=spy_runner)
        self.assertTrue(out["ok"])
        self.assertEqual(out["state"], card.STATE_APPROVED)
        self.assertEqual(calls, [])                                    # approve never spends
        jobs = list(store.jobs.values())
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["authorization_type"], "approved_for_acquisition")

    def test_second_approve_is_idempotent(self):
        out1, store, _t, _l = click(card.ACT_APPROVE)
        # beyond the duplicate-click window: this is a genuine second attempt,
        # not a double click, and it must still yield exactly one job.
        out2, _s2, transport2, _l2 = click(card.ACT_APPROVE, store=store, now=1000.0 + 21)
        self.assertTrue(out2["ok"])
        self.assertEqual(len(store.jobs), 1)
        self.assertFalse(out2["effects"]["created"])

    def test_approve_updates_the_card_in_place(self):
        _out, _s, transport, _l = click(card.ACT_APPROVE)
        self.assertEqual(len(transport.updates), 1)
        embed = transport.updates[-1]["embeds"][0]
        self.assertEqual(embed["title"], card.STATE_TITLES[card.STATE_APPROVED])

    def test_duplicate_click_inside_the_window_is_suppressed(self):
        out1, store, _t, log = click(card.ACT_APPROVE)
        out2, _s, transport2, _l = click(card.ACT_APPROVE, store=store, now=1001.0)
        self.assertFalse(out2["ok"])
        self.assertEqual(out2["guard"], card.GUARD_DUPLICATE)


class TestGateTwoFromTheCard(unittest.TestCase):
    def _approved(self, runner=ok_runner):
        store = ops.MemoryLaneStore()
        store.create_job(ops.lane.plan_pending_job(
            fingerprint=CID, platform="instagram", platform_content_id=CID,
            canonical_url=REEL, actor="bryan"))
        out, _s, transport, _l = click(card.ACT_ACQUIRE, state=card.STATE_APPROVED,
                                       store=store, runner=runner)
        return out, store, transport

    def test_acquire_requires_a_separate_spend_authorization_job(self):
        out, store, transport = self._approved()
        self.assertTrue(out["ok"])
        types = sorted(j["authorization_type"] for j in store.jobs.values())
        self.assertEqual(types, ["acquire_for_reaction", "approved_for_acquisition"])
        self.assertEqual(out["state"], card.STATE_ACQUIRED)

    def test_acquire_updates_the_card_with_library_facts(self):
        _out, _s, transport = self._approved()
        payload = transport.updates[-1]
        blob = json.dumps(payload)
        self.assertIn("Acquired", blob)
        self.assertIn("9b22f887b2ff", blob)
        self.assertEqual(payload["embeds"][0]["title"],
                         card.STATE_TITLES[card.STATE_ACQUIRED])

    def test_acquire_without_approval_is_refused(self):
        out, store, transport, _l = click(card.ACT_ACQUIRE, state=card.STATE_APPROVED,
                                          runner=ok_runner)
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "NOT_APPROVED")
        self.assertEqual(store.jobs, {})
        self.assertTrue(transport.ephemerals)

    def test_failed_run_shows_the_failed_card(self):
        out, _s, transport = self._approved(runner=fail_runner)
        self.assertTrue(out["ok"])
        self.assertEqual(out["state"], card.STATE_FAILED)
        embed = transport.updates[-1]["embeds"][0]
        self.assertEqual(embed["title"], card.STATE_TITLES[card.STATE_FAILED])
        self.assertIn("EMPTY_RESULT", json.dumps(embed))

    def test_retry_requires_the_failed_state_and_is_authorized(self):
        store = ops.MemoryLaneStore()
        pending = ops.lane.plan_pending_job(fingerprint=CID, platform="instagram",
                                            platform_content_id=CID, canonical_url=REEL,
                                            actor="bryan")
        store.create_job(pending)
        auth = ops.lane.plan_authorized_job(pending_job=pending, actor="bryan")
        auth["status"] = "failed"
        store.create_job(auth)
        seen = {}

        def runner(**kw):
            seen.update(kw)
            return ok_runner()

        ctx = ops.context_for({"action": card.ACT_RETRY, "ref": CID}, store=store)
        self.assertEqual(ctx.state, card.STATE_FAILED)
        out = ops.handle({"action": card.ACT_RETRY, "ref": CID}, deps=store,
                         transport=ops.RecordingTransport(), actor="bryan",
                         ctx=ctx, runner=runner, now=5.0)
        self.assertTrue(out["ok"])
        self.assertTrue(seen.get("retry_authorized"))

    def test_repeat_acquisition_of_a_library_item_is_blocked(self):
        store = ops.MemoryLaneStore()
        store.create_job(ops.lane.plan_pending_job(
            fingerprint=CID, platform="instagram", platform_content_id=CID,
            canonical_url=REEL, actor="bryan"))
        store.library.append({"platform_content_id": CID, "duplicate_fingerprint": CID,
                              "asset_filename": "a.mp4", "asset_hash": "f" * 64})
        out, _s, transport, _l = click(card.ACT_ACQUIRE, state=card.STATE_APPROVED,
                                       store=store, runner=ok_runner)
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "ALREADY_ACQUIRED")

    def test_runner_absent_spends_nothing(self):
        store = ops.MemoryLaneStore()
        store.create_job(ops.lane.plan_pending_job(
            fingerprint=CID, platform="instagram", platform_content_id=CID,
            canonical_url=REEL, actor="bryan"))
        out, _s, transport, _l = click(card.ACT_ACQUIRE, state=card.STATE_APPROVED,
                                       store=store, runner=None)
        self.assertFalse(out["ok"])
        self.assertTrue(out["effects"]["runner_missing"])
        types = [j["authorization_type"] for j in store.jobs.values()]
        self.assertNotIn("acquire_for_reaction", types)     # no job authorized at all


class TestDurableDecisions(unittest.TestCase):
    """Hold and Deny are REAL operator decisions and must be durable."""

    def test_hold_is_persisted_durably(self):
        out, store, transport, log = click(card.ACT_HOLD)
        self.assertTrue(out["ok"])
        self.assertEqual(out["effects"]["decision"], "hold")
        self.assertTrue(out["effects"]["created"])
        self.assertEqual(len(store.decisions), 1)
        self.assertEqual(store.decisions[0]["decision"], "hold")
        self.assertEqual(store.decisions[0]["reviewer"], "bryan")
        self.assertEqual(log.events[0], "ack:6")
        self.assertTrue(transport.updates)

    def test_deny_is_persisted_durably(self):
        out, store, _t, _l = click(card.ACT_DENY)
        self.assertTrue(out["ok"])
        self.assertEqual(out["effects"]["decision"], "rejected")
        self.assertEqual(store.decisions[0]["decision"], "rejected")

    def test_repeat_hold_cannot_overwrite_the_first_decision(self):
        _o1, store, _t, _l = click(card.ACT_HOLD)
        out2, _s, transport2, _l2 = click(card.ACT_DENY, store=store, now=1000.0 + 21)
        self.assertTrue(out2["ok"])
        self.assertFalse(out2["effects"]["created"])        # first decision wins
        self.assertEqual(len(store.decisions), 1)
        self.assertEqual(store.decisions[0]["decision"], "hold")
        title = transport2.updates[-1]["embeds"][0]["title"]
        self.assertIn("Already decided", title)

    def test_decisions_are_not_created_by_guarded_clicks(self):
        _o, store, _t, _l = click(card.ACT_ACQUIRE, state=card.STATE_REVIEW)
        self.assertEqual(store.decisions, [])

    def test_deny_ends_the_lane_without_a_job(self):
        out, store, transport, _l = click(card.ACT_DENY)
        self.assertTrue(out["ok"])
        self.assertEqual(store.jobs, {})
        self.assertEqual(transport.updates[0]["components"], [])

class TestViewsAndTerminalActions(unittest.TestCase):
    def test_library_view_is_ephemeral_and_informative(self):
        ctx = make_ctx(card.STATE_ACQUIRED, library={"status": "acquired",
                                                     "asset_filename": "a.mp4",
                                                     "sha256": "9b22f887b2ff" + "0" * 52},
                       size_bytes=23721338)
        out, _s, transport, _l = click(card.ACT_LIBRARY, state=card.STATE_ACQUIRED, ctx=ctx)
        self.assertTrue(out["ok"])
        self.assertIn("a.mp4", transport.ephemerals[0])
        self.assertEqual(transport.updates, [])

    def test_error_view_reports_the_code(self):
        ctx = make_ctx(card.STATE_FAILED, error={"code": "EMPTY_RESULT", "message": "no rows"})
        out, _s, transport, _l = click(card.ACT_ERROR, state=card.STATE_FAILED, ctx=ctx)
        self.assertIn("EMPTY_RESULT", transport.ephemerals[0])

    def test_illegal_action_for_state_is_refused(self):
        out, store, _t, _l = click(card.ACT_ACQUIRE, state=card.STATE_REVIEW)
        self.assertFalse(out["ok"])
        self.assertEqual(out["guard"], card.GUARD_NOT_ALLOWED)
        self.assertEqual(store.jobs, {})


class TestContextAndHygiene(unittest.TestCase):
    def test_context_states_derive_from_the_store(self):
        store = ops.MemoryLaneStore()
        self.assertEqual(ops.context_for({"ref": CID}, store=store).state, card.STATE_REVIEW)

        pending = ops.lane.plan_pending_job(fingerprint=CID, platform="instagram",
                                            platform_content_id=CID,
                                            canonical_url=REEL, actor="bryan")
        store.create_job(pending)
        ctx = ops.context_for({"ref": CID}, store=store)
        self.assertEqual(ctx.state, card.STATE_APPROVED)
        self.assertEqual(ctx.canonical_url, REEL)

        auth = ops.lane.plan_authorized_job(pending_job=pending, actor="bryan")
        auth["status"] = "failed"
        store.create_job(auth)
        self.assertEqual(ops.context_for({"ref": CID}, store=store).state, card.STATE_FAILED)

        # mutate the STORED row (MemoryLaneStore keeps its own copy).
        store.jobs[auth["dedup_key"]]["status"] = "acquired"
        self.assertEqual(ops.context_for({"ref": CID}, store=store).state, card.STATE_ACQUIRED)

    def test_context_marks_a_library_hit_as_acquired(self):
        store = ops.MemoryLaneStore()
        store.library.append({"platform_content_id": CID, "duplicate_fingerprint": CID,
                              "asset_filename": "a.mp4", "asset_hash": "f" * 64})
        ctx = ops.context_for({"ref": CID}, store=store)
        self.assertEqual(ctx.state, card.STATE_ACQUIRED)
        self.assertEqual(ctx.library["asset_filename"], "a.mp4")

    def test_no_credential_reaches_a_payload(self):
        _out, _s, transport, _l = click(card.ACT_APPROVE)
        blob = json.dumps(transport.updates).lower()
        for marker in ("sig=", "bearer", "authorization", "token", "cookie"):
            self.assertNotIn(marker, blob)

    def test_custom_id_round_trip_through_the_adapter_alias(self):
        cid = card.make_custom_id(card.ACT_ACQUIRE, CID)
        self.assertEqual(ops.parse_custom_id(cid), {"action": card.ACT_ACQUIRE, "ref": CID})
        self.assertIsNone(ops.parse_custom_id("mi:submit:abc"))


class TestLiveRunnerBoundary(unittest.TestCase):
    """The operator surface must never reach the paid runner without Gate 2."""

    def test_approve_cannot_reach_the_live_runner(self):
        calls = []

        def live_runner(**_kw):
            calls.append(_kw)
            return ok_runner()

        out, store, _t, _l = click(card.ACT_APPROVE, runner=live_runner)
        self.assertTrue(out["ok"])
        self.assertEqual(calls, [])                     # approve never touches the runner
        self.assertNotIn("acquire_for_reaction",
                         [j["authorization_type"] for j in store.jobs.values()])

    def test_duplicate_acquire_produces_exactly_one_provider_call(self):
        calls = []

        def live_runner(**_kw):
            calls.append(_kw)
            return ok_runner()

        store = ops.MemoryLaneStore()
        store.create_job(ops.lane.plan_pending_job(
            fingerprint=CID, platform="instagram", platform_content_id=CID,
            canonical_url=REEL, actor="bryan"))
        out1, _s, _t, _l = click(card.ACT_ACQUIRE, state=card.STATE_APPROVED,
                                 store=store, runner=live_runner, now=500.0)
        self.assertTrue(out1["ok"])
        # inside the duplicate window -> guard, no second provider call
        out2, _s2, _t2, _l2 = click(card.ACT_ACQUIRE, state=card.STATE_APPROVED,
                                    store=store, runner=live_runner, now=502.0)
        self.assertFalse(out2["ok"])
        self.assertEqual(out2["guard"], card.GUARD_DUPLICATE)
        # outside the window -> already authorized, still no second call
        out3, _s3, _t3, _l3 = click(card.ACT_ACQUIRE, state=card.STATE_APPROVED,
                                    store=store, runner=live_runner, now=900.0)
        # Documented contract: a repeat authorize+acquire continues IDEMPOTENTLY from
        # the durable authorization — never a second provider call, and it must not
        # demand another approval. (The invariant below is what matters.)
        self.assertNotEqual(out3.get("error"), "ALREADY_AUTHORIZED")
        self.assertEqual(len(calls), 1)                 # exactly one provider call

    def test_health_reports_lane_readiness_without_secrets(self):
        h = ops.acquisition_health(store=ops.MemoryLaneStore(), environ={})
        self.assertEqual(h["route"], "AQ")
        self.assertTrue(h["route_active"])
        self.assertEqual(h["store"], "memory")
        self.assertEqual(h["live_runner"], "disabled")
        self.assertFalse(h["apify_token_present"])
        self.assertEqual(h["bounds"]["max_results"], 1)
        self.assertEqual(h["bounds"]["max_attempts"], 1)
        self.assertFalse(h["bounds"]["fallback"])
        self.assertNotIn("token", " ".join(str(v) for v in h.values()).lower()
                         .replace("apify_token_present", ""))

    def test_health_reports_durable_store_and_live_switch(self):
        h = ops.acquisition_health(store=ops.SupabaseLaneStore("https://x.invalid", "k"),
                                   environ={"LOOM_ACQUISITION_LIVE": "1",
                                            "LOOM_ACQUISITION_DURABLE_STORE": "1",
                                            "LOOM_APIFY_API_TOKEN": "***"})
        self.assertEqual(h["store"], "durable")
        self.assertEqual(h["live_runner"], "enabled")
        self.assertTrue(h["apify_token_present"])

    def test_live_runner_factory_fails_closed(self):
        built = []

        def builder(**kw):
            built.append(kw)
            return "RUNNER"

        base = dict(store=ops.MemoryLaneStore(), url="u", key="k",
                    library_dir="/lib", tmp_dir="/tmp", builder=builder)
        # switch off
        self.assertIsNone(ops.build_live_runner_from_env(
            environ={"LOOM_APIFY_API_TOKEN": "***"}, **base))
        # switch on, token absent
        self.assertIsNone(ops.build_live_runner_from_env(
            environ={"LOOM_ACQUISITION_LIVE": "1"}, **base))
        # switch on, token present, no Supabase access
        self.assertIsNone(ops.build_live_runner_from_env(
            environ={"LOOM_ACQUISITION_LIVE": "1", "LOOM_APIFY_API_TOKEN": "***"},
            store=ops.MemoryLaneStore(), url="", key="", library_dir="/lib",
            tmp_dir="/tmp", builder=builder))
        self.assertEqual(built, [])                     # never built in a bad state
        # fully configured
        env = {"LOOM_ACQUISITION_LIVE": "1", "LOOM_APIFY_API_TOKEN": "***"}
        self.assertEqual(ops.build_live_runner_from_env(environ=env, **base), "RUNNER")
        self.assertEqual(len(built), 1)


if __name__ == "__main__":
    unittest.main()
