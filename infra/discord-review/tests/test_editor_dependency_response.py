"""Wire-level regression for the editor's missing-workspace API response.

Cloudflare replaces origin 502 responses with branded HTML. A workspace that
cannot be materialized is an application dependency failure, not a bad gateway.
Keep that failure JSON without changing authentication or backend forwarding.
"""

import json
import sys
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import adapter


class EditorDependencyResponseTests(unittest.TestCase):
    SESSION = {
        "edit_session_id": "session-test",
        "tenant_id": "tenant-test",
        "clip_id": "source-job:3",
        "source_asset_version": "source-1.0",
        "job_id": "source-job",
        "clip_index": "3",
    }

    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), adapter.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(self, *, authenticated=True):
        headers = {"Content-Type": "application/json"}
        if authenticated:
            headers["Authorization"] = "Bearer test-editor-token"
        body = {"job_id": "source-job", "clip_index": "3",
                "segments": [{"start": 0, "end": 20.11}]}
        req = Request(
            f"http://127.0.0.1:{self.server.server_port}/api/clip/rerender",
            data=json.dumps(body).encode(), headers=headers, method="POST")
        try:
            response = urlopen(req, timeout=5)
        except HTTPError as exc:
            response = exc
        with response:
            return (response.status, response.headers.get_content_type(),
                    json.loads(response.read()))

    def test_missing_workspace_is_json_failed_dependency_without_rendering(self):
        with mock.patch.object(adapter, "resolve_editor_session_token_diag",
                               return_value=(self.SESSION, "ok")), \
             mock.patch.object(adapter, "rehydrate_edit_job", return_value=(None, None)), \
             mock.patch.object(adapter.Handler, "_backend_request") as backend:
            status, content_type, body = self.request()
        self.assertEqual(status, 424)
        self.assertEqual(content_type, "application/json")
        self.assertEqual(body["code"], "live_job_unavailable")
        self.assertEqual(body["error"], "could not rehydrate edit job")
        backend.assert_not_called()

    def test_available_workspace_still_forwards_rerender_with_server_identity(self):
        result = {"new_video_url": "/output/temp-job/edited.mp4"}
        with mock.patch.object(adapter, "resolve_editor_session_token_diag",
                               return_value=(self.SESSION, "ok")), \
             mock.patch.object(adapter, "rehydrate_edit_job", return_value=("temp-job", 0)), \
             mock.patch.object(adapter.Handler, "_backend_request",
                               return_value=(200, json.dumps(result).encode())) as backend:
            status, content_type, body = self.request()
        self.assertEqual((status, content_type, body), (200, "application/json", result))
        method, path, raw = backend.call_args.args
        self.assertEqual((method, path), ("POST", "/api/clip/rerender"))
        rewritten = json.loads(raw)
        self.assertEqual((rewritten["job_id"], rewritten["clip_index"]), ("temp-job", 0))
        self.assertEqual(rewritten["segments"], [{"start": 0, "end": 20.11}])

    def test_unauthenticated_request_still_stops_before_workspace_or_backend(self):
        with mock.patch.object(adapter, "rehydrate_edit_job") as rehydrate, \
             mock.patch.object(adapter.Handler, "_backend_request") as backend:
            status, content_type, body = self.request(authenticated=False)
        self.assertEqual((status, content_type), (401, "application/json"))
        self.assertEqual(body["reason"], "missing_token")
        rehydrate.assert_not_called()
        backend.assert_not_called()

    def test_actual_backend_connection_failure_remains_bad_gateway(self):
        with mock.patch.object(adapter, "resolve_editor_session_token_diag",
                               return_value=(self.SESSION, "ok")), \
             mock.patch.object(adapter, "rehydrate_edit_job", return_value=("temp-job", 0)), \
             mock.patch.object(adapter.Handler, "_backend_request", return_value=(None, None)):
            status, content_type, body = self.request()
        self.assertEqual((status, content_type), (502, "application/json"))
        self.assertEqual(body["error"], "backend unreachable")


if __name__ == "__main__":
    unittest.main()
