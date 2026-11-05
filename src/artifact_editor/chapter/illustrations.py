"""Recognize standalone source illustration cues across Quill formatting runs."""
import re
from pathlib import PurePosixPath

# An illustration block includes the empty lines after its marker. Removing
# only the marker line leaves those lines as visible blank rows in LaTeX.
MARKER = re.compile(r"^[^\S\n]*\[Illustration:\s*([^\]\n]+?)\s*\][^\S\n]*(?:\n|$)(?:[^\S\n]*\n)*", re.MULTILINE | re.IGNORECASE)


def extract_illustrations(sections, chapter):
    from artifact_editor.images.source import source_images

    available = None
    cleaned, operations = [], []
    for section_index, section in enumerate(sections):
        if section_index:
            operations.append({'insert': '\n'})
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
                attributes = dict(piece.get('attributes') or {}, _typesetting_method=section['typesetting_method'])
                operations.append(dict(piece, attributes=attributes))
            offset = end
        cleaned.append(dict(section, ops=clean_ops))
    return cleaned, operations
