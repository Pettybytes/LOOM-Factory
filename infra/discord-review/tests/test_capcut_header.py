"""Saved Header -> version bootstrap -> authenticated CapCut handoff."""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT/'infra/discord-review'))
for key in ('LOOM_DISCORD_APP_ID','LOOM_DISCORD_PUBLIC_KEY','LOOM_DISCORD_BOT_TOKEN','LOOM_SUPABASE_SERVICE_ROLE_KEY'):
    os.environ.setdefault(key, 'test-inert')
os.environ.setdefault('LOOM_SUPABASE_URL', 'https://unit-test.invalid')
import adapter
import source_review

HEADER = {'text':'Saved per-clip Header', 'font_family':'Verdana', 'font_size':64,
          'text_color':'#FFFFFF', 'background':'none', 'background_color':'#000000',
          'position':'top', 'alignment':'center', 'region':'dedicated'}


class HeaderForwardingTests(unittest.TestCase):
    def source_bootstrap(self, overlay):
        correction = {'trim_start':0, 'trim_end':26, 'edl_metadata':{'title_overlay':overlay}}
        return source_review.edited_version_bootstrap({'title':'Metadata only'},
            {'clean_source_url':'https://unit-test.invalid/video.mp4','duration_seconds':26},
            correction, 'render-1.5')[0]

    def test_registered_source_version_forwards_its_own_saved_header(self):
        first = self.source_bootstrap(HEADER)
        second = self.source_bootstrap({**HEADER, 'region':'overlay', 'text':'Second clip', 'font_size':40})
        self.assertEqual(first.get('title_overlay'), HEADER)
        self.assertEqual(second['title_overlay']['text'], 'Second clip')
        self.assertEqual(second['title_overlay']['font_size'], 40)
        self.assertEqual(first['renderable_text']['title'], '')

    def test_absent_or_explicitly_disabled_header_is_not_invented(self):
        self.assertIsNone(self.source_bootstrap(None).get('title_overlay'))
        disabled = {**HEADER, 'enabled':False}
        self.assertEqual(self.source_bootstrap(disabled).get('title_overlay'), disabled)

    def test_rendered_clip_bootstrap_forwards_current_correction_header(self):
        media = [{'asset_version':'render-1.5', 'canonical_url':'https://unit-test.invalid/v.mp4', 'duration_seconds':26}]
        correction = {'trim_start':0,'trim_end':26,'edl_metadata':{'title_overlay':HEADER}}
        def fetch(table, filters):
            return media if table=='loom_media' else [correction]
        with patch.object(adapter, 'supabase_filter_get', side_effect=fetch), \
             patch.object(adapter, 'supabase_get', return_value=[]):
            result = adapter.resolve_durable_bootstrap('tenant', 'job:0', 'render-1.5')
        self.assertEqual(result.get('title_overlay'), HEADER)
        self.assertEqual(result['renderable_text']['title'], '')

    def test_finish_endpoint_forwards_the_authenticated_sessions_saved_header(self):
        for overlay in (HEADER, {**HEADER,'enabled':False}, None):
            with self.subTest(overlay=overlay), tempfile.TemporaryDirectory() as request_root:
                responses=[]
                handler = SimpleNamespace(headers={'Authorization':'Bearer test'},
                    _read_body=lambda: b'{"edit_session_id":"session"}',
                    _send_json=lambda status, body: responses.append((status,body)))
                session={'edit_session_id':'session','tenant_id':'tenant','clip_id':'job:0',
                         'source_asset_version':'render-1.5','draft':{'title_overlay':overlay}}
                durable={'asset_version':'render-1.5', 'title_overlay':{**HEADER,'text':'Older saved Header'}}
                original=copy.deepcopy(durable)
                with patch.object(adapter.capcut_runner, 'DEFAULT_HANDOFF_ROOT', request_root), \
                     patch.object(adapter, 'resolve_editor_session_token_diag', return_value=(session,'ok')), \
                     patch.object(adapter, 'resolve_durable_bootstrap_diag', return_value=(durable,'ok')), \
                     patch.object(adapter, 'materialize_local_working_media', return_value=('/tmp/unused.mp4','local')), \
                     patch.object(adapter.capcut_runner, 'run_handoff', side_effect=adapter.capcut_runner.ch.CapCutHandoffUnavailable('test boundary')) as handoff:
                    adapter.Handler._handle_editor_capcut(handler)
                self.assertEqual(handoff.call_args.kwargs['bootstrap']['title_overlay'], overlay)
                self.assertEqual(durable, original)
                self.assertEqual(responses[0][0],409)


