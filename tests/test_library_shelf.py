"""Shelf stays writable through existing editor paths and verified tier moves."""
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from flask import Flask
import pytest

from artifact_editor.library import storage


@pytest.fixture
def disks(tmp_path, monkeypatch):
    import const
    paths = {name: tmp_path / name for name in ('active', 'shelf', 'storage')}
    for path in paths.values():
        path.mkdir()
    monkeypatch.setattr(storage, 'roots', lambda: (paths['active'], paths['storage']))
    monkeypatch.setattr(const, 'LIBRARY_DIR', str(paths['active']))
    monkeypatch.setattr(const, 'SHELF_DIR', str(paths['shelf']))
    monkeypatch.setenv('BOOK_STORAGE_WRITE_DIR', str(paths['storage']))
    monkeypatch.delenv('LIBRARY_LOCK_DIR', raising=False)
    return paths


def make_book(root):
    book = root / 'Author/Book'
    (book / 'chapter/0001/english').mkdir(parents=True)
    (book / 'config.json').write_text('{"title":"Book","publication_year":"1904"}')
    (book / 'chapter/0001/english/chapter.txt').write_text('The original story.')
    (book / 'empty').mkdir()
    return book


def test_shelf_edit_and_worker_paths_write_to_physical_shelf(disks):
    source = make_book(disks['shelf'])
    storage.assert_active('Author', 'Book')
    link = disks['active'] / 'Author/Book'
    assert link.is_symlink() and link.resolve() == source
    assert storage.placements('Author', 'Book') == {'shelf': source}
    (link / 'config.json').write_text('{"title":"Book","edited":true}')
    storage.guard_task({'action': 'draw_frame', 'chapter': {'author': 'Author', 'title': 'Book'}})
    frame = link / 'chapter/0001/frame.png'
    frame.write_bytes(b'worker output')
    assert json.loads((source / 'config.json').read_text())['edited']
    assert (source / 'chapter/0001/frame.png').read_bytes() == b'worker output'


def test_existing_shelf_catalog_is_editable_and_does_not_duplicate_books(disks):
    from artifact_editor.author.profile import bibliography
    source = make_book(disks['shelf'])
    (source.parent / 'author.json').write_text('{"biography":"Existing profile"}')
    storage.sync_shelf_catalog()
    books = bibliography(disks['active'] / 'Author')
    assert [book['title'] for book in books] == ['Book']
    assert books[0]['processed']
    assert json.loads((disks['active'] / 'Author/author.json').read_text())['biography'] == 'Existing profile'
    assert list(storage.placements('Author', 'Book')) == ['shelf']


def test_shelf_http_access_is_writable_while_storage_remains_read_only(disks):
    make_book(disks['shelf'])
    app = Flask(__name__)
    storage.register_guards(app)
    app.add_url_rule('/library/<author>/<title>/edit', endpoint='book.edit',
                     view_func=lambda **_: 'editable', methods=['GET', 'PUT'])
    client = app.test_client()
    assert client.put('/library/Author/Book/edit').status_code == 200
    make_book(disks['storage'])
    assert client.put('/library/Author/Book/edit').status_code == 409


def test_unmounted_shelf_cannot_fall_back_to_writing_active(disks):
    shelf = make_book(disks['shelf'])
    storage.ensure_shelf_link('Author', 'Book')
    shelf.rename(shelf.with_name('Offline'))
    with pytest.raises(storage.StorageError, match='unavailable'):
        storage.assert_active('Author', 'Book')
    assert (disks['active'] / 'Author/Book').is_symlink()
    assert not (disks['active'] / 'Author/Book').exists()


def test_only_exact_shelf_catalog_links_are_allowed(disks):
    make_book(disks['shelf'])
    author = disks['active'] / 'Author'
    author.mkdir()
    (author / 'Book').symlink_to(disks['shelf'])
    with pytest.raises(storage.StorageError, match='symbolic links'):
        storage.assert_active('Author', 'Book')


def test_duplicate_physical_copies_are_never_silently_selected(disks):
    make_book(disks['active'])
    make_book(disks['shelf'])
    with pytest.raises(storage.StorageError, match='multiple locations'):
        storage.assert_active('Author', 'Book')
    assert storage.move_controls('Author', 'Book', 'active') == []
    storage.sync_shelf_catalog()
    assert not (disks['active'] / 'Author/Book').is_symlink()


def test_shelf_image_reconnect_snapshot_and_animation_cleanup(disks, monkeypatch):
    import const
    from artifact_editor.images.completions import chapter_image_snapshot
    from artifact_editor.chapter.timeline import remove_image_animation
    from bs4 import BeautifulSoup
    source = make_book(disks['shelf'])
    storage.ensure_shelf_link('Author', 'Book')
    paragraph = source / 'chapter/0001/paragraphs/000000'
    paragraph.mkdir(parents=True)
    (paragraph / 'done.png').write_bytes(b'image')
    video = paragraph / 'special.mp4'
    video.write_bytes(b'animation')
    xml = '<chapter><paragraph index="0"><image index="1" src="done.png" image_generation_id="version"/></paragraph></chapter>'
    (source / 'chapter/0001/english/chapter.xml').write_text(xml)
    snapshot = chapter_image_snapshot(dict(author='Author', title='Book', language='english', chapter_number=1))
    assert snapshot['images'][0]['index'] == 1
    soup = BeautifulSoup(xml, 'xml')
    soup.image['animated_src'] = str(video)
    output = disks['active'] / 'comfy-output'
    output.mkdir()
    monkeypatch.setattr(const, 'COMFY_DIRS', {'artifactserver': {'OUTPUT_DIR': str(output)}})
    remove_image_animation(SimpleNamespace(nice='shelf-test', get_paragraphdir=lambda _: 'Author/Book/chapter/0001/paragraphs/000000'), soup.image)
    assert not video.exists()
    assert (paragraph / 'done.png').is_file()


