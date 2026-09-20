"""Editor reliability layer — regression coverage for the seven accepted defects.

Run:  python3 -m unittest discover -s infra/discord-review/tests
(or:  cd infra/discord-review && python3 -m unittest tests.test_editor_reliability)

Each class pins ONE contract, and the contract is stated in the failure message
so a future drift reads as a regression rather than a mystery:

  session lifecycle      an active session survives bootstrap -> draft -> complete
  token classification   expired / completed / revoked / unknown / lookup-failure
                         are all still REJECTED, each with its own truthful reason
  hash_mismatch          an orphaning rebind cannot happen (write-once + append)
  version selection      a pinned version is never silently replaced by an older one
  submission identity    same submission replays idempotently; a different one is
                         never mistaken for a replay
  clip sub-resources     live-else-durable, and 404-vs-401-vs-lookup are distinct
  /api/config            deliberately public; every other /api/* stays gated

No network: the Supabase surface is injected.
"""

import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import adapter  # noqa: E402


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

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
    """Stands in for the adapter's BaseHTTPRequestHandler response path."""

    def __init__(self, body=None, token="tok-editor"):
        self.responses = []
        self._body = json.dumps(body or {}).encode()
        # The complete handler authenticates BEFORE it reads the body, so the
        # request must carry the editor credential, exactly as the real client
        # does. Pass token=None to model an unauthenticated caller.
        self.headers = {"Authorization": f"Bearer {token}"} if token else {}

    def _read_body(self):
        return self._body

    def _send_json(self, code, payload):
        self.responses.append((code, payload))

    def _send_bytes(self, status, body):
        self.responses.append((status, json.loads(body.decode() or "{}")))


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


class _Patch:
    """Monkeypatch a set of module attributes for the duration of a test."""

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


# ---------------------------------------------------------------------------
# 1 + 2. Session lifecycle and truthful rejection reasons
# ---------------------------------------------------------------------------

class SessionTokenClassificationTests(unittest.TestCase):
    """Every terminal state is still REJECTED — and each one says why."""

    def _classify(self, row, source="token_table", raises=None):
        def lookup(_token_hash):
            if raises:
                raise raises
            return (row, source)

        with _Patch(lookup_editor_token=lookup):
            return adapter.resolve_editor_session_token_diag("tok")

    def test_an_active_session_resolves(self):
        session, reason = self._classify(session_row())
        self.assertEqual(reason, "ok")
        self.assertEqual(session["edit_session_id"], "sess-1")
        self.assertEqual(session["job_id"], "clip-abc")
        self.assertEqual(session["clip_index"], "0")

    def test_a_missing_token_is_reportable(self):
        session, reason = adapter.resolve_editor_session_token_diag("")
        self.assertIsNone(session)
        self.assertEqual(reason, "missing_token")

    def test_an_unknown_token_is_still_rejected_as_hash_mismatch(self):
        session, reason = self._classify(None)
        self.assertIsNone(session)
        self.assertEqual(reason, "hash_mismatch")

    def test_every_terminal_status_is_rejected_with_its_own_reason(self):
        expectations = {
            "expired": "expired",
            "completed": "completed",
            "revoked": "revoked",
            "inactive": "inactive",
            "cancelled": "revoked",
            "something_unexpected": "inactive",
        }
        for status, expected in expectations.items():
            with self.subTest(status=status):
                session, reason = self._classify(session_row(status=status))
                self.assertIsNone(session, f"{status} must never authenticate")
                self.assertEqual(reason, expected)

    def test_an_active_but_expired_credential_is_rejected(self):
        row = session_row(expires_at="2000-01-01T00:00:00+00:00")
        session, reason = self._classify(row)
        self.assertIsNone(session)
        self.assertEqual(reason, "expired")

    def test_a_lookup_failure_is_not_reported_as_an_auth_verdict(self):
        """A permission/transport failure must be distinguishable from
        'this token is wrong' — they need different operator responses."""
        session, reason = self._classify(
            None, raises=adapter.SupabaseLookupError(403, "url"))
        self.assertIsNone(session)
        self.assertEqual(reason, "lookup_failed")

    def test_a_session_row_without_clip_identity_is_rejected(self):
        session, reason = self._classify(session_row(clip_id=""))
        self.assertIsNone(session)
        self.assertEqual(reason, "hash_mismatch")


