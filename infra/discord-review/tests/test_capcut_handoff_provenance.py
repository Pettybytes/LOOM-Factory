"""Focused tests: DURABLE provenance for a successful "Finish in CapCut" handoff.

The provenance record exists so a LATER BEFORE/AFTER comparison needs no
archaeology: it preserves the authoritative BEFORE state — the exact compiled
spec, the working-media hash, the coordinate origin and the compiler identity —
against the tenant, candidate, clip, version and correction that produced it.

What these tests protect:
  * exactly those fields are recorded, tenant-scoped;
  * the write is APPEND-ONLY (a new handoff_id per success, never an upsert that
    could overwrite an earlier BEFORE state);
  * nothing is recorded without a real specification AND a real media hash, so a
    failed or refused handoff can never leave a success row;
  * a provenance write failure is NON-FATAL and never reported as recorded —
    provenance must not be able to break the operator's editor lane.

No network and no Supabase: the single write call is stubbed.
"""

import json
import os
import sys
import unittest
import unittest.mock as mock
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_ROOT / "infra" / "discord-review"))

# The adapter reads its configuration at import time; supply inert values with a
# syntactically valid URL so a Request can be constructed in the stubs below.
for _k, _v in (("LOOM_DISCORD_APP_ID", "test-inert"),
               ("LOOM_DISCORD_PUBLIC_KEY", "test-inert"),
               ("LOOM_DISCORD_BOT_TOKEN", "test-inert"),
               ("LOOM_SUPABASE_URL", "https://test.supabase.co"),
               ("LOOM_SUPABASE_SERVICE_ROLE_KEY", "test-inert")):
    os.environ.setdefault(_k, _v)

import adapter  # noqa: E402
import capcut_runner as cr  # noqa: E402

HANDOFF_ID = "11111111-2222-3333-4444-555555555555"


def _report(**over):
    report = {
        "ok": True,
        "asset_version": "render-1.2",
        "coordinates": {"timebase_origin": 1800.0,
                        "working": {"start": 0.0, "end": 60.0}},
        "media": {"sha256": "b" * 64},
        "compiler": {"name": "capcut-cli", "version": "0.26.0",
                     "commit": cr.PINNED_DONOR_COMMIT},
        "spec": {"name": "LOOM render-1.2 20260928-2031",
                 "tracks": [{"type": "video", "items": [
                     {"path": "/x/working.mp4", "start": 0, "duration": 60,
                      "sourceStart": 0}]}]},
    }
    report.update(over)
    return report


class _CapturedResponse:
    def __init__(self, body):
        self._body = json.dumps(body).encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Recorder:
    """Stub for the one urllib call the helper makes; captures the request."""

    def __init__(self, body=None, raises=None):
        self.body = body if body is not None else [{"handoff_id": HANDOFF_ID}]
        self.raises = raises
        self.calls = []

    def __call__(self, req, timeout=15):
        self.calls.append({"url": req.full_url,
                           "payload": json.loads(req.data.decode()),
                           "prefer": req.get_header("Prefer"),
                           "method": req.get_method()})
        if self.raises:
            raise self.raises
        return _CapturedResponse(self.body)


