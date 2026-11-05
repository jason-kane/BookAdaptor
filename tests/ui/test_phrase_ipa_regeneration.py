"""Manual phrase IPA survives speech generation, caching and UI refresh."""
import ast
import html
import json
import os
from pathlib import Path
import re
import shutil
import sys
from types import SimpleNamespace
from unittest.mock import Mock
import uuid

from bs4 import BeautifulSoup, Tag
import flask
import pytest

ROOT = Path(__file__).resolve().parents[2]


def function(relative, name, **values):
    path = ROOT / relative
    tree = ast.parse(path.read_text())
    node = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name)
    node.decorator_list = []
    scope = dict(json=json, os=os, html=html, re=re, shutil=shutil, uuid=uuid,
        Tag=Tag, Chapter=object, log=Mock(), request=flask.request, g=flask.g, **values)
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), scope)
    return scope[name]


@pytest.fixture
def phrase_ui(tmp_path, monkeypatch):
    image = BeautifulSoup('<chapter><paragraph index="0"><phrase index="2" speaker="Narrator" pronunciation="old">A name.</phrase></paragraph></chapter>', 'xml').phrase
    wav = tmp_path / 'phrase.wav'
    wav.write_bytes(b'old audio')
    image['src'] = wav.name
    chapter = SimpleNamespace(soup=image, get_phrase=lambda index: image if str(index) == '2' else None,
        save_phrase=Mock(), load_xml=Mock())
    phrases = SimpleNamespace(get_wavfile_filename=lambda *a: str(wav), wav_pad_to_frame=Mock(),
        get_wav_duration=lambda path: 2, recalculate_image_frames=Mock())
    def generate(chapter, phrase, speaker, filename, force):
        assert chapter.save_phrase.called, 'Correction must be saved before generation'
        assert force is True
        Path(filename).write_bytes(b'new audio')
        Path(filename.replace('.wav', '.pronunciation.txt')).write_text('generated IPA must not replace manual IPA')
    generate = Mock(side_effect=generate)
    htmx = SimpleNamespace(chapter_audio_controls=Mock(return_value='refreshed chapter panel'), phrase_editor=Mock(return_value='refreshed phrase panel'))
    regenerate = function('src/artifact_editor/phrases/views.py', 'regenerate_phrase',
        phrases=phrases, llm=SimpleNamespace(text_2_audio=generate), htmx=htmx,
        const=SimpleNamespace(FPS=25, COMFY_DIRS={'artifactserver': {'OUTPUT_DIR': str(tmp_path)}}))
    monkeypatch.setitem(sys.modules, 'artifact_editor.phrases.views', SimpleNamespace(regenerate_phrase=regenerate))
    save = function('src/artifact_editor/phrases/pronunciation/views.py', 'save')
    app = flask.Flask(__name__)
    app.testing = True
    app.add_url_rule('/ipa/<phrase_index>', view_func=save, methods=['PUT'])
    @app.before_request
    def bind():
        flask.g.chapter = chapter
    return SimpleNamespace(client=app.test_client(), phrase=image, chapter=chapter, phrases=phrases,
        generate=generate, htmx=htmx, wav=wav)


@pytest.mark.parametrize('target,body', [('chapter-audio-controls-2', 'refreshed chapter panel'), ('phrase-2', 'refreshed phrase panel')])
def test_edit_ipa_regenerates_audio_preserves_override_and_refreshes_timing(phrase_ui, target, body):
    ui = phrase_ui
    response = ui.client.put('/ipa/2', data={'pronunciation': ' mɒmbi '}, headers={'HX-Target': target})
    assert response.status_code == 200 and response.text == body
    assert ui.phrase['pronunciation_override'] == 'mɒmbi'
    assert ui.phrase['pronunciation'] == 'mɒmbi'
    assert ui.phrase['duration'] == '2' and int(ui.phrase['frames']) == 50
    assert ui.phrase['audio_generation_id']
    assert ui.wav.read_bytes() == b'new audio'
    ui.generate.assert_called_once()
    ui.phrases.recalculate_image_frames.assert_called_once_with(ui.chapter)


