"""Workflow link regression checks without generation services."""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
from bs4 import BeautifulSoup
from flask import Flask, redirect, request


class WorkflowOpenTests(unittest.TestCase):
    def test_query_template_resolves_like_generate_image(self):
        path = Path(__file__).parents[1] / 'views.py'
        tree = ast.parse(path.read_text())
        node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'comfyui_workflow_open')
        node.decorator_list = []
        app = Flask(__name__)
        for template in ['t2i.zit-paragraph', 'zit-paragraph', 'ui.t2i.zit-paragraph']:
            with self.subTest(template=template):
                image = BeautifulSoup('<image index="8"/>', 'xml').image
                workflow = Mock(side_effect=lambda image, **kw: 'resolved.json' if kw['workflow_name'] == 'zit-paragraph' else None)
                chapter = SimpleNamespace(get_image=lambda index: image, get_workflow=workflow, save_xml=Mock(), nice='book')
                ns = dict(g=SimpleNamespace(chapter=chapter), request=request, redirect=redirect,
                          log=Mock(), const=SimpleNamespace(COMFYUI_UI_URL='http://comfy.local/'),
                          images=SimpleNamespace(generate_metadata_from_text=Mock()))
                exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), ns)
                with app.test_request_context('/', query_string={'workflow_image_template': template}):
                    response = ns['comfyui_workflow_open'](8, '*', 0, 'false')
                self.assertEqual(response.status_code, 302)
                self.assertEqual(response.location, 'http://comfy.local/?workflow=resolved.json')
                self.assertEqual(image['workflow_image_template'], 'zit-paragraph')
                self.assertIsNone(workflow.call_args.kwargs['video_index'])
                chapter.save_xml.assert_called_once()


if __name__ == '__main__':
    unittest.main()
