import json
import sys
import pytest
from types import SimpleNamespace
from bs4 import BeautifulSoup
from artifact_editor import loras


def test_defaults_snapshot_and_explicit_empty(monkeypatch):
    config = SimpleNamespace(get_config=lambda path: {'default_loras': ['a.safetensors', 'b.safetensors']})
    monkeypatch.setitem(sys.modules, 'artifact_editor.config', config)
    chapter = SimpleNamespace(bookdir='book')
    image = BeautifulSoup('<image/>', 'xml').image
    assert loras.image_selection(chapter, image) == ['a.safetensors', 'b.safetensors']
    monkeypatch.setattr(config, 'get_config', lambda path: {'default_loras': ['c.safetensors']})
    assert loras.image_selection(chapter, image) == ['a.safetensors', 'b.safetensors']
    image['loras'] = '[]'
    assert loras.image_selection(chapter, image) == []


def test_workflow_chains_multiple_loras_in_subgraph(monkeypatch):
    monkeypatch.setattr(loras, 'catalog', lambda: [{'filename': 'a', 'weight': .8}, {'filename': 'b', 'weight': 1}])
    graph = {'nodes': [{'id': 1, 'type': 'UNETLoader', 'outputs': [{'type': 'MODEL', 'links': [3]}]},
                       {'id': 2, 'type': 'KSampler', 'inputs': [{'name': 'model', 'link': 3}]}],
             'links': [{'id': 3, 'origin_id': 1, 'origin_slot': 0, 'target_id': 2, 'target_slot': 0, 'type': 'MODEL'}]}
    workflow = {'nodes': [], 'links': [], 'definitions': {'subgraphs': [graph]}}
    loras.apply_to_workflow(workflow, ['a', 'b'])
    loaders = [node for node in graph['nodes'] if node['type'] == 'LoraLoaderModelOnly']
    assert [node['widgets_values'] for node in loaders] == [['a', .8], ['b', 1]]
    assert graph['links'][0]['origin_id'] == loaders[-1]['id']
    assert loaders[-1]['inputs'][0]['link'] == graph['links'][-1]['id']
    assert graph['nodes'][0]['outputs'][0]['links'] == [4]


def test_catalog_filters_other_models(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, 'const', SimpleNamespace(LORA_DIR=str(tmp_path)))
    monkeypatch.setenv('CHAPTER_STORAGE', 'xml')
    (tmp_path / 'a.safetensors').touch()
    (tmp_path / 'b.safetensors').touch()
    (tmp_path / 'b.safetensors.json').write_text(json.dumps({'model': 'flux'}))
    assert [item['filename'] for item in loras.catalog()] == ['a.safetensors']


def test_real_zit_template_accepts_multiple_loras(monkeypatch):
    from pathlib import Path
    monkeypatch.setattr(loras, 'catalog', lambda: [{'filename': 'a', 'weight': 1}, {'filename': 'b', 'weight': .5}])
    template = Path(__file__).resolve().parents[1] / 'templates/ui.t2i.zit-paragraph_Z-Image Turbo Paragraph Image.json'
    workflow = json.loads(template.read_text())
    loras.apply_to_workflow(workflow, ['a', 'b'])
    graph = workflow['definitions']['subgraphs'][0]
    loaders = [n for n in graph['nodes'] if n['type'] == 'LoraLoaderModelOnly']
    assert len(loaders) == 2
    assert graph['state']['lastNodeId'] >= max(n['id'] for n in graph['nodes'])
    assert len({link['id'] for link in graph['links']}) == len(graph['links'])
