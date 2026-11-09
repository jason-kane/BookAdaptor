"""Rename an author identity and its assets without changing record IDs."""
from contextlib import contextmanager
import ctypes
import errno
import fcntl
import json
import html
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import quote

from .profile import read_json, safe_name

RESERVED = {'home', 'active', 'lost+found', 'global_characters', 'Theater', 'loras',
            'pages', 'sounds', 'stockpile', 'style_exploration', 'author', 'textoviva', 'favicon.ico'}


class RenameError(ValueError):
    def __init__(self, message, status=409):
        super().__init__(message)
        self.status = status


def resolve_name(root, name):
    root = Path(root)
    if (root / name).exists():
        return name
    aliases = read_json(root / '.author-redirects.json')
    seen = set()
    while name in aliases and name not in seen:
        seen.add(name)
        name = aliases[name]
    return name


def register_redirects(app):
    from flask import g, redirect, request
    import const
    @app.url_value_preprocessor
    def canonical_author(endpoint, values):
        if not values or 'author' not in values or not request.path.startswith('/library/'):
            return
        old = str(values['author'])
        new = resolve_name(const.LIBRARY_DIR, old)
        if new != old:
            values['author'] = new
            tail = request.path.split('/', 3)
            g.renamed_author_url = '/library/' + quote(new, safe='') + ('/' + quote(tail[3], safe='/') if len(tail) > 3 else '')
            if request.query_string:
                g.renamed_author_url += '?' + request.query_string.decode('latin1')
    @app.before_request
    def redirect_author():
        if getattr(g, 'renamed_author_url', None):
            return redirect(g.renamed_author_url, code=308)


def rewrite(value, old, new, root):
    if isinstance(value, dict):
        return {key: new if key in ('author', 'AUTHOR', 'pen_name') and item in (old, old.replace('_', ' '))
                and isinstance(item, str) else rewrite(item, old, new, root) for key, item in value.items()}
    if isinstance(value, list):
        result = [rewrite(item, old, new, root) for item in value]
        if len(value) == 4 and value[0] == old and isinstance(value[1], str) and (isinstance(value[2], int) or str(value[2]).isdigit()):
            result[0] = new
        return result
    if isinstance(value, str):
        if value.startswith('['):
            try:
                encoded = json.loads(value)
                updated = rewrite(encoded, old, new, root)
                if updated != encoded:
                    return json.dumps(updated, ensure_ascii=False)
            except ValueError:
                pass
        prefixes = [(str(Path(root) / old) + '/', str(Path(root) / new) + '/'),
                    ('/library/' + quote(old, safe='') + '/', '/library/' + quote(new, safe='') + '/'),
                    ('/library/' + old + '/', '/library/' + new + '/')]
        for before, after in prefixes:
            value = value.replace(before, after)
        if value.startswith(old + '/'):
            value = new + value[len(old):]
        if value == str(Path(root) / old):
            value = str(Path(root) / new)
        return value
    return value


def rewrite_xml(text, old, new, root):
    def tag(match):
        def attribute(found):
            value = html.unescape(found['value'])
            key = found['key'].split('=')[0].strip()
            updated = new if key.lower() == 'author' and value in (old, old.replace('_', ' ')) else rewrite(value, old, new, root)
            if updated == value:
                return found.group(0)
            return found['key'] + found['quote'] + html.escape(updated, quote=True) + found['quote']
        return re.sub(r'(?P<key>\b[\w:-]+\s*=\s*)(?P<quote>["\x27])(?P<value>.*?)(?P=quote)', attribute, match.group(0))
    return re.sub(r'<[A-Za-z_:][^>]*>', tag, text)


def check_jobs(old, new, root):
    import redis
    client = redis.Redis(host=os.environ.get('REDIS_HOST', 'redis'), decode_responses=True,
                         socket_connect_timeout=2, socket_timeout=2)
    try:
        queues = ('video_render_jobs', 'styler_image_tag_jobs', 'styler_image_tag_priority_jobs',
                  'styler_idle_jobs', 'chapter_render_jobs', 'cpu_jobs', 'chapter_render_jobs:processing')
        payloads = []
        for queue in queues:
            payloads.extend(client.lrange(queue, 0, -1))
        for key in client.scan_iter('cpu_jobs:processing*'):
            payloads.extend(client.lrange(key, 0, -1))
        for key in ('styler:worker-running', 'chapter_render:running'):
            current = client.get(key)
            if current:
                payloads.append(current)
        payloads.extend(client.hvals('cpu_jobs:running'))
        for raw in payloads:
            try:
                task = json.loads(raw)
            except (TypeError, ValueError):
                continue
            if rewrite(task, old, new, root) != task:
                raise RenameError('This author has queued or running work. Wait for it to finish before renaming.')
    except redis.RedisError as error:
        raise RenameError('Cannot check active jobs. Try renaming again when the task service is available.', 503) from error
    finally:
        client.close()


