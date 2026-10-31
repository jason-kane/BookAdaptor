"""Chapter adapter/import contracts against isolated PostgreSQL schemas."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from bs4 import BeautifulSoup
import pytest

pytest.importorskip('sqlalchemy')
from sqlalchemy import select
from sqlalchemy.orm import Session
from artifact_editor.db import chapter_content as storage
from artifact_editor.db import models as m
from test_database_models import engine


XML = '''<chapter version="1.1" style="ink"><!--keep--><paragraph index="0" id="intro" tags="spoken-only=true"/>
<paragraph index="9" id="body"><scene index="0" description="Forest"/><image index="4" src="old.png" duration="7.72" frames="193" seed="8399" description="A road" custom="yes"/>
<phrase index="0" speaker="Tip" delta='[{"insert":"Hello"}]' latex="hi">Hello <em>Jack &amp; Jill</em>!</phrase><silence index="0" duration="0.5"/><sound index="0" name="bell"/></paragraph></chapter>'''


@pytest.fixture
def chapter(tmp_path):
    path = tmp_path / 'chapter.xml'
    path.write_text(XML)
    return SimpleNamespace(author=SimpleNamespace(name='Import '+uuid4().hex), title='Book',
                           number=3, language='english', chapter_title='Three', mood='happy', theme='Oz',
                           get_xml_fn=lambda: str(path), path=path)


def load(chapter, engine):
    return storage.load_content(chapter, engine=engine)


def again(chapter):
    return SimpleNamespace(**{key: value for key, value in vars(chapter).items()
                              if key not in ('soup', '_persisted_content')})


def test_import_roundtrip_preserves_markup_unknown_attributes_order_and_typed_fields(chapter, engine):
    tree = load(chapter, engine)
    assert str(tree) == str(BeautifulSoup(XML, 'xml'))
    with Session(engine) as session:
        image = session.scalar(select(m.Image).where(m.Image.id == storage._identity(tree.image)))
        assert image.seed == 8399
        assert image.duration_seconds == storage.Decimal('7.72')
        assert image.frame_count == 193
        assert image.description == 'A road'
        assert image.attributes['custom'] == 'yes'
        assert session.get(m.Chapter, image.chapter_id).legacy_import['sha256']
    assert chapter.path.read_text() == XML


def test_import_once_even_after_database_emptied(chapter, engine):
    identity, imported = storage.import_legacy(chapter, engine=engine)
    assert imported
    load(chapter, engine)
    storage.replace_content(chapter, BeautifulSoup('<chapter/>', 'xml'), engine=engine)
    chapter.path.write_text(XML.replace('old.png', 'stale.png'))
    assert storage.import_legacy(chapter, engine=engine) == (identity, False)
    assert load(chapter, engine).image is None


def test_database_load_survives_missing_legacy_file(chapter, engine):
    load(chapter, engine)
    chapter.path.unlink()
    assert load(chapter, engine).image['src'] == 'old.png'


def test_workers_merge_independent_fields_and_keep_unsaved_edits(chapter, engine):
    first = chapter
    second = again(chapter)
    load(first, engine); load(second, engine)
    first.soup.image['src'] = 'new.png'
    second.soup.image['animated_src'] = 'new.mp4'
    first.soup.phrase['speaker'] = 'Jack'
    storage.save_records(first, [first.soup.image], engine=engine)
    storage.save_records(second, [second.soup.image], engine=engine)
    actual = load(again(chapter), engine)
    assert actual.image['src'] == 'new.png'
    assert actual.image['animated_src'] == 'new.mp4'
    assert actual.phrase['speaker'] == 'Tip'
    storage.save_records(first, [first.soup.phrase], engine=engine)
    assert load(again(chapter), engine).phrase['speaker'] == 'Jack'


def test_attribute_removal_and_phrase_content(chapter, engine):
    load(chapter, engine)
    del chapter.soup.image['description']
    chapter.soup.phrase.clear(); chapter.soup.phrase.append('New words')
    storage.save_records(chapter, [chapter.soup.image, chapter.soup.phrase], engine=engine)
    actual = load(chapter, engine)
    assert 'description' not in actual.image.attrs
    assert actual.phrase.text == 'New words'


def test_move_reindex_and_delete_preserve_identity_and_task_reference(chapter, engine):
    tree = load(chapter, engine)
    identity = storage._identity(tree.image)
    with storage.session_scope(engine=engine) as session:
        task = m.Task(item_id=identity, action='animate', provider=m.Provider.CPU)
        session.add(task); session.flush(); task_id = task.id
    image = tree.image.extract()
    image['index'] = '30'
    tree.paragraph.append(image)
    tree.sound.decompose()
    storage.replace_content(chapter, tree, engine=engine)
    actual = load(chapter, engine)
    assert storage._identity(actual.image) == identity
    assert actual.image.find_parent('paragraph')['index'] == '0'
    assert actual.image['index'] == '30'
    assert actual.sound is None
    with Session(engine) as session:
        assert str(session.get(m.Task, task_id).item_id) == identity


def test_structural_save_accepts_deepcopy_without_changing_ids(chapter, engine):
    tree = load(chapter, engine)
    identity = storage._identity(tree.image)
    tree = deepcopy(tree)
    tree.image['src'] = 'copied.png'
    storage.replace_content(chapter, tree, engine=engine)
    assert storage._identity(load(chapter, engine).image) == identity


def test_invalid_import_rolls_back_and_can_be_retried(chapter, engine):
    chapter.path.write_text('<chapter><paragraph index="0"><image index="0" duration="bad"/></paragraph></chapter>')
    with pytest.raises(ValueError, match='Invalid numeric'):
        storage.import_legacy(chapter, engine=engine)
    chapter.path.write_text(XML)
    assert storage.import_legacy(chapter, engine=engine)[1]


def test_failed_save_keeps_baseline_and_database(chapter, engine):
    load(chapter, engine)
    chapter.soup.image['frames'] = '-10'
    from sqlalchemy.exc import IntegrityError
    with pytest.raises(IntegrityError):
        storage.save_records(chapter, [chapter.soup.image], engine=engine)
    assert chapter._persisted_content.image['frames'] == '193'
    assert load(again(chapter), engine).image['frames'] == '193'


def test_record_save_rejects_deleted_row(chapter, engine):
    load(chapter, engine)
    stale = again(chapter); load(stale, engine)
    chapter.soup.image.decompose()
    storage.replace_content(chapter, chapter.soup, engine=engine)
    stale.soup.image['src'] = 'resurrect.png'
    with pytest.raises(ValueError, match='not persisted'):
        storage.save_records(stale, [stale.soup.image], engine=engine)


def test_direct_orm_edits_project_into_compatibility_tags(chapter, engine):
    tree = load(chapter, engine)
    with storage.session_scope(engine=engine) as session:
        image = session.get(m.Image, storage._identity(tree.image))
        image.description = 'New description'
        image.frame_count = 250
        phrase = session.get(m.Phrase, storage._identity(tree.phrase))
        phrase.text = 'ORM text'
    actual = load(chapter, engine)
    assert actual.image['description'] == 'New description'
    assert actual.image['frames'] == '250'
    assert actual.phrase.text == 'ORM text'


def test_delete_chapter_cascades_content(chapter, engine):
    tree = load(chapter, engine)
    identity = storage._identity(tree.image)
    storage.delete_content(chapter, engine=engine)
    with Session(engine) as session:
        assert session.get(m.Image, identity) is None


def test_duplicate_historical_paragraph_indexes_preserve_both_records(chapter, engine):
    chapter.path.write_text('<chapter version="1.1"><paragraph index="5"><image index="0"/></paragraph>'
                            '<paragraph index="5" id="body"><phrase index="0">Text</phrase></paragraph></chapter>')
    tree = load(chapter, engine)
    assert [p['index'] for p in tree.find_all('paragraph')] == ['5', '5']
    identities = [storage._identity(p) for p in tree.find_all('paragraph')]
    storage.replace_content(chapter, deepcopy(tree), engine=engine)
    actual = load(chapter, engine)
    assert [storage._identity(p) for p in actual.find_all('paragraph')] == identities
    actual.phrase['speaker'] = 'Tip'
    storage.save_records(chapter, [actual.phrase], engine=engine)
    assert load(chapter, engine).phrase['speaker'] == 'Tip'


def test_concurrent_first_load_imports_only_once(chapter, engine):
    from concurrent.futures import ThreadPoolExecutor
    def run(_):
        other = again(chapter)
        return storage._identity(load(other, engine).image)
    with ThreadPoolExecutor(max_workers=4) as pool:
        identities = list(pool.map(run, range(4)))
    assert len(set(identities)) == 1


def test_index_swap_keeps_rows_and_saved_record_identity(chapter, engine):
    tree = load(chapter, engine)
    new = tree.new_tag('image', index='5', src='second.png')
    tree.find_all('paragraph')[-1].append(new)
    storage.replace_content(chapter, tree, engine=engine)
    first, second = tree.find_all('image')
    identities = storage._identity(first), storage._identity(second)
    first['index'], second['index'] = '5', '4'
    storage.replace_content(chapter, tree, engine=engine)
    first['description'] = 'After reindex'
    storage.save_records(chapter, [first], engine=engine)
    actual = load(chapter, engine)
    assert tuple(storage._identity(i) for i in actual.find_all('image')) == identities
    assert actual.image['index'] == '5'
    assert actual.image['description'] == 'After reindex'


def test_read_only_snapshot_and_empty_database_override_legacy(chapter, engine, monkeypatch):
    from artifact_editor.chapter_source import read_chapter_source
    from artifact_editor.db import session as connections
    path = chapter.path.parent / chapter.author.name / chapter.title / 'chapter/0003/english/chapter.xml'
    path.parent.mkdir(parents=True)
    path.write_text(XML)
    monkeypatch.setenv('CHAPTER_STORAGE', 'postgres')
    monkeypatch.setattr(connections, 'get_engine', lambda: engine)
    chapter.get_xml_fn = lambda: str(path)
    assert read_chapter_source(path).image['src'] == 'old.png'
    load(chapter, engine)
    storage.replace_content(chapter, BeautifulSoup('<chapter/>', 'xml'), engine=engine)
    assert read_chapter_source(path).image is None


def test_import_preserves_original_duration_beyond_column_precision(chapter, engine):
    chapter.path.write_text(XML.replace('7.72', '7.713333333333334'))
    tree = load(chapter, engine)
    assert tree.image['duration'] == '7.713333333333334'
    tree.image['src'] = 'another.png'
    storage.save_records(chapter, [tree.image], engine=engine)
    assert load(chapter, engine).image['duration'] == '7.713333333333334'


def test_delete_removes_all_languages_for_numbered_chapter(chapter, engine):
    english = load(chapter, engine)
    other = again(chapter); other.language = 'french'
    french = load(other, engine)
    identities = [storage._identity(english.image), storage._identity(french.image)]
    storage.delete_content(chapter, engine=engine)
    with Session(engine) as session:
        assert all(session.get(m.Image, identity) is None for identity in identities)


def test_legacy_upgrade_during_import_has_no_filesystem_cleanup(chapter, engine):
    import ast
    import os
    import shutil
    from unittest.mock import Mock
    from bs4 import Tag
    source = Path(__file__).parents[1] / 'src/artifact_editor/chapter/chapter.py'
    function = next(node for node in ast.parse(source.read_text()).body
                    if isinstance(node, ast.FunctionDef) and node.name == 'xml_upgrade')
    namespace = dict(Chapter=object, Tag=Tag, os=os, shutil=shutil, log=Mock(),
                     const=SimpleNamespace(LIBRARY_DIR=str(chapter.path.parent)))
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(source), 'exec'), namespace)
    chapter.get_paragraph_dir = lambda index: str(chapter.path.parent / 'paragraphs' / str(index))
    old_animation = Path(chapter.get_paragraph_dir(9)) / 'animation' / 'old-bug,'
    old_animation.mkdir(parents=True)
    chapter.path.write_text(XML.replace('version="1.1"', 'version="0"'))
    original = chapter.path.read_bytes()
    tree = storage.load_content(chapter, engine=engine,
        upgrade=lambda c, tree: namespace['xml_upgrade'](c, tree, migrate_files=False))
    assert tree.chapter['version'] == '1.1'
    assert old_animation.is_dir()
    assert chapter.path.read_bytes() == original


def test_image_completion_snapshot_reads_database_selection(chapter, engine, monkeypatch):
    from artifact_editor.images.completions import chapter_image_snapshot
    from artifact_editor.db import session as connections
    root = chapter.path.parent
    directory = root / chapter.author.name / chapter.title / 'chapter/0003'
    path = directory / 'english/chapter.xml'
    path.parent.mkdir(parents=True)
    path.write_text(XML)
    chapter.get_xml_fn = lambda: str(path)
    monkeypatch.setenv('CHAPTER_STORAGE', 'postgres')
    monkeypatch.setattr(connections, 'get_engine', lambda: engine)
    tree = load(chapter, engine)
    tree.image['src'] = 'fresh.png'
    tree.image['image_generation_id'] = 'fresh-generation'
    image_path = directory / 'paragraphs/000009/fresh.png'
    image_path.parent.mkdir(parents=True)
    image_path.touch()
    storage.save_records(chapter, [tree.image], engine=engine)
    result = chapter_image_snapshot(dict(author=chapter.author.name, title=chapter.title,
                                        chapter_number=3, language='english'), root)
    assert [image['id'] for image in result['images']] == ['fresh-generation']
    assert path.read_text() == XML


def test_import_cli_is_idempotent(chapter, engine):
    import os
    import subprocess
    import sys
    from sqlalchemy import text
    with engine.connect() as connection:
        schema = connection.scalar(text('SELECT current_schema()'))
    environment = {**os.environ, 'DATABASE_URL': os.environ['BOOKADAPTOR_TEST_DATABASE_URL'],
                   'PGOPTIONS': f'-c search_path={schema}'}
    command = [sys.executable, '-m', 'artifact_editor.db', 'import-chapter',
               '--author', chapter.author.name, '--title', chapter.title,
               '--number', '3', '--xml', str(chapter.path)]
    first = subprocess.run(command, env=environment, capture_output=True, text=True, timeout=30)
    assert first.returncode == 0, first.stderr
    assert 'Imported' in first.stdout
    second = subprocess.run(command, env=environment, capture_output=True, text=True, timeout=30)
    assert second.returncode == 0, second.stderr
    assert 'Already in PostgreSQL' in second.stdout
    assert chapter.path.read_text() == XML
