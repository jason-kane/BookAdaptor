"""Speaker assignment can explicitly review every phrase before audio generation."""
import ast
import json
import os
import re
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from bs4 import BeautifulSoup
from flask import Flask, render_template
from markupsafe import escape

ROOT = Path(__file__).resolve().parents[1]


def functions(path, names, **scope):
    tree = ast.parse((ROOT / path).read_text())
    tree.body = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    exec(compile(tree, path, 'exec'), scope)
    return scope


def test_assign_reviews_every_phrase_and_invalidates_changed_audio(tmp_path, monkeypatch):
    xml = BeautifulSoup('<chapter><paragraph index="1">'
        '<phrase index="1" speaker="Jack" speaker_source="manual">Hello</phrase>'
        '<phrase index="2" speaker="Narrator">Jack said hello.</phrase>'
        '</paragraph></chapter>', 'xml')
    audio = tmp_path / 'phrase.wav'
    stale = [audio, Path(str(audio) + '.pronunciation'), Path(str(audio) + '.pronunciation.txt')]
    for path in stale:
        path.write_text('stale')
    monkeypatch.setitem(sys.modules, 'artifact_editor.phrases.phrases',
                        SimpleNamespace(get_wavfile_filename=lambda *a: str(audio)))
    review = [{'index': '1', 'speaker': 'Jack Pumpkinhead', 'evidence': 'Hello'},
              {'index': '2', 'speaker': 'Narrator', 'evidence': 'Jack said hello.'}]
    llm = SimpleNamespace(json_prompt=Mock(return_value=review))
    catalog = Mock(return_value={'Jack_Pumpkinhead': {
        'name': 'Jack Pumpkinhead', 'tag': 'Jack_Pumpkinhead', 'alias': ['Jack']}})
    chapter = SimpleNamespace(get_xml=lambda: xml, save_phrases=Mock())
    scope = functions('src/artifact_editor/characters/characters.py',
        {'assign_phrase_speakers', 'name_to_tag', 'character_names'},
        os=os, re=re, json=json, llm=llm, get_all_characters=catalog)
    changed = scope['assign_phrase_speakers'](chapter, reassign_all=True)
    assert changed == 1
    assert llm.json_prompt.call_args_list[0].kwargs['label'] == 'Assign Characters · Identify speakers · batch 1/1'
    assert llm.json_prompt.call_args_list[1].kwargs['label'] == 'Assign Characters · Verify speakers · batch 1/1'
    catalog.assert_called_once_with(chapter, is_global=True)
    targets = llm.json_prompt.call_args_list[0].args[0].split('Target phrases: ')[1].split('\n')[0]
    assert [item['index'] for item in json.loads(targets)] == ['1', '2']
    assert xml.phrase['speaker'] == 'Jack_Pumpkinhead'
    assert all(phrase['speaker_source'] == 'llm' for phrase in xml.find_all('phrase'))
    assert all(not path.exists() for path in stale)
    chapter.save_phrases.assert_called_once()


def test_timeline_assignment_button_sends_chapter_identity_before_audio():
    scope = functions('src/artifact_editor/chapter/htmx.py',
        {'assign_characters_button', 'missing_asset_button'}, escape=escape, websocket_url=lambda: '/ws')
    chapter = SimpleNamespace(author=SimpleNamespace(name='Writer'), title='Book', number=2,
                              language='english', kwargs={})
    button = scope['assign_characters_button'](chapter)
    app = Flask(__name__, template_folder=str(ROOT / 'src/artifact_editor/chapter/templates'))
    app.jinja_env.globals['url_for'] = lambda *a, **kw: '/action'
    with app.app_context():
        html = render_template('chapter_timeline_actions.html', chapter=chapter,
                               assign_characters_button=button, generate_missing_audio_button='Add Missing Audio')
    soup = BeautifulSoup(html, 'html.parser')
    group = soup.select_one('[data-chapter-action="chapter_assign_characters"]')
    payload = json.loads(group.select_one('wa-button')['hx-vals'])
    assert payload['chapter_number'] == 2
    assert payload['author'] == 'Writer'
    assert payload['action'] == 'chapter_assign_characters'
    assert group.select_one('wa-button').text == 'Assign Characters'
    assert html.index('Assign Characters') < html.index('Add Missing Audio')


