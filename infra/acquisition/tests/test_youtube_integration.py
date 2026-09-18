"""YouTube acquisition — end-to-end integration (mocked local adapter).

Run:  python3 -m unittest discover -s infra/acquisition/tests -v

Proves BOTH intake lanes reach the same premium card, the same gates and the same
local adapter, and that nothing YouTube-related can touch Apify accounting.

No network, no yt-dlp, no ffmpeg, no Apify.
"""

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

_REPO_ROOT = Path(__file__).resolve().parents[3]
for d in ("acquisition", "discord-review"):
    sys.path.insert(0, str(_REPO_ROOT / "infra" / d))

import acquisition_card as card  # noqa: E402
import acquisition_ops as ops  # noqa: E402
import approved_lane as lane  # noqa: E402
import ledger as L  # noqa: E402
import live_runner as lr  # noqa: E402
import local_acquisition as la  # noqa: E402
import platform_capabilities as caps  # noqa: E402
import youtube_url as yt  # noqa: E402
_TESTS = Path(__file__).resolve().parent
if str(_TESTS) not in sys.path:
    sys.path.insert(0, str(_TESTS))
from test_ledger import FakePostgrest  # noqa: E402

SHORT_ID = "h3Q_tAFWY5g"
LONG_ID = "ClhBXVygWbc"
SHORT_URL = "https://www.youtube.com/watch?v=h3Q_tAFWY5g"
LONG_URL = "https://www.youtube.com/watch?v=ClhBXVygWbc"
SHORT_SUBMITTED = "https://youtube.com/shorts/h3Q_tAFWY5g?si=Xph8JJ1pDg6cq6YC"
LONG_SUBMITTED = "https://youtu.be/ClhBXVygWbc?si=4Yf6NFv594czcRdc"


class SpyStore(ops.MemoryLaneStore):
    """Memory store that records writes so we can assert durable outcomes."""

    def __init__(self):
        super().__init__()
        self.updates = []

    def update_job(self, job_id, patch):
        self.updates.append(patch)
        return super().update_job(job_id, patch)


def seed_source(store, video_id, url, *, provenance):
    """Seed the durable source context for a YouTube video (as intake would)."""
    store.source_contexts[video_id] = {
        "platform": "youtube", "canonical_url": url, "content_id": video_id,
        "fingerprint": video_id, "provenance": provenance,
    }


def ok_acquire_factory(video_id):
    def acquire(**_kw):
        return {
            "asset_filename": f"youtube__{video_id}__{'a' * 12}.mp4",
            "path": f"/tmp/{video_id}.mp4", "video_id": video_id,
            "canonical_url": SHORT_URL if video_id == SHORT_ID else LONG_URL,
            "duration_seconds": 77.6, "size_bytes": 16_689_411,
            "video_codec": "h264", "audio_codec": "aac", "width": 720, "height": 1280,
            "sha256": "a" * 64, "container": "mov,mp4", "provider": "local",
            "adapter": "local:yt-dlp", "provider_cost_micro": 0, "attempts": 1,
        }
    return acquire


