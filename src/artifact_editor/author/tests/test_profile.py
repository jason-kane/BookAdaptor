import json

from jinja2 import ChoiceLoader, DictLoader

import pytest
from flask import Flask, Blueprint

from artifact_editor.author.views import bp
from artifact_editor.author.profile import bibliography
import const


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(const, 'LIBRARY_DIR', str(tmp_path))
    (tmp_path / 'An Author').mkdir()
    app = Flask(__name__)
    library = Blueprint('library', __name__)
    library.add_url_rule('/', endpoint='library', view_func=lambda: '')
    library.add_url_rule('/<author>/<title>/cover.png', endpoint='book_cover', view_func=lambda **kw: '')
    book = Blueprint('book', __name__)
    book.add_url_rule('/<author>/<title>/', endpoint='table_of_contents', view_func=lambda **kw: '')
    library.register_blueprint(book)
    library.register_blueprint(bp, url_prefix='/<author>')
    app.register_blueprint(library, url_prefix='/library')
    app.jinja_loader = ChoiceLoader([app.jinja_loader, DictLoader({
        'base.html': '{% block title %}{% endblock %}{% block body %}{% endblock %}',
    })])
    app.config['TESTING'] = True
    return app.test_client()


def test_profile_round_trip(client, tmp_path, monkeypatch):
    monkeypatch.setattr('artifact_editor.author.views.download_portrait', lambda *args: 'a' * 64)
    response = client.post('/library/An Author/profile', data={
        'full_name': 'A & B <Writer>', 'birth_year': '1856', 'death_year': '1919',
        'birth_country': 'United States', 'biography': 'A life in words.',
        'portrait_url': 'https://example.org/portrait.jpg',
        'portrait_source': 'https://example.org/source', 'portrait_credit': 'A painter',
    })
    assert response.status_code == 303
    page = client.get(response.location)
    assert page.status_code == 200
    assert b'A &amp; B &lt;Writer&gt;' in page.data
    assert b'A life in words.' in page.data
    assert json.loads((tmp_path / 'An Author/author.json').read_text())['birth_year'] == '1856'


def test_bibliography_create_edit_order_and_preserve_config(client, tmp_path):
    for title, published in [('Later', '1900'), ('Undated', ''), ('Earlier', '1850')]:
        assert client.post('/library/An Author/bibliography', data={
            'book_name': title, 'publication_year': published, 'create': '1',
        }).status_code == 303
    directory = tmp_path / 'An Author'
    assert [entry['title'] for entry in bibliography(directory)] == ['Earlier', 'Later', 'Undated']
    config = directory / 'Later/config.json'
    config.write_text(json.dumps({'subtitle': 'Keep me', 'publication_year': '1900'}))
    assert client.post('/library/An Author/bibliography', data={
        'book_name': 'Later', 'publication_year': '1800',
    }).status_code == 303
    assert json.loads(config.read_text())['subtitle'] == 'Keep me'
    assert client.post('/library/An Author/bibliography', data={
        'book_name': 'Later', 'create': '1',
    }).status_code == 409
    assert json.loads(config.read_text())['publication_year'] == '1800'
    (directory / 'unrelated.txt').write_text('not a book')
    assert len(bibliography(directory)) == 3
    assert client.get('/library/An Author/').status_code == 200


@pytest.mark.parametrize('data', [
    {'birth_year': 'word'}, {'birth_year': '2000', 'death_year': '1900'},
    {'portrait_url': 'javascript:alert(1)'},
    {'portrait_url': 'https://example.org/portrait.jpg'},
])
def test_invalid_profile_is_not_saved(client, tmp_path, data):
    assert client.post('/library/An Author/profile', data=data).status_code == 400
    assert not (tmp_path / 'An Author/author.json').exists()


@pytest.mark.parametrize('title', ['../escape', '/absolute', '.', '__pycache__', ''])
def test_invalid_book_names(client, title):
    assert client.post('/library/An Author/bibliography', data={'book_name': title}).status_code == 400


def test_missing_author(client):
    assert client.get('/library/Missing/').status_code == 404


def test_research_returns_suggestions_without_writing(client, tmp_path, monkeypatch):
    from artifact_editor.author import views
    directory = tmp_path / 'An Author'
    (directory / 'A Book').mkdir()
    original = {'full_name': 'Keep my name', 'birth_country': 'My country'}
    (directory / 'author.json').write_text(json.dumps(original))
    def lookup(name, titles=()):
        assert name == 'An Author'
        assert titles == ()
        return {'profile': {'birth_year': '1856'}, 'books': {}, 'sources': [], 'warnings': []}
    monkeypatch.setattr(views, 'research_author', lookup)
    response = client.post('/library/An Author/research', json={'name': 'Specific Author'})
    assert response.status_code == 200
    assert response.json['profile']['birth_year'] == '1856'
    assert json.loads((directory / 'author.json').read_text()) == original
    assert not (directory / 'A Book/config.json').exists()


