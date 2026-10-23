"""Build reference slots and the matching prompt manifest for ref2i workflows."""
import hashlib
import json
from pathlib import Path
import shutil


def reference_inputs(scene_image, chapter_directory, image_xml):
    directory = Path(chapter_directory)
    scene = Path(scene_image) if scene_image else None
    if scene is None or not scene.is_file():
        raise ValueError('ref2i requires an existing current scene image (IMAGE_1).')
    result = [{'slot': 'IMAGE_1', 'image': '<image1>', 'kind': 'scene',
               'name': 'Current scene', 'path': scene}]
    for kind, attribute in [('characters', 'scene_characters'), ('props', 'scene_props')]:
        tags = list(dict.fromkeys(tag.strip() for tag in image_xml.get(attribute, '').split(',') if tag.strip()))
        catalog_file = directory / (kind + '.json')
        records = json.loads(catalog_file.read_text()) if catalog_file.exists() else {}
        used = set()
        for selection in tags:
            tag = selection if selection in records else next((key for key, record in records.items()
                if selection in [record.get('name'), *record.get('alias', [])]), None)
            if tag is None:
                raise ValueError(f'ref2i cannot find selected {kind} record: {selection}.')
            if tag in used:
                continue
            used.add(tag)
            path = directory / kind / (tag + '.png')
            if not path.is_file():
                raise ValueError(f'ref2i reference image is missing for {kind}: {records[tag].get("name", tag)} ({path}).')
            number = len(result) + 1
            result.append({'slot': f'IMAGE_{number}', 'image': f'<image{number}>',
                           'kind': kind, 'tag': tag, 'name': records[tag].get('name', tag),
                           'aliases': records[tag].get('alias', []), 'path': path})
    if len(result) > 10:
        raise ValueError(f'ref2i supports at most nine character/prop references; this scene selects {len(result) - 1}.')
    return result


def prepare_workflow(workflow, references, input_directory, overrides=None):
    """Populate IMAGE_N nodes and disconnect absent optional reference sockets."""
    overrides = overrides or {}
    references = [dict(reference) for reference in references]
    for reference in references:
        if reference['slot'] in overrides:
            reference['path'] = Path(overrides[reference['slot']])
            if not reference['path'].is_file():
                reference['path'] = Path(input_directory) / overrides[reference['slot']]
            if not reference['path'].is_file():
                raise ValueError(f"ref2i image override is missing: {reference['slot']}.")
    active = {reference['slot'] for reference in references}
    removed = {node['id'] for node in workflow['nodes']
               if node.get('title', '').startswith('IMAGE_')
               and node.get('title', '')[6:].isdigit() and node['title'] not in active}
    removed_links = {link[0] for link in workflow['links'] if link[1] in removed or link[3] in removed}
    workflow['nodes'] = [node for node in workflow['nodes'] if node['id'] not in removed]
    workflow['links'] = [link for link in workflow['links'] if link[0] not in removed_links]
    for node in workflow['nodes']:
        for socket in node.get('inputs', []):
            if socket.get('link') in removed_links:
                socket['link'] = None
        for socket in node.get('outputs', []):
            if socket.get('links'):
                socket['links'] = [link for link in socket['links'] if link not in removed_links]
    nodes = {node.get('title'): node for node in workflow['nodes']}
    for reference in references:
        node = nodes.get(reference['slot'])
        if node is None or node['type'] not in ('LoadImage', 'PrimitiveString'):
            raise ValueError(f"ref2i template needs a LoadImage or PrimitiveString titled {reference['slot']}.")
    mapping = nodes.get('REFERENCE_IMAGES')
    if mapping is None:
        raise ValueError('ref2i template needs a REFERENCE_IMAGES prompt input.')
    destination = Path(input_directory)
    destination.mkdir(parents=True, exist_ok=True)
    for reference in references:
        source = reference['path']
        digest = hashlib.sha256(source.read_bytes()).hexdigest()[:16]
        filename = f"ref2i_{reference['slot'].lower()}_{digest}{source.suffix}"
        target = destination / filename
        if source.resolve() != target.resolve():
            shutil.copyfile(source, target)
        node = nodes[reference['slot']]
        if node['type'] == 'LoadImage':
            node['widgets_values'] = [filename, 'image']
            node['widgets_values_named'] = {'image': filename, 'upload': 'image'}
        else:
            node['widgets_values'] = [filename]
            node['widgets_values_named'] = {'value': filename}
    manifest = json.dumps([{key: value for key, value in reference.items() if key != 'path'}
                           for reference in references], indent=2)
    mapping['widgets_values'] = [manifest]
    mapping['widgets_values_named'] = {'value': manifest}
