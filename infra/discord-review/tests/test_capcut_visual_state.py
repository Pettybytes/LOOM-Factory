"""Focused tests: the CapCut handoff carries LOOM's ACTUAL visual composition.

The failure these protect against is a handoff that opens in CapCut showing the
raw/source framing instead of the composition LOOM rendered — or one that shows
a stale composition belonging to a different version. So the tests cover three
things: the persisted canvas survives (including when it is NOT 9:16), the
resolved composition is mapped without being re-derived, and anything the lane
cannot carry faithfully is REFUSED rather than flattened.

Offline by construction: the vendored CLI is never executed (an injected runner
stands in for it) and the draft store is a temp directory, so nothing is written
into the operator's real CapCut library.
"""

import json
import os
import sys
import tempfile
import unittest
import unittest.mock as mock
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
for _p in (str(_ROOT / "infra" / "discord-review"), str(_ROOT / "infra"),
           str(_ROOT / "infra" / "acquisition")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# The adapter reads its configuration at import time; inert values keep the
# import side-effect free (no network is made by importing it).
for _k, _v in (("LOOM_DISCORD_APP_ID", "test-inert"),
               ("LOOM_DISCORD_PUBLIC_KEY", "test-inert"),
               ("LOOM_DISCORD_BOT_TOKEN", "test-inert"),
               ("LOOM_SUPABASE_URL", "https://test.supabase.co"),
               ("LOOM_SUPABASE_SERVICE_ROLE_KEY", "test-inert")):
    os.environ.setdefault(_k, _v)

import acquisition  # noqa: E402,F401  (MUST precede adapter in this harness)
import adapter  # noqa: E402
import capcut_handoff as ch  # noqa: E402
import capcut_runner as cr  # noqa: E402
import visual_state as vs  # noqa: E402

SOURCE = {"width": 1920, "height": 1080}
BASIS = {"canvas": "output_pixels", "crop": "source_pixels",
         "crop_override": "source_width_fraction",
         "position": "canvas_centre_to_edge"}


def _media(tmp_path, name="working.mp4", data=b"v" * 4096):
    path = str(tmp_path / name)
    with open(path, "wb") as fh:
        fh.write(data)
    return path


def _crop_scene(start=0.0, end=5.0, points=None, strategy="TRACK", manual=False):
    return {"start": start, "end": end, "start_frame": int(start * 30),
            "end_frame": int(end * 30), "strategy": strategy, "manual": manual,
            "composition": {
                "representation": "crop_window", "basis": "source_pixels",
                "change_points": points if points is not None else [
                    {"time": start, "x": 657, "width": 606, "height": 1080, "y": 0}]}}


def _general_scene(start=0.0, end=5.0, content_height=806, strategy="GENERAL"):
    return {"start": start, "end": end, "start_frame": int(start * 30),
            "end_frame": int(end * 30), "strategy": strategy, "manual": False,
            "composition": {"representation": "general", "mode": "general",
                            "content_height": content_height,
                            "placement": "center",
                            "overflow": "canvas_clipped",
                            "background": "blurred_cover",
                            "background_blur_sigma": 12}}


def _split_scene(start=0.0, end=5.0):
    return {"start": start, "end": end, "start_frame": 0, "end_frame": 150,
            "strategy": "SPLIT", "manual": False,
            "composition": {"representation": "split", "basis": "source_pixels",
                            "top": [200.0, 300.0], "bottom": [900.0, 300.0]}}


def _block(media_path, *, scenes, width=1080, height=1920, ratio="9:16",
           fmt="vertical", baked=True, version=""):
    return {"schema": vs.SCHEMA, "resolution": "resolved",
            "output_baked": baked,
            "ownership": {"output_filename": os.path.basename(media_path),
                          "sha256": vs.digest_of(media_path),
                          "asset_version": version},
            "canvas": {"format": fmt, "ratio": ratio, "width": width,
                       "height": height, "fps": 30.0},
            "coordinate_basis": dict(BASIS), "source": dict(SOURCE),
            "scenes": scenes}


def _write_sidecar(media_path, block):
    with open(media_path + ".layout.json", "w", encoding="utf-8") as fh:
        json.dump({"ranges": [{"start": 0.0, "end": 5.0, "layout": "track"}],
                   "visual_state": block}, fh)


class _Case(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="loom-vs-"))
        self.media = _media(self.tmp)
        self.store = self.tmp / "store"
        self.store.mkdir()
        self.handoff_root = self.tmp / "handoff"
        cli = self.tmp / "cli"
        (cli / "dist").mkdir(parents=True)
        (cli / "dist" / "index.js").write_text("//")
        self.cli = str(cli)

    def _sidecar(self, **over):
        _write_sidecar(self.media, _block(self.media, **over))

    def _fake_cli(self):
        calls = []

        def invoke(cmd, timeout):
            calls.append(cmd)
            if "compile" in cmd:
                out = cmd[cmd.index("--out") + 1]
                os.makedirs(out, exist_ok=True)
                path = os.path.join(out, "draft_content.json")
                with open(path, "w") as fh:
                    fh.write("{}")
                return 0, json.dumps({"ok": True, "file_path": path, "refs": {
                    "scene0": "seg-0", "scene1": "seg-1", "clip": "seg-clip", "caption_seed": "seg-caption"}}), ""
            return 0, json.dumps({"ok": True, "in_sync": True}), ""

        return calls, invoke

    def _spec(self, bootstrap=None, **kwargs):
        """Build a spec through the real mapper with a temp draft store."""
        record = _media(self.tmp, "recorded.mp4")
        state = kwargs.pop("state", None)
        plan = kwargs.pop("composition", None)
        canvas = kwargs.pop("canvas", None)
        return ch.build_capcut_spec(
            bootstrap or {"source_range": {"start": 0.0, "end": 5.0}},
            media_path=record, aspect=kwargs.pop("aspect", None),
            renderable=kwargs.pop("renderable", None), canvas=canvas,
            composition=plan, visual_state=state, **kwargs)


