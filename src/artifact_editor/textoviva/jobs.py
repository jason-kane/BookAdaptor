"""Background tasks for Textoviva rendering."""

import os

import logger

log = logger.log(__name__)


def rerender_chapter(chapter, progress=None, image_index=None, animation_prompt=None, regenerate_image_indices=None, regenerate_insufficient=False, theater_stage=None, theater_project_id=None):
    """Rebuild rendered frames and video while retaining completed animations."""
    chapter.load_xml(force=True)
    if image_index is not None:
        image_xml = chapter.get_image(index=image_index)
        if image_xml is None:
            raise ValueError(f"Image {image_index} not found")
        image_xml.attrs['animation_prompt'] = animation_prompt
        chapter.save_xml()
    # Keep frame PNGs and their metadata; draw_frame invalidates changed inputs.

    # Theater's rough film is intentionally quick; the final film replaces its
    # Turbo animations at the higher preset. The initial rough draft remains a
    # still-and-audio cut so it is quick to review before animation.
    if theater_stage in ("rough_film", "final"):
        for image in chapter.images():
            image.attrs["workflow_i2v_template"] = "i2v.h3turbo"
            image.attrs["animation_quality"] = "low" if theater_stage == "rough_film" else "high"
        chapter.save_xml()

    chapter.load_xml(force=True)
    log.info(
        "Starting Textoviva re-render from button",
        video_filename=chapter.get_video_filename(),
    )
    # Rebuild from stills and existing animations. Generate animations only
    # when an explicit animation action selected them.
    try:
        if theater_stage == "rough":
            from artifact_editor.theater.rendering import (
                prepare_audio_and_aspect,
                preserve_render,
                rough_draft_progress,
                update_project,
            )

            progress = rough_draft_progress(chapter, progress)
            update_project(theater_project_id, "rough_rendering", render_error="")
            from gpu_tasks import task_label
            with task_label("Theater rough draft · timed stills and narration"):
                duration, aspect = prepare_audio_and_aspect(chapter, progress)
                chapter.textoviva.create_video(
                    force=False, rebuild=True, progress=progress, fast_draft=True,
                )
            if progress:
                progress(99, "Saving rough draft")
            preserve_render(chapter, theater_project_id, "rough")
            update_project(theater_project_id, "rough_ready", runtime_seconds=round(duration, 3), aspect=aspect, render_error="")
            return
        if theater_stage == "rough_film":
            from artifact_editor.theater.rendering import preserve_render, update_project
            update_project(theater_project_id, "rough_film_rendering", render_error="")
            animation_indices = [int(image["index"]) for image in chapter.images()]
            from gpu_tasks import task_label
            with task_label("Theater rough film · MiniMax H3 Turbo Low animation"):
                chapter.textoviva.create_video(
                    force=False, rebuild=True, progress=progress,
                    regenerate_image_indices=animation_indices,
                )
            preserve_render(chapter, theater_project_id, "rough")
            update_project(theater_project_id, "rough_film_ready", render_error="")
            return
        if theater_stage == "final":
            from artifact_editor.theater.rendering import preserve_render, update_project
            update_project(theater_project_id, "final_rendering", render_error="")
            animation_indices = [int(image["index"]) for image in chapter.images()]
            from gpu_tasks import task_label
            with task_label("Theater final film · MiniMax H3 Turbo High animation"):
                chapter.textoviva.create_video(
                    force=False, rebuild=True, progress=progress,
                    regenerate_image_indices=animation_indices,
                    regenerate_insufficient=True,
                )
            preserve_render(chapter, theater_project_id, "final")
            update_project(theater_project_id, "final_ready", render_error="")
            return
        chapter.textoviva.create_video(
            force=False, rebuild=True, progress=progress, regenerate_image_index=image_index,
            regenerate_insufficient=regenerate_insufficient,
            regenerate_image_indices=regenerate_image_indices
        )
    except Exception as error:
        if theater_project_id:
            from artifact_editor.theater.rendering import update_project
            update_project(theater_project_id, "render_failed", render_error=str(error)[:1000])
        raise
