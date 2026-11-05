"""The image editor works before scene character metadata exists."""
import ast
import html
from pathlib import Path
from types import SimpleNamespace

from bs4 import BeautifulSoup
import pytest


@pytest.mark.parametrize('scene_names,expected', [
    ('', []),
    ('  , ', []),
    ('Jack', ['Jack_Pumpkinhead']),
    ('Jack, Unknown', ['Jack_Pumpkinhead', 'Unknown']),
    ('Unknown', ['Unknown']),
])
def test_focus_character_handles_missing_and_unresolved_scene_metadata(scene_names, expected):
    path = Path(__file__).parents[1] / 'src/artifact_editor/images/scene/htmx.py'
    node = next(node for node in ast.parse(path.read_text()).body
                if isinstance(node, ast.FunctionDef) and node.name == 'focus_character')
    characters = SimpleNamespace(
        get_all_characters=lambda _: {'Jack_Pumpkinhead': {'name': 'Jack Pumpkinhead'}},
        resolve_character_tag=lambda _, name: 'Jack_Pumpkinhead' if name in ('Jack', 'Jack_Pumpkinhead') else None,
    )
    scope = {'html': html, 'characters': characters, 'url_for': lambda *args, **kwargs: '/focus'}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), scope)
    image = BeautifulSoup('<image index="5"/>', 'xml').image
    image['scene_characters'] = scene_names
    image['focus_character'] = 'Jack'
    markup = scope['focus_character'](SimpleNamespace(kwargs={}), image)
    if not expected:
        assert markup == ''
    else:
        soup = BeautifulSoup(markup, 'html.parser')
        assert [option['value'] for option in soup.find_all('wa-option')] == expected
        assert soup.find('wa-select')['value'] == 'Jack_Pumpkinhead'