class RecordedFieldsTests(unittest.TestCase):
    def test_a_successful_handoff_records_the_full_before_state(self):
        rec = _Recorder()
        with mock.patch.object(adapter, "urlopen", rec):
            row = adapter.record_capcut_handoff(
                _report(), tenant_id="dreemzy", clip_id="job1:0",
                candidate_id="fp" * 32,
                correction={"id": "corr-1", "source_asset_version": "render-1.1"})
        self.assertEqual(row, {"handoff_id": HANDOFF_ID})
        self.assertEqual(len(rec.calls), 1)
        payload = rec.calls[0]["payload"]
        self.assertEqual(payload["tenant_id"], "dreemzy")
        self.assertEqual(payload["clip_id"], "job1:0")
        self.assertEqual(payload["candidate_id"], "fp" * 32)
        self.assertEqual(payload["asset_version"], "render-1.2")
        self.assertEqual(payload["source_asset_version"], "render-1.1")
        self.assertEqual(payload["correction_id"], "corr-1")
        self.assertEqual(payload["media_sha256"], "b" * 64)
        self.assertEqual(payload["working_timebase_origin_s"], 1800.0)
        self.assertEqual(payload["spec"]["name"], "LOOM render-1.2 20260928-2031")
        self.assertEqual(payload["compiler"]["commit"], cr.PINNED_DONOR_COMMIT)

    def test_the_immutable_spec_is_recorded_verbatim(self):
        spec = {"name": "x", "width": 1080, "height": 1920, "ratio": "9:16",
                "fps": 30, "tracks": [{"type": "video", "items": [
                    {"path": "/m.mp4", "start": 0, "duration": 60, "sourceStart": 0}]}],
                "operations": [{"op": "captions", "path": "/c.srt",
                                "trackName": "captions", "timeOffset": 0}]}
        rec = _Recorder()
        with mock.patch.object(adapter, "urlopen", rec):
            adapter.record_capcut_handoff(_report(spec=spec), tenant_id="t",
                                          clip_id="c")
        self.assertEqual(rec.calls[0]["payload"]["spec"], spec)

    def test_the_write_is_append_only_not_an_upsert(self):
        # An upsert could overwrite an earlier BEFORE state; each success must be
        # its own row keyed by a freshly generated handoff_id.
        rec = _Recorder()
        with mock.patch.object(adapter, "urlopen", rec):
            adapter.record_capcut_handoff(_report(), tenant_id="t", clip_id="c")
        call = rec.calls[0]
        self.assertEqual(call["method"], "POST")
        self.assertNotIn("on_conflict", call["url"])
        self.assertNotIn("merge-duplicates", call["prefer"] or "")
        self.assertIn("return=representation", call["prefer"] or "")
        self.assertTrue(call["url"].endswith("/loom_capcut_handoffs"))

    def test_the_record_is_tenant_scoped(self):
        rec = _Recorder()
        with mock.patch.object(adapter, "urlopen", rec):
            adapter.record_capcut_handoff(_report(), tenant_id="other-tenant",
                                          clip_id="c")
        self.assertEqual(rec.calls[0]["payload"]["tenant_id"], "other-tenant")

    def test_a_version_without_a_correction_records_nulls(self):
        rec = _Recorder()
        with mock.patch.object(adapter, "urlopen", rec):
            adapter.record_capcut_handoff(_report(), tenant_id="t", clip_id="c")
        payload = rec.calls[0]["payload"]
        self.assertIsNone(payload["correction_id"])
        self.assertIsNone(payload["source_asset_version"])

    def test_a_missing_candidate_is_null_not_a_fabricated_id(self):
        rec = _Recorder()
        with mock.patch.object(adapter, "urlopen", rec):
            adapter.record_capcut_handoff(_report(), tenant_id="t", clip_id="c")
        self.assertIsNone(rec.calls[0]["payload"]["candidate_id"])


class NoFalseSuccessTests(unittest.TestCase):
    """No success row without a real generated project."""

    def test_nothing_is_recorded_without_a_specification(self):
        rec = _Recorder()
        with mock.patch.object(adapter, "urlopen", rec):
            for bad in (_report(spec=None), _report(spec="not-a-dict"), None, {}):
                self.assertIsNone(adapter.record_capcut_handoff(
                    bad, tenant_id="t", clip_id="c"))
        self.assertEqual(rec.calls, [])

    def test_nothing_is_recorded_without_a_media_hash(self):
        rec = _Recorder()
        with mock.patch.object(adapter, "urlopen", rec):
            self.assertIsNone(adapter.record_capcut_handoff(
                _report(media={}), tenant_id="t", clip_id="c"))
            self.assertIsNone(adapter.record_capcut_handoff(
                _report(media={"bytes": 10}), tenant_id="t", clip_id="c"))
        self.assertEqual(rec.calls, [])

    def test_a_failure_response_records_nothing(self):
        # The DB returning an empty representation is not a recorded handoff.
        rec = _Recorder(body=[])
        with mock.patch.object(adapter, "urlopen", rec):
            self.assertIsNone(adapter.record_capcut_handoff(
                _report(), tenant_id="t", clip_id="c"))

    def test_a_failed_handoff_never_reaches_this_helper(self):
        # The runner RAISES on every failure path, so the adapter's success
        # branch — the only caller — cannot run. Proven for the failure modes
        # that would otherwise leave a false success row. A REAL media file is
        # used so the failure under test is the CLI's, not media validation.
        import tempfile
        tmp = tempfile.mkdtemp(prefix="loom-prov-")
        media = os.path.join(tmp, "working.mp4")
        with open(media, "wb") as fh:
            fh.write(b"v" * 128)

        def fails(cmd, timeout):
            return 1, "", "refused"
        for invoke, expect in (
            (fails, "exit 1"),
            (lambda c, t: (0, "not json", ""), "not the expected JSON report"),
            (lambda c, t: (0, json.dumps({"ok": True,
                                          "file_path": "/nope/x.json"}), ""),
             "produced no draft"),
        ):
            with self.assertRaises(Exception) as ctx:
                cr.run_handoff(bootstrap={"source_range": {"start": 0, "end": 1}},
                               media_path=media, draft_store=tmp,
                               handoff_root=os.path.join(tmp, "handoff"),
                               invoke=invoke)
            self.assertIn(expect, str(ctx.exception))
        # Nothing was recorded, and no project directory was left behind.
        self.assertEqual([d for d in os.listdir(tmp) if d.startswith("LOOM")], [])


