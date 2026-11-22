"""OpenShorts saved text styles -> capcut-cli presets and command arguments.

Field structure is from the operator-created 1121 (1) reference. Layout follows
the Activity editor's pixel measurements; CapCut unit conversion still needs visual proof.
No reference project or particular hook copy is needed at runtime.
"""
import json
from pathlib import Path

from capcut_handoff import HOOK_STYLE_CARD, HOOK_SIZE_SCALE
from capcut_geometry import activity_box, wrapped_lines

CATALOG = json.loads(Path(__file__).with_name('capcut_text_styles.json').read_text())
STYLE_TABLE = {**HOOK_STYLE_CARD, **CATALOG['styles']}


def resolve_font(family, weight=None):
    family = family or 'Noto Serif'
    bold = str(weight).lower() in ('bold', '700', '800', '900')
    names = CATALOG['fonts'].get(family) or [
        family.replace(' ', '') + (' Bold' if bold else '') + '.ttf',
        family + (' Bold' if bold else '') + '.ttf', family + '.ttc']
    roots = [Path.home() / 'Library/Containers/com.lemon.lvoverseas/Data/Library/Fonts',
             Path.home() / 'Library/Fonts', Path('/System/Library/Fonts/Supplemental'),
             Path('/System/Library/Fonts'), Path('/Library/Fonts'),
             Path(__file__).resolve().parents[2] / 'openshorts/fonts']
    for root in roots:
        for name in names:
            if (root / name).is_file():
                return str(root / name)
    raise ValueError(f'CapCut font unavailable: {family} (weight {weight})')


