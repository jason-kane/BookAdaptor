"""UI route tests isolate service imports, preserving real Flask decorators and Jinja.

Application imports initialize deployment paths, databases and model services. Load
view definitions with explicit boundary collaborators instead; never start workers
or read the user's Library while collecting these tests.
"""
import ast
import glob
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from types import SimpleNamespace
import unicodedata
import uuid
from datetime import datetime
from typing import Any
from unittest.mock import Mock

import flask
from jinja2 import ChoiceLoader, FileSystemLoader
import pytest

ROOT = Path(__file__).resolve().parents[2]


def load_views(relative, **collaborators):
    path = ROOT / 'src' / relative
    tree = ast.parse(path.read_text())
    # Keep function-local imports: these represent the actual service boundary.
    tree.body = [n for n in tree.body if not isinstance(n, (ast.Import, ast.ImportFrom))
                 or isinstance(n, ast.ImportFrom) and n.module == '__future__']
    scope = dict(vars(flask), __name__='ui_test_' + path.parent.name,
                 __file__=str(path), os=os, glob=glob, json=json, re=re,
                 shutil=shutil, tempfile=tempfile, uuid=uuid, datetime=datetime,
                 Path=Path, Any=Any, unicodedata=unicodedata,
                 logger=SimpleNamespace(log=lambda _: Mock()), **collaborators)
    exec(compile(tree, str(path), 'exec'), scope)
    return SimpleNamespace(**scope), scope


def make_app(views, prefix):
    app = flask.Flask(__name__)
    app.testing = True
    app.register_blueprint(views.bp, url_prefix=prefix)
    app.jinja_loader = ChoiceLoader([
        FileSystemLoader(str(Path(views.__file__).parent / 'templates')),
        FileSystemLoader(str(ROOT / 'src/artifact_editor/templates')),
        FileSystemLoader(str(ROOT / 'src/styler/templates')),
    ])
    app.jinja_env.globals['websocket_url'] = lambda: 'ws://test/ws'
    return app


@pytest.fixture
def workflow_ui(tmp_path):
    templates = tmp_path / 'templates'
    workflows = tmp_path / 'workflows'
    templates.mkdir()
    workflows.mkdir()
    chapter = SimpleNamespace(get_image=Mock(return_value=object()),
        get_workflow_by_filename=Mock(side_effect=lambda **kw: str(workflows / kw['force_name'])), kwargs={})
    views, scope = load_views('artifact_editor/workflow_templates/views.py',
        const=SimpleNamespace(COMFY_DIRS={'artifactserver': {
            'WORKFLOW_TEMPLATES_DIR': str(templates), 'WORKFLOWS_DIR': str(workflows)}},
            COMFYUI_UI_URL='http://comfy.test/'),
        md=None, markdown_it=SimpleNamespace(MarkdownIt=lambda *a: None),
        Chapter=Mock(return_value=chapter), Author=Mock())
    app = make_app(views, '/workflow_templates')
    return SimpleNamespace(client=app.test_client(), views=views, scope=scope,
                           templates=templates, workflows=workflows, chapter=chapter)


@pytest.fixture
def library_ui(tmp_path):
    def author(name):
        return SimpleNamespace(name=name, pretty_name=name,
            authordir=str(tmp_path / name), books=[], inventory_books=Mock())
    views, scope = load_views('artifact_editor/library/views.py',
        const=SimpleNamespace(LIBRARY_DIR=str(tmp_path), SOUND_DIR=str(tmp_path / 'sounds')),
        Author=Mock(side_effect=author), tools=SimpleNamespace(
            get_bookdir=lambda a, t: f'{a}/{t}',
            get_chapterdir=lambda a, t, n: f'{a}/{t}/chapter/{n:04d}'))
    app = make_app(views, '/library')
    # Navigation outside this isolated blueprint is rendered as a stable endpoint URL.
    app.jinja_env.globals['url_for'] = lambda endpoint, **kw: '/' + endpoint + '?' + '&'.join(f'{k}={v}' for k,v in kw.items())
    return SimpleNamespace(client=app.test_client(), views=views, scope=scope, root=tmp_path)


