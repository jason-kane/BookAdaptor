"""Repeated LLM requests must rerun the text saver after its output is consumed."""
import ast
import glob
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import uuid

ROOT = Path(__file__).parents[1]


def load_function(path, name, namespace):
    node = next(n for n in ast.parse(path.read_text()).body if isinstance(n, ast.FunctionDef) and n.name == name)
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), namespace)
    return namespace[name]


class LLMOutputTests(unittest.TestCase):
    def test_repeated_requests_create_distinct_workflows_and_output_files(self):
        with TemporaryDirectory() as directory:
            dirs = {'WORKFLOW_TEMPLATES_DIR': str(ROOT / 'templates'), 'WORKFLOWS_DIR': directory, 'OUTPUT_DIR': directory}
            const = SimpleNamespace(COMFY_DIRS={'artifactserver': dirs, 'comfyui': dirs})
            builder = load_function(ROOT / 'src/artifact_editor/chapter/chapter.py', 'get_prompt_workflow_by_filename',
                                    dict(os=os, json=json, uuid=uuid, const=const))
            executed_prefixes, workflow_files, seeds = [], [], []
            def run(filename):
                workflow_files.append(filename)
                data = json.loads((Path(directory) / filename).read_text())
                generator = next(n for n in data['nodes'] if n['type'] == 'TextGenerate')
                seed = generator['widgets_values_named']['sampling_mode.seed']
                names = [item['widget']['name'] for item in generator['inputs'] if 'widget' in item]
                self.assertEqual(generator['widgets_values'][names.index('sampling_mode.seed')], seed)
                seeds.append(seed)
                prefix = next(n for n in data['nodes'] if n.get('title') == 'Output Filename')['widgets_values'][0]
                # Simulate ComfyUI caching identical SaveText inputs.
                if prefix in executed_prefixes:
                    return
                executed_prefixes.append(prefix)
                (Path(directory) / (prefix + '_00001.txt')).write_text('Empty stone hall')
            namespace = dict(uuid=uuid, get_cached_prompt=Mock(return_value=None), set_cached_prompt=Mock(),
                             comfy=SimpleNamespace(run_workflow_fn=run), const=const,
                             os=os, glob=glob, Path=Path, log=Mock())
            prompt = load_function(ROOT / 'src/artifact_editor/llm.py', 'str_prompt', namespace)
            with patch.dict(sys.modules, {'artifact_editor.chapter.chapter': SimpleNamespace(get_prompt_workflow_by_filename=builder)}):
                for _ in range(2):
                    self.assertEqual(prompt('Describe a hall', system_prompt='Exclude props', force=True), 'Empty stone hall')
            self.assertEqual(len(set(workflow_files)), 2)
            self.assertEqual(len(set(executed_prefixes)), 2)
            self.assertEqual(len(set(seeds)), 2)
            self.assertFalse(list(Path(directory).glob('llm_str*')))

    def test_unforced_workflow_preserves_template_seed(self):
        with TemporaryDirectory() as directory:
            dirs = {'WORKFLOW_TEMPLATES_DIR': str(ROOT / 'templates'), 'WORKFLOWS_DIR': directory, 'OUTPUT_DIR': directory}
            builder = load_function(ROOT / 'src/artifact_editor/chapter/chapter.py', 'get_prompt_workflow_by_filename',
                                    dict(os=os, json=json, uuid=uuid, const=SimpleNamespace(COMFY_DIRS={'artifactserver': dirs, 'comfyui': dirs})))
            filename = builder('chat.qwen35-json.Qwen 3.5 4b bf16.json', 'System', 'Prompt', 'output')
            workflow = json.loads((Path(directory) / filename).read_text())
            generator = next(n for n in workflow['nodes'] if n['type'] == 'TextGenerate')
            self.assertEqual(generator['widgets_values_named']['sampling_mode.seed'], 0)


if __name__ == '__main__':
    unittest.main()
