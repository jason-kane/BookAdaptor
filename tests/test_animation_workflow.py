import importlib.util
from pathlib import Path
import unittest


spec = importlib.util.spec_from_file_location(
    "animation_workflow",
    Path(__file__).parents[1] / "src/artifact_editor/textoviva/workflow.py",
)
workflow = importlib.util.module_from_spec(spec)
spec.loader.exec_module(workflow)


class AnimationWorkflowTests(unittest.TestCase):
    def test_minimax_duration_rounds_up_to_cover_audio(self):
        import json
        import math

        template = Path(__file__).parents[1] / 'templates/ui.i2v.minimax_h3-MiniMax H3.json'
        expressions = []
        def visit(value):
            if isinstance(value, dict):
                if value.get('type') == 'ComfyMathExpression':
                    expressions.append(value['widgets_values'][0])
                for item in value.values():
                    visit(item)
            elif isinstance(value, list):
                for item in value:
                    visit(item)
        visit(json.loads(template.read_text()))
        self.assertTrue(expressions)
        for frames in range(1, 1001):
            count = eval(expressions[0], {'__builtins__': {}, 'max': max, 'ceil': math.ceil},
                         {'a': frames / 25})
            self.assertGreaterEqual(count / 24, frames / 25)
            self.assertEqual(count % 17, 5)

    def test_workflow_names_preserve_dots_and_remove_file_prefixes(self):
        cases = {
            "ui.i2v.minimax_h3-MiniMax H3.json": "minimax_h3-MiniMax H3",
            "/templates/ui.i2v.ltx_2.3-LTX 2.3.json": "ltx_2.3-LTX 2.3",
            "api.i2v.ltx_2.3.json": "ltx_2.3",
            "i2v.minimax_h3": "minimax_h3",
            "i2v.ltx_2.3": "ltx_2.3",
            "ltx_2.3": "ltx_2.3",
        }
        for template, expected in cases.items():
            with self.subTest(template=template):
                self.assertEqual(workflow.animation_workflow_name(template), expected)

    def test_repository_templates_match_loader_glob(self):
        templates = Path(__file__).parents[1] / "templates"
        for template in templates.glob("ui.i2v.*.json"):
            name = workflow.animation_workflow_name(str(template))
            self.assertIn(template, templates.glob(f"ui.i2v.{name}*.json"))

    def test_default_animation_request_covers_full_audio_span(self):
        import ast

        source = Path(__file__).parents[1] / 'src/artifact_editor/textoviva/textoviva.py'
        tree = ast.parse(source.read_text())
        assignment = next(node for node in ast.walk(tree) if isinstance(node, ast.Assign)
                          and any(isinstance(target, ast.Name) and target.id == 'default_animation_frames'
                                  for target in node.targets))
        for frames in [0, 1, 25, 96, 100, 151]:
            scope = {'audio_frames': frames}
            exec(compile(ast.Module(body=[assignment], type_ignores=[]), str(source), 'exec'), scope)
            self.assertEqual(scope['default_animation_frames'], max(1, frames))

    def test_missing_animation_selection_retains_sufficient_clips(self):
        import ast
        import os
        from types import SimpleNamespace
        from bs4 import BeautifulSoup

        source = Path(__file__).parents[1] / 'src/artifact_editor/textoviva/textoviva.py'
        tree = ast.parse(source.read_text())
        selection = next(node for node in ast.walk(tree) if isinstance(node, ast.If)
                         and isinstance(node.test, ast.Name) and node.test.id == 'regenerate_insufficient')
        selection.body = [node for node in selection.body if not isinstance(node, ast.ImportFrom)]
        loop = ast.For(target=ast.Name(id='image_xml', ctx=ast.Store()),
                       iter=ast.Name(id='items', ctx=ast.Load()),
                       body=[selection, ast.parse('selected.append(image_xml["index"])').body[0]], orelse=[])
        soup = BeautifulSoup('<paragraph index="0">' + ''.join(
            f'<image index="{index}" frames="100" animation_oom_success_frames="90"/>'
            for index in range(5)) + '</paragraph>', 'xml')
        counts = [0, 94, 100, 110, None]
        scope = dict(items=soup.find_all('image'), regenerate_insufficient=True, selected=[],
                     os=os, const=SimpleNamespace(FPS=25, LIBRARY_DIR='/library'),
                     self=SimpleNamespace(chapter=SimpleNamespace(get_paragraph_dir=lambda index: 'paragraph')),
                     animation_frame_count=lambda image, *args: counts[int(image['index'])])
        exec(compile(ast.fix_missing_locations(ast.Module(body=[loop], type_ignores=[])), str(source), 'exec'), scope)
        self.assertEqual(scope['selected'], ['0', '1', '4'])
        for index in [0, 1, 4]:
            self.assertNotIn('animation_oom_success_frames', scope['items'][index].attrs)
        self.assertEqual(scope['items'][2]['animation_oom_success_frames'], '90')
