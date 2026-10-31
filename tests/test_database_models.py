"""PostgreSQL integration tests use a disposable schema, never application rows."""
from contextlib import contextmanager
from decimal import Decimal
import os
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

import pytest

pytest.importorskip("sqlalchemy")
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, delete, inspect, insert, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool
from sqlalchemy.schema import CreateSchema, DropSchema

from artifact_editor.db import Base, session as connections
from artifact_editor.db.models import (
    Author, Book, Chapter, Character, Image, MediaAsset, MediaKind, Paragraph,
    Phrase, Provider, Scene, Section, Silence, Sound, Task, TaskDependency,
    TaskState, TimelineItem, TimelineKind,
)

ROOT = Path(__file__).resolve().parents[1]


def migrate(engine, action):
    config = Config(str(ROOT / "src/alembic.ini"))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        if action == "check":
            command.check(config)
        elif action == "downgrade":
            command.downgrade(config, "base")
        else:
            command.upgrade(config, "head")


@contextmanager
def isolated_database():
    url = os.environ.get("BOOKADAPTOR_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set BOOKADAPTOR_TEST_DATABASE_URL for PostgreSQL integration tests")
    schema = "test_orm_" + uuid4().hex
    admin = create_engine(url, isolation_level="AUTOCOMMIT", poolclass=NullPool)
    engine = None
    try:
        with admin.connect() as connection:
            connection.execute(CreateSchema(schema))
        engine = create_engine(url, poolclass=NullPool,
                               connect_args={"options": f"-csearch_path={schema}"})
        migrate(engine, "upgrade")
        yield engine
    finally:
        if engine is not None:
            engine.dispose()
        with admin.connect() as connection:
            connection.execute(DropSchema(schema, cascade=True, if_exists=True))
        admin.dispose()


@pytest.fixture(scope="module")
def engine():
    with isolated_database() as value:
        yield value


@pytest.fixture
def session(engine):
    with Session(engine) as value:
        yield value
        value.rollback()


def chapter_graph(session):
    author = Author(name="ORM test " + uuid4().hex)
    book = Book(author=author, title="Test book")
    chapter = Chapter(book=book, number=1)
    section = Section(chapter=chapter, position=0, delta={"ops": [{"insert": "Hello"}]})
    paragraph = Paragraph(chapter=chapter, section=section, position=0, legacy_index=0)
    session.add(author)
    session.flush()
    return chapter, paragraph


