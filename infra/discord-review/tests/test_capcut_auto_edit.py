"""Accepted generated AUTO artifact -> existing CapCut compiler, no model calls."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'infra/discord-review'))
import capcut_runner as cr
import capcut_handoff as ch


class AutoEditHandoffTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='loom-capcut-auto-')
        cls.root = Path(cls.tmp.name)
        (cls.root / 'job').mkdir()
        cls.media = cls.root / 'job' / 'generated.mp4'
        subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
            'testsrc=duration=6:size=144x256:rate=30', '-pix_fmt', 'yuv420p',
            str(cls.media)], check=True)
        cls.digest = cr.sha256_file(str(cls.media))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def bootstrap(self):
        return {'source_range': {'start': 0, 'end': 6}, 'framing': 'auto',
            'source_title': 'https://www.tiktok.com/@creator/video/123',
            'renderable_text': {'title': 'Cold plunge', 'hook': 'Into the ice bath'},
            'auto_edit': {'status': 'completed', 'output_url': '/videos/job/generated.mp4',
                'edit_plan': {'edits': [{'type': 'zoom_in', 'start': 0.5, 'end': 6, 'strength': 0.08}]},
                'hook_settings': {'duration_seconds': 5, 'position': 'top'}}}

    def test_native_canvas_and_already_applied_effects_survive_real_compile(self):
        with tempfile.TemporaryDirectory(dir=self.root) as td:
            store = Path(td) / 'drafts'; store.mkdir()
            with patch.dict(os.environ, {'LOOM_OPENSHORTS_OUTPUT': str(self.root)}):
                report = cr.run_handoff(bootstrap=self.bootstrap(), media_path=str(self.media),
                    asset_version='render-1.5', draft_store=str(store), handoff_root=td,
                    template=str(ROOT / 'vendor/capcut-cli/templates/_init'))
            spec = report['spec']
            self.assertEqual((spec['width'], spec['height'], spec['ratio'], spec['fps']), (144,256,'9:16',30))
            self.assertEqual(report['visual_state']['state_source'], 'verified_auto_edit_output')
            self.assertFalse(report['visual_state']['applied'])
            self.assertEqual(spec['visual_state']['auto_edit']['output_sha256'], self.digest)
            self.assertEqual(spec['visual_state']['auto_edit']['edit_plan'], self.bootstrap()['auto_edit']['edit_plan'])
            self.assertNotIn('keyframe', json.dumps(spec.get('operations', [])), 'never apply baked zoom twice')
            self.assertEqual(spec['tracks'][1]['items'], [{'text':'Into the ice bath', 'start':0, 'duration':5.0, 'y':0.65, 'fontSize':14}])
            draft = json.loads(Path(report['draft_file']).read_text())
            self.assertTrue(draft['materials']['videos'])
            self.assertTrue(draft['materials']['texts'], 'text must be independently editable, not a final render')
            for video in draft['materials']['videos']:
                self.assertEqual(cr.sha256_file(video['path']), self.digest)
            self.assertNotIn('tiktok.com', json.dumps(draft['materials']['texts']))

    def test_stale_or_raw_asset_cannot_fall_back_to_legacy(self):
        with patch.dict(os.environ, {'LOOM_OPENSHORTS_OUTPUT': str(self.root)}):
            with self.assertRaisesRegex(ch.CapCutHandoffUnavailable, 'cannot be verified'):
                cr.verified_auto_edit_state(self.bootstrap(), media_path=str(self.media), digest='0'*64, asset_version='render-1.5')

    def test_missing_generated_output_refuses_instead_of_rerendering(self):
        b = self.bootstrap(); b['auto_edit']['output_url'] = '/videos/missing/output.mp4'
        with self.assertRaisesRegex(ch.CapCutHandoffUnavailable, 'cannot be verified'):
            cr.verified_auto_edit_state(b, media_path=str(self.media), digest=self.digest, asset_version='render-1.5')

    def test_macos_denial_is_actionable_and_does_not_stage_media(self):
        with patch.object(cr.os, 'listdir', side_effect=PermissionError('Operation not permitted')):
            with patch.object(cr, 'stage_working_media') as stage:
                with self.assertRaisesRegex(ch.CapCutHandoffUnavailable, 'macOS denied access'):
                    cr.run_handoff(bootstrap=self.bootstrap(), media_path=str(self.media), draft_store=str(self.root))
                stage.assert_not_called()


if __name__ == '__main__':
    unittest.main()