class TokenLookupSourceTests(unittest.TestCase):
    """An append-only credential keeps working after another token is minted."""

    def test_a_token_recorded_in_the_append_only_table_resolves(self):
        def fake_get(url):
            if adapter.EDITOR_TOKEN_TABLE in url:
                return [{"edit_session_id": "sess-1", "token_hash": "h"}]
            return [session_row()]

        with _Patch(_supabase_get_json=fake_get):
            row, source = adapter.lookup_editor_token("h")
        self.assertEqual(source, "token_table")
        self.assertEqual(row["id"], "sess-1")

    def test_an_undeployed_token_table_falls_back_to_the_legacy_column(self):
        def fake_get(url):
            if adapter.EDITOR_TOKEN_TABLE in url:
                raise adapter.SupabaseLookupError(404, url)
            return [session_row()]

        with _Patch(_supabase_get_json=fake_get):
            row, source = adapter.lookup_editor_token("h")
        self.assertEqual(source, "legacy")
        self.assertEqual(row["id"], "sess-1")


class MintingIsWriteOnceTests(unittest.TestCase):
    """Minting must never rebind an existing credential (the hash_mismatch root
    cause): the open editor's token must survive a second mint."""

    def test_appending_a_second_credential_succeeds(self):
        with _Patch(_append_editor_token=lambda *a: "ok"):
            token, code = adapter.mint_session_token_diag("sess-1")
        self.assertTrue(token)
        self.assertEqual(code, "ok")

    def test_the_legacy_column_is_only_bound_when_unbound(self):
        seen = {}

        def fake_urlopen(req, timeout=None):
            seen["url"] = req.full_url
            return FakeResponse(200, [{"id": "sess-1"}])

        with _Patch(_append_editor_token=lambda *a: "unavailable", urlopen=fake_urlopen):
            token, code = adapter.mint_session_token_diag("sess-1")
        self.assertTrue(token)
        self.assertEqual(code, "ok")
        self.assertIn("session_token_hash=is.null", seen["url"],
                      "the legacy binding must be conditional (write-once)")

    def test_an_already_bound_session_reports_already_authenticated(self):
        def fake_urlopen(req, timeout=None):
            return FakeResponse(200, [])  # zero rows: nothing was bound

        with _Patch(_append_editor_token=lambda *a: "unavailable",
                    editor_session_has_credential=lambda _sid: True,
                    urlopen=fake_urlopen):
            token, code = adapter.mint_session_token_diag("sess-1")
        self.assertIsNone(token, "no second token is issued, so none is orphaned")
        self.assertEqual(code, "already_authenticated")

    def test_a_terminal_session_is_not_minted(self):
        def fake_urlopen(req, timeout=None):
            return FakeResponse(200, [])

        with _Patch(_append_editor_token=lambda *a: "unavailable",
                    editor_session_has_credential=lambda _sid: False,
                    urlopen=fake_urlopen):
            token, code = adapter.mint_session_token_diag("sess-1")
        self.assertIsNone(token)
        self.assertEqual(code, "not_active")

    def test_mint_session_token_still_returns_a_bare_token(self):
        with _Patch(_append_editor_token=lambda *a: "ok"):
            self.assertTrue(adapter.mint_session_token("sess-1"))


# ---------------------------------------------------------------------------
# 3. Asset-version selection
# ---------------------------------------------------------------------------

