"""Book page templates and explicit roles for editable chapter sections."""
from copy import deepcopy
import json
from pathlib import Path
import re

ROLES = {'book_page', 'chapter_page', 'chapter_text'}


def validate_delta(delta):
    if not isinstance(delta, dict) or not isinstance(delta.get('ops'), list):
        raise ValueError('Expected a Quill document.')
    for op in delta['ops']:
        if (not isinstance(op, dict) or not isinstance(op.get('insert'), str)
                or not isinstance(op.get('attributes', {}), dict)):
            raise ValueError('Page templates support text and text formatting only.')
    return delta


def validate_sections(sections):
    if not isinstance(sections, list) or not sections:
        raise ValueError('At least one section is required')
    for section in sections:
        if (not isinstance(section, dict)
                or section.get('typesetting_method') not in {'verse', 'plain'}
                or section.get('section_type', 'chapter_text') not in ROLES
                or section.get('template', '') not in {'', 'book_page', 'chapter_page'}
                or (section.get('template') and section['template'] != section.get('section_type'))):
            raise ValueError('Invalid chapter section')
        validate_delta({'ops': section.get('ops')})
    return sections


def templates(book):
    return {
        'book_page': deepcopy(book.config.get('title_page_delta', {'ops': [
            {'insert': book.config.get('pretty_title', book.title), 'attributes': {'bold': True}},
            {'insert': '\n', 'attributes': {'align': 'center'}},
            {'insert': 'by ' + book.author.pretty_name},
            {'insert': '\n', 'attributes': {'align': 'center'}},
        ]})),
        'chapter_page': deepcopy(book.config.get('chapter_page_delta', {'ops': [
            {'insert': 'Chapter <CHAPTER NUMBER>', 'attributes': {'bold': True}},
            {'insert': '\n', 'attributes': {'align': 'center'}},
            {'insert': '<CHAPTER TITLE>'},
            {'insert': '\n', 'attributes': {'align': 'center'}},
        ]})),
    }


def expand(delta, chapter):
    """Replace macros even when Quill split a token across formatting runs."""
    result = deepcopy(delta)
    title = chapter.chapter_title
    # The default title is already represented by the chapter number line.
    if title == f'Chapter {chapter.number}':
        title = ''
    for token, value in (('<CHAPTER TITLE>', title), ('<CHAPTER NUMBER>', str(chapter.number))):
        text = ''.join(op['insert'] for op in result['ops'])
        for match in reversed(list(re.finditer(re.escape(token), text))):
            offset = 0
            for op in result['ops']:
                end = offset + len(op['insert'])
                if offset < match.end() and end > match.start():
                    left, right = max(0, match.start() - offset), min(len(op['insert']), match.end() - offset)
                    op['insert'] = op['insert'][:left] + (value if offset <= match.start() < end else '') + op['insert'][right:]
                offset = end
    return result


def book_for(chapter):
    from types import SimpleNamespace
    from artifact_editor import config, tools
    return SimpleNamespace(author=chapter.author, title=chapter.title,
                           bookdir=tools.get_bookdir(chapter.author.name, chapter.title),
                           config=config.get_config(str(Path(chapter.author.name) / chapter.title)))


def template_section(kind, book, chapter):
    delta = templates(book)[kind]
    if kind == 'chapter_page':
        delta = expand(delta, chapter)
    return dict(section_type=kind, template=kind, typesetting_method='plain', ops=delta['ops'])


def slice_ops(ops, start, end):
    result, offset = [], 0
    for op in ops:
        stop = offset + len(op['insert'])
        if stop > start and offset < end:
            result.append(dict(deepcopy(op), insert=op['insert'][max(0, start-offset):end-offset]))
        offset = stop
    return result


def classify(ops, chapter, method='verse'):
    """Recognize only exact leading book/author lines and explicit chapter headings.

    Unknown front matter stays editable as chapter text; never guess from caps.
    """
    book = book_for(chapter)
    text = ''.join(op['insert'] for op in ops)
    author_name = getattr(chapter.author, 'pretty_name', chapter.author.name)
    chapter_title = getattr(chapter, 'chapter_title', '')
    title_lines = {book.title.casefold(), book.config.get('pretty_title', book.title).casefold(),
                   chapter.author.name.casefold(), author_name.casefold(),
                   ('by ' + chapter.author.name).casefold(), ('by ' + author_name).casefold()}
    spans, offset, body = [], 0, False
    for line in text.splitlines(keepends=True):
        clean = line.strip().strip('_*').strip()
        role = 'chapter_text'
        if not body:
            if clean.casefold() in title_lines:
                role = 'book_page'
            elif (re.fullmatch(r'(?:chapter|letter|book)\s+(?:\d+|[IVXLCDM]+)(?:[.:\s—–-].*)?', clean, re.I)
                  or (book.config.get('HAS_NUMBERED_CHAPTERS') and re.fullmatch(r'\d+\.?', clean))
                  or (book.config.get('HAS_ROMAN_NUMERAL_CHAPTERS') and re.fullmatch(r'[IVXLCDM]+\.?', clean))
                  or (clean and clean == chapter_title)):
                role = 'chapter_page'
            elif not clean:
                role = spans[-1][2] if spans else 'book_page'
            else:
                body = True
        if spans and spans[-1][2] == role:
            spans[-1] = (spans[-1][0], offset + len(line), role)
        else:
            spans.append((offset, offset + len(line), role))
        offset += len(line)
    return [dict(section_type=role, typesetting_method=method, ops=slice_ops(ops, start, end))
            for start, end, role in spans if text[start:end].strip()]


