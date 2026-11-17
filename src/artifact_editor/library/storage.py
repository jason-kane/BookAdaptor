"""Active catalog, managed book links, and verified background transfers.

Active is authoritative. A real book directory may contain asset links into
Shelf; a whole-book link in Active identifies a book placed on Shelf or Storage.
"""
from contextlib import contextmanager
from functools import wraps
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import time


class StorageError(ValueError):
    pass


TRANSFER_SLOT = 'library_transfer_active'


def release_transfer_slot(client, transfer_id):
    """Release the one global disk-copy slot only for its owning transfer."""
    client.eval(
        "if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('DEL', KEYS[1]) else return 0 end",
        1, TRANSFER_SLOT, transfer_id,
    )


def roots():
    import const
    return Path(const.LIBRARY_DIR), Path(const.STORAGE_DIR)


def location_roots():
    import const
    active, stored = roots()
    return {'active': active, 'shelf': Path(getattr(const, 'SHELF_DIR', '/home/jkane/books')), 'storage': stored}


def managed_link_location(path, author, title):
    """Return the tier named by an exact whole-book link from Active."""
    if Path(path).parent.is_symlink():
        raise StorageError('Author directories must not be symbolic links.')
    path = Path(path)
    if not path.is_symlink():
        return None
    paths = location_roots()
    target = os.readlink(path)
    for location in ('shelf', 'storage'):
        expected = paths[location] / component(author) / component(title)
        if target == str(expected.absolute()):
            return location
    return None


def is_shelf_link(path, author, title):
    return managed_link_location(path, author, title) == 'shelf'


def is_library_book_link(path, author, title):
    return managed_link_location(path, author, title) in ('shelf', 'storage')


def placements(author, title):
    paths = location_roots()
    active = paths['active'] / component(author) / component(title)
    if active.is_symlink():
        location = managed_link_location(active, author, title)
        if location is None:
            raise StorageError('Books must not be arbitrary symbolic links.')
        target = paths[location] / component(author) / component(title)
        return {location: target} if target.is_dir() else {}
    # A physical Active book is authoritative, even when some of its media is
    # mirrored on Shelf or a stale whole-book copy exists elsewhere.
    return {'active': active} if active.is_dir() else {}


def ensure_location_link(author, title, location):
    paths = location_roots()
    if location not in ('shelf', 'storage'):
        raise StorageError('Only Shelf or Storage books can be linked into Active.')
    destination = book_path(paths[location], author, title)
    if not paths[location].is_dir() or not destination.is_dir():
        raise StorageError(f'{location.title()} is unavailable or this book is missing there.')
    link = paths['active'] / component(author) / component(title)
    if link.parent.is_symlink():
        raise StorageError('Author directories must not be symbolic links.')
    current = managed_link_location(link, author, title)
    if current == location:
        return link
    if link.exists() or link.is_symlink():
        raise StorageError('A different copy already exists in Active. Resolve the duplicate first.')
    link.parent.mkdir(parents=True, exist_ok=True)
    # Author profiles stay in the shared working catalog. Seed an existing
    # Shelf profile when this author has never been opened from Active.
    profile = destination.parent / 'author.json'
    target_profile = link.parent / 'author.json'
    if location == 'shelf' and profile.is_file() and not profile.is_symlink() and not target_profile.exists():
        try:
            with target_profile.open('xb') as output, profile.open('rb') as source:
                shutil.copyfileobj(source, output)
        except FileExistsError:
            pass
    try:
        link.symlink_to(destination.absolute(), target_is_directory=True)
    except FileExistsError:
        if managed_link_location(link, author, title) != location:
            raise StorageError('A different copy appeared in Active. Nothing was overwritten.')
    return link


def ensure_shelf_link(author, title):
    return ensure_location_link(author, title, 'shelf')


