"""Focused tests: REMOTE-ONLY durable working media -> LOCAL CapCut handoff media.

The operator's live failure: an unedited, manually-intaked Kick working clip was
playable in the editor from its DURABLE GHL asset, but "Finish in CapCut" failed
with ``no local working media for the handoff (got '')`` — because the handoff
only ever looked for a RENDERED local output and never considered the durable
object this host does not keep a copy of.

What these tests protect:
  * a local copy is still used first, with NO download (unchanged behaviour);
  * when only the durable object exists it is materialised and VERIFIED against
    the durable sha256/size before the runner is given a path — the transport
    copy is the working asset, so CapCut keeps receiving working-relative
    coordinates and no second ORIGINAL offset can appear;
  * materialisation is transport, never acquisition: one GET of the durable URL,
    no platform/VOD fetch, no re-acquisition, no shelling out, docker or ffmpeg;
  * the CURRENT version stays authoritative and a remote current render never
    falls back to the original source asset;
  * a failure to obtain+verify means NO CapCut project, NO provenance row, and a
    clear operator error — the fail-closed guard is untouched;
  * a success writes EXACTLY ONE provenance row describing the bytes actually
    handed over, and leaves the canonical GHL object byte-identical.

No network: the CDN GET is stubbed; the vendored CLI is never executed.
"""

import hashlib
import json
import os
import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
for directory in ("acquisition", "discord-review"):
    sys.path.insert(0, str(ROOT / "infra" / directory))

# The adapter pulls in LOOM's acquisition stack, whose `import acquisition`
# expects the MODULE (acquisition.py) and only resolves while infra/acquisition
# is ahead of infra/ on sys.path — the accepted test pattern above. Importing it
# here, BEFORE any other module can cache the same-named package, keeps the
# accepted version contract importable whatever order the suite discovers files
# in (an earlier suite file can leave infra/ on sys.path with the acquisition
# path already present, so its own guarded insert is skipped).
import acquisition  # noqa: E402,F401

# The adapter reads configuration at import time; inert values only.
for _k, _v in (("LOOM_DISCORD_APP_ID", "test-inert"),
               ("LOOM_DISCORD_PUBLIC_KEY", "test-inert"),
               ("LOOM_DISCORD_BOT_TOKEN", "test-inert"),
               ("LOOM_SUPABASE_URL", "https://test.supabase.co"),
               ("LOOM_SUPABASE_SERVICE_ROLE_KEY", "test-inert")):
    os.environ.setdefault(_k, _v)

# Prime the `acquisition` module BEFORE anything that drags in acquisition_ops ->
# approved_lane, which reads acquisition.DESTINATION_PERSONAL_REACTION_LIBRARY at
# import time. With the infra/acquisition root already on sys.path this resolves to
# acquisition.py; leaving it cold lets the chain resolve it unprimed and raises
# AttributeError. This mirrors the accepted test pattern used by the other suites.
import acquisition  # noqa: E402,F401

import adapter                                                # noqa: E402
import capcut_handoff as ch                                   # noqa: E402
import capcut_runner as cr                                    # noqa: E402

TENANT = "dreemzy"
FINGERPRINT = "f1d6caad6e879cb31d8b6e66eaf17c7706e5b0328748487fe1e8158bad3b3315"
CLIP_ID = f"src-{FINGERPRINT}:0"
JOB_ID = f"src-{FINGERPRINT}"
ASSET_VERSION = "source-1.0"
# The durable GHL object the operator's editor actually plays (filesafe CDN).
DURABLE_URL = ("https://assets.cdn.filesafe.space/GNhV1OyhFLzcl67XwTBV/media/"
               "4fd8699d-44fe-4913-a0ab-b196d4095d5d.mp4")
DURABLE_NAME = "kick__05e14bff-67ca-492a-ba79-3f3248ca41a5.mp4"
# The ORIGINAL platform source this working asset was cut from. Materialisation
# must never touch it.
PLATFORM_URL = "https://kick.com/deenthegreat/videos/05e14bff-67ca-492a-ba79-3f3248ca41a5"
ORIGINAL_WINDOW = {"start": 1800.0, "end": 1860.0}
TIMEBAKE_ORIGIN = 1800.0
WORKING_END = 60.016016
WORKING_BYTES = (b"KICKWORKINGASSET" * 256)


