"""Manual speaker pickers must include characters missing from local extraction."""
import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

ROOT = Path(__file__).parents[1] / 'src/artifact_editor'


def test_both_speaker_pickers_offer_book_characters():
    chapter = SimpleNamespace(kwargs={})
    records = {'Narrator': {'tag': 'Narrator', 'name': 'Narrator'},
               'Jack_Pumpkinhead': {'tag': 'Jack_Pumpkinhead', 'name': 'Jack Pumpkinhead'}}
    catalog = Mock(side_effect=lambda chapter, is_global=False: records if is_global else {'Narrator': records['Narrator']})
    scope = dict(characters=SimpleNamespace(get_all_characters=catalog), Speaker=SimpleNamespace,
                 url_for=lambda *a, **kw: '/speaker', log=Mock())
    tree = ast.parse((ROOT / 'chapter/chapter.py').read_text())
    method = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == 'speakers')
    exec(compile(ast.Module(body=[method], type_ignores=[]), '<speakers>', 'exec'), scope)
    assert [s.tag for s in scope['speakers'](chapter)] == ['Narrator', 'Jack_Pumpkinhead']
    tree = ast.parse((ROOT / 'phrases/htmx.py').read_text())
    tree.body = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'speaker_selector']
    exec(compile(tree, '<selector>', 'exec'), scope)
    html = scope['speaker_selector'](chapter, {'index': '137', 'speaker': 'Narrator'})
    assert '<wa-option value="Jack_Pumpkinhead">Jack Pumpkinhead</wa-option>' in html
    assert catalog.call_count == 2
    assert all(call.kwargs == {'is_global': True} for call in catalog.call_args_list)
