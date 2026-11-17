"""One Shot may render automatic speakers without claiming human review."""
import ast
from contextvars import ContextVar
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock

from bs4 import BeautifulSoup
import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def prepared_chapter(monkeypatch, tmp_path):
    soup = BeautifulSoup('<chapter><paragraph index="0"><image index="0"/>'
                         '<phrase index="0" speaker="Tip">Hello.</phrase></paragraph></chapter>', 'xml')
    still = tmp_path / 'still.png'
    still.touch()
    chapter = SimpleNamespace(
        config={'character_attributions_verified': False},
        chapterdir='author/book/chapter/0011', language='english',
        get_xml=lambda: soup, images=lambda: [soup.image],
        get_image_filename=lambda _: str(still), get_paragraph_dir=lambda _: 'paragraph',
    )
    monkeypatch.setitem(sys.modules, 'const', SimpleNamespace(LIBRARY_DIR=str(tmp_path), FPS=25))
    monkeypatch.setitem(sys.modules, 'artifact_editor.chapter.timeline', SimpleNamespace(
        phrase_audio_missing=lambda *args: False, image_timings=lambda _: {'0': {'frames': 100}}))
    monkeypatch.setitem(sys.modules, 'artifact_editor.images.animation_source', SimpleNamespace(
        animation_frame_count=lambda *args: 100))
    modules = []
    for name in ('readiness', 'workflow'):
        key = f'artifact_editor.chapter.{name}'
        spec = importlib.util.spec_from_file_location(key, ROOT / f'src/artifact_editor/chapter/{name}.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        monkeypatch.setitem(sys.modules, key, module)
        modules.append(module)
    return chapter, *modules


def test_one_shot_bypass_keeps_human_review_unchecked(prepared_chapter):
    chapter, readiness, workflow = prepared_chapter
    with pytest.raises(ValueError, match='Must verify all character attributions first'):
        workflow.begin_step(chapter, 'textoviva_rerender')
    workflow.begin_step(chapter, 'textoviva_rerender', require_verified_speakers=False)
    assert chapter.config['character_attributions_verified'] is False
    assert readiness.video_prerequisite(chapter) == 'Must verify all character attributions first'


@pytest.mark.parametrize('missing,expected', [
    ('text', 'Must import chapter first'),
    ('audio', 'Must finish generating missing audio first'),
    ('image', 'Must finish generating missing images first'),
    ('animation', 'Must finish generating missing animation first'),
])
def test_one_shot_bypass_still_requires_media(prepared_chapter, monkeypatch, missing, expected):
    chapter, _, workflow = prepared_chapter
    if missing == 'text':
        chapter.get_xml().phrase.decompose()
    elif missing == 'audio':
        monkeypatch.setattr(sys.modules['artifact_editor.chapter.timeline'], 'phrase_audio_missing', lambda *args: True)
    elif missing == 'image':
        chapter.get_image_filename = lambda _: None
    else:
        monkeypatch.setattr(sys.modules['artifact_editor.images.animation_source'], 'animation_frame_count', lambda *args: 50)
    with pytest.raises(ValueError, match=expected):
        workflow.begin_step(chapter, 'textoviva_rerender', require_verified_speakers=False)


@pytest.mark.parametrize('action,expected_state', [
    ('textoviva_one_shot', 'complete'), ('textoviva_rerender', 'failed'),
])
def test_render_job_limits_checkbox_bypass_to_one_shot(prepared_chapter, monkeypatch, action, expected_state):
    chapter, _, _ = prepared_chapter
    imported, assets, rendered = Mock(), Mock(), Mock()
    stubs = {
        'artifact_editor.library.storage': SimpleNamespace(guard_task=lambda _: None),
        'artifact_editor.author.author': SimpleNamespace(Author=lambda _: None),
        'artifact_editor.chapter.chapter': SimpleNamespace(Chapter=lambda **kwargs: chapter),
        'artifact_editor.chapter.views': SimpleNamespace(import_chapter_content=imported),
        'artifact_editor.chapter.typesetting': SimpleNamespace(enqueue_typesetting=Mock()),
        'artifact_editor.video.video': SimpleNamespace(book_to_video=Mock()),
        'artifact_editor.textoviva.jobs': SimpleNamespace(rerender_chapter=rendered),
        'comfy': SimpleNamespace(workflow_progress=ContextVar('workflow_progress', default=None)),
        'task_queue': SimpleNamespace(RENDER_ACTIONS={'textoviva_one_shot', 'textoviva_rerender'}),
    }
    for name, module in stubs.items():
        monkeypatch.setitem(sys.modules, name, module)
    updates = []
    scope = dict(os=__import__('os'), log=Mock(), redis=SimpleNamespace(RedisError=RuntimeError),
                 traceback=__import__('traceback'), run_chapter_add_all=assets,
                 publish_progress=lambda *args, **kwargs: updates.append((args, kwargs)),
                 publish_comfy_progress=Mock())
    node = next(node for node in ast.parse((ROOT / 'src/websocket/server.py').read_text()).body
                if isinstance(node, ast.FunctionDef) and node.name == 'run_video_job')
    exec(compile(ast.Module(body=[node], type_ignores=[]), '<render-job>', 'exec'), scope)
    scope['run_video_job'](Mock(), dict(job_id='one-shot-readiness', action=action,
        author='Writer', title='Book', chapter_number=11, language='english'))
    assert updates[-1][0][4] == expected_state
    assert rendered.call_count == imported.call_count == assets.call_count == (expected_state == 'complete')
    assert chapter.config['character_attributions_verified'] is False


def test_one_shot_player_restores_recent_failure_and_debug_details(monkeypatch):
    import json
    import re
    import shutil
    import uuid
    import flask
    import pytest

    entry = {
        'job_id': 'failed-one-shot', 'action': 'textoviva_one_shot',
        'author': 'Writer', 'title': 'Book', 'chapter_number': 11,
        'language': 'english', 'state': 'failed', 'message': 'Animation failed',
        'debug_details': 'Traceback (most recent call last): animation error',
    }
    client = Mock()
    client.get.return_value = None
    client.lrange.side_effect = lambda key, *_: [json.dumps(entry)] if key == 'chapter_render:recent' else []
    client.hgetall.return_value = {}
    monkeypatch.setitem(sys.modules, 'redis', SimpleNamespace(Redis=lambda **_: client, RedisError=RuntimeError))
    monkeypatch.setitem(sys.modules, 'task_queue', SimpleNamespace(RENDER_QUEUE='render-queue'))
    monkeypatch.setitem(sys.modules, 'artifact_editor.chapter.workflow', SimpleNamespace(
        prerequisite_message=lambda *_: None))
    chapter = SimpleNamespace(
        author=SimpleNamespace(name='Writer'), title='Book', number=11,
        language='english', kwargs={}, get_aspect=lambda: 'widescreen',
    )
    node = next(n for n in ast.parse((ROOT / 'src/artifact_editor/textoviva/views.py').read_text()).body
                if isinstance(n, ast.FunctionDef) and n.name == 'player_stack')
    scope = {
        'Chapter': object, 'Tag': object, 'bp': SimpleNamespace(get=lambda *_: lambda fn: fn),
        'redis': sys.modules['redis'], 'os': __import__('os'), 'json': json,
        'escape': __import__('html').escape, 'uuid': uuid, 'url_for': lambda *a, **k: '/asset',
        'websocket_url': lambda: '/ws', 'g': SimpleNamespace(), 'request': SimpleNamespace(),
        'render_template': lambda *a, **k: '', 'const': SimpleNamespace(FPS=25),
        'quote': __import__('urllib.parse', fromlist=['quote']).quote,
    }
    exec(compile(ast.Module(body=[node], type_ignores=[]), '<player-stack>', 'exec'), scope)
    markup = scope['player_stack'](chapter, None, embedded=True)
    assert 'Animation failed' in markup
    assert 'data-video-state="failed"' in markup
    assert 'data-video-job="failed-one-shot"' in markup