def _sha(payload):
    return hashlib.sha256(payload).hexdigest()


WORKING_SHA = _sha(WORKING_BYTES)
WORKING_SIZE = len(WORKING_BYTES)


class _AttrPatch:
    """Monkeypatch module attributes for the duration of one test."""

    def __init__(self, module, **attrs):
        self.module = module
        self.attrs = attrs
        self.saved = {}

    def __enter__(self):
        for name, value in self.attrs.items():
            self.saved[name] = getattr(self.module, name)
            setattr(self.module, name, value)
        return self

    def __exit__(self, *exc):
        for name, value in self.saved.items():
            setattr(self.module, name, value)
        return False


class _FakeHTTP:
    """A byte stream, as urlopen returns it to the chunked downloader."""

    def __init__(self, payload, status=200):
        self._payload = payload
        self._pos = 0
        self.status = status

    def read(self, size=-1):
        if size is None or size < 0:
            chunk = self._payload[self._pos:]
        else:
            chunk = self._payload[self._pos:self._pos + size]
        self._pos += len(chunk)
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _UrlOpener:
    """Stands in for adapter.urlopen; records every Request it is handed."""

    def __init__(self, payload=b"", status=200):
        self.payload = payload
        self.status = status
        self.requests = []

    def __call__(self, req, timeout=None):
        self.requests.append(req)
        return _FakeHTTP(self.payload, self.status)

    @property
    def urls(self):
        return [r.full_url for r in self.requests]


class _DownloadStub:
    """Stands in for adapter._download: copies the durable object's bytes."""

    def __init__(self, source_path=None, ok=True, payload=None):
        self.source_path = source_path
        self.ok = ok
        self.payload = payload
        self.calls = []

    def __call__(self, url, dest, timeout=120):
        self.calls.append(url)
        if not self.ok:
            return False
        if self.payload is not None:
            Path(dest).write_bytes(self.payload)
        else:
            shutil.copyfile(self.source_path, dest)
        return os.path.exists(dest) and os.path.getsize(dest) > 0


class _Handler:
    """The adapter's response path, without the HTTP server."""

    def __init__(self, body=None, token="tok-editor"):
        self.headers = {"Authorization": f"Bearer {token}"} if token else {}
        self._body = json.dumps(body or {"edit_session_id": "sess-1"}).encode()
        self.responses = []

    def _read_body(self):
        return self._body

    def _send_json(self, code, payload):
        self.responses.append((code, payload))

    @property
    def last(self):
        return self.responses[-1] if self.responses else (None, None)


def fake_ok(store):
    """An injected CLI that behaves like a successful compile.

    It writes an EMPTY draft, so this file must never assert on a real draft's
    internal `materials` graph (that would need the vendored CLI). The "the
    draft carries its own media copy" proof is therefore made against the REAL
    vendored CLI in
    test_capcut_runner.py::test_the_draft_carries_its_own_media_copy.
    """
    def invoke(cmd, timeout):
        if 'compile' not in cmd:
            return 0, json.dumps({'ok': True, 'in_sync': True}), ''
        target = cmd[cmd.index("--out") + 1]
        os.makedirs(target, exist_ok=True)
        Path(target, "draft_content.json").write_text("{}")
        return 0, json.dumps({"ok": True, "name": "x", "draft_path": target,
                              "file_path": os.path.join(target,
                                                        "draft_content.json")}), ""
    return invoke


def _endpoint(extra_attrs=None):
    """Drive the real `_handle_editor_capcut` with the LOOM surface stubbed."""
    handler = _Handler()
    session = {"edit_session_id": "sess-1", "tenant_id": TENANT,
               "clip_id": CLIP_ID, "source_asset_version": ASSET_VERSION}
    attrs = {"resolve_editor_session_token_diag": lambda token, **kw: (session, "ok"),
             "supabase_filter_get": lambda *a, **k: []}
    attrs.update(extra_attrs or {})
    return handler, session, attrs


