import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from flask import Flask

from artifact_editor.library import storage


@pytest.fixture
def locations(tmp_path, monkeypatch):
    import const
    active, stored = tmp_path / 'active', tmp_path / 'storage'
    active.mkdir()
    stored.mkdir()
    monkeypatch.setattr(storage, 'roots', lambda: (active, stored))
    monkeypatch.setattr(const, 'SHELF_DIR', str(tmp_path / 'shelf'))
    return active, stored


def make_book(root):
    book = root / 'Author' / 'Book'
    (book / 'chapter/0001').mkdir(parents=True)
    (book / 'chapter/0001/chapter.xml').write_text('<chapter>Original text</chapter>')
    (book / 'config.json').write_text('{"publication_year": "1904"}')
    (book / 'empty').mkdir()
    return book


def test_round_trip_retains_assets_and_empty_directories(locations):
    active, stored = locations
    source = make_book(active)
    destination = stored / 'Author/Book'
    storage.transfer(source, destination, 'outbound')
    assert not source.exists()
    assert (destination / 'empty').is_dir()
    assert (destination / 'chapter/0001/chapter.xml').read_text() == '<chapter>Original text</chapter>'
    with pytest.raises(storage.StorageError, match='read-only'):
        storage.assert_active('Author', 'Book')
    storage.transfer(destination, source, 'inbound')
    assert not destination.exists()
    assert json.loads((source / 'config.json').read_text()) == {'publication_year': '1904'}
    storage.assert_active('Author', 'Book')


def test_insufficient_space_never_removes_source(locations, monkeypatch):
    active, stored = locations
    source = make_book(active)
    monkeypatch.setattr(storage.shutil, 'disk_usage', lambda path: SimpleNamespace(free=0))
    with pytest.raises(storage.StorageError, match='Not enough free space'):
        storage.transfer(source, stored / 'Author/Book', 'full')
    assert (source / 'config.json').is_file()
    assert not (stored / 'Author/Book').exists()


def test_copy_failure_keeps_source_and_can_resume(locations, monkeypatch):
    active, stored = locations
    source = make_book(active)
    destination = stored / 'Author/Book'
    copy = storage.shutil.copyfileobj
    monkeypatch.setattr(storage.shutil, 'copyfileobj', Mock(side_effect=OSError('disk full')))
    with pytest.raises(OSError, match='disk full'):
        storage.transfer(source, destination, 'retry')
    assert (source / 'config.json').is_file()
    assert not destination.exists()
    monkeypatch.setattr(storage.shutil, 'copyfileobj', copy)
    storage.transfer(source, destination, 'retry')
    assert not source.exists()
    assert (destination / 'config.json').is_file()


def test_destination_collision_never_merges_or_deletes(locations):
    active, stored = locations
    source = make_book(active)
    destination = make_book(stored)
    (destination / 'config.json').write_text('different')
    with pytest.raises(storage.StorageError, match='already exists'):
        storage.transfer(source, destination, 'collision')
    assert (source / 'config.json').is_file()
    assert (destination / 'config.json').read_text() == 'different'


def test_bad_copy_is_detected_before_source_removal(locations, monkeypatch):
    active, stored = locations
    source = make_book(active)
    monkeypatch.setattr(storage.shutil, 'copyfileobj', lambda reader, writer, size: writer.write(b'corrupt'))
    with pytest.raises(storage.StorageError, match='verification failed'):
        storage.transfer(source, stored / 'Author/Book', 'corrupt')
    assert (source / 'config.json').is_file()


def test_source_mutation_during_copy_keeps_original(locations, monkeypatch):
    active, stored = locations
    source = make_book(active)
    copy = storage.shutil.copyfileobj
    def mutate(reader, writer, size):
        copy(reader, writer, size)
        (source / 'new-file.txt').write_text('new data')
    monkeypatch.setattr(storage.shutil, 'copyfileobj', mutate)
    with pytest.raises(storage.StorageError, match='changed during'):
        storage.transfer(source, stored / 'Author/Book', 'changed')
    assert (source / 'new-file.txt').read_text() == 'new data'


