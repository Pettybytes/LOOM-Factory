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
# A minimal fast-start MP4: moov precedes mdat, as the delivery contract requires.
_FS = b"\x00\x00\x00\x18moov" + b"\x00" * 64 + b"\x00\x00\x00\x18mdat" + b"x" * 4096


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
        # BOUNDED recovery, never unbounded enumeration: a few retries so an
        # interrupted transfer can complete and resume from its .part file.
        self.assertEqual(cmd[cmd.index("--retries") + 1], str(ya.DOWNLOAD_RETRIES))
        self.assertEqual(cmd[cmd.index("--fragment-retries") + 1],
                         str(ya.FRAGMENT_RETRIES))
        self.assertLessEqual(ya.DOWNLOAD_RETRIES, 5)
        self.assertLessEqual(ya.FRAGMENT_RETRIES, 5)
        # --no-part let an interrupted download masquerade as a completed file
        # (runtime-proven on a truncated artifact with a normal-looking name).
        self.assertNotIn("--no-part", cmd)
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


class TestSizeAwareFormatSelection(unittest.TestCase):
    """The intake ceiling is respected by CHOICE OF REPRESENTATION."""

    def test_unbounded_selector_is_unchanged_for_existing_callers(self):
        self.assertEqual(ya.build_format_selector(), ya.FORMAT_SELECTOR)
        self.assertEqual(ya.build_format_selector(max_height=None), ya.FORMAT_SELECTOR)

    def test_height_bound_is_applied_to_every_bounded_alternative(self):
        sel = ya.build_format_selector(max_height=720)
        self.assertNotEqual(sel, ya.FORMAT_SELECTOR)
        # every PREFERENCE alternative stays bounded, so a rendition that actually
        # advertises a resolution can never be picked above the ceiling
        alts = sel.split("/")
        self.assertEqual(alts[-2:], ya.UNTAGGED_FALLBACK_SELECTOR.split("/"))
        for alt in alts[:-2]:
            self.assertIn("height<=720", alt, alt)

    def test_sparse_manifest_still_resolves_under_a_height_bound(self):
        """THE 2026-09-29 REGRESSION: a manifest with no resolution metadata.

        A Kick clip publishes exactly one untagged combined rendition
        (`ext=mp4`, `height=NA`, `vcodec=NA`, `acodec=NA`). `height<=H` is false for
        a MISSING height, so every bounded alternative missed and yt-dlp exited 1
        with "Requested format is not available" although an eligible rendition
        existed. The bounded selector must therefore keep a resolution-free
        alternative.
        """
        sel = ya.build_format_selector(max_height=1080)
        height_free = [a for a in sel.split("/") if "height<=" not in a]
        self.assertTrue(height_free, sel)
        # and one of them is the plain combined mp4 rendition
        self.assertIn("b[ext=mp4]", height_free)

    def test_the_height_free_fallback_cannot_shadow_a_bounded_choice(self):
        """Preference order is preserved: bounded alternatives come FIRST."""
        for bound in ya.height_ladder():
            alts = ya.build_format_selector(max_height=bound).split("/")
            # the FIRST preference is still the bounded delivery-ready rendition
            self.assertIn("height<=%d" % bound, alts[0])
            self.assertEqual(alts[-2:], ya.UNTAGGED_FALLBACK_SELECTOR.split("/"))

    def test_every_bounded_rung_carries_the_fallback_in_the_real_command(self):
        for bound in ya.height_ladder():
            cmd = ya.build_download_command(URL, out_template="/tmp/x.%(ext)s",
                                            max_height=bound)
            selector = cmd[cmd.index("--format") + 1]
            self.assertTrue(selector.endswith("/" + ya.UNTAGGED_FALLBACK_SELECTOR))
            self.assertIn("--max-filesize", cmd)
            self.assertIn("height<=%d" % bound, selector)

    def test_command_carries_the_bound_and_the_ladder_is_ordered(self):
        cmd = ya.build_download_command(URL, out_template="/tmp/x.%(ext)s", max_height=480)
        self.assertIn(ya.build_format_selector(max_height=480), cmd)
        ladder = ya.height_ladder()
        self.assertEqual(list(ladder), sorted(ladder, reverse=True))
        self.assertGreaterEqual(len(ladder), 2)

    def test_higher_quality_is_tried_before_lower(self):
        self.assertEqual(ya.height_ladder()[0], max(ya.height_ladder()))


