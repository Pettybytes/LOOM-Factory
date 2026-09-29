"""043 — the finalize registration carries the clean pre-hook source.

WHY THIS EXISTS (traced, not guessed)
-------------------------------------
An operator's Edit produced a version that reached final review with NO
clean_source_* identity at all: `register_edited_asset` (013) had no clean-source
parameter and its `loom_media` insert omitted those columns; 017 added the
columns and NOTHING wrote them. `resolve_clip_clean_source` then legitimately
returned nothing, the (correct) Edit readiness gate withheld Edit, and the card
said hook replacement was disabled — a rendered dead-end.

`043` gives the RPC four trailing defaulted, nullable parameters. The finalize
caller now PROPAGATES the clean sibling that already exists at finalize time
(the same local-output convention `register_clip_media` uses) and UPLOADS it
with the existing primitive.

These tests pin, without touching Supabase or Discord:
  derivation        the existing clean sibling is found, hashed, sized, uploaded
  honesty           no obtainable clean source -> {} -> NULLs on the RPC payload
  no fabrication    never another version's source, never a burned file as clean
  the gate          UNCHANGED: still withholds Edit without a reachable source
  one version       the propagation adds no second registration and no replay

HARNESS NOTE: `import acquisition` is primed BEFORE `import adapter`. Importing
adapter first dies at collection in this suite's environment.
"""

import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
for _p in (str(_ROOT / "infra" / "discord-review"), str(_ROOT / "infra"),
           str(_ROOT / "infra" / "acquisition")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import acquisition  # noqa: E402,F401  (order sentinel — MUST precede adapter)
import adapter  # noqa: E402


class _Patch:
    """Monkeypatch module attributes for the duration of a test."""

    def __init__(self, **attrs):
        self.attrs = attrs
        self.saved = {}

    def __enter__(self):
        for name, value in self.attrs.items():
            self.saved[name] = getattr(adapter, name)
            setattr(adapter, name, value)
        return self

    def __exit__(self, *exc):
        for name, value in self.saved.items():
            setattr(adapter, name, value)
        return False


class FakeResponse:
    def __init__(self, status, payload):
        self.status = status
        self._payload = json.dumps(payload).encode()

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeHandler:
    """Stands in for the adapter's request-handler response path."""

    def __init__(self, body=None, token="tok-editor"):
        self.responses = []
        self._body = json.dumps(body or {}).encode()
        self.headers = {"Authorization": f"Bearer {token}"} if token else {}

    def _read_body(self):
        return self._body

    def _send_json(self, code, payload):
        self.responses.append((code, payload))


def session_row(**over):
    row = {
        "id": "sess-1",
        "tenant_id": "dreemzy",
        "clip_id": "clip-abc:0",
        "source_asset_version": "render-1.2",
        "status": "active",
        "expires_at": None,
    }
    row.update(over)
    return row


class _JobWorkspace:
    """A temp OPENSHORTS_OUTPUT with one job dir, as the real render leaves it."""

    JOB = "loom-abc123def456"

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="loom-clean-")
        self.job_dir = os.path.join(self.tmp, self.JOB)
        os.makedirs(self.job_dir)
        self._saved_output = adapter.OPENSHORTS_OUTPUT
        adapter.OPENSHORTS_OUTPUT = self.tmp

    def tearDown(self):
        adapter.OPENSHORTS_OUTPUT = self._saved_output
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, name, data=b"clean-bytes"):
        path = os.path.join(self.job_dir, name)
        Path(path).write_bytes(data)
        return path


# ---------------------------------------------------------------------------
# 1 + 2 + 3. Derivation: the existing sibling, honesty, and no fabrication
# ---------------------------------------------------------------------------

