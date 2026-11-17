"""Gutenberg illustration labels can differ from their actual filenames."""
import sys
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location('illustrations', Path(__file__).resolve().parents[1]
    / 'src/artifact_editor/chapter/illustrations.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
extract_illustrations = module.extract_illustrations


@pytest.fixture
def source_book(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, 'artifact_editor.images.source', SimpleNamespace(
        source_directory=lambda _: tmp_path,
        source_images=lambda _: ['images/image151.jpg', 'images/other.jpg']))
    chapter = SimpleNamespace(bookdir='Author/Book')
    sections = [{'typesetting_method': 'plain', 'ops': [{'insert': '[Illustration: image152]\nAfter.\n'}]}]
    return tmp_path, chapter, sections


def test_html_alt_resolves_mismatched_filename(source_book):
    root, chapter, sections = source_book
    (root / 'book.htm').write_text('<img src="images/image151.jpg" alt="image152">')
    clean, ops = extract_illustrations(sections, chapter)
    assert [op['illustration'] for op in ops if 'illustration' in op] == ['images/image151.jpg']
    assert ''.join(op['insert'] for op in clean[0]['ops']) == 'After.\n'


@pytest.mark.parametrize('source', ['https://example.com/images/image151.jpg', '../images/image151.jpg'])
def test_nonlocal_html_alias_does_not_resolve(source_book, source):
    root, chapter, sections = source_book
    (root / 'book.htm').write_text(f'<img src="{source}" alt="image152">')
    with pytest.raises(ValueError, match='exactly one source image'):
        extract_illustrations(sections, chapter)


def test_ambiguous_html_alias_still_requires_explicit_selection(source_book):
    root, chapter, sections = source_book
    (root / 'book.htm').write_text('<img src="images/image151.jpg" alt="image152">'
                                 '<img src="images/other.jpg" alt="image152">')
    with pytest.raises(ValueError, match='found 2'):
        extract_illustrations(sections, chapter)


def test_nested_html_image_id_and_encoded_path(source_book):
    root, chapter, sections = source_book
    (root / 'chapters').mkdir()
    (root / 'chapters/chapter11.html').write_text('<img src="../images/image%31%35%31.jpg" id="image152">')
    _, ops = extract_illustrations(sections, chapter)
    assert [op['illustration'] for op in ops if 'illustration' in op] == ['images/image151.jpg']
