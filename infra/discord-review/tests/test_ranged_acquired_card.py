"""Ranged-acquisition card behaviour: ACK/ACQUIRING ordering, link integrity,
truthful metadata, and honest attachment claims.

Run:  python3 -m unittest discover -s infra/discord-review/tests -v

No network, no yt-dlp, no ffmpeg, no Discord.
"""

import sys
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
for d in ("acquisition", "discord-review"):
    sys.path.insert(0, str(_REPO / "infra" / d))

import acquisition_card as card  # noqa: E402
import acquisition_ops as ops  # noqa: E402
import approved_lane as lane  # noqa: E402
import discord_media as dm  # noqa: E402
import source_window as sw  # noqa: E402

VID = "ClhBXVygWbc"
URL = f"https://www.youtube.com/watch?v={VID}"
DIGEST = "3a891cb35f99ac01a1fc199d505fc1505b0abe4170450259a0b593027723c86d"
WIN = {"platform": "youtube", "canonical_url": URL, "content_id": VID,
       "start_ms": 30_000, "end_ms": 60_000, "clip_duration_ms": 30_000,
       "origin": "manual_submit_source", "mode": "bounded_segment",
       "range_fingerprint": "f" * 64}


def acquired_vm(**over):
    kw = dict(ref=VID, platform="youtube", state=card.STATE_ACQUIRED, content_id=VID,
              canonical_url=URL, duration_seconds=30.0, size_bytes=2_489_452,
              width=640, height=480, video_codec="h264", audio_codec="aac",
              has_audio=True, actual_cost_micro=0, ceiling_micro=1_000_000,
              remaining_acquisitions=8, window=WIN, requires_range=True,
              local_acquisition=True,
              library={"status": "acquired",
                       "asset_filename": f"youtube__{VID}__30-60__3a891cb35f99.mp4",
                       "sha256": DIGEST},
              preview={"kind": "video_attachment",
                       "note": "Video attached · playable in the message"},
              provenance={"adapter": "local:yt-dlp", "actor": "bryan#1", "run_id": "r1"})
    kw.update(over)
    return card.build_view_model(**kw)


def buttons(vm):
    return [b for row in card.format_acquisition_buttons(vm, library_url=vm.get("library_url"))
            for b in row.get("components", [])]


class FakeStore:
    def __init__(self, pending):
        self._pending = pending
        self.jobs = {}
        self.ledger = {}

    def pending_job(self, _ref):
        return self._pending

    def create_job(self, job):
        key = job.get("dedup_key") or "job-1"
        if key in self.jobs:
            return self.jobs[key], False
        self.jobs[key] = dict(job, id="job-1")
        return self.jobs[key], True

    def update_job(self, job_id, patch):
        for job in self.jobs.values():
            if str(job.get("id")) == str(job_id):
                job.update(patch)
                return job
        return {}

    def library_rows(self):
        return []

    def ledger_put(self, **fields):
        self.ledger.update(fields)


class TestAcquiringBeforeWork(unittest.TestCase):
    def _ctx(self):
        return ops.LaneContext(ref=VID, platform="youtube", content_id=VID,
                               canonical_url=URL, state=card.STATE_FAILED,
                               window=WIN, requires_range=True, local_acquisition=True,
                               ceiling_micro=1_000_000, remaining_acquisitions=8,
                               tenant_id="dreemzy", provenance={"actor": "bryan#1"})

    def _pending(self):
        return lane.plan_pending_job(fingerprint=VID, platform="youtube",
                                     platform_content_id=VID, canonical_url=URL,
                                     actor="bryan", requested_start_s=30.0,
                                     requested_end_s=60.0)

    def test_acquiring_is_shown_before_the_runner_starts(self):
        transport = ops.RecordingTransport()
        store = FakeStore(self._pending())
        seen = {}

        def runner(**_kw):
            seen["updates_when_run"] = list(transport.updates)
            return {"ok": True, "status": "acquired", "local": True,
                    "library": {"asset_filename": "x.mp4", "asset_hash": DIGEST},
                    "job_patch": {"status": "acquired"},
                    "provenance": {"adapter": "local:yt-dlp"}}

        ops._do_acquire(deps=store, ctx=self._ctx(), actor="bryan#1",
                        action=card.ACT_RETRY, runner=runner, runner_kwargs={},
                        transport=transport)

        self.assertTrue(transport.updates, "the card must be updated before the work")
        first = (transport.updates[0].get("embeds") or [{}])[0]
        self.assertIn("IN PROGRESS", first.get("title", ""),
                      "the FIRST pre-work update must show the acquiring state")
        self.assertTrue(seen.get("updates_when_run"),
                        "ACQUIRING must already be visible when the runner starts")
        early_title = (seen["updates_when_run"][0].get("embeds") or [{}])[0].get("title", "")
        self.assertIn("IN PROGRESS", early_title,
                      "the acquiring state must already be visible when the work starts")

    def test_acquiring_payload_shows_the_window_and_locks_actions(self):
        transport = ops.RecordingTransport()
        store = FakeStore(self._pending())

        def runner(**_kw):
            return {"ok": False, "error": {"code": "NO_RESULTS", "message": "x"},
                    "job_patch": {}}

        ops._do_acquire(deps=store, ctx=self._ctx(), actor="bryan#1",
                        action=card.ACT_RETRY, runner=runner, runner_kwargs={},
                        transport=transport)
        payload = transport.updates[0]
        embed = (payload.get("embeds") or [{}])[0]
        fields = {f["name"]: f["value"] for f in embed.get("fields", [])}
        self.assertIn("00:30–01:00", fields.get("Window", ""),
                      "the approved interval must be visible while acquiring")
        for row in payload.get("components", []):
            for b in row.get("components", []):
                if "custom_id" in b:
                    self.assertTrue(b.get("disabled"),
                                    f"{b.get('label')} must be locked while acquiring")


