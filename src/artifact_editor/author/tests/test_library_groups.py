import json
from pathlib import Path

import pytest
from bs4 import BeautifulSoup
from flask import Blueprint, Flask
from jinja2 import ChoiceLoader, DictLoader

import const


@pytest.mark.parametrize('route', ['/library/', '/library/textoviva'])
def test_library_groups_authors_series_dates_and_hides_unprocessed(tmp_path, monkeypatch, route):
    monkeypatch.setattr(const, 'LIBRARY_DIR', str(tmp_path))
    def book(author, title, settings, processed=False):
        folder = tmp_path / author / title
        folder.mkdir(parents=True)
        (folder / 'config.json').write_text(json.dumps(settings))
        if processed:
            (folder / 'chapter/0001').mkdir(parents=True)
    book('B Author', 'Later', {'series': 'A Series', 'publication_year': '1904'}, True)
    book('B Author', 'Earlier', {'series': 'A Series', 'publication_year': '1900'})
    book('B Author', 'Known Day Later', {'series': 'A Series', 'publication_date': '1904-08-01', 'publication_year': '1904'})
    book('B Author', 'Known Day Earlier', {'series': 'A Series', 'publication_date': '1904-02-01', 'publication_year': '1904'})
    book('B Author', 'Undated', {'series': 'A Series'})
    book('B Author', 'Catalogue Only', {'series': 'B Series'})
    book('B Author', 'Standalone', {})
    book('A Author', 'Other Work', {}, True)
    book('C Author', 'Unstarted', {})
    (tmp_path / 'B Author/.author-media').mkdir()
    app = Flask(__name__)
    author = Blueprint('author', __name__)
    author.add_url_rule('/<author>/', endpoint='author_base', view_func=lambda **kw: '')
    books = Blueprint('book', __name__)
    books.add_url_rule('/<author>/<title>/', endpoint='table_of_contents', view_func=lambda **kw: '')
    # Build a fresh parent blueprint so repeated test registrations stay isolated.
    library = Blueprint('library', __name__, template_folder=str(Path(__file__).parents[2] / 'library/templates'))
    library.register_blueprint(author)
    library.register_blueprint(books)
    from artifact_editor.library import views
    library.add_url_rule('/', view_func=views.library)
    library.add_url_rule('/textoviva', view_func=views.textoviva)
    library.add_url_rule('/<author>/<title>/cover.png', endpoint='book_cover', view_func=lambda **kw: '')
    app.register_blueprint(library, url_prefix='/library')
    app.jinja_loader = ChoiceLoader([app.jinja_loader, DictLoader({
        'base.html': '{% block title %}{% endblock %}{% block styles %}{% endblock %}{% block navbar_center %}{% endblock %}{% block body %}{% endblock %}',
    })])
    response = app.test_client().get(route)
    assert response.status_code == 200
    soup = BeautifulSoup(response.data, 'html.parser')
    authors = soup.select('.library-author')
    assert [row['data-author'] for row in authors] == ['A Author', 'B Author', 'C Author']
    assert not authors[0].has_attr('hidden') and not authors[1].has_attr('hidden')
    assert authors[2].has_attr('hidden')
    assert soup.select_one('h1') is None
    groups = authors[1].select('.library-series')
    assert [row['data-series-key'] for row in groups] == ['a series', 'b series', '']
    assert groups[0].has_attr('open') and not groups[1].has_attr('open') and not groups[2].has_attr('open')
    assert [card['data-title'] for card in groups[0].select('.library-book')] == [
        'Earlier', 'Later', 'Known Day Earlier', 'Known Day Later', 'Undated',
    ]
    for card in soup.select('.library-book'):
        assert card.has_attr('hidden') == (card['data-processed'] == 'false')
    assert not soup.select_one('#show-unprocessed-books').has_attr('checked')
    assert '.author-media' not in response.data.decode()
    assert '1/7 processed' in authors[1].get_text()
