"""XML implementation of Chapter's explicit content persistence operations.

Callers select records or replace the timeline. They do not choose a storage
format. Record updates merge against the last loaded state so saving an image
does not replace another worker's changes to the rest of the chapter.
"""
from copy import deepcopy
import os
from pathlib import Path
import tempfile

from bs4 import BeautifulSoup, Tag
from filelock import FileLock


def remember_content(chapter):
    chapter._persisted_content = deepcopy(chapter.soup)


def _write(path, soup):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o644
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=path.name + ".", delete=False) as output:
            temporary = output.name
            output.write(soup.prettify())
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def replace_content(chapter, content):
    if not isinstance(content, (BeautifulSoup, Tag)):
        raise TypeError("Chapter content must be a parsed chapter")
    path = Path(chapter.get_xml_fn())
    path.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(path) + ".lock"):
        _write(path, content)
    chapter.soup = content
    remember_content(chapter)


def _find(soup, record):
    if soup is None:
        return None
    if record.name in ("chapter", "book"):
        return soup if soup.name == record.name else soup.find(record.name)
    for field in ("index", "id"):
        if record.get(field) is not None:
            # Attributes are occasionally integers in the working tree and
            # strings after parsing the persisted representation.
            return next((tag for tag in soup.find_all(record.name)
                         if str(tag.get(field)) == str(record[field])), None)
    raise ValueError(f"Cannot save {record.name} without an index or id")


def _contents(record):
    return "".join(str(child) for child in record.contents)


def save_records(chapter, records, *, include_contents=True):
    records = list(records)
    if not records:
        return
    baseline = getattr(chapter, "_persisted_content", None)
    if baseline is None:
        raise RuntimeError("Load chapter content or use replace_content() before saving records")
    path = Path(chapter.get_xml_fn())
    with FileLock(str(path) + ".lock"):
        if not path.exists():
            raise RuntimeError("Persist the chapter timeline before saving its records")
        latest = BeautifulSoup(path.read_text(encoding="utf-8"), "xml")
        changes = []
        for record in records:
            if not isinstance(record, Tag):
                record = getattr(record, "phrase_xml", record)
            if not isinstance(record, Tag):
                raise TypeError("Expected a chapter record")
            previous = _find(baseline, record)
            target = _find(latest, record)
            if previous is None or target is None:
                raise ValueError(f"{record.name} {record.get('index', '')} is not persisted; use save_timeline() for structural changes")
            attributes = {
                key: deepcopy(value) for key, value in record.attrs.items()
                if key not in previous.attrs or str(value) != str(previous[key])
            }
            removed = set(previous.attrs) - set(record.attrs)
            contents_changed = include_contents and _contents(record) != _contents(previous)
            if not attributes and not removed and not contents_changed:
                continue
            target.attrs.update(attributes)
            for key in removed:
                target.attrs.pop(key, None)
            if contents_changed:
                target.clear()
                for child in record.contents:
                    target.append(deepcopy(child))
            changes.append((previous, deepcopy(record), contents_changed))
        if changes:
            _write(path, latest)
            # Keep caller-held tags alive; only advance the baseline for the
            # records this operation committed, including removed attributes.
            for previous, saved, contents_changed in changes:
                previous.attrs = deepcopy(saved.attrs)
                if contents_changed:
                    previous.clear()
                    for child in saved.contents:
                        previous.append(deepcopy(child))