class CleanSiblingDerivationTests(_JobWorkspace, unittest.TestCase):
    def setUp(self):
        _JobWorkspace.setUp(self)

    def test_the_existing_clean_sibling_is_found_hashed_sized_and_uploaded(self):
        clean = self.write("loom_abc123de_clip_1.mp4", b"c" * 2048)
        burned = self.write("hooked_1730000000_loom_abc123de_clip_1.mp4", b"b" * 512)
        uploads = []

        def fake_upload(path, name, parent_id=None):
            uploads.append((path, name))
            return {"url": "https://filesafe.example/clean.mp4", "fileId": "f-1"}

        with _Patch(ghl_upload_file=fake_upload):
            fields = adapter.derive_clean_source_for_edit(
                self.JOB, os.path.basename(burned))

        self.assertEqual(uploads, [(clean, "loom_abc123de_clip_1.mp4")],
                         "the derived sibling is what gets uploaded")
        self.assertEqual(fields["p_clean_source_url"],
                         "https://filesafe.example/clean.mp4")
        self.assertEqual(fields["p_clean_source_filename"], "loom_abc123de_clip_1.mp4")
        self.assertEqual(fields["p_clean_source_hash"],
                         hashlib.sha256(b"c" * 2048).hexdigest(),
                         "the hash must be of the clean bytes, never the burned ones")
        self.assertEqual(fields["p_clean_source_size_bytes"], 2048)

    def test_nested_caption_and_hook_prefixes_still_reach_the_clean_sibling(self):
        self.write("loom_abc123de_clip_1.mp4", b"c" * 64)
        burned = self.write("subtitled_173_hooked_172_loom_abc123de_clip_1.mp4",
                            b"b" * 8)
        with _Patch(ghl_upload_file=lambda p, n, parent_id=None: {"url": "u", "fileId": "i"}):
            fields = adapter.derive_clean_source_for_edit(
                self.JOB, os.path.basename(burned))
        self.assertEqual(fields["p_clean_source_filename"], "loom_abc123de_clip_1.mp4")

    def test_no_derivable_clean_sibling_yields_no_fields_and_no_upload(self):
        """A burned render whose clean sibling is gone is HONESTLY no source."""
        burned = self.write("hooked_173_loom_abc123de_clip_1.mp4", b"b" * 8)
        uploads = []
        with _Patch(ghl_upload_file=lambda p, n, parent_id=None: uploads.append(p) or {"url": "u"}):
            fields = adapter.derive_clean_source_for_edit(
                self.JOB, os.path.basename(burned))
        self.assertEqual(fields, {}, "no sibling -> {} (-> NULLs on the payload)")
        self.assertEqual(uploads, [], "nothing may be uploaded without a sibling")

    def test_a_burned_file_is_never_offered_as_its_own_clean_source(self):
        """If the strip could not walk back, the burned bytes must NOT be
        presented as clean — even though that file exists on disk."""
        burned = self.write("hooked_173_loom_abc123de_clip_1.mp4", b"b" * 8)
        with _Patch(_strip_clean_filename=lambda name: name,
                    ghl_upload_file=lambda p, n, parent_id=None: {"url": "u"}):
            fields = adapter.derive_clean_source_for_edit(
                self.JOB, os.path.basename(burned))
        self.assertEqual(fields, {})

    def test_another_jobs_clean_file_is_never_reused(self):
        """The sibling must come from THIS job's output directory. A same-named
        file in a different job is another version's artifact."""
        other = os.path.join(self.tmp, "loom-999999999999")
        os.makedirs(other)
        Path(os.path.join(other, "loom_abc123de_clip_1.mp4")).write_bytes(b"other" * 4)
        burned = self.write("hooked_173_loom_abc123de_clip_1.mp4", b"b" * 8)
        with _Patch(ghl_upload_file=lambda p, n, parent_id=None: {"url": "u"}):
            fields = adapter.derive_clean_source_for_edit(
                self.JOB, os.path.basename(burned))
        self.assertEqual(fields, {}, "never borrow another version's bytes")

    def test_a_durable_source_of_another_version_is_never_consulted(self):
        """The derivation reads the local output convention only — it must not
        reach for the lineage's durable clean_source_url."""
        self.write("loom_abc123de_clip_1.mp4", b"c" * 32)
        burned = self.write("hooked_173_loom_abc123de_clip_1.mp4", b"b" * 8)
        calls = []

        def forbidden(*a, **k):
            calls.append(a)
            return "https://filesafe.example/ANCESTOR-clean.mp4"

        with _Patch(resolve_clip_clean_source=forbidden,
                    ghl_upload_file=lambda p, n, parent_id=None: {"url": "local", "fileId": "i"}):
            fields = adapter.derive_clean_source_for_edit(
                self.JOB, os.path.basename(burned))
        self.assertEqual(calls, [], "must not resolve an ancestor's clean source")
        self.assertEqual(fields["p_clean_source_url"], "local")

    def test_a_failed_upload_yields_no_fields_rather_than_a_half_identity(self):
        self.write("loom_abc123de_clip_1.mp4", b"c" * 32)
        burned = self.write("hooked_173_loom_abc123de_clip_1.mp4", b"b" * 8)
        with _Patch(ghl_upload_file=lambda p, n, parent_id=None: None):
            self.assertEqual(adapter.derive_clean_source_for_edit(
                self.JOB, os.path.basename(burned)), {})

    def test_an_upload_without_a_url_is_not_a_clean_source(self):
        self.write("loom_abc123de_clip_1.mp4", b"c" * 32)
        burned = self.write("hooked_173_loom_abc123de_clip_1.mp4", b"b" * 8)
        with _Patch(ghl_upload_file=lambda p, n, parent_id=None: {"fileId": "f-1"}):
            self.assertEqual(adapter.derive_clean_source_for_edit(
                self.JOB, os.path.basename(burned)), {})

    def test_missing_inputs_are_inert(self):
        for job, ready in ((None, "x.mp4"), (self.JOB, None), (self.JOB, "")):
            with _Patch(ghl_upload_file=lambda p, n, parent_id=None: {"url": "u"}):
                self.assertEqual(adapter.derive_clean_source_for_edit(job, ready), {})