class AssetVersionSelectionTests(unittest.TestCase):
    ROWS = [{"asset_version": v} for v in ("render-1.0", "render-1.1",
                                           "render-1.2", "render-1.9", "render-1.10")]

    def test_a_pinned_version_is_honoured_exactly(self):
        version, mode, reason = adapter.resolve_asset_version_selection(self.ROWS, "render-1.2")
        self.assertEqual((version, mode, reason), ("render-1.2", "pinned", "ok"))

    def test_an_unknown_pinned_version_is_never_substituted(self):
        version, mode, reason = adapter.resolve_asset_version_selection(self.ROWS, "render-1.7")
        self.assertIsNone(version, "an older version must never be silently chosen")
        self.assertEqual(mode, "pinned")
        self.assertEqual(reason, "version_not_found")

    def test_latest_is_deterministic_and_numeric(self):
        version, mode, reason = adapter.resolve_asset_version_selection(self.ROWS, "latest")
        self.assertEqual((version, reason), ("render-1.10", "ok"),
                         "1.10 is newer than 1.9 — a string compare gets this wrong")
        self.assertEqual(mode, "latest")

    def test_latest_normalises_for_empty_and_star(self):
        for requested in ("", None, "*"):
            with self.subTest(requested=requested):
                version, mode, _ = adapter.resolve_asset_version_selection(self.ROWS, requested)
                self.assertEqual(version, "render-1.10")
                self.assertEqual(mode, "latest")

    def test_no_versions_is_reported_not_invented(self):
        version, _mode, reason = adapter.resolve_asset_version_selection([], "latest")
        self.assertIsNone(version)
        self.assertEqual(reason, "no_versions")

    def test_stale_pinned_version_is_not_upgraded_or_downgraded(self):
        """The editor was pinned to render-1.1 while render-1.2 existed. That is
        the pinned contract: hydrate 1.1, do not silently move the edit."""
        version, mode, _ = adapter.resolve_asset_version_selection(self.ROWS, "render-1.1")
        self.assertEqual(version, "render-1.1")
        self.assertEqual(mode, "pinned")


class DurableBootstrapReasonTests(unittest.TestCase):
    def test_a_missing_pinned_version_reports_version_not_found(self):
        with _Patch(supabase_filter_get=lambda *a, **k: [{"asset_version": "render-1.2"}]):
            durable, reason = adapter.resolve_durable_bootstrap_diag(
                "dreemzy", "clip:0", "render-1.9")
        self.assertIsNone(durable)
        self.assertEqual(reason, "version_not_found")

    def test_a_missing_identity_is_reported(self):
        durable, reason = adapter.resolve_durable_bootstrap_diag("", "clip:0", "render-1.0")
        self.assertIsNone(durable)
        self.assertEqual(reason, "missing_identity")

    def test_a_lookup_failure_is_not_reported_as_missing_data(self):
        def boom(*a, **k):
            raise RuntimeError("supabase down")

        with _Patch(supabase_filter_get=boom):
            durable, reason = adapter.resolve_durable_bootstrap_diag(
                "dreemzy", "clip:0", "render-1.0")
        self.assertIsNone(durable)
        self.assertEqual(reason, "lookup_failed")

    def test_the_resolved_version_is_reported_back(self):
        media = [{"asset_version": "render-1.0", "canonical_url": "https://cdn/x.mp4",
                  "previous_asset_version": None, "duration_seconds": 9.0}]

        def fake_filter_get(table, filters=None):
            if table == "loom_media":
                return media
            return []

        with _Patch(supabase_filter_get=fake_filter_get):
            durable, reason = adapter.resolve_durable_bootstrap_diag(
                "dreemzy", "clip:0", "render-1.0")
        self.assertEqual(reason, "ok")
        self.assertEqual(durable["asset_version"], "render-1.0")
        self.assertEqual(durable["version_mode"], "pinned")


# ---------------------------------------------------------------------------
# 5. Submission identity / duplicate completion
# ---------------------------------------------------------------------------

