import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

PATH = Path(__file__).resolve().parents[1] / 'src/styler/views.py'


def load(name, namespace):
    method = next(n for n in ast.parse(PATH.read_text()).body if isinstance(n, ast.FunctionDef) and n.name == name)
    method.decorator_list = []
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(PATH), 'exec'), namespace)
    return namespace[name]


class StylerTierTests(unittest.TestCase):
    def test_slider_has_eight_icon_markers_and_current_value(self):
        cosmetics = [(i, 'face-smile', 'brand', str(i)) for i in range(8)]
        slider = load('style_tier_slider', dict(styles=SimpleNamespace(tier_cosmetics=cosmetics),
                      url_for=lambda *args, **kwargs: '/styles/artist/tier?engine=' + kwargs['engine']))
        html = slider(SimpleNamespace(style_tag='artist', get_engine_tier=lambda engine: 4), 'qwen')
        self.assertIn('value="4"', html)
        self.assertEqual(html.count('slot="reference"'), 8)
        self.assertIn('hx-trigger="change"', html)
        self.assertIn('hx-post="/styles/artist/tier?engine=qwen"', html)

    def test_tier_saves_and_rejects_invalid_values(self):
        style = Mock()
        constructor = Mock(return_value=style)
        request = SimpleNamespace(form={'tier': '7'}, args={'engine': 'qwen'})
        handler = load('set_style_tier', dict(request=request, log=Mock(),
                       styles=SimpleNamespace(Style=constructor, tier_cosmetics=list(range(8)))))
        self.assertEqual(handler('artist')[1], 200)
        constructor.assert_called_once_with('artist')
        style.set_engine_tier.assert_called_once_with('qwen', 7)
        for value in ('8', '-1', 'invalid'):
            request.form = {'tier': value}
            self.assertEqual(handler('artist')[1], 400)
        style.set_engine_tier.assert_called_once()

        request.args = {'engine': 'unknown'}
        self.assertEqual(handler('artist')[1], 400)
