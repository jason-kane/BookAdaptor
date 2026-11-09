import json

import pytest
from bs4 import BeautifulSoup

from artifact_editor.author.tests.test_profile import client
from artifact_editor.author.contributors import merge_people
from artifact_editor.author.research import _research
from artifact_editor.author.tests.test_research import entity


def test_individual_profiles_autosave_independently_from_shared_identity(client, tmp_path):
    first = client.post('/library/An Author/contributors', json={'name': 'First Writer'})
    second = client.post('/library/An Author/contributors', json={'name': 'Second Writer'})
    assert first.status_code == second.status_code == 200
    first_id, second_id = first.json['id'], second.json['id']
    assert first_id != second_id
    assert client.patch('/library/An Author/contributors/' + first_id, json={
        'birth_name': 'First Birth Name', 'birth_name_pronunciation': '/first/', 'name_pronunciation': '/writer/',
        'birth_country': 'A Country', 'birth_date': '1785-01-04', 'death_date': '1863-09-20', 'biography': 'First life.',
    }).status_code == 200
    assert client.patch('/library/An Author/contributors/' + second_id, json={
        'birth_name': 'Second Birth Name', 'birth_date': '1786-02-24', 'biography': 'Second life.',
    }).status_code == 200
    assert client.patch('/library/An Author/profile', json={'biography': 'Shared identity biography.'}).status_code == 200
    profile = json.loads((tmp_path / 'An Author/author.json').read_text())
    assert profile['identity_type'] == 'collective'
    assert profile['pen_name'] == 'An Author'
    assert profile['biography'] == 'Shared identity biography.'
    assert profile['contributors'][0]['birth_country'] == 'A Country'
    assert profile['contributors'][1]['biography'] == 'Second life.'
    soup = BeautifulSoup(client.get('/library/An Author/').data, 'html.parser')
    assert not soup.select_one('#contributors-section').has_attr('hidden')
    assert len(soup.select('.contributor-card')) == 2
    assert all(field.has_attr('hidden') for field in soup.select('#author-profile [data-personal-field]'))
    assert not (tmp_path / 'First Writer').exists()


def test_invalid_individual_dates_do_not_modify_other_profiles(client, tmp_path):
    person_id = client.post('/library/An Author/contributors', json={'name': 'A Person'}).json['id']
    path = tmp_path / 'An Author/author.json'
    original = path.read_bytes()
    response = client.patch('/library/An Author/contributors/' + person_id, json={'birth_date': '1900-02-30'})
    assert response.status_code == 400
    assert path.read_bytes() == original
    assert client.post('/library/An Author/contributors', json={'name': ''}).status_code == 400
    assert client.patch('/library/An Author/contributors/not-a-person', json={'name': 'Other'}).status_code == 404


def test_research_merge_deduplicates_and_preserves_manual_details(tmp_path):
    found = [{'name': 'Jacob Grimm', 'birth_name': 'Jacob Ludwig Carl Grimm', 'wikidata_id': 'Q6701',
              'birth_date': '1785-01-04', 'biography': 'Source biography.',
              'research_sources': ['https://www.wikidata.org/wiki/Q6701']}]
    profile, added, updated = merge_people(tmp_path, found)
    assert added == 1
    person = profile['contributors'][0]
    identity = person['id']
    person['biography'] = 'My own biography.'
    person['name'] = 'Jacob L. C. Grimm'
    (tmp_path / 'author.json').write_text(json.dumps(profile))
    profile, added, updated = merge_people(tmp_path, found)
    assert added == updated == 0
    assert len(profile['contributors']) == 1
    assert profile['contributors'][0]['id'] == identity
    assert profile['contributors'][0]['biography'] == 'My own biography.'
    assert profile['contributors'][0]['name'] == 'Jacob L. C. Grimm'


def test_shared_biography_research_saves_people_without_changing_pen_name(client, tmp_path, monkeypatch):
    from artifact_editor.author import views
    result = dict(profile={'identity_type': 'collective', 'biography': 'The shared name.'}, books={}, sources=[], warnings=[],
                  contributors=[{'name': 'First Writer', 'wikidata_id': 'Q1'}, {'name': 'Second Writer', 'wikidata_id': 'Q2'}])
    monkeypatch.setattr(views, 'research_author', lambda *args: result.copy())
    response = client.post('/library/An Author/research', json={})
    assert response.status_code == 200
    assert response.json['contributors_added'] == 2
    assert 'First Writer' in response.json['contributors_html']
    profile = json.loads((tmp_path / 'An Author/author.json').read_text())
    assert profile['identity_type'] == 'collective'
    assert len(profile['contributors']) == 2
    assert client.post('/library/An Author/research', json={}).json['contributors_added'] == 0
    assert (tmp_path / 'An Author').is_dir()


def test_individual_research_fills_only_that_persons_blanks(client, tmp_path, monkeypatch):
    from artifact_editor.author import views
    person_id = client.post('/library/An Author/contributors', json={'name': 'A Person', 'birth_country': 'Keep this country'}).json['id']
    other_id = client.post('/library/An Author/contributors', json={'name': 'Other Person'}).json['id']
    monkeypatch.setattr(views, 'research_author', lambda *args: dict(identity='Q1', profile={
        'birth_country': 'Source country', 'birth_date': '1785-01-04', 'biography': 'Source biography.',
    }, sources=['https://www.wikidata.org/wiki/Q1'], warnings=[]))
    response = client.post('/library/An Author/contributors/' + person_id + '/research')
    assert response.status_code == 200
    profile = json.loads((tmp_path / 'An Author/author.json').read_text())
    people = {person['id']: person for person in profile['contributors']}
    assert people[person_id]['birth_country'] == 'Keep this country'
    assert people[person_id]['birth_date'] == '1785-01-04'
    assert not people[other_id].get('birth_date')
    assert not profile.get('birth_date')


def test_collective_source_expands_verified_human_members():
    class GroupSource:
        def entities(self, ids):
            records = {'Q10': entity(P31=[{'id': 'Q999'}], P527=[{'id': 'Q1'}, {'id': 'Q2'}]),
                       'Q1': {**entity(P31=[{'id': 'Q5'}]), 'labels': {'en': {'value': 'First Writer'}}},
                       'Q2': {**entity(P31=[{'id': 'Q5'}]), 'labels': {'en': {'value': 'Second Writer'}}}}
            return {key: records[key] for key in ids if key in records}
        def get(self, url, **params):
            if '/summary/' in url:
                name = url.rsplit('/', 1)[-1]
                identity = {'Shared_Name': 'Q10', 'First_Writer': 'Q1', 'Second_Writer': 'Q2'}[name]
                return dict(title=name.replace('_', ' '), wikibase_item=identity, extract='Biography of ' + name)
            return {}
    result = _research(GroupSource(), 'Shared Name', ())
    assert result['profile']['identity_type'] == 'collective'
    assert 'birth_date' not in result['profile'] and 'birth_name' not in result['profile']
    assert [person['name'] for person in result['contributors']] == ['First Writer', 'Second Writer']
    assert all(person['biography'] for person in result['contributors'])
