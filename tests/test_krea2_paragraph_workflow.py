"""Krea paragraph generation preserves book inputs and the existing Krea backend."""
import ast
import json
import os
from pathlib import Path
import shutil
from types import SimpleNamespace
from unittest.mock import Mock

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / 'templates/ui.t2i.krea2-paragraph_Krea 2 Paragraph Image.json'
ZIT = ROOT / 'templates/ui.t2i.zit-paragraph_Z-Image Turbo Paragraph Image.json'
KREA = ROOT / 'templates/ui.t2i.krea2_sampler-Krea 2 Sampler.json'


def test_paragraph_inputs_and_prompt_pipeline():
    workflow = json.loads(TEMPLATE.read_text())
    reference = json.loads(ZIT.read_text())
    input_types = {'PrimitiveString', 'PrimitiveStringMultiline', 'PrimitiveInt', 'PrimitiveFloat', 'LoadImage', 'CustomStyler'}
    def interface(data):
        return {(n.get('title'), n['type']) for n in data['nodes'] if n['type'] in input_types}
    assert interface(workflow) == interface(reference)
    nodes = {n['id']: n for n in workflow['nodes']}
    links = {link[0]: link for link in workflow['links']}
    def source(node_id, name):
        port = next(p for p in nodes[node_id]['inputs'] if p['name'] == name)
        return links[port['link']][1]
    assert source(144, 'string') == 134  # Chapter metadata
    assert source(147, 'string') == 162  # Scene metadata
    assert source(146, 'string') == 163  # Focus paragraph
    assert source(145, 'string') == 155  # Prior result
    assert source(143, 'chain') == 166   # System prompt
    assert source(149, 'prompt1') == 142
    assert 'Krea 2 Turbo' in nodes[142]['widgets_values_named']['string']
    assert nodes[142]['widgets_values'][0] == nodes[142]['widgets_values_named']['string']
    assert source(165, 'text_positive') == 157  # Apply book style after refinement
    assert source(160, 'text') == 165
    assert source(160, 'width') == 125
    assert source(160, 'height') == 124
    assert source(164, 'filename_prefix') == 130
    assert source(150, 'output_file_path') == 148
    assert source(156, 'file_name') == 158


def test_generation_core_matches_existing_krea_sampler_and_has_valid_links():
    workflow = json.loads(TEMPLATE.read_text())
    graph = workflow['definitions']['subgraphs'][0]
    reference = json.loads(KREA.read_text())['definitions']['subgraphs'][0]
    for kind in ('UNETLoader', 'CLIPLoader', 'VAELoader', 'EmptyLatentImage', 'KSampler'):
        actual = next(n for n in graph['nodes'] if n['type'] == kind)
        expected = next(n for n in reference['nodes'] if n['type'] == kind)
        assert actual['widgets_values_named'] == expected['widgets_values_named']
    assert not any(n['type'] in ('ModelSamplingAuraFlow', 'LoraLoaderModelOnly') for n in graph['nodes'])
    for data in (workflow, graph):
        nodes = {n['id']: n for n in data['nodes']}
        assert len(nodes) == len(data['nodes'])
        ids = set()
        for raw in data['links']:
            if isinstance(raw, dict):
                identity, origin, out_slot, target, in_slot = (raw[k] for k in ('id', 'origin_id', 'origin_slot', 'target_id', 'target_slot'))
            else:
                identity, origin, out_slot, target, in_slot, _ = raw
            assert identity not in ids
            ids.add(identity)
            if origin != -10:
                assert identity in nodes[origin]['outputs'][out_slot]['links']
            if target != -20:
                assert nodes[target]['inputs'][in_slot]['link'] == identity


def test_application_populates_krea_paragraph_inputs(tmp_path):
    path = ROOT / 'src/artifact_editor/chapter/chapter.py'
    method = next(n for n in ast.walk(ast.parse(path.read_text()))
                  if isinstance(n, ast.FunctionDef) and n.name == 'get_workflow_by_filename')
    dirs = dict(INPUT_DIR=str(tmp_path), OUTPUT_DIR=str(tmp_path), WORKFLOWS_DIR=str(tmp_path))
    namespace = dict(json=json, os=os, shutil=shutil, Tag=object, as_int=int, log=Mock(),
                     styles=SimpleNamespace(Style=lambda _: SimpleNamespace(name='Ink')),
                     const=SimpleNamespace(FPS=25, IMG_TARGET_WIDTH=1280, IMG_TARGET_HEIGHT=720,
                                           COMFY_DIRS={'artifactserver': dirs, 'comfyui': dirs}))
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(path), 'exec'), namespace)
    chapter = SimpleNamespace(nice='test', get_image_frames=lambda _: 25,
        get_image_filename=lambda _: None, get_image=lambda _: None,
        get_meta_dict=lambda: {'title': 'A Book', 'author': 'Writer'},
        get_scene=lambda _: {'setting': 'A stone hall'},
        get_prompt=lambda _: 'A traveler opens a door.', get_image_style=lambda _: 'ink',
        get_paragraph_snippet=Mock(return_value='#FOCUS_PARAGRAPH\nA traveler opens a door.'))
    soup = BeautifulSoup('<paragraph><image index="4"/><phrase index="10">A traveler opens a door.</phrase><image index="5"/></paragraph>', 'xml')
    filename = namespace['get_workflow_by_filename'](chapter, soup.image, str(TEMPLATE),
        template_environment={'WIDTH': 768, 'HEIGHT': 512, 'SEED': 1234, 'FILENAME_PREFIX': 'test_krea'},
        force_name='test.json')
    result = json.loads((tmp_path / filename).read_text())
    values = {n.get('title'): n.get('widgets_values_named', {}) for n in result['nodes']}
    assert values['WIDTH']['value'] == 768
    assert values['HEIGHT']['value'] == 512
    assert values['META']['value'].find('A Book') >= 0
    assert 'A stone hall' in values['SCENE']['value']
    assert '#FOCUS_PARAGRAPH' in values['SOURCE']['value']
    assert values['STYLE']['Custom'] == 'Ink'
    assert values['FILENAME_PREFIX']['value'] == 'test_krea'
    assert values['Krea 2 Turbo Image']['seed'] == 1234
    assert values['Krea 2 Turbo Image']['unet_name'] == 'krea2_turbo_fp8_scaled.safetensors'