def ensure_shelf_media_path(author, title, relative, *, directory=False):
    """Route a new or existing generated media path to the parallel Shelf tree."""
    relative = Path(relative)
    if relative.is_absolute() or '..' in relative.parts or not relative.parts:
        raise StorageError('Invalid media path.')
    paths = location_roots()
    active_book = paths['active'] / component(author) / component(title)
    shelf_book = paths['shelf'] / component(author) / component(title)
    if not paths['shelf'].is_dir():
        raise StorageError('Shelf is unavailable; generated media cannot be routed there.')

    if active_book.is_symlink():
        location = managed_link_location(active_book, author, title)
        if location == 'storage':
            raise StorageError('Storage is read-only; move this book to Active or Shelf before rendering.')
        if location != 'shelf' or not shelf_book.is_dir():
            raise StorageError('The Active book link does not resolve to Shelf.')
        output = shelf_book / relative
        if directory:
            output.mkdir(parents=True, exist_ok=True)
        else:
            output.parent.mkdir(parents=True, exist_ok=True)
        return active_book / relative
    if not active_book.is_dir():
        raise StorageError('This book is missing from Active.')

    active_path, shelf_path = active_book / relative, shelf_book / relative
    if active_path.is_symlink():
        if os.readlink(active_path) != str(shelf_path.absolute()):
            raise StorageError('Media path is an unmanaged symbolic link.')
        if directory and not shelf_path.is_dir():
            raise StorageError('Shelf media directory is missing.')
        if not directory and shelf_path.exists() and not shelf_path.is_file():
            raise StorageError('Shelf media path is not a file.')
        return active_path

    if active_path.exists() and shelf_path.exists():
        raise StorageError('Media exists in both Active and Shelf; refusing to overwrite either copy.')
    shelf_path.parent.mkdir(parents=True, exist_ok=True)
    if active_path.exists():
        if directory:
            def signature(root):
                count = total = 0
                for current, dirs, files in os.walk(root):
                    if any((Path(current) / name).is_symlink() for name in dirs + files):
                        raise StorageError('Generated media contains an unmanaged symbolic link.')
                    for name in files:
                        count += 1
                        total += (Path(current) / name).stat().st_size
                return count, total
            shutil.copytree(active_path, shelf_path, copy_function=shutil.copy2)
            if signature(active_path) != signature(shelf_path):
                raise StorageError('Shelf media copy verification failed; the Active copy was kept.')
            shutil.rmtree(active_path)
        else:
            shutil.copy2(active_path, shelf_path)
            if (active_path.stat().st_size != shelf_path.stat().st_size
                    or hashlib.sha256(active_path.read_bytes()).digest()
                    != hashlib.sha256(shelf_path.read_bytes()).digest()):
                shelf_path.unlink(missing_ok=True)
                raise StorageError('Shelf media copy verification failed; the Active copy was kept.')
            active_path.unlink()
    elif directory:
        shelf_path.mkdir(parents=True, exist_ok=True)
    active_path.parent.mkdir(parents=True, exist_ok=True)
    active_path.symlink_to(shelf_path.absolute(), target_is_directory=directory)
    return active_path


def remove_managed_link(author, title):
    path = location_roots()['active'] / component(author) / component(title)
    if managed_link_location(path, author, title):
        path.unlink()


def remove_shelf_link(author, title):
    path = location_roots()['active'] / component(author) / component(title)
    if is_shelf_link(path, author, title):
        path.unlink()


def sync_shelf_catalog(author=None):
    """Ensure external books have their authoritative entry beneath Active."""
    paths = location_roots()
    excluded = {'lost+found', '__pycache__', 'loras', 'sounds', 'global_characters', 'Theater'}
    for location in ('shelf', 'storage'):
        root = paths[location]
        if not root.is_dir():
            continue
        other_location = 'storage' if location == 'shelf' else 'shelf'
        other_root = paths[other_location]
        authors = [root / component(author)] if author else root.iterdir()
        for directory in authors:
            if not directory.is_dir() or directory.is_symlink() or directory.name.startswith('.') or directory.name in excluded:
                continue
            for book in directory.iterdir():
                if not book.is_dir() or book.is_symlink() or book.name.startswith('.') or book.name == '__pycache__':
                    continue
                try:
                    with book_lock(directory.name, book.name):
                        active = paths['active'] / directory.name / book.name
                        if not active.exists() and not active.is_symlink():
                            if (other_root / directory.name / book.name).is_dir():
                                continue
                            ensure_location_link(directory.name, book.name, location)
                except StorageError:
                    continue


def transfer_identity(paths, source, target):
    return {name: [paths[name].stat().st_dev, paths[name].stat().st_ino]
            for name in sorted({'active', source, target})}


