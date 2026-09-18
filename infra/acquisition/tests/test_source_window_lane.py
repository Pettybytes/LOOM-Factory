"""Long-form range lane: contract, planning, execution, provenance, card.

Run:  python3 -m unittest discover -s infra/acquisition/tests -v

Mocked only — no network, no yt-dlp, no ffmpeg, no Discord, no Apify.
"""

import hashlib
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
for d in ("acquisition", "discord-review"):
    sys.path.insert(0, str(_REPO / "infra" / d))

import acquisition_card as card  # noqa: E402
# A minimal fast-start MP4: moov precedes mdat, as the delivery contract requires.
_FS = b"\x00\x00\x00\x18moov" + b"\x00" * 64 + b"\x00\x00\x00\x18mdat" + b"x" * 4096

import approved_lane as lane  # noqa: E402
import local_acquisition as la  # noqa: E402
import source_window as sw  # noqa: E402
import ytdlp_adapter as ya  # noqa: E402

VID = "ClhBXVygWbc"
URL = f"https://www.youtube.com/watch?v={VID}"
SRC_MS = 302_000
WINDOW = dict(start_ms=30_000, end_ms=60_000)


def window(**over):
    kw = dict(platform="youtube", canonical_url=URL, content_id=VID,
              origin="manual_submit_source", source_duration_s=302.0, **WINDOW)
    kw.update(over)
    return sw.validate_window(**kw)


def probe(*, duration, v="h264", a="aac"):
    return {"streams": [{"codec_type": "video", "codec_name": v, "pix_fmt": "yuv420p",
                         "width": 1080, "height": 1920, "avg_frame_rate": "30/1"},
                        {"codec_type": "audio", "codec_name": a}],
            "format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2", "duration": str(duration)}}


class TestContract(unittest.TestCase):
    def test_valid_window_describes_source_range_and_clip(self):
        w = window()
        self.assertEqual(w["mode"], "bounded_segment")
        self.assertEqual(w["clip_duration_ms"], 30_000)
        self.assertEqual(sw.human_range(w["start_ms"], w["end_ms"]), "00:30–01:00")
        self.assertEqual(sw.human_clock(SRC_MS), "05:02")
        self.assertEqual(sw.human_duration(w["clip_duration_ms"]), "30.0 s")

    def test_rejections_fail_closed_with_stable_codes(self):
        cases = [
            (dict(start_ms=float("nan")), "NON_FINITE"),
            (dict(end_ms=float("inf")), "NON_FINITE"),
            (dict(start_ms=True), "BAD_TIMESTAMP"),
            (dict(end_ms="later"), "BAD_TIMESTAMP"),
            (dict(end_ms=None), "MISSING_TIMESTAMP"),
            (dict(start_ms=-1), "NEGATIVE"),
            (dict(start_ms=60_000, end_ms=30_000), "REVERSED_OR_EMPTY"),
            (dict(start_ms=30_000, end_ms=30_000), "REVERSED_OR_EMPTY"),
            (dict(start_ms=0, end_ms=301_000), "RANGE_TOO_LONG"),
            (dict(start_ms=290_000, end_ms=310_000), "RANGE_BEYOND_SOURCE"),
            (dict(origin="freelance"), "BAD_ORIGIN"),
            (dict(content_id="AAAAAAAAAAA"), "SOURCE_URL_DRIFT"),
        ]
        for over, code in cases:
            with self.assertRaises(sw.WindowError) as e:
                window(**over)
            self.assertEqual(e.exception.code, code, over)

    def test_limits_apply_to_the_clip_not_the_source(self):
        self.assertEqual(window(start_ms=0, end_ms=300_000)["clip_duration_ms"], 300_000)
        with self.assertRaises(sw.WindowError):
            window(start_ms=0, end_ms=300_500)
        with self.assertRaises(sw.WindowError) as e:
            sw.require_full_item_allowed(platform="youtube", content_id=VID,
                                         canonical_url=URL, source_duration_s=302.0)
        self.assertEqual(e.exception.code, "LONG_FORM_REQUIRES_RANGE")
        self.assertEqual(sw.require_full_item_allowed(
            platform="youtube", content_id=VID, canonical_url=URL,
            source_duration_s=40.6)["mode"], "full_item")

    def test_identity_is_range_aware(self):
        a = window()
        b = window(start_ms=30_000, end_ms=60_000)
        c = window(start_ms=60_000, end_ms=90_000)
        self.assertEqual(a["identity"], b["identity"], "same source+range is idempotent")
        self.assertNotEqual(a["identity"], c["identity"],
                            "a different range is a separate valid asset")
        self.assertNotEqual(a["identity"], sw.dedup_identity("youtube", VID, None))
        self.assertEqual(a["identity"], a["range_fingerprint"])

    def test_delivery_helpers(self):
        w = window()
        self.assertEqual(sw.ytdlp_section_arg(w), "*30.000-60.000")
        self.assertEqual(sw.filename_stem("youtube", VID, w, "a" * 64),
                         f"youtube__{VID}__30-60__aaaaaaaaaaaa")
        self.assertEqual(sw.filename_stem("youtube", VID, None, "a" * 64),
                         f"youtube__{VID}__aaaaaaaaaaaa")
        self.assertEqual(sw.parse_clock("1:02:03"), 3_723_000)

    def test_resulting_duration_must_match_the_window(self):
        w = window()
        self.assertTrue(sw.validate_resulting_duration(approved=w, measured_ms=30_400))
        with self.assertRaises(sw.WindowError) as e:
            sw.validate_resulting_duration(approved=w, measured_ms=45_000)
        self.assertEqual(e.exception.code, "CLIP_DURATION_MISMATCH")


