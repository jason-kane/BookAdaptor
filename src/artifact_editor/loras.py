"""LoRA catalog, book defaults, and generation provenance."""
import json
import os
from pathlib import Path


def normalize(value):
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, list) or any(not isinstance(v, str) or not v for v in value):
        raise ValueError("LoRAs must be a list of filenames")
    return list(dict.fromkeys(value))


def catalog():
    import const
    items = []
    for path in sorted(Path(const.LORA_DIR).glob('*.safetensors')):
        metadata = path.with_name(path.name + '.json')
        info = json.loads(metadata.read_text()) if metadata.exists() else {}
        if info.get('model', 'z-image-turbo') != 'z-image-turbo':
            continue
        items.append({**info, 'filename': path.name, 'name': info.get('name') or path.stem,
                      'weight': float(info.get('weight', 1))})
    if os.environ.get('CHAPTER_STORAGE', 'xml') == 'postgres':
        from sqlalchemy import select
        from sqlalchemy.dialects.postgresql import insert
        from artifact_editor.db import session_scope
        from artifact_editor.db.models import Lora
        with session_scope() as session:
            for info in items:
                session.execute(insert(Lora).values(filename=info['filename'], name=info['name'],
                    weight=info['weight'], details=info).on_conflict_do_nothing(index_elements=['filename']))
            items = [{**row.details, 'filename': row.filename, 'name': row.name, 'weight': float(row.weight)}
                     for row in session.scalars(select(Lora).where(Lora.model == 'z-image-turbo').order_by(Lora.name))]
    return sorted(items, key=lambda item: item['name'].lower())


def image_selection(chapter, image):
    """Snapshot book defaults and always include LoRAs linked to the image style."""
    if 'loras' not in image.attrs:
        from artifact_editor.config import get_config
        settings = get_config(chapter.bookdir)
        image['loras'] = json.dumps(normalize(settings.get('default_loras', [])))
    selected = normalize(image['loras'])
    try:
        style_tag = chapter.get_image_style(image)
        if style_tag:
            from styler.styles import Style
            style = Style(style_tag)
            linked = style.style.get('loras', '[]')
            selected = list(dict.fromkeys([*selected, *normalize(linked)]))
    except ValueError:
        # Images without a configured/saved style keep their explicit selections.
        pass
    return selected


def apply_to_workflow(workflow, filenames):
    """Insert model-only loaders after each diffusion model loader, including subgraphs."""
    if not filenames:
        return
    weights = {item['filename']: item['weight'] for item in catalog()}
    missing = set(filenames) - weights.keys()
    if missing:
        raise ValueError('Unknown LoRAs: ' + ', '.join(sorted(missing)))

    def apply(graph):
        nodes = graph.get('nodes', [])
        links = graph.get('links', [])
        # Subgraph links use objects rather than the top-level array format.
        object_links = bool(links and isinstance(links[0], dict))
        def parts(link):
            if isinstance(link, dict):
                return [link['id'], link['origin_id'], link['origin_slot'], link['target_id'], link['target_slot'], link['type']]
            return link
        def make_link(values):
            if object_links:
                return dict(zip(('id', 'origin_id', 'origin_slot', 'target_id', 'target_slot', 'type'), values))
            return values
        node_id = max([int(n['id']) for n in nodes] + [0])
        link_id = max([int(parts(link)[0]) for link in links] + [0])
        for source in list(nodes):
            if source.get('type') not in ('UNETLoader', 'CheckpointLoaderSimple', 'CheckpointLoader'):
                continue
            for slot, output in enumerate(source.get('outputs', [])):
                if output.get('type') != 'MODEL':
                    continue
                outgoing = [link for link in links if parts(link)[1:3] == [source['id'], slot]]
                if not outgoing:
                    continue
                previous, previous_slot = source, slot
                for filename in filenames:
                    node_id += 1
                    link_id += 1
                    previous['outputs'][previous_slot]['links'] = [link_id]
                    links.append(make_link([link_id, previous['id'], previous_slot, node_id, 0, 'MODEL']))
                    node = {'id': node_id, 'type': 'LoraLoaderModelOnly', 'pos': [0, 0], 'size': [315, 82],
                            'flags': {}, 'order': source.get('order', 0) + 1, 'mode': 0,
                            'inputs': [{'name': 'model', 'type': 'MODEL', 'link': link_id}],
                            'outputs': [{'name': 'MODEL', 'type': 'MODEL', 'links': []}],
                            'properties': {'Node name for S&R': 'LoraLoaderModelOnly'},
                            'widgets_values': [filename, weights[filename]],
                            'widgets_values_named': {'lora_name': filename, 'strength_model': weights[filename]}}
                    nodes.append(node)
                    previous, previous_slot = node, 0
                previous['outputs'][0]['links'] = [parts(link)[0] for link in outgoing]
                for link in outgoing:
                    if isinstance(link, dict):
                        link['origin_id'], link['origin_slot'] = previous['id'], 0
                    else:
                        link[1:3] = [previous['id'], 0]
        graph['last_node_id'], graph['last_link_id'] = node_id, link_id
        if 'state' in graph:
            graph['state'].update(lastNodeId=node_id, lastLinkId=link_id)
    apply(workflow)
    for graph in workflow.get('definitions', {}).get('subgraphs', []):
        apply(graph)