def test_research_failure_is_actionable(client, monkeypatch):
    from artifact_editor.author import views
    def lookup(*args):
        raise views.ResearchError('Source unavailable. Try again.')
    monkeypatch.setattr(views, 'research_author', lookup)
    response = client.post('/library/An Author/research', json={})
    assert response.status_code == 502
    assert 'Try again' in response.json['error']


def test_save_research_attribution(client, tmp_path):
    sources = ['https://en.wikipedia.org/wiki/An_Author', 'https://www.wikidata.org/wiki/Q1']
    response = client.post('/library/An Author/profile', data={
        'full_name': 'An Author', 'research_sources': json.dumps(sources),
    })
    assert response.status_code == 303
    assert json.loads((tmp_path / 'An Author/author.json').read_text())['research_sources'] == sources
    assert b'CC BY-SA' in client.get(response.location).data


def test_new_fields_and_exact_dates_round_trip(client, tmp_path):
    response = client.post('/library/An Author/profile', data={
        'pen_name': 'L. Frank Baum', 'birth_name': 'Lyman Frank Baum',
        'pen_name_pronunciation': '/ɛl fræŋk bɔːm/',
        'birth_name_pronunciation': '/ˈlaɪmən fræŋk bɔːm/',
        'birth_date': '1856-05-15', 'death_date': '1919-05-06',
    })
    assert response.status_code == 303
    saved = json.loads((tmp_path / 'An Author/author.json').read_text())
    assert saved['birth_name'] == 'Lyman Frank Baum'
    assert saved['birth_date'] == '1856-05-15'
    assert saved['death_date'] == '1919-05-06'
    assert saved['birth_name_pronunciation'] == '/ˈlaɪmən fræŋk bɔːm/'
    page = client.get(response.location).data
    assert b'Birth name' in page and b'Full name' not in page
    assert b'1856-05-15' in page and b'1919-05-06' in page
    assert b'<h1>An Author</h1>' in page
    assert saved['pen_name'] == 'An Author'


@pytest.mark.parametrize('birth,death', [('1856-02-30', ''), ('1856', ''), ('2020-01-01', '1919-05-06'), ('18560515', '')])
def test_invalid_full_dates_rejected(client, birth, death):
    assert client.post('/library/An Author/profile', data={'birth_date': birth, 'death_date': death}).status_code == 400


def test_old_profile_keeps_name_and_year_without_inventing_day(client, tmp_path):
    (tmp_path / 'An Author/author.json').write_text(json.dumps({'full_name': 'Birth Name', 'birth_year': '1856'}))
    page = client.get('/library/An Author/').data
    assert b'value="Birth Name"' in page
    assert b'Previously recorded year: 1856' in page
    assert b'1856-01-01' not in page


def test_portrait_route_uses_local_cached_file(client, tmp_path, monkeypatch):
    folder = tmp_path / 'An Author/.author-media'
    folder.mkdir()
    record = 'a' * 64
    (folder / f'{record}-thumb.jpg').write_bytes(b'local-thumbnail')
    monkeypatch.setattr('artifact_editor.author.views.download_portrait', lambda *args: record)
    response = client.post('/library/An Author/portrait', json={'url': 'https://example.org/image.jpg'})
    assert response.status_code == 200
    assert response.json['thumbnail_url'].startswith('/library/An%20Author/portrait/')
    thumbnail = client.get(response.json['thumbnail_url'])
    assert thumbnail.status_code == 200
    assert thumbnail.data == b'local-thumbnail'
    assert thumbnail.mimetype == 'image/jpeg'
    assert client.get('/library/An Author/portrait/not-a-hash/thumb.jpg').status_code == 404


def test_pen_name_changes_require_the_directory_rename_endpoint(client, tmp_path):
    response = client.post('/library/An Author/profile', data={
        'pen_name': 'A different author', 'pen_name_pronunciation': '/a pronunciation/',
    })
    assert response.status_code == 303
    saved = json.loads((tmp_path / 'An Author/author.json').read_text())
    assert saved['pen_name'] == 'An Author'
    assert saved['pen_name_pronunciation'] == '/a pronunciation/'
    page = client.get(response.location).data
    assert b'name="pen_name" value="An Author" required' in page
    assert b'data-rename-url=' in page


