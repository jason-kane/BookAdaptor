"""Worker image generation selects its result and persists it."""
import ast
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from bs4 import BeautifulSoup
from flask_caching import Cache


class ImageJobTests(unittest.TestCase):
    def test_generation_persists_output_and_missing_slots_fail(self):
        path = Path(__file__).parents[1] / 'jobs.py'
        tree = ast.parse(path.read_text())
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef))
        runner = Mock()
        cache = Cache()  # Deliberately uninitialized: workers have no Flask application.
        namespace = {'os': os, 'comfy': SimpleNamespace(run_workflow_fn=runner),
                     'const': SimpleNamespace(DEFAULT_IMAGE_TEMPLATE='default')}
        exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), 'exec'), namespace)
        image = BeautifulSoup('<image index="4" workflow_image_template="t2i.test"/>', 'xml').image
        chapter = SimpleNamespace(get_image=Mock(return_value=image), get_workflow=Mock(return_value='workflow.json'), save_xml=Mock())
        with patch.dict(sys.modules, {
            'artifact_editor.cache': SimpleNamespace(cache=cache),
            'artifact_editor.images.selector.htmx': SimpleNamespace(gather_candidate_images=lambda image, chapter: ['/chapter/new.png']),
        }):
            namespace['generate_timeline_image'](chapter, 4)
            runner.assert_called_once_with('workflow.json')
            self.assertEqual(image['src'], 'new.png')
            chapter.save_xml.assert_called_once()
            chapter.get_image.return_value = None
            with self.assertRaisesRegex(ValueError, 'no longer exists'):
                namespace['generate_timeline_image'](chapter, 4)


if __name__ == '__main__':
    unittest.main()