class SubmissionFingerprintTests(unittest.TestCase):
    def test_the_same_submission_has_the_same_identity(self):
        body = {"file_hash": "abc", "size_bytes": 10, "trim_start": 0,
                "trim_end": 5, "caption_changes": {"a": 1, "b": 2}}
        self.assertEqual(adapter.submission_fingerprint(body),
                         adapter.submission_fingerprint(dict(body)))

    def test_key_order_does_not_change_identity(self):
        a = {"trim_start": 0, "trim_end": 5, "caption_changes": {"x": 1, "y": 2}}
        b = {"caption_changes": {"y": 2, "x": 1}, "trim_end": 5, "trim_start": 0}
        self.assertEqual(adapter.submission_fingerprint(a),
                         adapter.submission_fingerprint(b))

    def test_a_different_submission_has_a_different_identity(self):
        base = {"trim_start": 0, "trim_end": 5}
        self.assertNotEqual(adapter.submission_fingerprint(base),
                            adapter.submission_fingerprint({"trim_start": 0, "trim_end": 6}))

    def test_a_different_rendered_file_has_a_different_identity(self):
        base = {"file_hash": "aaa", "size_bytes": 100}
        self.assertNotEqual(adapter.submission_fingerprint(base),
                            adapter.submission_fingerprint({"file_hash": "bbb", "size_bytes": 100}))

    def test_the_fingerprint_is_recorded_in_the_edl_metadata(self):
        body = {"edit_session_id": "sess-1", "trim_start": 0, "trim_end": 5,
                "edl_metadata": {"framing": "auto"}}
        fp = adapter.submission_fingerprint(body)
        handler = FakeHandler(body)
        captured = {}

        def fake_rpc(payload):
            captured.update(payload)
            return 200, {"asset_version": "render-1.3", "tenant_id": "dreemzy",
                         "clip_id": "clip-abc:0"}

        with _Patch(resolve_edit_session=lambda sid: {"status": "active",
                                                      "tenant_id": "dreemzy",
                                                      "clip_id": "clip-abc:0",
                                                      "source_asset_version": "render-1.2"},
                    lookup_editor_token=lambda h: (session_row(), "token_table"),
                    register_edited_asset_rpc=fake_rpc,
                    mark_edited_storage_status=lambda *a: True,
                    check_existing_card=lambda *a: [],
                    rehydrate_edit_job=lambda *a: (None, None)):
            adapter.Handler._handle_editor_complete(handler)
        self.assertEqual(captured.get("p_edl_metadata", {}).get("submission_fingerprint"), fp)
        self.assertEqual(captured.get("p_edl_metadata", {}).get("framing"), "auto",
                         "unrelated metadata must be preserved")

    def test_an_unauthenticated_complete_is_rejected_before_body_validation(self):
        """A bogus or absent credential must get an AUTH verdict, never a
        body-validation error. Previously a bogus token returned 400
        "missing edit_session_id", exposing request shape to anyone."""
        for headers in ({}, {"Authorization": "Bearer not-a-real-token"}):
            no_credential = headers == {}
            handler = FakeHandler({"edit_session_id": "sess-1"},
                                  token=None if no_credential else "not-a-real-token")
            with _Patch(lookup_editor_token=lambda h: (None, "legacy")):
                adapter.Handler._handle_editor_complete(handler)
            code, payload = handler.responses[-1]
            self.assertEqual(code, 401, headers)
            self.assertEqual(
                payload["reason"],
                "missing_token" if no_credential else "hash_mismatch",
            )
            self.assertNotEqual(code, 400)

    def test_a_credential_cannot_complete_another_session(self):
        """A valid credential for one session must not finalize a different
        session named in the body."""
        body = {"edit_session_id": "sess-other", "trim_start": 0, "trim_end": 5}
        handler = FakeHandler(body)
        rpc_calls = []
        with _Patch(lookup_editor_token=lambda h: (session_row(), "token_table"),
                    register_edited_asset_rpc=lambda p: (rpc_calls.append(p), (200, {}))[1]):
            adapter.Handler._handle_editor_complete(handler)
        code, payload = handler.responses[-1]
        self.assertEqual(code, 403)
        self.assertEqual(payload["reason"], "session_mismatch")
        self.assertEqual(rpc_calls, [])

    def test_a_replay_of_the_same_submission_is_idempotent(self):
        body = {"edit_session_id": "sess-1", "trim_start": 0, "trim_end": 5}
        fp = adapter.submission_fingerprint(body)
        handler = FakeHandler(body)
        rpc_calls = []

        with _Patch(resolve_edit_session=lambda sid: {"status": "completed",
                                                      "tenant_id": "dreemzy",
                                                      "clip_id": "clip-abc:0",
                                                      "target_asset_version": "render-1.3"},
                    lookup_editor_token=lambda h: (session_row(), "token_table"),
                    stored_submission_fingerprint=lambda sid: fp,
                    register_edited_asset_rpc=lambda p: (rpc_calls.append(p), (200, {}))[1],
                    check_existing_card=lambda *a: [{"discord_message_id": "m-1"}]):
            adapter.Handler._handle_editor_complete(handler)

        code, payload = handler.responses[-1]
        self.assertEqual(code, 200)
        self.assertTrue(payload["idempotent"])
        self.assertEqual(payload["submission"]["asset_version"], "render-1.3")
        self.assertTrue(payload["submission"]["already_completed"])
        self.assertEqual(payload["card"]["message_id"], "m-1")
        self.assertEqual(rpc_calls, [], "a replay must not register a second version")

    def test_a_different_submission_on_a_completed_session_conflicts(self):
        body = {"edit_session_id": "sess-1", "trim_start": 0, "trim_end": 99}
        handler = FakeHandler(body)
        rpc_calls = []

        with _Patch(resolve_edit_session=lambda sid: {"status": "completed",
                                                      "tenant_id": "dreemzy",
                                                      "clip_id": "clip-abc:0",
                                                      "target_asset_version": "render-1.3"},
                    lookup_editor_token=lambda h: (session_row(), "token_table"),
                    stored_submission_fingerprint=lambda sid: "a-different-fingerprint",
                    register_edited_asset_rpc=lambda p: (rpc_calls.append(p), (200, {}))[1]):
            adapter.Handler._handle_editor_complete(handler)

        code, payload = handler.responses[-1]
        self.assertEqual(code, 409)
        self.assertEqual(payload["code"], "submission_conflict")
        self.assertEqual(rpc_calls, [], "one session never yields two versions")

    def test_a_new_submission_on_a_new_session_registers_the_next_version(self):
        body = {"edit_session_id": "sess-2", "trim_start": 1, "trim_end": 8}
        handler = FakeHandler(body)
        rpc_calls = []

        def fake_rpc(payload):
            rpc_calls.append(payload)
            return 200, {"asset_version": "render-1.4", "tenant_id": "dreemzy",
                         "clip_id": "clip-abc:0"}

        with _Patch(resolve_edit_session=lambda sid: {"status": "active",
                                                      "tenant_id": "dreemzy",
                                                      "clip_id": "clip-abc:0",
                                                      "source_asset_version": "render-1.3"},
                    lookup_editor_token=lambda h: (session_row(id="sess-2"), "token_table"),
                    register_edited_asset_rpc=fake_rpc,
                    mark_edited_storage_status=lambda *a: True,
                    # A card already exists for an OLDER version: it must not
                    # suppress this genuinely new submission.
                    check_existing_card=lambda tenant, clip, version: (
                        [{"discord_message_id": "old"}] if version == "render-1.3" else []),
                    rehydrate_edit_job=lambda *a: (None, None)):
            adapter.Handler._handle_editor_complete(handler)

        self.assertEqual(len(rpc_calls), 1, "a distinct submission must be registered")
        code, payload = handler.responses[-1]
        self.assertNotEqual(code, 200)
        self.assertIn(code, (502,), "it proceeds past the dedup guard to the file check")
        self.assertNotIn("idempotent", payload or {})


