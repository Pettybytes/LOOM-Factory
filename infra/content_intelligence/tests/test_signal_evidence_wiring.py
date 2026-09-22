"""Extracted signal windows must actually count as visual evidence.

Live defect: the bounded inspection extracted windows (signal_status
"extracted:6") and then DROPPED them - nothing populated ev.signals or
ev.visual_opening. Consequences on every candidate regardless of clip quality:
has_visual() stayed False so Content was capped at the metadata-only ceiling,
activity_strength() stayed 0.0 so confidence lost its visual/activity dimensions
(conf pinned at 22). The window "basis" names also differ from ACTIVITY_KINDS
(visual_motion/scene_cut vs motion/scene_change), so they must be aliased.

The paired test below proves the opposite direction too: when analysis yields no
windows, confidence must NOT be inflated - failed media analysis is never a pass.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from discovery.discovery_record import DiscoveryRecord  # noqa: E402
from discovery.source_brief import SourceBrief  # noqa: E402
from discovery.topic_intelligence import TopicIntelligence  # noqa: E402
from content_intelligence.pipeline import Pass1Config, Pass1Pipeline  # noqa: E402
from content_intelligence.store_supabase import ContentStoreSupabase  # noqa: E402


def _brief():
    return SourceBrief(tenant_id="dreemzy", profile_id="dreemzy-main", version="2",
                       target_platforms=["youtube"], topics=["funny dog moments"], entities=[],
                       keywords=["dog", "funny", "moment"], exclusions=[],
                       creator_channel_allowlist=[], creator_channel_blocklist=[],
                       freshness_window_days=3650, content_type_requirements=["video"],
                       language="en", provenance_rights_requirements="yt-dlp",
                       acquisition_eligibility=True, cost_ceiling_usd=0.15,
                       manual_review_required=True, connector_restrictions=[])


def _record():
    return DiscoveryRecord(
        tenant_id="dreemzy", source_brief_version="2", profile_id="dreemzy-main",
        platform="youtube", canonical_url="https://www.youtube.com/watch?v=abcdefghijk",
        platform_content_id="abcdefghijk", title="funny dog moment",
        creator_channel_identity="c", creator_channel_url="https://youtube.com/@c",
        publication_timestamp=None,
        observed_metrics={"content_type": "video", "views": 500000},
        metric_observation_timestamp="2026-01-01T00:00:00+00:00", metric_history=[],
        discovery_query="funny dog moments", discovery_reason="t", evidence_confidence=0.9,
        provenance_rights_evidence="yt-dlp", duplicate_fingerprint="x", global_fingerprint="x",
        processing_eligibility=True, qualification_status="pending",
        connector_name="t", connector_version="1", duration_seconds=60)


class _FakePreview:
    artifact_path = "/tmp/loom-fake.mp4"
    artifact_bytes = 1024
    available = True
    method = "section_download"

    def __getattr__(self, _name):        # tolerate any other attribute access
        return None


def _run(windows):
    """Run one candidate with the given extracted signal windows.

    The windows reach the pipeline through the module-level
    fetch_media_and_signals used by _process, so that is the correct injection
    point (patching the inspection hook alone never reaches the attachment path).
    """
    cfg = Pass1Config(max_candidates=1, market_shortlist_threshold=0, cost_ceiling_usd=0.15,
                      extract_multimodal_signals=True)
    p = Pass1Pipeline(cfg, _brief(), TopicIntelligence())
    p._inspection_preview = lambda *a, **k: _FakePreview()
    p._inspection_captions = lambda *a, **k: (
        [{"start_s": 0.0, "end_s": 4.0, "text": "and then he did it!"}], "auto_generated")
    with mock.patch("content_intelligence.pipeline.fetch_media_and_signals",
                    lambda *a, **k: windows):
        res = p.run([_record()], ContentStoreSupabase())
    return res.candidates[0]


class SignalEvidenceWiringTests(unittest.TestCase):
    def test_extracted_windows_are_credited_as_visual_evidence(self):
        """Windows must RAISE confidence; discarding them pinned it at the floor."""
        with_windows = _run([{"basis": "visual_motion", "start_s": 0.0, "end_s": 3.0},
                             {"basis": "scene_cut", "start_s": 0.0, "end_s": 3.0}])
        without = _run([])
        self.assertGreater(with_windows.evidence_confidence, without.evidence_confidence)

    def test_no_windows_means_no_inflated_confidence(self):
        """Failed/absent media analysis must never be treated as a pass."""
        no_windows = _run([])
        unmapped = _run([{"basis": "some_other_basis", "start_s": 0.0, "end_s": 3.0}])
        self.assertLessEqual(unmapped.evidence_confidence, no_windows.evidence_confidence)


if __name__ == "__main__":
    unittest.main()