def move_controls(author, title, location, status=None):
    """Describe available destinations consistently on the catalog and book page."""
    status = status or {}
    paths = location_roots()
    copies = placements(author, title)
    resume_target = status.get('target')
    resume = (resume_target in paths and can_resume(paths[resume_target] / author / title, status, resume_target))
    if resume:
        return [dict(target=resume_target, source=status.get('source', 'active' if resume_target == 'storage' else 'storage'),
                     available=paths[resume_target].is_dir(), resume=True)]
    controls = []
    active_book = paths['active'] / component(author) / component(title)
    split_media = active_book.is_dir() and any(
        path.is_symlink() for path in active_book.glob('chapter/*/frames')
    )
    if location == 'active' and split_media:
        # The whole-book mover does not merge existing mirrored assets yet.
        return []
    active_link_target = managed_link_location(
        active_book, author, title
    )
    for target, root in paths.items():
        if target == location or not root.is_dir():
            continue
        target_path = root / component(author) / component(title)
        # Returning a whole-book link to Active is valid only when it already
        # names the current source. Never offer a move into a partially mirrored
        # destination, which the whole-book transfer intentionally won't merge.
        if target == 'active':
            if active_link_target != location:
                continue
        elif target_path.exists() or target_path.is_symlink():
            continue
        controls.append(dict(target=target, source=location, available=True, resume=False))
    return controls


def mount_identity(active, stored):
    return {name: [root.stat().st_dev, root.stat().st_ino]
            for name, root in (('active', Path(active)), ('storage', Path(stored)))}


def component(value):
    if not isinstance(value, str) or not value or value.startswith('.') or any(c in value for c in '/\\\0'):
        raise StorageError('Invalid author or book name.')
    return value


def identity_key(author, title):
    component(author)
    component(title)
    return hashlib.sha256(json.dumps([author, title]).encode()).hexdigest()


def status_key(author, title):
    return 'library_transfer:' + identity_key(author, title)


def pending_key(author, title):
    return 'library_transfer_pending:' + identity_key(author, title)


def book_path(root, author, title):
    path = Path(root) / component(author) / component(title)
    if path.parent.is_symlink() or path.is_symlink():
        raise StorageError('Books and author directories must not be symbolic links.')
    return path


@contextmanager
def author_lock(author, *, exclusive=False, wait=False):
    active, _ = roots()
    directory = Path(os.environ.get('LIBRARY_LOCK_DIR', str(active / '.library-locks')))
    directory.mkdir(exist_ok=True)
    key = hashlib.sha256(component(author).encode()).hexdigest()
    with (directory / ('author-' + key)).open('a') as handle:
        try:
            fcntl.flock(handle, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | (0 if wait else fcntl.LOCK_NB))
        except BlockingIOError as error:
            raise StorageError('This book is being moved. Wait for the transfer to finish.') from error
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


@contextmanager
def book_lock(author, title, *, exclusive=False, wait=False):
    active, _ = roots()
    directory = Path(os.environ.get('LIBRARY_LOCK_DIR', str(active / '.library-locks')))
    with author_lock(author, wait=wait):
        with (directory / identity_key(author, title)).open('a') as handle:
            try:
                fcntl.flock(handle, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | (0 if wait else fcntl.LOCK_NB))
            except BlockingIOError as error:
                raise StorageError('This book is being moved. Wait for the transfer to finish.') from error
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)


def assert_active(author, title):
    copies = placements(author, title)
    if not copies:
        paths = location_roots()
        external = [location for location in ('shelf', 'storage')
                    if (paths[location] / component(author) / component(title)).is_dir()]
        if len(external) == 1:
            ensure_location_link(author, title, external[0])
            copies = placements(author, title)
        elif len(external) > 1:
            raise StorageError('This book exists in more than one external location. Resolve it before editing.')
    if 'storage' in copies:
        raise StorageError('This book is in storage and is read-only. Move it to Active or Shelf in the library first.')
    if 'shelf' in copies:
        ensure_shelf_link(author, title)
    elif 'active' not in copies:
        path = location_roots()['active'] / component(author) / component(title)
        location = managed_link_location(path, author, title)
        if location:
            raise StorageError(f'{location.title()} is unavailable or this book is missing there.')
        raise StorageError('This book is missing from Active.')


