"""Shared reference workflows for generation and opening in ComfyUI."""
import json
import random
import uuid
from pathlib import Path
from urllib.parse import urlencode


def reference_workflow(kind, description):
    import comfy
    description = description.strip()
    if not description:
        raise ValueError('Enter a description before opening the workflow.')
    if kind == 'characters':
        prompt = ('Full-body image of a single character, entire body visible from head to feet, '
                  'centered with space above the head and below the feet. Plain, solid-color background '
                  'with strong contrast against the character so its outline and details are clearly visible. '
                  'no scenery, no text. Character description: ' + description)
        width, height = 768, 1152
    elif kind == 'props':
        prompt = ('A single prop, fully visible and centered on a plain, solid-color background '
                  'with strong contrast against the prop so its outline and details are clearly visible. '
                  'No people, no scenery, no text. Prop: ' + description)
        width, height = 1024, 1024
    else:
        raise ValueError('Unknown reference kind')
    workflow = comfy.load_workflow_template('api', 't2i', 'zit_style_sample').serialize()
    for node in workflow.values():
        if node.get('class_type') == 'EmptySD3LatentImage':
            node['inputs'].update(width=width, height=height)
        elif node.get('class_type') == 'KSampler':
            node['inputs']['seed'] = random.randrange(2**32)
    return workflow, {'PROMPT': prompt, 'FILENAME_PREFIX': kind + '/' + uuid.uuid4().hex}


def open_reference_workflow(kind, description):
    import const
    workflow, environment = reference_workflow(kind, description)
    def render(value):
        if isinstance(value, str):
            for key, replacement in environment.items():
                value = value.replace('{{' + key + '}}', replacement)
            return value
        if isinstance(value, list):
            return [render(item) for item in value]
        if isinstance(value, dict):
            return {key: render(item) for key, item in value.items()}
        return value
    filename = 'reference-' + uuid.uuid4().hex + '.json'
    directory = Path(const.COMFY_DIRS['artifactserver']['WORKFLOWS_DIR'])
    directory.mkdir(parents=True, exist_ok=True)
    (directory / filename).write_text(json.dumps(render(workflow), indent=2))
    return const.COMFYUI_UI_URL + '?' + urlencode({'workflow': filename})
