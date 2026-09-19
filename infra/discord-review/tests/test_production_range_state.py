"""Production-shaped range-state resolution + the exact rendered click payload.

Row set mirrors production: an ACQUIRED 30-60 asset, a historical 60-90 job, and an
active 65-95 candidate. The rendered Approve & Acquire payload must be ACCEPTED by
the real guard, and only 65-95 may reach the runner.

Run:  python3 -m unittest discover -s infra/discord-review/tests -v
"""

import json
import os
import sys
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
for d in ("acquisition", "discord-review"):
    sys.path.insert(0, str(_REPO / "infra" / d))

import acquisition_card as card  # noqa: E402
import acquisition_ops as ops  # noqa: E402
import approved_lane as lane  # noqa: E402

VID = "ClhBXVygWbc"
URL = f"https://www.youtube.com/watch?v={VID}"
ACTIVE_FP = "fad6787e957e17c4c7730f0e0a1b2c3d"
OLD_FP = "929e4e72cabf4268f2c2e29364da36be393f259d7b1a19b86f4ddba80c3bd003"


class ProductionStore:
    """The exact production shape: stale ranges present, active range adjusted."""

    def __init__(self, *, active_fp=ACTIVE_FP, gate1_status="failed"):
        self.jobs = [
            # real rows carry the platform: planning validates it
            {"id": "j-3060-auth", "platform": "youtube", "authorization_type": "acquire_for_reaction",
             "status": "acquired", "requested_start_s": 30.0, "requested_end_s": 60.0,
             "platform_content_id": VID, "duplicate_fingerprint": VID},
            {"id": "j-6090-hist", "platform": "youtube", "authorization_type": "acquire_for_reaction",
             "status": "failed", "requested_start_s": 60.0, "requested_end_s": 90.0,
             "platform_content_id": VID, "duplicate_fingerprint": VID},
            {"id": "j-6595-g1", "platform": "youtube", "authorization_type": "approved_for_acquisition",
             "status": gate1_status, "requested_start_s": 65.0, "requested_end_s": 95.0,
             "platform_content_id": VID, "duplicate_fingerprint": VID},
        ]
        self.library = [{"platform_content_id": VID, "asset_filename":
                         "youtube__ClhBXVygWbc__30-60__3a891cb35f99.mp4",
                         "asset_hash": "3a891cb35f99ac01a1fc199d505fc1505b0abe4170450259a0b593027723c86d",
                         "duplicate_fingerprint": "929e4e72cabf4268f2c2e29364da36be393f259d7b1a19b86f4ddba80c3bd003"}]
        self.created = []

    # ---- LaneStore surface used by context_for / _do_approve / _do_acquire ----
    def jobs_for(self, ref):
        return list(self.jobs)

    def pending_job(self, ref):
        return next((j for j in self.jobs
                     if j["authorization_type"] == "approved_for_acquisition"), None)

    def authorized_job(self, ref):
        return next((j for j in self.jobs
                     if j["authorization_type"] == "acquire_for_reaction"), None)

    def library_rows(self):
        return list(self.library)

    def source_context(self, ref):
        import source_context as sc
        rows = [{"platform": "youtube", "source_url": URL,
                 "duplicate_fingerprint": "f" * 64,
                 "reviewer_note": json.dumps(
                     {"window": {"start_ms": 65_000, "end_ms": 95_000,
                                 "clip_duration_ms": 30_000,
                                 "origin": "operator_adjusted",
                                 "range_fingerprint": ACTIVE_FP},
                      "active_origin": "operator_adjusted"}),
                 "time_start_s": 65.0, "time_end_s": 95.0}]
        r = sc.resolve_context(ref, get=lambda _p: rows)
        return r.as_dict() if r else None

    def create_job(self, job):
        if any(j.get("dedup_key") == job.get("dedup_key") for j in self.jobs):
            existing = next(j for j in self.jobs if j.get("dedup_key") == job.get("dedup_key"))
            return existing, False
        row = dict(job, id=f"new-{len(self.created)}")
        self.created.append(row)
        self.jobs.append(row)
        return row, True

    def update_job(self, job_id, patch):
        for j in self.jobs:
            if str(j.get("id")) == str(job_id):
                j.update(patch)
                return j
        return {}

    def ledger_get(self):
        return {}

    def ledger_put(self, **fields):
        return None

    def record_decision(self, **kw):
        return {}, False


def ctx_for(store):
    return ops.context_for({"ref": VID}, store=store)


