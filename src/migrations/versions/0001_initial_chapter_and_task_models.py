"""Initial chapter and task models

Revision ID: 0001
Revises: none
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '0001'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('authors',
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('settings', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_authors')),
    sa.UniqueConstraint('name', name=op.f('uq_authors_name'))
    )
    op.create_table('books',
    sa.Column('author_id', sa.Uuid(), nullable=False),
    sa.Column('title', sa.Text(), nullable=False),
    sa.Column('settings', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['author_id'], ['authors.id'], name=op.f('fk_books_author_id_authors'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_books')),
    sa.UniqueConstraint('author_id', 'title', name=op.f('uq_books_author_id_title'))
    )
    op.create_index(op.f('ix_books_author_id'), 'books', ['author_id'], unique=False)
    op.create_table('chapters',
    sa.Column('book_id', sa.Uuid(), nullable=False),
    sa.Column('number', sa.Integer(), nullable=False),
    sa.Column('language', sa.Text(), server_default='english', nullable=False),
    sa.Column('title', sa.Text(), server_default='', nullable=False),
    sa.Column('mood', sa.Text(), server_default='', nullable=False),
    sa.Column('theme', sa.Text(), server_default='', nullable=False),
    sa.Column('source_delta', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('settings', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('attributes', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('number >= 0', name=op.f('ck_chapters_number_nonnegative')),
    sa.ForeignKeyConstraint(['book_id'], ['books.id'], name=op.f('fk_chapters_book_id_books'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_chapters')),
    sa.UniqueConstraint('book_id', 'number', 'language', name=op.f('uq_chapters_book_id_number_language'))
    )
    op.create_index(op.f('ix_chapters_book_id'), 'chapters', ['book_id'], unique=False)
    op.create_table('media_assets',
    sa.Column('book_id', sa.Uuid(), nullable=False),
    sa.Column('chapter_id', sa.Uuid(), nullable=True),
    sa.Column('source_asset_id', sa.Uuid(), nullable=True),
    sa.Column('kind', sa.Enum('image', 'audio', 'video', name='media_kind'), nullable=False),
    sa.Column('path', sa.Text(), nullable=False),
    sa.Column('revision', sa.Integer(), server_default='1', nullable=False),
    sa.Column('sha256', sa.Text(), nullable=True),
    sa.Column('width', sa.Integer(), nullable=True),
    sa.Column('height', sa.Integer(), nullable=True),
    sa.Column('duration_seconds', sa.Numeric(precision=14, scale=6), nullable=True),
    sa.Column('details', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('duration_seconds >= 0', name=op.f('ck_media_assets_duration_nonnegative')),
    sa.CheckConstraint('revision > 0', name=op.f('ck_media_assets_revision_positive')),
    sa.CheckConstraint('width > 0 AND height > 0', name=op.f('ck_media_assets_dimensions_positive')),
    sa.ForeignKeyConstraint(['book_id'], ['books.id'], name=op.f('fk_media_assets_book_id_books'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['chapter_id'], ['chapters.id'], name=op.f('fk_media_assets_chapter_id_chapters'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['source_asset_id'], ['media_assets.id'], name=op.f('fk_media_assets_source_asset_id_media_assets'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_media_assets')),
    sa.UniqueConstraint('book_id', 'path', 'revision', name=op.f('uq_media_assets_book_id_path_revision'))
    )
    op.create_index(op.f('ix_media_assets_book_id'), 'media_assets', ['book_id'], unique=False)
    op.create_index(op.f('ix_media_assets_chapter_id'), 'media_assets', ['chapter_id'], unique=False)
    op.create_index(op.f('ix_media_assets_source_asset_id'), 'media_assets', ['source_asset_id'], unique=False)
    op.create_table('sections',
    sa.Column('chapter_id', sa.Uuid(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('typesetting_method', sa.Text(), server_default='plain', nullable=False),
    sa.Column('delta', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('attributes', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('position >= 0', name=op.f('ck_sections_position_nonnegative')),
    sa.ForeignKeyConstraint(['chapter_id'], ['chapters.id'], name=op.f('fk_sections_chapter_id_chapters'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_sections')),
    sa.UniqueConstraint('chapter_id', 'position', deferrable=True, initially='DEFERRED', name=op.f('uq_sections_chapter_id_position'))
    )
    op.create_index(op.f('ix_sections_chapter_id'), 'sections', ['chapter_id'], unique=False)
    op.create_table('characters',
    sa.Column('book_id', sa.Uuid(), nullable=False),
    sa.Column('chapter_id', sa.Uuid(), nullable=True),
    sa.Column('tag', sa.Text(), nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('description', sa.Text(), server_default='', nullable=False),
    sa.Column('aliases', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
    sa.Column('reference_asset_id', sa.Uuid(), nullable=True),
    sa.Column('attributes', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['book_id'], ['books.id'], name=op.f('fk_characters_book_id_books'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['chapter_id'], ['chapters.id'], name=op.f('fk_characters_chapter_id_chapters'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['reference_asset_id'], ['media_assets.id'], name=op.f('fk_characters_reference_asset_id_media_assets'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_characters'))
    )
    op.create_index(op.f('ix_characters_book_id'), 'characters', ['book_id'], unique=False)
    op.create_index(op.f('ix_characters_chapter_id'), 'characters', ['chapter_id'], unique=False)
    op.create_index('uq_characters_book_tag', 'characters', ['book_id', 'tag'], unique=True, postgresql_where=sa.text('chapter_id IS NULL'))
    op.create_index('uq_characters_chapter_tag', 'characters', ['chapter_id', 'tag'], unique=True, postgresql_where=sa.text('chapter_id IS NOT NULL'))
    op.create_table('paragraphs',
    sa.Column('chapter_id', sa.Uuid(), nullable=False),
    sa.Column('section_id', sa.Uuid(), nullable=True),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('legacy_index', sa.Integer(), nullable=True),
    sa.Column('typesetting_method', sa.Text(), nullable=True),
    sa.Column('attributes', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('position >= 0', name=op.f('ck_paragraphs_position_nonnegative')),
    sa.ForeignKeyConstraint(['chapter_id'], ['chapters.id'], name=op.f('fk_paragraphs_chapter_id_chapters'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['section_id'], ['sections.id'], name=op.f('fk_paragraphs_section_id_sections'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_paragraphs')),
    sa.UniqueConstraint('chapter_id', 'legacy_index', name=op.f('uq_paragraphs_chapter_id_legacy_index')),
    sa.UniqueConstraint('chapter_id', 'position', deferrable=True, initially='DEFERRED', name=op.f('uq_paragraphs_chapter_id_position')),
    sa.UniqueConstraint('id', 'chapter_id', name=op.f('uq_paragraphs_id_chapter_id'))
    )
    op.create_index(op.f('ix_paragraphs_chapter_id'), 'paragraphs', ['chapter_id'], unique=False)
    op.create_index(op.f('ix_paragraphs_section_id'), 'paragraphs', ['section_id'], unique=False)
    op.create_table('props',
    sa.Column('book_id', sa.Uuid(), nullable=False),
    sa.Column('chapter_id', sa.Uuid(), nullable=True),
    sa.Column('tag', sa.Text(), nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('description', sa.Text(), server_default='', nullable=False),
    sa.Column('reference_asset_id', sa.Uuid(), nullable=True),
    sa.Column('attributes', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['book_id'], ['books.id'], name=op.f('fk_props_book_id_books'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['chapter_id'], ['chapters.id'], name=op.f('fk_props_chapter_id_chapters'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['reference_asset_id'], ['media_assets.id'], name=op.f('fk_props_reference_asset_id_media_assets'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_props'))
    )
    op.create_index(op.f('ix_props_book_id'), 'props', ['book_id'], unique=False)
    op.create_index(op.f('ix_props_chapter_id'), 'props', ['chapter_id'], unique=False)
    op.create_index('uq_props_book_tag', 'props', ['book_id', 'tag'], unique=True, postgresql_where=sa.text('chapter_id IS NULL'))
    op.create_index('uq_props_chapter_tag', 'props', ['chapter_id', 'tag'], unique=True, postgresql_where=sa.text('chapter_id IS NOT NULL'))
    op.create_table('chapter_characters',
    sa.Column('chapter_id', sa.Uuid(), nullable=False),
    sa.Column('character_id', sa.Uuid(), nullable=False),
    sa.Column('overrides', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.ForeignKeyConstraint(['chapter_id'], ['chapters.id'], name=op.f('fk_chapter_characters_chapter_id_chapters'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['character_id'], ['characters.id'], name=op.f('fk_chapter_characters_character_id_characters'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('chapter_id', 'character_id', name=op.f('pk_chapter_characters'))
    )
    op.create_table('timeline_items',
    sa.Column('chapter_id', sa.Uuid(), nullable=False),
    sa.Column('paragraph_id', sa.Uuid(), nullable=False),
    sa.Column('kind', sa.Enum('phrase', 'image', 'scene', 'silence', 'sound', name='timeline_kind'), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('legacy_index', sa.Integer(), nullable=True),
    sa.Column('legacy_id', sa.Text(), nullable=True),
    sa.Column('duration_seconds', sa.Numeric(precision=14, scale=6), nullable=True),
    sa.Column('frame_count', sa.Integer(), nullable=True),
    sa.Column('attributes', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('duration_seconds >= 0', name=op.f('ck_timeline_items_duration_nonnegative')),
    sa.CheckConstraint('frame_count >= 0', name=op.f('ck_timeline_items_frames_nonnegative')),
    sa.CheckConstraint('position >= 0', name=op.f('ck_timeline_items_position_nonnegative')),
    sa.ForeignKeyConstraint(['paragraph_id', 'chapter_id'], ['paragraphs.id', 'paragraphs.chapter_id'], name=op.f('fk_timeline_items_paragraph_id_paragraphs'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_timeline_items')),
    sa.UniqueConstraint('chapter_id', 'kind', 'legacy_index', name=op.f('uq_timeline_items_chapter_id_kind_legacy_index')),
    sa.UniqueConstraint('paragraph_id', 'position', deferrable=True, initially='DEFERRED', name=op.f('uq_timeline_items_paragraph_id_position'))
    )
    op.create_index(op.f('ix_timeline_items_chapter_id'), 'timeline_items', ['chapter_id'], unique=False)
    op.create_index('ix_timeline_items_chapter_kind', 'timeline_items', ['chapter_id', 'kind'], unique=False)
    op.create_index(op.f('ix_timeline_items_paragraph_id'), 'timeline_items', ['paragraph_id'], unique=False)
    op.create_table('images',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('description', sa.Text(), server_default='', nullable=False),
    sa.Column('prompt', sa.Text(), nullable=True),
    sa.Column('animation_prompt', sa.Text(), nullable=True),
    sa.Column('image_workflow', sa.Text(), nullable=True),
    sa.Column('animation_workflow', sa.Text(), nullable=True),
    sa.Column('animation_quality', sa.Text(), nullable=True),
    sa.Column('seed', sa.BigInteger(), nullable=True),
    sa.Column('image_asset_id', sa.Uuid(), nullable=True),
    sa.Column('video_asset_id', sa.Uuid(), nullable=True),
    sa.ForeignKeyConstraint(['id'], ['timeline_items.id'], name=op.f('fk_images_id_timeline_items'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['image_asset_id'], ['media_assets.id'], name=op.f('fk_images_image_asset_id_media_assets'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['video_asset_id'], ['media_assets.id'], name=op.f('fk_images_video_asset_id_media_assets'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_images'))
    )
    op.create_table('phrases',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('text', sa.Text(), server_default='', nullable=False),
    sa.Column('delta', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('latex', sa.Text(), nullable=True),
    sa.Column('speaker_id', sa.Uuid(), nullable=True),
    sa.Column('speaker_source', sa.Text(), nullable=True),
    sa.Column('pronunciation', sa.Text(), nullable=True),
    sa.Column('audio_asset_id', sa.Uuid(), nullable=True),
    sa.ForeignKeyConstraint(['audio_asset_id'], ['media_assets.id'], name=op.f('fk_phrases_audio_asset_id_media_assets'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['id'], ['timeline_items.id'], name=op.f('fk_phrases_id_timeline_items'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['speaker_id'], ['characters.id'], name=op.f('fk_phrases_speaker_id_characters'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_phrases'))
    )
    op.create_index(op.f('ix_phrases_speaker_id'), 'phrases', ['speaker_id'], unique=False)
    op.create_table('scenes',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('description', sa.Text(), server_default='', nullable=False),
    sa.Column('time_of_day', sa.Text(), nullable=True),
    sa.Column('lighting', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('reference_asset_id', sa.Uuid(), nullable=True),
    sa.ForeignKeyConstraint(['id'], ['timeline_items.id'], name=op.f('fk_scenes_id_timeline_items'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['reference_asset_id'], ['media_assets.id'], name=op.f('fk_scenes_reference_asset_id_media_assets'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_scenes'))
    )
    op.create_table('silences',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['id'], ['timeline_items.id'], name=op.f('fk_silences_id_timeline_items'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_silences'))
    )
    op.create_table('sounds',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('name', sa.Text(), server_default='', nullable=False),
    sa.Column('audio_asset_id', sa.Uuid(), nullable=True),
    sa.ForeignKeyConstraint(['audio_asset_id'], ['media_assets.id'], name=op.f('fk_sounds_audio_asset_id_media_assets'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['id'], ['timeline_items.id'], name=op.f('fk_sounds_id_timeline_items'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_sounds'))
    )
    op.create_table('tasks',
    sa.Column('chapter_id', sa.Uuid(), nullable=True),
    sa.Column('item_id', sa.Uuid(), nullable=True),
    sa.Column('input_asset_id', sa.Uuid(), nullable=True),
    sa.Column('output_asset_id', sa.Uuid(), nullable=True),
    sa.Column('parent_task_id', sa.Uuid(), nullable=True),
    sa.Column('action', sa.Text(), nullable=False),
    sa.Column('label', sa.Text(), nullable=True),
    sa.Column('message', sa.Text(), nullable=True),
    sa.Column('progress', sa.Integer(), server_default='0', nullable=False),
    sa.Column('worker_id', sa.Text(), nullable=True),
    sa.Column('provider', sa.Enum('cpu', 'comfyui', 'llama', 'none', name='task_provider'), nullable=False),
    sa.Column('state', sa.Enum('queued', 'blocked', 'running', 'complete', 'failed', 'cancelled', 'cancelling', name='task_state'), server_default='queued', nullable=False),
    sa.Column('priority', sa.Integer(), server_default='0', nullable=False),
    sa.Column('arguments', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('result', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('id != parent_task_id', name=op.f('ck_tasks_parent_not_self')),
    sa.CheckConstraint('progress >= 0 AND progress <= 100', name=op.f('ck_tasks_progress_range')),
    sa.ForeignKeyConstraint(['chapter_id'], ['chapters.id'], name=op.f('fk_tasks_chapter_id_chapters'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['input_asset_id'], ['media_assets.id'], name=op.f('fk_tasks_input_asset_id_media_assets'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['item_id'], ['timeline_items.id'], name=op.f('fk_tasks_item_id_timeline_items'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['output_asset_id'], ['media_assets.id'], name=op.f('fk_tasks_output_asset_id_media_assets'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['parent_task_id'], ['tasks.id'], name=op.f('fk_tasks_parent_task_id_tasks'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_tasks'))
    )
    op.create_index(op.f('ix_tasks_chapter_id'), 'tasks', ['chapter_id'], unique=False)
    op.create_index('ix_tasks_dispatch', 'tasks', ['provider', 'state', 'priority', 'created_at'], unique=False)
    op.create_index(op.f('ix_tasks_item_id'), 'tasks', ['item_id'], unique=False)
    op.create_index(op.f('ix_tasks_parent_task_id'), 'tasks', ['parent_task_id'], unique=False)
    op.create_table('image_characters',
    sa.Column('image_id', sa.Uuid(), nullable=False),
    sa.Column('character_id', sa.Uuid(), nullable=False),
    sa.Column('pose', sa.Text(), nullable=True),
    sa.Column('location', sa.Text(), nullable=True),
    sa.Column('action', sa.Text(), nullable=True),
    sa.Column('motivation', sa.Text(), nullable=True),
    sa.Column('description_override', sa.Text(), nullable=True),
    sa.ForeignKeyConstraint(['character_id'], ['characters.id'], name=op.f('fk_image_characters_character_id_characters'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['image_id'], ['images.id'], name=op.f('fk_image_characters_image_id_images'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('image_id', 'character_id', name=op.f('pk_image_characters'))
    )
    op.create_table('image_props',
    sa.Column('image_id', sa.Uuid(), nullable=False),
    sa.Column('prop_id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['image_id'], ['images.id'], name=op.f('fk_image_props_image_id_images'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['prop_id'], ['props.id'], name=op.f('fk_image_props_prop_id_props'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('image_id', 'prop_id', name=op.f('pk_image_props'))
    )
    op.create_table('task_dependencies',
    sa.Column('task_id', sa.Uuid(), nullable=False),
    sa.Column('depends_on_task_id', sa.Uuid(), nullable=False),
    sa.CheckConstraint('task_id != depends_on_task_id', name=op.f('ck_task_dependencies_dependency_not_self')),
    sa.ForeignKeyConstraint(['depends_on_task_id'], ['tasks.id'], name=op.f('fk_task_dependencies_depends_on_task_id_tasks'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['task_id'], ['tasks.id'], name=op.f('fk_task_dependencies_task_id_tasks'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('task_id', 'depends_on_task_id', name=op.f('pk_task_dependencies'))
    )
    op.create_index(op.f('ix_task_dependencies_depends_on_task_id'), 'task_dependencies', ['depends_on_task_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_task_dependencies_depends_on_task_id'), table_name='task_dependencies')
    op.drop_table('task_dependencies')
    op.drop_table('image_props')
    op.drop_table('image_characters')
    op.drop_index(op.f('ix_tasks_parent_task_id'), table_name='tasks')
    op.drop_index(op.f('ix_tasks_item_id'), table_name='tasks')
    op.drop_index('ix_tasks_dispatch', table_name='tasks')
    op.drop_index(op.f('ix_tasks_chapter_id'), table_name='tasks')
    op.drop_table('tasks')
    op.drop_table('sounds')
    op.drop_table('silences')
    op.drop_table('scenes')
    op.drop_index(op.f('ix_phrases_speaker_id'), table_name='phrases')
    op.drop_table('phrases')
    op.drop_table('images')
    op.drop_index(op.f('ix_timeline_items_paragraph_id'), table_name='timeline_items')
    op.drop_index('ix_timeline_items_chapter_kind', table_name='timeline_items')
    op.drop_index(op.f('ix_timeline_items_chapter_id'), table_name='timeline_items')
    op.drop_table('timeline_items')
    op.drop_table('chapter_characters')
    op.drop_index('uq_props_chapter_tag', table_name='props', postgresql_where=sa.text('chapter_id IS NOT NULL'))
    op.drop_index('uq_props_book_tag', table_name='props', postgresql_where=sa.text('chapter_id IS NULL'))
    op.drop_index(op.f('ix_props_chapter_id'), table_name='props')
    op.drop_index(op.f('ix_props_book_id'), table_name='props')
    op.drop_table('props')
    op.drop_index(op.f('ix_paragraphs_section_id'), table_name='paragraphs')
    op.drop_index(op.f('ix_paragraphs_chapter_id'), table_name='paragraphs')
    op.drop_table('paragraphs')
    op.drop_index('uq_characters_chapter_tag', table_name='characters', postgresql_where=sa.text('chapter_id IS NOT NULL'))
    op.drop_index('uq_characters_book_tag', table_name='characters', postgresql_where=sa.text('chapter_id IS NULL'))
    op.drop_index(op.f('ix_characters_chapter_id'), table_name='characters')
    op.drop_index(op.f('ix_characters_book_id'), table_name='characters')
    op.drop_table('characters')
    op.drop_index(op.f('ix_sections_chapter_id'), table_name='sections')
    op.drop_table('sections')
    op.drop_index(op.f('ix_media_assets_source_asset_id'), table_name='media_assets')
    op.drop_index(op.f('ix_media_assets_chapter_id'), table_name='media_assets')
    op.drop_index(op.f('ix_media_assets_book_id'), table_name='media_assets')
    op.drop_table('media_assets')
    op.drop_index(op.f('ix_chapters_book_id'), table_name='chapters')
    op.drop_table('chapters')
    op.drop_index(op.f('ix_books_author_id'), table_name='books')
    op.drop_table('books')
    op.drop_table('authors')
    for name in ('task_state', 'task_provider', 'timeline_kind', 'media_kind'):
        postgresql.ENUM(name=name).drop(op.get_bind(), checkfirst=True)