class TestAcquiredCard(unittest.TestCase):
    def test_library_link_is_resolved_from_the_real_hash(self):
        vm = acquired_vm(library_url=f"http://127.0.0.1:8787/library/{DIGEST}")
        links = [b for b in buttons(vm) if b.get("url")]
        self.assertTrue(links, "an acquired ranged asset must expose its library link")
        self.assertEqual(links[0]["url"], f"http://127.0.0.1:8787/library/{DIGEST}")
        self.assertNotIn("custom_id", links[0])

    def test_media_facts_replace_unknown(self):
        payload = card.format_acquisition_card(acquired_vm())
        fields = {f["name"]: f["value"] for f in payload["embeds"][0].get("fields", [])}
        media = fields.get("Media", "")
        self.assertTrue(media, "the acquired card must report real media facts")
        for expected in ("0:30", "640×480", "h264".upper() if False else "2.4 MB", "AAC"):
            self.assertIn(expected, media)
        self.assertNotIn("unknown", media.lower())

    def test_link_buttons_never_carry_a_custom_id(self):
        for state in (card.STATE_REVIEW, card.STATE_APPROVED, card.STATE_ACQUIRING,
                      card.STATE_ACQUIRED, card.STATE_FAILED):
            vm = acquired_vm(state=state, library_url=f"http://127.0.0.1:8787/library/{DIGEST}")
            for b in buttons(vm):
                if "custom_id" in b:
                    self.assertNotIn("url", b, f"{state}: an action button must not be a link")
                else:
                    self.assertIn("url", b, f"{state}: {b.get('label')} must be a link")

    def test_missing_library_route_never_claims_success_or_playback(self):
        vm = acquired_vm(library={"status": "acquired"}, library_url="",
                         preview={"kind": "none"})
        payload = card.format_acquisition_card(vm)
        embed = payload["embeds"][0]
        text = " ".join([embed.get("description") or ""]
                        + [f"{f['name']}: {f['value']}" for f in embed.get("fields", [])]).lower()
        for claim in ("playable", "player", "decoded"):
            self.assertNotIn(claim, text,
                             "without a resolvable route the card must not claim playback")
        for b in buttons(vm):
            self.assertNotIn("url", b, "no link may be offered when none resolves")


class TestAttachmentTruthfulness(unittest.TestCase):
    def test_fitting_media_claims_native_playback_only_when_attached(self):
        limit = dm.upload_limit_for_tier(0)
        fits = 2_489_452 <= limit
        self.assertTrue(fits, "this clip is expected to fit the tier-0 limit")
        vm = acquired_vm(preview={"kind": "video_attachment",
                                  "note": "Video attached · playable in the message"})
        text = " ".join(f"{f['name']}: {f['value']}"
                        for f in card.format_acquisition_card(vm)["embeds"][0]["fields"])
        self.assertIn("playable", text.lower())

    def test_oversize_media_states_the_exact_reason_and_claims_no_playback(self):
        limit = dm.upload_limit_for_tier(0)
        reason = dm.oversize_reason(limit + 1, limit, 0)
        self.assertIn("exceeds", reason.lower())
        vm = acquired_vm(preview={"kind": "poster_fallback", "attach_reason": reason})
        payload = card.format_acquisition_card(vm)
        text = " ".join(f"{f['name']}: {f['value']}" for f in payload["embeds"][0]["fields"])
        self.assertIn("exceeds", text.lower(), "the exact size-limit reason must be shown")
        self.assertNotIn("playable in this message", text.lower(),
                         "a poster fallback must not claim native playback")

    def test_upload_limits_are_tier_based(self):
        self.assertLess(dm.upload_limit_for_tier(0), dm.upload_limit_for_tier(2))


if __name__ == "__main__":
    unittest.main()