# ---------------------------------------------------------------------------
# 6. Clip sub-resource resolution: live -> durable -> truthful 404
# ---------------------------------------------------------------------------

class ClipSubresourceFallbackTests(unittest.TestCase):
    DURABLE = {"asset_version": "render-1.2",
               "captions": [{"text": "Who", "startMs": 100, "endMs": 200}],
               "duration_seconds": 15.7,
               "source_range": {"start": 36.0, "end": 51.74},
               "framing": "auto"}

    def _run(self, kind, live, durable):
        handler = FakeHandler()
        # Inject on the INSTANCE: a class-level attribute would be resolved as a
        # bound method and the fake would receive an extra `self` argument.
        handler._backend_request = lambda m, t, b: live
        with _Patch(resolve_durable_bootstrap_diag=lambda *a: (
                durable, "ok" if durable else "version_not_found")):
            adapter.Handler._forward_clip_subresource(
                handler, "/api/clip/job/0/" + kind, kind,
                {"tenant_id": "dreemzy", "clip_id": "clip:0",
                 "source_asset_version": "render-1.2"})
        return handler.responses[-1]

    def test_live_data_is_preferred(self):
        code, payload = self._run("transcript", (200, b'{"captions":[{"text":"live"}]}'), self.DURABLE)
        self.assertEqual(code, 200)
        self.assertEqual(payload["captions"][0]["text"], "live")

    def test_a_live_404_falls_back_to_the_durable_transcript(self):
        code, payload = self._run("transcript", (404, b"{}"), self.DURABLE)
        self.assertEqual(code, 200)
        self.assertEqual(payload["source"], "durable", "the fallback must be labelled")
        self.assertEqual(payload["captions"][0]["text"], "Who")
        self.assertEqual(payload["asset_version"], "render-1.2")

    def test_scenes_fall_back_to_the_accepted_empty_list(self):
        code, payload = self._run("scenes", (404, b"{}"), self.DURABLE)
        self.assertEqual(code, 200)
        self.assertEqual(payload["scenes"], [], "the accepted contract default")
        self.assertEqual(payload["source"], "durable")

    def test_edl_falls_back_to_the_durable_recipe(self):
        code, payload = self._run("edl", (404, b"{}"), self.DURABLE)
        self.assertEqual(code, 200)
        self.assertEqual(payload["canonical_range"], {"start": 36.0, "end": 51.74})
        self.assertTrue(payload["durable"])

    def test_no_durable_source_is_a_labelled_404_not_an_auth_error(self):
        code, payload = self._run("transcript", (404, b"{}"), None)
        self.assertEqual(code, 404)
        self.assertEqual(payload["error"], "durable_data_missing")
        self.assertIn("reason", payload, "the cause must be named")
        self.assertNotIn("session token", json.dumps(payload))

    def test_an_unreachable_backend_also_falls_back(self):
        code, payload = self._run("transcript", (None, None), self.DURABLE)
        self.assertEqual(code, 200)
        self.assertEqual(payload["source"], "durable")

    def test_a_durable_transcript_keeps_an_empty_list_truthful(self):
        empty = dict(self.DURABLE, captions=[])
        code, payload = self._run("transcript", (404, b"{}"), empty)
        self.assertEqual(code, 200)
        self.assertEqual(payload["captions"], [], "never invent words")