@pytest.fixture
def theater_ui(tmp_path, monkeypatch):
    import sys
    gpu = SimpleNamespace(submit=Mock())
    queue = SimpleNamespace(VIDEO_QUEUE='video', enqueue_task=Mock())
    workers = SimpleNamespace(ensure_worker_services=Mock())
    monkeypatch.setitem(sys.modules, 'gpu_tasks', gpu)
    monkeypatch.setitem(sys.modules, 'task_queue', queue)
    monkeypatch.setitem(sys.modules, 'worker_startup', workers)
    monkeypatch.setitem(sys.modules, 'artifact_editor.theater.rendering',
                        SimpleNamespace(prepare_chapter=Mock()))
    views, scope = load_views('artifact_editor/theater/views.py',
        const=SimpleNamespace(THEATER_DIR=str(tmp_path), LIBRARY_DIR=str(tmp_path)),
        redis=SimpleNamespace(Redis=Mock()), MaintNotificationsConfig=Mock(),
        read_chapter_source=lambda path: None)
    app = make_app(views, '/theater')
    client = app.test_client()
    profile = next(iter(views.profiles()))
    response = client.post('/theater/projects', data={'raw_story': 'A traveler finds a door at sea.', 'director_profile': profile})
    assert response.status_code == 302
    url = response.headers['Location']
    project_id = url.rsplit('/', 1)[-1]
    return SimpleNamespace(client=client, views=views, scope=scope, root=tmp_path,
        project_id=project_id, url=url, gpu=gpu, queue=queue, workers=workers,
        folder=tmp_path / project_id, profile=profile)


@pytest.fixture
def styler_ui(tmp_path, monkeypatch):
    import sys
    from collections import defaultdict
    import math
    from bs4 import BeautifulSoup
    style_data = {'name': 'Ink'}
    style = SimpleNamespace(style_tag='ink', key='ink', style=style_data,
        name='Ink', tags=['ink'], save_to_redis=Mock(), set_engine_tier=Mock(),
        remove_tag=Mock(), engine_tier_badge=Mock(return_value='<span>Tier</span>'), as_card=Mock(return_value='<article>Ink card</article>'))
    store = SimpleNamespace(r=Mock(), Style=Mock(return_value=style),
        add_tag_to_style=Mock(), all_styles=Mock(return_value=[]), tier_cosmetics=list(range(8)),
        as_id=lambda s: s.lower().replace(' ', '_'))
    tree = {'topics': [{'name': 'Art', 'description': 'Visual art', 'categories': [
        {'name': 'Drawing', 'description': 'Drawn marks', 'tags': ['ink', 'pen']}]}], 'tag_count': 2}
    taxonomy = SimpleNamespace(cached_tree=Mock(return_value=tree))
    topic_jobs = SimpleNamespace(enqueue_taxonomy_rebuild=Mock(return_value={'state': 'queued'}))
    backfill = SimpleNamespace(stop=Mock(), start=Mock())
    jobs = SimpleNamespace(clear_queued_samples=Mock(return_value=0))
    for name, module in [('styler.topic_taxonomy', taxonomy), ('styler.topic_jobs', topic_jobs),
                         ('styler.backfill', backfill), ('styler.jobs', jobs)]:
        monkeypatch.setitem(sys.modules, name, module)
    identity = lambda **kw: lambda fn: fn
    views, scope = load_views('styler/views.py',
        __package__='styler', flask=flask, styles=store,
        const=SimpleNamespace(LIBRARY_DIR=str(tmp_path), COMFY_DIRS={
            'artifactserver': {'OUTPUT_DIR': str(tmp_path)}}),
        htmx=SimpleNamespace(lora_selector=lambda: ''),
        cache=SimpleNamespace(memoize=identity, cached=identity),
        defaultdict=defaultdict, math=math, BeautifulSoup=BeautifulSoup,
        as_id=store.as_id)
    app = make_app(views, '/styles')
    return SimpleNamespace(client=app.test_client(), views=views, scope=scope,
        store=store, style=style, taxonomy=taxonomy, tree=tree, topic_jobs=topic_jobs,
        root=tmp_path, backfill=backfill, jobs=jobs)


@pytest.fixture
def chapter_ui(tmp_path, monkeypatch):
    import sys
    import importlib.util
    from bs4 import BeautifulSoup
    path = ROOT / 'src/artifact_editor/chapter/timeline.py'
    spec = importlib.util.spec_from_file_location('artifact_editor.chapter.timeline', path)
    timeline = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(timeline)
    monkeypatch.setitem(sys.modules, spec.name, timeline)
    monkeypatch.setitem(sys.modules, 'artifact_editor.phrases.htmx', SimpleNamespace(
        chapter_timing_updates=lambda chapter: '<div>updated timing</div>'))
    tree = BeautifulSoup('<chapter><paragraph index="0"><image index="0" description="A door" src="door.png" animated_src="door.mp4"/><phrase index="0" duration="2" frames="48">A door.</phrase><silence index="0" duration="1" frames="24"/></paragraph></chapter>', 'xml')
    chapter = SimpleNamespace(language='english', config={}, mood='', theme='',
        get_xml=lambda: tree, get_chapterdir=lambda: 'book/chapter/0001',
        chapterdir='book/chapter/0001', load_delta=Mock(return_value={'ops': [{'insert': 'A door.\n'}]}),
        load_txt=Mock(return_value='A door.'), save_config=Mock(), set_chapter_title=Mock(),
        save_timeline=Mock(), save_images=Mock(), save_timing=Mock(),
        speakers=lambda: ['Narrator'], kwargs={})
    identity = lambda *a: None
    views, scope = load_views('artifact_editor/chapter/views.py',
        __package__='artifact_editor.chapter', flask=flask, BeautifulSoup=BeautifulSoup,
        TranslatorQuillJS=Mock(), LineRenderLaTeX=SimpleNamespace(font_sizes={}),
        chapter_url_defaults_helper=identity, chapter_url_value_preprocessor=identity,
        Chapter=SimpleNamespace, const=SimpleNamespace(LIBRARY_DIR=str(tmp_path), FPS=24),
        htmx=SimpleNamespace(chapter_mood_widget=lambda ch: ch.mood,
                             chapter_theme_widget=lambda ch: ch.theme))
    app = make_app(views, '/chapter')
    @app.before_request
    def bind_chapter():
        flask.g.chapter = chapter
    return SimpleNamespace(client=app.test_client(), views=views, scope=scope,
        chapter=chapter, tree=tree, root=tmp_path)


