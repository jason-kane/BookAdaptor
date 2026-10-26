"""Pagination refills render missing cards without restarting backfill or replacing survivors."""
import ast
from html import escape
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from flask import Flask, request
import flask

ROOT = Path(__file__).resolve().parents[1]


def view_functions(reader):
    path = ROOT / 'src/styler/views.py'
    tree = ast.parse(path.read_text())
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in ('_lost_page', 'style_page', 'choose_tag')]
    for node in nodes:
        node.decorator_list = []
    scope = dict(styles=SimpleNamespace(r=Mock(), lost_styles_page=reader), request=request,
                 flask=flask, escape=escape, json=json, url_for=lambda _: '/styles/style_page')
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), scope)
    return scope


def styles(count):
    return [SimpleNamespace(key=chr(97 + index), style_tag=chr(97 + index),
                            as_card=Mock(return_value=f'<div id="card_{chr(97 + index)}" class="card-overview"></div>')) for index in range(count)]


def test_refill_returns_only_missing_cards_and_the_canonical_page():
    members = styles(5)
    reader = Mock(return_value=(members, 9))
    scope = view_functions(reader)
    app = Flask(__name__)
    with app.test_request_context('/styles/style_page?tag=LOST&page=0&present=a&present=b&present=c&present=d'):
        data = scope['style_page']().get_json()
    assert data['keys'] == ['a', 'b', 'c', 'd', 'e']
    assert list(data['entries']) == ['e']
    assert 'data-style-key="e"' in data['entries']['e']
    assert data['page'] == 0 and data['page_size'] == 5 and data['total'] == 9
    for style in members[:4]:
        style.as_card.assert_not_called()
    members[-1].as_card.assert_called_once_with(tag='LOST', oob=False)
    scope['styles'].r.lpush.assert_not_called()


def test_refill_moves_back_when_the_last_page_is_no_longer_valid():
    members = styles(5)
    reader = Mock(side_effect=[([], 5), (members, 5)])
    scope = view_functions(reader)
    app = Flask(__name__)
    with app.test_request_context('/styles/style_page?tag=LOST&page=1'):
        data = scope['style_page']().get_json()
    assert data['page'] == 0
    assert len(data['keys']) == 5
    assert [call.args[1] for call in reader.call_args_list] == [1, 0]


def test_empty_page_returns_valid_zero_metadata():
    scope = view_functions(Mock(return_value=([], 0)))
    app = Flask(__name__)
    with app.test_request_context('/styles/style_page?tag=LOST&page=0'):
        data = scope['style_page']().get_json()
    assert data['keys'] == [] and data['entries'] == {} and data['total'] == 0


def test_initial_lost_list_exposes_refill_and_pager_metadata():
    members = styles(5)
    scope = view_functions(Mock(return_value=(members, 8)))
    app = Flask(__name__)
    backfill = SimpleNamespace(start=Mock(), stop=Mock(), restrict_lost_queue=Mock())
    with patch.dict('sys.modules', {'styler.backfill':backfill}), app.test_request_context('/styles/choose_tag', method='PUT', data={'tag':'LOST'}):
        html, status = scope['choose_tag']()
    assert status == 200
    assert 'data-style-page ' in html and 'data-page-url="/styles/style_page"' in html
    assert 'data-page-total="8"' in html
    assert 'data-style-page-nav="next"' in html and 'data-style-page-nav="previous"' in html
    assert html.count('data-style-entry') == 5
    backfill.start.assert_not_called()


def test_lost_reader_fills_holes_from_deleted_and_already_resolved_styles():
    class Client:
        def __init__(self):
            self.lost = set(['0missing', '1race', '2resolved', 'a', 'b', 'c', 'd', 'e', 'f'])
            self.index = set(self.lost)
        def scard(self, _): return len(self.lost)
        def zcard(self, _): return len(self.index)
        def zrange(self, _, start, end): return sorted(self.index)[start:end+1]
        def sismember(self, _, key): return key in self.lost
        def zrem(self, _, key): self.index.discard(key)
        def pipeline(self, **kwargs):
            client = self
            class Pipe:
                def __init__(self): self.keys = []
                def __enter__(self): return self
                def __exit__(self, *_): pass
                def type(self, key): self.keys.append(key)
                def execute(self): return ['none' if key == '0missing' else 'hash' for key in self.keys]
            return Pipe()
    client = Client()
    def discard(client, key):
        client.lost.discard(key)
        client.index.discard(key)
    def style(key):
        if key == '1race': raise ValueError('Deleted during load')
        if key == '2resolved': discard(client, key)
        return SimpleNamespace(key=key)
    path = ROOT / 'src/styler/styles.py'
    node = next(node for node in ast.parse(path.read_text()).body if isinstance(node, ast.FunctionDef) and node.name == 'lost_styles_page')
    scope = dict(Style=style, as_id=lambda key:key, _discard_missing_lost_style=discard)
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), scope)
    members, total = scope['lost_styles_page'](client, 0, 5)
    assert [member.key for member in members] == ['a', 'b', 'c', 'd', 'e']
    assert total == 6
