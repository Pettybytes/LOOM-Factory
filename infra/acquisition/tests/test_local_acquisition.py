"""Local acquisition path tests: $0 cost, real audit, ceiling untouched.

Run:  python3 -m unittest discover -s infra/acquisition/tests -v

Mocked only — no yt-dlp, no ffmpeg, no network, no Apify.
"""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
for d in ("acquisition", "discord-review"):
    sys.path.insert(0, str(_REPO_ROOT / "infra" / d))
_TESTS = Path(__file__).resolve().parent
if str(_TESTS) not in sys.path:
    sys.path.insert(0, str(_TESTS))

import ledger as L  # noqa: E402
import local_acquisition as la  # noqa: E402
import ytdlp_adapter as ya  # noqa: E402
from test_ledger import FakePostgrest  # noqa: E402

VID = "dQw4w9WgXcQ"
URL = f"https://www.youtube.com/watch?v={VID}"
JOB = "b1bb78e6-3601-47d9-9068-46afb45587e1"
RUN = f"local:{JOB}:1"


def ok_acquire(**_kw):
    return {
        "asset_filename": ya.filename_for(VID, "a" * 64),
        "path": "/tmp/ignored.mp4",
        "video_id": VID,
        "canonical_url": URL,
        "duration_seconds": 77.6,
        "size_bytes": 16689411,
        "video_codec": "h264",
        "audio_codec": "aac",
        "width": 720,
        "height": 1280,
        "sha256": "a" * 64,
        "container": "mov,mp4",
        "provider": "local",
        "adapter": "local:yt-dlp",
        "provider_cost_micro": 0,
        "attempts": 1,
    }


def failing_acquire(**_kw):
    raise ya.YtDlpError("TOO_LONG", "301.0s exceeds the 300s limit")


class TestLocalLedgerReconcile(unittest.TestCase):
    def test_local_reconcile_audits_without_moving_the_ceiling(self):
        fake = FakePostgrest(spent=2600, succeeded=2)
        led = L.DurableLedger(fake)
        led.record_run(run_id=RUN, actor_id="local:yt-dlp", job_id=JOB, estimate_micro=0,
                       output_ref="local attempt")
        self.assertEqual(fake.ceiling["lifetime_spent_micro"], 2600)

        out = led.reconcile_local(run_id=RUN, succeeded=True, status_reason="done")
        self.assertTrue(out["applied"])
        self.assertFalse(out["ceiling_touched"])
        self.assertEqual(fake.runs[0]["status"], L.STATUS_ACQUIRED)
        self.assertEqual(fake.runs[0]["cost_actual_micro"], 0)
        # the paid-provider ceiling is untouched in both amount and success count
        self.assertEqual(fake.ceiling["lifetime_spent_micro"], 2600)
        self.assertEqual(fake.ceiling["acquisitions_succeeded"], 2)

    def test_local_reconcile_is_idempotent(self):
        fake = FakePostgrest(spent=2600, succeeded=2)
        led = L.DurableLedger(fake)
        led.record_run(run_id=RUN, actor_id="local:yt-dlp", job_id=JOB, estimate_micro=0)
        led.reconcile_local(run_id=RUN, succeeded=True)
        again = led.reconcile_local(run_id=RUN, succeeded=True)
        self.assertFalse(again["applied"])
        self.assertTrue(again["idempotent"])
        self.assertEqual(fake.ceiling["lifetime_spent_micro"], 2600)


class TestRunLocal(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="loom-local-"))
        self.tmp = self.root / "tmp"
        self.lib = self.root / "library"
        for d in (self.tmp, self.lib):
            d.mkdir(parents=True, exist_ok=True)
        self.fake = FakePostgrest(spent=2600, succeeded=2)
        self.ledger = L.DurableLedger(self.fake)
        self.persisted: list = []

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _run(self, acquire_fn):
        return la.run_local(canonical_url=URL, content_id=VID, acquire_fn=acquire_fn,
                            tmp_dir=self.tmp, library_dir=self.lib,
                            persist_library=lambda rec: self.persisted.append(rec) or rec,
                            ledger=self.ledger, run_id=RUN, job_id=JOB)

    def test_success_reports_zero_cost_and_audits_locally(self):
        out = self._run(ok_acquire)
        self.assertTrue(out["ok"])
        self.assertEqual(out["status"], "acquired")
        self.assertEqual(out["cost"]["actual_micro"], 0)
        self.assertEqual(out["cost"]["estimated_micro"], 0)
        self.assertEqual(out["cost"]["label"], la.COST_LABEL)
        self.assertEqual(out["provenance"]["adapter"], "local:yt-dlp")
        self.assertEqual(out["provenance"]["provider"], "local")
        self.assertEqual(out["provenance"]["provider_cost_micro"], 0)
        self.assertEqual(out["attempts"], 1)
        self.assertTrue(self.persisted, "the library row must be persisted")
        self.assertEqual(self.persisted[0]["platform"], "youtube")
        self.assertEqual(self.persisted[0]["platform_content_id"], VID)
        # The library row uses the same contract as the paid path; adapter
        # provenance is carried on the outcome and on the job row.
        row = self.persisted[0]
        self.assertEqual(row.get("platform"), "youtube")
        self.assertEqual(row.get("platform_content_id"), VID)
        self.assertIn(VID, str(row.get("canonical_url", "")))
        self.assertTrue(row.get("duplicate_fingerprint"))
        self.assertEqual(out["provenance"]["adapter"], "local:yt-dlp")
        self.assertEqual(out["job_patch"]["status"], "acquired")
        # audit written, ceiling untouched
        self.assertEqual(self.fake.runs[0]["cost_actual_micro"], 0)
        self.assertEqual(self.fake.ceiling["lifetime_spent_micro"], 2600)
        self.assertEqual(self.fake.ceiling["acquisitions_succeeded"], 2)

    def test_failure_is_classified_and_costs_nothing(self):
        out = self._run(failing_acquire)
        self.assertFalse(out["ok"])
        # stable operator-facing classification (raw extractor code is TOO_LONG)
        self.assertEqual(out["error"]["code"], "DURATION_LIMIT")
        self.assertTrue(out["policy_refusal"])
        self.assertFalse(out["error"]["retryable"])
        self.assertEqual(out["failure_stage"], "local_download")
        self.assertEqual(out["cost"]["actual_micro"], 0)
        self.assertEqual(out["job_patch"]["status"], "failed")
        self.assertEqual(self.persisted, [])
        self.assertEqual(self.fake.runs[0]["status"], L.STATUS_FAILED_KNOWN)
        self.assertEqual(self.fake.runs[0]["cost_actual_micro"], 0)
        self.assertEqual(self.fake.ceiling["lifetime_spent_micro"], 2600)

    def test_toolchain_exception_is_classified_not_raised(self):
        def broken(**_kw):
            raise RuntimeError("yt-dlp binary missing")

        out = self._run(broken)
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"]["code"], "LOCAL_TOOLCHAIN_ERROR")
        self.assertEqual(self.fake.ceiling["lifetime_spent_micro"], 2600)

    def test_no_apify_accounting_is_ever_touched(self):
        self._run(ok_acquire)
        for row in self.fake.runs:
            self.assertEqual(row["actor_id"], "local:yt-dlp")
            self.assertNotIn("apify", str(row["actor_id"]).lower())
        self.assertEqual(self.fake.ceiling["lifetime_spent_micro"], 2600)
        self.assertEqual(self.fake.ceiling["acquisitions_succeeded"], 2)


if __name__ == "__main__":
    unittest.main()