class CanvasIsThePersistedTruthTests(_Case):
    def _mapped(self, **over):
        self._sidecar(scenes=[_crop_scene()], **over)
        state = vs.load(self.media)
        plan = vs.plan_composition(state, canvas=vs.canvas(state),
                                   source=state["source"])
        return state, self._spec(state=state, canvas=vs.canvas(state),
                                 composition=plan)

    def test_vertical_canvas_is_persisted_and_mapped(self):
        state, spec = self._mapped()
        self.assertEqual((spec["width"], spec["height"], spec["ratio"]),
                         (1080, 1920, "9:16"))
        self.assertEqual(spec["canvas_source"], ch.CANVAS_SOURCE_PERSISTED)

    def test_square_canvas_is_persisted_and_mapped(self):
        self._sidecar(scenes=[_crop_scene(points=[
            {"time": 0.0, "x": 420, "width": 1080, "height": 1080, "y": 0}])],
            width=1080, height=1080, ratio="1:1", fmt="square")
        state = vs.load(self.media)
        plan = vs.plan_composition(state, canvas=vs.canvas(state),
                                   source=state["source"])
        spec = self._spec(state=state, canvas=vs.canvas(state), composition=plan)
        self.assertEqual((spec["width"], spec["height"], spec["ratio"]),
                         (1080, 1080, "1:1"))
        item = spec["tracks"][0]["items"][0]
        self.assertAlmostEqual(item["scale"], 1.0, places=5)
        self.assertAlmostEqual(item["x"], 0.0, places=5)

    def test_horizontal_canvas_is_persisted_and_mapped(self):
        self._sidecar(scenes=[_crop_scene(points=[
            {"time": 0.0, "x": 0, "width": 1920, "height": 1080, "y": 0}])],
            width=1920, height=1080, ratio="16:9", fmt="horizontal")
        state = vs.load(self.media)
        plan = vs.plan_composition(state, canvas=vs.canvas(state),
                                   source=state["source"])
        spec = self._spec(state=state, canvas=vs.canvas(state), composition=plan)
        self.assertEqual((spec["width"], spec["height"], spec["ratio"]),
                         (1920, 1080, "16:9"))
        self.assertAlmostEqual(spec["tracks"][0]["items"][0]["scale"], 1.0,
                               places=5)

    def test_the_old_hardcoded_9_16_no_longer_overrides_the_persisted_format(self):
        # The mapper previously forced 9:16 unconditionally; a square render must
        # survive even when the legacy default is passed in explicitly.
        self._sidecar(scenes=[_crop_scene(points=[
            {"time": 0.0, "x": 420, "width": 1080, "height": 1080, "y": 0}])],
            width=1080, height=1080, ratio="1:1", fmt="square")
        state = vs.load(self.media)
        plan = vs.plan_composition(state, canvas=vs.canvas(state),
                                   source=state["source"])
        spec = self._spec(state=state, canvas=vs.canvas(state), composition=plan,
                          aspect="9:16", fps=30)
        self.assertEqual(spec["ratio"], "1:1")
        self.assertEqual(spec["height"], 1080)

    def test_a_legacy_asset_gets_a_labelled_fallback_canvas(self):
        spec = self._spec()
        self.assertEqual(spec["ratio"], "9:16")
        self.assertEqual(spec["canvas_source"], ch.CANVAS_SOURCE_LEGACY)

    def test_an_unresolved_state_without_a_canvas_is_not_invented(self):
        self._sidecar(scenes=[_crop_scene()], width=0, height=0)
        state = vs.load(self.media)
        self.assertIsNone(vs.canvas(state))