# ---------------------------------------------------------------------------
# 1 + 2. The four fields reach the RPC from the finalize caller
# ---------------------------------------------------------------------------

class FinalizePropagatesCleanSourceTests(_JobWorkspace, unittest.TestCase):
    def setUp(self):
        _JobWorkspace.setUp(self)

    def _complete(self, body, session=None, upload=None, rpc=None):
        captured = {"rpc": [], "uploads": []}

        def fake_rpc(payload):
            captured["rpc"].append(payload)
            return 200, {"asset_version": "render-1.3", "tenant_id": "dreemzy",
                         "clip_id": "clip-abc:0",
                         "source_asset_version": "render-1.2"}

        def fake_upload(path, name, parent_id=None):
            captured["uploads"].append((path, name))
            return upload or {"url": "https://filesafe.example/clean.mp4", "fileId": "f-1"}

        handler = FakeHandler(body)
        with _Patch(lookup_editor_token=lambda h: (session_row(), "token_table"),
                    resolve_edit_session=lambda sid: session or {
                        "status": "active", "tenant_id": "dreemzy",
                        "clip_id": "clip-abc:0", "source_asset_version": "render-1.2"},
                    rehydrate_edit_job=lambda *a: (self.JOB, 0),
                    ghl_upload_file=fake_upload,
                    register_edited_asset_rpc=rpc or fake_rpc,
                    mark_edited_storage_status=lambda *a: True,
                    check_existing_card=lambda *a: [],
                    send_review_card=lambda payload: {"message_id": "m-1"},
                    supabase_filter_get=lambda table, flt: []):
            adapter.Handler._handle_editor_complete(handler)
        return captured, handler

    def test_the_four_fields_carry_the_existing_sibling_into_the_rpc(self):
        self.write("loom_abc123de_clip_1.mp4", b"c" * 777)
        burned = self.write("hooked_173_loom_abc123de_clip_1.mp4", b"b" * 8)
        captured, handler = self._complete({"edit_session_id": "sess-1",
                                            "ready_file": os.path.basename(burned)})
        self.assertEqual(len(captured["rpc"]), 1)
        payload = captured["rpc"][0]
        self.assertEqual(payload.get("p_clean_source_url"),
                         "https://filesafe.example/clean.mp4")
        self.assertEqual(payload.get("p_clean_source_filename"), "loom_abc123de_clip_1.mp4")
        self.assertEqual(payload.get("p_clean_source_hash"),
                         hashlib.sha256(b"c" * 777).hexdigest())
        self.assertEqual(payload.get("p_clean_source_size_bytes"), 777)
        self.assertEqual(captured["uploads"],
                         [(os.path.join(self.job_dir, "loom_abc123de_clip_1.mp4"),
                           "loom_abc123de_clip_1.mp4")])
        self.assertEqual(handler.responses[-1][0], 200)

    def test_an_unobtainable_clean_source_leaves_the_fields_null(self):
        """Honest dead-end: the version registers, and stays truthfully
        replacement-unavailable rather than receiving a fabricated reference."""
        burned = self.write("hooked_173_loom_abc123de_clip_1.mp4", b"b" * 8)
        captured, handler = self._complete({"edit_session_id": "sess-1",
                                            "ready_file": os.path.basename(burned)})
        self.assertEqual(len(captured["rpc"]), 1, "the version still registers")
        payload = captured["rpc"][0]
        for key in ("p_clean_source_url", "p_clean_source_hash",
                    "p_clean_source_size_bytes", "p_clean_source_filename"):
            self.assertIsNone(payload.get(key), f"{key} must stay NULL")
        self.assertEqual(captured["uploads"], [])
        self.assertEqual(handler.responses[-1][0], 200)

    def test_a_failing_derivation_never_blocks_the_registration(self):
        self.write("loom_abc123de_clip_1.mp4", b"c" * 32)
        burned = self.write("hooked_173_loom_abc123de_clip_1.mp4", b"b" * 8)

        def exploding(*a, **k):
            raise RuntimeError("uploader down")

        captured = {"rpc": []}

        def fake_rpc(payload):
            captured["rpc"].append(payload)
            return 200, {"asset_version": "render-1.3", "tenant_id": "dreemzy",
                         "clip_id": "clip-abc:0", "source_asset_version": "render-1.2"}

        handler = FakeHandler({"edit_session_id": "sess-1",
                               "ready_file": os.path.basename(burned)})
        with _Patch(lookup_editor_token=lambda h: (session_row(), "token_table"),
                    resolve_edit_session=lambda sid: {
                        "status": "active", "tenant_id": "dreemzy",
                        "clip_id": "clip-abc:0", "source_asset_version": "render-1.2"},
                    rehydrate_edit_job=lambda *a: (self.JOB, 0),
                    ghl_upload_file=exploding,
                    register_edited_asset_rpc=fake_rpc,
                    mark_edited_storage_status=lambda *a: True,
                    check_existing_card=lambda *a: [],
                    send_review_card=lambda payload: {"message_id": "m-1"},
                    supabase_filter_get=lambda table, flt: []):
            adapter.Handler._handle_editor_complete(handler)

        self.assertEqual(len(captured["rpc"]), 1,
                         "an uploader failure must not lose the version")
        self.assertIsNone(captured["rpc"][0].get("p_clean_source_url"))
        self.assertEqual(handler.responses[-1][0], 200)

    def test_no_second_version_is_registered_by_the_propagation(self):
        self.write("loom_abc123de_clip_1.mp4", b"c" * 32)
        burned = self.write("hooked_173_loom_abc123de_clip_1.mp4", b"b" * 8)
        captured, handler = self._complete({"edit_session_id": "sess-1",
                                            "ready_file": os.path.basename(burned)})
        self.assertEqual(len(captured["rpc"]), 1,
                         "exactly one registration — the clean propagation adds none")

    def test_a_replay_registers_nothing_and_uploads_nothing(self):
        fp = adapter.submission_fingerprint({"edit_session_id": "sess-1"})
        handler = FakeHandler({"edit_session_id": "sess-1"})
        rpc_calls, uploads = [], []
        with _Patch(lookup_editor_token=lambda h: (session_row(), "token_table"),
                    resolve_edit_session=lambda sid: {
                        "status": "completed", "tenant_id": "dreemzy",
                        "clip_id": "clip-abc:0", "target_asset_version": "render-1.3"},
                    stored_submission_fingerprint=lambda sid: fp,
                    rehydrate_edit_job=lambda *a: (self.JOB, 0),
                    ghl_upload_file=lambda p, n, parent_id=None: uploads.append(p) or {"url": "u"},
                    register_edited_asset_rpc=lambda p: (rpc_calls.append(p), (200, {}))[1],
                    check_existing_card=lambda *a: [{"discord_message_id": "m-1"}]):
            adapter.Handler._handle_editor_complete(handler)
        code, payload = handler.responses[-1]
        self.assertEqual(code, 200)
        self.assertTrue(payload["idempotent"])
        self.assertEqual(rpc_calls, [])
        self.assertEqual(uploads, [], "a replay must not re-upload either")


