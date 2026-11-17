import ast
import base64
from hashlib import md5
from pathlib import Path
from types import SimpleNamespace


def test_chapter_18_identity_contains_no_path_separator():
    path = Path('src/artifact_editor/chapter/chapter.py')
    chapter = next(node for node in ast.parse(path.read_text()).body
                   if isinstance(node, ast.ClassDef) and node.name == 'Chapter')
    method = next(node for node in chapter.body if isinstance(node, ast.FunctionDef)
                  and node.name == 'make_it_nice')
    scope = dict(base64=base64, md5=md5)
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(path), 'exec'), scope)
    book = dict(author=SimpleNamespace(name='L. Frank Baum'), title='The Marvelous Land of Oz', language='english')
    for number in range(1, 25):
        value = scope['make_it_nice'](SimpleNamespace(**book, number=number))
        assert '/' not in value and '\\' not in value
        if number == 18:
            assert value == 'baum-marv-018-y_gf'
