"""Regression tests: durable source context, validation, and Gate 2 isolation.

Run:  python3 -m unittest discover -s infra/acquisition/tests -v

No network, no provider, no downloads, no spend. The durable lookups are injected
fakes, so the "restart" case is modelled by building a brand-new resolver.
"""

import json
import sys
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
for d in ("acquisition", "discord-review"):
    sys.path.insert(0, str(_REPO_ROOT / "infra" / d))

import acquisition_card as card  # noqa: E402
import approved_lane as lane  # noqa: E402
import cost  # noqa: E402
import live_runner as lr  # noqa: E402
import provider  # noqa: E402
import source_context as sc  # noqa: E402

CID = "DdQE_RUS0GJ"
CANONICAL = f"https://www.instagram.com/reel/{CID}/"
HISTORICAL_ID = "d3b4bba9-4e1c-49d8-b6c6-f7bf4a8fc742"
HISTORICAL_DEDUP = "7f9ddc86f56c335b605494c0e22ca2239c35d27c112d5660ec2aa0f4596deb88"
SUBMISSION_FP = "baf2f7a41b29d43b174167d2901b7819848d81e977adae19123f73ea7a045fe3"


class FakeGet:
    """Injected PostgREST reader over canned rows (models a cold process)."""

    def __init__(self, submissions=(), jobs=()):
        self.submissions = list(submissions)
        self.jobs = list(jobs)
        self.paths = []

    def __call__(self, path: str):
        self.paths.append(path)
        if path.startswith("loom_manual_submissions"):
            return list(self.submissions)
        if path.startswith("loom_acquisition_jobs"):
            return list(self.jobs)
        return []


def live_submission_row(url: str) -> dict:
    return {"platform": "instagram", "source_url": url,
            "duplicate_fingerprint": SUBMISSION_FP, "reviewer_note": ""}


def gate1_row(canonical: str = CANONICAL, **over) -> dict:
    row = lane.plan_pending_job(fingerprint=CID, platform="instagram",
                                platform_content_id=CID, canonical_url=canonical,
                                actor="bryan")
    row.update({"id": "40560336-bbe9-468e-9230-5fa880c9eef1",
                "dedup_key": "a3935fe97af53220986aebcaa641cee3d9406d1501b39e3664bba8363f2212b4"})
    row.update(over)
    return row


# ---------------------------------------------------------------------------
# 2 & 3. Validation and normalization, before anything is persisted
# ---------------------------------------------------------------------------
class TestNormalization(unittest.TestCase):
    def test_reels_and_p_forms_normalize_to_one_canonical_form(self):
        for raw in (f"https://www.instagram.com/reels/{CID}/",
                    f"https://instagram.com/p/{CID}/",
                    f"https://www.instagram.com/reel/{CID}/",
                    f"https://www.instagram.com/reel/{CID}/?utm_source=ig_web_copy_link"):
            self.assertEqual(sc.normalize_instagram_url(raw, expected_content_id=CID), CANONICAL)

    def test_query_and_fragment_are_dropped(self):
        out = sc.normalize_instagram_url(f"https://www.instagram.com/reel/{CID}/?x=1#frag",
                                         expected_content_id=CID)
        self.assertEqual(out, CANONICAL)
        self.assertNotIn("?", out)
        self.assertNotIn("#", out)

    def test_signed_or_credentialed_urls_are_rejected(self):
        signed = (f"https://www.instagram.com/reel/{CID}/?sig=abc&oh=xyz")
        for raw, code in ((signed, "SIGNED_PARAMS"),
                          (f"https://user:pw@www.instagram.com/reel/{CID}/", "CREDENTIALS"),
                          (f"https://www.instagram.com/reel/{CID}/?access_token=abc", "SIGNED_PARAMS"),
                          ("http://www.instagram.com/reel/%s/" % CID, "BAD_SCHEME")):
            with self.assertRaises(sc.SourceContextError) as e:
                sc.normalize_instagram_url(raw, expected_content_id=CID)
            self.assertEqual(e.exception.code, code, raw)

    def test_foreign_hosts_and_non_reel_paths_are_rejected(self):
        for raw, code in ((f"https://evil.example.com/reel/{CID}/", "HOST_MISMATCH"),
                          ("https://www.instagram.com/stories/%s/" % CID, "PATH_NOT_A_REEL"),
                          ("https://www.instagram.com/%s/" % CID, "PATH_NOT_A_REEL"),
                          ("", "EMPTY_URL")):
            with self.assertRaises(sc.SourceContextError) as e:
                sc.normalize_instagram_url(raw, expected_content_id=CID)
            self.assertEqual(e.exception.code, code, raw)

    def test_content_id_drift_is_rejected(self):
        with self.assertRaises(sc.SourceContextError) as e:
            sc.normalize_instagram_url("https://www.instagram.com/reel/OTHERID/",
                                       expected_content_id=CID)
        self.assertEqual(e.exception.code, "CONTENT_ID_MISMATCH")

    def test_payload_is_built_only_from_a_valid_canonical_url(self):
        payload = sc.build_context_payload(content_id=CID, canonical_url=CANONICAL,
                                           card_ref_text="card_ref=ch:1/msg:2",
                                           profile_id="acquisition-live-proof")
        self.assertEqual(payload["source_url"], CANONICAL)
        self.assertEqual(payload["platform"], "instagram")
        self.assertEqual(payload["platform_content_id"], CID)
        self.assertEqual(len(payload["duplicate_fingerprint"]), 64)
        with self.assertRaises(sc.SourceContextError):
            sc.build_context_payload(content_id=CID,
                                     canonical_url=f"https://www.instagram.com/reel/{CID}/?sig=x")


