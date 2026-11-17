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
        prompt = (
            'Create a clear character design reference illustration of exactly one character. '
            'Show the entire figure from head to feet, large and centered, in a relaxed neutral '
            'standing pose facing forward, with arms slightly separated from the torso and both '
            'hands and feet visible. Keep the face fully visible and readable; do not cover it '
            'with hair, a hat brim, a mask, or shadow. Use soft, even front lighting and balanced '
            'exposure so facial features, skin or fur, hair, clothing colors, materials, and small '
            'accessories are easy to distinguish. Use a simple light neutral background with gentle '
            'contrast. This is a usable visual reference, not a dramatic scene: no silhouette, '
            'backlighting, deep shadows, spotlight, dramatic color cast, monochrome treatment, '
            'cropped limbs, multiple views, extra characters, text, or decorative frame. Follow '
            'the character description closely: preserve every specified physical feature, species, '
            'age, color, garment, and accessory; do not omit described details or invent new ones. '
            'Include described equipment or carried items, but no other props. No scenery. '
            'Character description: ' + description
        )
        width, height = 768, 1152
    elif kind == 'props':
        prompt = ('A single prop, fully visible and centered on a plain, solid-color background '
                  'with strong contrast against the prop so its outline and details are clearly visible. '
                  'No people, no scenery, no text. Prop: ' + description)
        width, height = 1024, 1024
    elif kind == 'scenes':
        prompt = ('An establishing view of the empty environment: architecture, terrain, permanent structural features, atmosphere, and lighting only. Props will be added separately; keep this backdrop empty of story objects and characters. No text. Setting: ' + description)
        width, height = 1536, 1024
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