class NonFatalTests(unittest.TestCase):
    """Provenance must never be able to break the operator's lane."""

    def test_a_write_failure_is_swallowed_and_reported_as_none(self):
        rec = _Recorder(raises=OSError("db down"))
        with mock.patch.object(adapter, "urlopen", rec):
            self.assertIsNone(adapter.record_capcut_handoff(
                _report(), tenant_id="t", clip_id="c"))

    def test_a_json_failure_from_the_store_is_swallowed(self):
        class Bad(_CapturedResponse):
            def read(self):
                return b"<html>not json</html>"
        rec = _Recorder()
        rec.body = None
        with mock.patch.object(adapter, "urlopen",
                               lambda req, timeout=15: Bad([])):
            self.assertIsNone(adapter.record_capcut_handoff(
                _report(), tenant_id="t", clip_id="c"))


class RunnerProvidesProvenanceInputsTests(unittest.TestCase):
    """The runner must hand the adapter everything the record needs."""

    def test_the_runner_reports_the_spec_hash_origin_and_compiler(self):
        # The RUNNER is what must supply the provenance inputs.
        with open(cr.__file__, encoding="utf-8") as fh:
            source = fh.read()
        for token in ('"spec": spec', '"compiler": compiler_identity(cli_path)',
                      '"sha256": staged["sha256"]',
                      '"timebase_origin": coords.get("timebase_origin", 0.0)'):
            self.assertIn(token, source)

    def test_the_pinned_commit_matches_the_vendored_provenance(self):
        prov = _ROOT / "vendor" / "capcut-cli" / "PROVENANCE.md"
        if not prov.is_file():
            self.skipTest("vendor tree absent")
        text = prov.read_text(encoding="utf-8")
        self.assertIn(cr.PINNED_DONOR_COMMIT, text)
        self.assertIn(cr.PINNED_DONOR_URL, text)
        self.assertIn("MIT", text)

    def test_the_compiler_identity_is_read_from_the_vendored_manifest(self):
        ident = cr.compiler_identity()
        self.assertEqual(ident["name"], "capcut-cli")
        self.assertEqual(ident["commit"], cr.PINNED_DONOR_COMMIT)
        self.assertEqual(ident["license"], "MIT")
        if cr.capcut_invocation():
            self.assertTrue(ident["version"])


class SchemaTests(unittest.TestCase):
    """The migration matches the record the code writes."""

    def test_the_migration_defines_exactly_the_recorded_columns(self):
        sql = (_ROOT / "infra" / "supabase" /
               "042_loom_capcut_handoffs.sql").read_text(encoding="utf-8")
        for column in ("handoff_id", "tenant_id", "candidate_id", "clip_id",
                       "asset_version", "source_asset_version", "correction_id",
                       "media_sha256", "working_timebase_origin_s", "spec",
                       "compiler", "created_at"):
            self.assertIn(column, sql)
        self.assertIn("primary key", sql)
        self.assertIn("create table if not exists public.loom_capcut_handoffs", sql)

    def test_every_recorded_payload_key_exists_in_the_schema(self):
        rec = _Recorder()
        with mock.patch.object(adapter, "urlopen", rec):
            adapter.record_capcut_handoff(_report(), tenant_id="t", clip_id="c")
        sql = (_ROOT / "infra" / "supabase" /
               "042_loom_capcut_handoffs.sql").read_text(encoding="utf-8")
        for key in rec.calls[0]["payload"]:
            self.assertIn(key, sql, f"recorded key {key!r} missing from the schema")

    def test_the_migration_is_idempotent(self):
        sql = (_ROOT / "infra" / "supabase" /
               "042_loom_capcut_handoffs.sql").read_text(encoding="utf-8")
        self.assertIn("if not exists", sql)
        self.assertNotIn("drop table", sql.lower())


if __name__ == "__main__":
    unittest.main(verbosity=2)
