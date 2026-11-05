"""Check the actual media required by the Generate Video control."""
import os


def video_prerequisite(chapter, milestone_reason=None):
    if milestone_reason:
        return milestone_reason
    images = list(chapter.images())
    if not images:
        return 'Must place image slots first'
    for image in images:
        filename = chapter.get_image_filename(image)
        if not filename or not os.path.isfile(filename):
            return 'Must finish generating missing images first'

    import const
    from artifact_editor.chapter.timeline import image_timings
    from artifact_editor.images.animation_source import animation_frame_count
    timings = image_timings(chapter.get_xml())
    for image in images:
        if image.get('do_not_animate') == '1':
            continue
        paragraph = image.find_parent('paragraph')
        directory = os.path.join(const.LIBRARY_DIR, chapter.get_paragraph_dir(int(paragraph['index'])))
        count = animation_frame_count(image, directory, const.FPS)
        required = int(timings.get(str(image['index']), {}).get('frames', image.get('frames', 0)))
        if count is None or count <= 0 or count < required:
            return 'Must finish generating missing animation first'
    return None