def task_identity(task):
    if not isinstance(task, dict):
        return None
    if isinstance(task.get('author'), str) and isinstance(task.get('title'), str):
        return task['author'], task['title']
    for key in ('chapter', 'arguments'):
        result = task_identity(task.get(key))
        if result:
            return result
    return None


def guard_task(task):
    if task.get('action') == 'move_book':
        return
    identity = task_identity(task)
    if identity:
        with book_lock(*identity):
            assert_active(*identity)


def protect_submission(function):
    """Keep the shared lock until a job is visible to the transfer's busy check."""
    @wraps(function)
    def guarded(*args, **kwargs):
        task = kwargs.get('task', kwargs.get('payload', args[2] if len(args) > 2 else None))
        try:
            decoded = json.loads(task) if isinstance(task, str) else task
        except (ValueError, TypeError):
            decoded = None
        identity = task_identity(decoded)
        if identity and decoded.get('action') != 'move_book':
            with book_lock(*identity):
                assert_active(*identity)
                return function(*args, **kwargs)
        return function(*args, **kwargs)
    return guarded


def protect_cpu_submission(function):
    @wraps(function)
    def guarded(batch, action, arguments, label):
        identity = task_identity(batch.chapter or {}) or task_identity(arguments)
        if identity and action != 'move_book':
            with book_lock(*identity):
                assert_active(*identity)
                return function(batch, action, arguments, label)
        return function(batch, action, arguments, label)
    return guarded


def register_guards(app):
    """Run before blueprint URL preprocessors can instantiate a Chapter."""
    from flask import g, request

    @app.errorhandler(StorageError)
    def storage_error(error):
        return str(error), 409

    @app.url_value_preprocessor
    def protect_book(endpoint, values):
        if not values or not values.get('author'):
            return
        author, title = values['author'], values.get('title')
        if not title and request.method not in ('GET', 'HEAD', 'OPTIONS'):
            if endpoint in ('library.author.discover_bibliography', 'library.author.change_pen_name'):
                _, stored = roots()
                directory = stored / component(author)
                if directory.is_dir() and any(p.is_dir() for p in directory.iterdir() if not p.name.startswith('.')):
                    raise StorageError('Move this author’s stored books to active before researching the bibliography or renaming the author.')
                if endpoint == 'library.author.change_pen_name':
                    for location in ('shelf', 'storage'):
                        external_author = location_roots()[location] / component(author)
                        if external_author.is_dir() and any(p.is_dir() for p in external_author.iterdir() if not p.name.startswith('.')):
                            raise StorageError(f'Move this author’s {location.title()} books to Active before renaming the author across disks.')
                lock = author_lock(author, exclusive=True)
                lock.__enter__()
                g.library_book_lock = lock
            payload = request.get_json(silent=True)
            if not isinstance(payload, dict):
                payload = request.form
            if endpoint in ('library.author.save_bibliography', 'library.author.add_book'):
                title = payload.get('book_name') or payload.get('title')
                if title and endpoint == 'library.author.add_book':
                    from artifact_editor.author.views import title_to_directory
                    title = title_to_directory(title)
        if not title:
            return
        if endpoint in ('library.move_book', 'library.transfer_status', 'library.book_cover'):
            return
        lock = book_lock(author, title)
        lock.__enter__()
        g.library_book_lock = lock
        assert_active(author, title)

    @app.teardown_request
    def release_book(error):
        lock = g.pop('library_book_lock', None)
        if lock:
            lock.__exit__(None, None, None)


def _mentions(value, author, title):
    if isinstance(value, dict):
        if value.get('author') == author and value.get('title') == title:
            return True
        return any(_mentions(item, author, title) for item in value.values())
    if isinstance(value, list):
        if len(value) >= 2 and value[:2] == [author, title]:
            return True
        return any(_mentions(item, author, title) for item in value)
    if isinstance(value, str):
        if value.startswith('['):
            try:
                return _mentions(json.loads(value), author, title)
            except ValueError:
                pass
        bookdir = author + '/' + title
        return value == bookdir or value.startswith(bookdir + '/') or ('/' + bookdir + '/') in value or value.endswith('/' + bookdir)
    return False


