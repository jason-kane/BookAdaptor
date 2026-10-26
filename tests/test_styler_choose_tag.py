import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


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
        form = Mock()
        form.getlist.return_value = ['2', '7']
        form.get.side_effect = lambda key, default=None: {'tag': 'Artist'}.get(key, default)
        namespace = dict(request=SimpleNamespace(args={}, form=form), log=Mock(),
                         styles=SimpleNamespace(StyleLibrary=lambda: library, r=Mock()), draw_sample=draw_sample,
                         stockpile_entry=Mock(return_value='<div>Stockpile card</div>'))
        exec(compile(ast.Module(body=[method], type_ignores=[]), str(path), 'exec'), namespace)
        with patch.dict('sys.modules', {'styler.backfill': SimpleNamespace(start=Mock())}):
            response, status = namespace['choose_tag']()
        self.assertEqual(status, 200)
        self.assertIn('Artist card', response)
        self.assertLess(response.index('Stockpile card'), response.index('Artist card'))
        library.with_tag.assert_called_once_with('Artist')
        draw_sample.assert_not_called()
        style.get_image_path.assert_not_called()
