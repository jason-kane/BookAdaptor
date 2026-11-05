"""Library navigation and media use disposable files, never installed books."""
import pytest
from bs4 import BeautifulSoup


def test_library_inventory_excludes_internal_and_hidden_directories(library_ui):
    ui = library_ui
    for name in ('Z Author', 'A Author', '.hidden', 'home', 'active', 'lost+found', 'global_characters', 'Theater'):
        (ui.root / name).mkdir()
    (ui.root / 'notes.txt').touch()
    response = ui.client.get('/library/')
    assert response.status_code == 200
    assert [c.args[0] for c in ui.scope['Author'].call_args_list] == ['A Author', 'Z Author']
    assert [n.text for n in BeautifulSoup(response.data, 'html.parser').select('h2')] == ['A Author', 'Z Author']


def test_book_cards_navigate_to_book_and_request_cover(library_ui):
    from types import SimpleNamespace
    author = SimpleNamespace(name='Writer', pretty_name='Writer', books=[SimpleNamespace(title='A Book')], inventory_books=lambda: None)
    library_ui.scope['Author'].return_value = author
    library_ui.scope['Author'].side_effect = None
    (library_ui.root / 'Writer').mkdir()
    response = library_ui.client.get('/library/')
    soup = BeautifulSoup(response.data, 'html.parser')
    assert soup.select_one('.card-link')['href'] == '/library.book.table_of_contents?author=Writer&title=A Book'
    assert soup.select_one('img')['src'] == '/library.book_cover?author=Writer&title=A Book'


def test_add_author_persists_and_rejects_duplicate_or_missing_name(library_ui):
    client = library_ui.client
    assert client.post('/library/author/add').status_code == 400
    assert client.post('/library/author/add', data={'author_name': 'New Writer'}).status_code == 200
    assert (library_ui.root / 'New Writer').is_dir()
    assert client.post('/library/author/add', data={'author_name': 'New Writer'}).status_code == 400


@pytest.mark.parametrize('fallback', [False, True])
def test_book_cover_serves_existing_or_copies_first_chapter_cover(library_ui, fallback):
    folder = library_ui.root / 'Writer/Book'
    source = folder / ('chapter/0001/cover.png' if fallback else 'cover.png')
    source.parent.mkdir(parents=True)
    source.write_bytes(b'cover bytes')
    response = library_ui.client.get('/library/Writer/Book/cover.png')
    assert response.status_code == 200 and response.data == b'cover bytes'
    assert (folder / 'cover.png').read_bytes() == b'cover bytes'


@pytest.mark.parametrize('fallback', [False, True])
def test_chapter_cover_serves_existing_or_first_paragraph_image(library_ui, fallback):
    folder = library_ui.root / 'Writer/Book/chapter/0001'
    source = folder / ('paragraphs/000000/img-1.png' if fallback else 'cover.png')
    source.parent.mkdir(parents=True)
    source.write_bytes(b'chapter cover')
    response = library_ui.client.get('/library/Writer/Book/0001/cover.png')
    assert response.status_code == 200 and response.data == b'chapter cover'
    assert (folder / 'cover.png').read_bytes() == b'chapter cover'


def test_missing_chapter_cover_directory_returns_404(library_ui):
    library_ui.scope['tools'].get_chapterdir = lambda *args: None
    assert library_ui.client.get('/library/Writer/Book/1/cover.png').status_code == 404


def test_sound_preview_serves_audio_and_reports_missing_file(library_ui):
    sounds = library_ui.root / 'sounds'
    sounds.mkdir()
    (sounds / 'voice.wav').write_bytes(b'RIFF')
    assert library_ui.client.get('/library/sounds/voice.wav').data == b'RIFF'
    assert library_ui.client.get('/library/sounds/missing.wav').status_code == 404
