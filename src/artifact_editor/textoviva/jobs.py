"""Background tasks for Textoviva rendering."""

import os

import logger

log = logger.log(__name__)


def rerender_chapter(chapter, progress=None, image_index=None, animation_prompt=None, regenerate_image_indices=None, regenerate_insufficient=False):
    """Rebuild rendered frames and video while retaining completed animations."""
    chapter.load_xml(force=True)
    if image_index is not None:
        image_xml = chapter.get_image(index=image_index)
        if image_xml is None:
            raise ValueError(f"Image {image_index} not found")
        image_xml.attrs['animation_prompt'] = animation_prompt
        chapter.save_xml()
    # Keep frame PNGs and their metadata; draw_frame invalidates changed inputs.
    # The renderer skips an existing output video, so clear its output before rebuilding.
    videofile = chapter.get_video_filename()
    if os.path.isfile(videofile):
        os.remove(videofile)

    chapter.load_xml(force=True)
    log.info(
        "Starting Textoviva re-render from button",
        video_filename=chapter.get_video_filename(),
    )
    # Rebuild the chapter render from available assets. Textovia will create
    # animations that are missing, while reusing completed paragraph/ComfyUI
    # animations instead of deleting and regenerating them on every rerender.
    chapter.textoviva.create_video(
        force=False, progress=progress, regenerate_image_index=image_index,
        regenerate_insufficient=regenerate_insufficient,
        regenerate_image_indices=regenerate_image_indices
    )
