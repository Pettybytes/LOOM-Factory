"""Pixel geometry port of the Activity editor's TextOverlayPreview.

The Activity preview is authoritative for this lane. hooks.py uses a different
anchor and padded raster image, so its image dimensions cannot position these
native objects. Browser-measured fixtures guard this port against drift.
"""
from PIL import ImageFont
from capcut_handoff import active_title_overlay


def wrapped_lines(text, font, width):
    # Arithmetic at a shrink-to-fit boundary can differ by a few float ULPs.
    width += 1e-6
    lines = []
    for paragraph in text.split('\n'):
        line = ''
        for word in paragraph.split():
            candidate = (line + ' ' + word).strip()
            if line and font.getlength(candidate) > width:
                lines.append(line)
                line = ''
            while font.getlength(word) > width and len(word) > 1:
                end = 1
                while end < len(word) and font.getlength(word[:end + 1]) <= width:
                    end += 1
                lines.append(word[:end])
                word = word[end:]
            line = (line + ' ' + word).strip()
        lines.append(line)
    return lines


def activity_box(text, settings, canvas, font_path, role, has_background=True):
    """Resolve saved presets to canvas pixels; there is no drag schema today."""
    w, h = float(canvas['width']), float(canvas['height'])
    mode = settings.get('region', 'overlay')
    if any(k in settings for k in ('x', 'y', 'geometry')):
        raise ValueError('The Activity editor does not persist free text positioning')
    content_width = w * .84  # OVERLAY_PADDING_PCT=8 on each side
    position = settings.get('position', 'top')
    if role == 'hook':
        scale = {'S': .8, 'M': 1, 'L': 1.3}.get(settings.get('size', 'M'), 1)
        font_px = content_width * .05 * scale
        # cqw resolves against the hook container CONTENT box, after padding.
        padding_x = content_width * .015 * scale if has_background else 0
        padding_y = content_width * .0125 * scale if has_background else 0
        max_box_width = content_width * .9
        line_height = font_px * 1.4
        radius = 20 if has_background else 0
        video_top = .18 if mode == 'dedicated' else 0
        fraction = {'top': .2, 'center': .5, 'bottom': .7}.get(position, .2)
        center_y = h * (video_top + (1-video_top) * fraction)
    elif role == 'title':
        font_px = float(settings.get('font_size', 40)) * .4
        padding_x, padding_y = (10, 4) if has_background else (0, 0)
        max_box_width, line_height = content_width, font_px * 1.5
        radius = 6 if has_background else 0
        # HeaderLayer calls headerBand() without its mode: the actual current
        # preview keeps a 14% band in both modes. Mirror it, don't silently fix UI.
        center_y = h * .07
    else:
        raise ValueError('Activity geometry supports hook and title')
    # BASIC FreeType rounds small glyph advances. Measure Header text at
    # higher resolution, then scale back to CSS fractional-pixel metrics.
    metric_scale = 100 if role == 'title' else 1
    font = ImageFont.truetype(font_path, max(1, font_px * metric_scale))
    width = min(max_box_width, max(font.getlength(p) / metric_scale for p in text.split('\n')) + 2*padding_x)
    wrap_width = max(1, width - 2*padding_x)
    lines = wrapped_lines(text, font, wrap_width * metric_scale)
    height = len(lines) * line_height + 2*padding_y
    x = (w-width)/2
    if role == 'title':
        if position == 'upper':
            center_y = h * .14 * (2 / (18 if mode == 'dedicated' else 14)) + height/2
        alignment = settings.get('alignment', 'center')
        if alignment == 'left': x = w * .08
        elif alignment == 'right': x = w * .92 - width
    return {'source': 'Activity TextOverlayPreview', 'x': x, 'y': center_y-height/2,
            'width': width, 'height': height, 'centerX': x+width/2, 'centerY': center_y,
            'font_px': font_px, 'line_height': line_height, 'padding_x': padding_x,
            'padding_y': padding_y, 'radius_px': radius, 'wrap_width_px': wrap_width,
            'lines': lines}


def header_video_band(overlay, canvas):
    """Activity PreviewSurface's dedicated region in output pixels.

    The source is the already-composed full-frame video. Cropping 9% off each
    source edge retains 82% of its height; shifting its centre down by 9% puts
    that region below the 18% band without stretching or re-encoding it.
    """
    if active_title_overlay(overlay).get('region') != 'dedicated':
        return None
    w, h = float(canvas['width']), float(canvas['height'])
    return {'x':0, 'y':h*.18, 'width':w, 'height':h*.82,
            'crop':[0, .09, 1, .82], 'transform_y':-.18,
            'source':'Activity PreviewSurface.canvasLayout'}
