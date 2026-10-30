"""Chapter metadata must request constrained JSON without a ComfyUI round trip."""
import ast
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


class JsonPromptTests(unittest.TestCase):
    def setUp(self):
        source = Path(__file__).resolve().parents[1] / 'src/artifact_editor/llm.py'
        function = next(node for node in ast.parse(source.read_text()).body
                        if isinstance(node, ast.FunctionDef) and node.name == 'json_prompt')
        self.cache_get = Mock(return_value=None)
        self.cache_set = Mock()
        self.comfy = Mock()
        namespace = dict(json=json, os=os, get_cached_prompt=self.cache_get,
                         set_cached_prompt=self.cache_set, comfy=self.comfy, log=Mock())
        exec(compile(ast.Module(body=[function], type_ignores=[]), str(source), 'exec'), namespace)
        self.json_prompt = namespace['json_prompt']
        self.completion = Mock()
        self.modules = patch.dict(sys.modules, {
            'gpu_tasks': SimpleNamespace(gpu_label=SimpleNamespace(get=lambda: 'Scene characters')),
            'llama_inference': SimpleNamespace(chat_completion=self.completion),
        })
        self.modules.start()
        self.addCleanup(self.modules.stop)
        self.environment = patch.dict(os.environ, RENDER_WORKER_PROCESS='0', VIDEO_WORKER_PROCESS='0')
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_metadata_accepts_json_arrays_and_objects(self):
        for result in [['Jack', 'Tip'], {'characters': ['Jack']}, []]:
            with self.subTest(result=result):
                self.completion.return_value = json.dumps(result)
                self.assertEqual(self.json_prompt('Which characters?'), result)
                options = self.completion.call_args.kwargs
                self.assertEqual(options['response_format'], {
                    'type': 'json_object',
                    'schema': {'anyOf': [{'type': 'object'}, {'type': 'array'}]},
                })
                self.cache_set.assert_called_with('Which characters?', result, format='json')
        self.comfy.load_workflow_template.assert_not_called()

    def test_cached_empty_character_list_is_a_cache_hit(self):
        self.cache_get.return_value = []
        self.assertEqual(self.json_prompt('Which characters?', force=False), [])
        self.completion.assert_not_called()
        self.comfy.load_workflow_template.assert_not_called()

    def test_malformed_response_is_not_cached(self):
        self.completion.return_value = '[["Narrator"], "Jack", "Tip", "Pumpkinhead"]]'
        with self.assertRaises(json.JSONDecodeError):
            self.json_prompt('Which characters?', force=True)
        self.cache_get.assert_called_with('Which characters?', format='json', force=True)
        self.cache_set.assert_not_called()


if __name__ == '__main__':
    unittest.main()
