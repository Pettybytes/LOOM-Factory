"""Finish after Auto Edit: authorization and exact completed-version export."""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
for key in ('LOOM_DISCORD_APP_ID', 'LOOM_DISCORD_PUBLIC_KEY',
            'LOOM_DISCORD_BOT_TOKEN', 'LOOM_SUPABASE_SERVICE_ROLE_KEY'):
    os.environ.setdefault(key, 'test-only')
os.environ.setdefault('LOOM_SUPABASE_URL', 'https://unit-test.invalid')
import adapter


class CompletedFinishTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.row = {'id': 'session', 'tenant_id': 'tenant', 'clip_id': 'job:0',
                    'status': 'completed', 'source_asset_version': 'render-1.0',
                    'target_asset_version': 'render-1.1',
                    'expires_at': '2099-01-01T00:00:00Z',
                    'created_at': adapter.datetime.now(adapter.timezone.utc).isoformat(),
                    'draft': {'title_overlay': {'text': 'Stale draft'}}}
        self.durable = {'asset_version': 'render-1.1',
                        'title_overlay': {'text': 'Registered Header'},
                        'auto_edit': {'hook_settings': {'size': 'L'}}}

    def finish(self, body=None, token='test', report=None):
        responses = []
        handler = SimpleNamespace(headers={'Authorization': 'Bearer ' + token},
            _read_body=lambda: json.dumps(body or {'edit_session_id': 'session'}).encode(),
            _send_json=lambda status, data: responses.append((status, data)))
        with patch.object(adapter, 'lookup_editor_token', return_value=(self.row, 'legacy')), \
             patch.object(adapter, 'resolve_durable_bootstrap_diag', return_value=(self.durable, 'ok')) as resolve, \
             patch.object(adapter, 'materialize_local_working_media', return_value=('/tmp/unused', 'local')), \
             patch.object(adapter.capcut_runner, 'DEFAULT_HANDOFF_ROOT', self.tmp.name), \
             patch.object(adapter, 'supabase_filter_get', return_value=[]), \
             patch.object(adapter, 'record_capcut_handoff', return_value={'handoff_id': 'one'}), \
             patch.object(adapter.capcut_runner, 'run_handoff', return_value=report,
                          side_effect=None if report else adapter.capcut_runner.ch.CapCutHandoffUnavailable('Close CapCut, then retry.')) as runner:
            adapter.Handler._handle_editor_capcut(handler)
        return responses[0], resolve, runner

    def test_completed_finish_exports_exact_target_and_registered_settings(self):
        before = copy.deepcopy(self.durable)
        response, resolve, runner = self.finish()
        self.assertEqual(response[0], 409)  # reached the runner's app-open gate
        resolve.assert_called_once_with('tenant', 'job:0', 'render-1.1')
        self.assertEqual(runner.call_args.kwargs['bootstrap'], self.durable)
        self.assertEqual(runner.call_args.kwargs['asset_version'], 'render-1.1')
        self.assertEqual(self.durable, before)

    def test_completed_exception_does_not_reopen_other_endpoints(self):
        with patch.object(adapter, 'lookup_editor_token', return_value=(self.row, 'legacy')):
            self.assertEqual(adapter.resolve_editor_session_token_diag('test'), (None, 'completed'))

    def test_browser_cannot_select_another_clip_or_asset(self):
        _, resolve, runner = self.finish({'edit_session_id': 'session',
                                         'clip_id': 'another:0', 'asset_version': 'render-9.9'})
        resolve.assert_called_once_with('tenant', 'job:0', 'render-1.1')
        self.assertEqual(runner.call_args.kwargs['asset_version'], 'render-1.1')

    def test_missing_target_record_never_falls_back_to_source(self):
        self.durable = None
        response, resolve, runner = self.finish()
        self.assertEqual(response[0], 404)
        resolve.assert_called_once_with('tenant', 'job:0', 'render-1.1')
        runner.assert_not_called()

    def test_terminal_expired_unknown_and_mismatched_sessions_never_export(self):
        cases = [('revoked', '2099-01-01T00:00:00Z', 'session', 401),
                 ('expired', '2099-01-01T00:00:00Z', 'session', 401),
                 ('completed', '2000-01-01T00:00:00Z', 'session', 401),
                 ('completed', '2099-01-01T00:00:00Z', 'other', 403)]
        for status, expiry, sid, expected in cases:
            with self.subTest(status=status, expiry=expiry, sid=sid):
                self.row.update(status=status, expires_at=expiry)
                response, resolve, runner = self.finish({'edit_session_id': sid})
                self.assertEqual(response[0], expected)
                resolve.assert_not_called()
                runner.assert_not_called()
        self.row = None
        response, _, runner = self.finish()
        self.assertEqual(response[0], 401)
        runner.assert_not_called()

    def test_completed_without_target_refuses_instead_of_exporting_source(self):
        self.row['target_asset_version'] = None
        response, resolve, runner = self.finish()
        self.assertEqual(response[0], 409)
        self.assertEqual(response[1]['error'], 'missing_target_version')
        resolve.assert_not_called()
        runner.assert_not_called()

    def test_target_mismatch_and_unbounded_target_refuse(self):
        self.durable['asset_version'] = 'render-1.0'
        response, _, runner = self.finish()
        self.assertEqual(response[1]['error'], 'target_version_mismatch')
        runner.assert_not_called()
        for target in ('latest', '*', ''):
            self.row['target_asset_version'] = target
            response, resolve, runner = self.finish()
            self.assertEqual(response[1]['error'], 'missing_target_version')
            resolve.assert_not_called()
            runner.assert_not_called()

    def test_no_expiry_or_missing_token_does_not_gain_completed_exception(self):
        for expiry in (None, 'invalid', '2099-01-01T00:00:00'):
            self.row['expires_at'] = expiry
            response, _, runner = self.finish()
            self.assertEqual(response[0], 401)
            runner.assert_not_called()
        response, _, runner = self.finish(token='')
        self.assertEqual(response[0], 401)
        runner.assert_not_called()

    def test_endpoint_replay_rechecks_authorization_before_returning_saved_project(self):
        draft = Path(self.tmp.name) / 'draft_content.json'
        draft.write_text('{}')
        report = {'ok': True, 'draft_file': str(draft), 'asset_version': 'render-1.1'}
        response, _, runner = self.finish(report=report)
        self.assertEqual(response[0], 200)
        runner.assert_called_once()
        response, _, runner = self.finish(report=report)
        self.assertTrue(response[1]['replayed'])
        runner.assert_not_called()
        for status, expiry in [('revoked', '2099-01-01T00:00:00Z'),
                               ('completed', '2000-01-01T00:00:00Z')]:
            self.row.update(status=status, expires_at=expiry)
            response, _, runner = self.finish(report=report)
            self.assertEqual(response[0], 401)
            runner.assert_not_called()


if __name__ == '__main__':
    unittest.main()