def check_jobs(client, author, title, own_job=None):
    queues = ['video_render_jobs', 'styler_image_tag_jobs', 'styler_image_tag_priority_jobs',
              'styler_idle_jobs', 'chapter_render_jobs', 'cpu_jobs', 'chapter_render_jobs:processing']
    queues.extend(client.scan_iter('cpu_jobs:processing*'))
    payloads = [raw for queue in queues for raw in client.lrange(queue, 0, -1)]
    payloads.extend(client.hvals('cpu_jobs:running'))
    for key in ('styler:worker-running', 'chapter_render:running'):
        raw = client.get(key)
        if raw:
            payloads.append(raw)
    for raw in payloads:
        task = json.loads(raw)
        if task.get('job_id') != own_job and _mentions(task, author, title):
            raise StorageError('This book has queued or running work. Wait for it to finish before moving it.')
    from artifact_editor.book.lora_training import queue_entries
    active_root, _ = roots()
    if any(job.get('state') in ('queued', 'running') for job in queue_entries(active_root, bookdir=author + '/' + title)):
        raise StorageError('This book has queued or running training. Wait for it to finish before moving it.')
    # Training has a separate durable queue, outside the CPU/GPU Redis lists.
    from artifact_editor.chapter.persistence import uses_postgres
    if uses_postgres():
        from sqlalchemy import select
        from artifact_editor.db import models as m, session_scope
        with session_scope() as session:
            book_id = session.scalar(select(m.Book.id).join(m.Author).where(m.Author.name == author, m.Book.title == title))
            chapter_ids = list(session.scalars(select(m.Chapter.id).where(m.Chapter.book_id == book_id))) if book_id else []
            active = {m.TaskState.QUEUED, m.TaskState.BLOCKED, m.TaskState.RUNNING, m.TaskState.CANCELLING}
            for task in session.scalars(select(m.Task).where(m.Task.state.in_(active))):
                if task.chapter_id in chapter_ids or _mentions(task.arguments, author, title):
                    raise StorageError('This book has queued or running work. Wait for it to finish before moving it.')


def inventory(source):
    files, directories = [], []
    def failed(error):
        raise error
    for directory, dirs, names in os.walk(source, onerror=failed):
        for name in dirs + names:
            path = Path(directory) / name
            if path.is_symlink():
                raise StorageError('This book contains symbolic links. Resolve them before moving it.')
            if not path.is_dir() and not path.is_file():
                raise StorageError('This book contains a special file and cannot be moved.')
        directories.extend((Path(directory) / name).relative_to(source) for name in dirs)
        files.extend((Path(directory) / name).relative_to(source) for name in names)
    return files, directories


def digest(path, progress=None):
    result = hashlib.sha256()
    with path.open('rb') as handle:
        last_report = time.monotonic()
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b''):
            result.update(chunk)
            now = time.monotonic()
            if progress and now - last_report >= 1:
                progress()
                last_report = now
    return result.digest()


class TransferDiskIo:
    """Sample read/write rates and busy time for the transfer's physical disks."""

    def __init__(self, source, destination):
        self.devices = {}
        for role, path in (("source", source), ("destination", destination)):
            try:
                device = self._physical_device(path)
            except (OSError, ValueError):
                continue
            self.devices[role] = device
        self.previous = self._read_stats()
        self.previous_at = time.monotonic()

    @staticmethod
    def _physical_device(path):
        path = Path(path)
        while not path.exists():
            path = path.parent
        device = os.stat(path).st_dev
        major, minor = os.major(device), os.minor(device)
        block = (Path("/sys/dev/block") / f"{major}:{minor}").resolve(strict=True)
        if (block / "partition").exists():
            block = block.parent
        return block.name

    @staticmethod
    def _read_stats():
        stats = {}
        with open("/proc/diskstats", encoding="ascii") as diskstats:
            for line in diskstats:
                fields = line.split()
                if len(fields) < 14:
                    continue
                stats[fields[2]] = (
                    int(fields[3]),  # completed reads
                    int(fields[7]),  # completed writes
                    int(fields[12]), # milliseconds doing I/O
                )
        return stats

    def sample(self):
        try:
            current = self._read_stats()
        except OSError:
            return {}
        now = time.monotonic()
        elapsed = now - self.previous_at
        previous = self.previous
        self.previous = current
        self.previous_at = now
        if elapsed <= 0:
            return {}

        output = {}
        for role, device in self.devices.items():
            before, after = previous.get(device), current.get(device)
            if before is None or after is None:
                continue
            reads = max(0, after[0] - before[0]) / elapsed
            writes = max(0, after[1] - before[1]) / elapsed
            busy = max(0, after[2] - before[2]) / (elapsed * 10)
            output.update({
                f"{role}_disk": device,
                f"{role}_read_iops": round(reads, 1),
                f"{role}_write_iops": round(writes, 1),
                f"{role}_busy_percent": round(min(100, busy), 1),
            })
        return output


