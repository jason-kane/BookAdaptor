from contextlib import contextmanager, nullcontext
import json

import pytest

from artifact_editor.author import rename as renaming
from artifact_editor.author.tests.test_profile import client


@pytest.fixture
def offline(monkeypatch):
    monkeypatch.setattr(renaming, 'check_jobs', lambda *args: None)
    monkeypatch.setattr(renaming, 'database_change', lambda *args: nullcontext())


def test_rename_moves_assets_and_updates_references_without_rewriting_prose(tmp_path, offline):
    source = tmp_path / 'Old Author'
    chapter = source / 'A Book/chapter/0001'
    chapter.mkdir(parents=True)
    asset = chapter / 'cover.png'
    asset.write_bytes(b'keep-these-bytes')
    data = {'author': 'Old Author', 'AUTHOR': 'Old Author', 'src': str(asset),
            'relative': 'Old Author/A Book/chapter/0001/cover.png',
            'url': '/library/Old%20Author/A%20Book/', 'biography': 'Old Author wrote a book.',
            'birth_name': 'Old Author', 'chapter_key': ['Old Author', 'A Book', 1, 'english']}
    (chapter / 'config.json').write_text(json.dumps(data))
    (chapter / 'book.xml').write_text('<chapter author="Old Author"><p>Old Author wrote this.</p></chapter>')
    (source / 'author.json').write_text(json.dumps({'pen_name': 'Old Author', 'birth_name': 'Original Name'}))
    assert renaming.rename_author(tmp_path, 'Old Author', 'New Author') == 'New Author'
    assert not source.exists()
    destination = tmp_path / 'New Author'
    assert (destination / 'A Book/chapter/0001/cover.png').read_bytes() == b'keep-these-bytes'
    updated = json.loads((destination / 'A Book/chapter/0001/config.json').read_text())
    assert updated['author'] == updated['AUTHOR'] == 'New Author'
    assert updated['src'] == str(destination / 'A Book/chapter/0001/cover.png')
    assert updated['relative'].startswith('New Author/')
    assert updated['url'].startswith('/library/New%20Author/')
    assert updated['chapter_key'][0] == 'New Author'
    assert updated['biography'] == data['biography'] and updated['birth_name'] == 'Old Author'
    assert '<p>Old Author wrote this.</p>' in (destination / 'A Book/chapter/0001/book.xml').read_text()
    assert renaming.resolve_name(tmp_path, 'Old Author') == 'New Author'
    renaming.rename_author(tmp_path, 'New Author', 'Old Author')
    assert source.exists() and renaming.resolve_name(tmp_path, 'New Author') == 'Old Author'


@pytest.mark.parametrize('name', ['', '../escape', '.hidden', 'Theater', 'New/Author', 'a' * 256])
def test_invalid_names_leave_directory_unchanged(tmp_path, offline, name):
    (tmp_path / 'Old').mkdir()
    with pytest.raises(renaming.RenameError):
        renaming.rename_author(tmp_path, 'Old', name)
    assert (tmp_path / 'Old').is_dir()


def test_existing_destination_is_never_overwritten(tmp_path, offline):
    (tmp_path / 'Old').mkdir()
    (tmp_path / 'Existing').mkdir()
    with pytest.raises(renaming.RenameError):
        renaming.rename_author(tmp_path, 'Old', 'Existing')
    assert (tmp_path / 'Old').exists() and (tmp_path / 'Existing').exists()


def test_commit_failure_rolls_back_directory_metadata_and_redirects(tmp_path, offline, monkeypatch):
    source = tmp_path / 'Old'
    source.mkdir()
    original = b'{"author": "Old", "path": "Old/Book/file.png"}'
    (source / 'config.json').write_bytes(original)
    @contextmanager
    def fail(*args):
        yield
        raise RuntimeError('Commit failed')
    monkeypatch.setattr(renaming, 'database_change', fail)
    with pytest.raises(RuntimeError, match='Commit failed'):
        renaming.rename_author(tmp_path, 'Old', 'New')
    assert source.is_dir() and not (tmp_path / 'New').exists()
    assert (source / 'config.json').read_bytes() == original
    assert not (source / 'author.json').exists()
    assert not (tmp_path / '.author-redirects.json').exists()