class TestPlanning(unittest.TestCase):
    def test_ranged_pending_job_records_the_range_and_mode(self):
        job = lane.plan_pending_job(fingerprint=VID, platform="youtube",
                                    platform_content_id=VID, canonical_url=URL,
                                    actor="bryan", requested_start_s=30.0,
                                    requested_end_s=60.0)
        self.assertEqual(job["acquisition_mode"], "bounded_segment")
        self.assertEqual(job["requested_start_s"], 30.0)
        self.assertEqual(job["requested_end_s"], 60.0)

    def test_full_item_job_stays_full_item_without_a_range(self):
        job = lane.plan_pending_job(fingerprint=VID, platform="youtube",
                                    platform_content_id=VID, canonical_url=URL,
                                    actor="bryan")
        self.assertEqual(job["acquisition_mode"], "full_item")
        self.assertIsNone(job["requested_start_s"])

    def test_authorized_job_inherits_the_range_from_gate_one(self):
        g1 = lane.plan_pending_job(fingerprint=VID, platform="youtube",
                                   platform_content_id=VID, canonical_url=URL,
                                   actor="bryan", requested_start_s=30.0,
                                   requested_end_s=60.0)
        g2 = lane.plan_authorized_job(pending_job=g1, actor="bryan")
        self.assertEqual(g2["acquisition_mode"], "bounded_segment")
        self.assertEqual(g2["requested_start_s"], 30.0)