class CompositionIsMappedNotRederivedTests(_Case):
    def test_auto_crop_is_mapped_from_the_persisted_geometry(self):
        self._sidecar(scenes=[_crop_scene()], baked=False)
        state = vs.load(self.media)
        plan = vs.plan_composition(state, canvas=vs.canvas(state),
                                   source=state["source"])
        item = plan["items"][0]
        # scale is canvas width / crop width, straight from the persisted window.
        self.assertAlmostEqual(item["scale"], 1080.0 / 606.0, places=6)
        # A window centred in the source needs no displacement.
        self.assertAlmostEqual(item["x"], 0.0, places=5)

    def test_mapping_uses_the_persisted_numbers_not_a_recalculation(self):
        self._sidecar(scenes=[_crop_scene()], baked=False)
        state = vs.load(self.media)
        # Move the window in the PERSISTED state only; the output must follow it.
        state["scenes"][0]["composition"]["change_points"][0]["x"] = 700
        plan = vs.plan_composition(state, canvas=vs.canvas(state),
                                   source=state["source"])
        expected_dx = -(700 + 606 / 2.0 - 960) * (1080.0 / 606.0)
        self.assertAlmostEqual(plan["items"][0]["x"], expected_dx / (1080 / 2.0),
                               places=5)

    def test_the_legacy_layout_label_never_drives_the_composition(self):
        # A bogus/incoherent legacy label must not change the mapped geometry.
        self._sidecar(scenes=[_crop_scene()], baked=False)
        with open(self.media + ".layout.json") as fh:
            payload = json.load(fh)
        payload["ranges"] = [{"start": 0.0, "end": 5.0, "layout": "screen_record"}]
        with open(self.media + ".layout.json", "w") as fh:
            json.dump(payload, fh)
        state = vs.load(self.media)
        plan = vs.plan_composition(state, canvas=vs.canvas(state),
                                   source=state["source"])
        self.assertEqual(plan["scenes"][0]["representation"], "static_crop")

    def test_a_baked_output_is_handed_as_one_identity_item(self):
        # The render's output already contains the composition: applying the
        # recorded crop again would crop it twice.
        self._sidecar(scenes=[_crop_scene()], baked=True)
        state = vs.load(self.media)
        plan = vs.plan_composition(state, canvas=vs.canvas(state),
                                   source=state["source"], baked_identity=True)
        self.assertEqual(len(plan["items"]), 1)
        self.assertNotIn("scale", plan["items"][0])
        self.assertNotIn("x", plan["items"][0])
        self.assertEqual(plan["scenes"][0]["representation"], "already_baked")

    def test_full_stays_a_semantic_general_composition_not_a_crop(self):
        self._sidecar(scenes=[_general_scene()], baked=False)
        state = vs.load(self.media)
        plan = vs.plan_composition(state, canvas=vs.canvas(state),
                                   source=state["source"])
        item = plan["items"][0]
        self.assertAlmostEqual(item["scale"], 806.0 / 1080.0, places=6)
        # Centred foreground over a bed — never expressed as a crop window.
        self.assertNotIn("crop", json.dumps(item))
        self.assertEqual(plan["lane_ops"][0]["op"], "bg-blur")
        self.assertEqual(plan["lane_ops"][0]["renderer_blur_sigma"], 12)
        self.assertEqual(plan["scenes"][0]["representation"], "general")
        spec = self._spec(state=state, canvas=vs.canvas(state), composition=plan)
        self.assertEqual(spec["lane_operations"][0]["op"], "bg-blur")

    def test_track_stays_dynamic_and_keyframed(self):
        self._sidecar(scenes=[_crop_scene(points=[
            {"time": 0.0, "x": 657, "width": 606, "height": 1080, "y": 0},
            {"time": 1.5, "x": 700, "width": 606, "height": 1080, "y": 0},
            {"time": 3.0, "x": 600, "width": 606, "height": 1080, "y": 0}])],
            baked=False)
        state = vs.load(self.media)
        plan = vs.plan_composition(state, canvas=vs.canvas(state),
                                   source=state["source"])
        moves = [k for k in plan["keyframes"] if k["property"] == "position_x"]
        self.assertEqual(len(moves), 3)                       # one per change-point
        self.assertEqual([m["time"] for m in moves], [0.0, 1.5, 3.0])
        self.assertNotEqual(moves[0]["value"], moves[1]["value"])
        spec = self._spec(state=state, canvas=vs.canvas(state), composition=plan)
        operations = spec["operations"]
        self.assertTrue(all(op["op"] == "keyframe" for op in operations))
        self.assertEqual(len(operations), 3)

    def test_a_punch_in_keeps_its_scale_moves(self):
        self._sidecar(scenes=[_crop_scene(points=[
            {"time": 0.0, "x": 657, "width": 606, "height": 1080, "y": 0},
            {"time": 2.0, "x": 657, "width": 500, "height": 890, "y": 0}])],
            baked=False)
        state = vs.load(self.media)
        plan = vs.plan_composition(state, canvas=vs.canvas(state),
                                   source=state["source"])
        scales = [k for k in plan["keyframes"] if k["property"] == "uniform_scale"]
        self.assertEqual(len(scales), 2)

    def test_an_unsupported_composition_is_refused_not_flattened(self):
        self._sidecar(scenes=[_split_scene()], baked=False)
        state = vs.load(self.media)
        with self.assertRaises(vs.UnrepresentableVisualState) as ctx:
            vs.plan_composition(state, canvas=vs.canvas(state),
                                source=state["source"])
        self.assertIn("SPLIT", str(ctx.exception))

    def test_a_crop_window_that_cannot_fill_the_canvas_is_refused(self):
        self._sidecar(scenes=[_crop_scene(points=[
            {"time": 0.0, "x": 0, "width": 400, "height": 1080, "y": 0}])],
            baked=False)
        state = vs.load(self.media)
        with self.assertRaises(vs.UnrepresentableVisualState) as ctx:
            vs.plan_composition(state, canvas=vs.canvas(state),
                                source=state["source"])
        self.assertIn("uniform scale", str(ctx.exception))

    def test_a_track_scene_without_change_points_is_refused(self):
        scene = _crop_scene()
        scene["composition"]["change_points"] = []
        self._sidecar(scenes=[scene], baked=False)
        state = vs.load(self.media)
        with self.assertRaises(vs.UnrepresentableVisualState) as ctx:
            vs.plan_composition(state, canvas=vs.canvas(state),
                                source=state["source"])
        self.assertIn("change-points", str(ctx.exception))


