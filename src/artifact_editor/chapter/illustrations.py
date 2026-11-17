"""Recognize standalone source illustration cues across Quill formatting runs."""
import re
from pathlib import PurePosixPath
from urllib.parse import unquote, urlsplit

from bs4 import BeautifulSoup

# An illustration block includes the empty lines after its marker. Removing
# only the marker line leaves those lines as visible blank rows in LaTeX.
MARKER = re.compile(r"^[^\S\n]*\[Illustration:\s*([^\]\n]+?)\s*\][^\S\n]*(?:\n|$)(?:[^\S\n]*\n)*", re.MULTILINE | re.IGNORECASE)


def source_illustration_aliases(chapter, available):
    """Map source HTML image labels to existing, book-local image files."""
    from artifact_editor.images.source import source_directory

    if not getattr(chapter, 'bookdir', None):
        return {}
    root = source_directory(chapter).resolve()
    aliases = {}
    for document in root.rglob('*'):
        if document.suffix.lower() not in {'.htm', '.html'} or not document.is_file():
            continue
        html = BeautifulSoup(document.read_text(encoding='utf-8', errors='replace'), 'html.parser')
        for image in html.find_all('img', src=True):
            source = urlsplit(image['src'])
            if source.scheme or source.netloc:
                continue
            try:
                filename = (document.parent / unquote(source.path)).resolve().relative_to(root).as_posix()
            except ValueError:
                continue
            if filename not in available:
                continue
            for label in (image.get('alt'), image.get('id')):
                if isinstance(label, str) and label.strip():
                    aliases.setdefault(label.strip().casefold(), set()).add(filename)
    return aliases


def extract_illustrations(sections, chapter):
    from artifact_editor.images.source import source_images

    available = None
    aliases = None
    cleaned, operations = [], []
    for section_index, section in enumerate(sections):
        attributes = {'_typesetting_method': section['typesetting_method'],
                      '_section_type': section.get('section_type', 'chapter_text'),
                      '_section_index': section_index}
        if section_index:
            operations.append({'insert': '\n', 'attributes': attributes})
        text = ''.join(op.get('insert', '') for op in section['ops'])
        matches = list(MARKER.finditer(text))
        cues = {}
        for match in matches:
            if available is None:
                available = source_images(chapter)
            reference = match[1].strip()
            candidates = [name for name in available if name.casefold() == reference.casefold()]
            if not candidates:
                candidates = [name for name in available if
                              PurePosixPath(name).name.casefold() == reference.casefold()
                              or PurePosixPath(name).stem.casefold() == reference.casefold()]
            if not candidates:
                if aliases is None:
                    aliases = source_illustration_aliases(chapter, available)
                candidates = sorted(aliases.get(reference.casefold(), ()))
            if len(candidates) != 1:
                raise ValueError(f'Illustration {reference!r} must match exactly one source image (found {len(candidates)})')
            cues[match.start()] = candidates[0]
        clean_ops = []
        offset = 0
        for op in section['ops']:
            value = op.get('insert', '')
            end = offset + len(value)
            cuts = sorted({offset, end, *(point for match in matches for point in match.span() if offset < point < end)})
            for start, stop in zip(cuts, cuts[1:]):
                if start in cues:
                    operations.append({'insert': '', 'illustration': cues[start],
                                       'attributes': {'_illustration': len(operations)}})
                if any(match.start() <= start < match.end() for match in matches):
                    continue
                piece = dict(op, insert=value[start-offset:stop-offset])
                clean_ops.append(piece)
                operations.append(dict(piece, attributes=dict(piece.get('attributes') or {}, **attributes)))
            offset = end
        cleaned.append(dict(section, ops=clean_ops))
    return cleaned, operations
