"""Saved editor styles -> editable native text, independently for each role."""
import json
from pathlib import Path
import sys
import unittest
import hashlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import capcut_text as ct
from capcut_geometry import activity_box


class TextMappingTests(unittest.TestCase):
    def test_preset_geometry_matches_rendered_activity_component(self):
        fixture = json.loads(Path(__file__).with_name('fixtures').joinpath('capcut_activity_geometry.json').read_text())
        root = Path(__file__).resolve().parents[3]
        for source, digest in fixture['sources'].items():
            self.assertEqual(hashlib.sha256((root/source).read_bytes()).hexdigest(), digest,
                             'Activity layout changed: remeasure the browser geometry fixture')
        for case in fixture['hook_cases']:
            with self.subTest(case={k: case[k] for k in ('size','position','region')}):
                box = activity_box(fixture['text'], case, fixture['canvas'], ct.resolve_font('Noto Serif'), 'hook')
                for key in ('x','y','width','height','centerX','centerY'):
                    self.assertAlmostEqual(box[key], case['card'][key], delta=2)
                m = {'content': json.dumps({'text':fixture['text']})}
                s = {'target_timerange':{'start':0,'duration':5000000}}
                report = ct.style_plan(fixture['text'], case, fixture['canvas'], 'hook')
                m = report['preset']['style']
                s = {'clip': {'transform': report['preset']['transform']}}
                self.assertAlmostEqual((1-s['clip']['transform']['y'])*1024/2, case['card']['centerY'], delta=2)
                self.assertEqual(report['geometry'], box)
                self.assertTrue(m['force_apply_line_max_width'])
                self.assertEqual(report['text'], fixture['text'])

    def test_free_positioning_is_not_invented(self):
        with self.assertRaisesRegex(ValueError, 'does not persist'):
            activity_box('Text', {'x':.4,'y':.3}, {'width':576,'height':1024}, ct.resolve_font('Noto Serif'), 'hook')

    def test_header_geometry_matches_actual_region_modes(self):
        fixture = json.loads(Path(__file__).with_name('fixtures').joinpath('capcut_activity_geometry.json').read_text())
        for case in fixture['header_cases']:
            settings = {**case, 'font_size':40, 'alignment':'center'}
            box = activity_box('Native title geometry', settings, fixture['canvas'],
                               ct.resolve_font('Arial', 700), 'title')
            with self.subTest(position=case['position'], region=case['region']):
                for key in ('x','y','width','height','centerX','centerY'):
                    self.assertAlmostEqual(box[key], case['header'][key], delta=3)

    def test_two_clips_do_not_share_geometry(self):
        text = 'Each clip keeps its own settings'
        font = ct.resolve_font('Noto Serif')
        first = activity_box(text, {'position':'top','size':'S'}, {'width':576,'height':1024}, font, 'hook')
        second = activity_box(text, {'position':'bottom','size':'L','region':'dedicated'},
                              {'width':576,'height':1024}, font, 'hook')
        self.assertAlmostEqual(first['centerY'], 204.8)
        self.assertAlmostEqual(second['centerY'], 772.096)
        self.assertAlmostEqual(second['font_px']/first['font_px'], 1.3/.8)
    def apply(self, role, settings, text='A short editable line'):
        plan = ct.style_plan(text, settings, {'width': 576, 'height': 1024}, role)
        return plan['preset']['style'], plan

    def test_classic_hook_uses_native_font_and_card_schema(self):
        m, s = self.apply('hook', {'style': 'classic', 'duration_seconds': 5},
                         'Stepping into a freezing cold ice bath in the backyard')
        self.assertEqual(m['background_style'], 1)  # native 1121 (1)
        self.assertEqual(m['background_color'], '#FFFFFF')
        self.assertAlmostEqual(m['background_alpha'], 240/255)
        self.assertEqual(m['alignment'], 1)
        content = {'text': s['text']}
        self.assertNotIn('\n', content['text'])
        self.assertTrue(m['force_apply_line_max_width'])
        self.assertTrue(m['font_path'].endswith('NotoSerif-Bold.ttf'))
        self.assertEqual(s['duration_seconds'], 5)

    def test_no_card_hook_clears_background_and_uses_saved_style(self):
        m, s = self.apply('hook', {'style': 'outline_yellow', 'position': 'bottom',
            'font_size': 30, 'alignment': 'left', 'wrap_width': 0.55,
            'duration_seconds': 3, 'weight': 400, 'font_family': 'Arial'})
        self.assertEqual(m['background_alpha'], 0)
        self.assertEqual(m['text_color'], '#FFD600')
        self.assertEqual(m['alignment'], 0)
        self.assertLess(s['preset']['transform']['y'], 0)
        self.assertEqual(s['duration_seconds'], 3)
        self.assertIn('Arial', m['font_path'])

    def test_title_uses_its_own_saved_values(self):
        m, s = self.apply('title', {'font_family': 'Georgia', 'font_size': 60,
            'text_color': '#123456', 'background': 'solid', 'background_color': '#ABCDEF',
            'background_opacity': 0.4, 'corner_radius': 0.3, 'alignment': 'right'})
        self.assertEqual(m['text_color'], '#123456')
        self.assertEqual(m['background_color'], '#ABCDEF')
        self.assertEqual(m['background_alpha'], 0.4)
        self.assertEqual(m['background_round_radius'], 0.3)
        self.assertEqual(m['alignment'], 2)
        self.assertIn('Georgia', m['font_path'])

    def test_caption_preserves_cue_times_and_uses_caption_settings(self):
        m, s = self.apply('caption', {'font_name': 'Arial', 'font_size': 36,
            'font_color': '#00FF00', 'bg_color': '#112233', 'bg_opacity': 0.7,
            'position': 'center', 'uppercase': True, 'base_opacity': 0.8}, text='Hi')
        self.assertEqual(m['text_color'], '#00E600')
        self.assertEqual(m['background_color'], '#112233')
        self.assertEqual(m['background_alpha'], 0.7)
        self.assertEqual(m['text_alpha'], 1)
        self.assertIsNone(s['duration_seconds'])
        self.assertEqual(s['text'], 'HI')

    def test_unknown_font_is_reported_instead_of_silent_system_substitution(self):
        with self.assertRaisesRegex(ValueError, 'font'):
            self.apply('hook', {'font_family': 'MissingTestFont'})
