"""ComfyUI SCENE must retain source truth alongside generated metadata."""
import ast
from pathlib import Path
from types import SimpleNamespace
from typing import Any
import unittest

from bs4 import BeautifulSoup, Tag


class SceneSourceTests(unittest.TestCase):
    def test_scene_carries_source_and_constraints_without_rewriting_saved_description(self):
        root = Path(__file__).parents[2]
        tree = ast.parse((root / 'tools.py').read_text())
        names = {'get_text_to_next', 'get_image_narration_context'}
        functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
        namespace = {'log': SimpleNamespace(debug=lambda *args: None), 'PUNCTUATION': ['.', ',']}
        exec(compile(ast.Module(body=functions, type_ignores=[]), 'tools.py', 'exec'), namespace)
        tree = ast.parse((root / 'chapter/chapter.py').read_text())
        chapter = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'Chapter')
        method = next(node for node in chapter.body if isinstance(node, ast.FunctionDef) and node.name == 'get_scene')
        scope = {'tools': SimpleNamespace(**{name: namespace[name] for name in names}),
                 'as_str': str, 'Tag': Tag, 'Any': Any}
        exec(compile(ast.Module(body=[method], type_ignores=[]), 'chapter.py', 'exec'), scope)
        text = 'A PIGEON who was very thirsty saw a goblet of water painted on a signboard.'
        soup = BeautifulSoup(f'<chapter><paragraph><image index="0" description="Diving toward a painted goblet"/><phrase>{text}</phrase><image index="1"/><phrase>Later it crashes.</phrase></paragraph></chapter>', 'xml')
        scene = scope['get_scene'](None, soup.image)
        self.assertEqual(scene['source_passage'], text)
        self.assertNotIn('crashes', scene['visual_constraints'])
        self.assertIn('two-dimensional picture', scene['visual_constraints'])
        self.assertIn('override conflicting description', scene['interpretation_priority'])
        self.assertEqual(soup.image['description'], 'Diving toward a painted goblet')


if __name__ == '__main__':
    unittest.main()
