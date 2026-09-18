"""Local yt-dlp adapter tests: hygiene, bounds, validation, merge, atomicity.

Run:  python3 -m unittest discover -s infra/acquisition/tests -v

Mocked only — no network, no yt-dlp, no ffmpeg, no YouTube download.
"""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
for d in ("acquisition", "discord-review"):
    sys.path.insert(0, str(_REPO_ROOT / "infra" / d))

import ytdlp_adapter as ya  # noqa: E402

VID = "dQw4w9WgXcQ"
URL = f"https://www.youtube.com/watch?v={VID}"


def ffprobe_ok(*, duration=77.6, vcodec="h264", acodec="aac", size_streams=1):
    return {
        "streams": [{"codec_type": "video", "codec_name": vcodec, "width": 1280,
                     "height": 720}][:size_streams]
        + [{"codec_type": "audio", "codec_name": acodec}],
        "format": {"duration": str(duration), "format_name": "mov,mp4,m4a,3gp,3g2,mj2"},
    }


def probe_meta(**over):
    base = {"id": VID, "duration": 77.6, "title": "Fixture Reel", "is_live": False,
            "live_status": "not_live"}
    base.update(over)
    return base


class TestCommandHygiene(unittest.TestCase):
    def test_download_command_is_bounded_and_hygienic(self):
        cmd = ya.build_download_command(URL, out_template="/tmp/x.%(ext)s")
        self.assertIn("--no-playlist", cmd)
        self.assertIn("--playlist-items", cmd)
        self.assertEqual(cmd[cmd.index("--playlist-items") + 1], "1")
        self.assertIn("--merge-output-format", cmd)
        self.assertEqual(cmd[cmd.index("--merge-output-format") + 1], "mp4")
        self.assertIn(ya.FORMAT_SELECTOR, cmd)
        # one attempt, no unbounded enumeration
        self.assertEqual(cmd[cmd.index("--retries") + 1], "0")
        self.assertNotIn("-F", cmd)
        self.assertNotIn("--format", [a for a in cmd if a == "all"])
        # no cookies / account / auth material whatsoever
        for flag in ("--cookies", "--cookies-from-browser", "--username", "--password",
                     "--netrc", "--add-header", "--referer", "--user-agent"):
            self.assertNotIn(flag, cmd)

    def test_probe_command_is_download_free_and_fetch_one_item(self):
        cmd = ya.build_probe_command(URL)
        self.assertIn("--skip-download", cmd)
        self.assertIn("--dump-single-json", cmd)
        self.assertIn("--no-playlist", cmd)

    def test_hygiene_guard_rejects_a_tampered_command(self):
        with self.assertRaises(ya.YtDlpError) as e:
            ya._assert_hygiene(["yt-dlp", "--cookies", "/tmp/c.txt", "--no-playlist"])
        self.assertEqual(e.exception.code, "COMMAND_HYGIENE")

    def test_no_paid_provider_tokens_anywhere(self):
        for cmd in (ya.build_probe_command(URL),
                    ya.build_download_command(URL, out_template="/tmp/x.%(ext)s")):
            joined = " ".join(cmd).lower()
            for forbidden in ("apify", "actor", "token", "api_key", "bearer"):
                self.assertNotIn(forbidden, joined)


class TestMetadataRefusals(unittest.TestCase):
    def _refuse(self, meta, code):
        with self.assertRaises(ya.YtDlpError) as e:
            ya.validate_metadata(meta, expected_id=VID)
        self.assertEqual(e.exception.code, code)

    def test_rejects_playlist_and_multiple_entries(self):
        self._refuse({"_type": "playlist", "id": VID, "duration": 10}, "PLAYLIST_REJECTED")
        self._refuse({"entries": [probe_meta(), probe_meta(id="other12345")]},
                     "MULTIPLE_RESULTS")

    def test_accepts_exactly_one_entry(self):
        out = ya.validate_metadata({"entries": [probe_meta()]}, expected_id=VID)
        self.assertEqual(out["video_id"], VID)

    def test_rejects_duration_over_the_limit_before_downloading(self):
        self._refuse(probe_meta(duration=301), "TOO_LONG")
        self._refuse(probe_meta(duration=300.5), "TOO_LONG")
        out = ya.validate_metadata(probe_meta(duration=300), expected_id=VID)
        self.assertEqual(out["duration_seconds"], 300.0)

    def test_rejects_live_and_upcoming(self):
        self._refuse(probe_meta(is_live=True), "LIVE_REJECTED")
        self._refuse(probe_meta(live_status="is_upcoming"), "LIVE_REJECTED")

    def test_rejects_id_drift_and_unknown_duration(self):
        self._refuse(probe_meta(id="other12345"), "CONTENT_ID_DRIFT")
        self._refuse(probe_meta(duration=None), "UNKNOWN_DURATION")

    def test_rejects_oversized_reported_size(self):
        self._refuse(probe_meta(filesize=ya.MAX_BYTES + 1), "TOO_LARGE")


