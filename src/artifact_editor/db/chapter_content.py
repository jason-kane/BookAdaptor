"""Relational persistence behind Chapter's parsed-tag compatibility interface.

XML is read only when initializing a chapter. Layouts contain text, markup and
UUID references, never serialized chapter XML. Paragraphs and timeline entries
are individual ORM records; their UUIDs survive moves and reindexing.
"""
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
from pathlib import Path
from uuid import uuid4

from bs4 import BeautifulSoup, Comment, NavigableString, Tag
from sqlalchemy import select, text

from . import models as m
from .session import session_scope

KINDS = {"paragraph": m.Paragraph, "phrase": m.Phrase, "image": m.Image,
         "scene": m.Scene, "silence": m.Silence, "sound": m.Sound}
FIELDS = {
    "paragraph": {"typesetting_method": "typesetting_method"},
    "phrase": {"latex": "latex", "speaker_source": "speaker_source", "pronunciation": "pronunciation", "delta": "delta"},
    "image": {"description": "description", "prompt": "prompt", "animation_prompt": "animation_prompt",
              "workflow_image_template": "image_workflow", "workflow_i2v_template": "animation_workflow", "seed": "seed"},
    "scene": {"description": "description", "time_of_day": "time_of_day"},
    "sound": {"name": "name"},
}


def clone(tree):
    result = deepcopy(tree)
    originals = [tree, *tree.find_all(True)]
    copies = [result, *result.find_all(True)]
    for old, new in zip(originals, copies):
        if "_record_id" in old.__dict__:
            new.__dict__["_record_id"] = old.__dict__["_record_id"]
    return result


def remember_content(chapter):
    chapter._persisted_content = clone(chapter.soup)


def _identity(tag):
    return tag.__dict__.get("_record_id")


def _find(tree, tag):
    if tree is None:
        return None
    candidates = [tree] if tree.name == tag.name else []
    candidates += list(tree.find_all(tag.name))
    identity = _identity(tag)
    if identity:
        return next((other for other in candidates if _identity(other) == identity), None)
    if tag.name in ("chapter", "book"):
        return candidates[0] if candidates else None
    for key in ("id", "index"):
        if tag.get(key) is not None:
            return next((other for other in candidates if str(other.get(key)) == str(tag[key])), None)
    raise ValueError(f"Cannot save {tag.name} without an index or id")


def _number(value, integer=False):
    if value is None or str(value).strip() == "":
        return None
    try:
        number = Decimal(str(value))
        if not number.is_finite():
            raise ValueError("Non-finite chapter number")
        if integer and number != number.to_integral_value():
            raise ValueError(f"Expected an integer, got {value!r}")
        return int(number) if integer else number
    except InvalidOperation as error:
        raise ValueError(f"Invalid numeric chapter attribute {value!r}") from error


def _typed(key, value):
    if key in ("index", "frames", "seed"):
        return _number(value, True)
    if key == "duration":
        return _number(value)
    if key == "delta":
        import json
        return json.loads(value) if isinstance(value, str) else value
    return value


def _assign(row, tag):
    row.attributes = deepcopy(tag.attrs)
    if isinstance(row, m.Chapter):
        return
    # Non-numeric historical paragraph indexes remain intact in attributes.
    try:
        row.legacy_index = _typed("index", tag.get("index"))
    except ValueError:
        row.legacy_index = None
    if isinstance(row, m.TimelineItem):
        row.legacy_id = tag.get("id")
        row.duration_seconds = _typed("duration", tag.get("duration"))
        row.frame_count = _typed("frames", tag.get("frames"))
    for key, column in FIELDS.get(tag.name, {}).items():
        value = _typed(key, tag.get(key))
        if column in ("description", "name") and value is None:
            value = ""
        setattr(row, column, value)
    if isinstance(row, m.Phrase):
        row.text = tag.get_text()


def _attributes(row, kind):
    attrs = deepcopy(row.attributes)
    fields = dict(FIELDS.get(kind, {}))
    if isinstance(row, m.TimelineItem):
        fields.update(duration="duration_seconds", frames="frame_count", id="legacy_id")
    for key, column in fields.items():
        value = getattr(row, column)
        original = attrs.get(key)
        if value is None:
            attrs.pop(key, None)
        elif original is not None and (
                _typed(key, original) == value or
                (key == "duration" and _typed(key, original).quantize(Decimal("0.000001")) == value)):
            pass  # Preserve the original spelling/precision and JSON formatting.
        elif original is not None or value != "":
            if key == "delta":
                import json
                attrs[key] = json.dumps(value)
            else:
                attrs[key] = str(value)
    return attrs


