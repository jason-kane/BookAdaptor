"""Independent people represented by a shared author identity."""
from contextlib import contextmanager
import fcntl
import re
from uuid import uuid4

from .profile import read_json, write_json, full_date, web_url

FIELDS = {'name', 'name_pronunciation', 'birth_name', 'birth_name_pronunciation',
          'birth_country', 'birth_date', 'death_date', 'biography', 'research_sources', 'wikidata_id'}


@contextmanager
def profile_lock(directory):
    with (directory / '.author-profile.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def validate_person(changes, existing=None):
    if not isinstance(changes, dict) or set(changes) - FIELDS:
        raise ValueError('Unknown individual-author fields.')
    person = dict(existing or {})
    for key, value in changes.items():
        if key == 'research_sources':
            if not isinstance(value, list) or len(value) > 20 or any(not isinstance(item, str) for item in value):
                raise ValueError('Invalid individual research sources.')
            person[key] = [web_url(item) for item in value]
            continue
        if not isinstance(value, str):
            raise ValueError('Individual-author fields must contain text.')
        value = value.strip()
        if len(value) > (60000 if key == 'biography' else 1000):
            raise ValueError('That individual-author field is too long.')
        if key in ('birth_date', 'death_date'):
            value = full_date(value)
        if key == 'wikidata_id' and value and not re.fullmatch(r'Q[1-9][0-9]*', value):
            raise ValueError('Invalid individual source identity.')
        person[key] = value
    if not person.get('name'):
        raise ValueError('Enter the individual author’s name.')
    if person.get('birth_date') and person.get('death_date') and person['death_date'] < person['birth_date']:
        raise ValueError('Death date cannot precede birth date.')
    return person


def name_key(value):
    return ''.join(char for char in value.casefold() if char.isalnum())


def merge_people(directory, discoveries):
    added = updated = 0
    with profile_lock(directory):
        path = directory / 'author.json'
        profile = read_json(path)
        people = profile.get('contributors', [])
        for found in discoveries:
            candidate = validate_person(found)
            identity = candidate.get('wikidata_id')
            keys = {name_key(candidate.get(field, '')) for field in ('name', 'birth_name')} - {''}
            existing = next((person for person in people if (identity and person.get('wikidata_id') == identity)
                            or (not person.get('wikidata_id') and keys.intersection(
                                name_key(person.get(field, '')) for field in ('name', 'birth_name')))), None)
            if existing is None:
                candidate['id'] = str(uuid4())
                people.append(candidate)
                added += 1
            else:
                changes = {key: value for key, value in candidate.items() if value and not existing.get(key)}
                sources = list(dict.fromkeys(existing.get('research_sources', []) + candidate.get('research_sources', [])))[:20]
                if sources != existing.get('research_sources', []):
                    changes['research_sources'] = sources
                if changes:
                    existing.update(changes)
                    updated += 1
        profile['contributors'] = people
        profile['identity_type'] = 'collective'
        write_json(path, profile)
    return profile, added, updated