def test_clearing_ipa_restores_automatic_generation(phrase_ui):
    ui = phrase_ui
    ui.phrase['pronunciation_override'] = 'manual'
    response = ui.client.put('/ipa/2', data={'pronunciation': ' '})
    assert response.status_code == 200
    assert 'pronunciation_override' not in ui.phrase.attrs
    assert ui.phrase['pronunciation'] == 'generated IPA must not replace manual IPA'
    ui.generate.assert_called_once()


@pytest.mark.parametrize('path,data,status', [('/ipa/9', {'pronunciation': 'ipa'}, 404), ('/ipa/2', {}, 400)])
def test_invalid_edit_never_regenerates_audio(phrase_ui, path, data, status):
    response = phrase_ui.client.put(path, data=data)
    assert response.status_code == status
    phrase_ui.generate.assert_not_called()
    assert phrase_ui.wav.read_bytes() == b'old audio'


def test_audio_source_changes_after_regeneration():
    urls = []
    def url_for(endpoint, **values):
        urls.append(values)
        return '/audio.wav?v=' + values['v']
    render = function('src/artifact_editor/phrases/htmx.py', 'audio_player', url_for=url_for)
    phrase = BeautifulSoup('<paragraph index="0"><phrase src="audio.wav" audio_generation_id="new"/></paragraph>', 'xml').phrase
    chapter = SimpleNamespace(author=SimpleNamespace(name='Writer'), title='Book', number=1, language='english')
    assert '/audio.wav?v=new' in render(chapter, phrase)
    assert urls[0]['v'] == 'new'


def test_ipa_control_replaces_whole_audio_panel_and_escapes_input():
    render = function('src/artifact_editor/phrases/htmx.py', 'pronunciation_textarea', url_for=lambda *a, **kw: '/ipa/2')
    chapter = SimpleNamespace(author=SimpleNamespace(name='Writer'), title='Book', number=1, language='english')
    phrase = BeautifulSoup('<phrase index="2"/>', 'xml').phrase
    phrase['pronunciation'] = '"ɔ & æ'
    soup = BeautifulSoup(render(chapter, phrase, target='#chapter-audio-controls-2'), 'html.parser')
    control = soup.select_one('wa-textarea')
    assert control['value'] == '"ɔ & æ'
    assert control['hx-target'] == '#chapter-audio-controls-2'
    assert control['hx-swap'] == 'outerHTML' and control['hx-disabled-elt'] == 'this'


@pytest.mark.parametrize('override', ['', 'mɒmbi'])
def test_audio_workflow_prefers_only_user_corrected_ipa(tmp_path, monkeypatch, override):
    input_dir, output_dir = tmp_path / 'in', tmp_path / 'out'
    input_dir.mkdir(); output_dir.mkdir()
    phrase = BeautifulSoup('<phrase index="2" pronunciation="generated IPA">Mombi</phrase>', 'xml').phrase
    if override:
        phrase['pronunciation_override'] = override
    chapter = SimpleNamespace(title='Book', get_pronunciation=lambda: {}, get_phrase_workflow=Mock(return_value='phrase.json'))
    monkeypatch.setitem(sys.modules, 'artifact_editor.phrases.phrases', SimpleNamespace(clean_latex=lambda text: text))
    prepare = function('src/artifact_editor/llm.py', 'text_2_audio',
        characters=SimpleNamespace(get_character=lambda *a: {'voices': [{'id': 'am_adam', 'strength': 95}]}),
        const=SimpleNamespace(COMFY_DIRS={'artifactserver': {'INPUT_DIR': str(input_dir), 'OUTPUT_DIR': str(output_dir)}, 'comfyui': {'OUTPUT_DIR': '/output'}}),
        _kokoro_language=lambda character: 'English (American)')
    # An old output cache must not win over the manual correction.
    if override:
        (output_dir / 'speech.wav').write_bytes(b'stale audio')
    result = prepare(chapter, phrase, 'Narrator', str(tmp_path / 'speech.wav'), queue_only=True)
    assert result['ready'] is False
    environment = chapter.get_phrase_workflow.call_args.kwargs['template_environment']
    assert environment['PRONUNCIATION'] == override
    assert environment['TEXT'] == 'Mombi'