@pytest.fixture
def book_ui(tmp_path, monkeypatch):
    from artifact_editor import loras
    from html import escape
    author = SimpleNamespace(name='Writer', pretty_name='Writer')
    book = SimpleNamespace(bookdir=str(tmp_path), config={}, title='Book', subtitle='',
        bookurl='/library/Writer/Book/', set_book_metadata_url='/library/Writer/Book/', save_config=Mock(),
        input_field=lambda label, name, value: f'<input name="{name}" value="{value}">',
        choice_field=lambda label, name, value, choices: f'<select name="{name}"></select>')
    store = SimpleNamespace(get_all_characters=Mock(return_value={}), save_characters=Mock(),
                            name_to_tag=lambda s: s.lower().replace(' ', '_'))
    constants = SimpleNamespace(LIBRARY_DIR=str(tmp_path), DEFAULT_IMAGE_TEMPLATE='t2i.zit-paragraph',
        COMFY_DIRS={'artifactserver': {'WORKFLOW_TEMPLATES_DIR': str(ROOT / 'templates')}})
    import sys
    monkeypatch.setitem(sys.modules, 'const', constants)
    views, scope = load_views('artifact_editor/book/views.py',
        const=constants,
        Author=Mock(return_value=author), Book=Mock(return_value=book), Chapter=Mock(),
        loras=SimpleNamespace(normalize=loras.normalize, catalog=lambda: [{'filename': 'a', 'name': 'A'}, {'filename': 'b', 'name': 'B'}]), escape=escape,
        character_store=store, character_htmx=SimpleNamespace(character_data=lambda *a: '<article>Character</article>'),
        chapter_htmx=SimpleNamespace(chapter_list=lambda **kw: '<article>Chapter One</article>'),
        styles_htmx=SimpleNamespace(get_style_choices=lambda: []),
        htmx=SimpleNamespace(get_aspect_ratio_choices=lambda: []))
    app = make_app(views, '/library/<author>/<title>')
    app.jinja_env.globals['url_for'] = lambda endpoint, **kw: '/' + endpoint
    return SimpleNamespace(client=app.test_client(), views=views, scope=scope,
                           book=book, store=store, url='/library/Writer/Book')


@pytest.fixture
def author_ui(tmp_path):
    author = SimpleNamespace(name='Writer', pretty_name='Writer', authordir=str(tmp_path))
    views, scope = load_views('artifact_editor/author/views.py', Author=Mock(return_value=author))
    app = make_app(views, '/library/<author>')
    app.jinja_env.globals['url_for'] = lambda endpoint, **kw: '/' + endpoint
    return SimpleNamespace(client=app.test_client(), root=tmp_path, scope=scope)


@pytest.fixture
def pronunciation_ui(tmp_path):
    dictionary = {'door': {'word': 'Door', 'pronunciation': 'dɔːr', 'after': ''}}
    store = SimpleNamespace(get_global_pronunciations=lambda chapter: dictionary,
        save_global_pronunciations=Mock(), word_to_key=lambda word: word.lower())
    chapter = SimpleNamespace(bookdir='Writer/Book')
    views, scope = load_views('artifact_editor/phrases/pronunciation/views.py',
        const=SimpleNamespace(LIBRARY_DIR=str(tmp_path)), pronunciation=store,
        chapter_url_defaults_helper=lambda *args: None,
        chapter_url_value_preprocessor=lambda *args: None,
        phrases=SimpleNamespace(find_unpronouncable_words=Mock(return_value=['Door'])),
        htmx=SimpleNamespace(global_pronunciation_table=lambda chapter: '<table>pronunciations</table>',
            edit_row=Mock(return_value='<input name="pronunciation">')))
    app = make_app(views, '/pronunciation/')
    @app.before_request
    def bind_chapter():
        flask.g.chapter = chapter
    return SimpleNamespace(client=app.test_client(), scope=scope, store=store,
                           dictionary=dictionary, root=tmp_path)
