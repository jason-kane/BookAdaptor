import glob
import os
import shutil

from flask import (
    Blueprint,
    render_template,
    request,
    send_from_directory,
)

import const
import logger
from artifact_editor import (
    config,
    tools,
)
from artifact_editor.author.author import Author
from artifact_editor.author.profile import bibliography, bibliography_groups


log = logger.log(__name__)

bp = Blueprint(
    'library',
    __name__,
    template_folder=os.path.join(
        os.path.dirname(__file__),
        "templates"
    ),
)

def library_authors():
    """Merge physical inventories, keeping Shelf writable and Storage read-only."""
    from pathlib import Path
    from .storage import location_roots, placements, sync_shelf_catalog
    sync_shelf_catalog()
    paths = location_roots()
    active = paths['active']
    excluded = {'home', 'active', 'lost+found', 'global_characters', 'Theater',
                'loras', 'sounds', 'stockpile', 'style_exploration', '__pycache__'}
    names = set()
    for root in paths.values():
        if root.is_dir():
            names.update(p.name for p in root.iterdir() if p.is_dir() and not p.is_symlink()
                         and not p.name.startswith('.') and p.name not in excluded)
    authors = []
    for name in sorted(names):
        author = Author(name)
        books = []
        for location, root in paths.items():
            directory = root / name
            if directory.is_dir():
                for book in bibliography(directory):
                    copies = placements(name, book['title'])
                    if location not in copies:
                        continue  # Shelf's Active catalog link is not a second copy.
                    book['location'] = location
                    book['conflict'] = len(copies) > 1
                    books.append(book)
        author.book_groups = bibliography_groups(books)
        author.book_count = len(books)
        author.processed_count = sum(book['processed'] for book in books)
        author.is_active = (active / name).is_dir()
        authors.append(author)
    return authors


def library_page(section):
    from .storage import location_roots, status_key, move_controls
    from cpu_queue import connection
    import redis
    statuses = {}
    client = connection()
    try:
        keys = list(client.scan_iter('library_transfer:*'))
        if keys:
            with client.pipeline() as pipe:
                for key in keys:
                    pipe.hgetall(key)
                statuses = dict(zip(keys, pipe.execute()))
    except redis.RedisError:
        pass
    finally:
        client.close()
    authors = library_authors()
    size_client = connection()
    try:
        from .book_size import cached_sizes
        size_paths = location_roots()
        size_books = [
            (author.name, book['title'], book['location'])
            for author in authors for group in author.book_groups for book in group['books']
        ]
        sizes = cached_sizes(size_client, size_books, size_paths)
        for author in authors:
            for group in author.book_groups:
                for book in group['books']:
                    book['disk_size'] = sizes.get(
                        (author.name, book['title'], book['location']), '—'
                    )
    finally:
        size_client.close()
    for author in authors:
        for group in author.book_groups:
            for book in group['books']:
                book['transfer'] = statuses.get(status_key(author.name, book['title']), {})
                book['move_controls'] = move_controls(author.name, book['title'], book['location'], book['transfer'])
    paths = location_roots()
    capacity = {}
    for name, root in paths.items():
        try:
            if root.is_dir():
                usage = shutil.disk_usage(root)
                capacity[name] = {
                    'total': usage.total,
                    'used': usage.used,
                    'free': usage.free,
                    'percent': round(usage.used / usage.total * 100) if usage.total else 0,
                    'device': root.stat().st_dev,
                }
            else:
                capacity[name] = None
        except OSError:
            capacity[name] = None
    return render_template('library.html', authors=authors, section=section,
                           storage_available=paths['storage'].is_dir(), shelf_available=paths['shelf'].is_dir(),
                           capacity=capacity)


@bp.route('/textoviva')
def textoviva():
    return library_page('textoviva')


@bp.route('/')
def library():
    return library_page('library')


@bp.route('/<author>/<title>/cover.png')
def book_cover(author, title):
    from .storage import placements
    for directory in placements(author, title).values():
        for relative in ('cover.png', 'chapter/0001/cover.png'):
            path = directory / relative
            if path.is_file():
                return send_from_directory(directory, relative)
    return send_from_directory(os.path.join(os.path.dirname(__file__), 'static'), 'placeholder_cover.png')