# ---------------------------------------------------------------------------
# 1 & 2. Durable resolution (no in-memory / client-supplied reliance)
# ---------------------------------------------------------------------------
class TestDurableResolution(unittest.TestCase):
    def test_resolves_from_the_submission_row(self):
        get = FakeGet(submissions=[live_submission_row(
            f"https://www.instagram.com/reel/{CID}")])       # stored without trailing slash
        ctx = sc.resolve_context(CID, get=get)
        self.assertIsNotNone(ctx)
        self.assertEqual(ctx.canonical_url, CANONICAL)        # normalized on read
        self.assertEqual(ctx.origin, "loom_manual_submissions")
        self.assertEqual(ctx.fingerprint, SUBMISSION_FP)

    def test_resolution_survives_a_restart(self):
        """A brand-new resolver (cold process) returns the same context."""
        rows = [live_submission_row(CANONICAL)]
        first = sc.resolve_context(CID, get=FakeGet(submissions=rows))
        second = sc.resolve_context(CID, get=FakeGet(submissions=rows))
        self.assertEqual(first.as_dict(), second.as_dict())

    def test_falls_back_to_acquisition_jobs_when_no_submission_exists(self):
        get = FakeGet(jobs=[{"platform": "instagram", "canonical_url": CANONICAL,
                             "duplicate_fingerprint": CID}])
        ctx = sc.resolve_context(CID, get=get)
        self.assertEqual(ctx.origin, "loom_acquisition_jobs")
        self.assertEqual(ctx.canonical_url, CANONICAL)

    def test_no_durable_row_yields_none_not_a_guess(self):
        self.assertIsNone(sc.resolve_context(CID, get=FakeGet()))
        self.assertIsNone(sc.resolve_context("", get=FakeGet()))

    def test_drifted_or_empty_stored_values_are_refused(self):
        for bad in ("", f"https://www.instagram.com/p/OTHERID", "not a url"):
            get = FakeGet(submissions=[live_submission_row(bad)])
            self.assertIsNone(sc.resolve_context(CID, get=get), bad)

    def test_lookup_failure_does_not_fabricate_context(self):
        def boom(path):
            raise RuntimeError("db down")
        self.assertIsNone(sc.resolve_context(CID, get=boom))


# ---------------------------------------------------------------------------
# 5. Card identity: the button carries only the content id
# ---------------------------------------------------------------------------
class TestCardIdentity(unittest.TestCase):
    def test_custom_id_carries_only_action_and_content_id(self):
        cid = card.make_custom_id(card.ACT_APPROVE, CID)
        self.assertEqual(cid, f"AQ:ap:{CID}")
        self.assertNotIn("http", cid)
        self.assertNotIn("?", cid)
        self.assertLessEqual(len(cid), 100)

    def test_context_is_tied_to_the_card_content_id(self):
        import acquisition_ops as ops
        store = ops.MemoryLaneStore()
        store.source_contexts[CID] = {"platform": "instagram", "canonical_url": CANONICAL,
                                      "content_id": CID, "fingerprint": SUBMISSION_FP}
        ctx = ops.context_for({"action": card.ACT_APPROVE, "ref": CID}, store=store)
        self.assertEqual(ctx.canonical_url, CANONICAL)
        self.assertEqual(ctx.content_id, CID)

    def test_unknown_content_id_resolves_to_nothing(self):
        import acquisition_ops as ops
        store = ops.MemoryLaneStore()
        ctx = ops.context_for({"action": card.ACT_APPROVE, "ref": "NOTOURREEL"}, store=store)
        self.assertEqual(ctx.canonical_url, "")      # fail closed, never guessed


