"""Visual generation must not use future paragraphs as surrounding context."""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from bs4 import BeautifulSoup, Tag


def load_function(path, name, namespace):
    tree = ast.parse(path.read_text())
    node = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name)
    node.decorator_list = []
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), namespace)
    return namespace[name]


class NarrationContextTests(unittest.TestCase):
    def test_title_context_excludes_later_plot_and_prompt_has_boundary(self):
        root = Path(__file__).parents[2]
        context = load_function(root / 'tools.py', 'get_image_narration_context', {})
        soup = BeautifulSoup('''<chapter><paragraph><image index="0" frames="25"/>
            <phrase>The Journey</phrase></paragraph><paragraph><image index="1"/>
            <phrase>The dragon wins the final battle.</phrase></paragraph></chapter>''', 'xml')
        image = soup.image
        self.assertNotIn('dragon', context(image))
        self.assertIn('The Journey', context(image))
        self.assertIn('The Journey', context(soup.find('image', index='1')))
        for name in ('generate_image_description', 'generate_animation_prompt'):
            with self.subTest(name=name):
                prompt = Mock(return_value='Quiet ambient motion')
                namespace = {
                    'get_image_narration_context': context,
                    'get_text_to_next': lambda **kwargs: 'The Journey',
                    'llm': SimpleNamespace(str_prompt=prompt),
                    'const': SimpleNamespace(FPS=25), 'Tag': Tag,
                }
                function = load_function(root / 'images/images.py', name, namespace)
                if name == 'generate_animation_prompt':
                    function(None, image)
                else:
                    function(image)
                sent = prompt.call_args.args[0]
                self.assertNotIn('dragon', sent)
                self.assertIn('Do not summarize the story', sent)
                self.assertIn('quiet introductory illustration', sent)


if __name__ == '__main__':
    unittest.main()
