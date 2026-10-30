"""Image generation jobs run outside the HTTP request."""
import os
import uuid

import comfy
import const


def generate_timeline_image(chapter, image_index, force=True):
    from artifact_editor.images.selector.htmx import gather_candidate_images

    image = chapter.get_image(int(image_index))
    if image is None:
        raise ValueError(f"Image {image_index} no longer exists")
    template = image.get('workflow_image_template') or const.DEFAULT_IMAGE_TEMPLATE
    from artifact_editor.images.source import SOURCE_WORKFLOW

    if template == SOURCE_WORKFLOW:
        raise ValueError('Choose a source image from Chapter Source')
    from artifact_editor.textoviva.workflow import image_workflow_name
    template = image_workflow_name(template)
    image['workflow_image_template'] = template
    template_environment = {}
    output_token = None
    previous_candidates = set(gather_candidate_images(image, chapter)) if force else set()
    if force:
        import random

        seed = random.SystemRandom().randrange(0, 2**32)
        output_token = uuid.uuid4().hex
        image['seed'] = str(seed)
        template_environment = {
            'SEED': seed,
            'FILENAME_PREFIX': (
                f'{chapter.nice}_img_{int(image_index)}_{template}_{output_token}'
            ),
        }
    workflow = chapter.get_workflow(
        image, workflow_name=template, video_index=None,
        template_environment=template_environment,
    )
    comfy.run_workflow_fn(workflow)
    candidates = gather_candidate_images(image, chapter)
    if not candidates:
        raise ValueError('Image generation produced no image')
    if force:
        fresh_candidates = [
            candidate for candidate in candidates
            if output_token in os.path.basename(candidate)
        ]
        if not fresh_candidates:
            fresh_candidates = [
                candidate for candidate in candidates
                if candidate not in previous_candidates
            ]
        if not fresh_candidates:
            raise ValueError('Image generation did not produce a new image')
        candidates = fresh_candidates
    image['src'] = os.path.basename(candidates[0])
    image['image_generation_id'] = uuid.uuid4().hex
    chapter.save_xml()
    from artifact_editor.images.completions import publish_image_completion
    publish_image_completion(chapter, image)
    # The editor refresh uses a versioned preview URL, so no Flask cache is needed here.


def generate_timeline_metadata(chapter, image_index, progress):
    from artifact_editor.images import images

    image = chapter.get_image(int(image_index))
    if image is None:
        raise ValueError(f"Image {image_index} no longer exists")
    def report_detail(percent, message):
        from gpu_tasks import gpu_label

        detail = f"Image {int(image_index) + 1} · {message}"
        gpu_label.set(detail)
        progress(percent, detail)

    images.generate_metadata_from_text(
        chapter, image, force_all=True, progress=report_detail
    )
    report_detail(90, 'Writing illustration description')
    images.generate_image_description(image, force=True)
    chapter.save_xml()