class OwnershipTests(_Case):
    def test_a_stale_block_from_another_asset_is_refused(self):
        self._sidecar(scenes=[_crop_scene()])
        state = vs.load(self.media)
        other = _media(self.tmp, "other.mp4", b"different bytes")
        verdict = vs.assess(state, media_path=other)
        self.assertFalse(verdict["usable"])
        self.assertEqual(verdict["reason"], "asset_identity_mismatch")

    def test_a_wrong_version_is_refused(self):
        self._sidecar(scenes=[_crop_scene()], version="render-1.3")
        state = vs.load(self.media)
        verdict = vs.assess(state, media_path=self.media, asset_version="render-1.4")
        self.assertFalse(verdict["usable"])
        self.assertEqual(verdict["reason"], "asset_version_mismatch")

    def test_a_valid_block_is_usable(self):
        self._sidecar(scenes=[_crop_scene()])
        state = vs.load(self.media)
        self.assertTrue(vs.assess(state, media_path=self.media)["usable"])

    def test_an_unresolved_block_is_reported_with_its_reason(self):
        block = _block(self.media, scenes=[_crop_scene()])
        block["resolution"] = vs.RESOLVED
        carried = {**block, "resolution": "unresolved",
                   "unresolved_reason": "recut_changed_output_identity",
                   "ownership": {}}
        _write_sidecar(self.media, carried)
        state = vs.load(self.media)
        verdict = vs.assess(state, media_path=self.media)
        self.assertFalse(verdict["usable"])
        self.assertEqual(verdict["reason"], "recut_changed_output_identity")

    def test_legacy_sidecars_state_no_composition(self):
        with open(self.media + ".layout.json", "w") as fh:
            json.dump({"ranges": [{"start": 0.0, "end": 5.0, "layout": "track"}]}, fh)
        self.assertIsNone(vs.load(self.media))
        self.assertEqual(vs.assess(None)["reason"], "no_resolved_state")