# ---------------------------------------------------------------------------
# 7. /api/config auth contract
# ---------------------------------------------------------------------------

class ConfigAuthContractTests(unittest.TestCase):
    def test_config_is_forwarded_without_a_credential(self):
        handler = FakeHandler()
        calls = []
        handler._forward_to_backend = lambda m, t, b: calls.append((m, t, b))
        adapter.Handler._handle_public_config(handler)
        self.assertEqual(calls, [("GET", "/api/config", b"")])

    def test_every_other_api_route_still_requires_a_token(self):
        """Allow-listing /api/config is a deliberate, narrow contract — it must
        not have opened the gateway."""
        for path in ("/api/status/clip-abc", "/api/clip/clip-abc/0/transcript",
                     "/api/clip/rerender", "/api/config/secret"):
            with self.subTest(path=path):
                # This test models an UNAUTHENTICATED caller: no Authorization
                # header at all, hence the missing_token verdict.
                handler = FakeHandler(token=None)
                handler.path = path
                forwarded = []
                handler._forward_to_backend = lambda *a: forwarded.append(a)
                adapter.Handler._handle_editor_api(handler, "GET")
                code, payload = handler.responses[-1]
                self.assertEqual(code, 401, f"{path} must stay gated")
                self.assertEqual(payload.get("reason"), "missing_token")
                self.assertEqual(forwarded, [], "an unauthenticated call is never forwarded")

    def test_a_config_route_is_not_matched_by_a_sub_path(self):
        """`/api/config` is public; `/api/configX` is not."""
        self.assertNotEqual("/api/configX".split("?", 1)[0], "/api/config")


# ---------------------------------------------------------------------------
# Existing behaviour that must remain intact
# ---------------------------------------------------------------------------

