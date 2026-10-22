"""Accept common structured pose responses and reject unsupported choices."""
import ast
import logging
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
from bs4 import BeautifulSoup


class PoseResponseTests(unittest.TestCase):
    def test_pose_response_shapes(self):
        path = Path(__file__).parents[1] / 'images.py'
        node = next(node for node in ast.parse(path.read_text()).body
                    if isinstance(node, ast.FunctionDef) and node.name == 'generate_metadata_character_pose')
        prompt = Mock()
        scope = {'llm': SimpleNamespace(json_prompt=prompt), 'log': logging.getLogger(__name__),
                 'scene_const': SimpleNamespace(POSE_CHOICES=[('flying', 'Flying'), ('lying_down', 'Lying down')]),
                 'get_image_narration_context': lambda image: 'Context',
                 'get_text_to_next': lambda **kwargs: 'A pigeon sees water.'}
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), scope)
        for result, expected in [
            ({'pose': 'Flying'}, 'flying'),
            ({'character_pose': 'flying', 'narrative_context': 'Unused', 'pose_selection': 'flying'}, 'flying'),
            ({'pose_selection': 'lying-down'}, 'lying_down'), ('flying', 'flying'),
            ({'pose': None, 'character_pose': 'flying'}, 'flying'),
            ({'pose': 'teleporting'}, None), ({'pose': []}, None), (None, None),
        ]:
            with self.subTest(result=result):
                image = BeautifulSoup('<image/>', 'xml').image
                prompt.return_value = result
                call = lambda: scope['generate_metadata_character_pose']({'name': 'Pigeon', 'tag': 'Pigeon'}, image, None)
                if expected:
                    call()
                    self.assertEqual(image['Pigeon_pose'], expected)
                else:
                    with self.assertRaises(ValueError):
                        call()
                    self.assertNotIn('Pigeon_pose', image.attrs)


if __name__ == '__main__':
    unittest.main()
