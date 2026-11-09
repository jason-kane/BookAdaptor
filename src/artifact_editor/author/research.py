"""Sourced author suggestions from Wikimedia; no model-generated facts or writes."""
import re
import time
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

from .profile import web_url, full_date
from .pronunciation import name_pronunciations

WIKIPEDIA = 'https://en.wikipedia.org'
WIKIDATA = 'https://www.wikidata.org'
HEADERS = {'User-Agent': 'BookAdaptor/1.0 (local author bibliography research)'}


class ResearchError(Exception):
    pass


class Wikimedia:
    def __init__(self):
        self.deadline = time.monotonic() + 45
        self.responses = {}
        self.session = requests.Session()
        self.session.headers.update(HEADERS)

    def get(self, url, **params):
        key = (url, tuple(sorted(params.items())))
        if key in self.responses:
            return self.responses[key]
        remaining = self.deadline - time.monotonic()
        if remaining < 1:
            raise ResearchError('Research timed out. Please try again.')
        try:
            response = self.session.get(url, params=params, timeout=(3, min(8, remaining)))
            if response.status_code == 404:
                return {}
            response.raise_for_status()
            result = response.json()
            if 'error' in result:
                raise ResearchError('The research source could not complete this lookup. Please try again.')
            self.responses[key] = result
            return result
        except (requests.RequestException, ValueError) as error:
            raise ResearchError('Wikipedia or Wikidata is unavailable. Please try again.') from error

    def entities(self, ids):
        ids = list(dict.fromkeys(i for i in ids if re.fullmatch(r'Q[1-9][0-9]*', i or '')))
        result = {}
        for start in range(0, len(ids), 50):
            result.update(self.get(WIKIDATA + '/w/api.php', action='wbgetentities',
                                   ids='|'.join(ids[start:start + 50]), props='claims|labels',
                                   languages='en', format='json').get('entities', {}))
        return result


def values(entity, prop):
    claims = [c for c in entity.get('claims', {}).get(prop, [])
              if c.get('rank') != 'deprecated' and c.get('mainsnak', {}).get('snaktype') == 'value']
    preferred = [c for c in claims if c.get('rank') == 'preferred']
    return [c['mainsnak']['datavalue']['value'] for c in preferred or claims]


def single(entity, prop):
    items = values(entity, prop)
    return items[0] if len(items) == 1 else None


def label(entity):
    return entity.get('labels', {}).get('en', {}).get('value', '')


def date_year(value):
    if not isinstance(value, dict) or value.get('precision', 0) < 9:
        return ''
    match = re.match(r'^\+0*([1-9][0-9]{0,3})-', value.get('time', ''))
    return match.group(1) if match else ''


def exact_date(value):
    if not isinstance(value, dict) or value.get('precision', 0) < 11:
        return ''
    # Dates are stored in the source calendar. Do not silently label Julian dates
    # as modern Gregorian dates.
    if value.get('calendarmodel', 'http://www.wikidata.org/entity/Q1985727').rsplit('/', 1)[-1] != 'Q1985727':
        return ''
    match = re.match(r'^\+(\d{4})-(\d{2})-(\d{2})T', value.get('time', ''))
    if not match:
        return ''
    try:
        return full_date('-'.join(match.groups()))
    except ValueError:
        return ''


def plain(value):
    return BeautifulSoup(value or '', 'html.parser').get_text(' ', strip=True)


def normalized(value):
    return ' '.join(value.replace('_', ' ').casefold().split())


def research_author(name, titles=()):
    api = Wikimedia()
    try:
        return _research(api, name, titles)
    finally:
        api.session.close()


