"""Exercise forex rendering without importing image generation services."""
import ast
import logging
from pathlib import Path
import time
from types import SimpleNamespace
import unittest
from bs4 import BeautifulSoup


def load_function(path, name, namespace):
    tree = ast.parse(path.read_text())
    node = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name)
    node.decorator_list = []
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), namespace)
    return namespace[name]


class ForexTests(unittest.TestCase):
    def setUp(self):
        self.render = load_function(Path(__file__).parents[1] / 'images.py', 'image_forex', {
            'log': logging.getLogger(__name__), 'time': time,
            'url_for': lambda endpoint, **kw: f"/{endpoint}/{kw['image_index']}",
        })

    def check_sequence(self, ids, active_position):
        soup = BeautifulSoup('<chapter>' + ''.join(f'<image index="{index}" src="image.png"/>' for index in ids) + '</chapter>', 'xml')
        chapter = SimpleNamespace(get_xml=lambda: soup, kwargs={}, author=SimpleNamespace(name='Author'), title='Book', number=1, language='english')
        before = str(soup)
        active_id = ids[active_position]
        html = self.render(chapter, soup.find('image', index=str(active_id)), active_id)
        rendered = BeautifulSoup(html, 'html.parser')
        actual = [int(img['class'][0].removeprefix('image_')) for img in rendered.find_all('img')]
        self.assertEqual(actual, ids[max(0, active_position - 84):active_position + 85])
        for link in rendered.find_all('a'):
            image_id = link.img['class'][0].removeprefix('image_')
            self.assertTrue(link['href'].endswith('/' + image_id))
            self.assertTrue(link.img['src'].endswith('/' + image_id))
        self.assertEqual(str(soup), before)

    def test_sparse_unsorted_ids_at_each_position(self):
        for position in range(5):
            with self.subTest(position=position):
                self.check_sequence([80, 2, 999, 0, 15], position)

    def test_all_grid_boundaries_without_duplicate_thumbnails(self):
        ids = [10000 - index * 7 for index in range(200)]
        for position in [0, 4, 5, 20, 21, 84, 100, 199]:
            with self.subTest(position=position):
                self.check_sequence(ids, position)


if __name__ == '__main__':
    unittest.main()