def _locked_chapter(session, chapter):
    # Serialize natural-key creation and imports for an author, including two
    # workers creating different books for that same new author.
    # Transaction-scoped and deterministic across Python processes.
    name = str(chapter.author.name)
    lock = int.from_bytes(hashlib.sha256(name.encode()).digest()[:8], "big", signed=True)
    session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock})
    author = session.scalar(select(m.Author).where(m.Author.name == name))
    if author is None:
        author = m.Author(name=name)
        session.add(author)
        session.flush()
    book = session.scalar(select(m.Book).where(m.Book.author_id == author.id, m.Book.title == str(chapter.title)))
    if book is None:
        book = m.Book(author_id=author.id, title=str(chapter.title))
        session.add(book)
        session.flush()
    row = session.scalar(select(m.Chapter).where(m.Chapter.book_id == book.id,
                         m.Chapter.number == int(chapter.number), m.Chapter.language == chapter.language).with_for_update())
    if row is None:
        row = m.Chapter(book_id=book.id, number=int(chapter.number), language=chapter.language,
                        title=getattr(chapter, "chapter_title", ""), mood=getattr(chapter, "mood", ""),
                        theme=getattr(chapter, "theme", ""))
        session.add(row)
        session.flush()
    return row


def _rows(session, row):
    records = {str(row.id): row}
    for kind in (m.Paragraph, m.TimelineItem):
        records.update((str(item.id), item) for item in session.scalars(select(kind).where(kind.chapter_id == row.id)))
    return records


def _sync(session, row, tree, baseline=None):
    root = tree if tree.name in ("chapter", "book") else tree.find(["chapter", "book"])
    if root is None:
        raise ValueError("Chapter content has no chapter/book root")
    records = _rows(session, row)
    kept = {str(row.id)}
    positions = {}
    legacy_indexes = set()
    paragraph_position = 0

    def encode(node, paragraph=None):
        nonlocal paragraph_position
        if not isinstance(node, Tag):
            return {"comment" if isinstance(node, Comment) else "text": str(node)}
        if node is root:
            entity = row
        elif node.name in KINDS:
            identity = _identity(node)
            if identity is None and baseline is not None:
                old = _find(baseline, node)
                identity = _identity(old) if old is not None else None
            entity = records.get(identity)
            if entity is not None and not isinstance(entity, KINDS[node.name]):
                raise ValueError("Timeline record kind cannot change")
            if entity is None:
                entity = KINDS[node.name](id=uuid4(), chapter_id=row.id)
                session.add(entity)
            if node.name == "paragraph":
                entity.position = paragraph_position
                paragraph_position += 1
                paragraph = entity
            else:
                if paragraph is None:
                    raise ValueError(f"{node.name} must belong to a paragraph")
                entity.paragraph_id = paragraph.id
                entity.position = positions.get(paragraph.id, 0)
                positions[paragraph.id] = entity.position + 1
            kept.add(str(entity.id))
        else:
            return {"tag": node.name, "attrs": deepcopy(node.attrs),
                    "children": [encode(child, paragraph) for child in node.contents]}
        node.__dict__["_record_id"] = str(entity.id)
        _assign(entity, node)
        if not isinstance(entity, m.Chapter) and entity.legacy_index is not None:
            key = (node.name, entity.legacy_index)
            if key in legacy_indexes:
                # Some old chapters reused paragraph indexes for inserted
                # images. Keep their path-facing attribute verbatim; UUID and
                # position distinguish them in the relational model.
                entity.legacy_index = None
            else:
                legacy_indexes.add(key)
        entity.content_layout = {"tag": node.name, "children": [encode(child, paragraph) for child in node.contents]}
        return {"record": str(entity.id)}

    # Clear only indexes being changed or removed. Ordinary record saves must
    # not rewrite every timeline row; swaps still need two flushes because the
    # legacy-index uniqueness constraints are immediate.
    desired_indexes = {}
    seen_indexes = set()
    for tag in root.find_all(list(KINDS)):
        identity = _identity(tag)
        if identity is None and baseline is not None:
            old = _find(baseline, tag)
            identity = _identity(old) if old is not None else None
        try:
            index = _typed("index", tag.get("index"))
        except ValueError:
            index = None
        key = (tag.name, index)
        if key in seen_indexes:
            index = None
        else:
            seen_indexes.add(key)
        if identity:
            desired_indexes[identity] = index
    cleared = False
    for identity, entity in records.items():
        if not isinstance(entity, m.Chapter) and entity.legacy_index != desired_indexes.get(identity):
            entity.legacy_index = None
            cleared = True
    if cleared:
        session.flush()
    encode(root)
    for identity, entity in records.items():
        if identity not in kept:
            session.delete(entity)
    # Paragraph FKs must exist before inserting timeline rows. SQLAlchemy has
    # no relationship edges for these scalar assignments.
    session.flush([entity for entity in session.new if isinstance(entity, m.Paragraph)])
    session.flush()


