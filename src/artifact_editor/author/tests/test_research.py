import pytest

from artifact_editor.author.research import _research, ResearchError, date_year


def claim(value, rank='normal'):
    return {'rank': rank, 'mainsnak': {'snaktype': 'value', 'datavalue': {'value': value}}}


def entity(**props):
    return {'claims': {key: [claim(v) for v in values] for key, values in props.items()}}


def date(value):
    return {'time': f'+{value}-01-01T00:00:00Z', 'precision': 11}


class FakeWikimedia:
    def entities(self, ids):
        records = {
            'Q1': entity(P31=[{'id': 'Q5'}], P1477=[{'text': 'Full Author Name', 'language': 'en'}],
                         P569=[date('1856')], P570=[date('1919')], P19=[{'id': 'Q2'}], P18=['Portrait.jpg']),
            'Q2': entity(P17=[{'id': 'Q3'}]),
            'Q3': {'labels': {'en': {'value': 'United States'}}},
            'Q4': entity(P50=[{'id': 'Q1'}], P577=[date('1900'), date('1904')]),
            'Q6': entity(P50=[{'id': 'Q999'}], P577=[date('2000')]),
        }
        return {key: records[key] for key in ids if key in records}

    def get(self, url, **params):
        if '/summary/' in url:
            return {'title': 'Author Name', 'wikibase_item': 'Q1', 'extract': 'A sourced biography.'}
        if 'commons' in url:
            return {'query': {'pages': {'1': {'imageinfo': [{
                'url': 'https://upload.wikimedia.org/portrait.jpg',
                'descriptionurl': 'https://commons.wikimedia.org/wiki/File:Portrait.jpg',
                'extmetadata': {'Artist': {'value': '<a>Photographer</a>'},
                                'LicenseShortName': {'value': 'Public domain'}},
            }]}}}}
        return {'query': {'redirects': [{'from': 'Book', 'to': 'Canonical Book'}], 'pages': {
            '1': {'title': 'Canonical Book', 'pageprops': {'wikibase_item': 'Q4'}},
            '2': {'title': 'Wrong Author', 'pageprops': {'wikibase_item': 'Q6'}},
        }}}


def test_sourced_fields_and_verified_publication_dates():
    found = _research(FakeWikimedia(), 'Author Name', ['Book', 'Wrong Author', 'Unknown'])
    assert found['profile']['birth_name'] == 'Full Author Name'
    assert found['profile']['birth_country'] == 'United States'
    assert found['profile']['birth_date'] == '1856-01-01'
    assert found['profile']['death_date'] == '1919-01-01'
    assert found['profile']['biography'] == 'A sourced biography.'
    assert found['profile']['portrait_credit'] == 'Photographer · Public domain'
    assert found['books'] == {'Book': {'publication_year': '1900', 'source_url': 'https://www.wikidata.org/wiki/Q4'}}
    assert found['sources'] == ['https://en.wikipedia.org/wiki/Author_Name', 'https://www.wikidata.org/wiki/Q1']


def test_optional_failure_keeps_other_results():
    class UnavailablePortrait(FakeWikimedia):
        def get(self, url, **params):
            if 'commons' in url:
                raise ResearchError('Unavailable')
            return super().get(url, **params)
    found = _research(UnavailablePortrait(), 'Author Name', ['Book'])
    assert found['profile']['birth_date'] == '1856-01-01'
    assert 'portrait_url' not in found['profile']
    assert found['books']['Book']['publication_year'] == '1900'
    assert found['warnings'] == ['Unavailable']


@pytest.mark.parametrize('summary', [{}, {'type': 'disambiguation', 'wikibase_item': 'Q1'}, {'wikibase_item': 'Q4'}])
def test_no_guesses_for_missing_ambiguous_or_nonhuman_pages(summary):
    api = FakeWikimedia()
    api.get = lambda *args, **kwargs: summary
    with pytest.raises(ResearchError):
        _research(api, 'Ambiguous Name', [])


@pytest.mark.parametrize('value', [None, {}, {'time': '-0044-01-01', 'precision': 9},
                                   {'time': '+1800-01-01', 'precision': 7}])
def test_unknown_or_imprecise_year_stays_blank(value):
    assert date_year(value) == ''