class HandoffCompositionTests(_Case):
    def _run(self, **kwargs):
        calls, invoke = self._fake_cli()
        report = cr.run_handoff(
            bootstrap=kwargs.pop("bootstrap", {"source_range": {"start": 0.0,
                                                                "end": 5.0}}),
            media_path=kwargs.pop("media_path", self.media),
            cli_path=kwargs.pop("cli_path", self.cli),
            draft_store=kwargs.pop("draft_store", str(self.store)),
            handoff_root=kwargs.pop("handoff_root", str(self.handoff_root)),
            invoke=invoke, **kwargs)
        return report, calls

    def test_a_resolved_baked_output_becomes_one_identity_item(self):
        self._sidecar(scenes=[_crop_scene()])
        report, calls = self._run()
        item = report["spec"]["tracks"][0]["items"][0]
        self.assertNotIn("scale", item)
        self.assertEqual(report["visual_state"]["state_source"], "persisted")
        self.assertTrue(report["visual_state"]["output_baked"])
        self.assertFalse(report["visual_state"]["applied"])
        # No lane operation is needed for a crop-only composition.
        self.assertEqual([c for c in calls if "bg-blur" in c], [])

    def test_a_general_composition_applies_the_lane_background_blur(self):
        self._sidecar(scenes=[_general_scene()], baked=False)
        report, calls = self._run()
        blurs = [c for c in calls if "bg-blur" in c]
        self.assertEqual(len(blurs), 1)
        self.assertIn("seg-0", blurs[0])
        self.assertEqual(report["visual_state"]["applied_operations"][0]["op"],
                         "bg-blur")

    def test_the_actual_visual_state_is_in_the_immutable_spec(self):
        self._sidecar(scenes=[_crop_scene()])
        report, _ = self._run()
        spec = report["spec"]
        self.assertEqual(spec["visual_state"]["schema"], vs.SCHEMA)
        self.assertEqual(spec["visual_state"]["canvas"]["ratio"], "9:16")
        self.assertEqual(spec["visual_state"]["ownership"]["sha256"],
                         vs.digest_of(self.media))
        self.assertEqual(spec["composition"]["canvas_source"],
                         ch.CANVAS_SOURCE_PERSISTED)

    def test_a_legacy_clip_still_hands_over_with_a_labelled_fallback(self):
        report, _ = self._run()
        self.assertEqual(report["spec"]["canvas_source"], ch.CANVAS_SOURCE_LEGACY)
        self.assertEqual(report["visual_state"]["state_source"], "none")
        self.assertEqual(len(report["spec"]["tracks"][0]["items"]), 1)

    def test_unsupported_composition_fails_the_handoff_closed(self):
        self._sidecar(scenes=[_split_scene()], baked=False)
        with self.assertRaises(cr.ch.CapCutHandoffUnavailable) as ctx:
            self._run()
        self.assertIn("faithfully", str(ctx.exception))
        self.assertEqual(os.listdir(self.store), [])     # no project created


