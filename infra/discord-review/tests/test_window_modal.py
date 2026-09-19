"""Adjust Window modal: the exact quote_from_bytes regression + persistence shapes.

Run:  python3 -m unittest discover -s infra/discord-review/tests -v

No network: the HTTP layer is patched, so this exercises the real routing and the
real quoting boundaries without touching Discord or Supabase.
"""

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

_REPO = Path(__file__).resolve().parents[3]
for d in ("acquisition", "discord-review"):
    sys.path.insert(0, str(_REPO / "infra" / d))

import acquisition_ops as ops  # noqa: E402
import source_context as sc  # noqa: E402

VID = "ClhBXVygWbc"
URL = f"https://www.youtube.com/watch?v={VID}"


class _Sink:
    """Records what a modal submit does, without an HTTP server or a live DB."""

    def __init__(self, rows=None):
        self.rows = rows or [{
            "platform": "youtube", "source_url": URL,
            "duplicate_fingerprint": "f" * 64, "reviewer_note": "",
            "time_start_s": 60.0, "time_end_s": 90.0}]
        self.patched: list[tuple[str, dict]] = []

    def _get(self, path):
        return self.rows

    def source_context(self, ref):
        return sc.resolve_context(ref, get=self._get).as_dict() if sc.resolve_context(ref, get=self._get) else None

    def update_source_window(self, ref, *, start_s, end_s, provenance=None):
        if not isinstance(ref, str) or not ref:
            raise ValueError("ref must be a non-empty str")
        self.patched.append((ref, {"start_s": start_s, "end_s": end_s,
                                   "provenance": provenance}))
        return {"submission": [{"time_start_s": start_s, "time_end_s": end_s}],
                "job": [{"requested_start_s": start_s}]}

    def pending_job(self, ref):
        return {"id": "job-1", "asset_provenance": {}}

    def update_job(self, job_id, patch):
        return dict(patch, id=job_id)


def interaction(custom_id, start, end):
    return {"data": {"custom_id": custom_id, "components": [
        {"components": [{"custom_id": "window_start", "value": start}]},
        {"components": [{"custom_id": "window_end", "value": end}]}]}}


class TestModalSubmission(unittest.TestCase):
    def test_the_exact_quote_from_bytes_bug_cannot_recur(self):
        """The tuple (ref, message_id) once reached urllib.quote() -> TypeError.

        Through the real routing path the modal must persist, never raise.
        """
        cid = ops.window_modal_payload(VID, message_id="1550657694665412730")["data"]["custom_id"]
        store = _Sink()
        out = ops.handle_window_modal(interaction(cid, "01:05", "01:35"),
                                      deps=store, actor="bryan", transport=None)
        self.assertTrue(out["ok"])
        self.assertEqual(store.patched[0][0], VID, "the ref must be a plain str")
        self.assertEqual(out["window"]["start_ms"], 65_000)
        self.assertEqual(out["window"]["end_ms"], 95_000)
        self.assertEqual(out["window"]["origin"], "operator_adjusted")

    def test_a_non_str_ref_fails_loudly_at_the_boundary(self):
        """A malformed custom_id must not reach quote() as a non-str."""
        cid = f"AQW:{VID}:1550657694665412730"
        # simulate a parsed tuple leaking through as the ref
        with mock.patch.object(ops, "parse_window_modal_custom_id",
                               return_value=({"ref": VID}, "1")):
            with self.assertRaises(Exception) as e:
                ops.handle_window_modal(interaction(cid, "01:05", "01:35"),
                                        deps=_Sink(), actor="bryan", transport=None)
        self.assertIn("BAD_MODAL_REF", str(getattr(e.exception, "code", "")) + str(e.exception))

    def test_adjusted_window_persists_seconds_and_preserves_the_suggestion(self):
        original = {"window": {"start_ms": 60_000, "end_ms": 90_000,
                               "clip_duration_ms": 30_000,
                               "range_fingerprint": "a" * 64},
                    "suggestion_origin": "automated_source_engine"}
        store = _Sink(rows=[{"platform": "youtube", "source_url": URL,
                             "duplicate_fingerprint": "f" * 64,
                             "reviewer_note": json.dumps(original),
                             "time_start_s": 60.0, "time_end_s": 90.0}])
        cid = ops.window_modal_payload(VID, message_id="1")["data"]["custom_id"]
        out = ops.handle_window_modal(interaction(cid, "01:05", "01:35"),
                                      deps=store, actor="bryan", transport=None)
        ref, body = store.patched[0]
        self.assertEqual(body["start_s"], 65.0)
        self.assertEqual(body["end_s"], 95.0)
        prov = body["provenance"]
        self.assertEqual(prov["original_suggestion"]["start_ms"], 60_000,
                         "the automated suggestion must be preserved")
        self.assertEqual(prov["active_origin"], "operator_adjusted")
        self.assertEqual(prov["window"]["origin"], "operator_adjusted")
        self.assertNotEqual(out["window"]["range_fingerprint"], "a" * 64,
                            "the range fingerprint must be recomputed")

    def test_modal_timestamps_use_the_shared_contract(self):
        self.assertEqual(ops.parse_window_values(
            {"window_start": "65", "window_end": "95"}), (65_000, 95_000))
        self.assertEqual(ops.parse_window_values(
            {"window_start": "1:05", "window_end": "01:35"}), (65_000, 95_000))
        self.assertEqual(ops.parse_window_values(
            {"window_start": "0:01:05.5", "window_end": "01:35"}), (65_500, 95_000))
        import source_window as sw
        with self.assertRaises(sw.WindowError):
            ops.parse_window_values({"window_start": "later", "window_end": "01:35"})

    def test_invalid_adjustment_never_creates_a_row(self):
        store = _Sink()
        cid = ops.window_modal_payload(VID, message_id="1")["data"]["custom_id"]
        with self.assertRaises(Exception):
            ops.handle_window_modal(interaction(cid, "01:35", "01:05"),
                                    deps=store, actor="bryan", transport=None)
        self.assertEqual(store.patched, [], "a refused window persists nothing")


