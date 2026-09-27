"""Targeted tests for Manual Intelligence Intake V1.2 — supplied-media ingress.

No network, no provider, no paid calls: ffmpeg/ffprobe only (local toolchain).
Deterministic; safe to run in CI.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
for _p in (str(_REPO / "infra" / "discord-review"), str(_REPO / "infra"), str(_REPO / "infra" / "acquisition")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import manual_intake  # noqa: E402
import manual_media_intake as mmi  # noqa: E402

_HAS_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def _make_media(path: Path, seconds: float = 6.0, size: str = "320x240") -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi",
                    "-i", f"testsrc=size={size}:rate=24:duration={seconds}",
                    "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-shortest", "-y", str(path)], check=True, timeout=180)


class TestParsing(unittest.TestCase):
    def test_url_forms_and_tenant(self):
        p = mmi.parse_intake_message("url: https://www.tiktok.com/@espn/video/7515185025495633182 "
                                     "start=0:02 end=0:05 tenant=client-a")
        self.assertEqual(p["url"], "https://www.tiktok.com/@espn/video/7515185025495633182")
        self.assertEqual((p["start_s"], p["end_s"]), (2.0, 5.0))
        self.assertEqual(p["tenant"], "client-a")

    def test_bare_url_is_found(self):
        p = mmi.parse_intake_message("look at this https://youtu.be/qgLaCZyKv_8 cool")
        self.assertEqual(p["url"], "https://youtu.be/qgLaCZyKv_8")
        self.assertIsNone(p["start_s"])
        self.assertEqual(p["tenant"], "")

    def test_bare_seconds_timestamps(self):
        p = mmi.parse_intake_message("url: https://twitch.tv/a/clip/x start=83 end=95")
        self.assertEqual((p["start_s"], p["end_s"]), (83.0, 95.0))

    def test_timestamp_forms(self):
        self.assertEqual(mmi.parse_timestamp("83"), 83.0)
        self.assertEqual(mmi.parse_timestamp("1:23"), 83.0)
        self.assertEqual(mmi.parse_timestamp("1:02:03"), 3723.0)
        self.assertEqual(mmi.parse_timestamp("12.5"), 12.5)
        self.assertIsNone(mmi.parse_timestamp(""))
        with self.assertRaises(ValueError):
            mmi.parse_timestamp("not-a-time")

    def test_scan_for_video_prefers_media_and_ignores_others(self):
        atts = [{"filename": "notes.txt", "content_type": "text/plain"},
                {"filename": "clip.mp4", "content_type": "video/mp4", "url": "u"}]
        self.assertEqual(mmi.scan_for_video(atts)["filename"], "clip.mp4")
        self.assertIsNone(mmi.scan_for_video([{"filename": "a.txt", "content_type": "text/plain"}]))
        self.assertIsNone(mmi.scan_for_video([]))


@unittest.skipUnless(_HAS_FFMPEG, "ffmpeg/ffprobe required")
class TestLocalMedia(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="loom-v12-test-"))
        cls.media = cls.tmp / "supplied.mp4"
        _make_media(cls.media)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_probe_reports_real_media_facts(self):
        p = mmi.probe_media(self.media)
        self.assertAlmostEqual(p["duration_seconds"], 6.0, delta=0.7)
        self.assertEqual((p["width"], p["height"]), (320, 240))
        self.assertTrue(p["has_video"] and p["has_audio"])
        self.assertEqual(p["size_bytes"], self.media.stat().st_size)
        self.assertEqual(len(p["sha256"]), 64)

    def test_clip_uses_supplied_bytes(self):
        pv = mmi.clip_supplied_media(str(self.media), start=0.0, end=1.5)
        self.assertTrue(pv.available)
        self.assertGreater(pv.artifact_bytes, 0)
        self.assertEqual(pv.method, "local_supplied_media")
        self.assertIn("no_network", pv.provenance)

    def test_clip_is_bounded_by_cap(self):
        pv = mmi.clip_supplied_media(str(self.media), start=0.0, end=9999.0)
        self.assertTrue(pv.available)
        self.assertLessEqual(pv.requested_end - pv.requested_start,
                             mmi.MAX_LOCAL_CLIP_SECONDS + 0.001)

    def test_fetchers_ignore_the_url_and_use_supplied_media(self):
        bounded, contiguous = mmi.preview_fetchers(str(self.media))
        a = bounded("https://example.invalid/never-fetched", start=0.0, end=1.0)
        b = contiguous("https://example.invalid/never-fetched", start=1.0, end=2.0)
        self.assertTrue(a.available and b.available)
        self.assertEqual(a.provider, "manual_media")

    def test_missing_media_yields_explicit_unavailable(self):
        pv = mmi.clip_supplied_media(str(self.tmp / "nope.mp4"), start=0.0, end=1.0)
        self.assertFalse(pv.available)
        self.assertTrue(pv.reason)

    def test_cleanup_removes_transient_material(self):
        d = Path(tempfile.mkdtemp(prefix="loom-v12-clean-"))
        (d / "x.bin").write_bytes(b"0")
        mmi.cleanup(str(d))
        self.assertFalse(d.exists())


class TestSubmissionPayload(unittest.TestCase):
    ACTIVE = {"dreemzy", "client-a"}
    URL = "https://www.tiktok.com/@espn/video/7515185025495633182"

    def _probe(self):
        return {"duration_seconds": 7.99, "width": 1080, "height": 1920,
                "has_audio": True, "has_video": True, "size_bytes": 1234,
                "sha256": "a" * 64}

    def _build(self, window=(None, None)):
        return mmi.build_media_submission(
            url=self.URL, destination="dreemzy", content_format="hold_unassigned",
            submitter="bryan#1", media_reference={"url": "https://cdn/x.mp4", "file_id": "f1"},
            probe=self._probe(), human_window=window, reviewer_note="n",
            active_tenants=self.ACTIVE)

    def test_marks_supplied_media_origin_and_durable_reference(self):
        p = self._build()
        self.assertEqual(p["origin"], "manual_media")
        self.assertEqual(p["platform"], "tiktok")
        self.assertEqual(p["media_reference"]["url"], "https://cdn/x.mp4")
        self.assertEqual(p["media_reference"]["sha256"], "a" * 64)
        self.assertEqual(p["media_reference"]["duration_seconds"], 7.99)
        self.assertEqual(len(p["duplicate_fingerprint"]), 64)

    def test_human_window_is_recorded_as_manual_provenance(self):
        p = self._build(window=(2.0, 5.0))
        self.assertEqual(p["boundary_provenance"], "manual_intake")
        self.assertEqual((p["time_start_s"], p["time_end_s"]), (2.0, 5.0))

    def test_no_window_records_no_provenance_yet(self):
        # Without an operator window, provenance is unknown until Content
        # Intelligence produces a boundary (then 'machine'). Claiming 'machine'
        # at intake time would assert something that has not happened.
        self.assertIsNone(self._build()["boundary_provenance"])

    def test_invalid_url_rejected_by_existing_contract(self):
        with self.assertRaises(manual_intake.SubmissionError):
            mmi.build_media_submission(
                url="https://example.invalid/x", destination="dreemzy",
                content_format="hold_unassigned", submitter="b", media_reference={},
                probe=self._probe(), human_window=(None, None), active_tenants=self.ACTIVE)

    def test_unknown_format_rejected(self):
        with self.assertRaises(manual_intake.SubmissionError):
            mmi.build_media_submission(
                url=self.URL, destination="dreemzy", content_format="not_a_format",
                submitter="b", media_reference={}, probe=self._probe(),
                human_window=(None, None), active_tenants=self.ACTIVE)


class TestModeBUrlIntake(unittest.TestCase):
    """Mode B (URL-only): EXISTING resolver + EXISTING bounded local downloader.

    Offline and deterministic: the downloader is stubbed, so this pins the
    WIRING (which command builder is used, which identity results) without
    touching the network or any paid provider.
    """

    FIXTURES = [
        ("https://www.reddit.com/r/bootyrecoils/comments/1b6k4c7/booty_recoil/#lightbox",
         "reddit", "1b6k4c7",
         "https://www.reddit.com/r/bootyrecoils/comments/1b6k4c7/booty_recoil"),
        ("https://kick.com/deenthegreat/clips/clip_01M2XHV0P0HDBFFCXKQ1XK1581",
         "kick", "clip_01M2XHV0P0HDBFFCXKQ1XK1581",
         "https://kick.com/deenthegreat/clips/clip_01M2XHV0P0HDBFFCXKQ1XK1581"),
        ("https://www.twitch.tv/jestervirtual/clip/SpinelessInquisitiveRadishDoggo-dmzxfkqgVRyyQO7h",
         "twitch", "SpinelessInquisitiveRadishDoggo-dmzxfkqgVRyyQO7h",
         "https://www.twitch.tv/jestervirtual/clip/SpinelessInquisitiveRadishDoggo-dmzxfkqgVRyyQO7h"),
    ]

    def test_fixtures_resolve_with_non_empty_identity(self):
        for url, plat, cid, canonical in self.FIXTURES:
            with self.subTest(url=url):
                r = mmi.resolve_source_url(url)
                self.assertEqual(r["platform"], plat)
                self.assertEqual(r["content_id"], cid)
                self.assertEqual(r["canonical_url"], canonical)
                self.assertTrue(r["content_id"], "content_id must never be empty")

    def test_reddit_fragment_never_reaches_identity(self):
        self.assertNotIn("#", mmi.resolve_source_url(self.FIXTURES[0][0])["canonical_url"])

    def test_kick_identity_matches_acquisition_contract(self):
        import kick_url
        url = self.FIXTURES[1][0]
        self.assertEqual(mmi.resolve_source_url(url)["content_id"],
                         kick_url.parse(url)["content_id"])

    def test_unsupported_host_refused(self):
        with self.assertRaises(manual_intake.SubmissionError):
            mmi.resolve_source_url("https://example.com/watch?v=abc")

    def test_acquire_reuses_the_existing_bounded_local_command(self):
        import ytdlp_adapter as ya
        from unittest import mock
        captured = {}

        def fake_build(url, *, out_template, max_bytes=0, **kw):
            cmd = ["yt-dlp", "--no-playlist", "--playlist-items", "1", "--", url]
            captured.update(url=url, template=out_template,
                            max_bytes=max_bytes, cmd=cmd)
            return cmd

        def fake_run(cmd, **kw):
            out = os.path.join(os.path.dirname(captured["template"]), "media.mp4")
            with open(out, "wb") as fh:
                fh.write(b"\0" * 64)
            return out

        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(ya, "build_download_command", fake_build), \
                 mock.patch.object(ya, "run_ytdlp_command", fake_run):
                # This test is about WHERE the command came from, so the artifact
                # validators are injected. The real ones are exercised by the
                # fail-closed and integrity-gate tests.
                path, resolved = mmi.acquire_url_media(
                    self.FIXTURES[1][0], out_dir=d,
                    probe_fn=lambda p: {"has_video": True, "has_audio": True},
                    integrity_fn=lambda p: {"ok": True, "reason": "", "detail": ""})
            self.assertGreater(os.path.getsize(path), 0)
            self.assertEqual(resolved["platform"], "kick")

        # The command CAME FROM the acquisition lane's builder: bounded, single
        # item, no cookie/auth flags — i.e. no second downloader was introduced.
        self.assertIn("--no-playlist", captured["cmd"])
        self.assertIn("--playlist-items", captured["cmd"])
        self.assertEqual(captured["max_bytes"], mmi.MAX_URL_BYTES)
        self.assertEqual(captured["url"], self.FIXTURES[1][3])


if __name__ == "__main__":
    unittest.main(verbosity=2)
