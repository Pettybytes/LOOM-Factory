"""Raw captions must be normalized before any downstream reasoning.

Runtime evidence: every caption line arrived twice - one copy with inline word-timing
markup and one clean snapshot - interleaved with [Music] filler. A line ending in
"</c>" is not a sentence end, and a first-3s window full of [Music] yields no hook.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from content_intelligence.transcript_normalize import (  # noqa: E402
    clean_text, is_filler, normalize_transcript_segments, transcript_text,
)

MARKUP_LINE = ("Welcome<00:00:16.720><c> back</c><00:00:16.960><c> to</c>"
               "<00:00:17.279><c> Where</c><00:00:18.160><c> Funny.</c>")


class CleanTextTests(unittest.TestCase):
    def test_word_timing_markup_is_stripped(self):
        self.assertEqual(clean_text(MARKUP_LINE), "Welcome back to Where Funny.")

    def test_html_entities_are_decoded(self):
        self.assertEqual(clean_text("&gt;&gt; No, no, no."), "No, no, no.")
        self.assertEqual(clean_text("fish &amp; chips"), "fish & chips")

    def test_speaker_prefix_and_whitespace_collapsed(self):
        self.assertEqual(clean_text("  >>   hello   there "), "hello there")

    def test_filler_detected(self):
        for t in ("[Music]", "[Laughter]", "[Applause]", ""):
            self.assertTrue(is_filler(t))
        self.assertFalse(is_filler("he fell over"))


class NormalizeSegmentsTests(unittest.TestCase):
    RAW = [
        {"start_s": 16.4, "end_s": 18.79, "text": MARKUP_LINE},
        {"start_s": 18.79, "end_s": 18.8, "text": "Welcome back to Where Fur Meets Funny."},
        {"start_s": 25.45, "end_s": 34.31, "text": "[Music]"},
        {"start_s": 34.31, "end_s": 34.32, "text": "[Music]"},
        {"start_s": 39.04, "end_s": 42.88, "text": "&gt;&gt; No,<00:00:39.280><c> no.</c>"},
    ]

    def test_doubled_captions_collapse(self):
        out = normalize_transcript_segments(self.RAW)
        self.assertLess(len(out), len(self.RAW))
        self.assertTrue(all("<" not in s["text"] for s in out))

    def test_filler_segments_are_removed(self):
        out = normalize_transcript_segments(self.RAW)
        self.assertFalse(any("music" in s["text"].lower() for s in out))

    def test_output_is_monotonic_and_well_formed(self):
        out = normalize_transcript_segments(self.RAW)
        for a, b in zip(out, out[1:]):
            self.assertLessEqual(a["start_s"], b["start_s"])
        for s in out:
            self.assertLessEqual(s["start_s"], s["end_s"])

    def test_a_marked_up_sentence_is_now_detectable_as_a_sentence(self):
        out = normalize_transcript_segments(self.RAW)
        self.assertTrue(any(s["text"].endswith(".") for s in out))

    def test_transcript_text_is_clean_speech(self):
        t = transcript_text(self.RAW)
        self.assertNotIn("<", t)
        self.assertNotIn("&gt;", t)
        self.assertNotIn("[Music]", t)

    def test_handles_garbage_input_safely(self):
        for bad in (None, [], [None], [{}], [{"start_s": "x", "text": "y"}]):
            with self.subTest(bad=bad):
                self.assertIsInstance(normalize_transcript_segments(bad), list)


if __name__ == "__main__":
    unittest.main()
