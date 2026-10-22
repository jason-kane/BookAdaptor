import importlib.util
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('reference_images', Path(__file__).parents[1] / 'src/artifact_editor/reference_images.py')
reference = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reference)


class ReferenceTests(unittest.TestCase):
    def test_export_uses_generation_template_and_substitutes_current_description(self):
        def template(*args):
            return SimpleNamespace(serialize=lambda: {
                'latent': {'class_type': 'EmptySD3LatentImage', 'inputs': {}},
                'sample': {'class_type': 'KSampler', 'inputs': {}},
                'text': {'class_type': 'Text', 'inputs': {'text': '{{PROMPT}}'}},
                'save': {'class_type': 'SaveImage', 'inputs': {'filename_prefix': '{{FILENAME_PREFIX}}'}},
            })
        with TemporaryDirectory() as root, patch.dict(sys.modules, {
            'comfy': SimpleNamespace(load_workflow_template=template),
            'const': SimpleNamespace(COMFY_DIRS={'artifactserver': {'WORKFLOWS_DIR': root}}, COMFYUI_UI_URL='http://localhost:8188/'),
        }):
            for kind, size in [('props', (1024,1024)), ('characters', (768,1152))]:
                url = reference.open_reference_workflow(kind, 'Current edited description')
                filename = url.split('workflow=')[1]
                data = json.loads((Path(root) / filename).read_text())
                self.assertIn('Current edited description', data['text']['inputs']['text'])
                self.assertIn('strong contrast', data['text']['inputs']['text'])
                self.assertEqual(data['latent']['inputs'], dict(width=size[0], height=size[1]))
                self.assertTrue(data['save']['inputs']['filename_prefix'].startswith(kind + '/'))
            with self.assertRaises(ValueError):
                reference.open_reference_workflow('props', ' ')


if __name__ == '__main__':
    unittest.main()