def merge_section_fragments(sections):
    """Join paragraph fragments belonging to the same editor section."""
    result = []
    keys = ('section_type', 'typesetting_method', 'section_index', 'template')
    for section in sections:
        section = deepcopy(section)
        if result and all(result[-1].get(key) == section.get(key) for key in keys):
            result[-1]['ops'].extend(section['ops'])
        else:
            result.append(section)
    return result


def apply_body_typography(ops, book_config):
    """Resolve Quill's default font/size from the book without changing custom runs."""
    result = deepcopy(ops)
    defaults = {'font': ('Helvetica', 'default_body_font'),
                'size': ('16px', 'default_body_font_size')}
    for op in result:
        attributes = dict(op.get('attributes') or {})
        for field, (quill_default, config_key) in defaults.items():
            selected = book_config.get(config_key)
            if selected and attributes.get(field) in (None, False, '', quill_default):
                attributes[field] = selected
        if attributes:
            op['attributes'] = attributes
    return result


def prepare_sections(chapter, sections=None, delta=None):
    """Initialize legacy text once; refresh only copies explicitly tied to templates."""
    book = book_for(chapter)
    initialize_templates = sections is None or any(key in book.config for key in ('title_page_delta', 'chapter_page_delta'))
    if sections is None:
        sections = classify((delta or {'ops': []})['ops'], chapter)
        # New imports use the book's templates in place of recognized raw headers.
        sections = [s for s in sections if s['section_type'] == 'chapter_text']
    else:
        prepared = []
        for section in merge_section_fragments(sections):
            if 'section_type' in section:
                # Older chapter editors saved a plain copy of the generated
                # heading without its template link. Reattach only matching
                # headings that still use Quill's default typography.
                if (section.get('section_type') == 'chapter_page'
                        and not section.get('template')
                        and 'chapter_page_delta' in book.config):
                    expected = template_section('chapter_page', book, chapter)
                    text = lambda ops: ''.join(''.join(op['insert'].split()) for op in ops)
                    unformatted = all(
                        (op.get('attributes') or {}).get('font') in (None, False, '', 'Helvetica')
                        and (op.get('attributes') or {}).get('size') in (None, False, '', '16px')
                        for op in section['ops'])
                    if unformatted and text(section['ops']) == text(expected['ops']):
                        section['template'] = 'chapter_page'
                prepared.append(deepcopy(section))
            else:
                for part in classify(section['ops'], chapter, section['typesetting_method']):
                    if part['section_type'] in {'book_page', 'chapter_page'}:
                        part['template'] = part['section_type']
                    prepared.append(part)
        sections = merge_section_fragments(prepared)
    if initialize_templates:
        for kind in reversed(('book_page', 'chapter_page')):
            if not any(s.get('section_type') == kind for s in sections):
                sections.insert(0, template_section(kind, book, chapter))
    result = [dict(s, ops=template_section(s['template'], book, chapter)['ops'])
              if s.get('template') else s for s in sections]
    return [dict(s, ops=apply_body_typography(s['ops'], book.config))
            if s.get('section_type') == 'chapter_text' else s for s in result]


def sections_path(chapter):
    import const
    return Path(const.LIBRARY_DIR) / chapter.get_chapterdir() / chapter.language / 'chapter.sections.json'


def load_sections(chapter):
    path = sections_path(chapter)
    if path.is_file():
        return prepare_sections(chapter, sections=validate_sections(json.loads(path.read_text())))
    delta = chapter.load_delta()
    if not delta or not delta.get('ops'):
        delta = {'ops': [{'insert': chapter.load_txt() or ''}]}
    return prepare_sections(chapter, delta=delta)


def save_raw_chapter(chapter, text, book_config, heading=None):
    """Retain the raw source and use the selected parser's heading conventions."""
    path = sections_path(chapter)
    path.parent.mkdir(parents=True, exist_ok=True)
    chapter.save_txt(text)
    lines = text.splitlines()
    if heading and (book_config.get('HAS_ALLCAPS_BREAKS') or book_config.get('HAS_UNPUNCTUATED_SINGLE_LINE')):
        chapter.set_chapter_title(heading.strip().strip('_*'))
    # Only consume a following title line when that format was explicitly selected.
    for index, line in enumerate(lines):
        match = re.fullmatch(r'\s*(?:CHAPTER|LETTER|BOOK)\s+(?:\d+|[IVXLCDM]+)\.?\s*(.*)', line, re.I)
        if not match:
            continue
        heading = match[1].lstrip('.:—–- ').strip()
        if not heading and book_config.get('HAS_CHAPTER_TITLE') and not book_config.get('CHAPTER_TITLE_ON_SAME_LINE'):
            heading = next((value.strip() for value in lines[index+1:] if value.strip()), '')
        if heading:
            chapter.set_chapter_title(heading)
        break
    path.write_text(json.dumps(prepare_sections(chapter, delta={'ops': [{'insert': text}]}), indent=2))