@contextmanager
def database_change(old, new, root):
    from artifact_editor.chapter.persistence import uses_postgres
    if not uses_postgres():
        yield
        return
    from sqlalchemy import select
    from sqlalchemy.types import JSON
    from artifact_editor.db import models as m
    from artifact_editor.db import session_scope
    with session_scope() as session:
        author = session.scalar(select(m.Author).where(m.Author.name == old).with_for_update())
        if session.scalar(select(m.Author.id).where(m.Author.name == new)):
            raise RenameError('An author with that name already exists in the database.')
        book_ids = list(session.scalars(select(m.Book.id).where(m.Book.author_id == author.id))) if author else []
        chapter_ids = list(session.scalars(select(m.Chapter.id).where(m.Chapter.book_id.in_(book_ids)))) if book_ids else []
        active = {m.TaskState.QUEUED, m.TaskState.BLOCKED, m.TaskState.RUNNING, m.TaskState.CANCELLING}
        for task in session.scalars(select(m.Task)):
            arguments = rewrite(task.arguments, old, new, root)
            result = rewrite(task.result, old, new, root)
            related = task.chapter_id in chapter_ids or arguments != task.arguments or result != task.result
            if related and task.state in active:
                raise RenameError('This author has queued or running work. Wait for it to finish before renaming.')
            if related:
                task.arguments, task.result = arguments, result
        if author:
            author.name = new
            author.settings = rewrite(author.settings, old, new, root)
        for mapper in m.Base.registry.mappers:
            model = mapper.class_
            if model in (m.Author, m.Task):
                continue
            if hasattr(model, 'book_id') and book_ids:
                query = select(model).where(model.book_id.in_(book_ids))
            elif hasattr(model, 'chapter_id') and chapter_ids:
                query = select(model).where(model.chapter_id.in_(chapter_ids))
            elif model is m.Book and book_ids:
                query = select(model).where(model.id.in_(book_ids))
            else:
                continue
            for row in session.scalars(query):
                for column in mapper.columns:
                    if isinstance(column.type, JSON):
                        value = getattr(row, column.key)
                        updated = rewrite(value, old, new, root)
                        if updated != value:
                            setattr(row, column.key, updated)
                if model is m.MediaAsset:
                    row.path = rewrite(row.path, old, new, root)
        session.flush()
        yield


def atomic_bytes(path, data, mode=0o644):
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as output:
        temporary = Path(output.name)
        output.write(data)
        output.flush()
        os.fsync(output.fileno())
    try:
        temporary.chmod(mode)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def move_no_replace(source, destination):
    # Linux renameat2 refuses an existing destination, even an empty directory.
    function = getattr(ctypes.CDLL(None, use_errno=True), 'renameat2', None)
    if function is None:
        raise RenameError('This filesystem does not support safe author-directory renaming.', 503)
    if function(-100, os.fsencode(source), -100, os.fsencode(destination), 1):
        code = ctypes.get_errno()
        if code in (errno.EEXIST, errno.ENOTEMPTY):
            raise RenameError('An author directory with that name already exists.')
        raise OSError(code, os.strerror(code))


def rename_author(root, old, new):
    root = Path(root)
    try:
        new = safe_name(new)
    except (ValueError, AttributeError) as error:
        raise RenameError(str(error), 400) from error
    if new in RESERVED or len(os.fsencode(new)) > 255 or any(ord(char) < 32 for char in new):
        raise RenameError('Choose a non-reserved author name of at most 255 bytes.', 400)
    source, target = root / old, root / new
    with (root / '.author-rename.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not source.is_dir() or source.is_symlink():
            raise RenameError('The author directory no longer exists. Reload the library.', 404)
        from .contributors import profile_lock
        with profile_lock(source):
            if new == old:
                return new
            if os.path.lexists(target) or resolve_name(root, new) not in (new, old):
                raise RenameError('That author name is already in use.')
            check_jobs(old, new, root)
            edits = []
            for path in source.rglob('*'):
                if path.is_symlink() or not path.is_file() or path.suffix not in ('.json', '.xml'):
                    continue
                original = path.read_bytes()
                try:
                    if path.suffix == '.json':
                        value = json.loads(original)
                        if ('lora' in str(path).lower() or 'training' in str(path).lower()) and isinstance(value, dict) and value.get('status') in ('queued', 'waiting_for_gpu', 'training'):
                            raise RenameError('This author has an active training job. Wait for it to finish before renaming.')
                        updated = rewrite(value, old, new, root)
                        if path.name == 'author.json' and path.parent == source:
                            updated['pen_name'] = new
                        data = json.dumps(updated, indent=2, ensure_ascii=False).encode() if updated != value else original
                    else:
                        text = original.decode('utf-8')
                        data = rewrite_xml(text, old, new, root).encode()
                except (ValueError, UnicodeError) as error:
                    if isinstance(error, RenameError):
                        raise
                    raise RenameError(f'Cannot read {path.relative_to(source)}. Repair this metadata file before renaming.', 400) from error
                if data != original:
                    edits.append((path.relative_to(source), original, data, path.stat().st_mode & 0o777))
            if not (source / 'author.json').exists():
                edits.append((Path('author.json'), None, json.dumps({'pen_name': new}).encode(), 0o644))
            alias_path = root / '.author-redirects.json'
            previous_aliases = alias_path.read_bytes() if alias_path.exists() else None
            aliases = json.loads(previous_aliases) if previous_aliases else {}
            aliases = {key: new if value == old else value for key, value in aliases.items() if key != new}
            aliases[old] = new
            moved = False
            try:
                with database_change(old, new, root):
                    move_no_replace(source, target)
                    moved = True
                    for relative, original, data, mode in edits:
                        atomic_bytes(target / relative, data, mode)
                    atomic_bytes(alias_path, json.dumps(aliases, ensure_ascii=False, indent=2).encode())
            except Exception:
                if moved:
                    for relative, original, data, mode in reversed(edits):
                        if original is None:
                            (target / relative).unlink(missing_ok=True)
                        else:
                            atomic_bytes(target / relative, original, mode)
                    move_no_replace(target, source)
                    if previous_aliases is None:
                        alias_path.unlink(missing_ok=True)
                    else:
                        atomic_bytes(alias_path, previous_aliases)
                raise
    return new
