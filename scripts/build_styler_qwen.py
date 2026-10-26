"""Compose the Qwen sampler source and export its UI template.

Run with --object-info pointing to ComfyUI's /object_info response.
"""
import argparse
import json
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def export_ui(graph, schemas):
    graph = {key: node for key, node in graph.items() if "class_type" in node}
    nodes, links = [], []
    by_id = {}
    for index, (node_id, source) in enumerate(graph.items()):
        schema = schemas[source['class_type']]
        node = dict(id=int(node_id), type=source['class_type'],
                    title=source.get('_meta', {}).get('title', source['class_type']),
                    pos=[(index % 4) * 360, (index // 4) * 340], size=[320, 260],
                    flags={}, order=index, mode=0, inputs=[], outputs=[],
                    properties={'Node name for S&R': source['class_type']},
                    widgets_values=[], widgets_values_named={})
        definitions = {**schema['input'].get('required', {}), **schema['input'].get('optional', {})}
        for name in definitions:
            if name not in source['inputs']:
                continue
            value = source['inputs'][name]
            definition = definitions.get(name, ['*'])
            kind = definition[0]
            options = definition[1] if len(definition) > 1 else {}
            widget = isinstance(kind, list) or kind in ('INT', 'FLOAT', 'STRING', 'BOOLEAN')
            if isinstance(value, list) and len(value) == 2 and str(value[0]) in graph:
                source_id, port = int(value[0]), value[1]
                link_id = len(links) + 1
                socket = dict(name=name, type='COMBO' if isinstance(kind, list) else kind, link=link_id)
                if widget:
                    socket['widget'] = {'name': name}
                links.append([link_id, source_id, port, int(node_id), len(node['inputs']), socket['type']])
                node['inputs'].append(socket)
                value = options.get('default', kind[0] if isinstance(kind, list) else '')
            if widget:
                node['widgets_values'].append(value)
                node['widgets_values_named'][name] = value
                if options.get('control_after_generate'):
                    node['widgets_values'].append('fixed')
        for port, kind in enumerate(schema.get('output', [])):
            names = schema.get('output_name', schema['output'])
            node['outputs'].append(dict(name=names[port], type=kind, links=[]))
        nodes.append(node)
        by_id[node['id']] = node
    for link_id, source_id, port, *_ in links:
        by_id[source_id]['outputs'][port]['links'].append(link_id)
    return dict(last_node_id=max(by_id), last_link_id=len(links), nodes=nodes,
                links=links, groups=[], config={}, extra={}, version=0.4)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--object-info', type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as directory:
        compiled = Path(directory) / 'qwen-sampler.compiled.json'
        subprocess.run(['comfy', 'workflow', 'compose',
                        str(ROOT / 'workflows/styler/blueprints/qwen-sampler.yaml'),
                        '--lib', str(ROOT / 'workflows/styler/fragments'),
                        '--out', str(compiled)], check=True)
        ui = export_ui(json.loads(compiled.read_text()), json.loads(args.object_info.read_text()))
    target = ROOT / 'templates/ui.t2i.qwen_sampler-Qwen Image 2.1 Sampler.json'
    target.write_text(json.dumps(ui, indent=2) + '\n')
    print(target)


if __name__ == '__main__':
    main()