def test_phrase_workflow_passes_manual_ipa_to_kokoro_widget(tmp_path):
    template = tmp_path / 'template.json'
    template.write_text(json.dumps({'nodes': [{'type': 'KokoroGenerator', 'widgets_values': ['text', 1, 'English (American)']}]}))
    chapter = SimpleNamespace(nice='test')
    build = function('src/artifact_editor/chapter/chapter.py', 'get_phrase_workflow_by_filename',
        const=SimpleNamespace(COMFY_DIRS={'artifactserver': {'WORKFLOWS_DIR': str(tmp_path)}}),
        as_str=str)
    name = build(chapter, BeautifulSoup('<phrase index="2"/>', 'xml').phrase, str(template), {'TEXT': 'Mombi', 'PRONUNCIATION': 'mɒmbi'})
    node = json.loads((tmp_path / name).read_text())['nodes'][0]
    assert node['widgets_values'][3] == 'mɒmbi'
    assert node['widgets_values_named']['pronunciation'] == 'mɒmbi'


@pytest.mark.parametrize('language,code', [('English (American)', 'en-us'), ('French', 'fr-fr'), ('Japanese', 'ja')])
def test_kokoro_manual_ipa_bypasses_generated_phonemes(tmp_path, language, code):
    path = ROOT / 'comfy/ComfyUI/custom_nodes/comfyui-kokoro/ComfyUIKokoro.py'
    if not path.exists():
        pytest.skip('Installed Kokoro custom node is needed for the synthesis-boundary test')
    cls = next(n for n in ast.parse(path.read_text()).body if isinstance(n, ast.ClassDef) and n.name == 'KokoroGenerator')
    method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == 'generate')
    model = Mock()
    model.create.return_value = (object(), 24000)
    from unittest.mock import MagicMock
    tensor = MagicMock()
    g2p = Mock(side_effect=AssertionError('Manual IPA must bypass G2P'))
    scope = dict(logger=Mock(), download_model=Mock(), download_voices=Mock(),
        supported_languages={language: code}, Kokoro=Mock(return_value=model),
        en=SimpleNamespace(G2P=g2p), torch=SimpleNamespace(from_numpy=lambda _: tensor),
        os=os, json=json, OVERRIDE_FN=str(tmp_path / 'unused.json'))
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(path), 'exec'), scope)
    voice = object()
    audio, ipa = scope['generate'](SimpleNamespace(node_dir=str(tmp_path), model_path='model', voices_path='voices'),
        'Original text', {'speaker': voice}, 1.1, language, pronunciation=' mɒmbi ')
    model.create.assert_called_once_with('mɒmbi', voice=voice, is_phonemes=True, speed=1.1, lang=code)
    assert ipa == 'mɒmbi' and audio['sample_rate'] == 24000
    g2p.assert_not_called()


def test_kokoro_cache_identity_includes_pronunciation():
    path = ROOT / 'comfy/ComfyUI/custom_nodes/comfyui-kokoro/ComfyUIKokoro.py'
    if not path.exists():
        pytest.skip('Installed Kokoro custom node is needed for the synthesis-boundary test')
    cls = next(n for n in ast.parse(path.read_text()).body if isinstance(n, ast.ClassDef) and n.name == 'KokoroGenerator')
    method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == 'IS_CHANGED')
    method.decorator_list = []
    scope = {}
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(path), 'exec'), scope)
    speaker = {'speaker': SimpleNamespace(tobytes=lambda: b'voice')}
    changed = scope['IS_CHANGED']
    assert changed(None, 'Mombi', speaker, 1, 'English', 'mɒmbi') != changed(None, 'Mombi', speaker, 1, 'English', 'mɑmbi')
    assert changed(None, 'Mombi', speaker, 1, 'English', 'mɒmbi') == changed(None, 'Mombi', speaker, 1, 'English', 'mɒmbi')
