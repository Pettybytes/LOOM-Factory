"""Targeted test: manual-intake routing semantics (small correction, pinned).

Manually supplied media has already been discovered AND acquired by the operator,
so its post-Content-Intelligence card is CONTENT/CANDIDATE review — it must never
be routed into the acquisition-oriented Source Review workflow.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

_REPO = Path(__file__).resolve().parents[3]
for _p in (str(_REPO / "infra" / "discord-review"), str(_REPO / "infra"), str(_REPO / "infra" / "acquisition")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

if not os.environ.get("LOOM_SUPABASE_URL"):
    _env_file = Path.home() / ".openclaw/.env"
    if _env_file.exists():
        for _line in _env_file.read_text().splitlines():
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))
os.environ.setdefault("LOOM_ACQUISITION_LIVE", "0")

import adapter  # noqa: E402

SOURCE_REVIEW_CHANNEL = "1550207989569421444"


class TestManualIntakeChannelBinding(unittest.TestCase):
    def test_env_id_wins_over_display_name(self):
        with mock.patch.object(adapter, "MANUAL_INTAKE_CHANNEL_ID", "999-bound-id"), \
                mock.patch.object(adapter, "_manual_channel_cache", {"id": None, "ts": 0.0}), \
                mock.patch.object(adapter, "http_json") as h:
            self.assertEqual(adapter.resolve_manual_intake_channel(), "999-bound-id")
            h.assert_not_called()  # bound by id: never depends on the display name

    def test_name_fallback_tolerates_a_decorated_name(self):
        with mock.patch.object(adapter, "MANUAL_INTAKE_CHANNEL_ID", ""), \
                mock.patch.object(adapter, "_manual_channel_cache", {"id": None, "ts": 0.0}), \
                mock.patch.object(adapter, "http_json") as h:
            h.side_effect = [
                (200, [{"id": "g1"}]),
                (200, [{"id": "c1", "name": "manual-intake-📸", "type": 0}]),
            ]
            self.assertEqual(adapter.resolve_manual_intake_channel(), "c1")


class TestManualCandidateDestination(unittest.TestCase):
    def test_reuses_the_existing_clip_review_destination(self):
        with mock.patch.object(adapter, "MANUAL_REVIEW_CHANNEL_ID", ""), \
                mock.patch.object(adapter, "resolve_clip_review_channel",
                                  return_value="clip-review-id"):
            self.assertEqual(adapter.manual_review_channel_id(), "clip-review-id")

    def test_explicit_override_wins(self):
        with mock.patch.object(adapter, "MANUAL_REVIEW_CHANNEL_ID", "override-id"):
            self.assertEqual(adapter.manual_review_channel_id(), "override-id")

    def test_never_routes_to_source_review(self):
        with mock.patch.object(adapter, "MANUAL_REVIEW_CHANNEL_ID", ""), \
                mock.patch.object(adapter, "resolve_clip_review_channel",
                                  return_value="clip-review-id"):
            self.assertNotEqual(adapter.manual_review_channel_id(), SOURCE_REVIEW_CHANNEL)

    def test_source_review_is_not_wired_into_the_manual_path(self):
        """Regression guard: the manual worker must not reference a source-review
        destination at all — that semantics belongs to acquisition review."""
        source = (_REPO / "infra/discord-review/adapter.py").read_text()
        self.assertNotIn("SOURCE_REVIEW_CHANNEL_ID", source)
        self.assertIn("manual_review_channel_id()", source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
