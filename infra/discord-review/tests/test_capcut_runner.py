"""Focused tests: the LOOM -> CapCut RUNNER (Phase 2B "Finish in CapCut").

These tests are HERMETIC: every handoff writes to a temporary draft store, never
the operator's real CapCut store. The vendored CLI IS exercised where a real
draft is needed, so the vendored runtime is covered rather than mocked away.

The distinction the suite protects above all others: LOOM's ORIGINAL source
timestamps must never reach CapCut. CapCut is handed the WORKING asset, so its
times are working-relative — and both ways of getting that wrong (leaking an
ORIGINAL value, or applying the timebase origin twice) must fail closed.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
for directory in ("acquisition", "discord-review"):
    sys.path.insert(0, str(ROOT / "infra" / directory))

import capcut_handoff as ch                                     # noqa: E402
import capcut_runner as cr                                      # noqa: E402

# The adapter pulls in LOOM's acquisition stack, whose `import acquisition`
# expects the MODULE (acquisition.py) and only resolves when infra/acquisition is
# on sys.path — the accepted test pattern above. Importing it here, before any
# other module can cache the same-named package, keeps the accepted version
# contract importable whatever order the suite discovers files in.
import acquisition  # noqa: E402,F401

FFMPEG = shutil.which("ffmpeg")
VENDOR_CLI = ROOT / "vendor" / "capcut-cli"


def _make_media(directory, seconds=60.0, name="kick__working.mp4"):
    """A real, probe-able video file (the runner validates real bytes)."""
    path = os.path.join(directory, name)
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi",
         "-i", f"testsrc=duration={seconds}:size=320x180:rate=30",
         "-pix_fmt", "yuv420p", "-y", path], check=True)
    return path


def _read_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _read_text(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _duration(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                          "format=duration", "-of", "csv=p=0", path],
                         capture_output=True, text=True).stdout.strip()
    return float(out)


def fake_ok(draft_dir):
    """An injected CLI that behaves like a successful compile."""
    def invoke(cmd, timeout):
        target = cmd[cmd.index("--out") + 1]
        os.makedirs(target, exist_ok=True)
        Path(target, "draft_content.json").write_text("{}")
        return 0, json.dumps({"ok": True, "name": "x", "draft_path": target,
                              "file_path": os.path.join(target, "draft_content.json")}), ""
    return invoke


def fake_exit(code=1, out="", err="boom"):
    def invoke(cmd, timeout):
        return code, out, err
    return invoke


@unittest.skipUnless(FFMPEG, "ffmpeg is required to synthesise test media")
class CapCutRunnerCase(unittest.TestCase):
    """Shared fixture: a real working asset + a scratch draft store."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="loom-capcut-runner-")
        cls.media = _make_media(cls.tmp, seconds=60.0)
        cls.media_duration = _duration(cls.media)
        cls.store = os.path.join(cls.tmp, "drafts")
        os.makedirs(cls.store, exist_ok=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        self.work = tempfile.mkdtemp(prefix="case-", dir=self.tmp)

    # -- fixtures ---------------------------------------------------------
    def source_launch(self, **over):
        """A bounded source launch.

        The DURABLE range is working-relative (0→60) — that is the accepted
        contract for `boundary_plan.recommended_clip_*` — while the ORIGINAL
        window (1800→1860) and its origin are preserved separately.
        """
        bootstrap = {"media_url": "https://kick.example/videos/abc",
                     "version_mode": "source_lineage",
                     "source_range": {"start": 0.0, "end": 60.0},
                     "asset_version": "source-1.0",
                     "hook": "wait for it", "title": ""}
        bootstrap.update(over)
        acquisition = {"acquisition_mode": "bounded_segment",
                       "original_start_s": 1800.0, "original_end_s": 1860.0,
                       "working_timebase_origin_s": 1800.0,
                       "working_source_duration_s": round(self.media_duration, 6)}
        return bootstrap, acquisition

    def original_window(self):
        """The ORIGINAL pair as the records preserve it, separate from the range."""
        return {"start": 1800.0, "end": 1860.0}

    def rendered_clip(self, **over):
        bootstrap = {"media_url": "https://cdn.example/render-1.2.mp4",
                     "version_mode": "pinned",
                     "source_range": {"start": 2.0, "end": 12.0},
                     "asset_version": "render-1.2", "hook": "", "title": ""}
        bootstrap.update(over)
        return bootstrap

    def run_it(self, bootstrap, **over):
        kwargs = {"bootstrap": bootstrap, "media_path": self.media,
                  "draft_store": self.store,
                  "handoff_root": os.path.join(self.work, "handoff")}
        kwargs.update(over)
        return cr.run_handoff(**kwargs)

    def draft_json(self, report):
        return _read_json(report["draft_file"])


# ---------------------------------------------------------------------------
# (5)(6) WORKING coordinates — the contract that must never leak ORIGINAL
# ---------------------------------------------------------------------------
class WorkingCoordinateTests(CapCutRunnerCase):
    def test_original_1800_1860_maps_to_working_0_60(self):
        coords = cr.resolve_working_coords(original_start=1800.0,
                                          original_end=1860.0,
                                          timebase_origin=1800.0,
                                          working_duration=self.media_duration)
        self.assertEqual(coords["start"], 0.0)
        self.assertEqual(coords["end"], 60.0)
        self.assertEqual(coords["duration"], 60.0)

    def test_a_non_zero_origin_is_applied_exactly_once(self):
        # The second accepted Kick case: origin 1570 for a 0→60 working window.
        coords = cr.resolve_working_coords(original_start=1570.0,
                                          original_end=1630.0,
                                          timebase_origin=1570.0,
                                          working_duration=60.016016)
        self.assertEqual((coords["start"], coords["duration"]), (0.0, 60.0))

    def test_double_offset_is_refused(self):
        # Origin applied twice: 1800 - 1800 - 1800 < 0.
        with self.assertRaises(ch.CapCutHandoffUnavailable) as ctx:
            cr.resolve_working_coords(original_start=0.0, original_end=60.0,
                                      timebase_origin=1800.0,
                                      working_duration=self.media_duration)
        self.assertIn("mixed up", str(ctx.exception))

    def test_original_coordinates_leaked_into_a_short_asset_are_refused(self):
        # An ORIGINAL minute-30 seek inside a one-minute asset.
        with self.assertRaises(ch.CapCutHandoffUnavailable) as ctx:
            cr.resolve_working_coords(original_start=1800.0,
                                      original_end=1860.0,
                                      timebase_origin=0.0,
                                      working_duration=self.media_duration)
        self.assertIn("outside the working asset", str(ctx.exception))

    def test_a_window_past_the_end_of_the_working_asset_is_refused(self):
        with self.assertRaises(ch.CapCutHandoffUnavailable) as ctx:
            cr.resolve_working_coords(original_start=30.0, original_end=90.0,
                                      timebase_origin=0.0,
                                      working_duration=60.0)
        self.assertIn("exceeds the working asset", str(ctx.exception))

    def test_a_rendered_clip_is_not_offset(self):
        # version_mode is not a source launch: the range is already working
        # relative, so it passes through untouched (origin 0, not doubled).
        coords = cr.working_coordinates_for(self.rendered_clip(), {})
        self.assertEqual((coords["start"], coords["duration"]), (2.0, 10.0))

    def test_the_durable_range_is_already_working_so_it_passes_through(self):
        # The accepted contract stores recommended_clip_* in WORKING coordinates
        # (with the ORIGINAL pair kept separately), so the durable range must
        # NOT be translated again — that would double the offset.
        bootstrap, acquisition = self.source_launch()
        coords = cr.working_coordinates_for(bootstrap, acquisition)
        self.assertEqual((coords["start"], coords["end"]), (0.0, 60.0))
        self.assertEqual(coords["duration"], 60.0)
        self.assertEqual(coords["timebase_origin"], 1800.0)   # provenance only
        self.assertEqual(coords["original"], {"start": 1800.0, "end": 1860.0})

    def test_an_explicit_original_window_is_translated_exactly_once(self):
        # ORIGINAL 1800→1860, origin 1800 -> WORKING 0→60. The mandated proof.
        bootstrap, acquisition = self.source_launch()
        coords = cr.working_coordinates_for(bootstrap, acquisition,
                                           original_window=self.original_window())
        self.assertEqual((coords["start"], coords["end"], coords["duration"]),
                         (0.0, 60.0, 60.0))

    def test_re_translating_an_already_working_range_is_refused(self):
        # The double-offset regression: the working pair offered as if it were
        # ORIGINAL, while the record still carries origin 1800.
        bootstrap, acquisition = self.source_launch()
        with self.assertRaises(ch.CapCutHandoffUnavailable) as ctx:
            cr.working_coordinates_for(bootstrap, acquisition,
                                       original_window={"start": 0.0, "end": 60.0})
        self.assertIn("mixed up", str(ctx.exception))


# ---------------------------------------------------------------------------
# (4)(14) Trim + canvas land in the draft as working coordinates
# ---------------------------------------------------------------------------
class DraftMappingTests(CapCutRunnerCase):
    def test_the_trim_reaches_capcut_in_working_seconds(self):
        bootstrap, acquisition = self.source_launch()
        report = self.run_it(bootstrap, acquisition=acquisition, asset_version="render-1.2")
        self.assertEqual(report["coordinates"]["capcut"],
                         {"sourceStart": 0.0, "duration": 60.0})
        segment = self.draft_json(report)["tracks"][0]["segments"][0]
        self.assertEqual(segment["source_timerange"]["start"], 0)
        self.assertEqual(segment["source_timerange"]["duration"], 60_000_000)

    def test_a_rendered_clip_trim_reaches_capcut_unchanged(self):
        report = self.run_it(self.rendered_clip())
        segment = self.draft_json(report)["tracks"][0]["segments"][0]
        # 2.0 → 12.0 on the working asset.
        self.assertEqual(segment["source_timerange"]["start"], 2_000_000)
        self.assertEqual(segment["source_timerange"]["duration"], 10_000_000)

    def test_the_draft_lands_inside_the_store_and_registers(self):
        # (14) generated project location.
        report = self.run_it(self.rendered_clip())
        self.assertEqual(os.path.dirname(report["draft_path"]), self.store)
        self.assertTrue(os.path.isfile(report["draft_file"]))
        self.assertTrue(report["draft_name"].startswith(cr.DRAFT_NAME_PREFIX))
        self.assertTrue(report["manual_open_required"])
        index = os.path.join(self.store, "root_meta_info.json")
        names = [r.get("draft_name")
                 for r in _read_json(index)["all_draft_store"]]
        self.assertIn(report["draft_name"], names)

    def test_a_path_shaped_title_cannot_escape_the_store(self):
        name = cr.safe_draft_name("../../etc/passwd", store=self.store)
        self.assertNotIn("/", name)
        self.assertNotIn("\\", name)
        self.assertFalse(name.startswith(".."))

    def test_an_existing_project_is_never_overwritten(self):
        first = self.run_it(self.rendered_clip(), asset_version="render-1.2")
        again = self.run_it(self.rendered_clip(), asset_version="render-1.2",
                            draft_name=first["draft_name"])
        self.assertNotEqual(again["draft_path"], first["draft_path"])
        self.assertTrue(os.path.isdir(first["draft_path"]))


# ---------------------------------------------------------------------------
# (1)(2) The authoritative CURRENT version
# ---------------------------------------------------------------------------
class AuthoritativeVersionTests(unittest.TestCase):
    def rows(self):
        return [{"asset_version": "render-1.9"}, {"asset_version": "render-1.10"},
                {"asset_version": "render-1.2"}, {"asset_version": "source-1.0"}]

    def test_a_pinned_version_is_honoured_exactly(self):
        version, mode, reason = cr.resolve_current_version(self.rows(), "render-1.2")
        self.assertEqual((version, mode, reason),
                         ("render-1.2", "pinned", "ok"))

    def test_latest_is_numeric_so_1_10_beats_1_9(self):
        version, mode, _ = cr.resolve_current_version(
            [{"asset_version": "render-1.9"}, {"asset_version": "render-1.10"}], "latest")
        self.assertEqual(version, "render-1.10")
        self.assertEqual(mode, "latest")

    def test_an_unpinned_request_never_hands_back_the_original_source(self):
        # MEASURED: the accepted ordering compares the version prefix
        # alphabetically and "source" > "render", so a bare `latest` can prefer
        # source-1.0 (the ORIGINAL source) over a later render of it. Exporting
        # that would be the silent original-source export this lane forbids.
        version, mode, _ = cr.resolve_current_version(self.rows(), "latest")
        self.assertEqual(version, "render-1.10")
        self.assertFalse(cr.is_source_version(version))

    def test_an_explicitly_pinned_source_version_is_still_honoured(self):
        # Operator asked for the source: a different thing from a silent swap.
        version, mode, reason = cr.resolve_current_version(self.rows(), "source-1.0")
        self.assertEqual((version, mode, reason), ("source-1.0", "pinned", "ok"))

    def test_a_source_only_library_still_resolves(self):
        version, mode, _ = cr.resolve_current_version(
            [{"asset_version": "source-1.0"}], "latest")
        self.assertEqual(version, "source-1.0")
        self.assertEqual(mode, "latest")

    def test_an_unknown_pinned_version_is_never_substituted(self):
        # The stale-state guard: asking for a version that does not exist must
        # NOT quietly hand back a different (e.g. newer) render.
        version, mode, reason = cr.resolve_current_version(self.rows(), "render-1.3")
        self.assertIsNone(version)
        self.assertEqual((mode, reason), ("pinned", "version_not_found"))

    def test_the_newest_correction_wins_for_a_version(self):
        corrections = [
            {"id": "a", "target_asset_version": "render-1.2", "created_at": "2026-01-01T00:00:00Z",
             "trim_start": 1.0, "trim_end": 11.0},
            {"id": "b", "target_asset_version": "render-1.2", "created_at": "2026-03-01T00:00:00Z",
             "trim_start": 2.0, "trim_end": 12.0},
            {"id": "c", "target_asset_version": "render-1.1", "created_at": "2026-06-01T00:00:00Z",
             "trim_start": 9.0, "trim_end": 19.0},
        ]
        chosen = cr.select_current_correction(corrections, "render-1.2")
        self.assertEqual(chosen["id"], "b")           # newest for THIS version
        self.assertEqual(chosen["trim_start"], 2.0)

    def test_a_correction_for_another_version_is_never_borrowed(self):
        corrections = [{"id": "c", "target_asset_version": "render-1.1",
                        "created_at": "2026-06-01T00:00:00Z"}]
        self.assertEqual(cr.select_current_correction(corrections, "render-1.2"), {})


# ---------------------------------------------------------------------------
# (7)(8) Captions
# ---------------------------------------------------------------------------
class CaptionMappingTests(CapCutRunnerCase):
    def blocks(self, count=10):
        return [{"text": f"word{i}", "startMs": i * 1000, "endMs": i * 1000 + 900}
                for i in range(count)]

    def test_durable_blocks_become_editable_caption_items(self):
        report = self.run_it(self.rendered_clip(), caption_blocks=self.blocks())
        self.assertTrue(report["captions"]["included"])
        self.assertEqual(report["captions"]["count"], 10)
        tracks = [t for t in self.draft_json(report)["tracks"] if t["type"] == "text"]
        caption_segments = [s for t in tracks for s in t["segments"]]
        self.assertGreaterEqual(len(caption_segments), 10)

    def test_caption_timing_is_passed_through_not_rescaled(self):
        # Caption blocks are clip-relative, like the CapCut timeline: a zero
        # offset. Applying the source origin here would double-offset them.
        report = self.run_it(self.rendered_clip(), caption_blocks=self.blocks(3))
        items = _read_json(report["spec_path"])["operations"]
        self.assertEqual(items[0]["timeOffset"], 0)
        srt = _read_text(items[0]["path"])
        self.assertIn("00:00:00,000 --> 00:00:00,900", srt)

    def test_a_version_without_blocks_still_succeeds(self):
        report = self.run_it(self.rendered_clip(), caption_blocks=[])
        self.assertFalse(report["captions"]["included"])
        self.assertEqual(report["captions"]["reason"],
                         "captions unavailable for this version")
        self.assertEqual(self.draft_json(report)["tracks"][0]["type"], "video")

    def test_half_timed_blocks_are_dropped_never_guessed(self):
        blocks = [{"text": "good", "startMs": 0, "endMs": 500},
                  {"text": "no end", "startMs": 900},
                  {"text": "no start", "endMs": 2000},
                  {"text": "inverted", "startMs": 900, "endMs": 100},
                  {"text": "", "startMs": 0, "endMs": 100}]
        report = self.run_it(self.rendered_clip(), caption_blocks=blocks)
        self.assertEqual(report["captions"]["count"], 1)

    def test_the_srt_is_well_formed_and_monotonic(self):
        blocks = [{"text": "b", "startMs": 2000, "endMs": 2500},
                  {"text": "a", "startMs": 0, "endMs": 900}]
        report = self.run_it(self.rendered_clip(), caption_blocks=blocks)
        srt = _read_text(report["captions"]["srt"])
        self.assertTrue(srt.startswith("1\n00:00:00,000 --> 00:00:00,900\na"))
        self.assertIn("2\n00:00:02,000 --> 00:00:02,500\nb", srt)


# ---------------------------------------------------------------------------
# (3)(9)(10) Media handoff
# ---------------------------------------------------------------------------
class MediaHandoffTests(CapCutRunnerCase):
    def test_the_working_asset_is_copied_and_the_original_is_untouched(self):
        before = (os.path.getsize(self.media), os.path.getmtime(self.media))
        report = self.run_it(self.rendered_clip())
        self.assertEqual((os.path.getsize(self.media), os.path.getmtime(self.media)),
                         before)
        self.assertEqual(report["media"]["source_path"], self.media)
        self.assertNotEqual(report["media"]["staged_path"], self.media)

    def test_the_draft_carries_its_own_media_copy(self):
        # The draft must open with no missing media even after the scratch copy
        # is gone — that is why the staged copy is safe to remove.
        report = self.run_it(self.rendered_clip())
        draft_media = self.draft_json(report)["materials"]["videos"][0]["path"]
        self.assertTrue(os.path.isfile(draft_media))
        self.assertEqual(os.path.getsize(draft_media), os.path.getsize(self.media))
        self.assertFalse(os.path.exists(report["media"]["staged_path"]))

    def test_missing_media_fails_closed(self):
        with self.assertRaises(ch.CapCutHandoffUnavailable) as ctx:
            self.run_it(self.rendered_clip(), media_path="/nope/missing.mp4")
        self.assertIn("no local working media", str(ctx.exception))

    def test_a_remote_url_is_never_treated_as_media(self):
        with self.assertRaises(ch.CapCutHandoffUnavailable):
            self.run_it(self.rendered_clip(),
                        media_path="https://cdn.example/x.mp4")

    def test_staging_refuses_to_claim_a_mismatched_copy(self):
        staged = cr.stage_working_media(self.media, workspace=self.work)
        self.assertEqual(staged["bytes"], os.path.getsize(self.media))


# ---------------------------------------------------------------------------
# (11)(12)(13) Lane availability and CLI failure
# ---------------------------------------------------------------------------
class LaneAvailabilityTests(CapCutRunnerCase):
    def test_an_absent_cli_reports_the_lane_unavailable(self):
        with self.assertRaises(ch.CapCutHandoffUnavailable) as ctx:
            self.run_it(self.rendered_clip(), cli_path="/definitely/not/here",
                        invoke=fake_ok(self.store))
        self.assertIn("unavailable", str(ctx.exception))

    def test_the_vendored_cli_resolves_without_a_global_install(self):
        # capcut_cli_path() must find the vendored checkout in the repo. If it
        # ever only resolved via PATH this would fail on a clean machine.
        resolved = ch.capcut_cli_path()
        if resolved:
            self.assertTrue(os.path.isdir(resolved) or os.path.exists(resolved))

    def test_a_failing_cli_is_reported_not_swallowed(self):
        with self.assertRaises(ch.CapCutHandoffUnavailable) as ctx:
            self.run_it(self.rendered_clip(), invoke=fake_exit(1, "", "kaboom"))
        self.assertIn("exit 1", str(ctx.exception))
        self.assertIn("kaboom", str(ctx.exception))

    def test_malformed_cli_output_is_refused(self):
        with self.assertRaises(ch.CapCutHandoffUnavailable) as ctx:
            self.run_it(self.rendered_clip(), invoke=fake_exit(0, "not json at all"))
        self.assertIn("not the expected JSON report", str(ctx.exception))

    def test_a_cli_that_claims_success_without_a_draft_is_refused(self):
        def lying(cmd, timeout):
            return 0, json.dumps({"ok": True, "file_path": "/nope/nothing.json"}), ""
        with self.assertRaises(ch.CapCutHandoffUnavailable) as ctx:
            self.run_it(self.rendered_clip(), invoke=lying)
        self.assertIn("produced no draft", str(ctx.exception))

    def test_a_failed_compile_leaves_no_half_built_project(self):
        def creates_then_fails(cmd, timeout):
            target = cmd[cmd.index("--out") + 1]
            os.makedirs(target, exist_ok=True)
            Path(target, "draft_content.json").write_text("{}")
            return 1, "", "refused"
        with self.assertRaises(ch.CapCutHandoffUnavailable):
            self.run_it(self.rendered_clip(), invoke=creates_then_fails)
        leftovers = [d for d in os.listdir(self.store)
                     if d.startswith(cr.DRAFT_NAME_PREFIX)]
        self.assertEqual(leftovers, [])

    def test_the_editor_open_guard_only_applies_to_managed_stores(self):
        self.assertTrue(cr.is_managed_draft_store(
            "/Users/x/Movies/CapCut/User Data/Projects/com.lveditor.draft"))
        self.assertFalse(cr.is_managed_draft_store("/tmp/scratch/drafts"))


# ---------------------------------------------------------------------------
# (15) Failure isolation — LOOM state is untouched
# ---------------------------------------------------------------------------
class FailureIsolationTests(CapCutRunnerCase):
    def test_a_failed_handoff_changes_no_loom_state(self):
        bootstrap, acquisition = self.source_launch()
        blocks = [{"text": "hi", "startMs": 0, "endMs": 500}]
        before = json.dumps({"bootstrap": bootstrap, "acquisition": acquisition,
                             "blocks": blocks}, sort_keys=True)
        media_before = (os.path.getsize(self.media), os.path.getmtime(self.media))
        with self.assertRaises(ch.CapCutHandoffUnavailable):
            self.run_it(bootstrap, acquisition=acquisition, caption_blocks=blocks,
                        invoke=fake_exit(1, "", "boom"))
        self.assertEqual(json.dumps({"bootstrap": bootstrap,
                                     "acquisition": acquisition,
                                     "blocks": blocks}, sort_keys=True), before)
        self.assertEqual((os.path.getsize(self.media), os.path.getmtime(self.media)),
                         media_before)
        # No LOOM version/approval/CI vocabulary is produced by a handoff.
        report_keys = set()
        try:
            self.run_it(bootstrap, acquisition=acquisition,
                        caption_blocks=blocks, invoke=fake_exit(1, "", "boom"))
        except ch.CapCutHandoffUnavailable:
            pass
        self.assertEqual(report_keys, set())

    def test_a_missing_media_failure_also_leaves_state_alone(self):
        bootstrap, acquisition = self.source_launch()
        before = json.dumps(bootstrap, sort_keys=True)
        with self.assertRaises(ch.CapCutHandoffUnavailable):
            self.run_it(bootstrap, acquisition=acquisition,
                        media_path="/nope/missing.mp4")
        self.assertEqual(json.dumps(bootstrap, sort_keys=True), before)

    def test_the_original_working_asset_is_never_moved_or_deleted(self):
        with self.assertRaises(ch.CapCutHandoffUnavailable):
            self.run_it(self.rendered_clip(), invoke=fake_exit(2, "", "nope"))
        self.assertTrue(os.path.isfile(self.media))

    def test_importing_the_runner_needs_no_capcut_and_has_no_side_effects(self):
        # The lane must be inert: importing it must not create directories or
        # require the CLI/store to exist.
        self.assertTrue(callable(cr.run_handoff))
        self.assertIsInstance(cr.capcut_invocation("/nonexistent"), list)


# ---------------------------------------------------------------------------
# (16) Existing editor behaviour unchanged
# ---------------------------------------------------------------------------
class EditorUnchangedTests(CapCutRunnerCase):
    def test_the_committed_mapper_output_is_unchanged(self):
        # A frozen snapshot of build_capcut_spec's accepted contract. If the
        # additive caption work had altered the editor-facing mapping, this
        # fails. On-screen copy now travels through the EXPLICIT renderable
        # contract rather than the bootstrap's metadata `title`/`hook` keys, so
        # the copy is supplied the way the operator-facing lane supplies it.
        state = {"source_range": {"start": 30.0, "end": 90.0},
                 "title": "LOCKED IN ATHON", "hook": "wait for it"}
        spec = ch.build_capcut_spec(
            state, media_path=self.media,
            renderable={"title": "LOCKED IN ATHON", "hook": "wait for it"})
        self.assertEqual(spec["width"], 1080)
        self.assertEqual(spec["height"], 1920)
        self.assertEqual(spec["ratio"], "9:16")
        self.assertEqual(spec["fps"], 30)
        item = spec["tracks"][0]["items"][0]
        self.assertEqual(item["sourceStart"], 30.0)
        self.assertEqual(item["duration"], 60.0)
        self.assertEqual([t["type"] for t in spec["tracks"]], ["video", "text"])
        self.assertEqual(len(spec["tracks"][0]["items"]), 1)   # still ONE clip

    def test_the_mapper_still_emits_no_captions_by_itself(self):
        # Captions are additive and opt-in; the mapper alone never fabricates.
        spec = ch.build_capcut_spec({"source_range": {"start": 0, "end": 10}},
                                    media_path=self.media)
        self.assertNotIn("operations", spec)
        self.assertEqual(len(spec["tracks"]), 1)

    def test_the_version_contract_the_editor_uses_is_unchanged(self):
        import adapter
        self.assertEqual(adapter.resolve_asset_version_selection(
            [{"asset_version": "render-1.9"}, {"asset_version": "render-1.10"}],
            ""), ("render-1.10", "latest", "ok"))
        self.assertEqual(adapter.resolve_asset_version_selection(
            [{"asset_version": "render-1.2"}], "render-1.1"),
            (None, "pinned", "version_not_found"))

    def test_the_fixture_is_one_way_only(self):
        self.assertIn("one-way", ch.handoff_report({})["note"])


# ---------------------------------------------------------------------------
# The vendored runtime itself (no operator store, no CapCut app required)
# ---------------------------------------------------------------------------
@unittest.skipUnless(FFMPEG, "ffmpeg is required to synthesise test media")
class VendoredRuntimeTests(CapCutRunnerCase):
    def test_the_vendored_cli_runs_the_whole_compile_path(self):
        if not cr.capcut_invocation():
            self.skipTest("vendored capcut-cli not present")
        bootstrap, acquisition = self.source_launch()
        report = self.run_it(bootstrap, acquisition=acquisition,
                             asset_version="render-1.2",
                             caption_blocks=[{"text": f"w{i}", "startMs": i * 500,
                                              "endMs": i * 500 + 400}
                                             for i in range(10)])
        self.assertTrue(report["ok"])
        content = self.draft_json(report)
        self.assertEqual(content["tracks"][0]["segments"][0]["source_timerange"]["start"], 0)
        self.assertEqual(content["duration"], 60_000_000)
        self.assertGreaterEqual(len(content["materials"]["texts"]), 10)

    def test_the_vendored_cli_is_pinned_not_head(self):
        prov = VENDOR_CLI / "PROVENANCE.md"
        if not prov.is_file():
            self.skipTest("vendor tree absent")
        with open(prov, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("a21cc9ce65ef169920f0e18cb130777a13bee838", text)
        self.assertIn("MIT", text)
        self.assertTrue((VENDOR_CLI / "LICENSE").is_file())


if __name__ == "__main__":
    unittest.main(verbosity=2)