def test_assignment_worker_reviews_all_phrases_and_reports_unresolved(monkeypatch):
    tree = ast.parse((ROOT / 'src/websocket/server.py').read_text())
    worker = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'run_video_job')
    branch = next(node for node in ast.walk(worker) if isinstance(node, ast.If)
                  and ast.unparse(node.test) == "task.get('action') == 'chapter_assign_characters'")
    # Isolate the job branch from deployment imports while executing its real code.
    wrapper = ast.parse('def run(chapter, client, task):\n    job_id = task["job_id"]\n').body[0]
    wrapper.body.append(branch)
    scope = {'publish_progress': Mock()}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[wrapper], type_ignores=[])), '<worker>', 'exec'), scope)
    assign = Mock(return_value=2)
    monkeypatch.setitem(sys.modules, 'artifact_editor.characters.characters',
                        SimpleNamespace(assign_phrase_speakers=assign))
    chapter = SimpleNamespace(get_xml=lambda: BeautifulSoup(
        '<chapter><phrase speaker_source="llm_unresolved"/></chapter>', 'xml'))
    task = {'action': 'chapter_assign_characters', 'job_id': 'assignment'}
    scope['run'](chapter, 'client', task)
    assert assign.call_args.args == (chapter,)
    assert assign.call_args.kwargs['reassign_all'] is True
    assign.call_args.kwargs['progress'](50, 'Reviewing phrases')
    assert scope['publish_progress'].call_args.args == ('client', 'assignment', 50, 'Reviewing phrases')
    completion = scope['publish_progress'].call_args_list[0].args
    assert completion[4:] == ('complete', 'chapter_assign_characters')
    assert '1 phrases need speaker review' in completion[3]


def test_websocket_legacy_polling_is_bounded_between_receives():
    tree = ast.parse((ROOT / 'src/websocket/server.py').read_text())
    handler = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                   and node.name == 'handle_websocket')
    scans = [node for node in ast.walk(handler) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Attribute) and node.func.attr in ('scan', 'scan_iter')]
    assert len(scans) == 1
    assert scans[0].func.attr == 'scan'
    assert {kw.arg: ast.unparse(kw.value) for kw in scans[0].keywords}['count'] == '200'


def test_interrupted_dialogue_keeps_attribution_in_narrator_voice(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, 'artifact_editor.phrases.phrases',
                        SimpleNamespace(get_wavfile_filename=lambda *a: str(tmp_path / 'absent.wav')))
    for opening, closing in [('"', '"'), ('“', '”')]:
        xml = BeautifulSoup('<chapter><paragraph index="1">'
            f'<phrase index="1" speaker="John">{opening}Alpha,{closing}</phrase>'
            '<phrase index="2" speaker="John">John said, voice full of emotion,</phrase>'
            f'<phrase index="3" speaker="John">{opening}Beta too.{closing}</phrase>'
            '</paragraph></chapter>', 'xml')
        # Both reviews confidently make the reported mistake, across batches.
        def review(prompt, **kwargs):
            targets = json.loads(prompt.rsplit('Target phrases: ', 1)[1].split('\n')[0])
            return [{'index': t['index'], 'speaker': 'John', 'evidence': t['text']} for t in targets]
        llm = SimpleNamespace(json_prompt=Mock(side_effect=review))
        chapter = SimpleNamespace(get_xml=lambda: xml, save_phrases=Mock())
        scope = functions('src/artifact_editor/characters/characters.py',
            {'assign_phrase_speakers', 'name_to_tag', 'character_names'},
            os=os, re=re, json=json, llm=llm,
            get_all_characters=lambda *a, **kw: {'John': {'name': 'John'}})
        assert scope['assign_phrase_speakers'](chapter, batch_size=1, reassign_all=True) == 1
        assert [p['speaker'] for p in xml.find_all('phrase')] == ['John', 'Narrator', 'John']
        assert all(p['speaker_source'] == 'llm' for p in xml.find_all('phrase'))