def style_plan(text, settings, canvas, role='hook'):
    """Return a capcut-cli preset and pixel evidence; never edit draft internals."""
    from PIL import ImageFont
    s = settings or {}
    w, h = int(canvas['width']), int(canvas['height'])
    is_hook = role == 'hook'
    style_name = s.get('style', 'classic') if is_hook else 'classic'
    if style_name not in STYLE_TABLE:
        raise ValueError(f'CapCut hook style is not mapped: {style_name}')
    look = STYLE_TABLE[style_name]
    color = s.get('text_color', s.get('font_color', s.get('color')))
    color = color or ('#%02X%02X%02X' % look['text'] if is_hook else '#FFFFFF')
    if role == 'caption':
        # subtitles._dim_hex_color dims inactive words as RGB, not alpha.
        factor = .5 + .5 * max(.05, min(1, float(s.get('base_opacity', 1))))
        color = '#' + ''.join(f'{round(int(color.lstrip(chr(35))[i:i+2], 16)*factor):02X}' for i in (0, 2, 4))
    bg = s.get('background_color', s.get('bg_color')) or '#%02X%02X%02X' % look['box'][:3]
    alpha = float(s.get('background_opacity', s.get('bg_opacity',
                  look['box'][3] / 255 if is_hook else 0)))
    if s.get('background') in (False, 'none', 'off'):
        alpha = 0
    elif s.get('background') in (True, 'solid', 'box', 'highlight') and not any(
            k in s for k in ('background_opacity', 'bg_opacity')):
        alpha = 1
    family = s.get('font_family', s.get('font_name', s.get('font'))) or ('Noto Serif' if is_hook else 'Verdana')
    font_path = resolve_font(family, s.get('weight', 700 if role == 'title' else None))
    wrap = float(s.get('wrap_width', 0.9 if is_hook else 0.84))
    padding_x = float(s.get('padding_x', 30 if is_hook else (10 if alpha else 0)))
    padding_y = float(s.get('padding_y', 25 if is_hook else (4 if alpha else 0)))
    default_px = int(int(w * wrap) * .05) * HOOK_SIZE_SCALE.get(str(s.get('size', 'M')).upper(), 1)
    px = float(s.get('font_size', default_px if is_hook else (16 if role == 'caption' else 40)))
    if role == 'title':
        px *= .4  # create_header_image's saved font_size -> raster pixels
    elif role == 'caption':
        px = max(10, int(max(10, min(200, px)) * .85)) * h / 288
    if s.get('uppercase'):
        text = text.upper()
    geometry = None
    if role in ('hook', 'title'):
        geometry = activity_box(text, s, canvas, font_path, role, alpha > 0)
        px = geometry['font_px']
        padding_x, padding_y = geometry['padding_x'], geometry['padding_y']
    font = ImageFont.truetype(font_path, max(1, px))
    max_width = max(1, w * wrap - padding_x * 2)
    lines = wrapped_lines(text, font, max_width)
    # Preserve the operator's text, including any deliberate newlines. Native
    # wrapping must reflow if they edit the text in CapCut.
    if geometry:
        max_width = geometry['wrap_width_px']
        lines = geometry['lines']
    spacing = float(s.get('line_spacing_px', 20 if is_hook else 8))
    height = sum((font.getbbox(line)[3] - font.getbbox(line)[1]) if line else px for line in lines)
    height += spacing * (len(lines) - 1) + padding_y * 2
    position = s.get('position', s.get('alignment') if s.get('alignment') in ('top', 'middle', 'bottom') else None)
    position = position or ('top' if is_hook or role == 'title' else 'bottom')
    if role == 'title':
        center_y = h * (.02 if position == 'upper' else .07) + height / 2
    elif position in ('center', 'middle'):
        center_y = h / 2
    elif role == 'caption':
        margin = h * 43 / 288  # subtitles.py SAFE_MARGIN_V / PlayResY
        center_y = margin + height / 2 if position == 'top' else h - margin - height / 2
    else:
        center_y = h * (.7 if position == 'bottom' else .2) + height / 2 + (20 if is_hook else 0)
    y = float(s.get('y', 1 - 2 * center_y / h))
    if geometry:
        y = 1 - 2 * geometry['centerY'] / h
    # Native unit calibration is distinct from the target Activity geometry.
    # The 1946 app check measured ~70% of the intended glyph size at scale=1.
    # Correct the engine conversion, never the saved per-clip S/M/L setting.
    # This calibration remains subject to the installed-app visual gate.
    size = px * 1080 / (min(w, h) * 6 * .7)
    align = s.get('alignment', 'center')
    align = {'left': 0, 'center': 1, 'right': 2}.get(align, align if isinstance(align, int) else 1)
    bold = str(s.get('weight', 700 if role == 'title' else '')).lower() in ('bold', '700', '800', '900')
    outline = look['outline'] if is_hook else None
    border_width = float(s.get('border_width', outline[1] if outline else 0))
    # These are the CLI's public preset fields (STYLE_FIELDS), not a direct
    # draft patch. The exported native template supplies the app-owned flags.
    style = dict(font_path=font_path, font_name='', font_id='', font_resource_id='',
        font_size=size, text_color=color, text_alpha=1 if role == 'caption' else float(s.get('base_opacity', 1)),
        alignment=align, typesetting=0, line_feed=1, bold=bold,
        line_spacing=(geometry['line_height']/px-1) if geometry else spacing/max(px, 1),
        line_max_width=max_width/w, force_apply_line_max_width=True,
        fixed_width=-1, fixed_height=-1,
        background_style=1 if alpha else 0, background_color=bg, background_alpha=alpha,
        background_round_radius=float(s.get('corner_radius', .2 if is_hook else 0)),
        background_width=float(s.get('background_width', .14)),
        background_height=float(s.get('background_height', .14)),
        background_horizontal_offset=0, background_vertical_offset=0,
        has_border=border_width > 0, border_width=border_width/max(px, 1),
        border_color=s.get('border_color', '#000000'), border_alpha=1,
        has_shadow=bool(s.get('shadow', look['shadow'] if is_hook else False)))
    transform = {'x': 2*geometry['centerX']/w-1 if geometry else 0, 'y': y}
    return {'preset': {'capcutCliPreset': 1, 'style': style, 'transform': transform},
            'text': text, 'duration_seconds': s.get('duration_seconds'),
            'font_path': font_path, 'font_size': size, 'transform_y': y,
            'lines': len(lines), 'geometry': geometry, 'wrap_mode': 'native',
            'native_size_calibration': 'provisional_9.2_visual_gate'}


