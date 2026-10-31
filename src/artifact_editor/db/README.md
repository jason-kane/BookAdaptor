# PostgreSQL ORM

SQLAlchemy 2.0 models live in `artifact_editor.db.models`. The package has no
Flask, Redis, GPU, or XML imports and opens connections only when requested.
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
  and `legacy_id` retain XML/asset references for an eventual importer. Deferred
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

This establishes the schema and ORM API. The existing `Chapter` persistence
adapter still owns XML until its importer and database-backed implementation
are connected. Existing Redis jobs are not copied into these tables by schema
migration.

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