class TestIntegrityVerifier(unittest.TestCase):
    """The gate that ffprobe metadata cannot provide."""

    def test_passes_a_clean_artifact(self):
        verdict = ya.verify_media_integrity("/tmp/whatever.mp4", runner=lambda cmd: None)
        self.assertTrue(verdict["ok"])
        self.assertEqual(verdict["reason"], "")

    def test_command_decodes_only_the_tail_and_writes_no_output(self):
        seen = {}
        ya.verify_media_integrity("/tmp/x.mp4", runner=lambda cmd: seen.update(parts=cmd))
        cmd = seen["parts"]
        self.assertIn("-sseof", cmd)          # tail only, never a full decode
        self.assertIn("-f", cmd) and self.assertIn("null", cmd)   # no output written
        self.assertIn("error", cmd)            # errors are surfaced, not swallowed
        self.assertNotIn("libx264", cmd)       # never a transcode

    def test_a_nonzero_decode_is_a_failure(self):
        def failing(cmd):
            raise __import__("subprocess").CalledProcessError(183, cmd, stderr="corrupt")
        # The real runner path is exercised via subprocess; an unavailable or
        # erroring tool must never be reported as a pass.
        verdict = ya.verify_media_integrity("/tmp/definitely-not-here.mp4")
        self.assertFalse(verdict["ok"])
        self.assertIn(verdict["reason"], ("media_integrity_failed", "integrity_unavailable"))


class TestDownloadFailureClassification(unittest.TestCase):
    def test_classifies_the_distinguishable_failures(self):
        self.assertEqual(ya.classify_download_failure("ERROR: file is larger than max-filesize"),
                         "MEDIA_EXCEEDS_LIMIT")
        self.assertEqual(ya.classify_download_failure("ERROR: ffmpeg not found"),
                         "MERGE_FAILED")
        self.assertEqual(ya.classify_download_failure("ERROR: HTTP Error 403"),
                         "DOWNLOAD_INCOMPLETE")
        self.assertEqual(ya.classify_download_failure(""), "DOWNLOAD_FAILED")


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
        self.file.write_bytes(_FS)

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
        self.assertEqual(media["size_bytes"], len(_FS))
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
                path.write_bytes(_FS)
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