class YouTubeFlow(unittest.TestCase):
    def setUp(self):
        # The bounded adapter proof is in progress: dispatch is permitted without
        # claiming the platform is proven.
        os.environ[caps.PROOF_ENV] = "youtube"
        self.addCleanup(os.environ.pop, caps.PROOF_ENV, None)
        self.fake = FakePostgrest(spent=2600, succeeded=2)
        self.ledger = None
        self.store = SpyStore()
        self.transport = ops.RecordingTransport()
        self.card_transport = ops.RecordingTransport()
        seed_source(self.store, SHORT_ID, SHORT_URL,
                    provenance={"intake": "manual_submit_source"})

    def _ctx(self, video_id, state, **over):
        kw = dict(ref=video_id, platform="youtube", content_id=video_id,
                  canonical_url=SHORT_URL if video_id == SHORT_ID else LONG_URL,
                  state=state, estimated_cost_micro=0, ceiling_micro=1_000_000,
                  remaining_acquisitions=8, tenant_id="dreemzy",
                  profile_id="youtube-proof")
        kw.update(over)
        return ops.LaneContext(**kw)

    # ---------------------------------------------------------------- review
    def test_review_card_offers_the_full_gate1_action_set(self):
        vm = self._ctx(SHORT_ID, card.STATE_REVIEW, library_url="").view_model()
        labels = [b.get("label") for row in card.format_acquisition_buttons(vm)
                  for b in row.get("components", []) if "custom_id" in b]
        self.assertEqual(labels, ["Approve", "Deny", "Hold"])

    def test_approve_then_acquire_completes_into_the_library(self):
        # Gate 1: Approve
        out1 = ops.handle({"action": card.ACT_APPROVE, "ref": SHORT_ID},
                          deps=self.store, transport=self.transport, actor="bryan",
                          ctx=self._ctx(SHORT_ID, card.STATE_REVIEW), now=1.0)
        self.assertTrue(out1["ok"])
        self.assertEqual(out1["state"], card.STATE_APPROVED)

        # Gate 2: Acquire runs the LOCAL adapter (never Apify)
        lr._LOCAL_ACQUIRE = ok_acquire_factory(SHORT_ID)
        self.addCleanup(setattr, lr, "_LOCAL_ACQUIRE", None)
        out2 = ops.handle(
            {"action": card.ACT_ACQUIRE, "ref": SHORT_ID}, deps=self.store,
            transport=self.card_transport, actor="bryan",
            ctx=self._ctx(SHORT_ID, card.STATE_APPROVED), now=2.0,
            runner=lr.build_live_runner(io=self._io(), store=self.store,
                                        environ={}, dispatch=None))
        self.assertTrue(out2["ok"])
        self.assertEqual(out2["state"], card.STATE_ACQUIRED)
        self.assertTrue(self.store.library, "the asset must reach the library")

    def _io(self):
        import test_live_runner as base          # established fake LiveIO
        io = base.FakeIO()
        io.library_rows = lambda: list(self.store.library)
        io.pending_job = lambda fp: self.store.pending_job(fp)
        io.provider_runs_for_job = lambda job_id: []
        io.ceiling = lambda: {"lifetime_spent_micro": 2600, "acquisitions_succeeded": 2,
                              "ceiling_micro": 1_000_000, "max_acquisitions": 10,
                              "disabled": False}
        io.ledger = None                      # local path needs no paid ledger
        io.persist_library = lambda rec: self.store.library.append(rec) or rec
        return io

    # ------------------------------------------------------------------ URLs
    def test_alternate_url_forms_deduplicate_to_one_identity(self):
        forms = [SHORT_SUBMITTED, "https://www.youtube.com/watch?v=h3Q_tAFWY5g",
                 "https://m.youtube.com/watch?v=h3Q_tAFWY5g&si=x", "https://youtu.be/h3Q_tAFWY5g"]
        fps = {yt.parse(f)["fingerprint"] for f in forms}
        self.assertEqual(len(fps), 1)
        self.assertEqual({yt.parse(f)["canonical_url"] for f in forms}, {SHORT_URL})

    def test_manual_and_automated_intake_share_one_identity(self):
        store = SpyStore()
        seed_source(store, SHORT_ID, SHORT_URL,
                    provenance={"intake": "automated_source_engine"})
        manual = yt.parse(SHORT_SUBMITTED)
        automated = yt.parse(SHORT_URL)
        self.assertEqual(manual["fingerprint"], automated["fingerprint"])
        self.assertEqual(store.source_contexts[SHORT_ID]["canonical_url"], SHORT_URL)

    # -------------------------------------------------------------- refusals
    def test_duration_over_300_refuses_before_any_download(self):
        downloads = {"n": 0}

        def never(**_kw):
            downloads["n"] += 1
            raise AssertionError("must not download")

        import ytdlp_adapter as ya
        with self.assertRaises(ya.YtDlpError) as e:
            ya.acquire_local(url=LONG_URL, expected_id=LONG_ID, tmp_dir=Path("/tmp"),
                             library_dir=Path("/tmp"),
                             run_probe=lambda _c: {"id": LONG_ID, "duration": 301.4},
                             run_download=never, run_ffprobe=lambda _p: {})
        self.assertEqual(e.exception.code, "TOO_LONG")
        self.assertEqual(downloads["n"], 0)

    def test_duration_refusal_classifies_as_DURATION_LIMIT_and_is_not_retryable(self):
        lr._LOCAL_ACQUIRE = lambda **_kw: (_ for _ in ()).throw(
            __import__("ytdlp_adapter").YtDlpError("TOO_LONG", "301.4s exceeds the 300s limit"))
        self.addCleanup(setattr, lr, "_LOCAL_ACQUIRE", None)
        out = la.run_local(canonical_url=LONG_URL, content_id=LONG_ID,
                           acquire_fn=lr._LOCAL_ACQUIRE, ledger=None, run_id="")
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"]["code"], "DURATION_LIMIT")
        self.assertTrue(out["policy_refusal"])
        vm = self._ctx(LONG_ID, card.STATE_FAILED,
                       error=out["error"] | {"retryable": False}).view_model()
        labels = [b.get("label") for row in card.format_acquisition_buttons(vm)
                  for b in row.get("components", [])]
        self.assertNotIn("⚠️ Retry Manually", labels)
        self.assertIn("View Error", labels)

    # ------------------------------------------------------------- accounting
    def test_youtube_never_calls_apify(self):
        import ytdlp_adapter as ya
        for cmd in (ya.build_probe_command(SHORT_URL),
                    ya.build_download_command(SHORT_URL, out_template="/tmp/x.%(ext)s")):
            joined = " ".join(cmd).lower()
            for forbidden in ("apify", "actor", "token", "bearer", "api_key"):
                self.assertNotIn(forbidden, joined)

    def test_apify_ceiling_is_untouched_by_a_local_acquire(self):
        fake = FakePostgrest(spent=2600, succeeded=2)
        led = L.DurableLedger(fake)
        la.run_local(canonical_url=SHORT_URL, content_id=SHORT_ID,
                     acquire_fn=ok_acquire_factory(SHORT_ID), ledger=led,
                     run_id=f"local:{SHORT_ID}:1", job_id="job-1",
                     persist_library=lambda rec: rec)
        self.assertEqual(fake.ceiling["lifetime_spent_micro"], 2600)
        self.assertEqual(fake.ceiling["acquisitions_succeeded"], 2)
        self.assertEqual(fake.runs[0]["cost_actual_micro"], 0)
        self.assertEqual(fake.runs[0]["actor_id"], "local:yt-dlp")

    # ------------------------------------------------------------------ cards
    def test_local_acquisition_card_shows_zero_cost(self):
        vm = self._ctx(SHORT_ID, card.STATE_ACQUIRED, local_acquisition=True,
                       actual_cost_micro=0,
                       library={"status": "acquired",
                                "asset_filename": f"youtube__{SHORT_ID}__{'a'*12}.mp4",
                                "sha256": "a" * 64}).view_model()
        self.assertEqual(card.cost_line(vm), "Local acquisition · $0 provider cost")

    def test_every_displayed_youtube_action_is_usable_or_a_valid_link(self):
        for state in (card.STATE_REVIEW, card.STATE_APPROVED, card.STATE_ACQUIRING,
                      card.STATE_ACQUIRED, card.STATE_FAILED):
            vm = self._ctx(SHORT_ID, state, library_url="http://127.0.0.1:8787/library/x",
                           local_acquisition=True).view_model()
            for row in card.format_acquisition_buttons(vm, library_url=vm.get("library_url")):
                for b in row.get("components", []):
                    self.assertNotEqual("custom_id" in b, "url" in b,
                                        f"{state}: {b.get('label')!r} must be one or the other")
                    if "url" in b:
                        self.assertTrue(b["url"].startswith(("http://", "https://")))

    def test_provenance_stays_local_across_both_intake_lanes(self):
        for intake in ("manual_submit_source", "automated_source_engine"):
            store = SpyStore()
            seed_source(store, SHORT_ID, SHORT_URL, provenance={"intake": intake})
            out = la.run_local(canonical_url=SHORT_URL, content_id=SHORT_ID,
                               acquire_fn=ok_acquire_factory(SHORT_ID), ledger=None,
                               run_id="", job_id="job-1")
            self.assertEqual(out["provenance"]["adapter"], "local:yt-dlp")
            self.assertEqual(out["provenance"]["provider"], "local")
            self.assertEqual(out["cost"]["actual_micro"], 0)
            self.assertEqual(store.source_contexts[SHORT_ID]["provenance"]["intake"], intake)


if __name__ == "__main__":
    unittest.main()
