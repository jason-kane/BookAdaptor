"""Highlight bounds for empty phrases and chapters without visible text."""
import ast
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

import numpy as np
from PIL import Image
from bs4 import BeautifulSoup, Tag


class HighlightBoundsTests(unittest.TestCase):
    def test_empty_and_missing_highlights_do_not_break_bottom_height(self):
        source = Path(__file__).parents[1] / 'src/artifact_editor/chapter/chapter.py'
        chapter = next(node for node in ast.parse(source.read_text()).body
                       if isinstance(node, ast.ClassDef) and node.name == 'Chapter')
        methods = [node for node in chapter.body if isinstance(node, ast.FunctionDef)
                   and node.name in ('get_phrase_rainbow', 'get_bottom_height')]
        with TemporaryDirectory() as temp:
            scope = dict(np=np, os=os, Image=Image, Tag=Tag, as_int=int,
                         const=SimpleNamespace(LIBRARY_DIR=temp))
            exec(compile(ast.Module(body=methods, type_ignores=[]), str(source), 'exec'), scope)
            soup = BeautifulSoup('<chapter><phrase index="0">Visible</phrase>'
                                 '<phrase index="1">Hidden</phrase><phrase index="2"/></chapter>', 'xml')
            data = np.full((10, 12, 4), 255, dtype=np.uint8)
            data[3:6, 2:8] = [0, 0, 10, 255]
            Image.fromarray(data).save(Path(temp) / 'chapter.rainbow-1.png')
            fake = SimpleNamespace(chapterdir='', language='',
                                   index_to_highlight_color=lambda index: (0, 0, index + 10),
                                   phrases=lambda: [SimpleNamespace(phrase_xml=tag)
                                                    for tag in soup.find_all('phrase')])
            fake.get_phrase_rainbow = lambda tag: scope['get_phrase_rainbow'](fake, tag)
            mask, *bounds = fake.get_phrase_rainbow(soup.find('phrase', index='2'))
            self.assertFalse(mask.any())
            self.assertEqual(bounds, [0, 0, 0, 0])
            self.assertEqual(scope['get_bottom_height'](fake), 5)
            soup.find('phrase', index='0').decompose()
            self.assertEqual(scope['get_bottom_height'](fake), 0)
            soup.find('phrase', index='1').decompose()
            self.assertEqual(scope['get_bottom_height'](fake), 0)


if __name__ == '__main__':
    unittest.main()