class TestProductionRangeState(unittest.TestCase):
    def test_old_ranges_do_not_decide_the_active_state(self):
        store = ProductionStore()
        ctx = ctx_for(store)
        self.assertEqual(ctx.state, card.STATE_REVIEW,
                         "an acquired 30-60 asset must not mark 65-95 as acquired")
        self.assertIn(card.ACT_APPROVE_ACQUIRE, card.actions_for_state(ctx.state))
        self.assertEqual(card.guard_action(ctx.state, card.ACT_APPROVE_ACQUIRE, ref=VID),
                         card.GUARD_OK)

    def test_a_live_gate_one_reads_as_approved(self):
        store = ProductionStore(gate1_status="queued")
        ctx = ctx_for(store)
        self.assertEqual(ctx.state, card.STATE_APPROVED)
        self.assertEqual(card.guard_action(ctx.state, card.ACT_APPROVE_ACQUIRE, ref=VID),
                         card.GUARD_OK)

    def test_the_rendered_payload_is_accepted_and_only_the_active_range_runs(self):
        store = ProductionStore()
        ctx = ctx_for(store)
        win = ctx.window
        vm = card.build_view_model(ref=VID, platform="youtube", state=ctx.state,
                                   content_id=VID, canonical_url=URL, window=win,
                                   requires_range=True, one_click_local=True,
                                   local_acquisition=True)
        buttons = [b for r in card.format_acquisition_buttons(vm)
                   for b in r.get("components", [])]
        rendered = [b for b in buttons if b.get("label") == "Approve & Acquire"][0]
        parsed = card.parse_custom_id(rendered["custom_id"])
        self.assertEqual(parsed["action"], card.ACT_APPROVE_ACQUIRE)
        self.assertEqual(parsed["fingerprint"], ACTIVE_FP[:16])
        self.assertEqual(card.guard_action(ctx.state, parsed["action"], ref=parsed["ref"]),
                         card.GUARD_OK, "the displayed action must pass the guard")

        seen = {}

        def runner(**kw):
            job = kw.get("job") or {}
            seen["range"] = (job.get("requested_start_s"), job.get("requested_end_s"))
            seen["mode"] = job.get("acquisition_mode")
            return {"ok": True, "status": "acquired",
                    "library": {"asset_filename": "youtube__" + VID + "__65-95__ok.mp4",
                                "asset_hash": "b" * 64},
                    "job_patch": {"status": "acquired"},
                    "provenance": {"adapter": "local:yt-dlp"}}

        out = ops.one_click_approve_acquire(
            deps=store, ctx=ctx, actor="bryan", transport=ops.RecordingTransport(),
            runner=runner, required_fingerprint=parsed["fingerprint"])
        self.assertTrue(out.get("ok"))
        self.assertEqual(seen.get("range"), (65.0, 95.0),
                         "the runner must receive ONLY the active range")
        self.assertEqual(seen.get("mode"), "bounded_segment")

    def test_stale_payload_still_fails_closed(self):
        store = ProductionStore()
        ctx = ctx_for(store)
        with self.assertRaises(lane.LaneError) as e:
            ops.one_click_approve_acquire(
                deps=store, ctx=ctx, actor="bryan",
                transport=ops.RecordingTransport(),
                runner=lambda **kw: {"ok": True, "job_patch": {}},
                required_fingerprint=OLD_FP[:16])
        self.assertEqual(e.exception.code, "RANGE_STALE")


class TestRealWorkerWithDurableStore(unittest.TestCase):
    """Drive the exact rendered payload through the REAL post-ACK worker."""

    def test_real_worker_accepts_the_rendered_payload_no_download(self):
        sys.path.insert(0, str(_REPO / "infra" / "discord-review"))
        import adapter as ad

        store = ProductionStore()
        outbound = []

        def http_json(method, url, token="", body=None, timeout=None, **kw):
            outbound.append((method, url, body))
            return 200, {}

        ctx = ctx_for(store)
        win = ctx.window
        vm = card.build_view_model(ref=VID, platform="youtube", state=ctx.state,
                                   content_id=VID, canonical_url=URL, window=win,
                                   requires_range=True, one_click_local=True,
                                   local_acquisition=True)
        rendered = [b for r in card.format_acquisition_buttons(vm)
                    for b in r.get("components", [])
                    if b.get("label") == "Approve & Acquire"][0]
        parsed = card.parse_custom_id(rendered["custom_id"])

        ran = {}

        def no_download_runner(**kw):
            job = kw.get("job") or {}
            ran["range"] = (job.get("requested_start_s"), job.get("requested_end_s"))
            # assertion mode: no media is fetched, and no library row is written
            return {"ok": False, "status": "failed",
                    "error": {"code": "NO_DOWNLOAD_ASSERTION",
                              "message": "no-download assertion mode"},
                    "job_patch": {"status": "failed", "retry_count": 1,
                                  "status_reason": "no-download assertion mode"}}

        class Shim:
            def _acquisition_context(self, parsed_, store_):
                return ctx_for(store_)

        interaction = {"type": 3, "application_id": "app", "token": "tok",
                       "channel": {"id": "1"}, "message": {"id": "2"},
                       "member": {"user": {"id": "1", "username": "bryan"}}}
        import unittest.mock as mock
        with mock.patch.object(ad, "_acquisition_store", lambda: store), \
             mock.patch.object(ad, "http_json", side_effect=http_json), \
             mock.patch.object(ad, "_acquisition_live_runner", lambda: no_download_runner):
            ad.Handler._one_click_worker(Shim(), interaction, parsed,
                                         {"channel_id": "1", "message_id": "2",
                                          "application_id": "app",
                                          "interaction_token": "tok",
                                          "bot_token": "bot", "actor": "bryan#1"})

        self.assertEqual(ran.get("range"), (65.0, 95.0),
                         "the real worker must plan the active range only")
        self.assertTrue(any(call[0] in ("PATCH", "POST") for call in outbound),
                        "card/notes must travel as independent outbound requests")
        self.assertFalse(any(r.get("asset_filename") for r in store.library
                             if "65-95" in str(r.get("asset_filename"))),
                         "assertion mode must not create a library row")


if __name__ == "__main__":
    unittest.main()