def test_autosave_updates_only_changed_field_and_can_clear(client, tmp_path):
    path = tmp_path / 'An Author/author.json'
    path.write_text(json.dumps({'birth_name': 'Existing Name', 'birth_country': 'Existing Country',
                                'birth_date': '1856-05-15', 'birth_year': '1856'}))
    response = client.patch('/library/An Author/profile', json={'birth_name_pronunciation': '/test/'})
    assert response.status_code == 200 and response.json['saved']
    saved = json.loads(path.read_text())
    assert saved['birth_country'] == 'Existing Country'
    assert saved['birth_name'] == 'Existing Name'
    assert saved['birth_name_pronunciation'] == '/test/'
    assert client.patch('/library/An Author/profile', json={'birth_date': ''}).status_code == 200
    saved = json.loads(path.read_text())
    assert saved['birth_date'] == '' and saved['birth_year'] == ''


def test_autosave_validation_keeps_saved_data(client, tmp_path):
    path = tmp_path / 'An Author/author.json'
    original = {'birth_date': '1856-05-15', 'death_date': '1919-05-06'}
    path.write_text(json.dumps(original))
    for changes in ({'birth_date': '2020-01-01'}, {'birth_date': 'bad'}, {'pen_name': 'Changed'}, {'unknown': 'value'}):
        response = client.patch('/library/An Author/profile', json=changes)
        assert response.status_code == 400 and response.json['error']
        assert json.loads(path.read_text()) == original


def test_portrait_fields_can_self_save_independently(client, tmp_path, monkeypatch):
    monkeypatch.setattr('artifact_editor.author.views.download_portrait', lambda *args: pytest.fail('Metadata save must not block on image download'))
    assert client.patch('/library/An Author/profile', json={'portrait_url': 'https://example.org/image.jpg'}).status_code == 200
    assert client.patch('/library/An Author/profile', json={'portrait_source': 'https://example.org/source'}).status_code == 200
    assert client.patch('/library/An Author/profile', json={'birth_country': 'United States'}).status_code == 200
    saved = json.loads((tmp_path / 'An Author/author.json').read_text())
    assert saved['portrait_url'] == 'https://example.org/image.jpg'
    assert saved['portrait_source'] == 'https://example.org/source'


def test_bibliography_autosave_preserves_other_fields(client, tmp_path):
    folder = tmp_path / 'An Author/A Book'
    folder.mkdir()
    path = folder / 'config.json'
    path.write_text(json.dumps({'subtitle': 'Keep', 'publication_year': '1900', 'bibliography_source': 'https://example.org/old'}))
    response = client.patch('/library/An Author/bibliography', json={'book_name': 'A Book', 'publication_year': '1904'})
    assert response.status_code == 200 and response.json['saved']
    saved = json.loads(path.read_text())
    assert saved == {'subtitle': 'Keep', 'publication_year': '1904', 'bibliography_source': 'https://example.org/old'}
    assert client.patch('/library/An Author/bibliography', json={'book_name': 'A Book', 'publication_year': 'bad'}).status_code == 400
    page = client.get('/library/An Author/').data
    assert b'Save biography' not in page and b'Save entry' not in page


def test_copyright_status_autosaves_and_displays_with_source(client, tmp_path):
    folder = tmp_path / 'An Author/A Book'
    folder.mkdir()
    path = folder / 'config.json'
    path.write_text(json.dumps({'publication_year': '1904', 'subtitle': 'Keep me'}))
    assert b'Copyright: <span class="copyright-status">Unknown</span>' in client.get('/library/An Author/').data
    response = client.patch('/library/An Author/bibliography', json={
        'book_name': 'A Book', 'copyright_status': 'Public domain — United States',
        'copyright_source': 'https://www.wikidata.org/wiki/Q1659447#P6216',
    })
    assert response.status_code == 200
    saved = json.loads(path.read_text())
    assert saved['copyright_status'] == 'Public domain — United States'
    assert saved['publication_year'] == '1904' and saved['subtitle'] == 'Keep me'
    page = client.get('/library/An Author/').data.decode()
    assert 'Public domain — United States' in page and 'Q1659447#P6216' in page
    assert client.patch('/library/An Author/bibliography', json={
        'book_name': 'A Book', 'copyright_source': 'javascript:alert(1)',
    }).status_code == 400
    assert json.loads(path.read_text()) == saved
    assert client.patch('/library/An Author/bibliography', json={
        'book_name': 'A Book', 'copyright_status': '',
    }).status_code == 200
    assert b'Copyright: <span class="copyright-status">Unknown</span>' in client.get('/library/An Author/').data


