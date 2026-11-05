"""Per-image opt-outs persist and exclude bulk animation work."""
import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import sys

from bs4 import BeautifulSoup
import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(relative, name, **scope):
    path = ROOT / relative
    node = next(n for n in ast.walk(ast.parse(path.read_text()))
                if isinstance(n, ast.FunctionDef) and n.name == name)
    node.decorator_list = []
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), scope)
    return scope[name]


@pytest.mark.parametrize('insufficient', [False, True])
def test_bulk_skips_opted_out_images_before_asset_checks(monkeypatch, insufficient):
    image = BeautifulSoup('<image index="0" do_not_animate="1"/>', 'xml').image
    chapter = SimpleNamespace(images=lambda: [image], get_image_filename=Mock(), save_images=Mock(), get_xml=Mock())
    monkeypatch.setitem(sys.modules, 'artifact_editor.chapter.timeline', SimpleNamespace(image_timings=lambda _: {}))
    method = load('src/artifact_editor/textoviva/textoviva.py', 'generate_missing_animations')
    method(SimpleNamespace(chapter=chapter), regenerate_insufficient=insufficient)
    chapter.get_image_filename.assert_not_called()


def test_unchecked_default_still_requires_still_image():
    image = BeautifulSoup('<image index="0"/>', 'xml').image
    chapter = SimpleNamespace(images=lambda: [image], get_image_filename=Mock(return_value=None))
    method = load('src/artifact_editor/textoviva/textoviva.py', 'generate_missing_animations')
    with pytest.raises(ValueError, match='Generate missing images'):
        method(SimpleNamespace(chapter=chapter))


@pytest.mark.parametrize('value', ['1', '0'])
def test_preference_endpoint_saves_checked_and_unchecked_states(value):
    image = BeautifulSoup('<image index="0" do_not_animate="1"/>', 'xml').image
    chapter = SimpleNamespace(load_xml=Mock(), get_image=Mock(return_value=image), save_image=Mock(),
                              timeline=lambda: [dict(type='image', image=image, timing={'animation_shortfall': True})])
    render = Mock(return_value='badge update')
    save = load('src/artifact_editor/textoviva/views.py', 'save_animation_preference',
                g=SimpleNamespace(chapter=chapter), request=SimpleNamespace(form={'do_not_animate': value}), render_template=render)
    assert save(0) == 'badge update'
    assert render.call_args.kwargs['animation_disabled'] == (value == '1')
    assert render.call_args.kwargs['timing_oob'] is True
    assert (image.get('do_not_animate') == '1') == (value == '1')
    chapter.save_image.assert_called_once_with(image)