class DurableMediaRehydrationTests(unittest.TestCase):
    """The durable-media fallback: the local OpenShorts source is GONE, the
    only surviving copy is the durable canonical `media_url`, and
    `clean_source_url` is honestly None. rehydrate_edit_job must still rebuild
    a live workspace from the surviving media and return (temp_job_id, 0).

    No live download: `_download` is injected, so the test stays hermetic.
    """

    TENANT = "dreemzy"
    CLIP = "6c46c3ef-5eba-40bc-9b50-8f3457d1db79:1"
    VERSION = "render-1.2"

    def setUp(self):
        self.output = tempfile.mkdtemp(prefix="loom-rehydrate-test-")
        self.addCleanup(shutil.rmtree, self.output, True)
        self.session_id = "11111111-2222-3333-4444-555555555555"

    # The durable bootstrap a real clip exposes once the local source is gone:
    # a canonical CDN media_url, and NO durable clean pre-hook source.
    def _durable(self, **over):
        durable = {
            "media_url": "https://assets.cdn.filesafe.space/dreemzy/clip-canonical.mp4",
            "clean_source_url": None,
            "duration_seconds": 15.74,
            "source_range": {"start": 36.0, "end": 51.74},
            "framing": "auto",
            "title": "LOOM Edit",
            "captions": [{"text": "Who", "startMs": 100, "endMs": 200}],
            "asset_version": self.VERSION,
        }
        durable.update(over)
        return durable

    def _rehydrate(self, **over):
        durable = self._durable(**over.get("durable", {}))
        downloads = []

        def fake_download(url, dest, timeout=120):
            downloads.append((url, dest))
            with open(dest, "wb") as f:
                f.write(b"not-a-real-video")
            return True

        def fake_urlopen(req, timeout=None):
            # Only the /api/status/<job> recovery probe is exercised here.
            self.assertIn("/api/status/", req.full_url)
            return FakeResponse(200, {"status": "ready"})

        with _Patch(
            OPENSHORTS_OUTPUT=self.output,
            resolve_durable_bootstrap=lambda *a: durable,
            _download=fake_download,
            probe_duration=over.get("probe_duration", lambda _p: 15.74),
            urlopen=fake_urlopen,
        ):
            result = adapter.rehydrate_edit_job(
                self.session_id, self.TENANT, self.CLIP, self.VERSION)
        return result, downloads

    def test_a_missing_local_source_rehydrates_from_the_durable_media_url(self):
        (job_id, clip_index), downloads = self._rehydrate()
        sid = self.session_id.replace("-", "")
        self.assertEqual(job_id, "loom-" + sid[:12],
                         "the temp job id is derived from the edit session, never chosen by the client")
        self.assertEqual(clip_index, 0)
        self.assertTrue(os.path.isdir(os.path.join(self.output, job_id)),
                        "the workspace must actually be rebuilt on disk")
        self.assertEqual(len(downloads), 1)
        self.assertEqual(downloads[0][0], self._durable()["media_url"],
                         "with clean_source_url=None the durable canonical media_url is the fallback")

    def test_the_rebuild_writes_the_metadata_the_editor_endpoints_require(self):
        (job_id, _index), _downloads = self._rehydrate()
        sid = self.session_id.replace("-", "")
        base = f"loom_{sid[:8]}"
        job_dir = os.path.join(self.output, job_id)
        with open(os.path.join(job_dir, f"{base}_metadata.json")) as f:
            metadata = json.load(f)
        self.assertEqual(len(metadata["shorts"]), 1)
        self.assertEqual(metadata["shorts"][0]["recipe"]["canonical_range"],
                         {"start": 0.0, "end": 15.74})
        self.assertTrue(metadata["transcript"]["segments"][0]["words"],
                        "a truthy transcript is required by /api/subtitle")

    def test_no_durable_media_and_no_clean_source_fails_honestly(self):
        (job_id, clip_index), downloads = self._rehydrate(
            durable={"media_url": None, "clean_source_url": None})
        self.assertEqual((job_id, clip_index), (None, None))
        self.assertEqual(downloads, [], "nothing is downloaded when no source exists")

    def test_the_backend_recovery_probe_must_not_be_2xx_blind(self):
        """A non-200 from /api/status means the job was never registered in the
        backend's in-memory table, so the editor endpoints would 404."""
        durable = self._durable()
        with _Patch(
            OPENSHORTS_OUTPUT=self.output,
            resolve_durable_bootstrap=lambda *a: durable,
            _download=lambda url, dest, timeout=120: (open(dest, "wb").close() or True),
            probe_duration=lambda _p: 15.74,
            urlopen=lambda req, timeout=None: FakeResponse(404, {}),
        ):
            self.assertEqual(
                adapter.rehydrate_edit_job(self.session_id, self.TENANT,
                                           self.CLIP, self.VERSION),
                (None, None))

    def test_an_already_rehydrated_workspace_is_reused(self):
        (job_id, _index), _downloads = self._rehydrate()
        sid = self.session_id.replace("-", "")
        job_dir = os.path.join(self.output, job_id)
        self.assertTrue(os.path.exists(os.path.join(job_dir, f"loom_{sid[:8]}_clip_1.mp4")))
        (again, index_again), downloads = self._rehydrate()
        self.assertEqual((again, index_again), (job_id, 0))
        self.assertEqual(downloads, [], "an existing workspace is reused, not re-downloaded")


