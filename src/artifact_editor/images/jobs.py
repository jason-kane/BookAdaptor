"""Image generation jobs run outside the HTTP request."""
import os

import comfy
import const


def generate_timeline_image(chapter, image_index):
    from artifact_editor.images.selector.htmx import gather_candidate_images

    image = chapter.get_image(int(image_index))
    if image is None:
        raise ValueError(f"Image {image_index} no longer exists")
    template = image.get('workflow_image_template') or const.DEFAULT_IMAGE_TEMPLATE
    template = template.rsplit('.', 1)[-1]
    image['workflow_image_template'] = template
    workflow = chapter.get_workflow(
        image, workflow_name=template, video_index=None, template_environment={},
    )
    comfy.run_workflow_fn(workflow)
    candidates = gather_candidate_images(image, chapter)
    if not candidates:
        raise ValueError('Image generation produced no image')
    image['src'] = os.path.basename(candidates[0])
    chapter.save_xml()
    # The editor refresh uses a versioned preview URL, so no Flask cache is needed here.


def generate_timeline_metadata(chapter, image_index, progress):
    from artifact_editor.images import images

    image = chapter.get_image(int(image_index))
    if image is None:
        raise ValueError(f"Image {image_index} no longer exists")
    images.generate_metadata_from_text(chapter, image, force_all=True, progress=progress)
    progress(90, 'Generating illustration description')
    images.generate_image_description(image, force=True)
    chapter.save_xml()