class TestDurablePersistenceShapes(unittest.TestCase):
    """Strings, UUIDs, numeric timestamps and query-bearing identities all quote."""

    def _store(self):
        return ops.SupabaseLaneStore("https://example.supabase.co", "service-key")

    def _capture(self, ref):
        seen = []

        class _Resp:
            def read(self):
                return b"[]"

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def fake_urlopen(req, timeout=None):
            seen.append({"url": req.full_url, "method": req.get_method(),
                         "body": json.loads(req.data.decode()) if req.data else None})
            return _Resp()

        with mock.patch("urllib.request.urlopen", fake_urlopen):
            self._store().update_source_window(ref, start_s=65.0, end_s=95.0,
                                               provenance={"k": "v"})
        return seen

    def test_plain_string_id(self):
        seen = self._capture("ClhBXVygWbc")
        self.assertEqual(len(seen), 2)
        self.assertIn("platform_content_id=eq.ClhBXVygWbc", seen[0]["url"])
        self.assertEqual(seen[0]["body"]["time_start_s"], 65.0)

    def test_uuid_ref(self):
        seen = self._capture("59297cc6-5585-4b81-8f69-4cb47ad3baaf")
        self.assertIn("59297cc6-5585-4b81-8f69-4cb47ad3baaf", seen[0]["url"])

    def test_url_and_query_bearing_identity_is_quoted_not_crashed(self):
        seen = self._capture("https://www.youtube.com/watch?v=ClhBXVygWbc&si=abc")
        # quote() leaves "/" alone but MUST escape the query separators, or the
        # PostgREST filter would break apart at the "?" and "&".
        url = seen[0]["url"]
        self.assertIn("platform_content_id=eq.https%3A//www.youtube.com", url)
        self.assertIn("%3Fv%3DClhBXVygWbc%26si%3Dabc", url)
        self.assertNotIn("?v=", url.split("loom_manual_submissions")[1])

    def test_numeric_timestamps_are_floats_and_a_non_str_ref_is_refused(self):
        seen = self._capture("ClhBXVygWbc")
        self.assertIsInstance(seen[0]["body"]["time_start_s"], float)
        self.assertIsInstance(seen[0]["body"]["time_end_s"], float)
        with self.assertRaises(ValueError):
            self._store().update_source_window(12345, start_s=0.0, end_s=5.0)


if __name__ == "__main__":
    unittest.main()