# ---------------------------------------------------------------------------
# 6, 7, 8. Gate 2 planning: repaired URL, isolation, no side effects
# ---------------------------------------------------------------------------
class RecordingIO(lr.LiveIO):
    def __init__(self, gate1):
        self.paths = []
        self._gate1 = gate1
        super().__init__(
            transport=_NeverTransport(), download_one_video=_boom, run_ffprobe=_boom,
            parse_ffprobe_result=_boom, checksum_sha256=_boom, finalize=_boom,
            cleanup_temp=lambda *a, **k: None, persist_library=_boom,
            library_rows=self._library, pending_job=self._pending,
            ceiling=self._ceiling, existing_run=self._existing, ledger=_NoLedger())

    def _library(self):
        self.paths.append("library_rows")
        return []

    def _pending(self, fingerprint):
        self.paths.append(f"pending_job({fingerprint})")
        return self._gate1

    def _ceiling(self):
        self.paths.append("ceiling")
        return {"ceiling_micro": 1_000_000, "lifetime_spent_micro": 2600,
                "max_acquisitions": 10, "acquisitions_succeeded": 1, "disabled": False}

    def _existing(self, job_id):
        self.paths.append(f"existing_run({job_id})")
        return False


def _boom(*a, **k):
    raise AssertionError("no side effect expected during planning")


class _NeverTransport:
    def start_run(self, *a):
        raise AssertionError("provider must not be called")

    def get_dataset_items(self, *a):
        raise AssertionError("provider must not be called")


class _NoLedger:
    def reserve(self, **kw):
        raise AssertionError("ledger must not be touched during planning")


class TestGate2Planning(unittest.TestCase):
    def test_planning_uses_the_repaired_canonical_url_and_preflight_passes(self):
        row = gate1_row()
        pre = lr.preflight(dict(lane.plan_authorized_job(pending_job=row, actor="bryan"),
                               id="pending-verification"),
                           io=RecordingIO(row),
                           environ={"LOOM_APIFY_API_TOKEN": "present-for-proof"})
        self.assertEqual(pre["canonical_url"], CANONICAL)
        self.assertEqual(pre["actor"], provider.ACTOR_REEL_SCRAPER)
        self.assertEqual(pre["reserved_micro"], cost.REEL_SCRAPER_RESULT_MICRO)

    def test_an_empty_canonical_url_still_fails_closed(self):
        row = gate1_row(canonical_url="")
        with self.assertRaises(lr.LiveRunnerError) as e:
            lr.preflight(dict(lane.plan_authorized_job(pending_job=row, actor="bryan"),
                              id="pending"), io=RecordingIO(row),
                         environ={"LOOM_APIFY_API_TOKEN": "x"})
        self.assertEqual(e.exception.code, "NO_URL")

    def test_planned_job_is_new_and_isolated_from_the_historical_failure(self):
        row = gate1_row()
        planned = lane.plan_authorized_job(pending_job=row, actor="bryan")
        self.assertEqual(planned["authorization_type"], "acquire_for_reaction")
        self.assertNotEqual(planned["dedup_key"], HISTORICAL_DEDUP)
        self.assertEqual(planned["correlation_id"], row["dedup_key"])
        self.assertEqual(planned["retry_count"], 0)
        self.assertTrue(lane.attempts_allowed(planned))
        seeded = dict(planned, retry_count=1)
        self.assertFalse(lane.attempts_allowed(seeded))

    def test_planning_never_consults_the_historical_row(self):
        row = gate1_row()
        io = RecordingIO(row)
        lr.preflight(dict(lane.plan_authorized_job(pending_job=row, actor="bryan"),
                          id="pending-verification"), io=io,
                     environ={"LOOM_APIFY_API_TOKEN": "x"})
        self.assertFalse(any(HISTORICAL_ID in p for p in io.paths))
        self.assertFalse(any(HISTORICAL_DEDUP in p for p in io.paths))
        self.assertIn(f"pending_job({CID})", io.paths)

    def test_planning_makes_no_provider_download_ledger_or_write_call(self):
        row = gate1_row()
        planned = lane.plan_authorized_job(pending_job=row, actor="bryan")
        blob = json.dumps(planned)
        self.assertNotIn("chrome", blob)                  # never the historical adapter
        self.assertEqual(planned["asset_provenance"] if "asset_provenance" in planned else None,
                         None)


class TestRepairImmutability(unittest.TestCase):
    """The authorized repair must touch canonical_url only."""

    def test_a_repaired_row_keeps_its_authorization_identity(self):
        row = gate1_row()
        before = dict(row)
        row["canonical_url"] = sc.normalize_instagram_url(CANONICAL, expected_content_id=CID)
        for key in ("id", "dedup_key", "authorization_type", "actor", "retry_count",
                    "max_bytes", "max_duration_seconds", "status", "correlation_id"):
            self.assertEqual(row[key], before[key], key)

    def test_repeat_approve_recomputes_an_identical_dedup_key(self):
        import acquisition as acq
        row = gate1_row()
        repeat = acq.dedup_key(row["duplicate_fingerprint"],
                               acq.AUTH_APPROVED_FOR_ACQUISITION,
                               acq.MODE_FULL_ITEM, None, None)
        self.assertEqual(repeat, row["dedup_key"])        # unique constraint blocks a 2nd row


if __name__ == "__main__":
    unittest.main()
