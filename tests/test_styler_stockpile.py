import ast
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock

from bs4 import BeautifulSoup
from flask import Flask, render_template, send_from_directory
import flask
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from stockpile import tagged_images, tag_counts


def load(name, namespace):
    path = ROOT / 'src/styler/views.py'
    node = next(node for node in ast.parse(path.read_text()).body
                if isinstance(node, ast.FunctionDef) and node.name == name)
    node.decorator_list = []
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), namespace)
    return namespace[name]


class StockpileDisplayTests(unittest.TestCase):
    def test_counts_include_stockpile_only_tags_and_count_each_image_once(self):
        with TemporaryDirectory() as tmp:
            self.add_image(tmp, 'first.png', ['Oil Painting', 'oil painting', 'red'])
            self.add_image(tmp, 'second.jpg', ['oil painting', 'stock only'])
            invalid = self.add_image(tmp, 'invalid.png', ['ignore'])
            invalid.with_suffix('.json').write_text('invalid')
            self.assertEqual(tag_counts(tmp), {'oil painting': 2, 'red': 1, 'stock only': 1})
            category_counts = load('stockpile_category_counts', dict(const=SimpleNamespace(LIBRARY_DIR=tmp)))
            counts = category_counts(['Oil Painting'])
            self.assertEqual(counts, {'Oil Painting': 2, 'red': 1, 'stock only': 1})
            client = MagicMock()
            client.smembers.return_value = ['Oil Painting']
            client.pipeline.return_value.__enter__.return_value.execute.return_value = [3 if tag == 'Oil Painting' else 0 for tag in counts]
            handler = load('stockpile_tags', dict(styles=SimpleNamespace(r=client),
                                                   stockpile_category_counts=category_counts, flask=flask))
            with Flask(__name__).app_context():
                result = handler().get_json()['categories']
            self.assertEqual({item['tag']: (item['count'], item['stockpile_count']) for item in result},
                             {'Oil Painting': (3, 2), 'red': (0, 1), 'stock only': (0, 1)})

    def add_image(self, root, name, tags):
        path = Path(root) / 'stockpile' / name
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new('RGB', (3, 2), 'red').save(path)
        path.with_suffix('.json').write_text(json.dumps(dict(tags=tags, description='A <red> image')))
        return path

    def app(self):
        app = Flask(__name__, template_folder=str(ROOT / 'src/styler/templates'))
        app.add_url_rule('/styles/stockpile', 'styles.stockpile_page', lambda: '')
        app.add_url_rule('/styles/stockpile/image/<path:filename>', 'styles.stockpile_image', lambda filename: '')
        return app

    def test_matches_sidecars_case_insensitively_and_skips_unmatched_or_invalid(self):
        with TemporaryDirectory() as tmp:
            self.add_image(tmp, 'first.png', [' Oil   Painting '])
            self.add_image(tmp, 'nested/second.jpg', ['oil painting'])
            self.add_image(tmp, 'other.png', ['ink drawing'])
            invalid = self.add_image(tmp, 'invalid.png', ['oil painting'])
            invalid.with_suffix('.json').write_text('invalid JSON')
            self.assertEqual([image['filename'] for image in tagged_images(tmp, 'oil painting')],
                             ['first.png', 'nested/second.jpg'])
            self.assertEqual(tagged_images(tmp, 'unknown'), [])

    def test_pages_are_sets_of_three_and_boundaries_are_disabled(self):
        with TemporaryDirectory() as tmp:
            for i in range(7):
                self.add_image(tmp, f'{i}.png', ['oil & ink'])
            render = load('stockpile_entry', dict(const=SimpleNamespace(LIBRARY_DIR=tmp), render_template=render_template))
            app = self.app()
            with app.test_request_context():
                pages = [BeautifulSoup(render('oil & ink', page), 'html.parser') for page in (0, 1, 2, 999)]
                empty = BeautifulSoup(render('unknown'), 'html.parser')
            self.assertEqual([len(page.select('img')) for page in pages], [3, 3, 1, 1])
            self.assertTrue(pages[0].find('wa-button', attrs={'aria-label': 'Previous three stockpile images'}).has_attr('disabled'))
            self.assertIn('page=1', pages[0].find('wa-button', attrs={'aria-label': 'Next three stockpile images'})['hx-get'])
            self.assertIn('3.png', pages[1].select_one('img')['src'])
            self.assertTrue(pages[2].find('wa-button', attrs={'aria-label': 'Next three stockpile images'}).has_attr('disabled'))
            self.assertEqual(pages[0].select_one('img')['alt'], 'A <red> image')
            self.assertIsNone(pages[0].find('red'))
            self.assertIn('No stockpile images with this tag.', empty.get_text())
            self.assertEqual(len(empty.select('wa-button[disabled]')), 2)

    def test_image_route_serves_images_and_blocks_traversal_and_non_images(self):
        with TemporaryDirectory() as tmp:
            image = self.add_image(tmp, 'nested/image & test.png', ['red'])
            app = Flask(__name__)
            handler = load('stockpile_image', dict(const=SimpleNamespace(LIBRARY_DIR=tmp),
                                                   flask=flask, send_from_directory=send_from_directory))
            app.add_url_rule('/image/<path:filename>', view_func=handler)
            client = app.test_client()
            response = client.get('/image/nested/image%20%26%20test.png')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.data, image.read_bytes())
            response.close()
            self.assertEqual(client.get('/image/nested/image%20%26%20test.json').status_code, 404)
            self.assertEqual(client.get('/image/../outside.png').status_code, 404)
