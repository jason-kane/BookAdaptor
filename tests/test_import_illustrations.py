"""Standalone illustration cues and source-aware import spacing."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest
from bs4 import BeautifulSoup

ROOT = Path(__file__).parents[1] / 'src/artifact_editor/chapter'


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    import sys
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_formatted_marker_is_removed_and_resolved(monkeypatch):
    from artifact_editor.images import source
    monkeypatch.setattr(source, 'source_images', lambda _: ['images/image053.jpg'])
    sections = [{'typesetting_method': 'plain', 'ops': [
        {'insert': 'Before.\n[Illus'},
        {'insert': 'tration: image053]', 'attributes': {'bold': True}},
        {'insert': '\nAfter.\nAn inline [Illustration: image053] stays.\n'},
    ]}]
    cleaned, ops = load('illustrations').extract_illustrations(sections, SimpleNamespace())
    assert ''.join(op['insert'] for op in cleaned[0]['ops']) == 'Before.\nAfter.\nAn inline [Illustration: image053] stays.\n'
    assert [op['illustration'] for op in ops if 'illustration' in op] == ['images/image053.jpg']
    assert sections[0]['ops'][0]['insert'].endswith('[Illus')


@pytest.mark.parametrize('files', [[], ['a/image053.png', 'b/image053.jpg']])
def test_missing_or_ambiguous_illustration_fails_explicitly(monkeypatch, files):
    from artifact_editor.images import source
    monkeypatch.setattr(source, 'source_images', lambda _: files)
    with pytest.raises(ValueError, match='exactly one source image'):
        load('illustrations').extract_illustrations([
            {'typesetting_method': 'plain', 'ops': [{'insert': '[Illustration: image053]\n'}]},
        ], SimpleNamespace())


def test_fixed_images_reset_spacing_and_preserve_close_source_cues():
    timeline = load('timeline')
    soup = BeautifulSoup('<chapter><paragraph index="0"/></chapter>', 'xml')
    for i in range(15):
        if i in (5, 6):
            soup.paragraph.append(soup.new_tag('illustration', source_image=f'images/image{i}.png'))
        soup.paragraph.append(soup.new_tag('phrase', index=str(i), duration='2', frames='48'))
    timeline.add_import_images(soup)
    timings = timeline.image_timings(soup)
    images = soup.find_all('image')
    fixed = [image for image in images if image.get('source_image')]
    assert [timings[image['index']]['start'] for image in fixed] == [10, 12]
    assert not soup.find_all('illustration')
    assert all(timings[image['index']]['duration'] >= 6 for image in images if not image.get('source_image'))
    assert all(int(image['frames']) > 0 for image in images)


@pytest.mark.parametrize('separate_sections', [False, True])
def test_import_keeps_three_trailing_illustrations_without_empty_phrases(monkeypatch, tmp_path, separate_sections):
    """Execute the importer without booting Flask or external worker services."""
    import ast
    import html
    import json
    import logging
    import os
    import re
    import sys
    from typing import Any
    from unittest.mock import Mock
    from artifact_editor.images import source

    filenames = [f'images/image{i}.jpg' for i in range(53, 56)]
    monkeypatch.setattr(source, 'source_images', lambda _: filenames)
    choose = Mock()
    monkeypatch.setattr(source, 'choose_source_image', choose)
    monkeypatch.setitem(sys.modules, 'artifact_editor.textoviva.workflow', SimpleNamespace(default_image_workflow=lambda *_: 'default'))
    audio = Mock(side_effect=AssertionError('Import must not generate audio'))
    monkeypatch.setitem(sys.modules, 'artifact_editor.phrases.phrases', SimpleNamespace(generate_all_audio=audio))
    monkeypatch.setitem(sys.modules, 'artifact_editor.chapter.illustrations', load('illustrations'))
    monkeypatch.setitem(sys.modules, 'artifact_editor.chapter.timeline', load('timeline'))
    monkeypatch.setitem(sys.modules, 'artifact_editor.chapter.speech', load('speech'))
    scope = dict(__package__='artifact_editor.chapter', Any=Any, json=json, html=html,
                 os=os, re=re, BeautifulSoup=BeautifulSoup, log=logging.getLogger(__name__),
                 const=SimpleNamespace(LIBRARY_DIR=str(tmp_path), FPS=24,
                                       GEOMETRY={'test': {'TEXT_WIDTH': 100}}, DEFAULT_IMAGE_TEMPLATE='default'),
                 sentence_pipeline=lambda text: SimpleNamespace(sents=[SimpleNamespace(text_with_ws=text, text=text)] if text else []),
                 t=SimpleNamespace(ops_to_internal_representation=lambda ops: SimpleNamespace(render_tree=lambda **_: ''.join(op['insert'] for op in ops))),
                 enqueue_typesetting=Mock(), generate_chapter_mood=Mock(), generate_chapter_theme=Mock(),
                 render_sections=lambda *args: '', font_preamble=lambda: '', video_page_geometry=lambda _: '')
    path = ROOT / 'views.py'
    names = {'compress_ops', 'apply_paragraph_alignment', 'import_chapter_content'}
    nodes = [node for node in ast.parse(path.read_text()).body if isinstance(node, ast.FunctionDef) and node.name in names]
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), scope)
    chapter = SimpleNamespace(language='en', aspect='test', title='Oz', author=SimpleNamespace(name='Baum'),
                              get_chapterdir=lambda: 'chapter', get_paragraph_dir=lambda i: f'paragraphs/{i}',
                              save_delta=Mock(), save_timeline=Mock())
    chapter.replace_content = lambda soup: setattr(chapter, 'soup', soup)
    chapter.get_xml = lambda: chapter.soup
    parts = ['The chapter ends.\n\n', *[f'[Illustration: image{i}]\n\n' for i in range(53, 56)], '\n']
    sections = ([{'typesetting_method': 'plain', 'ops': [{'insert': part}]} for part in parts]
                if separate_sections else [{'typesetting_method': 'plain', 'ops': [{'insert': part} for part in parts]}])
    persisted = set()

    def save_timeline():
        persisted.update(image['index'] for image in chapter.soup.find_all('image'))

    chapter.save_timeline.side_effect = save_timeline

    def select_source(chapter, image, filename):
        assert image['index'] in persisted, 'Image must be persisted before asset selection'
        image['src'] = filename.rsplit('/', 1)[-1]

    choose.side_effect = select_source
    scope['import_chapter_content'](chapter, json.dumps(sections))
    audio.assert_not_called()
    assert len(chapter.soup.find_all('phrase')) == 1
    images = chapter.soup.find_all('image', source_image=True)
    assert [image['source_image'] for image in images] == filenames
    assert [image['index'] for image in images] == ['0', '1', '2']
    assert all(image.get('src') for image in images)
    assert [call.args[2] for call in choose.call_args_list] == filenames
    assert [pause['duration'] for pause in chapter.soup.find_all('silence')] == ['2.000', '5.000', '5.000']
    for image in images[:-1]:
        pause = image.find_next_sibling()
        assert pause.name == 'silence'
        assert pause['duration'] == '5.000'
        assert pause['frames'] == '120'
        assert pause.find_next_sibling().name == 'image'
    assert images[-1].find_next_sibling() is None
    assert not chapter.soup.find_all('illustration')


@pytest.mark.parametrize('method', ['plain', 'verse'])
@pytest.mark.parametrize('rainbow', [False, True])
def test_illustration_blocks_add_no_latex_whitespace(monkeypatch, method, rainbow):
    from artifact_editor.images import source
    monkeypatch.setattr(source, 'source_images', lambda _: ['images/image053.jpg', 'images/image054.jpg'])
    sections = [{'typesetting_method': method, 'ops': [
        {'insert': 'Before.\n\n[Illus'},
        {'insert': 'tration: image053]', 'attributes': {'bold': True}},
        {'insert': '\n\n  \n[Illustration: image054]\n\nAfter.\n'},
    ]}]
    cleaned, operations = load('illustrations').extract_illustrations(sections, SimpleNamespace())
    expected = [{'typesetting_method': method, 'ops': [{'insert': 'Before.\n\nAfter.\n'}]}]
    assert ''.join(op['insert'] for op in cleaned[0]['ops']) == expected[0]['ops'][0]['insert']
    assert len([op for op in operations if 'illustration' in op]) == 2
    import sys
    from unittest.mock import Mock
    monkeypatch.setitem(sys.modules, 'logger', SimpleNamespace(log=lambda _: Mock()))
    renderer = load('section_latex')
    phrases = [{'spoken': 'Before. After.'}] if rainbow else None
    # Rendering preserves text run boundaries; compare the visible blank rows
    # as well as complete output with equivalent surviving formatting runs.
    expected[0]['ops'] = [{'insert': 'Before.\n\n'}, {'insert': 'After.\n'}]
    assert renderer.render_sections(cleaned, phrases) == renderer.render_sections(expected, phrases)


def test_import_image_pauses_cross_paragraphs_but_not_narration():
    timeline = load('timeline')
    soup = BeautifulSoup('<chapter><paragraph><image index="0"/></paragraph>'
                         '<paragraph><image index="1"/><phrase index="0">Words.</phrase>'
                         '<image index="2"/><image index="3"/></paragraph></chapter>', 'xml')
    timeline.add_import_silences(soup, 24)
    pauses = soup.find_all('silence')
    assert len({pause['index'] for pause in pauses}) == 3
    for index in ('0', '2'):
        image = soup.find('image', index=index)
        assert image.find_next_sibling()['duration'] == '5.000'
        assert image['frames'] == '120'
    assert soup.find('image', index='1').find_next_sibling().name == 'phrase'
    assert timeline.silence_duration('5') == 5


@pytest.mark.parametrize('opening,closing', [('“', '”'), ('"', '"')])
@pytest.mark.parametrize('formatted', [False, True])
def test_import_never_merges_narration_with_interrupted_dialogue(monkeypatch, tmp_path, opening, closing, formatted):
    import ast
    import html
    import json
    import logging
    import os
    import re
    import sys
    from typing import Any
    from unittest.mock import Mock
    from artifact_editor.images import source

    filenames = [f'images/image{i}.jpg' for i in range(53, 56)]
    monkeypatch.setattr(source, 'source_images', lambda _: filenames)
    choose = Mock()
    monkeypatch.setattr(source, 'choose_source_image', choose)
    monkeypatch.setitem(sys.modules, 'artifact_editor.textoviva.workflow', SimpleNamespace(default_image_workflow=lambda *_: 'default'))
    audio = Mock(side_effect=AssertionError('Import must not generate audio'))
    monkeypatch.setitem(sys.modules, 'artifact_editor.phrases.phrases', SimpleNamespace(generate_all_audio=audio))
    monkeypatch.setitem(sys.modules, 'artifact_editor.chapter.illustrations', load('illustrations'))
    monkeypatch.setitem(sys.modules, 'artifact_editor.chapter.timeline', load('timeline'))
    monkeypatch.setitem(sys.modules, 'artifact_editor.chapter.speech', load('speech'))
    scope = dict(__package__='artifact_editor.chapter', Any=Any, json=json, html=html,
                 os=os, re=re, BeautifulSoup=BeautifulSoup, log=logging.getLogger(__name__),
                 const=SimpleNamespace(LIBRARY_DIR=str(tmp_path), FPS=24,
                                       GEOMETRY={'test': {'TEXT_WIDTH': 100}}, DEFAULT_IMAGE_TEMPLATE='default'),
                 sentence_pipeline=lambda text: SimpleNamespace(sents=[SimpleNamespace(text_with_ws=text, text=text)] if text else []),
                 t=SimpleNamespace(ops_to_internal_representation=lambda ops: SimpleNamespace(render_tree=lambda **_: ''.join(op['insert'] for op in ops))),
                 enqueue_typesetting=Mock(), generate_chapter_mood=Mock(), generate_chapter_theme=Mock(),
                 render_sections=lambda *args: '', font_preamble=lambda: '', video_page_geometry=lambda _: '')
    path = ROOT / 'views.py'
    names = {'compress_ops', 'apply_paragraph_alignment', 'import_chapter_content'}
    nodes = [node for node in ast.parse(path.read_text()).body if isinstance(node, ast.FunctionDef) and node.name in names]
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), scope)
    chapter = SimpleNamespace(language='en', aspect='test', title='Oz', author=SimpleNamespace(name='Baum'),
                              get_chapterdir=lambda: 'chapter', get_paragraph_dir=lambda i: f'paragraphs/{i}',
                              save_delta=Mock(), save_timeline=Mock())
    chapter.replace_content = lambda soup: setattr(chapter, 'soup', soup)
    chapter.get_xml = lambda: chapter.soup
    text = f'{opening}Alpha,{closing} said the boy, {opening}and rather obstinate, too.{closing}'
    if formatted:
        # Quotes and words cross Quill formatting boundaries.
        ops = [{'insert': char, 'attributes': {'bold': bool(i % 2)}}
               for i, char in enumerate(text)]
    else:
        ops = [{'insert': text}]
    scope['import_chapter_content'](chapter, json.dumps([
        {'typesetting_method': 'plain', 'ops': ops}]))
    texts = [phrase.get_text().strip() for phrase in chapter.soup.find_all('phrase')]
    if not formatted:
        assert texts == [f'{opening}Alpha,{closing}', 'said the boy,',
                         f'{opening}and rather obstinate, too.{closing}']
    # Every emitted phrase lies entirely within one speaker run, even when
    # formatting causes additional phrase breaks.
    boundaries = [text.index(' said'), text.index(opening, text.index(' said'))]
    offset = 0
    for phrase in chapter.soup.find_all('phrase'):
        end = offset + len(phrase.get_text())
        assert not any(offset < edge < end and text[offset:edge].strip()
                       and text[edge:end].strip() for edge in boundaries)
        offset = end
    assert ''.join(chapter.soup.find_all('phrase')[i].get_text() for i in range(len(texts))).strip() == text
    audio.assert_not_called()