class OptionAResolutionTests(_Case):
    def _resolved_block(self, **over):
        return _block(self.media, scenes=[_crop_scene()], **over)

    def _run(self, resolver, **kwargs):
        calls, invoke = self._fake_cli()
        report = cr.run_handoff(
            bootstrap=kwargs.pop("bootstrap", {"source_range": {"start": 0.0,
                                                                "end": 5.0}}),
            media_path=self.media, cli_path=self.cli,
            draft_store=str(self.store), handoff_root=str(self.handoff_root),
            invoke=invoke, resolve_state=resolver, **kwargs)
        return report, calls, invoke

    def test_an_unresolved_version_invokes_the_render_path_first(self):
        seen = {}

        def resolver(*, bootstrap, media_path, requested, workspace):
            seen.update(bootstrap=bootstrap, media_path=media_path,
                        requested=requested, workspace=workspace)
            return self._resolved_block()

        report, calls, invoke = self._run(
            resolver, output_format="square",
            bootstrap={"source_range": {"start": 0.0, "end": 5.0},
                       "framing": "track"})
        # The existing render path was invoked BEFORE anything was written.
        self.assertEqual(seen["requested"]["output_format"], "square")
        self.assertEqual(seen["requested"]["framing"], "track")
        self.assertEqual(seen["media_path"], self.media)
        self.assertEqual(report["visual_state"]["state_source"],
                         "resolved_via_render")
        # ...and the resolved block is what reached the draft.
        self.assertEqual(report["spec"]["width"], 1080)
        self.assertTrue(any("compile" in c for c in calls))

    def test_a_resolved_composition_is_applied_not_assumed_baked(self):
        def resolver(**kwargs):
            return self._resolved_block()

        report, _, _ = self._run(resolver, output_format="vertical",
                                 bootstrap={"source_range": {"start": 0.0,
                                                             "end": 5.0},
                                            "framing": "track"})
        # Resolved against the handed asset as its INPUT, so the geometry is
        # applied rather than treated as already in the pixels.
        self.assertTrue(report["visual_state"]["applied"])
        self.assertIn("scale", report["spec"]["tracks"][0]["items"][0])

    def test_no_choice_means_no_re_render(self):
        def resolver(**kwargs):
            raise AssertionError("the render path must not run without a choice")

        report, _, _ = self._run(resolver)
        self.assertEqual(report["visual_state"]["state_source"], "none")

    def test_a_failed_resolution_creates_no_project_and_no_report(self):
        def resolver(**kwargs):
            raise RuntimeError("backend unreachable")

        with self.assertRaises(cr.ch.CapCutHandoffUnavailable) as ctx:
            self._run(resolver, output_format="square",
                      bootstrap={"source_range": {"start": 0.0, "end": 5.0},
                                 "framing": "track"})
        self.assertIn("refusing to hand over unresolved framing",
                      str(ctx.exception))
        # No misleading project...
        self.assertEqual(os.listdir(self.store), [])
        # ...and nothing a provenance row could be recorded from.
        specs = [p for p in self.handoff_root.rglob("spec.json")]
        self.assertEqual(specs, [])

    def test_a_resolver_that_produces_nothing_authoritative_fails_closed(self):
        def resolver(**kwargs):
            return {"schema": vs.SCHEMA, "resolution": "unresolved"}

        with self.assertRaises(cr.ch.CapCutHandoffUnavailable):
            self._run(resolver, output_format="square",
                      bootstrap={"source_range": {"start": 0.0, "end": 5.0},
                                 "framing": "track"})
        self.assertEqual(os.listdir(self.store), [])

    def test_a_refused_handoff_never_records_provenance(self):
        def resolver(**kwargs):
            raise RuntimeError("backend unreachable")

        with mock.patch.object(adapter, "record_capcut_handoff") as recorder:
            with self.assertRaises(cr.ch.CapCutHandoffUnavailable):
                self._run(resolver, output_format="square",
                          bootstrap={"source_range": {"start": 0.0, "end": 5.0},
                                     "framing": "track"})
            recorder.assert_not_called()

    def test_a_choice_already_reflected_by_persisted_state_is_not_re_rendered(self):
        self._sidecar(scenes=[_crop_scene()])

        def resolver(**kwargs):
            raise AssertionError("state already matches the requested format")

        report, _, _ = self._run(resolver, output_format="vertical",
                                 bootstrap={"source_range": {"start": 0.0,
                                                             "end": 5.0},
                                            "framing": "track"})
        self.assertEqual(report["visual_state"]["state_source"], "persisted")

    def test_a_persisted_state_for_another_format_is_re_resolved(self):
        self._sidecar(scenes=[_crop_scene()], width=1080, height=1080,
                      ratio="1:1", fmt="square")

        def resolver(*, bootstrap, media_path, requested, workspace):
            return _block(media_path, scenes=[_crop_scene()], baked=False)

        report, _, _ = self._run(resolver, output_format="vertical")
        self.assertEqual(report["visual_state"]["state_source"],
                         "resolved_via_render")
        self.assertEqual(report["visual_state"]["reason"],
                         "the persisted composition is 1:1 but vertical was requested")


