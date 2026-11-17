"""Assigned speakers own persistent voices; regeneration is scoped to one speaker."""
import importlib.util
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import random
import sys

from bs4 import BeautifulSoup
import pytest

spec = importlib.util.spec_from_file_location('audio_helpers', Path(__file__).parent / 'ui/test_phrase_ipa_regeneration.py')
helpers = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helpers)


def test_missing_voice_is_persisted_and_reused_without_llm(monkeypatch):
    record = {'tag': 'Sad_man', 'name': 'Sad man', 'voices': []}
    chars = SimpleNamespace(get_character=lambda *a: record, save_character=Mock(),
                            generate_gender_and_age=Mock())
    voices = {'male': {'id': 'am_adam', 'name': 'Adam', 'gender': 'male', 'accent': 'american'},
              'female': {'id': 'bf_emma', 'name': 'Emma', 'gender': 'female', 'accent': 'british'}}
    monkeypatch.setitem(sys.modules, 'artifact_editor.characters.voices', SimpleNamespace(KOKORO_VOICES=voices))
    configure = helpers.function('src/artifact_editor/llm.py', 'configure_character_voice',
                                 characters=chars, random=random)
    result = configure(object(), 'Sad_man', infer_demographics=False)
    assert sum(v['strength'] for v in result['voices']) == 100
    original = result['voices'][:]
    configure(object(), 'Sad_man', infer_demographics=False)
    assert result['voices'] == original
    assert chars.save_character.call_count == 1
    chars.generate_gender_and_age.assert_not_called()


def test_assignment_creates_voice_before_saving_speaker(tmp_path):
    phrase = BeautifulSoup('<phrase index="2" speaker="Narrator"/>', 'xml').phrase
    audio = tmp_path / 'old.wav'
    audio.write_bytes(b'old voice')
    voice = Mock(side_effect=lambda *a, **kw: None)
    chapter = SimpleNamespace(get_phrase=lambda _: phrase, save_phrase=Mock())
    assign = helpers.function('src/artifact_editor/phrases/phrases.py', 'save_speaker',
        name_to_tag=lambda s: s.replace(' ', '_'), get_wavfile_filename=lambda *a: str(audio),
        llm=SimpleNamespace(configure_character_voice=voice))
    assert assign(chapter, 2, 'Sad man')
    voice.assert_called_once_with(chapter, 'Sad_man', infer_demographics=False)
    assert phrase['speaker'] == 'Sad_man'
    assert not audio.exists()


def test_voice_failure_does_not_change_speaker():
    phrase = BeautifulSoup('<phrase index="2" speaker="Narrator"/>', 'xml').phrase
    chapter = SimpleNamespace(get_phrase=lambda _: phrase, save_phrase=Mock())
    assign = helpers.function('src/artifact_editor/phrases/phrases.py', 'save_speaker',
        name_to_tag=lambda s: s, llm=SimpleNamespace(configure_character_voice=Mock(side_effect=ValueError('no voice'))))
    with pytest.raises(ValueError):
        assign(chapter, 2, 'Sad_man')
    assert phrase['speaker'] == 'Narrator'
    chapter.save_phrase.assert_not_called()


def test_filtered_generation_does_not_speak_other_characters(tmp_path):
    xml = BeautifulSoup('<chapter><paragraph><phrase index="1" speaker="Sad_man">Hello</phrase><phrase index="2" speaker="Narrator">Other</phrase><phrase index="3" speaker="Sad_man" type="dinkus">***</phrase></paragraph></chapter>', 'xml')
    phrase = xml.find('phrase')
    wav = tmp_path / 'speech.wav'
    wav.write_bytes(b'old speech')
    def speak(*a, **kw):
        wav.write_bytes(b'new speech')
    generate = Mock(side_effect=speak)
    chapter = SimpleNamespace(chapterdir="Book/chapter/0001", get_xml=lambda: xml, get_phrase=lambda _: phrase,
                              save_phrase=Mock(), save_timing=Mock())
    all_audio = Mock()
    run = helpers.function('src/artifact_editor/phrases/phrases.py', 'generate_all_audio',
        name_to_tag=lambda s: s, get_wavfile_filename=lambda *a: str(wav),
        llm=SimpleNamespace(text_2_audio=generate), const=SimpleNamespace(FPS=25,
        COMFY_DIRS={'artifactserver': {'OUTPUT_DIR':str(tmp_path)}}),
        wav_pad_to_frame=Mock(), get_wav_duration=lambda _: 1,
        generate_audio=all_audio, recalculate_image_frames=Mock())
    run(chapter, force=True, speaker_tags={'Sad_man'})
    generate.assert_called_once()
    assert generate.call_args.args[2] == 'Sad_man'
    assert generate.call_args.kwargs['force'] is True
    all_audio.assert_not_called()
    chapter.save_timing.assert_called_once()


