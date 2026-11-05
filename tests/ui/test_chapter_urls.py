"""URL builders preserve mounted prefixes, language and escaped path parameters."""
import ast
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import unquote, urlsplit

from bs4 import BeautifulSoup, Tag
from flask import Flask, g, request, url_for
import pytest

ROOT = Path(__file__).resolve().parents[2]


def load_function(relative, name, **scope):
    path = ROOT / 'src' / relative
    node = next(n for n in ast.parse(path.read_text()).body if isinstance(n, ast.FunctionDef) and n.name == name)
    node.decorator_list = []
    namespace = dict(os=os, url_for=url_for, Tag=Tag, log=Mock(), **scope)
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), namespace)
    return namespace[name]


def add_route(app, module, name, endpoint, prefix):
    path = ROOT / 'src' / module
    node = next(n for n in ast.parse(path.read_text()).body if isinstance(n, ast.FunctionDef) and n.name == name)
    route = next(d for d in node.decorator_list if isinstance(d, ast.Call)
                 and isinstance(d.func, ast.Attribute) and d.func.attr in ('route', 'get', 'post', 'put', 'delete'))
    rule = ast.literal_eval(route.args[0])
    methods = next((ast.literal_eval(k.value) for k in route.keywords if k.arg == 'methods'),
                   [route.func.attr.upper()] if route.func.attr != 'route' else ['GET'])
    app.add_url_rule(prefix.rstrip('/') + '/' + rule.lstrip('/'), endpoint=endpoint,
                     view_func=lambda **kw: '', methods=methods)


class Chapter:
    kwargs = dict(author='Zoë & Sons', title='Door #2? 海', chapter_number=7, language='french')

    @property
    def url(self):
        raise AssertionError('Do not read the obsolete chapter.url property')


@pytest.fixture
def urls():
    app = Flask(__name__)
    prefix = '/mounted/library/<author>/<title>/<chapter_number>/<language>'
    definitions = [
        ('phrases/views.py', 'repronounce_missing_words', 'phrases', '/phrases'),
        ('phrases/views.py', 'reset_audio_cache_json', 'phrases', '/phrases'),
        ('phrases/views.py', 'audio_try_cmu_dict', 'phrases', '/phrases'),
        ('phrases/pronunciation/views.py', 'find_unpronouncable_words', 'phrases.pronunciation', '/phrases/pronunciation'),
        ('images/views.py', 'create_t5_prompt', 'images', '/images'),
        ('images/views.py', 'set_meta_prompt', 'images', '/images'),
        ('images/views.py', 'generate_meta', 'images', '/images'),
        ('images/views.py', 'save_style', 'images', '/images'),
        ('images/scene/views.py', 'copy_from_previous', 'images.scene', '/images/<int:image_index>/scene'),
        ('characters/views.py', 'htmx_pronunciation_filter', 'characters', '/characters'),
        ('characters/views.py', 'htmx_add_effect_to_character', 'characters', '/characters'),
        ('characters/views.py', 'htmx_update_effect_config', 'characters', '/characters'),
    ]
    for module, name, namespace, suffix in definitions:
        add_route(app, 'artifact_editor/' + module, name,
                  'library.book.chapter.' + namespace + '.' + name, prefix + suffix)
    return app


def assert_destination(app, markup, attribute, endpoint, **parameters):
    button = BeautifulSoup(markup, 'html.parser').select_one(f'[{attribute}]')
    url = button[attribute]
    assert url.startswith('/mounted/library/')
    assert not urlsplit(url).query and not urlsplit(url).fragment
    resolved, values = app.url_map.bind('localhost').match(unquote(url), method='PUT' if attribute == 'hx-put' else 'POST')
    assert resolved == 'library.book.chapter.' + endpoint
    assert values == dict(Chapter.kwargs, chapter_number='7', **parameters)
    return url


@pytest.mark.parametrize('module,function,endpoint', [
    ('phrases/htmx.py', 'repronounce_where_missing_button', 'phrases.repronounce_missing_words'),
    ('phrases/htmx.py', 'reset_audio_cache_json_button', 'phrases.reset_audio_cache_json'),
    ('phrases/pronunciation/htmx.py', 'find_unpronouncable_words_button', 'phrases.pronunciation.find_unpronouncable_words'),
    ('phrases/pronunciation/htmx.py', 'try_cmu_dict_button', 'phrases.audio_try_cmu_dict'),
])
def test_audio_actions_use_registered_endpoint(urls, module, function, endpoint):
    generic = load_function('artifact_editor/tools.py', 'generic_button')
    render = load_function('artifact_editor/' + module, function, generic_button=generic)
    with urls.test_request_context('/'):
        assert_destination(urls, render(Chapter()), 'hx-post', endpoint)


@pytest.mark.parametrize('function,endpoint', [
    ('create_prompt_button', 'images.create_t5_prompt'),
    ('meta_prompt', 'images.set_meta_prompt'),
    ('copy_from_previous_button', 'images.scene.copy_from_previous'),
])
def test_image_actions_use_registered_endpoint(urls, function, endpoint):
    render = load_function('artifact_editor/images/htmx.py', function)
    image = BeautifulSoup('<image index="4"/>', 'xml').image
    with urls.test_request_context('/'):
        if function == 'meta_prompt':
            html = render(Chapter(), image, 4)
        elif function == 'create_prompt_button':
            html = render(Chapter(), image)
        else:
            html = render(Chapter(), image_index=4)
        assert_destination(urls, html, 'hx-post', endpoint, image_index=4)


def test_character_pronunciation_uses_chapter_object_and_named_route(urls):
    render = load_function('artifact_editor/characters/htmx.py', 'character_pronunciation_widget')
    with urls.test_request_context('/'):
        html = render(Chapter(), 'Keeper & 海?', 1500)
        assert_destination(urls, html, 'hx-put', 'characters.htmx_pronunciation_filter', character_name='Keeper & 海?')