@pytest.mark.parametrize('value', [date('1856'), {'time': '+1856-00-00T00:00:00Z', 'precision': 11},
                                   {'time': '+1856-05-15T00:00:00Z', 'precision': 9},
                                   {'time': '+1856-05-15T00:00:00Z', 'precision': 11,
                                    'calendarmodel': 'http://www.wikidata.org/entity/Q1985786'}])
def test_exact_date_requires_real_day_and_gregorian_calendar(value):
    from artifact_editor.author.research import exact_date
    assert exact_date(value) == ('1856-01-01' if value == date('1856') else '')


def test_pronunciation_suggestions_use_author_surname_and_dictionary():
    from artifact_editor.author.pronunciation import name_pronunciations
    class Dictionary:
        def get(self, url, **params):
            if 'wikipedia' in url:
                html = '<p><b>Lyman Frank Baum</b> (<span class="IPA">/bɔːm/</span>)</p>'
            else:
                ipa = {'Lyman': 'ˈlaɪmən', 'Frank': 'fræŋk'}[params['page']]
                html = f'<h2 id="English">English</h2><ul><li>IPA: <span class="IPA">/{ipa}/</span></li></ul>'
            return {'parse': {'text': {'*': html}}}
    found, sources = name_pronunciations(Dictionary(), 'L. Frank Baum', 'L. Frank Baum', 'Lyman Frank Baum')
    assert found['pen_name_pronunciation'] == '/ɛl fræŋk bɔːm/'
    assert found['birth_name_pronunciation'] == '/ˈlaɪmən fræŋk bɔːm/'
    assert len(sources) == 2


def test_explicit_copyright_statements_keep_jurisdiction_and_source():
    from artifact_editor.author.research import copyright_suggestions
    class Labels:
        def entities(self, ids):
            return {'Q19652': {'labels': {'en': {'value': 'public domain'}}},
                    'Q30': {'labels': {'en': {'value': 'United States'}}}}
    statement = claim({'id': 'Q19652'})
    statement['qualifiers'] = {'P1001': [{'snaktype': 'value', 'datavalue': {'value': {'id': 'Q30'}}}]}
    found = copyright_suggestions(Labels(), {'Q4': {'claims': {'P6216': [statement]}}})
    assert found == {'Q4': {'copyright_status': 'Public domain — United States',
                            'copyright_source': 'https://www.wikidata.org/wiki/Q4#P6216'}}


def test_no_copyright_guess_from_old_publication_or_historical_claim():
    from artifact_editor.author.research import copyright_suggestions
    statement = claim({'id': 'Q19652'})
    statement['qualifiers'] = {'P582': [{'snaktype': 'value', 'datavalue': {'value': date('1900')}}]}
    assert copyright_suggestions(FakeWikimedia(), {
        'Q1': entity(P577=[date('1800')]), 'Q2': {'claims': {'P6216': [statement]}},
    }) == {}


def test_research_can_fill_copyright_without_publication_year():
    class CopyrightData(FakeWikimedia):
        def entities(self, ids):
            records = super().entities(ids)
            if 'Q4' in records:
                records['Q4'] = entity(P50=[{'id': 'Q1'}], P6216=[{'id': 'Q19652'}])
                records['Q4']['claims']['P6216'][0]['qualifiers'] = {
                    'P1001': [{'snaktype': 'value', 'datavalue': {'value': {'id': 'Q30'}}}],
                }
            for identity, text in [('Q19652', 'public domain'), ('Q30', 'United States')]:
                if identity in ids:
                    records[identity] = {'labels': {'en': {'value': text}}}
            return records
    found = _research(CopyrightData(), 'Author Name', ['Book'])
    assert found['books']['Book'] == {
        'copyright_status': 'Public domain — United States',
        'copyright_source': 'https://www.wikidata.org/wiki/Q4#P6216',
    }


def test_series_lookup_uses_explicit_unambiguous_membership():
    from artifact_editor.author.research import series_suggestions
    class Labels:
        def entities(self, ids):
            return {'Q2': {'labels': {'en': {'value': 'Oz books'}}}}
    books = {'Q1': entity(P179=[{'id': 'Q2'}]),
             'Q3': entity(P179=[{'id': 'Q2'}, {'id': 'Q4'}]), 'Q5': entity()}
    assert series_suggestions(Labels(), books) == {
        'Q1': {'series': 'Oz books', 'series_source': 'https://www.wikidata.org/wiki/Q1#P179'},
    }
