"""Author profiles and bibliography stored alongside the library's book configs."""
import json
import os
import tempfile
from pathlib import Path
from datetime import date
from urllib.parse import urlsplit


BIO_FIELDS = ('identity_type', 'pen_name', 'birth_name', 'pen_name_pronunciation', 'birth_name_pronunciation',
              'birth_country', 'birth_date', 'death_date', 'biography',
              'portrait_url', 'portrait_source', 'portrait_credit')


def profile_data(directory):
    profile = read_json(Path(directory) / 'author.json')
    # Keep existing names and year-only information without inventing a day.
    profile.setdefault('birth_name', profile.get('full_name', ''))
    profile.setdefault('contributors', [])
    profile.setdefault('identity_type', 'collective' if profile['contributors'] else 'individual')
    return profile


def full_date(value):
    value = (value or '').strip()
    if value:
        try:
            parsed = date.fromisoformat(value)
            if parsed.isoformat() != value:
                raise ValueError()
        except ValueError as error:
            raise ValueError('Dates must be valid dates in yyyy-mm-dd format, or left blank.') from error
    return value


def safe_name(value):
    value = (value or '').strip()
    if not value or value in ('.', '..', '__pycache__') or value.startswith('.') or any(c in value for c in '/\\\0'):
        raise ValueError('Enter a name without path separators or a leading dot.')
    return value


def year(value):
    value = (value or '').strip()
    if value and (not value.isdigit() or not 1 <= int(value) <= 9999):
        raise ValueError('Years must be between 1 and 9999, or left blank.')
    return str(int(value)) if value else ''


def web_url(value):
    value = (value or '').strip()
    if value and (urlsplit(value).scheme not in ('http', 'https') or not urlsplit(value).netloc):
        raise ValueError('Image and source links must be HTTP or HTTPS URLs.')
    return value


def read_json(path):
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def write_json(path, data):
    # Readers should never see a partially written profile or config.
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False) as handle:
        temporary = handle.name
        json.dump(data, handle, indent=2, ensure_ascii=False)
    try:
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def bibliography(directory):
    from artifact_editor.library.storage import is_shelf_link
    entries = []
    for path in Path(directory).iterdir():
        if (not path.is_dir() or path.name.startswith('.') or path.name == '__pycache__'
                or (path.is_symlink() and not is_shelf_link(path, Path(directory).name, path.name))):
            continue
        config = read_json(path / 'config.json')
        publication_year = str(config.get('publication_year') or '')
        entries.append(dict(title=path.name, publication_year=publication_year,
                            source_url=config.get('bibliography_source', ''),
                            series=config.get('series', '').strip(), series_source=config.get('series_source', ''),
                            publication_date=config.get('publication_date', ''), processed=has_chapters(path),
                            copyright_status=config.get('copyright_status', ''),
                            copyright_source=config.get('copyright_source', '')))
    return sorted(entries, key=publication_key)


def has_chapters(book_directory):
    """Use the same chapter-directory inventory as the book's table of contents."""
    chapters = Path(book_directory) / 'chapter'
    return chapters.is_dir() and any(
        path.is_dir() and not path.is_symlink() and path.name.split('_')[0].isdigit()
        for path in chapters.iterdir()
    )


def publication_key(book):
    published = book.get('publication_date', '')
    known_year = str(book.get('publication_year') or '')
    return (published or (known_year.zfill(4) + '-00-00' if known_year.isdigit() else '99999'),
            book['title'].casefold())


def bibliography_groups(books):
    groups = {}
    for book in books:
        name = book.get('series', '').strip()
        key = name.casefold()
        group = groups.setdefault(key, dict(name=name or 'No series recorded', key=key, books=[]))
        group['books'].append(book)
    for group in groups.values():
        group['books'].sort(key=publication_key)
        group['processed_count'] = sum(bool(book.get('processed')) for book in group['books'])
    return [groups[key] for key in sorted(groups, key=lambda key: (not bool(key), key))]
