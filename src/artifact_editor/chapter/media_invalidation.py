"""Invalidate generated outputs whose narration or timing has changed."""
from pathlib import Path
import time
import shutil

from filelock import FileLock

import const


def _invalidate_steps(chapter, actions):
    from .workflow import _path, _save, completed_steps

    path = _path(chapter)
    path.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(path) + '.lock'):
        done = completed_steps(chapter)
        done.difference_update(actions)
        _save(path, done)


def invalidate_chapter_video(chapter):
    # Preserve the managed Active link while removing its generated Shelf file.
    video = Path(chapter.get_video_filename())
    video.resolve().unlink(missing_ok=True)
    chapter_dir = Path(const.LIBRARY_DIR) / chapter.chapterdir
    for filename in ('masterplan.json', 'camera.json'):
        (chapter_dir / filename).unlink(missing_ok=True)
    _invalidate_steps(chapter, {'textoviva_rerender'})


def invalidate_insufficient_animations(chapter):
    from .timeline import image_timings
    from .readiness import _animation_is_disabled
    from artifact_editor.images.animation_source import animation_frame_count

    timings = image_timings(chapter.get_xml())
    invalidated = []
    for image in chapter.images():
        required = int(timings.get(str(image['index']), {}).get('frames', 0))
        timing_changed = int(image.get('frames', 0)) != required
        if timing_changed:
            image['frames'] = str(required)
        if _animation_is_disabled(image) or required <= 0:
            if timing_changed:
                chapter.save_image(image)
            continue
        paragraph = image.find_parent('paragraph')
        directory = Path(const.LIBRARY_DIR) / chapter.get_paragraph_dir(int(paragraph['index']))
        available = animation_frame_count(image, directory, const.FPS)
        if available is not None and available >= required:
            if timing_changed:
                chapter.save_image(image)
            continue
        # Keep the original files, but prevent old MP4 variants and extracted
        # PNGs from silently satisfying this shot until a new animation exists.
        image['animation_invalidated_ns'] = str(time.time_ns())
        image.attrs.pop('animated_src', None)
        for field in ('animation_oom_audio_frames', 'animation_oom_success_frames',
                      'animation_oom_failure_frames'):
            image.attrs.pop(field, None)
        chapter.save_image(image)
        # Frame workers also have a low-level PNG/companion-video fallback.
        # Remove those expansions so they cannot bypass the invalidation stamp.
        for expansion in (directory / 'animation').glob(f'image_{int(image["index"]):06d}_*'):
            if expansion.is_dir():
                shutil.rmtree(expansion)
        if image.get('src'):
            companion = directory / Path(image['src']).with_suffix('.mp4').name
            companion.unlink(missing_ok=True)
        invalidated.append(str(image['index']))
    if invalidated:
        _invalidate_steps(chapter, {'chapter_missing_animations', 'textoviva_rerender'})
    return invalidated