def _endpoint_patches(durable, extra_attrs=None):
    attrs = {"resolve_durable_bootstrap_diag":
             lambda t, c, v: (dict(durable), "ok")}
    attrs.update(extra_attrs or {})
    _, _, merged = _endpoint(attrs)
    return merged


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="loom-capcut-materialize-")
        # The durable object's bytes, as the CDN holds them.
        self.durable_bytes = os.path.join(self.tmp, "cdn-object.mp4")
        Path(self.durable_bytes).write_bytes(WORKING_BYTES)
        self.output = os.path.join(self.tmp, "openshorts-output")
        self.store = os.path.join(self.tmp, "drafts")
        self.handoffs = os.path.join(self.tmp, "handoffs")
        self.transport = os.path.join(self.tmp, "transport")
        from unittest.mock import patch
        request_root = patch.object(cr, 'DEFAULT_HANDOFF_ROOT', self.handoffs)
        request_root.start()
        self.addCleanup(request_root.stop)
        os.makedirs(self.output, exist_ok=True)
        os.makedirs(self.store, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- fixtures ---------------------------------------------------------
    def durable(self, **over):
        """The CURRENT working asset's durable record (src/ source lane)."""
        record = {
            "media_url": DURABLE_URL,
            "clean_source_url": DURABLE_URL,
            "source_filename": DURABLE_NAME,
            "asset_version": ASSET_VERSION,
            "version_mode": "source_lineage",
            "source_range": {"start": 0.0, "end": WORKING_END},
            "duration_seconds": round(WORKING_END, 2),
            "media_sha256": WORKING_SHA,
            "media_size_bytes": WORKING_SIZE,
            "working_timebase_origin_s": TIMEBAKE_ORIGIN,
            "original_window": dict(ORIGINAL_WINDOW),
            "duplicate_fingerprint": FINGERPRINT,
            "hook": "", "title": "", "captions": [],
        }
        record.update(over)
        return record

    def patch_transport(self):
        return _AttrPatch(adapter, CAPCUT_HANDOFF_MEDIA_ROOT=self.transport,
                          OPENSHORTS_OUTPUT=self.output)

    def place_local_working_copy(self, record):
        """A rendered local copy, exactly where the resolver looks for one."""
        directory = os.path.join(self.output, JOB_ID)
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, os.path.basename(record["source_filename"]))
        Path(path).write_bytes(WORKING_BYTES)
        return path

    def hermetic_runner(self):
        """The REAL runner, pinned to hermetic scratch/draft roots.

        The real callable is captured HERE, before the caller's ``_AttrPatch(cr,
        run_handoff=...)`` replaces ``cr.run_handoff``: calling the module
        attribute from inside the stub would re-enter the stub (it IS the module
        attribute for the duration of the patch) and recurse forever.
        """
        real_run_handoff = cr.run_handoff

        def run_handoff(**kwargs):
            kwargs.setdefault("handoff_root", self.handoffs)
            kwargs.setdefault("draft_store", self.store)
            kwargs.setdefault("invoke", fake_ok(self.store))
            return real_run_handoff(**kwargs)
        return run_handoff