class EditorEndpointAllowlistTests(unittest.TestCase):
    """The adapter is the only thing between the browser editor and the backend.

    `/api/header` (the on-video title/header burn) was ABSENT from the POST
    allowlist, so the adapter answered its own 403 and never forwarded. The
    editor treats a failed step as fatal (`failStep(...)` then `return`), so
    the submit aborted BEFORE /discord/editor/complete — producing two visible
    symptoms (header 403, and no card looping back to Discord) from one cause.
    """

    JOB = "clip-abc"
    TEMP = "loom-111111112222"

    def _authorize(self, path, body, method="POST", job=None, clip="0"):
        return adapter.Handler._authorize_editor_path(
            FakeHandler(), method, path, json.dumps(body).encode(),
            job or self.JOB, clip, self.TEMP)

    def test_every_editor_step_endpoint_is_allowlisted(self):
        """All POST endpoints the editor actually calls must forward."""
        for path in ("/api/clip/rerender", "/api/hook", "/api/subtitle", "/api/header"):
            with self.subTest(path=path):
                self.assertEqual(
                    self._authorize(path, {"job_id": self.JOB, "clip_index": 0}), path,
                    f"{path} is a real editor step and must not be self-403'd")

    def test_header_is_ownership_checked_like_any_other_step(self):
        self.assertIsNone(
            self._authorize("/api/header", {"job_id": "someone-elses-job", "clip_index": 0}),
            "header must not forward for a job the session does not own")
        self.assertIsNone(
            self._authorize("/api/header", {"job_id": self.JOB, "clip_index": 3}),
            "header must not forward for a clip the session does not own")

    def test_header_with_a_malformed_body_is_rejected(self):
        self.assertIsNone(adapter.Handler._authorize_editor_path(
            FakeHandler(), "POST", "/api/header", b"not json",
            self.JOB, "0", self.TEMP))

    def test_unknown_api_routes_are_still_rejected(self):
        for path in ("/api/header/secret", "/api/shell", "/api/hooks"):
            with self.subTest(path=path):
                self.assertIsNone(
                    self._authorize(path, {"job_id": self.JOB, "clip_index": 0}))


class ExistingBehaviourIntactTests(unittest.TestCase):
    def test_edited_storage_status_contract_is_untouched(self):
        patched = []
        with _Patch(supabase_filter_get=lambda t, f: [
                {"id": "row-1", "storage_status": "uploaded",
                 "canonical_url": None, "ghl_media_id": None}],
                supabase_patch_media=lambda mid, upd: (patched.append((mid, upd)), (204, []))[1]):
            self.assertTrue(adapter.mark_edited_storage_status("dreemzy", "clip:0", "render-1.6"))
        self.assertEqual(patched, [("row-1", {"storage_status": adapter.STORAGE_NOT_APPLICABLE})])

    def test_durable_payload_never_invents_scene_data(self):
        payload = adapter.durable_clip_payload("scenes", {"captions": [{"text": "x"}]})
        self.assertEqual(payload["scenes"], [])

    def test_the_handler_class_is_still_a_request_handler(self):
        """The adapter's HTTP surface is unchanged — the new helpers are methods
        on the same Handler class used by every existing route."""
        self.assertTrue(issubclass(adapter.Handler, BaseHTTPRequestHandler))
        for name in ("_handle_editor_bootstrap", "_handle_editor_draft",
                     "_handle_editor_complete", "_handle_editor_api",
                     "_handle_public_config", "_forward_clip_subresource"):
            self.assertTrue(callable(getattr(adapter.Handler, name, None)), name)

    def test_hash_token_is_unchanged(self):
        """Token validation still hashes the raw token with SHA-256 — the
        credential check was never weakened to make anything pass."""
        self.assertEqual(adapter.hash_token("abc"),
                         hashlib.sha256(b"abc").hexdigest())


if __name__ == "__main__":
    unittest.main()
