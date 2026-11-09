"""English IPA suggestions assembled from sourced name parts, never guessed."""
import re
from urllib.parse import quote

from bs4 import BeautifulSoup

LETTER_IPA = dict(zip('ABCDEFGHIJKLMNOPQRSTUVWXYZ', (
    'eɪ', 'biː', 'siː', 'diː', 'iː', 'ɛf', 'dʒiː', 'eɪtʃ', 'aɪ', 'dʒeɪ', 'keɪ', 'ɛl', 'ɛm',
    'ɛn', 'oʊ', 'piː', 'kjuː', 'ɑːr', 'ɛs', 'tiː', 'juː', 'viː', 'ˈdʌbəljuː', 'ɛks', 'waɪ', 'ziː')))


def english_ipa(html):
    soup = BeautifulSoup(html, 'html.parser')
    language = None
    candidates = []
    for node in soup.find_all(['h2', 'span']):
        if node.name == 'h2':
            language = node.get_text(' ', strip=True).replace('[edit]', '').strip()
        elif language == 'English' and 'IPA' in node.get('class', []):
            # Ignore IPA symbols mentioned in qualifiers (e.g. “without /æ/ raising”).
            parent = node.find_parent('li')
            if parent is None or not parent.get_text(' ', strip=True).startswith(('IPA', '(', 'US', 'UK')):
                continue
            if node.find_parent(class_=lambda value: value and value in ('qualifier-content', 'ib-content', 'qualifier')):
                continue
            text = node.get_text('', strip=True)
            if text.startswith('/') and text.endswith('/'):
                candidates.append(text.strip('/'))
    return candidates[0] if candidates else ''


def name_pronunciations(api, article_title, pen_name, birth_name):
    parts = {}
    sources = []
    # The lead often documents the author's surname pronunciation, which takes
    # precedence over a dictionary surname that may have other pronunciations.
    page = api.get('https://en.wikipedia.org/w/api.php', action='parse', page=article_title,
                   prop='text', section=0, format='json')
    soup = BeautifulSoup(page.get('parse', {}).get('text', {}).get('*', ''), 'html.parser')
    lead = next((p for p in soup.find_all('p') if p.find('b')), None)
    ipa = lead.select_one('.IPA') if lead else None
    if ipa:
        text = ipa.get_text('', strip=True).strip('/[]')
        if text and not re.search(r'\s', text):
            parts[pen_name.split()[-1]] = text
    names = dict(pen_name_pronunciation=pen_name, birth_name_pronunciation=birth_name)
    tokens = list(dict.fromkeys(token for name in names.values() for token in name.split()))
    for token in tokens[:10]:
        if re.fullmatch(r'[A-Z]\.', token):
            parts[token] = LETTER_IPA[token[0]]
        elif token not in parts:
            page = api.get('https://en.wiktionary.org/w/api.php', action='parse', page=token,
                           prop='text', format='json')
            pronunciation = english_ipa(page.get('parse', {}).get('text', {}).get('*', ''))
            if pronunciation:
                parts[token] = pronunciation
                sources.append('https://en.wiktionary.org/wiki/' + quote(token) + '#English')
    found = {key: '/' + ' '.join(parts[token] for token in name.split()) + '/'
             for key, name in names.items() if name and all(token in parts for token in name.split())}
    return found, sources
