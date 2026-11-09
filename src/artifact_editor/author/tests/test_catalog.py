import json

from artifact_editor.author.catalog import merge_discoveries
from artifact_editor.author.research import _discover_bibliography, ResearchError
from artifact_editor.author.tests.test_research import claim, date, entity
import pytest


def discovery(title='A New Book', identity='Q4', **extra):
    return dict(title=title, wikidata_id=identity, aliases=[], publication_year='1904',
                source_url='https://www.wikidata.org/wiki/' + identity, **extra)


def test_add_is_idempotent_and_preserves_existing_metadata(tmp_path):
    folder = tmp_path / 'Existing Book'
    folder.mkdir()
    (folder / 'config.json').write_text(json.dumps({'subtitle': 'Keep me', 'publication_year': '1900'}))
    found = [discovery(), discovery('Existing Book', 'Q6')]
    assert merge_discoveries(tmp_path, found) == dict(added=1, updated=1, unchanged=0, skipped=0)
    saved = json.loads((folder / 'config.json').read_text())
    assert saved['publication_year'] == '1900' and saved['subtitle'] == 'Keep me'
    assert saved['wikidata_id'] == 'Q6'
    assert merge_discoveries(tmp_path, found) == dict(added=0, updated=0, unchanged=2, skipped=0)
    assert not (tmp_path / 'A New Book/chapter').exists()


def test_alias_and_existing_source_id_prevent_duplicate_books(tmp_path):
    folder = tmp_path / 'Original: Book!'
    folder.mkdir()
    (folder / 'config.json').write_text(json.dumps({'bibliography_source': 'https://www.wikidata.org/wiki/Q4'}))
    alias = discovery('Canonical Title', 'Q6')
    alias['aliases'] = ['Original Book']
    assert merge_discoveries(tmp_path, [discovery('A Renamed Title')])['added'] == 0
    # A distinct known work sharing a title must not contaminate the original.
    assert merge_discoveries(tmp_path, [alias])['skipped'] == 1
    assert len([p for p in tmp_path.iterdir() if p.is_dir()]) == 1


def test_unsafe_titles_and_hidden_metadata_are_not_books(tmp_path):
    (tmp_path / '.author-media').mkdir()
    counts = merge_discoveries(tmp_path, [discovery('../escape'), discovery('.hidden'), discovery()])
    assert counts['added'] == 1 and counts['skipped'] == 2
    assert not (tmp_path.parent / 'escape').exists()


class Catalogue:
    def get(self, url, **params):
        if '/summary/' in url:
            return {'wikibase_item': 'Q1', 'title': 'Author'}
        if 'sparql' in url:
            assert 'wdt:P50 wd:Q1' in params['query']
            return {'results': {'bindings': [
                {'book': {'value': 'http://www.wikidata.org/entity/Q4'}, 'title': {'value': 'A Book'}},
                {'book': {'value': 'http://www.wikidata.org/entity/Q6'}, 'title': {'value': 'Wrong Author'}},
            ]}}
        return {'entities': {
            'Q4': {**entity(P50=[{'id': 'Q1'}], P577=[date('1904')]),
                   'aliases': {'en': [{'value': 'An Alternate Title'}]}},
            'Q6': entity(P50=[{'id': 'Q999'}]),
        }}

    def entities(self, ids):
        return {}


def test_source_discovery_checks_author_and_includes_aliases():
    found = _discover_bibliography(Catalogue(), 'Author')
    assert len(found['books']) == 1
    assert found['books'][0]['wikidata_id'] == 'Q4'
    assert found['books'][0]['publication_year'] == '1904'
    assert found['books'][0]['aliases'] == ['An Alternate Title']


def test_ambiguous_author_does_not_discover_books():
    api = Catalogue()
    api.get = lambda *args, **kwargs: {'type': 'disambiguation', 'wikibase_item': 'Q1'}
    with pytest.raises(ResearchError):
        _discover_bibliography(api, 'Ambiguous')


def test_alias_matches_existing_book_without_source_identity(tmp_path):
    folder = tmp_path / 'Alternate Book Title'
    folder.mkdir()
    (folder / 'config.json').write_text(json.dumps({'subtitle': 'Keep'}))
    found = discovery('A Canonical Book Title')
    found['aliases'] = ['Alternate book title']
    assert merge_discoveries(tmp_path, [found])['updated'] == 1
    assert not (tmp_path / 'A Canonical Book Title').exists()
    assert json.loads((folder / 'config.json').read_text())['subtitle'] == 'Keep'


def test_research_series_does_not_replace_manual_assignment(tmp_path):
    folder = tmp_path / 'A New Book'
    folder.mkdir()
    path = folder / 'config.json'
    path.write_text(json.dumps({'series': 'My series', 'publication_year': '1900'}))
    found = discovery(series='Source series', series_source='https://www.wikidata.org/wiki/Q4#P179', publication_date='1904-07-05')
    merge_discoveries(tmp_path, [found])
    saved = json.loads(path.read_text())
    assert saved['series'] == 'My series'
    assert not saved.get('series_source') and not saved.get('publication_date')
