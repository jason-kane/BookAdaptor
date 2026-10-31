"""Relational chapter content, versioned media, and task dependencies."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
from uuid import UUID

from sqlalchemy import (BigInteger, CheckConstraint, DateTime, Enum as SQLAEnum,
                        ForeignKey, ForeignKeyConstraint, Index, Integer, Numeric,
                        Text, UniqueConstraint, text)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.mutable import MutableDict, MutableList
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, Identity, Timestamps


def json_object():
    return mapped_column(MutableDict.as_mutable(JSONB), default=dict,
                         server_default=text("'{}'::jsonb"))


def enum_type(kind, name):
    return SQLAEnum(kind, name=name, values_callable=lambda cls: [item.value for item in cls])


class TimelineKind(str, Enum):
    PHRASE = "phrase"
    IMAGE = "image"
    SCENE = "scene"
    SILENCE = "silence"
    SOUND = "sound"


class MediaKind(str, Enum):
    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"


class Provider(str, Enum):
    CPU = "cpu"
    COMFYUI = "comfyui"
    LLAMA = "llama"
    NONE = "none"


class TaskState(str, Enum):
    QUEUED = "queued"
    BLOCKED = "blocked"
    RUNNING = "running"
    COMPLETE = "complete"
    FAILED = "failed"
    CANCELLED = "cancelled"
    CANCELLING = "cancelling"


class Author(Identity, Timestamps, Base):
    __tablename__ = "authors"
    name: Mapped[str] = mapped_column(Text, unique=True)
    settings: Mapped[dict] = json_object()
    books: Mapped[list[Book]] = relationship(back_populates="author", cascade="all, delete-orphan", passive_deletes=True)


class Book(Identity, Timestamps, Base):
    __tablename__ = "books"
    __table_args__ = (UniqueConstraint("author_id", "title"),)
    author_id: Mapped[UUID] = mapped_column(ForeignKey("authors.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(Text)
    settings: Mapped[dict] = json_object()
    author: Mapped[Author] = relationship(back_populates="books")
    chapters: Mapped[list[Chapter]] = relationship(back_populates="book", order_by="Chapter.number, Chapter.language",
                                                  cascade="all, delete-orphan", passive_deletes=True)


class Chapter(Identity, Timestamps, Base):
    __tablename__ = "chapters"
    __table_args__ = (UniqueConstraint("book_id", "number", "language"),
                      CheckConstraint("number >= 0", name="number_nonnegative"))
    book_id: Mapped[UUID] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    language: Mapped[str] = mapped_column(Text, default="english", server_default="english")
    title: Mapped[str] = mapped_column(Text, default="", server_default="")
    mood: Mapped[str] = mapped_column(Text, default="", server_default="")
    theme: Mapped[str] = mapped_column(Text, default="", server_default="")
    source_delta: Mapped[dict | list | None] = mapped_column(JSONB)
    settings: Mapped[dict] = json_object()
    attributes: Mapped[dict] = json_object()
    content_layout: Mapped[dict | None] = mapped_column(JSONB)
    legacy_import: Mapped[dict | None] = mapped_column(JSONB)
    book: Mapped[Book] = relationship(back_populates="chapters")
    paragraphs: Mapped[list[Paragraph]] = relationship(back_populates="chapter", order_by="Paragraph.position",
                                                       cascade="all, delete-orphan", passive_deletes=True)
    sections: Mapped[list[Section]] = relationship(back_populates="chapter", order_by="Section.position",
                                                   cascade="all, delete-orphan", passive_deletes=True)


class Section(Identity, Timestamps, Base):
    __tablename__ = "sections"
    __table_args__ = (UniqueConstraint("chapter_id", "position", deferrable=True, initially="DEFERRED"),
                      CheckConstraint("position >= 0", name="position_nonnegative"))
    chapter_id: Mapped[UUID] = mapped_column(ForeignKey("chapters.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    typesetting_method: Mapped[str] = mapped_column(Text, default="plain", server_default="plain")
    delta: Mapped[dict] = json_object()
    attributes: Mapped[dict] = json_object()
    chapter: Mapped[Chapter] = relationship(back_populates="sections")


class Paragraph(Identity, Timestamps, Base):
    __tablename__ = "paragraphs"
    __table_args__ = (
        UniqueConstraint("id", "chapter_id"),
        UniqueConstraint("chapter_id", "position", deferrable=True, initially="DEFERRED"),
        UniqueConstraint("chapter_id", "legacy_index"),
        CheckConstraint("position >= 0", name="position_nonnegative"),
    )
    chapter_id: Mapped[UUID] = mapped_column(ForeignKey("chapters.id", ondelete="CASCADE"), index=True)
    section_id: Mapped[UUID | None] = mapped_column(ForeignKey("sections.id", ondelete="SET NULL"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    legacy_index: Mapped[int | None] = mapped_column(Integer)
    typesetting_method: Mapped[str | None] = mapped_column(Text)
    attributes: Mapped[dict] = json_object()
    content_layout: Mapped[dict | None] = mapped_column(JSONB)
    chapter: Mapped[Chapter] = relationship(back_populates="paragraphs")
    section: Mapped[Section | None] = relationship()
    items: Mapped[list[TimelineItem]] = relationship(back_populates="paragraph", order_by="TimelineItem.position",
                                                     cascade="all, delete-orphan", passive_deletes=True)


class TimelineItem(Identity, Timestamps, Base):
    __tablename__ = "timeline_items"
    __table_args__ = (
        ForeignKeyConstraint(["paragraph_id", "chapter_id"], ["paragraphs.id", "paragraphs.chapter_id"], ondelete="CASCADE"),
        UniqueConstraint("paragraph_id", "position", deferrable=True, initially="DEFERRED"),
        UniqueConstraint("chapter_id", "kind", "legacy_index"),
        CheckConstraint("position >= 0", name="position_nonnegative"),
        CheckConstraint("duration_seconds >= 0", name="duration_nonnegative"),
        CheckConstraint("frame_count >= 0", name="frames_nonnegative"),
        Index("ix_timeline_items_chapter_kind", "chapter_id", "kind"),
    )
    chapter_id: Mapped[UUID] = mapped_column(index=True)
    paragraph_id: Mapped[UUID] = mapped_column(index=True)
    kind: Mapped[TimelineKind] = mapped_column(enum_type(TimelineKind, "timeline_kind"))
    position: Mapped[int] = mapped_column(Integer)
    legacy_index: Mapped[int | None] = mapped_column(Integer)
    legacy_id: Mapped[str | None] = mapped_column(Text)
    duration_seconds: Mapped[Decimal | None] = mapped_column(Numeric(14, 6))
    frame_count: Mapped[int | None] = mapped_column(Integer)
    attributes: Mapped[dict] = json_object()
    content_layout: Mapped[dict | None] = mapped_column(JSONB)
    paragraph: Mapped[Paragraph] = relationship(back_populates="items")
    __mapper_args__ = {"polymorphic_on": kind, "polymorphic_abstract": True}


class MediaAsset(Identity, Timestamps, Base):
    __tablename__ = "media_assets"
    __table_args__ = (UniqueConstraint("book_id", "path", "revision"),
                      CheckConstraint("revision > 0", name="revision_positive"),
                      CheckConstraint("width > 0 AND height > 0", name="dimensions_positive"),
                      CheckConstraint("duration_seconds >= 0", name="duration_nonnegative"))
    book_id: Mapped[UUID] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"), index=True)
    chapter_id: Mapped[UUID | None] = mapped_column(ForeignKey("chapters.id", ondelete="CASCADE"), index=True)
    source_asset_id: Mapped[UUID | None] = mapped_column(ForeignKey("media_assets.id", ondelete="SET NULL"), index=True)
    kind: Mapped[MediaKind] = mapped_column(enum_type(MediaKind, "media_kind"))
    path: Mapped[str] = mapped_column(Text)
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    sha256: Mapped[str | None] = mapped_column(Text)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    duration_seconds: Mapped[Decimal | None] = mapped_column(Numeric(14, 6))
    details: Mapped[dict] = json_object()


class Character(Identity, Timestamps, Base):
    __tablename__ = "characters"
    __table_args__ = (
        Index("uq_characters_book_tag", "book_id", "tag", unique=True, postgresql_where=text("chapter_id IS NULL")),
        Index("uq_characters_chapter_tag", "chapter_id", "tag", unique=True, postgresql_where=text("chapter_id IS NOT NULL")),
    )
    book_id: Mapped[UUID] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"), index=True)
    chapter_id: Mapped[UUID | None] = mapped_column(ForeignKey("chapters.id", ondelete="CASCADE"), index=True)
    tag: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    aliases: Mapped[list] = mapped_column(MutableList.as_mutable(JSONB), default=list, server_default=text("'[]'::jsonb"))
    reference_asset_id: Mapped[UUID | None] = mapped_column(ForeignKey("media_assets.id", ondelete="SET NULL"))
    attributes: Mapped[dict] = json_object()


class ChapterCharacter(Base):
    __tablename__ = "chapter_characters"
    chapter_id: Mapped[UUID] = mapped_column(ForeignKey("chapters.id", ondelete="CASCADE"), primary_key=True)
    character_id: Mapped[UUID] = mapped_column(ForeignKey("characters.id", ondelete="CASCADE"), primary_key=True)
    overrides: Mapped[dict] = json_object()
    character: Mapped[Character] = relationship()


class Prop(Identity, Timestamps, Base):
    __tablename__ = "props"
    __table_args__ = (
        Index("uq_props_book_tag", "book_id", "tag", unique=True, postgresql_where=text("chapter_id IS NULL")),
        Index("uq_props_chapter_tag", "chapter_id", "tag", unique=True, postgresql_where=text("chapter_id IS NOT NULL")),
    )
    book_id: Mapped[UUID] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"), index=True)
    chapter_id: Mapped[UUID | None] = mapped_column(ForeignKey("chapters.id", ondelete="CASCADE"), index=True)
    tag: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    reference_asset_id: Mapped[UUID | None] = mapped_column(ForeignKey("media_assets.id", ondelete="SET NULL"))
    attributes: Mapped[dict] = json_object()


class Phrase(TimelineItem):
    __tablename__ = "phrases"
    id: Mapped[UUID] = mapped_column(ForeignKey("timeline_items.id", ondelete="CASCADE"), primary_key=True)
    text: Mapped[str] = mapped_column(Text, default="", server_default="")
    delta: Mapped[dict | list | None] = mapped_column(JSONB)
    latex: Mapped[str | None] = mapped_column(Text)
    speaker_id: Mapped[UUID | None] = mapped_column(ForeignKey("characters.id", ondelete="SET NULL"), index=True)
    speaker_source: Mapped[str | None] = mapped_column(Text)
    pronunciation: Mapped[str | None] = mapped_column(Text)
    audio_asset_id: Mapped[UUID | None] = mapped_column(ForeignKey("media_assets.id", ondelete="SET NULL"))
    speaker: Mapped[Character | None] = relationship()
    audio_asset: Mapped[MediaAsset | None] = relationship()
    __mapper_args__ = {"polymorphic_identity": TimelineKind.PHRASE, "polymorphic_load": "selectin"}


class Image(TimelineItem):
    __tablename__ = "images"
    id: Mapped[UUID] = mapped_column(ForeignKey("timeline_items.id", ondelete="CASCADE"), primary_key=True)
    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    prompt: Mapped[str | None] = mapped_column(Text)
    animation_prompt: Mapped[str | None] = mapped_column(Text)
    image_workflow: Mapped[str | None] = mapped_column(Text)
    animation_workflow: Mapped[str | None] = mapped_column(Text)
    animation_quality: Mapped[str | None] = mapped_column(Text)
    seed: Mapped[int | None] = mapped_column(BigInteger)
    image_asset_id: Mapped[UUID | None] = mapped_column(ForeignKey("media_assets.id", ondelete="SET NULL"))
    video_asset_id: Mapped[UUID | None] = mapped_column(ForeignKey("media_assets.id", ondelete="SET NULL"))
    image_asset: Mapped[MediaAsset | None] = relationship(foreign_keys=[image_asset_id])
    video_asset: Mapped[MediaAsset | None] = relationship(foreign_keys=[video_asset_id])
    characters: Mapped[list[ImageCharacter]] = relationship(cascade="all, delete-orphan", passive_deletes=True)
    props: Mapped[list[ImageProp]] = relationship(cascade="all, delete-orphan", passive_deletes=True)
    __mapper_args__ = {"polymorphic_identity": TimelineKind.IMAGE, "polymorphic_load": "selectin"}


class Scene(TimelineItem):
    __tablename__ = "scenes"
    id: Mapped[UUID] = mapped_column(ForeignKey("timeline_items.id", ondelete="CASCADE"), primary_key=True)
    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    time_of_day: Mapped[str | None] = mapped_column(Text)
    lighting: Mapped[dict] = json_object()
    reference_asset_id: Mapped[UUID | None] = mapped_column(ForeignKey("media_assets.id", ondelete="SET NULL"))
    __mapper_args__ = {"polymorphic_identity": TimelineKind.SCENE, "polymorphic_load": "selectin"}


class Silence(TimelineItem):
    __tablename__ = "silences"
    id: Mapped[UUID] = mapped_column(ForeignKey("timeline_items.id", ondelete="CASCADE"), primary_key=True)
    __mapper_args__ = {"polymorphic_identity": TimelineKind.SILENCE, "polymorphic_load": "selectin"}


class Sound(TimelineItem):
    __tablename__ = "sounds"
    id: Mapped[UUID] = mapped_column(ForeignKey("timeline_items.id", ondelete="CASCADE"), primary_key=True)
    name: Mapped[str] = mapped_column(Text, default="", server_default="")
    audio_asset_id: Mapped[UUID | None] = mapped_column(ForeignKey("media_assets.id", ondelete="SET NULL"))
    __mapper_args__ = {"polymorphic_identity": TimelineKind.SOUND, "polymorphic_load": "selectin"}


class ImageCharacter(Base):
    __tablename__ = "image_characters"
    image_id: Mapped[UUID] = mapped_column(ForeignKey("images.id", ondelete="CASCADE"), primary_key=True)
    character_id: Mapped[UUID] = mapped_column(ForeignKey("characters.id", ondelete="CASCADE"), primary_key=True)
    pose: Mapped[str | None] = mapped_column(Text)
    location: Mapped[str | None] = mapped_column(Text)
    action: Mapped[str | None] = mapped_column(Text)
    motivation: Mapped[str | None] = mapped_column(Text)
    description_override: Mapped[str | None] = mapped_column(Text)
    character: Mapped[Character] = relationship()


class ImageProp(Base):
    __tablename__ = "image_props"
    image_id: Mapped[UUID] = mapped_column(ForeignKey("images.id", ondelete="CASCADE"), primary_key=True)
    prop_id: Mapped[UUID] = mapped_column(ForeignKey("props.id", ondelete="CASCADE"), primary_key=True)
    prop: Mapped[Prop] = relationship()


class Task(Identity, Timestamps, Base):
    __tablename__ = "tasks"
    __table_args__ = (
        CheckConstraint("id != parent_task_id", name="parent_not_self"),
        CheckConstraint("progress >= 0 AND progress <= 100", name="progress_range"),
        Index("ix_tasks_dispatch", "provider", "state", "priority", "created_at"),
    )
    chapter_id: Mapped[UUID | None] = mapped_column(ForeignKey("chapters.id", ondelete="CASCADE"), index=True)
    item_id: Mapped[UUID | None] = mapped_column(ForeignKey("timeline_items.id", ondelete="SET NULL"), index=True)
    input_asset_id: Mapped[UUID | None] = mapped_column(ForeignKey("media_assets.id", ondelete="SET NULL"))
    output_asset_id: Mapped[UUID | None] = mapped_column(ForeignKey("media_assets.id", ondelete="SET NULL"))
    parent_task_id: Mapped[UUID | None] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    action: Mapped[str] = mapped_column(Text)
    label: Mapped[str | None] = mapped_column(Text)
    message: Mapped[str | None] = mapped_column(Text)
    progress: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    worker_id: Mapped[str | None] = mapped_column(Text)
    provider: Mapped[Provider] = mapped_column(enum_type(Provider, "task_provider"))
    state: Mapped[TaskState] = mapped_column(enum_type(TaskState, "task_state"), default=TaskState.QUEUED, server_default="queued")
    priority: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    arguments: Mapped[dict] = json_object()
    result: Mapped[dict | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    item: Mapped[TimelineItem | None] = relationship()
    input_asset: Mapped[MediaAsset | None] = relationship(foreign_keys=[input_asset_id])
    output_asset: Mapped[MediaAsset | None] = relationship(foreign_keys=[output_asset_id])
    dependencies: Mapped[list[TaskDependency]] = relationship(foreign_keys="TaskDependency.task_id", cascade="all, delete-orphan", passive_deletes=True)


class TaskDependency(Base):
    __tablename__ = "task_dependencies"
    __table_args__ = (CheckConstraint("task_id != depends_on_task_id", name="dependency_not_self"),)
    task_id: Mapped[UUID] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True)
    depends_on_task_id: Mapped[UUID] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True, index=True)
    depends_on: Mapped[Task] = relationship(foreign_keys=[depends_on_task_id])
