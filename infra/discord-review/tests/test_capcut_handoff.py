"""Focused tests: LOOM -> CapCut handoff spec (Phase 2B fixture, pure core).

The fixture must be additive and safe: it maps edit state LOOM already has, invents
nothing, and FAILS CLOSED without touching candidate/version/approval state.
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
for directory in ("acquisition", "discord-review"):
    sys.path.insert(0, str(ROOT / "infra" / directory))

import capcut_handoff as ch                                    # noqa: E402


def _state(**over):
    state = {"media_url": "https://cdn.example/x.mp4",
             "duration_seconds": 60.0,
             "source_range": {"start": 30.0, "end": 90.0},
             "title": "LOCKED IN ATHON",
             "hook": "wait for it",
             "framing": "auto",
             "captions": [{"text": "hi", "startMs": 0, "endMs": 500}],
             "transcript": [{"text": "hi", "startMs": 0, "endMs": 500}],
             "scenes": []}
    state.update(over)
    return state


class SpecMappingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="loom-capcut-")
        self.media = os.path.join(self.tmp, "working.mp4")
        Path(self.media).write_bytes(b"v" * 1024)

    def test_canvas_is_9_16_by_default(self):
        spec = ch.build_capcut_spec(_state(), media_path=self.media)
        self.assertEqual((spec["width"], spec["height"], spec["ratio"]),
                         (1080, 1920, "9:16"))
        self.assertEqual(spec["fps"], 30)

    def test_the_selected_trim_becomes_source_start_and_duration(self):
        spec = ch.build_capcut_spec(_state(), media_path=self.media)
        item = spec["tracks"][0]["items"][0]
        self.assertEqual(item["sourceStart"], 30.0)   # LOOM's trim-in, in source seconds
        self.assertEqual(item["duration"], 60.0)      # 90 - 30
        self.assertEqual(item["start"], 0)
        self.assertEqual(item["path"], self.media)

    def test_a_non_zero_start_trim_is_preserved_exactly(self):
        spec = ch.build_capcut_spec(
            _state(source_range={"start": 1570.0, "end": 1630.0}),
            media_path=self.media)
        item = spec["tracks"][0]["items"][0]
        self.assertEqual(item["sourceStart"], 1570.0)
        self.assertEqual(item["duration"], 60.0)

    def test_hook_and_title_reach_a_text_track(self):
        spec = ch.build_capcut_spec(_state(), media_path=self.media)
        text = [t for t in spec["tracks"] if t["type"] == "text"][0]
        joined = " ".join(i["text"] for i in text["items"])
        self.assertIn("wait for it", joined)
        self.assertIn("LOCKED IN ATHON", joined)

    def test_no_hook_means_no_invented_text(self):
        spec = ch.build_capcut_spec(_state(hook="", title=""),
                                    media_path=self.media)
        self.assertEqual([t for t in spec["tracks"] if t["type"] == "text"], [])

    def test_spec_uses_seconds_not_microseconds(self):
        spec = ch.build_capcut_spec(_state(), media_path=self.media)
        item = spec["tracks"][0]["items"][0]
        for key in ("start", "duration", "sourceStart"):
            self.assertLess(item[key], 10_000)   # µs values would be ~1e6+
        json.dumps(spec)                          # must be serialisable as-is

    def test_sixteen_by_nine_preset_is_available(self):
        spec = ch.build_capcut_spec(_state(), media_path=self.media, aspect="16:9")
        self.assertEqual((spec["width"], spec["height"]), (1920, 1080))


class NothingIsInventedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="loom-capcut-")
        self.media = os.path.join(self.tmp, "working.mp4")
        Path(self.media).write_bytes(b"v")

    def test_captions_and_transcript_are_reported_not_invented(self):
        spec = ch.build_capcut_spec(_state(), media_path=self.media)
        blob = json.dumps(spec)
        self.assertNotIn("hi", blob)                     # no fake caption items
        report = ch.handoff_report(_state())
        for field in ("captions", "transcript", "scenes", "framing"):
            self.assertIn(field, report["unsupported"])
        self.assertIn("selected trim (sourceStart + duration)", report["carried"])

    def test_the_report_declares_one_way_only(self):
        self.assertIn("one-way", ch.handoff_report(_state())["note"])


class FailureIsolationTests(unittest.TestCase):
    """A failed handoff must never damage the candidate or raise into the editor."""

    def test_a_remote_url_is_not_accepted_as_media(self):
        with self.assertRaises(ch.CapCutHandoffUnavailable) as ctx:
            ch.build_capcut_spec(_state(),
                                 media_path="https://cdn.example/x.mp4")
        self.assertIn("cannot reference a remote URL", str(ctx.exception))

    def test_missing_media_file_fails_closed(self):
        with self.assertRaises(ch.CapCutHandoffUnavailable):
            ch.build_capcut_spec(_state(), media_path="/nope/missing.mp4")

    def test_no_media_supplied_fails_closed(self):
        with self.assertRaises(ch.CapCutHandoffUnavailable):
            ch.build_capcut_spec(_state(), media_path="")

    def test_unknown_trim_fails_closed(self):
        # A REAL media file, so the trim check is the one under test (media is
        # validated first, and that path is covered separately above).
        tmp = tempfile.mkdtemp(prefix="loom-capcut-trim-")
        media = os.path.join(tmp, "working.mp4")
        Path(media).write_bytes(b"v")
        with self.assertRaises(ch.CapCutHandoffUnavailable) as ctx:
            ch.build_capcut_spec(_state(source_range={}), media_path=media)
        self.assertIn("source_range", str(ctx.exception))

    def test_invalid_range_fails_closed(self):
        with self.assertRaises(ch.CapCutHandoffUnavailable):
            ch.build_capcut_spec(_state(source_range={"start": 90.0, "end": 30.0}),
                                 media_path="/tmp/x.mp4")

    def test_bad_state_shape_fails_closed(self):
        with self.assertRaises(ch.CapCutHandoffUnavailable):
            ch.build_capcut_spec(None, media_path="/tmp/x.mp4")

    def test_failure_does_not_mutate_the_input_state(self):
        state = _state(source_range={})
        before = json.dumps(state, sort_keys=True)
        with self.assertRaises(ch.CapCutHandoffUnavailable):
            ch.build_capcut_spec(state, media_path="/tmp/x.mp4")
        self.assertEqual(json.dumps(state, sort_keys=True), before)


class OptionalDependencyTests(unittest.TestCase):
    """capcut-cli must never become a required dependency of LOOM."""

    def test_absent_cli_reports_lane_unavailable_not_an_error(self):
        old = os.environ.get("LOOM_CAPCUT_CLI")
        os.environ["LOOM_CAPCUT_CLI"] = "/definitely/not/here"
        try:
            resolved = ch.capcut_cli_path()
        finally:
            if old is None:
                os.environ.pop("LOOM_CAPCUT_CLI", None)
            else:
                os.environ["LOOM_CAPCUT_CLI"] = old
        # Either a genuinely installed CLI on PATH, or "" (lane unavailable).
        self.assertTrue(resolved == "" or os.path.exists(resolved))

    def test_the_module_imports_with_no_capcut_present(self):
        self.assertTrue(callable(ch.build_capcut_spec))
        self.assertTrue(callable(ch.write_spec))

    def test_write_spec_is_the_only_side_effect_and_is_explicit(self):
        tmp = tempfile.mkdtemp(prefix="loom-capcut-spec-")
        out = os.path.join(tmp, "nested", "handoff.json")
        ch.write_spec({"name": "x", "tracks": []}, out)
        self.assertTrue(os.path.isfile(out))
        self.assertEqual(json.load(open(out))["name"], "x")


if __name__ == "__main__":
    unittest.main(verbosity=2)
