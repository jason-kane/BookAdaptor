"""Frame an opening book illustration above its printed body text."""
import json

import numpy as np
from PIL import Image, ImageOps

SETTING = 'auto_chapter_title_pan'
VERSION = 1


def illustration_box(original):
    """Find the white gap before regular body lines on a scanned opening page.

    Ink is measured relative to the paper tone, so yellowed scans work too.
    A separator must lead into several ordinary text lines; gaps within the
    artwork or above large decorative lettering do not qualify.
    """
    image = ImageOps.exif_transpose(original).convert('RGB')
    width, height = image.size
    scale = min(1, 1000 / max(width, height))
    sample = image.resize((round(width * scale), round(height * scale)))
    gray = np.asarray(sample.convert('L'))
    h, w = gray.shape
    margin = max(1, round(w * .025))
    ink = gray < min(170, float(np.percentile(gray, 90)) - 60)
    profile = ink[:, margin:w-margin].sum(axis=1)
    occupied = np.flatnonzero(profile >= max(3, w * .012))
    if not len(occupied):
        raise ValueError('The opening source image has no detectable illustration or text.')
    bands = []
    for row in occupied:
        if bands and row - bands[-1][1] <= 1:
            bands[-1][1] = int(row) + 1
        else:
            bands.append([int(row), int(row) + 1])

    def line_width(band):
        columns = np.flatnonzero(ink[band[0]:band[1], margin:w-margin].any(axis=0))
        return int(columns[-1] - columns[0] + 1) if len(columns) else 0

    bands = [band for band in bands if band[1]-band[0] >= max(3, .006*h)]
    body_heights = [b-a for a, b in bands if .5*h < a < .9*h
                    and line_width((a, b)) > .4*w and .005*h < b-a < .06*h]
    if len(body_heights) < 3:
        raise ValueError('Cannot find regular body text below the opening illustration.')
    line_height = float(np.median(body_heights))
    for index in range(1, len(bands)-2):
        top = bands[index-1][1]
        bottom = bands[index][0]
        if not (.18*h <= top <= .7*h and bottom-top >= max(8, .012*h)):
            continue
        following = bands[index:index+3]
        if line_width(following[0]) < .4*w:
            continue
        if not all(.45*line_height <= b-a <= 1.6*line_height
                   and line_width((a, b)) >= .22*w for a, b in following):
            continue
        if any(following[i+1][0] - following[i][1] > 2*line_height for i in range(2)):
            continue
        # Keep the artwork and decorative lettering above the separator, with
        # a small paper border. Exclude stray edge marks from the bounds.
        y, x = np.nonzero(ink[:top, margin:w-margin])
        if not len(x):
            continue
        padding = max(2, round(.008*h))
        box = (max(0, int(x.min())+margin-padding), max(0, int(y.min())-padding),
               min(w, int(x.max())+margin+1+padding), min(bottom-1, top+padding))
        return tuple(value / scale for value in box)
    raise ValueError('No clear gap separates the opening illustration from body text; set its source framing manually.')


def title_motion(original, target_size):
    from .source_motion import normalize_motion

    illustration = ImageOps.exif_transpose(original)
    width, height = illustration.size
    left, top, right, bottom = illustration_box(illustration)
    aspect = target_size[0] / target_size[1]
    crop_height = min(bottom-top, (right-left) / aspect * .85)
    crop_width = crop_height * aspect
    zoom = min(width, height*aspect) / crop_width
    if not 1 <= zoom <= 8:
        raise ValueError('Opening illustration requires a crop beyond the source zoom controls; frame it manually.')
    motion = {'first': {'x': (left+crop_width/2)/width, 'y': (top+bottom)/(2*height), 'zoom': zoom},
              'last': {'x': (right-crop_width/2)/width, 'y': (top+bottom)/(2*height), 'zoom': zoom}}
    return normalize_motion(motion), (left, top, right, bottom)


def prepare_chapter_title(chapter):
    """Apply the book opt-in once, preserving manually selected source motion."""
    if not getattr(chapter, 'bookdir', None):
        return False
    from artifact_editor.book.pages import book_for
    enabled = book_for(chapter).config.get(SETTING, False)
    if enabled is not True and str(enabled).lower() not in {'1', 'true', 'on'}:
        return False
    from .source import choose_source_image, source_image_path
    from .source_motion import active_motion, output_size

    image = next((image for image in chapter.get_xml().find_all('image') if image.get('source_image')), None)
    if image is None:
        return False
    try:
        layout = json.loads(image.get('auto_source_layout', '{}'))
    except (TypeError, ValueError):
        layout = {}
    if layout.get('origin') == 'manual':
        return False
    if active_motion(image) and not layout:
        if image.get('do_not_animate') != '1':
            image['do_not_animate'] = '1'
            chapter.save_image(image)
            return True
        return False
    if image.get('src') and image.get('workflow_image_template') != 'source.chapter':
        return False
    filename = layout.get('input') or image['source_image']
    path = source_image_path(chapter, filename)
    with Image.open(path) as original:
        motion, box = title_motion(original, output_size(chapter, image))
    choose_source_image(chapter, image, filename, motion=motion)
    image.attrs.pop('auto_source_layout', None)
    image.attrs.pop('source_layout_updated_ns', None)
    image['do_not_animate'] = '1'
    image['auto_chapter_title_pan'] = json.dumps({'version': VERSION, 'box': box})
    chapter.save_image(image)
    return True