def rename_book(source, destination):
    from artifact_editor.author.rename import move_no_replace, RenameError
    try:
        move_no_replace(source, destination)
    except RenameError as error:
        message = ('The filesystem does not support safe book moves.' if error.status == 503 else
                   'The destination appeared during the move. Both copies were kept.')
        raise StorageError(message) from error


def can_resume(destination, status, target):
    if status.get('state') not in ('failed', 'cancelled') or status.get('target') != target or not status.get('transfer_id'):
        return False
    try:
        return json.loads((Path(destination) / '.library-transfer.json').read_text()).get('id') == status['transfer_id']
    except (OSError, ValueError, AttributeError):
        return False


def transfer(source, destination, transfer_id, report=lambda **fields: None):
    """Copy and hash-verify everything before retiring any source data.

    A private staging directory and receipt allow the CPU queue to recover a
    claimed transfer after a process/container restart. Never merge two books.
    """
    source, destination = Path(source), Path(destination)
    disk_io = TransferDiskIo(source, destination)
    last_io_report = 0.0

    def report_io(force=False):
        nonlocal last_io_report
        now = time.monotonic()
        if force or now - last_io_report >= 1:
            metrics = disk_io.sample()
            if metrics:
                report(**metrics)
            last_io_report = now

    def report_progress(**fields):
        nonlocal last_io_report
        fields.update(disk_io.sample())
        report(**fields)
        last_io_report = time.monotonic()

    stage = destination.parent / ('.moving-' + transfer_id)
    retired = source.parent / ('.retiring-' + transfer_id)
    receipt = '.library-transfer.json'
    if destination.exists():
        marker = destination / receipt
        if not marker.is_file() or json.loads(marker.read_text()).get('id') != transfer_id:
            raise StorageError('A book already exists at the destination. Nothing was overwritten.')
        if source.exists():
            # Recover the publish-to-retire gap without trusting an old copy.
            files, _ = inventory(source)
            for relative in files:
                if relative.name != receipt and (
                    digest(source / relative, report_io) != digest(destination / relative, report_io)
                ):
                    raise StorageError('The source changed after verification. Both copies were kept.')
    else:
        if not source.is_dir():
            raise StorageError('The source book was not found.')
        files, directories = inventory(source)
        snapshot = {name: ((source / name).stat().st_size, (source / name).stat().st_mtime_ns) for name in files}
        total = sum((source / name).stat().st_size for name in files)
        copied_size = sum((stage / name).stat().st_size for name in files if (stage / name).is_file())
        probe = destination.parent
        while not probe.exists():
            probe = probe.parent
        if shutil.disk_usage(probe).free < max(0, total - copied_size) + 64 * 1024 * 1024:
            raise StorageError('Not enough free space at the destination. The source book was kept.')
        destination.parent.mkdir(parents=True, exist_ok=True)
        if stage.is_symlink():
            raise StorageError('The transfer staging directory must not be a symbolic link.')
        stage.mkdir(exist_ok=True)
        staged_files, staged_directories = inventory(stage)
        for relative in set(staged_files) - set(files):
            (stage / relative).unlink()
        for relative in sorted(set(staged_directories) - set(directories), key=lambda p: len(p.parts), reverse=True):
            (stage / relative).rmdir()
        for relative in directories:
            (stage / relative).mkdir(parents=True, exist_ok=True)
        completed = 0
        last_report = 0
        report_progress(phase='copying', bytes_done=0, bytes_total=total, message='Copying and verifying files…')
        for relative in files:
            original, copied = source / relative, stage / relative
            copied.parent.mkdir(parents=True, exist_ok=True)
            if not copied.is_file() or copied.stat().st_size != original.stat().st_size or (
                digest(original, report_io) != digest(copied, report_io)
            ):
                with original.open('rb') as reader, copied.open('wb') as writer:
                    copied_bytes = 0
                    while chunk := reader.read(8 * 1024 * 1024):
                        writer.write(chunk)
                        copied_bytes += len(chunk)
                        now = time.monotonic()
                        if now - last_report >= 1:
                            report_progress(
                                bytes_done=completed + copied_bytes,
                                bytes_total=total,
                            )
                            last_report = now
                    writer.flush()
                    os.fsync(writer.fileno())
                shutil.copystat(original, copied)
                if digest(original, report_io) != digest(copied, report_io):
                    raise StorageError(f'Copy verification failed for {relative}. The source book was kept.')
            completed += original.stat().st_size
            if time.monotonic() - last_report > 1:
                report_progress(bytes_done=completed, bytes_total=total)
                last_report = time.monotonic()
        report_progress(bytes_done=total, bytes_total=total)
        current_files, current_directories = inventory(source)
        if set(current_files) != set(files) or set(current_directories) != set(directories) or any(
            ((source / name).stat().st_size, (source / name).stat().st_mtime_ns) != snapshot[name] for name in files
        ):
            raise StorageError('The source book changed during the move. The source was kept; try again after all work stops.')
        for relative in reversed(directories):
            shutil.copystat(source / relative, stage / relative)
        with (stage / receipt).open('w') as handle:
            json.dump({'id': transfer_id}, handle)
            handle.flush()
            os.fsync(handle.fileno())
        shutil.copystat(source, stage)
        rename_book(stage, destination)
        directory_fd = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    report(phase='removing_source', message='Verified copy complete; freeing source space…')
    if source.exists():
        rename_book(source, retired)
    if retired.exists():
        shutil.rmtree(retired)
    report(state='complete', phase='complete', message='Book moved successfully.')