@pytest.mark.parametrize('source,target', [(a, b) for a in ('active', 'shelf', 'storage')
                                        for b in ('active', 'shelf', 'storage') if a != b])
def test_verified_moves_between_every_pair(disks, monkeypatch, source, target):
    import cpu_queue
    make_book(disks[source])
    if source == 'shelf':
        storage.ensure_shelf_link('Author', 'Book')
    client = Mock()
    monkeypatch.setattr(cpu_queue, 'connection', lambda: client)
    monkeypatch.setattr(storage, 'check_jobs', Mock())
    storage.execute_transfer({'job_id': 'move', 'arguments': dict(author='Author', title='Book',
        source=source, target=target, transfer_id='move', mount_identity=storage.transfer_identity(disks, source, target))})
    destination = disks[target] / 'Author/Book'
    assert (destination / 'chapter/0001/english/chapter.txt').read_text() == 'The original story.'
    assert (destination / 'empty').is_dir()
    assert list(storage.placements('Author', 'Book')) == [target]
    if target == 'shelf':
        assert (disks['active'] / 'Author/Book').resolve() == destination
    elif target == 'storage':
        assert not (disks['active'] / 'Author/Book').is_symlink()
        with pytest.raises(storage.StorageError, match='read-only'):
            storage.assert_active('Author', 'Book')
    else:
        assert not (disks['active'] / 'Author/Book').is_symlink()
    assert client.hset.call_args.kwargs['mapping']['state'] == 'complete'


def test_failed_move_from_shelf_remains_editable_and_retryable(disks, monkeypatch):
    import cpu_queue
    shelf = make_book(disks['shelf'])
    storage.ensure_shelf_link('Author', 'Book')
    client = Mock()
    monkeypatch.setattr(cpu_queue, 'connection', lambda: client)
    monkeypatch.setattr(storage, 'check_jobs', Mock())
    copy = storage.shutil.copyfileobj
    monkeypatch.setattr(storage.shutil, 'copyfileobj', Mock(side_effect=OSError('Disk full')))
    task = {'job_id': 'move', 'arguments': dict(author='Author', title='Book', source='shelf', target='active',
        transfer_id='retry', mount_identity=storage.transfer_identity(disks, 'shelf', 'active'))}
    with pytest.raises(OSError, match='Disk full'):
        storage.execute_transfer(task)
    storage.assert_active('Author', 'Book')
    assert (disks['active'] / 'Author/Book').resolve() == shelf
    monkeypatch.setattr(storage.shutil, 'copyfileobj', copy)
    storage.execute_transfer(task)
    assert list(storage.placements('Author', 'Book')) == ['active']


def test_author_rename_cannot_orphan_shelf_catalog_links(disks):
    make_book(disks['shelf'])
    storage.ensure_shelf_link('Author', 'Book')
    app = Flask(__name__)
    storage.register_guards(app)
    app.add_url_rule('/library/<author>/rename', endpoint='library.author.change_pen_name',
                     view_func=lambda **_: 'renamed', methods=['POST'])
    response = app.test_client().post('/library/Author/rename')
    assert response.status_code == 409
    assert b'Shelf' in response.data


def test_http_submission_records_both_tiers_and_accepts_active_catalog_link(disks, monkeypatch):
    import cpu_queue
    from unittest.mock import MagicMock
    from artifact_editor.library import views
    make_book(disks['shelf'])
    storage.ensure_shelf_link('Author', 'Book')
    client = MagicMock()
    client.hgetall.return_value = {}
    client.set.return_value = True
    client.get.return_value = '{"workers":1}'
    monkeypatch.setattr(cpu_queue, 'connection', lambda: client)
    monkeypatch.setattr(storage, 'check_jobs', Mock())
    app = Flask(__name__)
    app.add_url_rule('/library/<author>/<title>/location', endpoint='library.move_book',
                     view_func=views.move_book, methods=['POST'])
    response = app.test_client().post('/library/Author/Book/location', json={'source':'shelf', 'target':'active'})
    assert response.status_code == 202
    pipe = client.pipeline.return_value.__enter__.return_value
    payload = json.loads(pipe.lpush.call_args.args[1])
    assert payload['action'] == 'move_book'
    assert payload['arguments']['source'] == 'shelf'
    assert payload['arguments']['target'] == 'active'
    assert payload['arguments']['mount_identity'] == storage.transfer_identity(disks, 'shelf', 'active')
    assert (disks['active'] / 'Author/Book').is_symlink()  # worker owns the actual move


@pytest.mark.parametrize('payload', [{'target':[]}, {'target':'shelf','source':[]}, {'target':'unknown'}])
def test_bad_destination_payload_is_rejected_without_queuing(disks, monkeypatch, payload):
    import cpu_queue
    from unittest.mock import MagicMock
    from artifact_editor.library import views
    client = MagicMock()
    client.hgetall.return_value = {}
    monkeypatch.setattr(cpu_queue, 'connection', lambda: client)
    app = Flask(__name__)
    app.add_url_rule('/library/<author>/<title>/location', view_func=views.move_book, methods=['POST'])
    assert app.test_client().post('/library/Author/Book/location', json=payload).status_code in (400,409)
    client.pipeline.assert_not_called()