def test_restart_after_publish_finishes_cleanup(locations):
    active, stored = locations
    source = make_book(active)
    destination = stored / 'Author/Book'
    def stop(**fields):
        if fields.get('phase') == 'removing_source':
            raise RuntimeError('process stopped')
    with pytest.raises(RuntimeError):
        storage.transfer(source, destination, 'recover', stop)
    assert source.exists() and destination.exists()
    storage.transfer(source, destination, 'recover')
    assert not source.exists()
    assert (destination / 'config.json').is_file()


def test_restart_during_source_cleanup(locations, monkeypatch):
    active, stored = locations
    source = make_book(active)
    destination = stored / 'Author/Book'
    remove = storage.shutil.rmtree
    monkeypatch.setattr(storage.shutil, 'rmtree', Mock(side_effect=OSError('interrupted cleanup')))
    with pytest.raises(OSError):
        storage.transfer(source, destination, 'cleanup')
    assert (source.parent / '.retiring-cleanup').exists()
    monkeypatch.setattr(storage.shutil, 'rmtree', remove)
    storage.transfer(source, destination, 'cleanup')
    assert not (source.parent / '.retiring-cleanup').exists()
    assert (destination / 'config.json').is_file()


def test_symlinks_and_traversal_are_rejected(locations):
    active, stored = locations
    source = make_book(active)
    (source / 'escape').symlink_to(active)
    with pytest.raises(storage.StorageError, match='symbolic links'):
        storage.transfer(source, stored / 'Author/Book', 'links')
    for name in ('..', '../escape', '/absolute', '.hidden', 'a\\b'):
        with pytest.raises(storage.StorageError):
            storage.book_path(active, 'Author', name)


def test_readonly_http_guard_runs_before_chapter_preprocessors(locations):
    make_book(locations[1])
    app = Flask(__name__)
    storage.register_guards(app)
    called = Mock()
    app.url_value_preprocessors.setdefault('chapter', []).append(called)
    app.add_url_rule('/library/<author>/<title>/edit', endpoint='chapter.edit', view_func=lambda **kw: 'editable', methods=['GET', 'POST', 'PUT'])
    for method in ('get', 'post', 'put'):
        response = getattr(app.test_client(), method)('/library/Author/Book/edit')
        assert response.status_code == 409
        assert b'read-only' in response.data
    called.assert_not_called()
    assert not (locations[0] / 'Author/Book').exists()


def test_move_lock_blocks_http_and_queue_submissions(locations):
    make_book(locations[0])
    enqueue = Mock()
    protected = storage.protect_submission(enqueue)
    with storage.book_lock('Author', 'Book', exclusive=True):
        with pytest.raises(storage.StorageError, match='being moved'):
            protected(None, 'jobs', {'author': 'Author', 'title': 'Book'})
    enqueue.assert_not_called()
    protected(None, 'jobs', {'author': 'Author', 'title': 'Book'})
    enqueue.assert_called_once()


def test_author_rename_and_discovery_lock_conflicts_with_transfer(locations):
    with storage.author_lock('Author', exclusive=True):
        with pytest.raises(storage.StorageError):
            with storage.book_lock('Author', 'Book', exclusive=True):
                pass


def test_stored_book_rejects_nested_task_and_serialized_submission(locations):
    make_book(locations[1])
    task = dict(chapter=dict(author='Author', title='Book'), action='draw_frame')
    with pytest.raises(storage.StorageError, match='read-only'):
        storage.guard_task(task)
    enqueue = Mock()
    protected = storage.protect_submission(enqueue)
    with pytest.raises(storage.StorageError):
        protected(client=None, queue='jobs', payload=json.dumps(task))
    enqueue.assert_not_called()
    protected(None, 'jobs', 'legacy non-json payload')
    enqueue.assert_called_once()