def _research(api, name, titles, *, include_portrait=True):
    result = {'profile': {}, 'books': {}, 'sources': [], 'warnings': []}
    summary = api.get(WIKIPEDIA + '/api/rest_v1/page/summary/' + quote(name.replace(' ', '_'), safe=''))
    if not summary or summary.get('type') == 'disambiguation' or not summary.get('wikibase_item'):
        raise ResearchError('No unambiguous Wikipedia author page was found. Try a more specific full name.')
    author_id = summary['wikibase_item']
    author = api.entities([author_id]).get(author_id, {})
    if not any(v.get('id') == 'Q5' for v in values(author, 'P31') if isinstance(v, dict)):
        return _research_collective(api, summary, author, titles)
    article_url = WIKIPEDIA + '/wiki/' + quote(summary.get('title', name).replace(' ', '_'))
    entity_url = WIKIDATA + '/wiki/' + author_id
    result['sources'] = [article_url, entity_url]
    result['identity'] = author_id
    profile = result['profile']
    birth_name = single(author, 'P1477')
    profile['birth_name'] = (birth_name['text'] if isinstance(birth_name, dict) and birth_name.get('text')
                            else label(author) or summary.get('title', ''))
    profile['pen_name'] = summary.get('title', name)
    profile['biography'] = summary.get('extract', '')
    profile['birth_date'] = exact_date(single(author, 'P569'))
    profile['death_date'] = exact_date(single(author, 'P570'))

    # Optional lookups can fail independently without discarding known facts.
    place = single(author, 'P19')
    if isinstance(place, dict) and place.get('id'):
        try:
            birthplace = api.entities([place['id']]).get(place['id'], {})
            country = single(birthplace, 'P17')
            if isinstance(country, dict) and country.get('id'):
                profile['birth_country'] = label(api.entities([country['id']]).get(country['id'], {}))
        except ResearchError as error:
            result['warnings'].append(str(error))

    image = single(author, 'P18')
    if include_portrait and isinstance(image, str):
        try:
            pages = api.get('https://commons.wikimedia.org/w/api.php', action='query',
                            titles='File:' + image, prop='imageinfo', iiprop='url|extmetadata',
                            iiurlwidth=600, format='json').get('query', {}).get('pages', {})
            info = next((p['imageinfo'][0] for p in pages.values() if p.get('imageinfo')), {})
            metadata = info.get('extmetadata', {})
            def text(key):
                return plain(metadata.get(key, {}).get('value', ''))
            if info.get('url') and info.get('descriptionurl'):
                profile['portrait_url'] = web_url(info.get('thumburl') or info['url'])
                profile['portrait_source'] = web_url(info['descriptionurl'])
                profile['portrait_credit'] = ' · '.join(filter(None, (text('Artist'), text('Credit'), text('LicenseShortName'))))
        except (ResearchError, ValueError) as error:
            result['warnings'].append(str(error))

    # Match existing titles, including redirects, then require the same Wikidata
    # author. Never take a year from a film, adaptation, or unrelated namesake.
    for start in range(0, len(titles), 50):
        chunk = titles[start:start + 50]
        try:
            query = api.get(WIKIPEDIA + '/w/api.php', action='query', titles='|'.join(chunk),
                            redirects=1, prop='pageprops', format='json').get('query', {})
            redirects = {normalized(v['from']): v['to'] for key in ('normalized', 'redirects') for v in query.get(key, [])}
            pages = {normalized(p['title']): p for p in query.get('pages', {}).values()}
            entities = api.entities([p.get('pageprops', {}).get('wikibase_item') for p in pages.values()])
            verified = {key: value for key, value in entities.items()
                        if any(v.get('id') == author_id for v in values(value, 'P50') if isinstance(v, dict))}
            try:
                copyrights = copyright_suggestions(api, verified)
            except ResearchError as error:
                copyrights = {}
                result['warnings'].append(str(error))
            for title in chunk:
                resolved = title
                seen = set()
                while normalized(resolved) in redirects and normalized(resolved) not in seen:
                    seen.add(normalized(resolved))
                    resolved = redirects[normalized(resolved)]
                page = pages.get(normalized(resolved), {})
                entity_id = page.get('pageprops', {}).get('wikibase_item')
                book = entities.get(entity_id, {})
                if not any(v.get('id') == author_id for v in values(book, 'P50') if isinstance(v, dict)):
                    continue
                years = [date_year(v) for v in values(book, 'P577')]
                years = [v for v in years if v]
                details = dict(copyrights.get(entity_id, {}))
                if years:
                    details.update(publication_year=min(years, key=int), source_url=WIKIDATA + '/wiki/' + entity_id)
                if details:
                    result['books'][title] = details
        except ResearchError as error:
            result['warnings'].append(str(error))
            break
    try:
        pronunciations, sources = name_pronunciations(api, summary.get('title', name),
                                                     profile['pen_name'], profile['birth_name'])
        profile.update(pronunciations)
        result['sources'].extend(sources)
    except ResearchError as error:
        result['warnings'].append(str(error))
    return result