class TestExecution(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="loom-range-"))
        self.tmp, self.lib = self.root / "tmp", self.root / "library"
        self.tmp.mkdir(); self.lib.mkdir()
        self.w = window()

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _runners(self, *, clip_duration=30.4, download_bytes=b"clip" * 512):
        def run_probe(_c):
            return {"id": VID, "duration": 302.0, "is_live": False}

        def run_download(cmd):
            cmd_seen["cmd"] = list(cmd)
            p = Path(cmd[cmd.index("--output") + 1].replace("%(ext)s", "mp4"))
            p.write_bytes(download_bytes)
            return str(p)
        cmd_seen = {}
        return run_probe, run_download, cmd_seen

    def test_ranged_download_uses_section_flags_and_names_the_range(self):
        run_probe, run_download, cmd_seen = self._runners()
        out = ya.acquire_local(url=URL, expected_id=VID, tmp_dir=self.tmp,
                               library_dir=self.lib, run_probe=run_probe,
                               run_download=run_download,
                               run_ffprobe=lambda _p: probe(duration=30.4),
                               run_ffmpeg=lambda c: Path(c[-1]).write_bytes(_FS),
                               window=self.w)
        cmd = cmd_seen["cmd"]
        self.assertIn("--download-sections", cmd)
        self.assertEqual(cmd[cmd.index("--download-sections") + 1], "*30.000-60.000")
        self.assertIn("--force-keyframes-at-cuts", cmd)
        self.assertTrue(out["asset_filename"].startswith(f"youtube__{VID}__30-60__"))
        self.assertEqual(out["duration_seconds"], 30.4)
        self.assertEqual(list(self.tmp.iterdir()), [], "no temp debris")
        self.assertEqual([p.name for p in self.lib.iterdir()], [out["asset_filename"]])

    def test_window_mismatch_is_refused_and_leaves_the_library_clean(self):
        run_probe, run_download, _ = self._runners()
        with self.assertRaises(sw.WindowError) as e:
            ya.acquire_local(url=URL, expected_id=VID, tmp_dir=self.tmp,
                             library_dir=self.lib, run_probe=run_probe,
                             run_download=run_download,
                             run_ffprobe=lambda _p: probe(duration=45.0),
                             run_ffmpeg=lambda c: Path(c[-1]).write_bytes(_FS),
                             window=self.w)
        self.assertEqual(e.exception.code, "CLIP_DURATION_MISMATCH")
        self.assertEqual(list(self.lib.iterdir()), [])
        self.assertEqual(list(self.tmp.iterdir()), [])

    def test_long_form_source_without_a_range_is_refused_before_download(self):
        def never(_c):
            raise AssertionError("must not download")

        with self.assertRaises(ya.YtDlpError) as e:
            ya.acquire_local(url=URL, expected_id=VID, tmp_dir=self.tmp,
                             library_dir=self.lib,
                             run_probe=lambda _c: {"id": VID, "duration": 302.0},
                             run_download=never, run_ffprobe=lambda _p: {})
        self.assertEqual(e.exception.code, "TOO_LONG")


class TestProvenanceAndCard(unittest.TestCase):
    def test_outcome_records_mode_window_and_zero_cost(self):
        w = window()
        seen = {}

        def fake(**kw):
            seen.update(kw)
            return {"asset_filename": f"youtube__{VID}__30-60__abc.mp4", "path": "/tmp/x.mp4",
                    "video_id": VID, "canonical_url": URL, "duration_seconds": 30.4,
                    "size_bytes": 10, "video_codec": "h264", "audio_codec": "aac",
                    "width": 1080, "height": 1920, "sha256": "a" * 64, "container": "mp4",
                    "provider": "local", "adapter": "local:yt-dlp",
                    "provider_cost_micro": 0, "attempts": 1}

        out = la.run_local(canonical_url=URL, content_id=VID, video_id=VID,
                           acquire_fn=fake, ledger=None, run_id="", window=w)
        self.assertTrue(seen.get("window") is w, "the window must reach the download")
        self.assertEqual(out["provenance"]["mode"], "bounded_segment")
        self.assertEqual(out["provenance"]["window"]["start_ms"], 30_000)
        self.assertEqual(out["provenance"]["window"]["end_ms"], 60_000)
        self.assertEqual(out["cost"]["actual_micro"], 0)
        self.assertEqual(out["provenance"]["provider_cost_micro"], 0)

    def test_card_shows_source_range_clip_and_origin(self):
        vm = card.build_view_model(ref=VID, platform="youtube", state=card.STATE_REVIEW,
                                   content_id=VID, canonical_url=URL, window=window(),
                                   requires_range=True, local_acquisition=True)
        text = card.window_field(vm)
        self.assertIn("05:02", text)
        self.assertIn("00:30–01:00", text)
        self.assertIn("30.0 s", text)
        self.assertIn("manual_submit_source", text)

    def test_acquire_is_not_offered_for_long_form_without_a_window(self):
        vm = card.build_view_model(ref=VID, platform="youtube", state=card.STATE_APPROVED,
                                   content_id=VID, canonical_url=URL, window={},
                                   requires_range=True, local_acquisition=True)
        labels = [b.get("label") for r in card.format_acquisition_buttons(vm)
                  for b in r.get("components", [])]
        self.assertNotIn("Acquire for Reaction", labels)

    def test_acquire_is_offered_once_a_window_is_approved(self):
        vm = card.build_view_model(ref=VID, platform="youtube", state=card.STATE_APPROVED,
                                   content_id=VID, canonical_url=URL, window=window(),
                                   requires_range=True, local_acquisition=True)
        labels = [b.get("label") for r in card.format_acquisition_buttons(vm)
                  for b in r.get("components", [])]
        self.assertIn("Acquire for Reaction", labels)


if __name__ == "__main__":
    unittest.main()