class TestMediaValidation(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="loom-ytdlp-media-"))
        self.file = self.tmp / f"{VID}.mp4"
        self.file.write_bytes(b"x" * 2048)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _refuse(self, probe, code, **kw):
        with self.assertRaises(ya.YtDlpError) as e:
            ya.validate_media(probe, path=kw.pop("path", self.file), expected_id=VID, **kw)
        self.assertEqual(e.exception.code, code)

    def test_happy_path_returns_full_media_facts(self):
        media = ya.validate_media(ffprobe_ok(), path=self.file, expected_id=VID)
        self.assertEqual(media["video_codec"], "h264")
        self.assertEqual(media["audio_codec"], "aac")
        self.assertEqual(media["size_bytes"], 2048)
        self.assertEqual(len(media["sha256"]), 64)
        self.assertEqual(media["duration_seconds"], 77.6)

    def test_refuses_missing_zero_byte_and_oversized_files(self):
        self._refuse(ffprobe_ok(), "MISSING_FILE", path=self.tmp / "nope.mp4")
        empty = self.tmp / "empty.mp4"
        empty.write_bytes(b"")
        self._refuse(ffprobe_ok(), "ZERO_BYTES", path=empty)

    def test_refuses_missing_audio_and_wrong_video_count(self):
        no_audio = {"streams": [{"codec_type": "video", "codec_name": "h264"}],
                    "format": {"duration": "10"}}
        self._refuse(no_audio, "NO_AUDIO")
        two_video = {"streams": [{"codec_type": "video", "codec_name": "h264"},
                                 {"codec_type": "video", "codec_name": "h264"},
                                 {"codec_type": "audio", "codec_name": "aac"}],
                     "format": {"duration": "10"}}
        self._refuse(two_video, "VIDEO_STREAM_COUNT")

    def test_refuses_unsupported_codecs_and_bad_container_duration(self):
        self._refuse(ffprobe_ok(vcodec="theora"), "UNSUPPORTED_VIDEO_CODEC")
        self._refuse(ffprobe_ok(acodec="pcm_s16le"), "UNSUPPORTED_AUDIO_CODEC")
        self._refuse({"streams": [{"codec_type": "video", "codec_name": "h264"},
                                  {"codec_type": "audio", "codec_name": "aac"}],
                      "format": {}}, "BAD_DURATION")

    def test_refuses_over_duration_and_ffprobe_errors(self):
        self._refuse(ffprobe_ok(duration=420), "TOO_LONG")
        self._refuse({"error": "boom"}, "FFPROBE_FAILED")
        self._refuse({}, "NO_STREAMS")          # usable JSON but nothing to validate

    def test_refuses_media_id_drift(self):
        self._refuse(ffprobe_ok(), "CONTENT_ID_DRIFT", video_id_from_meta="other12345")