class CoordinateInvariantsTests(_Case):
    def _run(self, **kwargs):
        calls, invoke = self._fake_cli()
        return cr.run_handoff(
            bootstrap=kwargs.pop("bootstrap"), media_path=self.media,
            cli_path=self.cli, draft_store=str(self.store),
            handoff_root=str(self.handoff_root), invoke=invoke, **kwargs), calls

    def test_working_times_are_preserved_unchanged(self):
        report, _ = self._run(
            bootstrap={"source_range": {"start": 12.0, "end": 42.0}},
            acquisition={"working_timebase_origin_s": 1800.0,
                         "original_start_s": 1812.0, "original_end_s": 1842.0})
        item = report["spec"]["tracks"][0]["items"][0]
        self.assertEqual(item["sourceStart"], 12.0)      # WORKING, untouched
        self.assertEqual(item["duration"], 30.0)
        self.assertEqual(report["coordinates"]["capcut"]["sourceStart"], 12.0)
        # The ORIGINAL pair travels as provenance only.
        self.assertEqual(report["coordinates"]["original"],
                         {"start": 1812.0, "end": 1842.0})
        self.assertEqual(report["coordinates"]["timebase_origin"], 1800.0)

    def test_an_original_window_is_translated_exactly_once(self):
        report, _ = self._run(
            bootstrap={"source_range": {"start": 0.0, "end": 1.0}},
            acquisition={"working_timebase_origin_s": 1800.0},
            original_window={"start": 1812.0, "end": 1842.0})
        item = report["spec"]["tracks"][0]["items"][0]
        self.assertEqual(item["sourceStart"], 12.0)      # 1812 - 1800, once
        self.assertEqual(item["duration"], 30.0)

    def test_scene_items_are_translated_once_into_the_asset_timebase(self):
        self._sidecar(scenes=[_crop_scene(start=0.0, end=5.0, points=[
            {"time": 0.0, "x": 657, "width": 606, "height": 1080, "y": 0}]),
            _crop_scene(start=5.0, end=10.0, points=[
                {"time": 5.0, "x": 657, "width": 606, "height": 1080, "y": 0}])],
            baked=False)
        report, _ = self._run(
            bootstrap={"source_range": {"start": 12.0, "end": 22.0}},
            acquisition={"working_timebase_origin_s": 1800.0})
        items = report["spec"]["tracks"][0]["items"]
        self.assertEqual([i["start"] for i in items], [0.0, 5.0])
        self.assertEqual([i["sourceStart"] for i in items], [12.0, 17.0])
        # Every item stays inside the working asset's own timeline.
        for item in items:
            self.assertLess(item["sourceStart"], 22.0)

    def test_caption_timing_stays_working_relative(self):
        report, _ = self._run(
            bootstrap={"source_range": {"start": 12.0, "end": 42.0}},
            caption_blocks=[{"text": "hello", "startMs": 500, "endMs": 1500}],
            acquisition={"working_timebase_origin_s": 1800.0})
        ass = Path(report['captions']['ass']).read_text()
        self.assertIn('Dialogue: 0,0:00:00.50,0:00:01.50', ass)
        self.assertNotIn('--time-offset', json.dumps(report['spec']))


    def test_a_metadata_pointer_never_becomes_a_timeline_text_object(self):
        self._sidecar(scenes=[_crop_scene()])
        report, _ = self._run(
            bootstrap={"source_range": {"start": 0.0, "end": 5.0},
                       "title": "https://kick.com/video/12345",
                       "hook": "https://cdn.example/x.mp4"})
        spec = report["spec"]
        self.assertEqual([t for t in spec["tracks"] if t["type"] == "text"], [])
        self.assertNotIn("kick.com", json.dumps(spec))
        self.assertNotIn("cdn.example", json.dumps(spec))