def test_busy_book_cannot_move(locations):
    client = Mock()
    client.scan_iter.return_value = []
    client.lrange.return_value = [json.dumps(dict(job_id='render', chapter=dict(author='Author', title='Book')))]
    client.hvals.return_value = []
    client.get.return_value = None
    with pytest.raises(storage.StorageError, match='queued or running'):
        storage.check_jobs(client, 'Author', 'Book', own_job='move')


def test_retry_does_not_resurrect_obsolete_staging_files(locations):
    active, stored = locations
    source = make_book(active)
    stage = stored / 'Author/.moving-retry/obsolete'
    stage.mkdir(parents=True)
    (stage / 'old.txt').write_text('obsolete')
    destination = stored / 'Author/Book'
    storage.transfer(source, destination, 'retry')
    assert not (destination / 'obsolete').exists()


def test_destination_created_during_copy_is_never_overwritten(locations):
    active, stored = locations
    source = make_book(active)
    destination = stored / 'Author/Book'
    def competing_destination(**fields):
        if fields.get('phase') == 'copying':
            destination.mkdir()
    with pytest.raises(storage.StorageError, match='destination appeared'):
        storage.transfer(source, destination, 'race', competing_destination)
    assert (source / 'config.json').is_file()
    assert destination.is_dir() and list(destination.iterdir()) == []


def test_worker_refuses_different_library_mounts(locations, monkeypatch):
    import cpu_queue
    source = make_book(locations[0])
    client = Mock()
    monkeypatch.setattr(cpu_queue, 'connection', lambda: client)
    monkeypatch.setattr(storage, 'check_jobs', Mock())
    monkeypatch.setenv('BOOK_STORAGE_WRITE_DIR', str(locations[1]))
    task = dict(job_id='move', arguments=dict(author='Author', title='Book',
                target='storage', transfer_id='different', mount_identity={'active': [0, 0]}))
    with pytest.raises(storage.StorageError, match='mounts differ'):
        storage.execute_transfer(task)
    assert (source / 'config.json').is_file()
    assert not (locations[1] / 'Author/Book').exists()
    client.delete.assert_called_once_with(storage.pending_key('Author', 'Book'))


def test_author_bibliography_and_rename_cannot_modify_stored_books(locations):
    make_book(locations[1])
    app = Flask(__name__)
    storage.register_guards(app)
    called = Mock(return_value='changed')
    for endpoint in ('save_bibliography', 'discover_bibliography', 'change_pen_name'):
        app.add_url_rule('/library/<author>/' + endpoint, endpoint='library.author.' + endpoint,
                         view_func=lambda author: called(author), methods=['POST'])
    for endpoint in ('save_bibliography', 'discover_bibliography', 'change_pen_name'):
        response = app.test_client().post('/library/Author/' + endpoint, json={'book_name': 'Book'})
        assert response.status_code == 409
    called.assert_not_called()


def test_only_a_matching_failed_transfer_can_resume_published_copy(locations):
    destination = make_book(locations[1])
    status = dict(state='failed', transfer_id='original', target='storage')
    assert not storage.can_resume(destination, status, 'storage')
    (destination / '.library-transfer.json').write_text('{"id": "original"}')
    assert storage.can_resume(destination, status, 'storage')
    assert not storage.can_resume(destination, status, 'active')
    assert not storage.can_resume(destination, {**status, 'transfer_id': 'other'}, 'storage')


def test_running_moves_cannot_be_cancelled_mid_cleanup(locations):
    import cpu_queue
    client = Mock()
    client.lrange.return_value = []
    client.hvals.return_value = [json.dumps(dict(job_id='moving', action='move_book'))]
    client.get.return_value = None
    client.scan_iter.return_value = []
    result = cpu_queue.cancel_task(client, 'moving', allow_running=True)
    assert result['state'] == 'cpu_cancel_rejected'
    assert 'verifying' in result['message']
    client.set.assert_not_called()
