"""Choose a square still or a camera pan without cutting off source artwork."""
import hashlib
import json
import math
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import time

import numpy as np
from PIL import Image, ImageOps

from .source_motion import active_motion, normalize_motion, output_size

MAX_ADDED_WHITESPACE = .10
VERSION = 1


def content_bounds(original):
    """Trim only a uniform, light paper border; preserve full-bleed pictures."""
    image = ImageOps.exif_transpose(original).convert('RGBA')
    paper_image = Image.new('RGBA', image.size, 'white')
    paper_image.alpha_composite(image)
    paper_image = paper_image.convert('RGB')
    sample = paper_image.copy()
    sample.thumbnail((1200, 1200))
    pixels = np.asarray(sample).astype(np.int16)
    border = np.concatenate((pixels[0], pixels[-1], pixels[:, 0], pixels[:, -1]))
    paper = np.median(border, axis=0)
    full = (0, 0, image.width, image.height)
    if paper.min() < 180 or np.percentile(np.max(np.abs(border-paper), axis=1), 90) > 24:
        return full, tuple(int(v) for v in paper)
    ink = np.max(np.abs(pixels-paper), axis=2) > 18
    y, x = np.nonzero(ink)
    if not len(x):
        return full, tuple(int(v) for v in paper)
    sx, sy = image.width/sample.width, image.height/sample.height
    padding = max(2, round(min(image.size)*.01))
    bounds = (max(0, math.floor(x.min()*sx)-padding),
              max(0, math.floor(y.min()*sy)-padding),
              min(image.width, math.ceil((x.max()+1)*sx)+padding),
              min(image.height, math.ceil((y.max()+1)*sy)+padding))
    return bounds, tuple(int(v) for v in paper)


def layout_plan(original, target_size=(1080, 1080)):
    image = ImageOps.exif_transpose(original)
    width, height = image.size
    bounds, paper = content_bounds(image)
    left, top, right, bottom = bounds
    side = max(right-left, bottom-top)
    # A square within the scan removes only blank margins. Otherwise allow
    # a small paper extension, measured as a share of the final square area.
    side = max(1, min(width, height)) if side <= min(width, height) else side
    x = round((left+right-side)/2)
    y = round((top+bottom-side)/2)
    if side <= width:
        x = min(width-side, max(0, x))
    if side <= height:
        y = min(height-side, max(0, y))
    overlap = max(0, min(width, x+side)-max(0, x))*max(0, min(height, y+side)-max(0, y))
    added = 1-overlap/(side*side)
    if added <= MAX_ADDED_WHITESPACE + 1e-9:
        return dict(mode='square', box=(x, y, x+side, y+side), paper=paper,
                    added_whitespace=added, bounds=bounds)
    aspect = target_size[0]/target_size[1]
    base = min(width, height*aspect)
    crop_width = max(min(right-left, (bottom-top)*aspect), base/8)
    crop_height = crop_width/aspect
    first = dict(x=(left+crop_width/2)/width, y=(top+crop_height/2)/height, zoom=base/crop_width)
    last = dict(x=(right-crop_width/2)/width, y=(bottom-crop_height/2)/height, zoom=base/crop_width)
    if right-left >= (bottom-top)*aspect:
        first['y'] = last['y'] = (top+bottom)/(2*height)
    else:
        first['x'] = last['x'] = (left+right)/(2*width)
    return dict(mode='pan', motion=normalize_motion(dict(first=first, last=last)), bounds=bounds)


def apply_source_layout(chapter, image, filename, *, origin='manual'):
    """Save the same decision used by import and the explicit Auto Pan button."""
    from .source import choose_source_image, source_image_path, source_directory

    path = source_image_path(chapter, filename)
    with Image.open(path) as original:
        original = ImageOps.exif_transpose(original)
        plan = layout_plan(original, output_size(chapter, image))
        selected = filename
        if plan['mode'] == 'square':
            x, y, right, bottom = plan['box']
            square = Image.new('RGB', (right-x, bottom-y), plan['paper'])
            rgba = original.convert('RGBA')
            square.paste(rgba, (-x, -y), rgba)
            size = max(output_size(chapter, image))
            square = square.resize((size, size), Image.Resampling.LANCZOS)
            fingerprint = hashlib.sha256(path.read_bytes()+json.dumps([VERSION, plan['box'], size]).encode()).hexdigest()[:16]
            selected = f'cropped/{path.stem}_square_{fingerprint}.png'
            destination = source_directory(chapter)/selected
            destination.parent.mkdir(parents=True, exist_ok=True)
            with TemporaryDirectory(dir=destination.parent) as temporary:
                output = Path(temporary)/'square.png'
                square.save(output)
                os.replace(output, destination)
    choose_source_image(chapter, image, selected, motion=plan.get('motion'))
    image.attrs.pop('auto_chapter_title_pan', None)
    image.attrs.pop('animated_src', None)
    image['auto_source_layout'] = json.dumps(dict(version=VERSION, mode=plan['mode'],
                                                 input=filename, selected=selected, src=image['src'], origin=origin))
    image['source_layout_updated_ns'] = str(time.time_ns())
    if plan['mode'] == 'pan':
        image['do_not_animate'] = '1'
    else:
        image.attrs.pop('do_not_animate', None)
    chapter.save_image(image)
    return plan['mode']


def prepare_chapter_sources(chapter):
    """Prepare opening artwork first, then normalize other imported sources."""
    from .chapter_title import prepare_chapter_title
    from .source import SOURCE_WORKFLOW

    prepare_chapter_title(chapter)
    if not getattr(chapter, 'bookdir', None):
        return
    for image in chapter.get_xml().find_all('image'):
        filename = image.get('source_image')
        if not filename:
            continue
        if active_motion(image):
            if image.get('do_not_animate') != '1':
                image['do_not_animate'] = '1'
                chapter.save_image(image)
            continue
        if image.get('src') and image.get('workflow_image_template') != SOURCE_WORKFLOW:
            continue
        try:
            saved = json.loads(image.get('auto_source_layout', '{}'))
        except (ValueError, TypeError):
            saved = {}
        if (saved.get('version') == VERSION and saved.get('selected') == filename
                and saved.get('src') == image.get('src')):
            continue
        apply_source_layout(chapter, image, filename, origin='import')