# ---------------------------------------------------------------------------
# 4. The Edit readiness gate is UNCHANGED (and still withholds Edit)
# ---------------------------------------------------------------------------

class EditReadinessGateUnchangedTests(unittest.TestCase):
    """The gate is the thing 043 must make USABLE, never thing it relaxes."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="loom-gate-")
        self.master = os.path.join(self.tmp, "render-1.3.mp4")
        Path(self.master).write_bytes(b"v" * 4096)
        self.posted = []

        def fake_urlopen(req, timeout=None):
            self.posted.append(req)
            return FakeResponse(200, {"id": "msg-1", "attachments": []})

        self._patch = _Patch(
            urlopen=fake_urlopen,
            resolve_clip_review_channel=lambda: "chan-1",
            register_review_token=lambda token, identity: True,
            update_review_token_message=lambda h, mid: True,
            generate_review_proxy=lambda path, tmpdir: path,
            _card_upload_limit=lambda channel_id: (10 * 1024 * 1024, 0),
        )
        self._patch.__enter__()

    def tearDown(self):
        self._patch.__exit__(None, None, None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _card_for(self, clean_source, reachable):
        self.posted.clear()
        with _Patch(resolve_clip_clean_source=lambda t, c: clean_source,
                    _url_reachable=lambda url, timeout=8: reachable):
            result = adapter.send_review_card({
                "clip_id": "clip-abc:0", "job_id": "job-1",
                "tenant_id": "dreemzy", "asset_version": "render-1.3",
                "review_stage": "final_publication",
                "ready_file": os.path.basename(self.master),
                "master_path": self.master,
            })
        self.assertTrue(result.get("message_id"))
        self.assertEqual(len(self.posted), 1, "exactly one card POST")
        return self._components_of(self.posted[0])

    @staticmethod
    def _components_of(req):
        body = req.data.decode("utf-8", "replace")
        start = body.index('name="payload_json"')
        head = body.index("\r\n\r\n", start) + 4
        payload = json.loads(body[head:body.index("\r\n", head)])
        return payload["components"]

    @staticmethod
    def _labels(components):
        return [b["label"] for row in components for b in row["components"]]

    def test_edit_is_withheld_when_the_clean_source_is_missing(self):
        labels = self._labels(self._card_for(None, False))
        self.assertNotIn("Edit", labels,
                         "no clean source -> no Edit control (043 must not weaken this)")

    def test_edit_is_withheld_when_the_clean_source_is_unreachable(self):
        labels = self._labels(self._card_for("https://filesafe.example/x.mp4", False))
        self.assertNotIn("Edit", labels, "present but unreachable is still not ready")

    def test_the_card_says_why_edit_is_unavailable(self):
        comps = self._card_for(None, False)
        # The warning field is part of the same contract as the missing button.
        body = self.posted[0].data.decode("utf-8", "replace")
        self.assertIn("Editor source unavailable", body)

    def test_edit_is_offered_once_a_reachable_clean_source_exists(self):
        labels = self._labels(self._card_for("https://filesafe.example/x.mp4", True))
        self.assertIn("Edit", labels)

    def test_the_decision_actions_are_unchanged(self):
        labels = self._labels(self._card_for("https://filesafe.example/x.mp4", True))
        for expected in ("Reject", "Hold"):
            self.assertIn(expected, labels)

    def test_the_gate_still_resolves_through_the_lineage_resolver(self):
        """A final_publication card resolves clean via resolve_clip_clean_source
        — the same call 043 relies on, never a second source of truth."""
        seen = []

        def recorder(tenant, clip):
            seen.append((tenant, clip))
            return None

        with _Patch(resolve_clip_clean_source=recorder,
                    _url_reachable=lambda url, timeout=8: False):
            adapter.send_review_card({
                "clip_id": "clip-abc:0", "job_id": "job-1",
                "tenant_id": "dreemzy", "asset_version": "render-1.3",
                "review_stage": "final_publication",
                "ready_file": os.path.basename(self.master),
                "master_path": self.master,
            })
        self.assertEqual(seen, [("dreemzy", "clip-abc:0")])


# ---------------------------------------------------------------------------
# The RPC contract: four trailing, defaulted, nullable parameters (043)
# ---------------------------------------------------------------------------

class RpcCarriesTheCleanSourceTests(unittest.TestCase):
    def test_the_rpc_forwards_every_payload_key_verbatim(self):
        """`register_edited_asset_rpc` posts the payload as-is, so the four
        p_clean_source_* names are the only thing that had to be added."""
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["url"] = req.full_url
            captured["body"] = json.loads(req.data.decode())
            return FakeResponse(200, {"asset_version": "render-1.3"})

        with _Patch(urlopen=fake_urlopen):
            status, result = adapter.register_edited_asset_rpc({
                "p_edit_session_id": "sess-1",
                "p_clean_source_url": "https://filesafe.example/clean.mp4",
                "p_clean_source_hash": "abc",
                "p_clean_source_size_bytes": 12,
                "p_clean_source_filename": "loom_x_clip_1.mp4",
            })

        self.assertEqual(status, 200)
        self.assertTrue(captured["url"].endswith("/rpc/register_edited_asset"))
        for key in ("p_clean_source_url", "p_clean_source_hash",
                    "p_clean_source_size_bytes", "p_clean_source_filename"):
            self.assertIn(key, captured["body"])

    def test_the_043_migration_declares_the_four_nullable_defaulted_params(self):
        """The caller and the contract must agree; the SQL is the contract."""
        sql = (_ROOT / "infra" / "supabase"
               / "043_register_edited_asset_clean_source.sql").read_text()
        for param, typ in (("p_clean_source_url", "text"),
                           ("p_clean_source_hash", "text"),
                           ("p_clean_source_size_bytes", "bigint"),
                           ("p_clean_source_filename", "text")):
            self.assertIn(f"{param} {typ} default null", sql,
                          f"{param} must be nullable-by-default in 043")
        self.assertIn("clean_source_url, clean_source_hash, "
                      "clean_source_size_bytes, clean_source_filename", sql,
                      "the insert must write the four columns")


if __name__ == "__main__":
    unittest.main()
