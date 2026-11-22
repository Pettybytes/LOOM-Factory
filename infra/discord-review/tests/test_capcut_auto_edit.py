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
            def invoke_with_nested_timeline(cmd, timeout):
                result = cr._default_invoke(cmd, timeout)
                if 'compile' in cmd and result[0] == 0:
                    # Model the installed 9.2 layout without reading/writing
                    # the live store. Keep a distinct native timeline GUID.
                    target = Path(cmd[cmd.index('--out') + 1])
                    nested = target / 'Timelines' / 'native-timeline'
                    nested.mkdir(parents=True)
                    draft = json.loads((target / 'draft_content.json').read_text())
                    draft['id'] = 'native-timeline'
                    for name in ('draft_info.json', 'template-2.tmp'):
                        (nested / name).write_text(json.dumps(draft))
                    (nested.parent / 'project.json').write_text(
                        '{"main_timeline_id":"native-timeline"}')
                return result
            with patch.dict(os.environ, {'LOOM_OPENSHORTS_OUTPUT': str(self.root)}):
                report = cr.run_handoff(bootstrap=self.bootstrap(), media_path=str(self.media),
                    asset_version='render-1.5', draft_store=str(store), handoff_root=td,
                    template=str(ROOT / 'vendor/capcut-cli/templates/_init'),
                    invoke=invoke_with_nested_timeline)
            spec = report['spec']
            self.assertEqual((spec['width'], spec['height'], spec['ratio'], spec['fps']), (144,256,'9:16',30))
            self.assertEqual(report['visual_state']['state_source'], 'verified_auto_edit_output')
            self.assertFalse(report['visual_state']['applied'])
            self.assertEqual(spec['visual_state']['auto_edit']['output_sha256'], self.digest)
            self.assertEqual(spec['visual_state']['auto_edit']['edit_plan'], self.bootstrap()['auto_edit']['edit_plan'])
            self.assertNotIn('keyframe', json.dumps(spec.get('operations', [])), 'never apply baked zoom twice')
            self.assertFalse(any(t['type'] == 'text' for t in spec['tracks']))
            ops = spec.get('operations') or []
            self.assertEqual(len(ops), 1)
            self.assertEqual(ops[0]['op'], 'template')
            self.assertEqual(ops[0]['ref'], 'hook')
            self.assertEqual(ops[0]['text'], 'Into the ice bath')
            self.assertEqual(ops[0]['duration'], 5)
            self.assertNotIn('keyframe', json.dumps(ops))
            draft = json.loads(Path(report['draft_file']).read_text())
            self.assertTrue(draft['materials']['videos'])
            self.assertTrue(draft['materials']['texts'], 'text must be independently editable, not a final render')
            for video in draft['materials']['videos']:
                self.assertEqual(cr.sha256_file(video['path']), self.digest)
            self.assertNotIn('tiktok.com', json.dumps(draft['materials']['texts']))
            # CapCut 9.2 loads the mirrors, so validating only the root content
            # file misses styling that never reaches the application.
            checked = subprocess.run(['node', str(ROOT / 'vendor/capcut-cli/dist/index.js'),
                'sync-timelines', report['draft_path'], '--nested', '--json'],
                capture_output=True, text=True, check=True)
            sync = json.loads(checked.stdout.strip().splitlines()[-1])
            self.assertTrue(sync['in_sync'], sync['drifted'])
            self.assertEqual(sync['nested_available'], 2)
            nested = Path(report['draft_path']) / 'Timelines' / 'native-timeline'
            native = json.loads((nested / 'draft_info.json').read_text())
            self.assertEqual(native['id'], 'native-timeline')
            self.assertEqual(native['materials']['texts'], draft['materials']['texts'])
            self.assertEqual(native['tracks'], draft['tracks'])
            self.assertEqual((nested.parent / 'project.json').read_text(),
                             '{"main_timeline_id":"native-timeline"}')

    def test_hook_style_mirror_matches_hooks_py_contract(self):
        """The mirrored HOOK_STYLE_CARD must keep matching the canonical dict.

        hooks.py imports PIL/ffmpeg at module load, so the mapper mirrors the
        contract as data. This parses hooks.py WITHOUT importing it and fails on
        any drift, so a style change can never silently desynchronise the
        Finish-in-CapCut path from the burned render.
        """
        import ast
        source = (ROOT / 'openshorts/hooks.py').read_text()
        tree = ast.parse(source)
        canonical = None
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(
                    isinstance(t, ast.Name) and t.id == 'HOOK_STYLES' for t in node.targets):
                canonical = ast.literal_eval(node.value)
        self.assertIsNotNone(canonical, 'HOOK_STYLES not found in hooks.py')
        self.assertEqual(ch.HOOK_STYLE_CARD, canonical)

    def test_bundled_skeleton_is_refused_and_leaves_no_project(self):
        """A `path` skeleton the app has outgrown must refuse, not ship.

        Measured failure (donor #67/#111): CapCut lists the draft at 00:00 and
        then refuses to open it. The refusal must also discard the project it
        just created, so no broken card is left behind.
        """
        with tempfile.TemporaryDirectory(dir=self.root) as td:
            store = Path(td) / 'drafts'; store.mkdir()
            target = store / 'Guard test'
            def fake(cmd, timeout):
                target.mkdir(parents=True, exist_ok=True)
                (target / 'draft_content.json').write_text('{}')
                return 0, json.dumps({
                    'ok': True,
                    'file_path': str(target / 'draft_content.json'),
                    'template': {'source': 'path', 'path': '/v/templates/_init',
                                 'app_version': '6.5.0',
                                 'warning': 'the store holds newer projects'},
                    'refs': {}}), ''
            with patch.dict(os.environ, {'LOOM_OPENSHORTS_OUTPUT': str(self.root)}):
                with self.assertRaisesRegex(ch.CapCutHandoffUnavailable, 'will not open'):
                    cr.run_handoff(bootstrap=self.bootstrap(), media_path=str(self.media),
                        asset_version='render-1.5', draft_name='Guard test',
                        draft_store=str(store), handoff_root=td, invoke=fake)
            self.assertFalse(target.exists(), 'a refused skeleton must leave no project')

    def test_skeleton_refusal_helper_cases(self):
        warned = {'ok': True, 'template': {'source': 'path', 'path': '/v/_init',
                  'app_version': '6.5.0', 'warning': 'store is newer'}}
        self.assertIn('store', cr.template_seed_refusal(warned))
        self.assertIsNone(cr.template_seed_refusal(warned, explicit_template_dir=True))
        self.assertIsNone(cr.template_seed_refusal({'template': {'source': 'store'}}))
        self.assertIsNone(cr.template_seed_refusal({}))

    def test_cli_template_and_style_write_native_card_font_and_wrap(self):
        import capcut_text as ct
        for settings in ({'style': 'classic', 'size': 'M', 'position': 'top'},
                         {'style': 'outline_yellow', 'size': 'L', 'position': 'bottom'}):
            with self.subTest(settings=settings), tempfile.TemporaryDirectory(dir=self.root) as td:
                store = Path(td) / 'drafts'; store.mkdir()
                bootstrap = self.bootstrap()
                bootstrap['auto_edit']['hook_settings'] = settings
                with patch.dict(os.environ, {'LOOM_OPENSHORTS_OUTPUT': str(self.root)}):
                    report = cr.run_handoff(bootstrap=bootstrap, media_path=str(self.media),
                        asset_version='render-1.5', draft_store=str(store), handoff_root=td,
                        template=str(ROOT / 'vendor/capcut-cli/templates/_init'))
                draft = json.loads(Path(report['draft_file']).read_text())
                material = draft['materials']['texts'][0]
                plan = ct.style_plan('Into the ice bath', settings, {'width':144,'height':256})
                for field in ('font_size','text_color','alignment','background_style','background_alpha',
                              'background_color','line_max_width','force_apply_line_max_width','font_path'):
                    self.assertEqual(material[field], plan['preset']['style'][field], field)
                self.assertEqual(material['check_flag'] & 16, 16, 'native template supplies card capability')
                content = json.loads(material['content'])
                self.assertEqual(content['text'], 'Into the ice bath')
                self.assertEqual(content['styles'][0]['size'], plan['font_size'])
                self.assertEqual(content['styles'][0]['font']['path'], plan['font_path'])
                seg = next(s for t in draft['tracks'] if t['type']=='text' for s in t['segments'])
                self.assertEqual(seg['clip']['transform'], plan['preset']['transform'])

    def test_each_text_role_keeps_its_saved_style_and_timing_through_compile(self):
        b = self.bootstrap()
        b['auto_edit']['hook_settings'] = {'style': 'yellow', 'position': 'center', 'duration_seconds': 3}
        b['auto_edit']['caption_settings'] = {'font_name': 'Arial', 'font_color': '#00FF00', 'font_size': 36}
        b['title_overlay'] = {'text': 'My header', 'font_family': 'Georgia', 'font_size': 50,
                              'text_color': '#123456', 'background': 'none'}
        with tempfile.TemporaryDirectory(dir=self.root) as td:
            store = Path(td) / 'drafts'; store.mkdir()
            with patch.dict(os.environ, {'LOOM_OPENSHORTS_OUTPUT': str(self.root)}):
                report = cr.run_handoff(bootstrap=b, media_path=str(self.media),
                    caption_blocks=[{'text': 'Hi', 'start': 1, 'end': 2}],
                    asset_version='render-1.5', draft_store=str(store), handoff_root=td,
                    template=str(ROOT / 'vendor/capcut-cli/templates/_init'))
            draft = json.loads(Path(report['draft_file']).read_text())
            by_text = {json.loads(m['content'])['text']: m for m in draft['materials']['texts']}
            self.assertEqual(by_text['Into the ice bath']['background_color'], '#FFD600')
            self.assertEqual(by_text['My header']['text_color'], '#123456')
            self.assertEqual(by_text['My header']['background_alpha'], 0)
            self.assertEqual(by_text['Hi']['text_color'], '#00FF00')
            caption = by_text['Hi']['id']
            segment = next(s for t in draft['tracks'] for s in t['segments'] if s['material_id'] == caption)
            self.assertEqual(segment['target_timerange'], {'start': 1000000, 'duration': 1000000})

    def test_ass_import_clones_caption_style_and_keeps_word_highlight_runs(self):
        bootstrap = self.bootstrap()
        bootstrap['auto_edit']['caption_settings'] = {'style': 'karaoke',
            'font_name': 'Arial', 'font_color': '#FFFFFF', 'font_size': 30,
            'highlight_color': '#FF0000', 'base_opacity': .4, 'alignment': 'middle',
            'bg_color': '#123456', 'bg_opacity': .6, 'uppercase': True}
        with tempfile.TemporaryDirectory(dir=self.root) as td:
            store = Path(td)/'drafts'; store.mkdir()
            with patch.dict(os.environ, {'LOOM_OPENSHORTS_OUTPUT': str(self.root)}):
                report = cr.run_handoff(bootstrap=bootstrap, media_path=str(self.media),
                    caption_blocks=[{'text':'Hello','start':1,'end':1.5}, {'text':'there','start':1.5,'end':2}],
                    asset_version='render-1.5', draft_store=str(store), handoff_root=td,
                    template=str(ROOT/'vendor/capcut-cli/templates/_init'))
            draft = json.loads(Path(report['draft_file']).read_text())
            track = next(t for t in draft['tracks'] if t.get('name')=='captions')
            self.assertEqual([s['target_timerange'] for s in track['segments']],
                             [{'start':1000000,'duration':500000}, {'start':1500000,'duration':500000}])
            materials = {m['id']:m for m in draft['materials']['texts']}
            for segment in track['segments']:
                material = materials[segment['material_id']]
                self.assertAlmostEqual(segment['clip']['transform']['y'], 0)
                self.assertEqual(material['background_color'], '#123456')
                self.assertEqual(material['background_alpha'], .6)
                self.assertIn('Arial', material['font_path'])
                content = json.loads(material['content'])
                self.assertEqual(content['text'], 'HELLO THERE')
                colors = [run['fill']['content']['solid']['color'] for run in content['styles']]
                self.assertIn([1,0,0], colors)
                self.assertTrue(any(abs(c[0]-178/255)<.01 and c[0]==c[1]==c[2] for c in colors), colors)
            self.assertFalse(any('Caption style seed' in m['content'] for m in materials.values()))

    def test_failed_nested_sync_never_reports_success(self):
        with tempfile.TemporaryDirectory(dir=self.root) as td:
            store = Path(td)/'drafts'; store.mkdir()
            def fail_sync(cmd, timeout):
                if 'sync-timelines' in cmd:
                    return 0, json.dumps({'ok':True, 'in_sync':False}), ''
                return cr._default_invoke(cmd, timeout)
            with patch.dict(os.environ, {'LOOM_OPENSHORTS_OUTPUT': str(self.root)}):
                with self.assertRaisesRegex(ch.CapCutHandoffUnavailable, 'not synchronized'):
                    cr.run_handoff(bootstrap=self.bootstrap(), media_path=str(self.media),
                        asset_version='render-1.5', draft_store=str(store), handoff_root=td,
                        template=str(ROOT/'vendor/capcut-cli/templates/_init'), invoke=fail_sync)

    def test_no_text_plan_runs_no_text_commands(self):
        calls = []
        result = cr.apply_text_commands([], target='/unused', refs={}, invocation=['node'],
            runner=lambda *args: calls.append(args), timeout=10)
        self.assertEqual(result, {'elements': []})
        self.assertEqual(calls, [])

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
