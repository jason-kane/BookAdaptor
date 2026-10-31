# PostgreSQL ORM

SQLAlchemy 2.0 models live in `artifact_editor.db.models`. The package has no
Flask, Redis, or GPU imports and opens connections only when requested. The
chapter adapter imports BeautifulSoup for compatibility with existing consumers.
Install `src/requirements-database.txt` for command-line development; the app
Dockerfile installs these dependencies in a separate layer after model tooling.

## Connection and migrations

`DATABASE_URL` takes precedence over the standard `PGHOST`, `PGPORT`, `PGUSER`,
`PGPASSWORD`, and `PGDATABASE` environment variables. The driver is psycopg 3;
plain `postgresql://` URLs are normalized to `postgresql+psycopg://`. Local
defaults match the PostgreSQL service: `localhost:5432`, database and username
`bookadaptor`, with the development password configured in Compose. Application
containers set `PGHOST=postgres`.

Start PostgreSQL through Home, then run:

```sh
docker compose exec -e PGHOST=postgres artifactserver python -m artifact_editor.db check
docker compose exec -e PGHOST=postgres artifactserver python -m artifact_editor.db upgrade
docker compose exec -e PGHOST=postgres artifactserver python -m artifact_editor.db current
```

Use Alembic for schema changes, rather than `Base.metadata.create_all()`:

```sh
cd src
alembic revision --autogenerate -m "Describe the change"
alembic upgrade head
alembic check
```

Review generated revisions before applying them. Autogenerate ignores reflected
tables outside this metadata, so unrelated tables made through pgAdmin are not
proposed for deletion. The initial revision has an explicit upgrade and downgrade,
including cleanup of its PostgreSQL enum types.

## Model boundaries

* `Author`, `Book`, `Chapter`, `Section`, and `Paragraph` hold the library and
  editable rich text. Chapters are unique by book, number, and language.
* `TimelineItem` provides one ordered sequence per paragraph. `Phrase`, `Image`,
  `Scene`, `Silence`, and `Sound` use joined table inheritance, so an ordered
  relationship returns the corresponding concrete objects. Composite foreign
  keys keep timeline items in their declared chapter.
* UUID primary keys are stable; `position` controls order, and `legacy_index`
  and `legacy_id` retain imported XML/asset references. Deferred
  position constraints allow swapping entries within one transaction.
* `MediaAsset` records versioned file paths, dimensions, duration, and provenance.
  Files remain in the media filesystem. Images and tasks reference the selected
  source asset explicitly.
* Characters and props can belong to a book or a chapter. Partial unique indexes
  distinguish those tag scopes. `ChapterCharacter` holds chapter-specific
  overrides; `ImageCharacter` and `ImageProp` describe appearances.
* `Task` records provider, state, progress, parent, image/timeline item, and input
  and output assets. `TaskDependency` expresses prerequisites independently of
  queue transport. Self-dependencies are rejected; a scheduler must validate
  longer dependency cycles when building a graph.
* JSONB attributes preserve uncommon legacy fields. Top-level dictionary/list
  changes are tracked; replace the containing value when changing nested JSON.

## Chapter storage and legacy import

Compose sets `CHAPTER_STORAGE=postgres` on the editor, WebSocket server, and
all three worker services. A process without this variable retains the old XML
backend for a coordinated rollout. PostgreSQL is a healthy startup dependency.
Apply migrations and drain existing XML jobs **before recreating these services
together**; do not mix XML writers with PostgreSQL writers for the same chapter.
A restart alone does not update container environment variables.

```sh
docker compose exec -e PGHOST=postgres artifactserver python -m artifact_editor.db upgrade
# After old chapter jobs have drained, recreate all chapter readers/writers:
docker compose up -d --no-build artifactserver websocket video_worker render_worker cpu_worker
```

`Chapter.load_xml()` now means load the compatibility view from the selected
backend. On its first PostgreSQL load, a chapter imports its language-specific
`chapter.xml` (or the older shared `book.xml`) within the same transaction.
The original files are left untouched. The importer runs the existing version
upgrades without their old filesystem cleanup/rename side effects. Import
provenance stores the source path, SHA-256 and timestamp. A saved empty timeline
stays empty, even when old XML is still present. Database failures propagate;
they never silently switch a PostgreSQL writer back to XML.

An explicit import is also available, after old writers have drained:

```sh
docker compose exec -e PGHOST=postgres artifactserver python -m artifact_editor.db import-chapter \
  --author "L. Frank Baum" --title "The Marvelous Land of Oz" --number 3 --language english
# Optional: --xml /path/to/old/chapter.xml
```

Repeated imports leave database content unchanged. Invalid XML/data rolls back
the transaction so it can be corrected and retried. Import creation is serialized
with transaction-scoped advisory locks; concurrent first loads import once.

Paragraphs, phrases, images, scenes, silence and sound are individual ORM rows.
Their compatibility layouts contain text, markup and UUID references to child
rows; they do not store a serialized chapter XML document. Existing tag-based
callers keep their interface. Typed fields (duration, frames, prompts, seed,
phrase text/delta/LaTeX, etc.) are populated, while the original attributes retain
less common metadata and exact legacy spellings. Duplicate historical paragraph
indexes remain in the attributes for existing media paths; additional duplicate
rows have null `legacy_index` and distinct UUIDs/positions.

Record saves merge changed fields against the caller's last loaded baseline.
Timeline saves reconcile UUIDs across moves and reindexing. Removed rows are
actually deleted; task item references become null through their foreign keys.
No XML is written by PostgreSQL saves. Notifications and Theater previews read
the selected backend, and GitHub publishing explicitly exports current XML.

This step converts the chapter timeline store. Chapter config, editable source
Delta/section JSON, character/prop definitions and generated media files retain
their existing filesystem APIs. Their corresponding ORM tables/relationships
are available for later conversions; this importer does not claim to migrate
those stores or existing Redis jobs.

## Transactions

Use one session per request or task; never share a Session between workers.
Engines are lazy and cached per process, with inherited pools discarded after a
fork. The context commits on success and rolls back on exceptions:

```python
from artifact_editor.db import session_scope
from artifact_editor.db.models import Author, Book, Chapter, Paragraph, Image

with session_scope() as session:
    author = Author(name="L. Frank Baum")
    book = Book(author=author, title="The Marvelous Land of Oz")
    chapter = Chapter(book=book, number=1, language="english")
    paragraph = Paragraph(chapter=chapter, position=0)
    image = Image(position=0, legacy_index=4, description="A forest path")
    paragraph.items.append(image)
    session.add(author)
```

Normal ORM updates write dirty columns, so workers editing different image
fields do not replace the rest of the chapter.

## Validation

`tests/test_database_models.py` runs integration checks in unique disposable
schemas. Set `BOOKADAPTOR_TEST_DATABASE_URL` to a PostgreSQL URL to enable them.
Tests cover mixed timelines, stable identities, scoping constraints, concurrent
field edits, JSON updates, rollback, deletion cascades, task prerequisites, and
an upgrade/downgrade/upgrade cycle. Application rows in `public` are untouched.

References: [SQLAlchemy PostgreSQL dialect](https://docs.sqlalchemy.org/en/20/dialects/postgresql.html)
and [Alembic migrations](https://alembic.sqlalchemy.org/en/latest/autogenerate.html).
