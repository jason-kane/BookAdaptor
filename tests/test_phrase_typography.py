"""Bulk typography persists phrase records without editing source sections or audio."""
import ast
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

from bs4 import BeautifulSoup
import flask
import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    'typography_persistence', ROOT / 'src/artifact_editor/chapter/legacy_persistence.py')
persistence = importlib.util.module_from_spec(spec)
spec.loader.exec_module(persistence)


@pytest.fixture
def client(tmp_path):
    path = tmp_path / 'chapter.xml'
    soup = BeautifulSoup('<chapter><paragraph index="0">'
        '<phrase index="0" src="audio.wav" frames="25" pronunciation="manual">One.</phrase>'
        '<phrase index="1" speaker="Narrator">Two.</phrase>'
        '<phrase index="2">Three.</phrase></paragraph></chapter>', 'xml')
    soup.find('phrase', index='0')['delta'] = json.dumps([
        {'insert': 'One.', 'attributes': {'font': 'Cardo', 'size': '14px', 'italic': True}},
        {'insert': '\n', 'attributes': {'align': 'center'}}])
    soup.find('phrase', index='1')['delta'] = json.dumps({'ops': [{'insert': 'Two.'}]})
    path.write_text(str(soup))
    sections = tmp_path / 'chapter.sections.json'
    sections.write_text('[{"template":"book_page"}]')
    chapter = SimpleNamespace(soup=soup, get_xml=lambda: soup, get_xml_fn=lambda: str(path))
    persistence.remember_content(chapter)
    chapter.save_phrases = lambda phrases: persistence.save_records(chapter, phrases)
    tree = ast.parse((ROOT / 'src/artifact_editor/chapter/views.py').read_text())
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'set_phrase_typography')
    node.decorator_list = []
    namespace = dict(request=flask.request, g=flask.g, flask=flask, json=json)
    exec(compile(ast.Module(body=[node], type_ignores=[]), 'views.py', 'exec'), namespace)
    app = flask.Flask(__name__)
    app.add_url_rule('/phrase_typography', view_func=namespace['set_phrase_typography'], methods=['POST'])

    @app.before_request
    def bind_chapter():
        flask.g.chapter = chapter

    return app.test_client(), chapter, path, sections


def test_changes_all_persisted_deltas_and_preserves_other_fields(client):
    http, chapter, path, sections = client
    before = BeautifulSoup(path.read_text(), 'xml')
    response = http.post('/phrase_typography', json={'font': 'Helvetica', 'size': '24px'})
    assert response.status_code == 200
    assert response.json == {'updated': 3}
    saved = BeautifulSoup(path.read_text(), 'xml')
    for old, phrase in zip(before.find_all('phrase'), saved.find_all('phrase')):
        # The existing XML writer pretty-prints text-node indentation.
        assert phrase.get_text().strip() == old.get_text().strip()
        assert {k: v for k, v in phrase.attrs.items() if k != 'delta'} == {
            k: v for k, v in old.attrs.items() if k != 'delta'}
        delta = json.loads(phrase['delta'])
        for operation in delta['ops'] if isinstance(delta, dict) else delta:
            assert operation['attributes']['font'] == 'Helvetica'
            assert operation['attributes']['size'] == '24px'
    first = json.loads(saved.phrase['delta'])
    assert first[0]['attributes']['italic'] is True
    assert first[1]['attributes']['align'] == 'center'
    assert sections.read_text() == '[{"template":"book_page"}]'


def test_reset_removes_only_typography(client):
    http, chapter, path, _ = client
    assert http.post('/phrase_typography', json={'font': None, 'size': None}).status_code == 200
    delta = json.loads(BeautifulSoup(path.read_text(), 'xml').phrase['delta'])
    assert delta[0]['attributes'] == {'italic': True}
    assert delta[1]['attributes'] == {'align': 'center'}


@pytest.mark.parametrize('payload', [{'font': 'Unknown', 'size': '24px'},
    {'font': ['Cardo'], 'size': '24px'}, {'font': 'Cardo'}, {'font': 'Cardo', 'size': 'huge'}])
def test_invalid_selection_does_not_modify_records(client, payload):
    http, chapter, path, _ = client
    before = path.read_bytes()
    assert http.post('/phrase_typography', json=payload).status_code == 400
    assert path.read_bytes() == before


def test_bad_later_delta_does_not_partially_apply_changes(client):
    http, chapter, path, _ = client
    chapter.soup.find('phrase', index='2')['delta'] = 'invalid JSON'
    before = chapter.soup.phrase['delta']
    assert http.post('/phrase_typography', json={'font': 'Helvetica', 'size': '24px'}).status_code == 400
    assert chapter.soup.phrase['delta'] == before
