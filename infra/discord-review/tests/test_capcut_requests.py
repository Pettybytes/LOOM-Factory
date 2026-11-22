"""Handoff requests remain single-flight and replay-safe across processes."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import capcut_requests


class RequestTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.draft = self.root / 'draft_content.json'
        self.draft.write_text('{}')
        self.identity = ('tenant', 'clip:0', 'render-1.1')
        self.result = (200, {'ok': True, 'draft_file': str(self.draft),
                             'draft_path': str(self.root), 'provenance': {'handoff_id': 'one'}})

    def run_request(self, build, identity=None):
        return capcut_requests.run_once(self.root, identity or self.identity, build)

    def test_repeat_after_process_restart_returns_the_same_project(self):
        self.assertEqual(self.run_request(lambda: self.result), self.result)
        code = '''import json,sys; sys.path.insert(0,sys.argv[1]); import capcut_requests
def forbidden():
    raise AssertionError('replay created another project')
print(json.dumps(capcut_requests.run_once(sys.argv[2], ('tenant','clip:0','render-1.1'), forbidden)))'''
        replay = subprocess.run([sys.executable, '-c', code, str(Path(capcut_requests.__file__).parent),
                                 str(self.root)], capture_output=True, text=True, check=True)
        status, body = json.loads(replay.stdout)
        self.assertEqual(status, 200)
        self.assertEqual(body['draft_file'], str(self.draft))
        self.assertEqual(body['provenance']['handoff_id'], 'one')
        self.assertTrue(body['replayed'])

    def test_concurrent_click_and_other_clip_cannot_write_shared_store(self):
        entered, release = threading.Event(), threading.Event()
        def build():
            entered.set()
            self.assertTrue(release.wait(5))
            return self.result
        thread = threading.Thread(target=lambda: self.run_request(build))
        thread.start()
        try:
            self.assertTrue(entered.wait(5))
            for identity in (self.identity, ('tenant', 'other:0', 'render-1.1')):
                status, body = self.run_request(lambda: self.fail('concurrent export'), identity)
                self.assertEqual(status, 409)
                self.assertEqual(body['error'], 'handoff_busy')
        finally:
            release.set()
            thread.join(5)

    def test_interrupted_request_refuses_to_create_a_duplicate(self):
        def crash():
            raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            self.run_request(crash)
        status, body = self.run_request(lambda: self.fail('duplicate after interruption'))
        self.assertEqual(status, 409)
        self.assertEqual(body['error'], 'handoff_incomplete')

    def test_known_refusal_can_be_retried_after_closing_capcut(self):
        self.assertEqual(self.run_request(lambda: (409, {'ok': False}))[0], 409)
        self.assertEqual(self.run_request(lambda: self.result), self.result)

    def test_missing_project_is_not_silently_recreated(self):
        self.run_request(lambda: self.result)
        self.draft.unlink()
        status, body = self.run_request(lambda: self.fail('recreated missing project'))
        self.assertEqual(status, 409)
        self.assertEqual(body['error'], 'handoff_project_missing')

    def test_other_asset_has_its_own_result(self):
        self.run_request(lambda: self.result)
        other = (200, {**self.result[1], 'asset_version': 'render-1.2'})
        self.assertEqual(self.run_request(lambda: other, ('tenant', 'clip:0', 'render-1.2')), other)


if __name__ == '__main__':
    unittest.main()
