"""A canonical book speaker remains usable without a chapter-local reference."""
import ast
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from bs4 import BeautifulSoup


def test_book_only_speaker_keeps_voice_after_chapter_count_refresh():
    path = Path('src/artifact_editor/characters/characters.py')
    names = {'get_character', 'number_of_appearances'}
    nodes = [n for n in ast.parse(path.read_text()).body
             if isinstance(n, ast.FunctionDef) and n.name in names]
    canonical = {'tag': 'Mombi', 'name': 'Mombi', 'is_global': True,
                 'voices': [{'id': 'af_heart', 'strength': 100}], 'description': 'Witch'}
    xml = BeautifulSoup('<chapter><phrase speaker="Mombi">Hello.</phrase></chapter>', 'xml')
    scope = dict(log=Mock(), inspect=SimpleNamespace(stack=lambda: [None, SimpleNamespace(function='test')]),
                 defaultdict=defaultdict,
                 get_all_characters=lambda chapter, is_global=False: {'Mombi': canonical.copy()} if is_global else {},
                 save_characters=Mock())
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), scope)
    result = scope['get_character'](SimpleNamespace(get_xml=lambda: xml), 'Mombi')
    assert result['voices'] == canonical['voices']
    assert result['description'] == 'Witch'
    assert result['tag'] == 'Mombi'
