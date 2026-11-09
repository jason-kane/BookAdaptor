"""Merge sourced bibliography discoveries into the existing book directories."""
import fcntl
import re
import unicodedata
from pathlib import Path

from .profile import read_json, write_json, safe_name, year, web_url, full_date


def title_key(title):
    return ''.join(c for c in unicodedata.normalize('NFKC', title).casefold() if c.isalnum())


def merge_discoveries(directory, discoveries):
    from artifact_editor.library.storage import is_shelf_link
    directory = Path(directory)
    counts = dict(added=0, updated=0, unchanged=0, skipped=0)
    # Multiple browser tabs must not create duplicate directories concurrently.
    with (directory / '.bibliography.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        titles, identities = {}, {}
        for path in directory.iterdir():
            if (path.name.startswith('.') or path.name == '__pycache__' or not path.is_dir()
                    or (path.is_symlink() and not is_shelf_link(path, directory.name, path.name))):
                continue
            config = read_json(path / 'config.json')
            titles[title_key(path.name)] = path
            identity = config.get('wikidata_id')
            if not identity:
                match = re.search(r'/wiki/(Q[1-9][0-9]*)(?:#.*)?$', config.get('bibliography_source', ''))
                identity = match.group(1) if match else None
            if identity:
                identities[identity] = path
        for found in discoveries:
            try:
                title = safe_name(found.get('title'))
                identity = found.get('wikidata_id', '')
                if not re.fullmatch(r'Q[1-9][0-9]*', identity):
                    raise ValueError('Invalid source identity')
                metadata = dict(wikidata_id=identity, publication_year=year(found.get('publication_year')),
                                bibliography_source=web_url(found.get('source_url')),
                                copyright_status=found.get('copyright_status', '').strip(),
                                copyright_source=web_url(found.get('copyright_source')),
                                series=found.get('series', '').strip(), series_source=web_url(found.get('series_source')),
                                publication_date=full_date(found.get('publication_date')))
                if len(metadata['series']) > 250:
                    raise ValueError('Series name too long')
                if len(metadata['copyright_status']) > 2000:
                    raise ValueError('Copyright description too long')
            except (ValueError, TypeError, AttributeError):
                counts['skipped'] += 1
                continue
            aliases = [title, *found.get('aliases', [])]
            path = identities.get(identity) or next((titles[title_key(alias)] for alias in aliases if title_key(alias) in titles), None)
            created = path is None
            if created:
                path = directory / title
                if path.exists():
                    counts['skipped'] += 1
                    continue
                path.mkdir()
            config = read_json(path / 'config.json')
            if config.get('wikidata_id') and config['wikidata_id'] != identity:
                counts['skipped'] += 1
                continue
            if config.get('publication_year') and metadata['publication_date'] and str(config['publication_year']) != metadata['publication_date'][:4]:
                metadata['publication_date'] = ''
            if config.get('series') and config['series'] != metadata['series']:
                metadata['series_source'] = ''
            changes = {key: value for key, value in metadata.items() if value and not config.get(key)}
            if changes or created:
                config.update(changes)
                write_json(path / 'config.json', config)
            counts['added' if created else 'updated' if changes else 'unchanged'] += 1
            identities[identity] = path
            for alias in aliases:
                titles[title_key(alias)] = path
    return counts