def copyright_suggestions(api, books):
    """Report explicit, undated copyright claims with their jurisdiction scope."""
    claims = {}
    ids = []
    for identity, book in books.items():
        current = []
        for claim in book.get('claims', {}).get('P6216', []):
            snak = claim.get('mainsnak', {})
            qualifiers = claim.get('qualifiers', {})
            if claim.get('rank') == 'deprecated' or snak.get('snaktype') != 'value':
                continue
            # Historical/dated claims need interpretation; leave these unknown.
            if any(key in qualifiers for key in ('P580', 'P582', 'P585')):
                continue
            status = snak.get('datavalue', {}).get('value', {}).get('id')
            jurisdictions = [v.get('datavalue', {}).get('value', {}).get('id')
                             for v in qualifiers.get('P1001', []) if v.get('snaktype') == 'value']
            if status:
                current.append((status, jurisdictions))
                ids.extend([status, *jurisdictions])
        claims[identity] = current
    labels = api.entities(ids) if ids else {}
    found = {}
    for identity, statements in claims.items():
        descriptions = []
        for status, jurisdictions in statements:
            text = label(labels.get(status, {}))
            places = [label(labels.get(value, {})) for value in jurisdictions]
            if not text or (jurisdictions and not all(places)):
                continue
            scope = ', '.join(places) if places else 'jurisdiction unspecified'
            descriptions.append(text[0].upper() + text[1:] + ' — ' + scope)
        if descriptions:
            found[identity] = dict(copyright_status='; '.join(dict.fromkeys(descriptions)),
                                   copyright_source=WIKIDATA + '/wiki/' + identity + '#P6216')
    return found


def research_bibliography(name):
    """Find work-level written publications explicitly attributed to this author."""
    api = Wikimedia()
    try:
        return _discover_bibliography(api, name)
    finally:
        api.session.close()


def _discover_bibliography(api, name):
    summary = api.get(WIKIPEDIA + '/api/rest_v1/page/summary/' + quote(name.replace(' ', '_'), safe=''))
    identity = summary.get('wikibase_item', '')
    if summary.get('type') == 'disambiguation' or not re.fullmatch(r'Q[1-9][0-9]*', identity):
        raise ResearchError('No unambiguous author was found. No bibliography entries were added.')
    books, warnings = [], []
    offset = 0
    while True:
        query = f'''SELECT DISTINCT ?book ?title WHERE {{
          ?book wdt:P50 wd:{identity}; wdt:P31/wdt:P279* ?kind; rdfs:label ?title.
          VALUES ?kind {{ wd:Q571 wd:Q7725634 wd:Q47461344 }}
          FILTER(LANG(?title) = "en")
          FILTER NOT EXISTS {{ ?book wdt:P31 wd:Q277759 }}
          FILTER NOT EXISTS {{ ?book (wdt:P31|wdt:P7937|wdt:P136)/wdt:P279* wd:Q49084 }}
          FILTER NOT EXISTS {{ ?book wdt:P31/wdt:P279* wd:Q3331189 }}
          FILTER NOT EXISTS {{ ?book wdt:P31 wd:Q42396623 }}
        }} ORDER BY ?book LIMIT 200 OFFSET {offset}'''
        try:
            rows = api.get('https://query.wikidata.org/sparql', query=query, format='json').get('results', {}).get('bindings', [])
            ids = [row['book']['value'].rsplit('/', 1)[-1] for row in rows]
            # Aliases help match existing entries under alternative English titles.
            entities = {}
            for start in range(0, len(ids), 50):
                entities.update(api.get(WIKIDATA + '/w/api.php', action='wbgetentities',
                    ids='|'.join(ids[start:start + 50]), props='claims|labels|aliases', languages='en',
                    format='json').get('entities', {}))
            try:
                copyrights = copyright_suggestions(api, entities)
            except ResearchError as error:
                copyrights = {}
                warnings.append(str(error))
            try:
                series = series_suggestions(api, entities)
            except ResearchError as error:
                series = {}
                warnings.append(str(error))
            for row in rows:
                book_id = row['book']['value'].rsplit('/', 1)[-1]
                book = entities.get(book_id, {})
                if not any(value.get('id') == identity for value in values(book, 'P50') if isinstance(value, dict)):
                    continue
                years = [date_year(value) for value in values(book, 'P577')]
                entry = dict(title=row['title']['value'], wikidata_id=book_id,
                             aliases=[value['value'] for value in book.get('aliases', {}).get('en', [])],
                             publication_year=min((value for value in years if value), key=int, default=''),
                             source_url=WIKIDATA + '/wiki/' + book_id)
                dates = [exact_date(value) for value in values(book, 'P577')]
                dates = [value for value in dates if value and value[:4] == entry['publication_year']]
                if dates:
                    entry['publication_date'] = min(dates)
                entry.update(series.get(book_id, {}))
                entry.update(copyrights.get(book_id, {}))
                books.append(entry)
        except ResearchError as error:
            if not books:
                raise
            warnings.append(str(error) + ' Some catalogue results could not be checked; retry to find more.')
            break
        if len(rows) < 200:
            break
        offset += 200
    return dict(books=books, warnings=list(dict.fromkeys(warnings)))