# ---------------------------------------------------------------------------
# (1)(2)(3) local-first, then the durable object materialised and verified
# ---------------------------------------------------------------------------
class MaterializationTests(_Base):
    def test_1_a_local_working_copy_is_used_with_no_download(self):
        record = self.durable()
        local = self.place_local_working_copy(record)
        download = _DownloadStub(self.durable_bytes)
        with self.patch_transport(), _AttrPatch(adapter, _download=download):
            path, how = adapter.materialize_local_working_media(JOB_ID, record)
        self.assertEqual(path, local,
                         "an existing local working copy must still win")
        self.assertEqual(how, "local_existing")
        self.assertEqual(download.calls, [],
                         "a local copy must never trigger a download")

    def test_2_remote_only_media_is_materialized_and_the_handoff_works(self):
        record = self.durable()
        download = _DownloadStub(self.durable_bytes)
        with self.patch_transport(), _AttrPatch(adapter, _download=download):
            self.assertEqual(adapter.resolve_local_working_media(JOB_ID, record),
                             "", "fixture must start remote-only")
            path, how = adapter.materialize_local_working_media(JOB_ID, record)
            self.assertEqual(how, "materialized")
            self.assertTrue(os.path.isfile(path))
            self.assertEqual(download.calls, [DURABLE_URL],
                             "exactly one GET, of the durable object")
            with _AttrPatch(cr, run_handoff=self.hermetic_runner()):
                report = cr.run_handoff(bootstrap=record, media_path=path,
                                        caption_blocks=[], asset_version=ASSET_VERSION,
                                        handoff_root=self.handoffs,
                                        draft_store=self.store,
                                        invoke=fake_ok(self.store))
        self.assertTrue(report["ok"])
        self.assertTrue(os.path.isfile(report["draft_file"]),
                        "the handoff must produce a real draft")
        self.assertEqual(report["media"]["source_path"], path)
        self.assertEqual(report["media"]["sha256"], WORKING_SHA,
                         "the bytes handed to CapCut are the durable working "
                         "asset's own bytes")
        self.assertEqual(report["media"]["bytes"], WORKING_SIZE)

    def test_3_materialized_bytes_and_hash_match_the_working_asset(self):
        record = self.durable()
        opener = _UrlOpener(WORKING_BYTES)
        with self.patch_transport(), _AttrPatch(adapter, urlopen=opener):
            path, how = adapter.materialize_local_working_media(JOB_ID, record)
        self.assertEqual(how, "materialized")
        payload = Path(path).read_bytes()
        self.assertEqual(len(payload), WORKING_SIZE)
        self.assertEqual(_sha(payload), WORKING_SHA)
        self.assertEqual([r.get_method() for r in opener.requests], ["GET"])
        self.assertIn("Mozilla", opener.requests[0].get_header("User-agent") or "",
                      "the filesafe CDN 403s the default urllib UA")
        self.assertEqual(adapter.verify_working_media_copy(path, record), True)

    def test_3b_a_wrong_object_is_refused_and_not_hidden(self):
        record = self.durable()
        # The CDN answers with DIFFERENT bytes of the SAME length, so the HASH
        # guard is the one that must refuse them (a shorter payload would be
        # caught by the size guard first and never reach the hash comparison).
        download = _DownloadStub(payload=b"x" * WORKING_SIZE)
        with self.patch_transport(), _AttrPatch(adapter, _download=download):
            path, reason = adapter.materialize_local_working_media(JOB_ID, record)
        self.assertEqual(path, "")
        self.assertEqual(reason, "durable_media_hash_mismatch")
        self.assertEqual(os.listdir(os.path.join(self.transport,
                                                 os.listdir(self.transport)[0])), [],
                         "an unverified transport copy must not be left behind")

    def test_3d_a_truncated_object_is_refused_by_size(self):
        """The other half of the identity gate: right name, wrong length."""
        record = self.durable()
        download = _DownloadStub(payload=WORKING_BYTES[:WORKING_SIZE // 2])
        with self.patch_transport(), _AttrPatch(adapter, _download=download):
            path, reason = adapter.materialize_local_working_media(JOB_ID, record)
        self.assertEqual(path, "")
        self.assertEqual(reason, "durable_media_size_mismatch")
        self.assertEqual(os.listdir(os.path.join(self.transport,
                                                 os.listdir(self.transport)[0])), [],
                         "an unverified transport copy must not be left behind")

    def test_3c_a_record_with_no_durable_identity_fails_closed(self):
        record = self.durable(media_sha256="", media_size_bytes=None)
        download = _DownloadStub(self.durable_bytes)
        with self.patch_transport(), _AttrPatch(adapter, _download=download):
            path, reason = adapter.materialize_local_working_media(JOB_ID, record)
        self.assertEqual(path, "")
        self.assertEqual(reason, "durable_media_identity_unavailable")
        self.assertEqual(download.calls, [], "nothing unverifiable may be fetched")


# ---------------------------------------------------------------------------
# (4)(5) the CURRENT version is authoritative; no remote->original fallback
# ---------------------------------------------------------------------------
class CurrentVersionTests(_Base):
    def _rows(self):
        # Durable rows as the store really holds them: `duration_seconds` is a
        # REQUIRED field for an unedited version (a version with no correction
        # takes its full 0 -> duration range from it), and `file_hash`/`size_bytes`
        # describe EXACTLY the object at this row's own `canonical_url` — that is
        # what makes the identity verifiable rather than invented. The ancestor
        # row deliberately carries a different, unverifiable identity so a fallback
        # to it cannot pass silently.
        return [
            {"tenant_id": TENANT, "clip_id": "clip-1:0", "asset_version": "render-1.0",
             "canonical_url": "https://cdn.example/original-render.mp4",
             "file_hash": "a" * 64, "size_bytes": 111,
             "duration_seconds": round(WORKING_END, 2),
             "previous_asset_version": None, "storage_status": "uploaded"},
            {"tenant_id": TENANT, "clip_id": "clip-1:0", "asset_version": "render-1.1",
             "canonical_url": "https://cdn.example/current-render.mp4",
             "file_hash": WORKING_SHA, "size_bytes": WORKING_SIZE,
             "duration_seconds": round(WORKING_END, 2),
             "previous_asset_version": "render-1.0", "storage_status": "uploaded"},
        ]

    def test_4_the_pinned_current_version_supplies_the_asset_identity(self):
        rows = self._rows()

        def supabase_filter_get(table, params):
            return rows if table == "loom_media" else []

        with _AttrPatch(adapter, supabase_filter_get=supabase_filter_get,
                        supabase_get=lambda *a, **k: []):
            record = adapter.resolve_durable_bootstrap(TENANT, "clip-1:0",
                                                       "render-1.1")
        self.assertEqual(record["asset_version"], "render-1.1")
        self.assertEqual(record["media_url"], "https://cdn.example/current-render.mp4")
        self.assertEqual(record["media_sha256"], WORKING_SHA,
                         "identity must come from the CURRENT row's own hash")
        self.assertEqual(record["media_size_bytes"], WORKING_SIZE)

        opener = _UrlOpener(WORKING_BYTES)
        with self.patch_transport(), _AttrPatch(adapter, urlopen=opener):
            path, how = adapter.materialize_local_working_media("clip-1:0", record)
        self.assertEqual(how, "materialized")
        self.assertEqual(opener.urls, ["https://cdn.example/current-render.mp4"],
                         "only the CURRENT version's object may be fetched")

    def test_5_a_remote_current_render_never_falls_back_to_the_source(self):
        rows = self._rows()

        def supabase_filter_get(table, params):
            return rows if table == "loom_media" else []

        with _AttrPatch(adapter, supabase_filter_get=supabase_filter_get,
                        supabase_get=lambda *a, **k: []):
            record = adapter.resolve_durable_bootstrap(TENANT, "clip-1:0",
                                                       "render-1.1")
        self.assertNotEqual(record["media_url"],
                            "https://cdn.example/original-render.mp4",
                            "the current remote render must not resolve to the "
                            "original source asset")
        self.assertNotEqual(record["media_sha256"], "a" * 64)

        download = _DownloadStub(payload=WORKING_BYTES)
        with self.patch_transport(), _AttrPatch(adapter, _download=download):
            adapter.materialize_local_working_media("clip-1:0", record)
        self.assertEqual(download.calls, ["https://cdn.example/current-render.mp4"])


# ---------------------------------------------------------------------------
# (6)(7) transport, not acquisition: no platform fetch, no VOD, no shelling out
# ---------------------------------------------------------------------------
class NoReacquisitionTests(_Base):
    def test_6_no_platform_downloader_is_invoked(self):
        record = self.durable()
        download = _DownloadStub(self.durable_bytes)

        def explode(*a, **k):
            raise AssertionError("materialisation must not shell out")

        with self.patch_transport(), \
                _AttrPatch(adapter, _download=download, subprocess=types.SimpleNamespace(run=explode)):
            path, how = adapter.materialize_local_working_media(JOB_ID, record)
        self.assertEqual(how, "materialized")
        self.assertEqual(download.calls, [DURABLE_URL])
        self.assertNotIn("kick.com", " ".join(download.calls),
                         "the platform source must never be requested")
        self.assertTrue(os.path.isfile(path))

    def test_7_only_the_bounded_working_asset_is_fetched(self):
        record = self.durable()
        download = _DownloadStub(self.durable_bytes)
        for name in os.listdir(self.tmp):
            self.assertNotEqual(name, "full-vod.mp4")
        with self.patch_transport(), _AttrPatch(adapter, _download=download):
            path, how = adapter.materialize_local_working_media(JOB_ID, record)
        self.assertEqual(how, "materialized")
        self.assertEqual(len(download.calls), 1)
        self.assertEqual(os.path.getsize(path), WORKING_SIZE,
                         "exactly the working asset's bytes, never a longer source")
        self.assertNotIn(PLATFORM_URL, download.calls)


# ---------------------------------------------------------------------------
# (8)(9) WORKING coordinates stay working-relative through the endpoint
# ---------------------------------------------------------------------------
class WorkingCoordinateTests(_Base):
    def _run(self, record):
        handler = _Handler()
        captured = []

        def record_row(report, **kwargs):
            captured.append(report)
            return {"handoff_id": "coords"}

        with _AttrPatch(adapter,
                        CAPCUT_HANDOFF_MEDIA_ROOT=self.transport,
                        OPENSHORTS_OUTPUT=self.output,
                        record_capcut_handoff=record_row,
                        **_endpoint_patches(record, {"_download":
                            _DownloadStub(self.durable_bytes)})), \
                _AttrPatch(cr, run_handoff=self.hermetic_runner()):
            adapter.Handler._handle_editor_capcut(handler)
        code, body = handler.last
        # The compiled spec is the durable BEFORE artifact: the endpoint records
        # it and strips it from the browser payload, so the coordinate contract
        # is asserted against the RECORDED report (the payload that still
        # genuinely carries the spec) as well as the returned body.
        return code, body, (captured[0] if captured else None)

    def test_8_the_spec_carries_working_relative_coordinates(self):
        code, body, report = self._run(self.durable())
        self.assertEqual(code, 200, body)
        self.assertNotIn("spec", body,
                         "the compiled spec is provenance, not browser payload")
        item = report["spec"]["tracks"][0]["items"][0]
        self.assertEqual(item["start"], 0)
        self.assertEqual(item["sourceStart"], 0.0,
                         "working t=0 is the trim-in; CapCut is handed the "
                         "working asset")
        self.assertEqual(item["duration"], 60.016)
        self.assertEqual(body["coordinates"]["capcut"]["sourceStart"], 0.0)
        self.assertEqual(body["coordinates"]["working"]["start"], 0.0)

    def test_9_the_original_pair_is_reported_but_never_applied(self):
        code, body, report = self._run(self.durable())
        self.assertEqual(code, 200, body)
        item = report["spec"]["tracks"][0]["items"][0]
        # A second ORIGINAL offset would show up as 1800 (or 3600) here.
        self.assertEqual(item["sourceStart"], 0.0)
        self.assertEqual(item["duration"], 60.016)
        self.assertEqual(body["coordinates"]["timebase_origin"], 1800.0)
        self.assertEqual(body["coordinates"]["original"], {"start": 1800.0,
                                                           "end": 1860.0})
        self.assertNotIn(1800.0, [item["start"], item["sourceStart"], item["duration"]])


# ---------------------------------------------------------------------------
# (10)(11)(12) failure writes nothing; success writes exactly one row
# ---------------------------------------------------------------------------
class ProvenanceTests(_Base):
    def test_10_a_materialization_failure_produces_no_capcut_project(self):
        record = self.durable()
        calls = {"handoff": 0, "provenance": 0}

        def run_handoff(**kwargs):
            calls["handoff"] += 1
            raise AssertionError("no project may be attempted without media")

        with _AttrPatch(adapter,
                        CAPCUT_HANDOFF_MEDIA_ROOT=self.transport,
                        OPENSHORTS_OUTPUT=self.output,
                        record_capcut_handoff=lambda *a, **k: calls.__setitem__(
                            "provenance", calls["provenance"] + 1),
                        **_endpoint_patches(record, {"_download":
                            _DownloadStub(ok=False)})), \
                _AttrPatch(cr, run_handoff=run_handoff):
            handler = _Handler()
            adapter.Handler._handle_editor_capcut(handler)
        code, body = handler.last
        self.assertEqual(code, 409)
        self.assertEqual(body["error"], "working_media_unavailable")
        self.assertEqual(body["reason"], "durable_media_download_failed")
        self.assertEqual(calls["handoff"], 0, "the runner must never be reached")
        self.assertEqual(calls["provenance"], 0)
        self.assertEqual(os.listdir(self.store), [], "no draft may be created")

    def test_11_a_materialization_failure_writes_no_success_row(self):
        record = self.durable()
        recorded = []

        def record_row(report, **kwargs):
            recorded.append(report)
            return {"handoff_id": "should-not-exist"}

        with _AttrPatch(adapter,
                        CAPCUT_HANDOFF_MEDIA_ROOT=self.transport,
                        OPENSHORTS_OUTPUT=self.output,
                        record_capcut_handoff=record_row,
                        **_endpoint_patches(record, {"_download":
                            _DownloadStub(ok=False)})):
            handler = _Handler()
            adapter.Handler._handle_editor_capcut(handler)
        self.assertEqual(handler.last[0], 409)
        self.assertEqual(recorded, [],
                         "a refused handoff must leave no provenance row")
        self.assertEqual(handler.last[1].get("provenance"), None)

    def test_12_a_remote_materialized_success_writes_exactly_one_row(self):
        record = self.durable()
        opener = _UrlOpener(WORKING_BYTES)
        posts = {"provenance": [], "handoff": 0}

        def record_row(report, **kwargs):
            posts["provenance"].append({"report": report, "kwargs": kwargs})
            return {"handoff_id": "11111111-2222-3333-4444-555555555555"}

        # Captured before `_AttrPatch(cr, run_handoff=...)` below replaces the
        # module attribute, so this stub calls the REAL runner, not itself.
        real_run_handoff = cr.run_handoff

        def run_handoff(**kwargs):
            posts["handoff"] += 1
            kwargs.setdefault("handoff_root", self.handoffs)
            kwargs.setdefault("draft_store", self.store)
            kwargs.setdefault("invoke", fake_ok(self.store))
            return real_run_handoff(**kwargs)

        with _AttrPatch(adapter,
                        CAPCUT_HANDOFF_MEDIA_ROOT=self.transport,
                        OPENSHORTS_OUTPUT=self.output,
                        record_capcut_handoff=record_row,
                        **_endpoint_patches(record, {"_download":
                            _DownloadStub(self.durable_bytes)})), \
                _AttrPatch(cr, run_handoff=run_handoff):
            handler = _Handler()
            adapter.Handler._handle_editor_capcut(handler)
        code, body = handler.last
        self.assertEqual(code, 200, body)
        self.assertEqual(body["media_source"], "materialized")
        self.assertEqual(posts["handoff"], 1, "exactly one handoff")
        self.assertEqual(len(posts["provenance"]), 1, "exactly one provenance row")
        row = posts["provenance"][0]
        report = row["report"]
        self.assertEqual(report["media"]["sha256"], WORKING_SHA,
                         "the row must describe the bytes actually handed over")
        self.assertEqual(row["kwargs"]["tenant_id"], TENANT)
        self.assertEqual(row["kwargs"]["clip_id"], CLIP_ID)
        self.assertEqual(row["kwargs"]["candidate_id"], FINGERPRINT)
        self.assertEqual(report["asset_version"], ASSET_VERSION)
        self.assertEqual(report["coordinates"]["timebase_origin"], 1800.0)
        self.assertEqual(report["spec"]["tracks"][0]["items"][0]["sourceStart"], 0.0)
        self.assertTrue(report["compiler"]["commit"])
        self.assertTrue(body["provenance"]["recorded"])

    def test_12b_a_local_handoff_reports_its_source_and_still_records_once(self):
        record = self.durable()
        self.place_local_working_copy(record)
        recorded = []
        with _AttrPatch(adapter,
                        CAPCUT_HANDOFF_MEDIA_ROOT=self.transport,
                        OPENSHORTS_OUTPUT=self.output,
                        record_capcut_handoff=lambda report, **k: recorded.append(report)
                        or {"handoff_id": "local"}, **_endpoint_patches(record)), \
                _AttrPatch(cr, run_handoff=self.hermetic_runner()):
            handler = _Handler()
            adapter.Handler._handle_editor_capcut(handler)
        code, body = handler.last
        self.assertEqual(code, 200, body)
        self.assertEqual(body["media_source"], "local_existing")
        self.assertEqual(len(recorded), 1)


# ---------------------------------------------------------------------------
# (13)(14) the canonical object is untouched; the editor does not need CapCut
# ---------------------------------------------------------------------------
class NonInterferenceTests(_Base):
    def test_13_the_canonical_durable_object_is_never_mutated(self):
        record = self.durable()
        before = _sha(Path(self.durable_bytes).read_bytes())
        decoy = os.path.join(self.tmp, "canonical-decoy.mp4")
        Path(decoy).write_bytes(WORKING_BYTES)
        opener = _UrlOpener(WORKING_BYTES)
        with self.patch_transport(), _AttrPatch(adapter, urlopen=opener):
            path, how = adapter.materialize_local_working_media(JOB_ID, record)
            # A second attempt reuses the verified copy rather than re-fetching.
            again, how_again = adapter.materialize_local_working_media(JOB_ID, record)
        self.assertEqual(how, "materialized")
        self.assertEqual((again, how_again), (path, "transport_reused"))
        self.assertEqual(len(opener.requests), 1)
        self.assertEqual(_sha(Path(self.durable_bytes).read_bytes()), before,
                         "the canonical object's bytes must be unchanged")
        self.assertTrue(os.path.isfile(decoy),
                        "cleanup must never reach outside the transport root")
        self.assertEqual(os.listdir(os.path.dirname(path)), [os.path.basename(path)],
                         "one bounded file per (clip, version)")

    def test_14_normal_editor_operation_does_not_depend_on_capcut(self):
        record = self.durable()
        materialized = []
        downloads = []
        handler = _Handler()

        def supabase_filter_get(table, params):
            return []

        with _AttrPatch(adapter,
                        CAPCUT_HANDOFF_MEDIA_ROOT=self.transport,
                        OPENSHORTS_OUTPUT=self.output,
                        _download=lambda *a, **k: downloads.append(a) or True,
                        materialize_local_working_media=lambda *a, **k:
                            materialized.append(a) or ("", "unused"),
                        resolve_editor_session_token_diag=lambda token: (
                            {"edit_session_id": "sess-1", "tenant_id": TENANT,
                             "clip_id": CLIP_ID,
                             "source_asset_version": ASSET_VERSION}, "ok"),
                        resolve_durable_bootstrap_diag=lambda t, c, v: (
                            dict(record), "ok"),
                        supabase_filter_get=supabase_filter_get):
            adapter.Handler._handle_editor_bootstrap(handler)
        code, body = handler.last
        self.assertEqual(code, 200, body)
        self.assertEqual(body["media_url"], DURABLE_URL,
                         "the editor keeps streaming the durable URL")
        self.assertEqual(downloads, [], "ordinary editing must download nothing")
        self.assertEqual(materialized, [],
                         "materialisation is reached only by the CapCut lane")


if __name__ == "__main__":
    unittest.main(verbosity=2)