def test_character_effect_passes_chapter_to_configuration_widgets(urls):
    effect = Mock()
    effect.get_configuration_widgets.return_value = '<div>Pitch options</div>'
    registry = SimpleNamespace(selector=Mock(return_value=''), get_effect=Mock(return_value=lambda: effect))
    render = load_function('artifact_editor/characters/htmx.py', 'get_character_effects', audio_effects=SimpleNamespace(registry=registry))
    chapter = Chapter()
    config = {'octaves': 1.2}
    with urls.test_request_context('/'):
        html = render(chapter, 'Keeper & 海?', {'effects': {'pitch_shift': config}})
        assert_destination(urls, html, 'hx-post', 'characters.htmx_add_effect_to_character', character_tag='Keeper & 海?')
    effect.get_configuration_widgets.assert_called_once_with(chapter=chapter, character_name='Keeper & 海?', effect_config_dict=config)


def test_pitch_shift_configuration_builds_its_update_url(urls):
    path = ROOT / 'src/audio_effects/pitch_shift.py'
    cls = next(n for n in ast.parse(path.read_text()).body if isinstance(n, ast.ClassDef) and n.name == 'PitchShift')
    fn = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == 'get_configuration_widgets')
    scope = {'url_for': url_for}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), str(path), 'exec'), scope)
    with urls.test_request_context('/'):
        html = scope[fn.name](SimpleNamespace(key='pitch_shift'), Chapter(), 'Keeper & 海?', {'octaves': 1.2})
        assert_destination(urls, html, 'hx-put', 'characters.htmx_update_effect_config', character_name='Keeper & 海?', effect_key='pitch_shift')


def test_repronounce_handler_uses_request_chapter():
    chapter = Chapter()
    phrases = SimpleNamespace(repronounce_where_missing=Mock())
    htmx = SimpleNamespace(repronounce_where_missing_button=Mock(return_value='button'))
    handler = load_function('artifact_editor/phrases/views.py', 'repronounce_missing_words',
                            phrases=phrases, htmx=htmx, g=g)
    with Flask(__name__).test_request_context('/'):
        g.chapter = chapter
        assert handler() == 'button'
    phrases.repronounce_where_missing.assert_called_once_with(chapter)
    htmx.repronounce_where_missing_button.assert_called_once_with(chapter)


def test_explicit_button_url_needs_no_legacy_base():
    generic = load_function('artifact_editor/tools.py', 'generic_button')
    assert 'hx-post="/mounted/action"' in generic(url='/mounted/action')
    with pytest.raises(ValueError, match='URL is required'):
        generic()


def test_scene_metadata_button_uses_registered_route(urls):
    generic = load_function('artifact_editor/tools.py', 'generic_button')
    empty = lambda *args: ''
    chapter = Chapter()
    chapter.get_image_style = lambda image: 'ink'
    dependencies = {name: empty for name in (
        'description', 'setting', 'mood', 'camera_direction', 'focus_character',
        'characters_section', 'scene_props')}
    render = load_function('artifact_editor/images/scene/htmx.py', 'image_scene_workshop',
        generic_button=generic, styles_htmx=SimpleNamespace(style_selector=lambda **kw: ''), **dependencies)
    with urls.test_request_context('/'):
        html = render(chapter, BeautifulSoup('<image index="4"/>', 'xml').image)
        assert_destination(urls, html, 'hx-post', 'images.generate_meta', image_index=4)


@pytest.mark.parametrize('action', ['add', 'update'])
def test_effect_callbacks_use_request_chapter(action):
    chapter = Chapter()
    chapter.language = 'french'
    character = {'effects': {}}
    store = SimpleNamespace(get_character=Mock(return_value=character), save_character=Mock())
    htmx = SimpleNamespace(get_character_effects=Mock(return_value='effect controls'))
    effect = SimpleNamespace(get_configuration_keys=lambda: ['octaves'])
    function = 'htmx_add_effect_to_character' if action == 'add' else 'htmx_update_effect_config'
    handler = load_function('artifact_editor/characters/views.py', function,
        g=g, request=request, characters=store, htmx=htmx,
        voices=SimpleNamespace(KOKORO_VOICES={}),
        audio_effects=SimpleNamespace(registry=SimpleNamespace(get_effect=lambda key: lambda: effect)))
    data = {'effect': 'pitch_shift'} if action == 'add' else {'octaves': '1.2'}
    with Flask(__name__).test_request_context('/', method='POST' if action == 'add' else 'PUT', data=data):
        g.chapter = chapter
        if action == 'add':
            assert handler('keeper') == 'effect controls'
            assert character['effects']['pitch_shift'] == {}
        else:
            assert handler('keeper', 'pitch_shift') == 'effect controls'
            assert character['effects']['pitch_shift'] == {'octaves': '1.2'}
    store.save_character.assert_called_once_with(chapter, 'keeper', character)
    htmx.get_character_effects.assert_called_once_with(chapter, 'keeper', character)


@pytest.mark.parametrize('module', ['chapter', 'characters', 'images', 'phrases',
    'phrases/pronunciation', 'phrases/timeline', 'frames', 'music'])
def test_view_modules_do_not_read_obsolete_chapter_url(module):
    path = ROOT / 'src/artifact_editor' / module / 'views.py'
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Attribute) and node.attr == 'url':
            receiver = node.value
            assert not (isinstance(receiver, ast.Name) and receiver.id == 'chapter'
                        or isinstance(receiver, ast.Attribute) and receiver.attr == 'chapter'), (path, node.lineno)
