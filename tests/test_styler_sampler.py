import ast
import json
import os
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock
from bs4 import BeautifulSoup

import unittest


class StylerSamplerTests(unittest.TestCase):
    def test_qwen_sampler_uses_direct_prompt_and_qwen_models(self):
        root = Path(__file__).resolve().parents[1]
        workflow = json.loads((root / 'templates/ui.t2i.qwen_sampler-Qwen Image 2.1 Sampler.json').read_text())
        nodes = workflow['nodes']
        self.assertFalse(any(n['type'] == 'TextGenerate' for n in nodes))
        model = next(n for n in nodes if n['type'] == 'UNETLoader')
        self.assertEqual(model['widgets_values_named']['unet_name'], 'qwen_image_2.1_int8_convrot.safetensors')
        encoder = next(n for n in nodes if n['type'] == 'TextEncodeQwenImage21')
        prompt = next(n for n in nodes if n.get('title') == 'PROMPT')
        prompt_input = next(i for i in encoder['inputs'] if i['name'] == 'prompt')
        link = next(link for link in workflow['links'] if link[0] == prompt_input['link'])
        self.assertEqual(link[1], prompt['id'])

    def test_global_samplers_export_without_paragraph_image(self):
        root = Path(__file__).resolve().parents[1]
        path = root / 'src/artifact_editor/chapter/chapter.py'
        method = next(n for n in ast.walk(ast.parse(path.read_text())) if isinstance(n, ast.FunctionDef) and n.name == 'get_workflow_by_filename')
        with TemporaryDirectory() as tmp:
            dirs = dict(INPUT_DIR=tmp, OUTPUT_DIR=tmp, WORKFLOWS_DIR=tmp)
            ns = dict(json=json, os=os, shutil=shutil, Tag=object, as_int=int,
                      const=SimpleNamespace(FPS=25, COMFY_DIRS={'artifactserver': dirs, 'comfyui': dirs}), log=Mock())
            exec(compile(ast.Module(body=[method], type_ignores=[]), str(path), 'exec'), ns)
            chapter = SimpleNamespace(nice='sampler', get_image_frames=lambda _: 1, get_image=Mock(),
                                      get_meta_dict=Mock(side_effect=AssertionError('Metadata lookup for global style')),
                                      get_scene=Mock(side_effect=AssertionError('Scene lookup for global style')),
                                      get_image_style=Mock(side_effect=AssertionError('Chapter style lookup for global style')),
                                      get_image_filename=Mock(side_effect=AssertionError('Image lookup for global style')),
                                      get_prompt=Mock(side_effect=AssertionError('Prompt lookup despite override')))
            for template in (root / 'templates').glob('ui.t2i.*sampler*.json'):
                name = ns['get_workflow_by_filename'](chapter, BeautifulSoup('<chapter/>', 'xml').chapter,
                        str(template), template_environment={'HEIGHT':512, 'WIDTH':512, 'PROMPT':'sample style', 'FILENAME_PREFIX':'samples/test'}, force_name='sample.json')
                data = json.loads((Path(tmp) / name).read_text())
                prompts = [n for n in data['nodes'] if n.get('title') == 'PROMPT']
                assert prompts and all(n['widgets_values'] == ['sample style'] for n in prompts)
                for node in data['nodes']:
                    if node.get('title') in ['SCENE', 'META']:
                        self.assertEqual(json.loads(node['widgets_values'][0]), {})
            chapter.get_image_filename.assert_not_called()
            chapter.get_prompt.assert_not_called()
            chapter.get_scene.assert_not_called()
            chapter.get_meta_dict.assert_not_called()
            chapter.get_image_style.assert_not_called()