class HeaderBandCompileTests(unittest.TestCase):
    def test_no_saved_header_means_no_text_band_or_shifted_hook(self):
        import tempfile
        import capcut_handoff as ch
        from capcut_geometry import header_video_band
        from capcut_text import prepare_text_commands
        for overlay in (None, {}, {'region':'dedicated'}, {**HEADER,'text':'  '},
                        {**HEADER,'enabled':False}):
            with self.subTest(overlay=overlay), tempfile.TemporaryDirectory() as td:
                b={'source_range':{'start':0,'end':26},'title_overlay':overlay,
                   'renderable_text':{'hook':'Each clip has its own text'},
                   'auto_edit':{'status':'completed','hook_settings':{'position':'top','size':'M'}}}
                media=Path(td)/'clip.mp4'; media.write_bytes(b'test media')
                spec=ch.build_capcut_spec(b,media_path=str(media),canvas={'width':576,'height':1024})
                plans=prepare_text_commands(spec,b,[],td)
                self.assertEqual([p['role'] for p in plans],['hook'])
                self.assertAlmostEqual(plans[0]['geometry']['centerY']/1024,.2)
                self.assertIsNone(header_video_band(overlay,{'width':576,'height':1024}))
                self.assertNotIn('HEADER TEST',json.dumps(spec))

    def test_header_and_video_boxes_match_rendered_editor_at_both_viewports(self):
        import hashlib
        from capcut_geometry import activity_box, header_video_band
        from capcut_text import resolve_font
        fixture=json.loads(Path(__file__).with_name('fixtures').joinpath('capcut_header_geometry.json').read_text())
        for path,digest in fixture['sources'].items():
            self.assertEqual(hashlib.sha256((ROOT/path).read_bytes()).hexdigest(),digest)
        for case in fixture['cases']:
            canvas={k:case['frame'][k] for k in ('width','height')}
            settings={**HEADER,'region':case['region']}
            with self.subTest(viewport=case['viewport'],region=case['region']):
                box=activity_box('HEADER TEST — dedicated band',settings,canvas,
                                 resolve_font('Verdana',700),'title',False)
                for key in ('x','y','width','height','centerX','centerY'):
                    self.assertAlmostEqual(box[key],case['header'][key],delta=2)
                band=header_video_band(settings,canvas)
                if case['region']=='dedicated':
                    for key in ('x','y','width','height'):
                        self.assertAlmostEqual(band[key],case['video'][key],delta=2)
                else:
                    self.assertIsNone(band)
                self.assertEqual(case['objectFit'],'cover')

    def test_native_header_and_dedicated_band_match_activity_geometry(self):
        import tempfile
        import subprocess
        import capcut_runner as cr
        with tempfile.TemporaryDirectory(prefix='loom-header-') as td:
            root=Path(td); (root/'job').mkdir()
            media=root/'job/base.mp4'
            subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i',
                'testsrc=duration=2:size=576x1024:rate=30','-pix_fmt','yuv420p',str(media)],check=True)
            digest=cr.sha256_file(str(media))
            for region in ('overlay','dedicated'):
                with self.subTest(region=region):
                    store=root/region; store.mkdir()
                    b={'source_range':{'start':0,'end':2}, 'framing':'auto',
                       'renderable_text':{'title':'Metadata title','hook':'Independent hook'},
                       'auto_edit':{'status':'completed','output_url':'/videos/job/base.mp4',
                                    'hook_settings':{'position':'top','size':'M','duration_seconds':1}},
                       'title_overlay':{**HEADER,'region':region}}
                    with patch.dict(os.environ,{'LOOM_OPENSHORTS_OUTPUT':str(root)}):
                        report=cr.run_handoff(bootstrap=b,media_path=str(media),asset_version='render-1.5',
                            draft_store=str(store),handoff_root=str(root/'handoffs'),
                            template=str(ROOT/'vendor/capcut-cli/templates/_init'))
                    draft=json.loads(Path(report['draft_file']).read_text())
                    video=draft['materials']['videos'][0]
                    self.assertEqual(cr.sha256_file(video['path']),digest)
                    segment=next(s for t in draft['tracks'] if t['type']=='video' for s in t['segments'])
                    self.assertEqual(segment['target_timerange'],{'start':0,'duration':2000000})
                    self.assertAlmostEqual(segment['clip']['transform']['y'], -.18 if region=='dedicated' else 0)
                    self.assertAlmostEqual(video['crop']['upper_left_y'], .09 if region=='dedicated' else 0)
                    self.assertAlmostEqual(video['crop']['lower_right_y'], .91 if region=='dedicated' else 1)
                    self.assertEqual(segment['clip']['scale'],{'x':1,'y':1})
                    by_text={json.loads(m['content'])['text']:m for m in draft['materials']['texts']}
                    self.assertNotIn('Metadata title',by_text)
                    title=by_text[HEADER['text']]
                    self.assertIn('Verdana',title['font_path'])
                    self.assertEqual(title['background_alpha'],0)
                    titleseg=next(s for t in draft['tracks'] for s in t['segments'] if s['material_id']==title['id'])
                    self.assertAlmostEqual((1-titleseg['clip']['transform']['y'])*1024/2,71.68)
                    self.assertEqual(titleseg['target_timerange'],{'start':0,'duration':2000000})
                    hook=by_text['Independent hook']
                    hookseg=next(s for t in draft['tracks'] for s in t['segments'] if s['material_id']==hook['id'])
                    self.assertAlmostEqual((1-hookseg['clip']['transform']['y'])/2, .344 if region=='dedicated' else .2)
