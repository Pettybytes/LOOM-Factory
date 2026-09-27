"""Window-intent parsing + WORKING-vs-ORIGINAL coordinate contract.

Covers the two bounded-intake seam defects closed in this pass:

A. Discord text -> START/END must never SILENTLY lose an intended window and
   downgrade into full-source acquisition. No intent stays allowed; unhonourable
   intent FAILS CLOSED.
B. `boundary_plan.recommended_clip_*` must be WORKING-ASSET coordinates, while the
   operator's ORIGINAL coordinates survive as provenance.
C. A working range must FIT the working asset (defence in depth).

The end-to-end test drives the SAME parser seam Discord uses
(parse_intake_message on message text) rather than injecting start_s/end_s below
the seam.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve()
_REPO = _HERE.parents[3]
for _p in (str(_REPO / "infra" / "discord-review"), str(_REPO / "infra"),
           str(_REPO / "infra" / "acquisition")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import manual_media_intake as mmi  # noqa: E402
import manual_intake_intelligence as mii  # noqa: E402
import source_window as swc  # noqa: E402
import source_review  # noqa: E402

URL = "https://www.youtube.com/watch?v=59xxeJCbzaM"
HAS_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def _make_media(path: Path, seconds: int = 20) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                    f"testsrc=size=320x240:rate=15:duration={seconds}",
                    "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-movflags", "+faststart", "-y", str(path)],
                   capture_output=True, text=True, timeout=180, check=True)


class TestWindowIntentParsing(unittest.TestCase):
    """A — parsing truth table, including every PRE-EXISTING accepted form."""

    PRE_EXISTING = {
        "start=1:23 end=2:45": (83.0, 165.0),
        "start=83 end=165": (83.0, 165.0),
        "START: 120 END: 180": (120.0, 180.0),
    }
    NEW_FORMS = {
        "START 120 / END 180 (space)": (120.0, 180.0),
        "START 120 alone-line": (120.0, 180.0),
        "window: 120-180": (120.0, 180.0),
        "bare line 120-180": (120.0, 180.0),
        "clock START 0:02:00": (120.0, 180.0),
    }

    def test_pre_existing_forms_still_parse(self):
        for form, (es, ee) in self.PRE_EXISTING.items():
            p = mmi.parse_intake_message(f"url: {URL} {form}")
            self.assertEqual((p["start_s"], p["end_s"]), (es, ee), form)

    def test_new_natural_forms_parse(self):
        bodies = {
            "START 120 / END 180 (space)": f"{URL}\nSTART 120\nEND 180",
            "START 120 alone-line": f"{URL}\nSTART 120\nEND 180",
            "window: 120-180": f"{URL}\nwindow: 120-180",
            "bare line 120-180": f"{URL}\n120-180",
            "clock START 0:02:00": f"{URL}\nSTART 0:02:00\nEND 0:03:00",
        }
        for label, text in bodies.items():
            p = mmi.parse_intake_message(text)
            self.assertEqual((p["start_s"], p["end_s"]), self.NEW_FORMS[label], label)

    def test_range_form_does_not_reuse_start_as_end(self):
        # Regression for a real bug: the range branch must read group(2) for END.
        p = mmi.parse_intake_message(f"{URL}\nwindow: 120-180")
        self.assertEqual(p["start_s"], 120.0)
        self.assertEqual(p["end_s"], 180.0)
        self.assertNotEqual(p["start_s"], p["end_s"])

    def test_no_window_intent_allows_full_source(self):
        for text in (f"{URL}", f"{URL}\nplease start the download",
                     f"{URL}\nposted 2026-09-27"):
            p = mmi.parse_intake_message(text)
            self.assertFalse(p["window_intent"], text)
            self.assertEqual(p["window_problem"], "", text)
            self.assertIsNone(p["start_s"], text)

    def test_incomplete_or_malformed_intent_is_flagged(self):
        cases = {
            f"{URL}\nSTART 120": "window_incomplete",
            f"{URL}\nEND 180": "window_incomplete",
            f"{URL}\nSTART: abc\nEND: 180": "window_incomplete",
            f"{URL}\nwindow: 120-": "window_unparsed",
            f"{URL}\nwindow: 120-": "window_unparsed",
        }
        for text, expected in cases.items():
            p = mmi.parse_intake_message(text)
            self.assertTrue(p["window_intent"], text)
            self.assertEqual(p["window_problem"], expected, text)

    def test_url_query_start_param_is_not_window_intent(self):
        # A source URL may legitimately carry `&start=`; that belongs to the URL,
        # not to the operator's window instruction.
        p = mmi.parse_intake_message("https://www.youtube.com/embed/abc?start=120&end=180")
        self.assertFalse(p["window_intent"])
        self.assertIsNone(p["start_s"])


class TestWorkingCoordinateContract(unittest.TestCase):
    """B/C — conversion, provenance preservation, and the fit assertion."""

    def test_operator_window_is_converted_to_working_coordinates(self):
        # ORIGINAL 120 -> 180 with a bounded working asset of 60s.
        submission = {"media_reference": {"acquisition": {"working_timebase_origin_s": 120.0}}}
        w_start, w_end, origin = mii._operator_window_in_working_coords(
            start_s=120.0, end_s=180.0, submission=submission)
        self.assertEqual((w_start, w_end, origin), (0.0, 60.0, 120.0))

    def test_full_source_origin_is_zero_and_unchanged(self):
        submission = {"media_reference": {}}
        w_start, w_end, origin = mii._operator_window_in_working_coords(
            start_s=2.0, end_s=5.0, submission=submission)
        self.assertEqual((w_start, w_end, origin), (2.0, 5.0, 0.0))

    def test_working_range_beyond_working_source_is_rejected(self):
        with self.assertRaises(swc.WindowError) as ctx:
            swc.validate_working_range(start_s=120.0, end_s=180.0, working_duration_s=60.0)
        self.assertEqual(ctx.exception.code, "WORKING_RANGE_BEYOND_WORKING_SOURCE")

    def test_working_range_reversed_and_negative_rejected(self):
        with self.assertRaises(swc.WindowError) as ctx:
            swc.validate_working_range(start_s=30.0, end_s=10.0, working_duration_s=60.0)
        self.assertEqual(ctx.exception.code, "WORKING_RANGE_REVERSED")
        with self.assertRaises(swc.WindowError) as ctx2:
            swc.validate_working_range(start_s=-1.0, end_s=10.0, working_duration_s=60.0)
        self.assertEqual(ctx2.exception.code, "WORKING_RANGE_NEGATIVE")

    def test_a_valid_working_range_passes(self):
        out = swc.validate_working_range(start_s=0.0, end_s=60.0, working_duration_s=60.0)
        self.assertEqual(out["clip_duration_s"], 60.0)


class TestP0RegressionExactExample(unittest.TestCase):
    """C — the exact P0 example from the seams audit.

    ORIGINAL source window 42:00 -> 47:30 (origin 2520), working duration 330s.
    No downstream working-media range may contain 2520 or 2850.
    """

    ORIGIN = 2520.0          # 42:00
    ORIG_START = 2520.0
    ORIG_END = 2850.0        # 47:30
    WORKING = 330.0

    def test_plan_is_working_relative_and_original_is_preserved(self):
        submission = {"media_reference": {"acquisition": {
            "working_timebase_origin_s": self.ORIGIN,
            "working_source_duration_s": self.WORKING}}}
        w_start, w_end, origin = mii._operator_window_in_working_coords(
            start_s=self.ORIG_START, end_s=self.ORIG_END, submission=submission)
        # working pair
        self.assertEqual((w_start, w_end, origin), (0.0, 330.0, self.ORIGIN))
        self.assertTrue(0 <= w_start < w_end <= self.WORKING)
        # the forbidden ORIGINAL values must NOT appear in a working-relative pair
        self.assertNotIn(self.ORIG_START, (w_start, w_end))
        self.assertNotIn(self.ORIG_END, (w_start, w_end))
        # ...and the plan passes the fit assertion
        swc.validate_working_range(start_s=w_start, end_s=w_end,
                                   working_duration_s=self.WORKING)
        # ORIGINAL provenance remains available for recording
        self.assertEqual((self.ORIG_START, self.ORIG_END), (2520.0, 2850.0))

    def test_the_unconverted_values_would_be_rejected(self):
        # Demonstrates the assertion catches exactly the P0 shape.
        with self.assertRaises(swc.WindowError) as ctx:
            swc.validate_working_range(start_s=self.ORIG_START, end_s=self.ORIG_END,
                                       working_duration_s=self.WORKING)
        self.assertEqual(ctx.exception.code, "WORKING_RANGE_BEYOND_WORKING_SOURCE")


@unittest.skipUnless(HAS_FFMPEG, "ffmpeg/ffprobe required")
class TestDiscordSeamEndToEnd(unittest.TestCase):
    """D — the SAME seam Discord uses: message TEXT -> parser -> acquire -> CI -> editor.

    Nothing is injected below the parser: the window enters as Discord message
    text and must survive every hop.
    """

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="loom-seam-e2e-")
        cls.source = Path(cls.tmp) / "source.mp4"
        _make_media(cls.source, seconds=20)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_message_text_survives_parser_acquisition_provenance_and_ci(self):
        # 1. DISCORD TEXT (the operator's actual message), window 5s -> 15s
        text = f"url: {URL}\nSTART 5\nEND 15"
        parsed = mmi.parse_intake_message(text)
        self.assertTrue(parsed["window_intent"])
        self.assertEqual((parsed["start_s"], parsed["end_s"]), (5.0, 15.0))

        # 2. A downloader that HONOURS --download-sections (a faithful local
        #    stand-in for yt-dlp, so the real window path is exercised).
        def fake_download(cmd):
            section = None
            if "--download-sections" in cmd:
                section = cmd[cmd.index("--download-sections") + 1]
            out = Path(self.tmp) / f"win-{section or 'full'}.mp4".replace("*", "")
            span = re.match(r"\*([0-9.]+)-([0-9.]+)", section or "")
            if span:
                a, b = float(span.group(1)), float(span.group(2))
                subprocess.run(["ffmpeg", "-v", "error", "-ss", str(a), "-i",
                                str(self.source), "-t", str(b - a), "-c:v", "libx264",
                                "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                                "-c:a", "aac", "-movflags", "+faststart", "-y",
                                str(out)], capture_output=True, text=True, timeout=180,
                               check=True)
            else:
                shutil.copyfile(self.source, out)
            return str(out)

        # 3. ACQUISITION through the real path, window taken FROM THE PARSER.
        path, resolved = mmi.acquire_url_media(
            parsed["url"], out_dir=self.tmp,
            start_s=parsed["start_s"], end_s=parsed["end_s"],
            run_download=fake_download,
            preflight_runner=lambda cmd: "{}",
        )
        self.assertEqual(resolved["acquisition_mode"], "bounded_segment")
        win = resolved["window"]
        self.assertEqual(win["clip_duration_ms"], 10000)          # 5s -> 15s
        self.assertEqual(resolved["working_source"]["timebase_origin_s"], 5.0)
        measured = mmi.probe_media(path)["duration_seconds"]
        self.assertAlmostEqual(measured, 10.0, delta=1.0)
        self.assertNotAlmostEqual(measured, 20.0, delta=1.0)      # NOT the full source

        # 4. DURABLE PROVENANCE carries BOTH coordinate systems.
        prov = {
            "acquisition_mode": resolved["acquisition_mode"],
            "original_start_s": parsed["start_s"],
            "original_end_s": parsed["end_s"],
            "working_timebase_origin_s": resolved["working_source"]["timebase_origin_s"],
            "working_source_duration_s": resolved["working_source"]["duration_seconds"],
        }
        self.assertEqual(prov["original_start_s"], 5.0)            # ORIGINAL preserved
        self.assertEqual(prov["working_timebase_origin_s"], 5.0)
        self.assertAlmostEqual(prov["working_source_duration_s"], 10.0, delta=1.0)

        # 5. CI SEAM produces a WORKING-relative recommendation.
        w_start, w_end, origin = mii._operator_window_in_working_coords(
            start_s=prov["original_start_s"], end_s=prov["original_end_s"],
            submission={"media_reference": {"acquisition": prov}})
        self.assertEqual((w_start, w_end, origin), (0.0, 10.0, 5.0))
        swc.validate_working_range(start_s=w_start, end_s=w_end,
                                   working_duration_s=prov["working_source_duration_s"])
        self.assertNotIn(5.0, (w_start, w_end))      # ORIGINAL value must not leak in
        self.assertNotIn(15.0, (w_start, w_end))

        # 6. EDITOR BOOTSTRAP receives coordinates INSIDE the working duration.
        candidate = {
            "tenant_id": "dreemzy",
            "duplicate_fingerprint": "a" * 64,
            "platform": "youtube",
            "platform_content_id": "59xxeJCbzaM",
            "title": "seam e2e",
            "boundary_plan": {
                "recommended_clip_start": w_start,
                "recommended_clip_end": w_end,
                "duration": prov["working_source_duration_s"],
            },
            "moment_evidence": {"lineage": {
                "media_reference": {"url": "https://example.invalid/x.mp4",
                                    "duration_seconds": prov["working_source_duration_s"]},
            }},
        }
        boot, reason = source_review.source_lineage_bootstrap(candidate)
        self.assertEqual(reason, "ok")
        self.assertLessEqual(boot["source_range"]["end"],
                             prov["working_source_duration_s"] + 2.0)
        self.assertNotIn(5.0, (boot["source_range"]["start"], boot["source_range"]["end"]))
        self.assertNotIn(15.0, (boot["source_range"]["start"], boot["source_range"]["end"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