def test_bibliography_research_adds_books_and_returns_refreshed_cards(client, tmp_path, monkeypatch):
    from artifact_editor.author import views
    monkeypatch.setattr(views, 'research_bibliography', lambda name: {'books': [{
        'title': 'Discovered Book', 'wikidata_id': 'Q4', 'publication_year': '1904',
        'source_url': 'https://www.wikidata.org/wiki/Q4', 'aliases': [],
    }], 'warnings': []})
    first = client.post('/library/An Author/bibliography/research')
    assert first.status_code == 200
    assert first.json['added'] == 1 and first.json['total'] == 1
    assert 'Discovered Book' in first.json['html'] and 'bibliography-edit' in first.json['html']
    assert (tmp_path / 'An Author/Discovered Book/config.json').is_file()
    second = client.post('/library/An Author/bibliography/research')
    assert second.json['added'] == 0 and second.json['unchanged'] == 1
    assert not (tmp_path / 'An Author/author.json').exists()


def test_bibliography_research_failure_creates_nothing(client, tmp_path, monkeypatch):
    from artifact_editor.author import views
    def fail(name):
        raise views.ResearchError('Source unavailable.')
    monkeypatch.setattr(views, 'research_bibliography', fail)
    response = client.post('/library/An Author/bibliography/research')
    assert response.status_code == 502 and response.json['error']
    assert not list((tmp_path / 'An Author').iterdir())


def test_series_groups_and_publication_date_order(client, tmp_path):
    author = tmp_path / 'An Author'
    for title, settings in [
        ('Later', {'series': 'Oz', 'publication_year': '1904', 'publication_date': '1904-07-05'}),
        ('Earlier', {'series': 'Oz', 'publication_year': '1900'}),
        ('Same Year Earlier', {'series': 'oz', 'publication_year': '1904', 'publication_date': '1904-02-01'}),
        ('Undated', {'series': 'Oz'}), ('Standalone', {'publication_year': '1890'}),
    ]:
        folder = author / title
        folder.mkdir()
        (folder / 'config.json').write_text(json.dumps(settings))
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(client.get('/library/An Author/').data, 'html.parser')
    groups = soup.select('.bibliography-series')
    assert [group['data-series-key'] for group in groups] == ['oz', '']
    assert [card['data-title'] for card in groups[0].select('.author-book')] == ['Earlier', 'Same Year Earlier', 'Later', 'Undated']
    assert 'No series recorded' in groups[1].get_text()
    assert client.patch('/library/An Author/bibliography', json={'book_name': 'Standalone', 'series': 'Oz'}).status_code == 200
    soup = BeautifulSoup(client.get('/library/An Author/').data, 'html.parser')
    assert len(soup.select('.bibliography-series')) == 1
    assert soup.select_one('.author-book')['data-title'] == 'Standalone'


def test_series_and_publication_date_autosave_preserve_metadata(client, tmp_path):
    folder = tmp_path / 'An Author/A Book'
    folder.mkdir()
    path = folder / 'config.json'
    path.write_text(json.dumps({'subtitle': 'Keep', 'publication_year': '1904'}))
    response = client.patch('/library/An Author/bibliography', json={
        'book_name': 'A Book', 'series': 'Oz books', 'series_source': 'https://www.wikidata.org/wiki/Q4#P179',
        'publication_date': '1904-07-05',
    })
    assert response.status_code == 200
    assert response.json['book'] == {'series': 'Oz books', 'publication_year': '1904', 'publication_date': '1904-07-05'}
    assert json.loads(path.read_text())['subtitle'] == 'Keep'
    assert client.patch('/library/An Author/bibliography', json={'book_name': 'A Book', 'publication_date': 'bad'}).status_code == 400
    response = client.patch('/library/An Author/bibliography', json={'book_name': 'A Book', 'publication_year': '1905'})
    assert response.json['book']['publication_date'] == ''
    assert client.patch('/library/An Author/bibliography', json={'book_name': 'A Book', 'series': ''}).status_code == 200
    assert json.loads(path.read_text())['series'] == ''


def test_series_without_processed_books_start_collapsed(client, tmp_path):
    author = tmp_path / 'An Author'
    for title, series in [('Imported', 'Active Series'), ('Catalogue Only', 'Empty Series'), ('No Series', '')]:
        folder = author / title
        folder.mkdir()
        (folder / 'config.json').write_text(json.dumps({'series': series}))
    (author / 'Imported/chapter/0001').mkdir(parents=True)
    # Metadata/cache directories are not evidence of processing a book.
    (author / 'Catalogue Only/chapter/.cache').mkdir(parents=True)
    (author / 'Catalogue Only/chapter/notes').mkdir()
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(client.get('/library/An Author/').data, 'html.parser')
    groups = {group['data-series-key']: group for group in soup.select('.bibliography-series')}
    assert all(group.name == 'details' and group.find('summary', recursive=False) for group in groups.values())
    assert groups['active series'].has_attr('open')
    assert not groups['empty series'].has_attr('open')
    assert not groups[''].has_attr('open')
    assert groups['active series'].select_one('.author-book')['data-processed'] == 'true'
    assert groups['empty series'].select_one('.author-book')['data-processed'] == 'false'
