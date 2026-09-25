"""Real, tiny local-video regressions; no network or provider calls."""

from concurrent.futures import ThreadPoolExecutor
import dataclasses
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from content_intelligence import bounded_preview as bp


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "local ffmpeg/ffprobe required")
class TestContiguousInspectionPreview(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixtures = tempfile.TemporaryDirectory(prefix="loom-preview-fixtures-")
        cls.source = Path(cls.fixtures.name) / "source.mp4"
        subprocess.run([
            "ffmpeg", "-v", "error", "-nostdin", "-y", "-f", "lavfi", "-i",
            "testsrc2=size=64x48:rate=10", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
            "-t", "16", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", str(cls.source),
        ], capture_output=True, text=True, check=True, timeout=30)

    @classmethod
    def tearDownClass(cls):
        cls.fixtures.cleanup()

    def setUp(self):
        self.cache = tempfile.TemporaryDirectory(prefix="loom-chunk-test-")
        self.calls = []
        self.outputs = []
        self.fetch_paths = []
        self.chunk_patch = patch.object(bp, "MAX_WINDOW", 4.0)
        self.chunk_patch.start()

    def tearDown(self):
        self.chunk_patch.stop()
        for result in self.outputs:
            bp.cleanup(result)
        for path in self.fetch_paths:
            bp.cleanup(str(path))
        self.cache.cleanup()

    def fetch(self, url, *, start, end, duration_delta=0.0, gap=False):
        self.calls.append((start, end))
        directory = Path(tempfile.mkdtemp(prefix="loom-inspect-local-fixture-"))
        artifact = directory / "part.mp4"
        self.fetch_paths.append(artifact)
        cmd = ["ffmpeg", "-v", "error", "-nostdin", "-y", "-ss", str(start),
               "-i", str(self.source), "-t", str(end - start + duration_delta)]
        if gap:
            # An internal presentation-time gap, not merely a short container.
            cmd += ["-an", "-vf", "setpts=PTS+if(gte(T\\,2)\\,0.6/TB\\,0)",
                    "-fps_mode", "passthrough"]
        cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(artifact)]
        subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=30)
        return bp.BoundedPreview(start, end, start, end, str(artifact), artifact.stat().st_size,
                                 "local-fixture", "section_download")

    def inspect(self, *, start=0, end=13, fetcher=None, runner=subprocess.run, cache_dir=None,
                allow_terminal_partial=False):
        result = bp.fetch_contiguous_inspection_preview(
            "https://www.youtube.com/watch?v=LocalOnly12", start=start, end=end,
            fetcher=fetcher or self.fetch, runner=runner, cache_dir=cache_dir or self.cache.name,
            allow_terminal_partial=allow_terminal_partial)
        self.outputs.append(result)
        return result

    def test_real_13_second_coverage_is_not_capped_to_one_transfer(self):
        result = self.inspect()
        self.assertTrue(result.available, result.reason)
        self.assertEqual((result.inspected_start, result.inspected_end), (0, 13))
        self.assertEqual(self.calls, [(0, 4), (4, 8), (8, 12), (12, 13)])
        self.assertTrue(all(end - start <= 4 for start, end in self.calls))
        probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                "-of", "json", result.artifact_path], capture_output=True,
                               text=True, check=True, timeout=30)
        self.assertAlmostEqual(float(json.loads(probe.stdout)["format"]["duration"]), 13, delta=.11)
        self.assertTrue(all(not path.exists() for path in self.fetch_paths))
        self.assertFalse(result.fallback_used)

    def test_verified_cache_reuse_needs_no_additional_transfer(self):
        first = self.inspect()
        second = self.inspect(fetcher=lambda *args, **kwargs: self.fail("cache caused a transfer"))
        self.assertTrue(first.available, first.reason)
        self.assertTrue(second.available, second.reason)
        self.assertIn("cache_hits:4", second.provenance)
        self.assertIn("fetched_artifact_bytes:0", second.provenance)
        self.assertNotEqual(first.artifact_path, second.artifact_path)
        bp.cleanup(first)
        self.assertTrue(Path(second.artifact_path).is_file())
        self.assertEqual(len(list(Path(self.cache.name).glob("*.mp4"))), 4)

    def test_large_native_video_becomes_bounded_inline_proxy_without_time_loss(self):
        with tempfile.TemporaryDirectory() as folder:
            large = Path(folder) / 'large.mp4'
            subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', str(self.source),
                            '-t', '4', '-vf', 'scale=1920:1080', '-c:v', 'libx264',
                            '-preset', 'ultrafast', str(large)],
                           capture_output=True, check=True, timeout=30)
            self.source = large
            result = self.inspect(end=4)
            self.assertTrue(result.available, result.reason)
            def probe(path):
                out = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0',
                    '-show_entries', 'stream=width,height,avg_frame_rate,duration',
                    '-of', 'json', str(path)], capture_output=True, text=True, check=True)
                return json.loads(out.stdout)['streams'][0]
            before, after = probe(large), probe(result.artifact_path)
            self.assertEqual((after['width'], after['height']), (640, 360))
            self.assertEqual(before['avg_frame_rate'], after['avg_frame_rate'])
            self.assertAlmostEqual(float(after['duration']), 4, delta=.05)
            self.assertEqual((result.inspected_start, result.inspected_end), (0, 4))
            self.assertLess(result.artifact_bytes, 20 * 1024 * 1024)

    def test_corrupt_cached_chunk_is_repaired_without_refetching_other_chunks(self):
        first = self.inspect(end=8)
        self.assertTrue(first.available, first.reason)
        target = next(Path(self.cache.name).glob("*.mp4"))
        # Atomic replacement simulates a foreign cache corruption without
        # mutating an inode already pinned by an earlier inspection result.
        corrupt = Path(self.cache.name) / "corrupt.tmp"
        corrupt.write_bytes(b"not a video")
        corrupt.replace(target)
        second = self.inspect(end=8)
        self.assertTrue(second.available, second.reason)
        self.assertEqual(len(self.calls), 3)
        self.assertIn("cache_hits:1", second.provenance)

    def test_concurrent_same_window_publishes_once_and_never_exposes_partial_chunk(self):
        staging_started, release = threading.Event(), threading.Event()
        first_normalization = True
        state_lock = threading.Lock()

        def runner(cmd, **kwargs):
            nonlocal first_normalization
            if cmd[0] == "ffmpeg" and Path(cmd[-1]).name == "normalized.mp4":
                with state_lock:
                    pause = first_normalization
                    first_normalization = False
                if pause:
                    Path(cmd[-1]).write_bytes(b"partial encoder output")
                    staging_started.set()
                    if not release.wait(10):
                        raise RuntimeError("test synchronization timeout")
            return subprocess.run(cmd, **kwargs)

        with ThreadPoolExecutor(max_workers=2) as workers:
            first = workers.submit(self.inspect, end=4, runner=runner)
            try:
                self.assertTrue(staging_started.wait(10))
                second = workers.submit(self.inspect, end=4, runner=runner)
                self.assertEqual(list(Path(self.cache.name).glob("*.mp4")), [])
            finally:
                release.set()
            results = [first.result(timeout=30), second.result(timeout=30)]
        self.assertTrue(all(r.available for r in results), [r.reason for r in results])
        self.assertEqual(self.calls, [(0, 4)])
        self.assertEqual(len(list(Path(self.cache.name).glob("*.mp4"))), 1)
        self.assertFalse(any(p.is_dir() for p in Path(self.cache.name).iterdir()))

    def test_declared_gap_or_shift_fails_closed(self):
        def shifted(url, **kwargs):
            return dataclasses.replace(self.fetch(url, **kwargs), inspected_start=1, inspected_end=5)
        result = self.inspect(end=4, fetcher=shifted)
        self.assertFalse(result.available)
        self.assertIn("coverage_gap_or_offset", result.reason)
        self.assertEqual(self.calls, [(0, 4)])
        self.assertEqual(list(Path(self.cache.name).glob("*.mp4")), [])

    def test_truncated_real_video_fails_closed_even_when_declared_bounds_match(self):
        result = self.inspect(end=4, fetcher=lambda url, **kw: self.fetch(url, duration_delta=-.6, **kw))
        self.assertFalse(result.available)
        self.assertIn("video_duration_mismatch", result.reason)
        self.assertEqual(list(Path(self.cache.name).glob("*.mp4")), [])

    def test_explicit_terminal_partial_reports_real_coverage_not_requested_end(self):
        # Local source physically ends at 16s, while discovery advertised 16.4s.
        result = self.inspect(start=5, end=16.4, allow_terminal_partial=True)
        self.assertTrue(result.available, result.reason)
        self.assertEqual(result.requested_end, 16.4)
        self.assertAlmostEqual(result.inspected_end, 16, delta=.01)
        self.assertTrue(any(x.startswith("terminal_partial:requested_end=16.400000:actual_end=16.")
                            for x in result.provenance))
        self.assertEqual(self.calls, [(5, 9), (9, 13), (13, 16.4)])

    def test_last_chunk_partial_is_explicit_and_reusable_without_padding(self):
        def terminal_short(url, **kwargs):
            delta = -.4 if kwargs["end"] == 7.4 else 0
            return self.fetch(url, duration_delta=delta, **kwargs)
        result = self.inspect(end=7.4, fetcher=terminal_short, allow_terminal_partial=True)
        self.assertTrue(result.available, result.reason)
        self.assertAlmostEqual(result.inspected_end, 7, delta=.01)
        self.assertEqual(result.requested_end, 7.4)
        replay = self.inspect(end=7.4, allow_terminal_partial=True,
                              fetcher=lambda *a, **k: self.fail("partial cache was not reused"))
        self.assertTrue(replay.available, replay.reason)
        self.assertAlmostEqual(replay.inspected_end, 7, delta=.01)
        strict = self.inspect(end=7.4, fetcher=terminal_short)
        self.assertFalse(strict.available)
        self.assertIn("video_duration_mismatch", strict.reason)

    def test_partial_flag_never_allows_interior_truncation(self):
        def interior_short(url, **kwargs):
            return self.fetch(url, duration_delta=-.6 if kwargs["start"] == 0 else 0, **kwargs)
        result = self.inspect(end=8, fetcher=interior_short, allow_terminal_partial=True)
        self.assertFalse(result.available)
        self.assertIn("video_duration_mismatch", result.reason)
        self.assertEqual(self.calls, [(0, 4)])

    def test_partial_flag_does_not_hide_normalization_loss(self):
        def runner(cmd, **kwargs):
            if cmd[0] == "ffmpeg" and Path(cmd[-1]).name == "normalized.mp4":
                cmd = list(cmd)
                cmd[cmd.index("-t") + 1] = "1"
            return subprocess.run(cmd, **kwargs)
        result = self.inspect(end=4, runner=runner, allow_terminal_partial=True)
        self.assertFalse(result.available)
        self.assertIn("video_duration_mismatch", result.reason)

    def test_internal_video_timestamp_gap_fails_closed(self):
        def runner(cmd, **kwargs):
            result = subprocess.run(cmd, **kwargs)
            if cmd[0] == "ffprobe" and "loom-inspect-local-fixture-" in cmd[-1]:
                payload = json.loads(result.stdout)
                # A genuinely missing packet interval (unlike a VFR frame whose
                # encoded duration legitimately holds its image for longer).
                for packet in payload["packets"]:
                    if float(packet["pts_time"]) >= 2:
                        packet["pts_time"] = str(float(packet["pts_time"]) + .6)
                result.stdout = json.dumps(payload)
            return result
        result = self.inspect(end=4, runner=runner)
        self.assertFalse(result.available)
        self.assertIn("video_timeline_gap", result.reason)

    def test_normalized_chunk_is_probed_before_atomic_publication(self):
        def runner(cmd, **kwargs):
            if cmd[0] == "ffmpeg" and Path(cmd[-1]).name == "normalized.mp4":
                cmd = list(cmd)
                cmd[cmd.index("-t") + 1] = "1"
            return subprocess.run(cmd, **kwargs)
        result = self.inspect(end=4, runner=runner)
        self.assertFalse(result.available)
        self.assertIn("video_duration_mismatch", result.reason)
        self.assertEqual(list(Path(self.cache.name).glob("*.mp4")), [])
        self.assertFalse(any(p.is_dir() for p in Path(self.cache.name).iterdir()))

    def test_full_source_fallback_is_explicitly_refused(self):
        def fallback(url, **kwargs):
            return dataclasses.replace(self.fetch(url, **kwargs), fallback_used=True)
        result = self.inspect(end=4, fetcher=fallback)
        self.assertFalse(result.available)
        self.assertIn("full_source_fallback_refused", result.reason)
        self.assertEqual(self.calls, [(0, 4)])

    def test_nonzero_source_start_and_quoted_cache_directory_are_supported(self):
        cache = Path(self.cache.name) / "cache with ' quote"
        result = self.inspect(start=5, end=14, cache_dir=cache)
        self.assertTrue(result.available, result.reason)
        self.assertEqual(self.calls, [(5, 9), (9, 13), (13, 14)])
        self.assertEqual((result.inspected_start, result.inspected_end), (5, 14))

    def test_invalid_ranges_do_not_fetch(self):
        for start, end in ((0, 0), (5, 4), (-1, 4), (0, float("inf")), ("bad", 4), (None, 4)):
            with self.subTest(start=start, end=end):
                result = self.inspect(start=start, end=end)
                self.assertFalse(result.available)
                self.assertEqual(result.reason, "invalid_coverage")
        self.assertEqual(self.calls, [])

    def test_tool_failure_is_sanitized_and_source_artifact_cleaned(self):
        def runner(cmd, **kwargs):
            raise subprocess.TimeoutExpired("PRIVATE_MARKER", 30, output="PRIVATE_MARKER")
        result = self.inspect(end=4, runner=runner)
        self.assertFalse(result.available)
        self.assertIn("inspection_tool_timeout", result.reason)
        self.assertNotIn("PRIVATE_MARKER", str(result.as_dict()))
        self.assertTrue(all(not path.exists() for path in self.fetch_paths))


if __name__ == "__main__":
    unittest.main()
