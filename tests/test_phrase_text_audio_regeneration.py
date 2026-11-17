"""Edited phrase text must replace stale IPA and speech outputs."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import glob
import hashlib
import sys

from bs4 import BeautifulSoup
import flask
import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('phrase_audio_helpers', ROOT / 'tests/ui/test_phrase_ipa_regeneration.py')
helpers = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helpers)


@pytest.mark.parametrize('changed', [True, False])
def test_wording_changes_clear_old_audio_and_ipa_but_formatting_edits_preserve_them(changed):
    phrase = BeautifulSoup('<paragraph><phrase index="2" src="old.wav" pronunciation="old" pronunciation_override="old" audio_generation_id="old">Old text.</phrase></paragraph>', 'xml').phrase
    chapter = SimpleNamespace(get_phrase=lambda _: phrase, save_phrase=Mock())
    save = helpers.function('src/artifact_editor/chapter/views.py', 'save_phrase_delta',
        t=SimpleNamespace(ops_to_internal_representation=lambda ops: SimpleNamespace(render_tree=lambda **_: ops[0]['insert'])),
        compress_ops=lambda ops: ops, _sync_sections_from_phrase_deltas=Mock())
    text = 'New phrase.' if changed else 'Old text.'
    app = flask.Flask(__name__)
    with app.test_request_context(method='POST', data={'delta': helpers.json.dumps({'ops': [{'insert': text}]})}):
        flask.g.chapter = chapter
        assert save(2) == ('', 200)
    assert phrase.get_text() == text
    for key in ('src', 'pronunciation', 'pronunciation_override', 'audio_generation_id'):
        assert (key not in phrase.attrs) == changed


def test_collects_new_ipa_from_app_visible_output_path(tmp_path):
    output = tmp_path / 'output'
    audio = output / 'audio'
    audio.mkdir(parents=True)
    flac = audio / 'speech_nonce.wav_00001_.flac'
    flac.write_bytes(b'new FLAC')
    (audio / 'speech_nonce.pronunciation.txt').write_text('new phrase IPA')
    wav = tmp_path / 'speech.wav'
    wav.write_bytes(b'old audio')
    wav.with_suffix('.pronunciation.txt').write_text('old IPA')
    convert = Mock(side_effect=lambda *args, **kwargs: wav.write_bytes(b'new audio'))
    collect = helpers.function('src/artifact_editor/llm.py', 'collect_text_2_audio',
        glob=glob, subprocess=SimpleNamespace(run=convert),
        const=SimpleNamespace(COMFY_DIRS={'artifactserver': {'OUTPUT_DIR': str(output)},
                                         'comfyui': {'OUTPUT_DIR': '/not-mounted-in-app'}}))
    collect({'wavfile': str(wav), 'outputfile': str(output / 'speech_nonce.wav')})
    assert wav.read_bytes() == b'new audio'
    assert wav.with_suffix('.pronunciation.txt').read_text() == 'new phrase IPA'
    assert str(flac) in convert.call_args.args[0]


def test_forced_generation_uses_unique_outputs_and_current_text(tmp_path, monkeypatch):
    incoming, output = tmp_path / 'input', tmp_path / 'output'
    incoming.mkdir(); output.mkdir()
    phrase = BeautifulSoup('<phrase index="2" pronunciation="stale">Edited phrase.</phrase>', 'xml').phrase
    chapter = SimpleNamespace(title='Book', get_pronunciation=lambda: {}, get_phrase_workflow=Mock(return_value='speech.json'))
    monkeypatch.setitem(sys.modules, 'artifact_editor.phrases.phrases', SimpleNamespace(clean_latex=lambda text: text))
    prepare = helpers.function('src/artifact_editor/llm.py', 'text_2_audio',
        hashlib=hashlib, configure_character_voice=lambda *args: {'voices': [{'id': 'am_adam', 'strength': 95}]},
        const=SimpleNamespace(COMFY_DIRS={'artifactserver': {'INPUT_DIR': str(incoming), 'OUTPUT_DIR': str(output)}, 'comfyui': {'OUTPUT_DIR': '/output'}}),
        _kokoro_language=lambda _: 'English (American)')
    first = prepare(chapter, phrase, 'Narrator', str(tmp_path / 'speech.wav'), force=True, queue_only=True)
    second = prepare(chapter, phrase, 'Narrator', str(tmp_path / 'speech.wav'), force=True, queue_only=True)
    assert first['outputfile'] != second['outputfile']
    environment = chapter.get_phrase_workflow.call_args.kwargs['template_environment']
    assert environment['TEXT'] == 'Edited phrase.'
    assert environment['PRONUNCIATION'] == ''
    assert second['pronunciation_file'] == str(output / 'audio' / (environment['PRONUNCIATION_FILENAME'] + '.txt'))
    assert second['label'] == '[Narrator] 2: Edited phrase.'


def test_dialogue_audio_label_uses_speaker_line_number_and_first_six_words(tmp_path, monkeypatch):
    incoming, output = tmp_path / 'input', tmp_path / 'output'
    incoming.mkdir(); output.mkdir()
    phrase = BeautifulSoup(
        '<phrase index="17">A very short opening followed by more words in the sentence.</phrase>',
        'xml',
    ).phrase
    chapter = SimpleNamespace(title='Book', get_pronunciation=lambda: {},
                              get_phrase_workflow=Mock(return_value='speech.json'))
    monkeypatch.setitem(sys.modules, 'artifact_editor.phrases.phrases',
                        SimpleNamespace(clean_latex=lambda text: text))
    prepare = helpers.function('src/artifact_editor/llm.py', 'text_2_audio',
        hashlib=hashlib, configure_character_voice=lambda *args: {'voices': [{'id': 'am_adam', 'strength': 95}]},
        const=SimpleNamespace(COMFY_DIRS={'artifactserver': {'INPUT_DIR': str(incoming), 'OUTPUT_DIR': str(output)}, 'comfyui': {'OUTPUT_DIR': '/output'}}),
        _kokoro_language=lambda _: 'English (American)')
    request = prepare(chapter, phrase, 'Dorothy', str(tmp_path / 'speech.wav'),
                      force=True, queue_only=True)
    assert request['label'] == '[Dorothy] 17: A very short opening followed by'


def test_audio_cache_is_separated_by_voice_blend(tmp_path, monkeypatch):
    incoming, output = tmp_path / 'input', tmp_path / 'output'
    incoming.mkdir(); output.mkdir()
    (output / 'speech.wav').write_bytes(b'old default voice')
    phrase = BeautifulSoup('<phrase index="2">Hello there.</phrase>', 'xml').phrase
    chapter = SimpleNamespace(title='Book', get_pronunciation=lambda: {},
                              get_phrase_workflow=Mock(return_value='speech.json'))
    monkeypatch.setitem(sys.modules, 'artifact_editor.phrases.phrases',
                        SimpleNamespace(clean_latex=lambda text:text))
    voice = {'voices':[{'id':'am_adam','strength':100}]}
    prepare = helpers.function('src/artifact_editor/llm.py', 'text_2_audio',
        hashlib=hashlib, configure_character_voice=lambda *args: voice,
        const=SimpleNamespace(COMFY_DIRS={'artifactserver':{'INPUT_DIR':str(incoming),'OUTPUT_DIR':str(output)},
                                         'comfyui':{'OUTPUT_DIR':'/output'}}),
        _kokoro_language=lambda _: 'English (American)')
    first = prepare(chapter,phrase,'Man',str(tmp_path/'speech.wav'),queue_only=True)
    voice['voices'] = [{'id':'bm_george','strength':100}]
    second = prepare(chapter,phrase,'Man',str(tmp_path/'speech.wav'),queue_only=True)
    assert not first['ready'] and not second['ready']
    assert first['outputfile'] != second['outputfile']
    assert not (tmp_path/'speech.wav').exists()