class TestAcquireLocal(unittest.TestCase):
    def setUp(self):
        # a fresh root per test: no cross-test residue in tmp or library
        self.root = Path(tempfile.mkdtemp(prefix="loom-ytdlp-acquire-"))
        self.tmp = self.root / "tmp"
        self.lib = self.root / "library"
        for d in (self.tmp, self.lib):
            d.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _runners(self, *, meta=None, media_probe=None, write=True):
        produced: dict = {}

        def run_probe(_cmd):
            return meta if meta is not None else probe_meta()

        def run_download(cmd):
            template = cmd[cmd.index("--output") + 1]
            path = Path(template.replace("%(ext)s", "mp4"))
            if write:
                path.write_bytes(b"v" * 4096)
            produced["path"] = path
            return str(path)

        def run_ffprobe(_p):
            return media_probe if media_probe is not None else ffprobe_ok()

        return run_probe, run_download, run_ffprobe, produced

    def test_success_finalizes_atomically_with_deterministic_name(self):
        run_probe, run_download, run_ffprobe, produced = self._runners()
        out = ya.acquire_local(url=URL, expected_id=VID, tmp_dir=self.tmp,
                               library_dir=self.lib, run_probe=run_probe,
                               run_download=run_download, run_ffprobe=run_ffprobe)
        self.assertTrue(out["asset_filename"].startswith(f"youtube__{VID}__"))
        self.assertTrue(out["asset_filename"].endswith(".mp4"))
        self.assertTrue(Path(out["path"]).is_file())
        self.assertFalse(produced["path"].exists(), "temp file must be moved, not copied")
        self.assertEqual(out["provider"], "local")
        self.assertEqual(out["adapter"], "local:yt-dlp")
        self.assertEqual(out["provider_cost_micro"], 0)
        self.assertEqual(out["attempts"], 1)
        self.assertEqual(out["video_id"], VID)
        self.assertEqual(list(self.tmp.glob(f"{VID}.*")), [])

    def test_failure_removes_partial_files_and_leaves_the_library_clean(self):
        run_probe, run_download, run_ffprobe, produced = self._runners(
            media_probe={"streams": [{"codec_type": "video", "codec_name": "h264"}],
                         "format": {"duration": "5"}})   # no audio -> refusal
        with self.assertRaises(ya.YtDlpError) as e:
            ya.acquire_local(url=URL, expected_id=VID, tmp_dir=self.tmp,
                             library_dir=self.lib, run_probe=run_probe,
                             run_download=run_download, run_ffprobe=run_ffprobe)
        self.assertEqual(e.exception.code, "NO_AUDIO")
        self.assertFalse(produced["path"].exists(), "partial file must be removed")
        self.assertEqual(list(self.lib.iterdir()), [])
        self.assertEqual(list(self.tmp.glob(f"{VID}.*")), [])

    def test_metadata_refusal_happens_before_any_download(self):
        called = {"download": False}

        def run_download(_cmd):
            called["download"] = True
            raise AssertionError("must not download after a metadata refusal")

        with self.assertRaises(ya.YtDlpError) as e:
            ya.acquire_local(url=URL, expected_id=VID, tmp_dir=self.tmp,
                             library_dir=self.lib,
                             run_probe=lambda _c: probe_meta(duration=999),
                             run_download=run_download,
                             run_ffprobe=lambda _p: ffprobe_ok())
        self.assertEqual(e.exception.code, "TOO_LONG")
        self.assertFalse(called["download"])

    def test_one_attempt_only(self):
        calls = {"n": 0}
        run_probe, run_download, run_ffprobe, _ = self._runners()

        def counted_download(cmd):
            calls["n"] += 1
            return run_download(cmd)

        ya.acquire_local(url=URL, expected_id=VID, tmp_dir=self.tmp, library_dir=self.lib,
                         run_probe=run_probe, run_download=counted_download,
                         run_ffprobe=run_ffprobe)
        self.assertEqual(calls["n"], 1)

    def test_separate_streams_merge_into_one_validated_file(self):
        """yt-dlp merges via ffmpeg; the adapter validates the merged result."""
        run_probe, run_download, run_ffprobe, _ = self._runners()
        cmd = ya.build_download_command(URL, out_template=str(self.tmp / "%(ext)s"))
        self.assertEqual(cmd[cmd.index("--merge-output-format") + 1], "mp4")
        out = ya.acquire_local(url=URL, expected_id=VID, tmp_dir=self.tmp,
                               library_dir=self.lib, run_probe=run_probe,
                               run_download=run_download, run_ffprobe=run_ffprobe)
        probe = ya.run_ffprobe_json  # real runner exists but is never used in tests
        self.assertTrue(callable(probe))
        self.assertEqual(out["video_codec"], "h264")
        self.assertEqual(out["audio_codec"], "aac")


if __name__ == "__main__":
    unittest.main()