def test_book_regeneration_visits_only_matching_chapters(tmp_path, monkeypatch):
    root = tmp_path / 'Book/chapter'
    for number in [1, 2, 3]:
        (root / f'{number:04}').mkdir(parents=True)
    record = {'tag': 'Sad_man', 'name': 'Sad man', 'alias': ['Stranger']}
    xmls = {1: '<phrase speaker="Sad_man">one</phrase>',
            2: '<phrase speaker="Narrator">other</phrase>',
            3: '<phrase speaker="Stranger">three</phrase>'}
    chapters = {n: SimpleNamespace(number=n, author='Author', title='Book', language='english',
                    bookdir='Book', chapterdir=f'Book/chapter/{n:04}', load_xml=Mock(),
                    get_xml=lambda n=n: BeautifulSoup(xmls[n], 'xml')) for n in xmls}
    generate = Mock()
    monkeypatch.setitem(sys.modules, 'artifact_editor.chapter.chapter',
                        SimpleNamespace(Chapter=lambda a, t, n, language: chapters[n]))
    monkeypatch.setitem(sys.modules, 'artifact_editor.phrases.phrases', SimpleNamespace(generate_all_audio=generate))
    monkeypatch.setitem(sys.modules, 'artifact_editor.llm', SimpleNamespace(configure_character_voice=lambda *a, **k: record))
    chars = SimpleNamespace(get_all_characters=lambda *a, **k: {'Sad_man':record},
        character_names=lambda *a: ['Sad_man','Sad man','Stranger'],
        resolve_character_tag=lambda records, name: 'Sad_man', name_to_tag=lambda s:s.replace(' ','_'))
    run = helpers.function('src/artifact_editor/characters/audio.py', 'regenerate_character_audio',
                           characters=chars, const=SimpleNamespace(LIBRARY_DIR=str(tmp_path)),
                           Path=Path, nullcontext=nullcontext, FileLock=lambda path:nullcontext())
    assert run(chapters[1], 'Sad_man') == 2
    assert [call.args[0].number for call in generate.call_args_list] == [1,3]
    for call in generate.call_args_list:
        assert call.kwargs['force'] is True
        assert call.kwargs['batch'] is True
        assert call.kwargs['speaker_tags'] == {'Sad_man','Stranger'}


@pytest.mark.parametrize('ready', [True, False])
def test_regenerate_button_queues_only_when_worker_supports_it(monkeypatch, ready):
    import flask
    chapter = SimpleNamespace(author=SimpleNamespace(name='Author'), title='Book',
                              number=1, language='english')
    client = SimpleNamespace(get=lambda _: '1' if ready else None, close=Mock())
    enqueue = Mock()
    monkeypatch.setitem(sys.modules, 'redis', SimpleNamespace(Redis=lambda **kw:client))
    monkeypatch.setitem(sys.modules, 'task_queue', SimpleNamespace(VIDEO_QUEUE='video', enqueue_task=enqueue))
    monkeypatch.setitem(sys.modules, 'worker_startup', SimpleNamespace(ensure_worker_services=Mock()))
    route = helpers.function('src/artifact_editor/characters/views.py','regenerate_audio',
        characters=SimpleNamespace(get_character=lambda *a:{'tag':'Sad_man','name':'Sad man'}),
        htmx=SimpleNamespace(regenerate_audio_control=lambda chapter,tag,message:message))
    app = flask.Flask(__name__)
    nonce = '00000000000000000000000000000001'
    with app.test_request_context(method='POST',data={'nonce':nonce}):
        flask.g.chapter = chapter
        response = route('Sad_man')
    assert enqueue.call_count == int(ready)
    if ready:
        task = enqueue.call_args.args[2]
        assert task['action'] == 'character_regenerate_audio'
        assert task['character_tag'] == 'Sad_man'
        assert enqueue.call_args.kwargs['submission_nonce'] == nonce
        assert 'Queued' in response
    else:
        assert 'Waiting' in response
    client.close.assert_called_once()
