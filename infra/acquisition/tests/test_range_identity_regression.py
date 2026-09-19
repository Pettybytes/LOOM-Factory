"""Range identity vs. the false ALREADY_ACQUIRED refusal (production-shaped).

One long-form source legitimately yields several assets. A content-id-only library
check refused a newly authorized window because an EARLIER window of the same video
was already in the library. These tests pin the range-aware contract the runner uses.

Run:  python3 -m unittest discover -s infra/acquisition/tests -v
"""

import sys
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
for d in ("acquisition", "discord-review"):
    sys.path.insert(0, str(_REPO / "infra" / d))

import approved_lane as lane  # noqa: E402
import source_window as sw  # noqa: E402

VID = "ClhBXVygWbc"
URL = f"https://www.youtube.com/watch?v={VID}"
ACQUIRED_30_60 = {"platform_content_id": VID,
                  "asset_filename": f"youtube__{VID}__30-60__3a891cb35f99.mp4",
                  "duplicate_fingerprint": "929e4e72cabf4268f2c2e29364da36be393f259d7b1a19b86f4ddba80c3bd003"}


def window(start_s: float, end_s: float) -> dict:
    return sw.validate_window(platform="youtube", canonical_url=URL, content_id=VID,
                              start_ms=int(start_s * 1000), end_ms=int(end_s * 1000),
                              origin="operator_adjusted", source_duration_s=302.0)


class TestRangeIdentity(unittest.TestCase):
    def test_a_different_window_is_not_already_acquired(self):
        """The exact production false refusal: 30-60 present, 65-95 authorized."""
        self.assertFalse(lane.already_acquired([ACQUIRED_30_60], content_id=VID,
                                               window=window(65, 95)))
        self.assertFalse(lane.already_acquired([ACQUIRED_30_60], content_id=VID,
                                               window=window(60, 90)))

    def test_the_same_window_is_idempotent(self):
        thirty = window(30, 60)
        row = dict(ACQUIRED_30_60, duplicate_fingerprint=thirty["range_fingerprint"])
        self.assertTrue(lane.already_acquired([row], content_id=VID, window=thirty))

    def test_a_new_window_after_another_exists_is_acquirable(self):
        """After the 65-95 asset lands, 95-125 must still be acquirable."""
        sixty_five = window(65, 95)
        rows = [ACQUIRED_30_60,
                {"platform_content_id": VID, "asset_filename": "x.mp4",
                 "duplicate_fingerprint": sixty_five["range_fingerprint"]}]
        self.assertFalse(lane.already_acquired(rows, content_id=VID, window=window(95, 125)))
        self.assertTrue(lane.already_acquired(rows, content_id=VID, window=sixty_five))

    def test_whole_item_semantics_are_unchanged(self):
        self.assertTrue(lane.already_acquired([ACQUIRED_30_60], content_id=VID, window=None))
        self.assertFalse(lane.already_acquired([], content_id=VID, window=None))

    def test_rows_for_another_range_never_match(self):
        self.assertFalse(lane.row_matches_range(
            {"requested_start_s": 30.0, "requested_end_s": 60.0}, window(65, 95)))
        self.assertTrue(lane.row_matches_range(
            {"requested_start_s": 65.0, "requested_end_s": 95.0}, window(65, 95)))
        self.assertFalse(lane.row_matches_range(
            {"requested_start_s": None, "requested_end_s": None}, window(65, 95)))


class TestRunnerDelegatesToRangeIdentity(unittest.TestCase):
    def test_the_runner_uses_the_range_aware_helper(self):
        src = (_REPO / "infra" / "acquisition" / "live_runner.py").read_text(encoding="utf-8")
        self.assertIn("lane.already_acquired(", src,
                      "the runner must delegate to the range-aware identity check")
        self.assertNotIn("library_has_content(io.library_rows(), platform_content_id=content_id",
                         src, "a content-id-only check is the false-refusal regression")


if __name__ == "__main__":
    unittest.main()
