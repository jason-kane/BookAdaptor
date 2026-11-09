"""Page templates, safe frame cuts, and reuse of complete chapter assets."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock
import wave

from bs4 import BeautifulSoup
from PIL import Image
import pytest

from artifact_editor.book import pages, textoviva


@pytest.fixture
def book(tmp_path, monkeypatch):
    import const
    monkeypatch.setattr(const, 'LIBRARY_DIR', str(tmp_path))
    author = SimpleNamespace(name='Author', pretty_name='The Author')
    book = SimpleNamespace(author=author, title='A Book', bookdir=str(tmp_path / 'Author/A Book'), config={})
    monkeypatch.setattr(pages, 'book_for', lambda chapter: book)
    return book


def make_chapter(book, number=1, title='The Beginning', xml=None):
    directory = f'Author/A Book/chapter/{number:04d}'
    chapter = SimpleNamespace(author=book.author, title=book.title, number=number,
                              chapter_title=title, language='english', chapterdir=directory,
                              get_chapterdir=lambda: directory, get_aspect=lambda: 'landscape')
    chapter.get_video_filename = lambda: str(Path(book.bookdir) / 'chapter' / f'{number:04d}' / 'chapter.mp4')
    chapter.get_xml = lambda: BeautifulSoup(xml or '<chapter/>', 'xml')
    return chapter


def test_macro_expansion_spans_runs_preserves_formatting_and_source(book):
    delta = {'ops': [{'insert': '<CHAP', 'attributes': {'bold': True}},
                     {'insert': 'TER TITLE> / <CHAPTER NUM'}, {'insert': 'BER>\n'}]}
    before = deepcopy(delta)
    expanded = pages.expand(delta, make_chapter(book, 12, 'A & B <C>'))
    assert ''.join(op['insert'] for op in expanded['ops']) == 'A & B <C> / 12\n'
    assert expanded['ops'][0]['attributes']['bold'] is True
    assert delta == before


def test_raw_headers_become_templates_without_losing_body(book):
    chapter = make_chapter(book, 2)
    sections = pages.prepare_sections(chapter, delta={'ops': [{'insert':
        'A Book\nby The Author\n\nChapter II. The Beginning\n\nThe story begins.\nA Book\n'}]})
    assert [s['section_type'] for s in sections] == ['book_page', 'chapter_page', 'chapter_text']
    assert ''.join(op['insert'] for op in sections[-1]['ops']) == 'The story begins.\nA Book\n'
    assert 'Chapter 2' in ''.join(op['insert'] for op in sections[1]['ops'])


def test_refresh_only_template_copies_and_keep_manual_sections(book):
    chapter = make_chapter(book)
    sections = pages.prepare_sections(chapter, delta={'ops': [{'insert': 'Body\n'}]})
    sections[0]['typesetting_method'] = 'verse'
    book.config['title_page_delta'] = {'ops': [{'insert': 'New title\n', 'attributes': {'bold': True}}]}
    sections[1].pop('template')
    sections[1]['ops'] = [{'insert': 'My chapter heading\n'}]
    refreshed = pages.prepare_sections(chapter, sections=sections)
    assert refreshed[0]['ops'][0]['insert'] == 'New title\n'
    assert refreshed[0]['typesetting_method'] == 'verse'
    assert refreshed[1]['ops'][0]['insert'] == 'My chapter heading\n'


def test_paragraph_fragments_load_as_three_sections_without_losing_formatting(book):
    chapter = make_chapter(book)
    sections = [
        {'typesetting_method': 'plain', 'ops': [{'insert': text, 'attributes': {'font': 'Cardo'}}]}
        for text in ('A Book\n', 'by The Author\n', 'Chapter 1\n',
                     'The Beginning\n', 'First paragraph.\n', 'Second paragraph.\n')
    ]
    before = deepcopy(sections)
    result = pages.prepare_sections(chapter, sections=sections)
    assert [s['section_type'] for s in result] == ['book_page', 'chapter_page', 'chapter_text']
    assert result[-1]['ops'] == sections[-2]['ops'] + sections[-1]['ops']
    assert sections == before
    # The same repair applies after legacy fragments were assigned explicit roles.
    fragmented = result[:2] + [dict(result[-1], ops=[op]) for op in result[-1]['ops']]
    assert pages.prepare_sections(chapter, sections=fragmented) == result


def test_section_fragment_merge_preserves_explicit_boundaries_and_methods():
    sections = [dict(section_type='chapter_text', section_index=index,
                     typesetting_method=method, ops=[{'insert': 'Text\n'}])
                for index, method in ((0, 'plain'), (1, 'plain'), (1, 'verse'))]
    assert pages.merge_section_fragments(sections) == sections


def test_raw_parser_creates_fresh_chapter_and_expands_selected_heading(book):
    chapter = make_chapter(book, 3, 'Chapter 3')
    raw_path = pages.sections_path(chapter).parent / 'chapter.txt'
    chapter.save_txt = lambda text: raw_path.write_text(text)
    chapter.set_chapter_title = lambda title: setattr(chapter, 'chapter_title', title)
    book.config['HAS_UNPUNCTUATED_SINGLE_LINE'] = True
    pages.save_raw_chapter(chapter, 'A New Beginning\n\nStory text.\n', book.config, heading='A New Beginning')
    assert raw_path.read_text() == 'A New Beginning\n\nStory text.\n'
    sections = json.loads(pages.sections_path(chapter).read_text())
    assert [s['section_type'] for s in sections] == ['book_page', 'chapter_page', 'chapter_text']
    assert 'A New Beginning' in ''.join(op['insert'] for op in sections[1]['ops'])
    assert ''.join(op['insert'] for op in sections[-1]['ops']) == 'Story text.\n'


@pytest.mark.parametrize('bad', [None, [], {'ops': [{'insert': {'image': 'x'}}]}, {'ops': [{'insert': 3}]}])
def test_templates_reject_nontext_documents(bad):
    with pytest.raises(ValueError):
        pages.validate_delta(bad)


def test_all_chapters_required_and_numeric_order(book, monkeypatch):
    items = [make_chapter(book, number) for number in (1, 2, 10)]
    monkeypatch.setattr(textoviva, 'chapters', lambda _: items)
    for chapter in items[:2]:
        path = Path(chapter.get_video_filename())
        path.parent.mkdir(parents=True)
        path.write_bytes(b'video')
    assert textoviva.availability(book) == dict(enabled=False, count=3, missing=[10], ready=False)
    Path(items[2].get_video_filename()).parent.mkdir(parents=True)
    Path(items[2].get_video_filename()).write_bytes(b'video')
    assert textoviva.availability(book)['enabled']
    monkeypatch.setattr(textoviva, 'chapters', lambda _: [])
    assert not textoviva.availability(book)['enabled']


def timeline():
    return '''<chapter><paragraph><phrase index="0" section_type="book_page" frames="2">A Book</phrase>
    <silence index="0" frames="1"/><phrase index="1" section_type="chapter_page" frames="2">Chapter</phrase>
    <phrase index="2" section_type="chapter_text" frames="3">Body</phrase><silence index="1" frames="1"/>
    </paragraph></chapter>'''


def test_frame_cuts_keep_first_intact_and_remove_later_title_audio_and_pause(book):
    chapter = make_chapter(book, xml=timeline())
    assert textoviva.kept_ranges(chapter, first=True) == ([(0, 9)], 9)
    assert textoviva.kept_ranges(chapter) == ([(3, 9)], 9)


def test_editor_role_changes_can_reuse_frames_but_changed_text_cannot(book):
    chapter = make_chapter(book, xml=timeline())
    path = pages.sections_path(chapter)
    path.parent.mkdir(parents=True)
    sections = [dict(section_type=role, typesetting_method='plain', ops=[{'insert': text}]) for role, text in
                [('book_page', 'A Book\n'), ('book_page', 'Chapter\n'), ('chapter_text', 'Body\n')]]
    path.write_text(json.dumps(sections))
    assert textoviva.kept_ranges(chapter) == ([(5, 9)], 9)
    sections[-1]['ops'][0]['insert'] = 'Changed text\n'
    path.write_text(json.dumps(sections))
    with pytest.raises(ValueError, match='changed since import'):
        textoviva.kept_ranges(chapter)


def test_legacy_phrase_crossing_roles_is_rejected(book):
    chapter = make_chapter(book, xml='<chapter><paragraph><phrase frames="3">A Book\nBody</phrase></paragraph></chapter>')
    with pytest.raises(ValueError, match='separate the Book Page'):
        textoviva.kept_ranges(chapter)


def test_assembly_reuses_exact_frames_and_trims_audio(book, monkeypatch):
    import const
    from artifact_editor import tools
    monkeypatch.setattr(const, 'FPS', 8)
    items = [make_chapter(book, i, xml=timeline()) for i in (1, 2)]
    monkeypatch.setattr(textoviva, 'chapters', lambda _: items)
    for chapter in items:
        root = Path(chapter.get_video_filename()).parent
        frames = root / 'frames/landscape'
        frames.mkdir(parents=True)
        Path(chapter.get_video_filename()).write_bytes(b'video')
        for index in range(9):
            Image.new('RGB', (32, 32), (chapter.number * 50, index * 20, 0)).save(frames / f'frame_{index:06d}.png')
        with wave.open(str(root / 'audio.wav'), 'wb') as audio:
            audio.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
            audio.writeframes(b'\0\0' * 18000)
    plan = textoviva.plan(book)
    original_encode = tools.assemble_mp4
    seen = []
    def encode(**kwargs):
        directory = Path(kwargs['framedir'])
        links = sorted(directory.glob('frame_*.png'))
        seen.extend(path.resolve() for path in links)
        assert all(path.is_symlink() for path in links)
        assert kwargs['max_frames'] == 15
        with wave.open(kwargs['wavfile'], 'rb') as audio:
            assert audio.getnframes() == 90000  # 15 frames at 8 FPS, 48kHz
        return original_encode(**kwargs)
    monkeypatch.setattr(tools, 'assemble_mp4', encode)
    textoviva.execute({'arguments': dict(author=book.author.name, title=book.title,
                                        fingerprint=textoviva.fingerprint(plan))})
    assert len(seen) == 15
    assert seen[9].name == 'frame_000003.png'
    probe = subprocess.run(['/usr/bin/ffprobe', '-v', 'error', '-show_streams', '-of', 'json',
                            str(textoviva.output_path(book))], check=True, capture_output=True, text=True)
    streams = json.loads(probe.stdout)['streams']
    assert int(next(s for s in streams if s['codec_type'] == 'video')['nb_frames']) == 15
    assert next(s for s in streams if s['codec_type'] == 'audio')
    assert all(Path(chapter.get_video_filename()).read_bytes() == b'video' for chapter in items)


def test_queued_assembly_rejects_changed_assets_before_encoding(book, monkeypatch):
    monkeypatch.setattr(textoviva, 'chapters', lambda _: [])
    monkeypatch.setattr(textoviva, 'plan', lambda _: [{'video_stamp': 2}])
    with pytest.raises(ValueError, match='changed while this book was queued'):
        textoviva.execute({'arguments': dict(author=book.author.name, title=book.title, fingerprint='old')})


def test_import_keeps_roles_at_phrase_boundaries_without_internal_quill_attributes(book, monkeypatch):
    import ast
    import html
    import logging
    import os
    import re
    from typing import Any
    import const
    import artifact_editor.chapter.workflow as workflow
    monkeypatch.setattr(workflow, 'begin_step', Mock())
    path = Path(__file__).parents[1] / 'src/artifact_editor/chapter/views.py'
    names = {'compress_ops', 'apply_paragraph_alignment', 'import_chapter_content'}
    nodes = [node for node in ast.parse(path.read_text()).body
             if isinstance(node, ast.FunctionDef) and node.name in names]
    scope = dict(__package__='artifact_editor.chapter', Any=Any, json=json, html=html,
                 os=os, re=re, const=const, BeautifulSoup=BeautifulSoup, log=logging.getLogger(__name__),
                 sentence_pipeline=lambda text: SimpleNamespace(sents=[SimpleNamespace(text_with_ws=text, text=text)] if text else []),
                 t=SimpleNamespace(ops_to_internal_representation=lambda ops: SimpleNamespace(render_tree=lambda **_: ''.join(op['insert'] for op in ops))),
                 enqueue_typesetting=Mock(), generate_chapter_mood=Mock(), generate_chapter_theme=Mock(),
                 render_sections=lambda *args: '', font_preamble=lambda: '', video_page_geometry=lambda _: '')
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), scope)
    chapter = make_chapter(book)
    chapter.aspect = 'widescreen'
    chapter.soup = BeautifulSoup('<chapter/>', 'xml')
    chapter.get_xml = lambda: chapter.soup
    chapter.get_paragraph_dir = lambda index: f'{chapter.chapterdir}/paragraphs/{index:06d}'
    chapter.replace_content = lambda soup: setattr(chapter, 'soup', soup)
    chapter.save_delta = Mock()
    chapter.save_timeline = Mock()
    sections = [dict(section_type=role, typesetting_method='plain', ops=[{'insert': text}]) for role, text in
                [('book_page', 'A Book'), ('chapter_page', 'Chapter One'), ('chapter_text', 'The story begins.')]]
    scope['import_chapter_content'](chapter, json.dumps(sections))
    phrases = chapter.soup.find_all('phrase')
    assert [p['section_type'] for p in phrases] == ['book_page', 'chapter_page', 'chapter_text']
    assert [p['section_index'] for p in phrases] == ['0', '1', '2']
    assert ''.join(p.get_text() for p in phrases).replace('\n', '') == 'A BookChapter OneThe story begins.'
    for phrase in phrases:
        assert phrase.find_parent('paragraph')['section_type'] == phrase['section_type']
        assert not any(key.startswith('_section') for op in json.loads(phrase['delta']) for key in op.get('attributes', {}))
