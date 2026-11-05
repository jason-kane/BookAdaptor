import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from flask import Flask, request


class ChooseTagTests(unittest.TestCase):
    def test_returns_cards_without_waiting_for_sample_generation(self):
        path = Path(__file__).resolve().parents[1] / 'src/styler/views.py'
        method = next(node for node in ast.walk(ast.parse(path.read_text()))
                      if isinstance(node, ast.FunctionDef) and node.name == 'choose_tag')
        method.decorator_list = []
        style = SimpleNamespace(name='Joe Quesada', style_tag='joe_quesada',
                                as_card=Mock(return_value='<div>Artist card</div>'),
                                get_image_path=Mock(side_effect=AssertionError('Sample lookup during tab response')))
        draw_sample = Mock(side_effect=AssertionError('Rendering during tab response'))
        library = SimpleNamespace(with_tag=Mock(return_value=[style]))
        client = Mock()
        client.scard.return_value = 1
        namespace = dict(request=request, log=Mock(),
                         styles=SimpleNamespace(StyleLibrary=lambda: library, r=client), draw_sample=draw_sample,
                         STYLE_TAG_PAGINATION_THRESHOLD=100,
                         url_for=lambda *args, **kwargs: '/styles/stockpile')
        exec(compile(ast.Module(body=[method], type_ignores=[]), str(path), 'exec'), namespace)
        app = Flask(__name__)
        with patch.dict('sys.modules', {'styler.backfill': SimpleNamespace(start=Mock(), stop=Mock())}), \
                app.test_request_context('/styles/choose_tag', method='PUT', data={'tag': 'Artist'}):
            response, status = namespace['choose_tag']()
        self.assertEqual(status, 200)
        self.assertIn('Artist card', response)
        self.assertLess(response.index('data-stockpile-slot'), response.index('Artist card'))
        library.with_tag.assert_called_once_with('Artist')
        draw_sample.assert_not_called()
        style.get_image_path.assert_not_called()