def _project(session, row):
    records = _rows(session, row)
    soup = BeautifulSoup("", "xml")

    def decode(node):
        if "text" in node:
            return NavigableString(node["text"])
        if "comment" in node:
            return Comment(node["comment"])
        entity = records[node["record"]] if "record" in node else None
        layout = entity.content_layout if entity is not None else node
        tag = soup.new_tag(layout["tag"])
        tag.attrs = _attributes(entity, tag.name) if entity is not None else deepcopy(node["attrs"])
        if entity is not None:
            tag.__dict__["_record_id"] = str(entity.id)
        for child in layout["children"]:
            tag.append(decode(child))
        if isinstance(entity, m.Phrase) and entity.text != tag.get_text():
            tag.clear()
            tag.append(entity.text)
        return tag

    soup.append(decode({"record": str(row.id)}))
    return soup


def _initialize(session, row, chapter, upgrade=None):
    if row.content_layout is not None:
        return False
    # Existing relational content must never be overwritten by legacy import.
    if session.scalar(select(m.Paragraph.id).where(m.Paragraph.chapter_id == row.id).limit(1)):
        raise RuntimeError("Chapter has relational records without a compatibility layout; import refused")
    path = Path(chapter.get_xml_fn())
    data = path.read_bytes() if path.is_file() else None
    tree = BeautifulSoup(data, "xml") if data is not None else BeautifulSoup("<chapter/>", "xml")
    if tree.find(["chapter", "book"]) is None:
        raise ValueError(f"Invalid legacy chapter: {path}")
    if upgrade is not None and data is not None:
        tree = upgrade(chapter, tree)
    _sync(session, row, tree)
    row.legacy_import = {"path": str(path) if data is not None else None,
                         "sha256": hashlib.sha256(data).hexdigest() if data is not None else None,
                         "at": datetime.now(timezone.utc).isoformat()}
    return True


def load_content(chapter, *, upgrade=None, engine=None):
    with session_scope(engine=engine) as session:
        row = _locked_chapter(session, chapter)
        _initialize(session, row, chapter, upgrade)
        tree = _project(session, row)
    chapter.soup = tree
    remember_content(chapter)
    return tree


def import_legacy(chapter, *, upgrade=None, engine=None):
    """Import once; later calls never replace existing database content."""
    with session_scope(engine=engine) as session:
        row = _locked_chapter(session, chapter)
        imported = _initialize(session, row, chapter, upgrade)
        return row.id, imported


def read_content(*, author, title, number, language, engine=None):
    """Read an existing chapter without creating rows or triggering import."""
    with session_scope(engine=engine) as session:
        session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        row = session.scalar(select(m.Chapter).join(m.Book).join(m.Author).where(
            m.Author.name == author, m.Book.title == title,
            m.Chapter.number == int(number), m.Chapter.language == language))
        return _project(session, row) if row is not None and row.content_layout is not None else None


def replace_content(chapter, content, *, engine=None):
    if not isinstance(content, (BeautifulSoup, Tag)):
        raise TypeError("Chapter content must be a parsed chapter")
    with session_scope(engine=engine) as session:
        row = _locked_chapter(session, chapter)
        _sync(session, row, content, getattr(chapter, "_persisted_content", None))
    chapter.soup = content
    remember_content(chapter)


def save_records(chapter, records, *, include_contents=True, engine=None):
    records = list(records)
    if not records:
        return
    baseline = getattr(chapter, "_persisted_content", None)
    if baseline is None:
        raise RuntimeError("Load chapter content or use replace_content() before saving records")
    committed = []
    with session_scope(engine=engine) as session:
        row = _locked_chapter(session, chapter)
        if row.content_layout is None:
            raise RuntimeError("Persist the chapter timeline before saving its records")
        latest = _project(session, row)
        for record in records:
            if not isinstance(record, Tag):
                record = getattr(record, "phrase_xml", record)
            if not isinstance(record, Tag):
                raise TypeError("Expected a chapter record")
            previous = _find(baseline, record)
            target = _find(latest, previous if previous is not None else record)
            if previous is None or target is None:
                raise ValueError(f"{record.name} is not persisted; use save_timeline() for structural changes")
            for key, value in record.attrs.items():
                if key not in previous.attrs or str(value) != str(previous[key]):
                    target[key] = deepcopy(value)
            for key in set(previous.attrs) - set(record.attrs):
                target.attrs.pop(key, None)
            changed = include_contents and record.decode_contents() != previous.decode_contents()
            if changed:
                target.clear()
                for child in record.contents:
                    target.append(clone(child) if isinstance(child, Tag) else deepcopy(child))
            committed.append((previous, clone(record), changed))
        _sync(session, row, latest)
    for previous, saved, changed in committed:
        previous.attrs = deepcopy(saved.attrs)
        if changed:
            previous.clear()
            for child in saved.contents:
                previous.append(clone(child) if isinstance(child, Tag) else deepcopy(child))


def delete_content(chapter, *, engine=None):
    with session_scope(engine=engine) as session:
        row = _locked_chapter(session, chapter)
        # Chapter.delete removes the entire numbered chapter directory,
        # including every language, so the database must follow that scope.
        for sibling in session.scalars(select(m.Chapter).where(
                m.Chapter.book_id == row.book_id, m.Chapter.number == row.number)):
            session.delete(sibling)