def test_url_normalizes_driver_and_preserves_escaped_password(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://reader:p%40ss@localhost:5432/bookadaptor")
    url = connections.database_url()
    assert url.drivername == "postgresql+psycopg"
    assert url.password == "p@ss"
    assert "p@ss" not in str(url)
    monkeypatch.setenv("DATABASE_URL", "sqlite:///example.db")
    with pytest.raises(ValueError, match="PostgreSQL"):
        connections.database_url()


def test_engine_is_lazy_reused_and_replaced_after_fork(monkeypatch):
    monkeypatch.setattr(connections, "_engines", {})
    first, second = Mock(), Mock()
    create = Mock(side_effect=[first, second])
    monkeypatch.setattr(connections, "create_engine", create)
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://user:pass@localhost/db")
    assert connections.get_engine() is first
    assert connections.get_engine() is first
    first.connect.assert_not_called()
    connections._after_fork()
    first.dispose.assert_called_once_with(close=False)
    assert connections.get_engine() is second


def test_timeline_roundtrip_keeps_mixed_order_and_typed_fields(session):
    chapter, paragraph = chapter_graph(session)
    paragraph.items.extend([
        Image(position=2, legacy_index=0, description="A road", seed=2**62),
        Scene(position=0, legacy_index=0, description="Forest"),
        Phrase(position=1, legacy_index=0, text="Tip & Jack", delta=[{"insert": "Tip & Jack"}],
               duration_seconds=Decimal("1.125"), frame_count=28),
        Silence(position=3, duration_seconds=Decimal("0.5")),
        Sound(position=4, name="Footsteps"),
    ])
    session.flush()
    paragraph_id = paragraph.id
    session.expire_all()
    loaded = session.get(Paragraph, paragraph_id)
    assert [type(item) for item in loaded.items] == [Scene, Phrase, Image, Silence, Sound]
    assert loaded.items[1].duration_seconds == Decimal("1.125")
    assert loaded.items[1].delta == [{"insert": "Tip & Jack"}]
    assert loaded.items[2].seed == 2**62
    assert all(item.chapter_id == chapter.id for item in loaded.items)
    assert loaded.section.delta["ops"][0]["insert"] == "Hello"


def test_order_can_swap_without_changing_identity(engine):
    with connections.session_scope(engine=engine) as session:
        _, paragraph = chapter_graph(session)
        first, second = Image(position=0), Phrase(position=1, text="Hello")
        paragraph.items.extend([first, second])
        session.flush()
        ids = first.id, second.id
        first.position, second.position = second.position, first.position
    with Session(engine) as session:
        assert session.get(Image, ids[0]).position == 1
        assert session.get(Phrase, ids[1]).position == 0


def test_concurrent_workers_update_independent_image_fields(engine):
    with connections.session_scope(engine=engine) as session:
        _, paragraph = chapter_graph(session)
        image = Image(position=0, description="Original")
        paragraph.items.append(image)
        session.flush()
        image_id = image.id
    with Session(engine) as first, Session(engine) as second:
        first_image, second_image = first.get(Image, image_id), second.get(Image, image_id)
        first_image.description = "Current character descriptions"
        second_image.animation_prompt = "Walk down the road"
        first.commit()
        second.commit()
    with Session(engine) as session:
        actual = session.get(Image, image_id)
        assert actual.description == "Current character descriptions"
        assert actual.animation_prompt == "Walk down the road"


def test_json_attributes_track_in_place_edits_and_sessions_rollback(engine):
    with connections.session_scope(engine=engine) as session:
        chapter, _ = chapter_graph(session)
        chapter_id = chapter.id
    with connections.session_scope(engine=engine) as session:
        loaded = session.get(Chapter, chapter_id)
        loaded.attributes["legacy_setting"] = "Keep this"
        assert session.is_modified(loaded, include_collections=True)
    with pytest.raises(RuntimeError, match="cancelled"):
        with connections.session_scope(engine=engine) as session:
            session.get(Chapter, chapter_id).title = "Must roll back"
            raise RuntimeError("cancelled")
    with Session(engine) as session:
        actual = session.get(Chapter, chapter_id)
        assert actual.attributes["legacy_setting"] == "Keep this"
        assert actual.title == ""


def test_timeline_membership_and_negative_duration_are_rejected(session):
    chapter, paragraph = chapter_graph(session)
    other, _ = chapter_graph(session)
    with pytest.raises(IntegrityError):
        with session.begin_nested():
            session.execute(insert(TimelineItem.__table__).values(
                id=uuid4(), chapter_id=other.id, paragraph_id=paragraph.id,
                kind=TimelineKind.IMAGE, position=0,
            ))
    with pytest.raises(IntegrityError):
        with session.begin_nested():
            paragraph.items.append(Silence(position=0, duration_seconds=Decimal("-1")))
            session.flush()


def test_character_tags_are_unique_within_their_scope(session):
    chapter, _ = chapter_graph(session)
    other = Chapter(book=chapter.book, number=2)
    session.add(other)
    session.flush()
    session.add_all([
        Character(book_id=chapter.book_id, tag="Guard", name="Book guard"),
        Character(book_id=chapter.book_id, chapter_id=chapter.id, tag="Guard", name="First guard"),
        Character(book_id=chapter.book_id, chapter_id=other.id, tag="Guard", name="Other guard"),
    ])
    session.flush()
    with pytest.raises(IntegrityError):
        with session.begin_nested():
            session.add(Character(book_id=chapter.book_id, tag="Guard", name="Duplicate"))
            session.flush()


def test_task_dependencies_reference_the_actual_image_and_source_asset(session):
    chapter, paragraph = chapter_graph(session)
    source = MediaAsset(book_id=chapter.book_id, chapter_id=chapter.id,
                        kind=MediaKind.IMAGE, path="images/source.png", width=512, height=512)
    image = Image(position=0, image_asset=source)
    paragraph.items.append(image)
    cpu = Task(chapter_id=chapter.id, item=image, input_asset=source,
               action="animate_image", provider=Provider.CPU, state=TaskState.BLOCKED)
    gpu = Task(chapter_id=chapter.id, item=image, input_asset=source,
               action="gpu_workflow", provider=Provider.COMFYUI)
    cpu.dependencies.append(TaskDependency(depends_on=gpu))
    session.add(cpu)
    session.flush()
    ready = select(Task).where(
        Task.id == cpu.id,
        ~Task.dependencies.any(TaskDependency.depends_on.has(Task.state != TaskState.COMPLETE)),
    )
    assert session.scalar(ready) is None
    gpu.state = TaskState.COMPLETE
    session.flush()
    assert session.scalar(ready).input_asset_id == image.image_asset_id
    with pytest.raises(IntegrityError):
        with session.begin_nested():
            session.add(TaskDependency(task_id=cpu.id, depends_on_task_id=cpu.id))
            session.flush()


def test_deleting_chapter_cascades_content_and_tasks_preserving_book_character(session):
    chapter, paragraph = chapter_graph(session)
    image = Image(position=0)
    paragraph.items.append(image)
    shared = Character(book_id=chapter.book_id, tag="Tip", name="Tip")
    task = Task(chapter_id=chapter.id, item=image, action="animate", provider=Provider.CPU)
    session.add_all([shared, task])
    session.flush()
    chapter_id, image_id, task_id, character_id = chapter.id, image.id, task.id, shared.id
    session.execute(delete(Chapter).where(Chapter.id == chapter_id))
    session.expire_all()
    assert session.get(TimelineItem, image_id) is None
    assert session.get(Task, task_id) is None
    assert session.get(Character, character_id) is not None


def test_migration_roundtrip_and_autogenerate_ignore_unmanaged_tables():
    with isolated_database() as engine:
        tables = set(inspect(engine).get_table_names())
        assert tables == set(Base.metadata.tables) | {"alembic_version"}
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE user_managed_notes (note text)"))
        migrate(engine, "check")
        migrate(engine, "downgrade")
        assert set(inspect(engine).get_table_names()) == {"alembic_version", "user_managed_notes"}
        assert not inspect(engine).get_enums()
        migrate(engine, "upgrade")
        migrate(engine, "check")