def test_active_job_blocks_rename_before_any_changes(tmp_path, offline, monkeypatch):
    source = tmp_path / 'Old'
    source.mkdir()
    def busy(*args):
        raise renaming.RenameError('This author has queued work.')
    monkeypatch.setattr(renaming, 'check_jobs', busy)
    with pytest.raises(renaming.RenameError, match='queued work'):
        renaming.rename_author(tmp_path, 'Old', 'New')
    assert source.exists() and not (tmp_path / 'New').exists()


def test_endpoint_and_old_urls_redirect_to_renamed_author(client, tmp_path, offline):
    renaming.register_redirects(client.application)
    response = client.post('/library/An Author/rename', json={'pen_name': 'New Pen Name'})
    assert response.status_code == 200
    assert response.json['author'] == 'New Pen Name'
    assert response.json['url'] == '/library/New%20Pen%20Name/'
    assert (tmp_path / 'New Pen Name/author.json').exists()
    old = client.get('/library/An Author/?test=1')
    assert old.status_code == 308 and old.location == '/library/New%20Pen%20Name/?test=1'
    assert client.get(old.location).status_code == 200
    assert client.patch('/library/An Author/profile', json={'birth_name': 'Original'}).status_code == 308
    assert client.patch('/library/An Author/profile', json={'birth_name': 'Original'}, follow_redirects=True).status_code == 200
    assert json.loads((tmp_path / 'New Pen Name/author.json').read_text())['birth_name'] == 'Original'


def test_redis_guard_detects_nested_author_jobs(monkeypatch, tmp_path):
    import redis
    class Jobs:
        def lrange(self, key, *args):
            return [json.dumps({'kwargs': {'author': 'Old'}})] if key == 'cpu_jobs' else []
        def scan_iter(self, *args): return []
        def get(self, *args): return None
        def hvals(self, *args): return []
        def close(self): pass
    monkeypatch.setattr(redis, 'Redis', lambda **kwargs: Jobs())
    with pytest.raises(renaming.RenameError, match='queued or running'):
        renaming.check_jobs('Old', 'New', tmp_path)
    renaming.check_jobs('Unrelated Author', 'New', tmp_path)


def test_encoded_chapter_keys_update_but_lists_of_names_do_not(tmp_path):
    original = {'key': json.dumps(['Old', 'Book', 1, 'english']),
                'names': ['Old', 'Other', 'Another', 'Fourth']}
    updated = renaming.rewrite(original, 'Old', 'New', tmp_path)
    assert json.loads(updated['key']) == ['New', 'Book', 1, 'english']
    assert updated['names'] == original['names']


def test_rename_fails_closed_when_jobs_cannot_be_checked(monkeypatch, tmp_path):
    import redis
    class Unavailable:
        def lrange(self, *args): raise redis.ConnectionError('Unavailable')
        def close(self): pass
    monkeypatch.setattr(redis, 'Redis', lambda **kwargs: Unavailable())
    with pytest.raises(renaming.RenameError) as result:
        renaming.check_jobs('Old', 'New', tmp_path)
    assert result.value.status == 503


def test_xml_author_and_asset_attributes_update_without_changing_text(tmp_path):
    from bs4 import BeautifulSoup
    text = '<chapter author="Old"><image src="' + str(tmp_path / 'Old/Book/image.png') + '"/><p>Old remains the birth name.</p></chapter>'
    renamed = renaming.rewrite_xml(text, 'Old', "O'Brien & Sons", tmp_path)
    soup = BeautifulSoup(renamed, 'xml')
    assert soup.chapter['author'] == "O'Brien & Sons"
    assert soup.image['src'] == str(tmp_path / "O'Brien & Sons/Book/image.png")
    assert soup.p.text == 'Old remains the birth name.'


def test_shared_pen_name_rename_preserves_individual_names(tmp_path, offline):
    source = tmp_path / 'Shared Name'
    source.mkdir()
    people = [{'id': 'stable-person-id', 'name': 'Individual Name', 'birth_name': 'Shared Name', 'birth_date': '1785-01-04'}]
    (source / 'author.json').write_text(json.dumps({'pen_name': 'Shared Name', 'identity_type': 'collective', 'contributors': people}))
    renaming.rename_author(tmp_path, 'Shared Name', 'New Shared Name')
    profile = json.loads((tmp_path / 'New Shared Name/author.json').read_text())
    assert profile['contributors'] == people
    assert profile['pen_name'] == 'New Shared Name'