@bp.post('/<author>/<title>/location')
def move_book(author, title):
    import uuid
    import redis
    from flask import jsonify
    from cpu_queue import Batch, connection
    from .storage import (StorageError, location_roots, placements, book_path, book_lock, check_jobs,
                          pending_key, status_key, transfer_identity, can_resume,
                          TRANSFER_SLOT, release_transfer_slot)
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        return jsonify(error='Choose Active, Shelf, or Storage.'), 400
    target = payload.get('target')
    paths = location_roots()
    if not isinstance(target, str) or target not in paths:
        return jsonify(error='Choose Active, Shelf, or Storage.'), 400
    if not paths[target].is_dir():
        return jsonify(error=f'{target.title()} is not mounted.'), 503
    client = connection()
    reserved = False
    transfer_slot_reserved = False
    submitted = False
    try:
        with book_lock(author, title, exclusive=True):
            previous = client.hgetall(status_key(author, title))
            source = payload.get('source')
            if source is None:
                candidates = set(placements(author, title)) - {target}
                source = next(iter(candidates)) if len(candidates) == 1 else None
            if not isinstance(source, str) or source not in paths or source == target:
                raise StorageError('Choose the source location for this book.')
            if not paths[source].is_dir():
                raise StorageError(f'{source.title()} is not mounted.')
            destination = book_path(paths[target], author, title) if target != 'active' else paths['active'] / author / title
            from .storage import is_shelf_link
            virtual_destination = target == 'active' and is_shelf_link(destination, author, title)
            resumable = not virtual_destination and can_resume(destination, previous, target)
            if not book_path(paths[source], author, title).is_dir() and not resumable:
                raise StorageError('The source book was not found.')
            if not virtual_destination and destination.exists() and not resumable:
                raise StorageError('A book already exists at the destination. Nothing was overwritten.')
            if destination.is_symlink() and not virtual_destination:
                raise StorageError('Books must not be arbitrary symbolic links.')
            check_jobs(client, author, title)
            reserved = bool(client.set(pending_key(author, title), '1', nx=True))
            if not reserved:
                raise StorageError('A move is already queued for this book.')
            transfer_id = (previous.get('transfer_id') if previous.get('state') in ('failed', 'cancelled')
                           and previous.get('target') == target else None) or uuid.uuid4().hex
            transfer_slot_reserved = bool(client.set(TRANSFER_SLOT, transfer_id, nx=True))
            if not transfer_slot_reserved:
                raise StorageError('Another book is already moving. Wait for it to finish before starting another move.')
            client.hset(status_key(author, title), mapping=dict(
                state='queued', source=source, target=target, author=author, title=title,
                transfer_id=transfer_id,
                job_id='', phase='', bytes_done=0, bytes_total=0, message='Waiting for a CPU worker…'))
            batch = Batch(client, chapter=dict(author=author, title=title), track_status=True)
            job_id = batch.submit('move_book', dict(author=author, title=title, source=source, target=target,
                                                  transfer_id=transfer_id, mount_identity=transfer_identity(paths, source, target)),
                                  f'Move to {target} · {title}')
            submitted = True
            client.hset(status_key(author, title), 'job_id', job_id)
        if not client.get('cpu_jobs:pool'):
            from worker_startup import ensure_worker_services
            try:
                ensure_worker_services(cpu_only=True)
            except RuntimeError:
                client.hset(status_key(author, title), 'message', 'Queued; CPU worker needs to be started.')
        return jsonify(job_id=job_id, state='queued'), 202
    except StorageError as error:
        return jsonify(error=str(error)), 409
    except redis.RedisError:
        return jsonify(error='The task service is unavailable. Try again shortly.'), 503
    finally:
        if reserved and not submitted:
            client.delete(pending_key(author, title))
            if transfer_slot_reserved:
                release_transfer_slot(client, transfer_id)
            client.hset(status_key(author, title), mapping=dict(state='failed', message='Unable to queue the move.'))
        client.close()


@bp.get('/<author>/<title>/location')
def transfer_status(author, title):
    from flask import jsonify
    from cpu_queue import connection
    from .storage import status_key
    from .transfer_events import snapshot
    client = connection()
    try:
        return jsonify(snapshot(client, status_key(author, title)))
    finally:
        client.close()


@bp.route("/author/add", methods=["POST"])
def add_author():
    author_name = request.form.get("author_name")
    if not author_name:
        return "Missing author name", 400

    author = Author(author_name)
    if os.path.exists(author.authordir):
        return f"Author {author_name} already exists", 400

    os.makedirs(author.authordir, exist_ok=True)

    return f"Author {author_name} created", 200


@bp.route("/<author>/<title>/<chapter_number>/video/cover.png")
@bp.route("/<author>/<title>/<chapter_number>/cover.png")
def chapter_cover(author, title, chapter_number):
    """if we have a cover image, use it."""
    log.info(f"Request for chapter cover: {author}/{title}/{chapter_number}")
    chapter = int(chapter_number.lstrip("0"))
    chapterdir = tools.get_chapterdir(author, title, chapter)
    
    if not chapterdir:
        log.info(f"Chapter directory not found for {author}/{title}/{chapter_number}")
        return render_template("404.html"), 404

    cover_path = os.path.join(
        const.LIBRARY_DIR, chapterdir, "cover.png"
    )
    
    if not os.path.exists(cover_path):
        log.info(f"Cover image not found for {chapterdir}, generating from first paragraph.")
        first_paragraph_dir = os.path.join(
            const.LIBRARY_DIR,
            chapterdir,
            "paragraphs", 
            "000000"
        )
        
        try:
            first_frame = glob.glob(
                os.path.join(
                    first_paragraph_dir,
                    "img*.png"
                )
            )[0]
        except IndexError:
            log.error(f"No images found in {first_paragraph_dir} to use as cover.")
            return send_from_directory(
                os.path.join(
                    os.path.dirname(__file__), 
                    "static"
                ),
                "placeholder_cover.png"
            )

        # copy the first image as the cover image   
        shutil.copyfile(
            os.path.join(first_paragraph_dir, first_frame),
            cover_path
        )
    
    return send_from_directory(
        os.path.join(const.LIBRARY_DIR, chapterdir),
        "cover.png"
    )



@bp.route("/sounds/<src>")
def sounds_src(src):
    sound_path = os.path.join(const.SOUND_DIR, src)

    if not os.path.exists(sound_path):
        log.error(f"Sound file not found: {sound_path}")
        return "Sound file not found", 404

    return send_from_directory(
        const.SOUND_DIR,
        src
    )
