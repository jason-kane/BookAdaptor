"""Repair backward time-of-day transitions without replacing valid image slots."""

TIME_ORDER = ('predawn', 'dawn', 'morning', 'day', 'afternoon', 'dusk', 'evening', 'night')


def time_corrections(image_tags):
    """Return backward transitions in document order; unknown times add no constraint."""
    previous = None
    corrections = []
    for image in image_tags:
        current = str(image.get('tod', '')).strip().lower()
        if current not in TIME_ORDER:
            continue
        if previous is not None and TIME_ORDER.index(current) < TIME_ORDER.index(previous):
            corrections.append((image, previous))
        else:
            previous = current
    return corrections


def align_chapter_time(chapter, progress=None):
    import os
    import shutil
    import uuid
    from pathlib import Path

    import comfy
    import const
    from artifact_editor.images.selector.htmx import gather_candidate_images
    from artifact_editor.textoviva.jobs import rerender_chapter

    report = progress or (lambda percent, message: None)
    chapter.load_xml(force=True)
    corrections = time_corrections(chapter.images())
    repaired = [int(image['index']) for image in chapter.images()
                if image.get('time_align_pending') == 'true']
    if not corrections and not repaired:
        report(100, 'Time Align: no backward time-of-day transitions found')
        return 0

    for position, (image, corrected_time) in enumerate(corrections):
        index = int(image['index'])
        report(int(40 * position / len(corrections)),
               f'Image {index}: replacing {image["tod"]} with {corrected_time}')
        old_attributes = dict(image.attrs)
        old_candidates = set(gather_candidate_images(image, chapter))
        image['tod'] = corrected_time
        image['prompt'] = (
            old_attributes.get('prompt', '')
            + f'\nRequired time of day: {corrected_time}. '
            'Use lighting consistent with this time; replace conflicting time-of-day details.'
        ).strip()
        workflow = (image.get('workflow_image_template') or const.DEFAULT_IMAGE_TEMPLATE).split('.')[-1]
        try:
            workflow_file = chapter.get_workflow(
                image, workflow_name=workflow, video_index=None, template_environment={
                    'PROMPT': image['prompt'],
                    'PROMPT_FN': f'{chapter.nice}_img_{index}_time_align_{uuid.uuid4().hex}.prompt',
                }
            )
            if not workflow_file:
                raise ValueError(f'No image workflow for image {index}')
            comfy.run_workflow_fn(workflow_file)
            candidates = set(gather_candidate_images(image, chapter)) - old_candidates
            if not candidates:
                raise ValueError(f'No replacement generated for image {index}')
            replacement = max(candidates, key=os.path.getmtime)
            image['src'] = replacement
            image.attrs.pop('animation_prompt', None)
            image['time_align_pending'] = 'true'
            chapter.save_xml()
        except Exception:
            image.attrs.clear()
            image.attrs.update(old_attributes)
            chapter.save_xml()
            raise

        # Remove rejected candidates from active selectors while retaining an
        # archive. ComfyUI originals must also move, or selectors recopy them.
        archive_id = uuid.uuid4().hex
        rejected = {Path(path) for path in old_candidates}
        previous_src = old_attributes.get('src')
        if previous_src:
            source = Path(previous_src)
            if not source.is_absolute():
                source = Path(const.LIBRARY_DIR) / source
            if source.is_file():
                rejected.add(source)
        output = Path(const.COMFY_DIRS['artifactserver']['OUTPUT_DIR'])
        for source in output.glob(f'{chapter.nice}_img_{index}_*.png'):
            if source.name.removeprefix(chapter.nice + '_') in {p.name for p in rejected}:
                rejected.add(source)
        for source in rejected:
            if source.is_file() and source.resolve() != Path(replacement).resolve():
                archive = source.parent / '.time-align-rejected' / archive_id
                archive.mkdir(parents=True, exist_ok=True)
                shutil.move(str(source), str(archive / source.name))
        repaired.append(index)

    report(45, f'Time Align: rebuilding {len(repaired)} affected animations and video')
    rerender_chapter(
        chapter, regenerate_image_indices=repaired,
        progress=lambda percent, message: report(45 + int(percent * .55), message),
    )
    chapter.load_xml(force=True)
    for image in chapter.images():
        if int(image['index']) in repaired:
            image.attrs.pop('time_align_pending', None)
    chapter.save_xml()
    return len(repaired)