def execute_transfer(task):
    from cpu_queue import connection
    arguments = task['arguments']
    author, title, target = arguments['author'], arguments['title'], arguments['target']
    transfer_id = arguments['transfer_id']
    client = connection()
    key = status_key(author, title)
    def report(**fields):
        client.hset(key, mapping=fields)
    try:
        with book_lock(author, title, exclusive=True, wait=True):
            check_jobs(client, author, title, own_job=task['job_id'])
            paths = location_roots()
            active = paths['active']
            # Only CPU workers receive a writable storage mount.
            stored = Path(os.environ.get('BOOK_STORAGE_WRITE_DIR', '/storage-transfer'))
            if target not in paths:
                raise StorageError('Invalid storage destination.')
            source = arguments.get('source', 'active' if target == 'storage' else 'storage')
            if source not in paths or source == target:
                raise StorageError('Invalid source location.')
            paths['storage'] = stored
            if any(not paths[name].is_dir() for name in (source, target)):
                raise StorageError('The source or destination disk is not mounted.')
            expected = (transfer_identity(paths, source, target) if arguments.get('source')
                        else mount_identity(active, stored))
            if arguments.get('mount_identity') != expected:
                raise StorageError('The library and worker storage mounts differ. Restart the library and worker services, then retry.')
            # An Active catalog link is not an existing physical destination.
            # Remove it only under the book's exclusive transfer lock.
            if target == 'active':
                remove_managed_link(author, title)
            report(state='running', message='Checking book files…')
            def progress(**fields):
                # Publish completion after restoring the working catalog link.
                if fields.get('state') != 'complete':
                    report(**fields)
            transfer(book_path(paths[source], author, title), book_path(paths[target], author, title), transfer_id, progress)
            if target in ('shelf', 'storage'):
                remove_managed_link(author, title)
                ensure_location_link(author, title, target)
            else:
                remove_managed_link(author, title)
            report(state='complete', phase='complete', message=f'Book moved to {target.title()} successfully.')
    except Exception as error:
        report(state='failed', message=str(error))
        raise
    finally:
        client.delete(pending_key(author, title))
        client.close()