def series_suggestions(api, books):
    memberships = {identity: single(book, 'P179') for identity, book in books.items()}
    ids = [value['id'] for value in memberships.values() if isinstance(value, dict) and value.get('id')]
    labels = api.entities(ids) if ids else {}
    result = {}
    for identity, value in memberships.items():
        if not isinstance(value, dict):
            continue
        name = label(labels.get(value.get('id'), {}))
        if name:
            result[identity] = dict(series=name, series_source=WIKIDATA + '/wiki/' + identity + '#P179')
    return result


def _research_collective(api, summary, entity, titles):
    group_id = summary['wikibase_item']
    members = [value['id'] for value in values(entity, 'P527') if isinstance(value, dict) and value.get('id')]
    members = api.entities(members) if members else {}
    people = {identity: member for identity, member in members.items()
              if any(value.get('id') == 'Q5' for value in values(member, 'P31') if isinstance(value, dict))}
    if not people:
        raise ResearchError('The matching page is not a person or a group with identified individual authors.')
    article = WIKIPEDIA + '/wiki/' + quote(summary['title'].replace(' ', '_'))
    sources = [article, WIKIDATA + '/wiki/' + group_id]
    result = dict(profile={'identity_type': 'collective', 'biography': summary.get('extract', '')},
                  books={}, sources=sources, warnings=[], contributors=[], identity=group_id)
    places = {identity: single(person, 'P19') for identity, person in people.items()}
    countries = {}
    try:
        locations = api.entities([place['id'] for place in places.values() if isinstance(place, dict) and place.get('id')])
        country_ids = {key: single(place, 'P17') for key, place in locations.items()}
        country_labels = api.entities([country['id'] for country in country_ids.values() if isinstance(country, dict) and country.get('id')])
        for identity, place in places.items():
            country = country_ids.get(place.get('id')) if isinstance(place, dict) else None
            if isinstance(country, dict):
                countries[identity] = label(country_labels.get(country.get('id'), {}))
    except ResearchError as error:
        result['warnings'].append(str(error))
    for identity, person in people.items():
        name = label(person)
        candidate = dict(name=name, wikidata_id=identity, birth_country=countries.get(identity, ''),
                         birth_date=exact_date(single(person, 'P569')),
                         death_date=exact_date(single(person, 'P570')),
                         research_sources=[WIKIDATA + '/wiki/' + identity])
        birth_name = single(person, 'P1477')
        if isinstance(birth_name, dict):
            candidate['birth_name'] = birth_name.get('text', '')
        transcription = single(person, 'P898')
        if isinstance(transcription, str):
            candidate['name_pronunciation'] = transcription
        for statement in person.get('claims', {}).get('P1477', []):
            if statement.get('rank') == 'deprecated':
                continue
            for value in statement.get('qualifiers', {}).get('P898', []):
                ipa = value.get('datavalue', {}).get('value')
                if isinstance(ipa, str):
                    candidate['birth_name_pronunciation'] = ipa
        try:
            personal = api.get(WIKIPEDIA + '/api/rest_v1/page/summary/' + quote(name.replace(' ', '_'), safe=''))
            if personal.get('wikibase_item') == identity and personal.get('type') != 'disambiguation':
                candidate['biography'] = personal.get('extract', '')
                candidate['research_sources'].append(WIKIPEDIA + '/wiki/' + quote(personal.get('title', name).replace(' ', '_')))
        except ResearchError as error:
            result['warnings'].append(str(error))
        if name:
            result['contributors'].append(candidate)
    # Prioritize every person's core facts and biography before optional
    # dictionary requests can consume the shared lookup deadline.
    for candidate in result['contributors']:
        if getattr(api, 'deadline', float('inf')) - time.monotonic() < 10:
            result['warnings'].append('Pronunciation lookup was incomplete; research an individual to try again.')
            break
        try:
            pronunciations, pronunciation_sources = name_pronunciations(
                api, candidate['name'], candidate['name'], candidate.get('birth_name', ''))
            for target, source in (('name_pronunciation', 'pen_name_pronunciation'), ('birth_name_pronunciation', 'birth_name_pronunciation')):
                if not candidate.get(target) and pronunciations.get(source):
                    candidate[target] = pronunciations[source]
            candidate['research_sources'].extend(pronunciation_sources)
        except ResearchError as error:
            result['warnings'].append(str(error))
            break
    result['warnings'] = list(dict.fromkeys(result['warnings']))
    return result