def prepare_text_commands(spec, bootstrap, cues, workspace):
    """Map saved text into CLI template operations/presets before compilation."""
    workspace = Path(workspace)
    template = str(Path(__file__).with_name('templates') / 'capcut-native-text.json')
    auto = bootstrap.get('auto_edit') or {}
    canvas = {'width': spec['width'], 'height': spec['height']}
    plans, tracks = [], []
    operations = list(spec.get('operations', []))
    for track in spec['tracks']:
        if track['type'] != 'text':
            tracks.append(track)
            continue
        for index, item in enumerate(track['items']):
            ref = item.get('ref') or f'title_{index}'
            role = 'hook' if ref == 'hook' else 'title'
            settings = dict(auto.get('hook_settings') or {}) if role == 'hook' else dict(bootstrap.get('title_overlay') or {})
            if role == 'hook':
                settings.setdefault('region', (bootstrap.get('title_overlay') or {}).get('region', 'overlay'))
            plan = style_plan(item['text'], settings, canvas, role)
            plan.update(ref=ref, role=role)
            plans.append(plan)
            operations.append({'op': 'template', 'path': template, 'ref': ref,
                'text': plan['text'], 'start': item['start'], 'duration': item['duration']})
    if cues:
        settings = dict(auto.get('caption_settings') or {})
        plan = style_plan(cues[0]['text'], settings, canvas, 'caption')
        plan.update(ref='caption_seed', role='caption', settings=settings)
        plans.append(plan)
        operations.append({'op': 'template', 'path': template, 'ref': 'caption_seed',
                           'text': 'Caption style seed', 'start': 0, 'duration': .01})
        plan['ass'] = write_caption_ass(cues, settings, workspace / 'captions.ass')
    spec['tracks'] = tracks
    if operations:
        spec['operations'] = operations
    else:
        spec.pop('operations', None)
    for plan in plans:
        path = workspace / (plan['ref'] + '-preset.json')
        path.write_text(json.dumps(plan['preset'], indent=2))
        plan['preset_path'] = str(path)
    return plans


def write_caption_ass(cues, settings, path):
    """Use OpenShorts' own ASS generator; never parse/rewrite CapCut text runs."""
    import importlib.util
    import sys
    root = str(Path(__file__).resolve().parents[2] / 'openshorts')
    module_spec = importlib.util.spec_from_file_location('loom_caption_generator', Path(root) / 'subtitles.py')
    module = importlib.util.module_from_spec(module_spec)
    sys.path.insert(0, root)
    try:
        module_spec.loader.exec_module(module)
    finally:
        sys.path.remove(root)
    karaoke = settings.get('style') == 'karaoke'
    transcript = {'segments': [{'words': [{'word': ' ' + cue['text'],
        'start': cue['startMs']/1000, 'end': cue['endMs']/1000} for cue in cues]}]}
    names = ('font_name', 'font_color', 'border_color', 'border_width', 'bg_color',
             'bg_opacity', 'effect', 'base_opacity', 'uppercase')
    kwargs = {k: settings[k] for k in names if k in settings}
    kwargs.update(fontsize=settings.get('font_size', 16),
                  alignment=settings.get('alignment', settings.get('position', 'bottom')),
                  max_chars=settings.get('max_chars', 20) if karaoke else 0,
                  max_duration=settings.get('max_duration', 2),
                  highlight_color=settings.get('highlight_color', '#FFD700') if karaoke
                                  else settings.get('font_color', '#FFFFFF'))
    if not module.generate_ass(transcript, 0, max(c['endMs'] for c in cues)/1000,
                               str(path), **kwargs):
        raise ValueError('OpenShorts ASS generator produced no captions')
    return str(path)