class TestPreflightRepresentation(unittest.TestCase):
    """Pre-flight CHOOOSES; the ladder still DECIDES.

    The format table below mirrors the real incident source as measured through
    the existing metadata probe: the 1080p avc1 stream reports 245,080,589 B and
    the 720p avc1 stream 143,868,985 B, both well over the 100 MiB ceiling, while
    480p reports 56,468,665 B + audio and genuinely fits.
    """

    CAP = 100 * 1024 * 1024
    META = {"formats": [
        {"format_id": "299", "vcodec": "avc1.64002a", "acodec": "none",
         "height": 1080, "filesize_approx": 245080589},
        {"format_id": "298", "vcodec": "avc1.640020", "acodec": "none",
         "height": 720, "filesize_approx": 143868985},
        {"format_id": "135", "vcodec": "avc1.4d401f", "acodec": "none",
         "height": 480, "filesize_approx": 56468665},
        {"format_id": "134", "vcodec": "avc1.4d401e", "acodec": "none",
         "height": 360, "filesize_approx": 31419392},
        {"format_id": "140", "vcodec": "none", "acodec": "mp4a.40.2",
         "filesize_approx": 7553017},
    ]}

    def test_estimate_sums_video_and_audio_plus_overhead(self):
        est = ya.estimate_merged_bytes(self.META, max_height=480)
        self.assertEqual(est, 56468665 + 7553017 + ya.MERGE_OVERHEAD_BYTES)

    def test_estimate_honours_the_height_bound(self):
        # 360 must never be estimated from the 480 or 1080 streams.
        self.assertEqual(ya.estimate_merged_bytes(self.META, max_height=360),
                         31419392 + 7553017 + ya.MERGE_OVERHEAD_BYTES)

    def test_prefers_avc1_and_mp4a_like_the_selector_does(self):
        meta = {"formats": [
            {"format_id": "vp9", "vcodec": "vp9", "acodec": "none",
             "height": 480, "filesize_approx": 32391604},
            {"format_id": "135", "vcodec": "avc1.4d401f", "acodec": "none",
             "height": 480, "filesize_approx": 56468665},
            {"format_id": "opus", "vcodec": "none", "acodec": "opus",
             "filesize_approx": 5000000},
            {"format_id": "140", "vcodec": "none", "acodec": "mp4a.40.2",
             "filesize_approx": 7553017},
        ]}
        self.assertEqual(ya.estimate_merged_bytes(meta, max_height=480),
                         56468665 + 7553017 + ya.MERGE_OVERHEAD_BYTES)

    def test_estimate_refuses_to_guess_when_a_half_has_no_size(self):
        # An unreported size is UNKNOWN, never zero and never "fits".
        meta = {"formats": [
            {"format_id": "135", "vcodec": "avc1", "acodec": "none", "height": 480},
            {"format_id": "140", "vcodec": "none", "acodec": "mp4a",
             "filesize_approx": 7553017},
        ]}
        self.assertIsNone(ya.estimate_merged_bytes(meta, max_height=480))
        self.assertIsNone(ya.estimate_merged_bytes({}, max_height=480))
        self.assertIsNone(ya.estimate_merged_bytes(None, max_height=480))

    def test_incident_source_preflight_skips_1080_and_720(self):
        """The whole point: the two rungs that wasted ~389 MB are never fetched."""
        height, estimate, why = ya.choose_starting_height(self.META, max_bytes=self.CAP)
        self.assertEqual(height, 480)
        self.assertEqual(why, "estimate_fits")
        self.assertLessEqual(estimate, self.CAP)

    def test_ladder_from_skips_only_rungs_above_the_choice(self):
        self.assertEqual(ya.ladder_from(480), (480, 360))
        self.assertEqual(ya.ladder_from(1080), ya.height_ladder())
        # An unknown/absent choice must not narrow the ladder at all.
        self.assertEqual(ya.ladder_from(None), ya.height_ladder())
        self.assertEqual(ya.ladder_from(999), ya.height_ladder())

    def test_unknown_estimate_never_narrows_the_ladder(self):
        # Metadata with no reported sizes at all: every rung estimates UNKNOWN, so
        # pre-flight must start at the TOP and leave the whole ladder intact — a
        # missing number must never silently cost quality.
        meta = {"formats": [
            {"format_id": "299", "vcodec": "avc1", "acodec": "none", "height": 1080},
            {"format_id": "135", "vcodec": "avc1", "acodec": "none", "height": 480},
            {"format_id": "140", "vcodec": "none", "acodec": "mp4a"},
        ]}
        height, estimate, why = ya.choose_starting_height(meta, max_bytes=self.CAP)
        self.assertEqual(height, ya.height_ladder()[0])
        self.assertIsNone(estimate)
        self.assertEqual(why, "estimate_unknown")
        self.assertEqual(ya.ladder_from(height), ya.height_ladder())

    def test_a_lower_stream_may_supply_the_fitting_estimate_for_a_higher_rung(self):
        # At rung <=720 the selector can legitimately fall back to the 480 stream,
        # so a fitting estimate at 720 is correct — the rung means "at most 720".
        meta = {"formats": [
            {"format_id": "299", "vcodec": "avc1", "acodec": "none",
             "height": 1080, "filesize_approx": 245080589},
            {"format_id": "135", "vcodec": "avc1", "acodec": "none",
             "height": 480, "filesize_approx": 56468665},
            {"format_id": "140", "vcodec": "none", "acodec": "mp4a",
             "filesize_approx": 7553017},
        ]}
        height, estimate, why = ya.choose_starting_height(meta, max_bytes=self.CAP)
        self.assertEqual(height, 720)
        self.assertEqual(why, "estimate_fits")
        self.assertLessEqual(estimate, self.CAP)

    def test_preflight_never_raises_the_ceiling(self):
        # Nothing fits: pre-flight must NOT invent a fitting rung and must NOT
        # widen the ceiling. It starts at the top and the post-download checks
        # still decide — so the ladder keeps every rung.
        height, estimate, why = ya.choose_starting_height(self.META, max_bytes=1000)
        self.assertEqual(height, ya.height_ladder()[0])
        self.assertIsNone(estimate)
        self.assertEqual(why, "no_estimate")
        self.assertEqual(ya.ladder_from(height), ya.height_ladder())

    def test_retry_bound_is_unchanged_by_preflight(self):
        self.assertEqual(ya.DOWNLOAD_RETRIES, 3)
        self.assertEqual(ya.FRAGMENT_RETRIES, 3)


if __name__ == "__main__":
    unittest.main()
