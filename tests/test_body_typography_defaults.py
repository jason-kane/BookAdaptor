from copy import deepcopy
from types import SimpleNamespace

from artifact_editor.book import pages


def test_quill_defaults_use_book_metadata_and_preserve_custom_formatting():
    source = [
        {'insert': 'Plain'},
        {'insert': 'Default', 'attributes': {'font': 'Helvetica', 'size': '16px', 'bold': True}},
        {'insert': 'Reset', 'attributes': {'font': False, 'size': None}},
        {'insert': 'Custom', 'attributes': {'font': 'Calibri', 'size': '24px', 'italic': True}},
        {'insert': 'Custom size', 'attributes': {'size': '18px'}},
    ]
    before = deepcopy(source)
    result = pages.apply_body_typography(source, {'default_body_font': 'Cardo', 'default_body_font_size': '14px'})
    assert source == before
    assert result[0]['attributes'] == {'font': 'Cardo', 'size': '14px'}
    assert result[1]['attributes'] == {'font': 'Cardo', 'size': '14px', 'bold': True}
    assert result[2]['attributes'] == {'font': 'Cardo', 'size': '14px'}
    assert result[3] == source[3]
    assert result[4]['attributes'] == {'font': 'Cardo', 'size': '18px'}
    assert pages.apply_body_typography(result, {'default_body_font': 'Cardo', 'default_body_font_size': '14px'}) == result


def test_only_body_sections_inherit_book_typography(monkeypatch):
    book = SimpleNamespace(config={'default_body_font': 'Cardo', 'default_body_font_size': '14px'})
    monkeypatch.setattr(pages, 'book_for', lambda _: book)
    sections = [dict(section_type=role, typesetting_method='plain', ops=[{'insert': role}])
                for role in ('book_page', 'chapter_page', 'chapter_text')]
    before = deepcopy(sections)
    result = pages.prepare_sections(SimpleNamespace(config={'default_body_font': 'Wrong'}), sections=sections)
    assert result[:2] == before[:2]
    assert result[2]['ops'][0]['attributes'] == {'font': 'Cardo', 'size': '14px'}
    assert sections == before


def test_legacy_default_heading_reattaches_to_matching_book_template(monkeypatch):
    book = SimpleNamespace(title='Oz',author=SimpleNamespace(pretty_name='Baum'),config={
        'chapter_page_delta': {'ops': [
            {'insert': 'Chapter <CHAPTER NUMBER>', 'attributes': {'font': 'Cardo', 'size': '14px', 'bold': True}},
            {'insert': '\n', 'attributes': {'align': 'center'}},
            {'insert': '<CHAPTER TITLE>', 'attributes': {'font': 'Cardo', 'size': '16px'}},
            {'insert': '\n\n\n', 'attributes': {'align': 'center'}},
        ]}})
    monkeypatch.setattr(pages,'book_for',lambda _:book)
    chapter=SimpleNamespace(number=20,chapter_title='A Title')
    source=[dict(section_type='book_page',typesetting_method='plain',ops=[{'insert':'Oz\n'}]),
            dict(section_type='chapter_page',template='',typesetting_method='plain',ops=[
                {'insert':'Chapter 20\n'}, {'insert':'A Title\n','attributes':{'bold':True}}])]
    before=deepcopy(source)
    result=pages.prepare_sections(chapter,sections=source)
    assert source==before
    assert result[1]['template']=='chapter_page'
    assert result[1]['ops']==pages.template_section('chapter_page',book,chapter)['ops']
    source[1]['ops'][1]['attributes']['font']='Calibri'
    assert pages.prepare_sections(chapter,sections=source)[1]['ops']==source[1]['ops']