class ProvenanceCarriesTheVisualStateTests(_Case):
    def test_the_recorded_spec_includes_the_visual_state(self):
        self._sidecar(scenes=[_crop_scene()])
        calls, invoke = self._fake_cli()
        report = cr.run_handoff(
            bootstrap={"source_range": {"start": 0.0, "end": 5.0}},
            media_path=self.media, cli_path=self.cli,
            draft_store=str(self.store), handoff_root=str(self.handoff_root),
            invoke=invoke)
        captured = {}

        class _Resp:
            def read(self):
                return json.dumps([{"handoff_id": "h-1"}]).encode()

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def fake_urlopen(req, timeout=None):
            captured["body"] = json.loads(req.data.decode())
            return _Resp()

        with mock.patch.object(adapter, "urlopen", fake_urlopen):
            row = adapter.record_capcut_handoff(
                report, tenant_id="t-1", clip_id="c-1")
        self.assertEqual(row, {"handoff_id": "h-1"})
        persisted = captured["body"]["spec"]["visual_state"]
        self.assertEqual(persisted["schema"], vs.SCHEMA)
        self.assertEqual(persisted["canvas"]["ratio"], "9:16")
        self.assertTrue(persisted["scenes"])
        self.assertEqual(captured["body"]["media_sha256"],
                         vs.digest_of(self.media))


class CapCutStaysOptionalTests(_Case):
    def test_the_visual_state_reader_needs_no_capcut_at_all(self):
        self._sidecar(scenes=[_crop_scene()])
        state = vs.load(self.media)
        self.assertTrue(vs.assess(state, media_path=self.media)["usable"])
        self.assertIsNotNone(vs.canvas(state))

    def test_an_absent_lane_is_reported_not_raised_as_a_crash(self):
        with self.assertRaises(cr.ch.CapCutHandoffUnavailable) as ctx:
            cr.run_handoff(bootstrap={"source_range": {"start": 0.0, "end": 5.0}},
                           media_path=self.media, cli_path="/definitely/not/here",
                           draft_store=str(self.store),
                           handoff_root=str(self.handoff_root))
        self.assertIn("lane is unavailable", str(ctx.exception))

    def test_a_missing_draft_store_is_a_lane_refusal(self):
        empty = self.tmp / "not-a-store"
        with self.assertRaises(cr.ch.CapCutHandoffUnavailable):
            cr.run_handoff(bootstrap={"source_range": {"start": 0.0, "end": 5.0}},
                           media_path=self.media, cli_path=self.cli,
                           draft_store=str(empty),
                           handoff_root=str(self.handoff_root))

    def test_no_remote_url_is_ever_accepted_as_media(self):
        with self.assertRaises(ch.CapCutHandoffUnavailable):
            ch.build_capcut_spec({"source_range": {"start": 0.0, "end": 5.0}},
                                 media_path="https://cdn.example/x.mp4")


if __name__ == "__main__":
    unittest.main(verbosity=2)
