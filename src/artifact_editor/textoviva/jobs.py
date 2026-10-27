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

    chapter.load_xml(force=True)
    log.info(
        "Starting Textoviva re-render from button",
        video_filename=chapter.get_video_filename(),
    )
    # Rebuild from stills and existing animations. Generate animations only
    # when an explicit animation action selected them.
    chapter.textoviva.create_video(
        force=False, rebuild=True, progress=progress, regenerate_image_index=image_index,
        regenerate_insufficient=regenerate_insufficient,
        regenerate_image_indices=regenerate_image_indices
    )
