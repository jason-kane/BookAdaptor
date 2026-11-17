"""Check the actual media required by the Generate Video control."""
import os


def _animation_is_disabled(image):
    value = image.get('do_not_animate', '')
    return str(value).strip().lower() in {'1', 'true', 'yes', 'on'}


def animation_is_complete(image, frame_count, required_frames):
    return _animation_is_disabled(image) or (
        frame_count is not None and frame_count > 0 and frame_count >= required_frames
    )


def has_missing_images(chapter):
    for image in chapter.images():
        filename = chapter.get_image_filename(image)
        if not filename or not os.path.isfile(filename):
            return True
    return False


def video_prerequisite(chapter, *, require_verified_speakers=True):
    import const
    from artifact_editor.chapter.timeline import phrase_audio_missing
    xml = chapter.get_xml()
    if not xml.find('phrase'):
        return 'Must import chapter first'
    if require_verified_speakers and not chapter.config.get('character_attributions_verified', False):
        return 'Must verify all character attributions first'
    if any(phrase_audio_missing(chapter, phrase, const.LIBRARY_DIR)
           for phrase in xml.find_all('phrase')):
        return 'Must finish generating missing audio first'
    images = list(chapter.images())
    if has_missing_images(chapter):
        return 'Must finish generating missing images first'

    from artifact_editor.images.animation_source import animation_frame_count
    from artifact_editor.chapter.timeline import image_timings
    timings = image_timings(xml)
    for image in images:
        if _animation_is_disabled(image):
            continue
        paragraph = image.find_parent('paragraph')
        directory = os.path.join(const.LIBRARY_DIR, chapter.get_paragraph_dir(int(paragraph['index'])))
        count = animation_frame_count(image, directory, const.FPS)
        required = int(timings.get(str(image['index']), {}).get('frames', image.get('frames', 0)))
        if not animation_is_complete(image, count, required):
            return 'Must finish generating missing animation first'
    return None
