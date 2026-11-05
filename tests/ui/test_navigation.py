"""A disabled Workshops menu must not contribute links to the UI audit."""
from pathlib import Path
from types import SimpleNamespace
from bs4 import BeautifulSoup
from jinja2 import Environment, FileSystemLoader
import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize('layout', ['base.html', 'pagebase.html'])
def test_workshops_is_disabled_and_has_no_rendered_destinations(layout):
    env = Environment(loader=FileSystemLoader(ROOT / 'src/artifact_editor/templates'))
    endpoints = []
    def url_for(endpoint, **kw):
        endpoints.append(endpoint)
        return '/' + endpoint
    chapter = SimpleNamespace(number=1, language='english', cosmetic='One')
    html = env.get_template(layout).render(chapter=chapter, author=SimpleNamespace(name='Writer'),
        title='Book', is_chapter=True, url_for=url_for, websocket_url=lambda: 'ws://test/ws')
    soup = BeautifulSoup(html, 'html.parser')
    trigger = next(n for n in soup.select('wa-button') if n.get_text(strip=True) == 'Workshops')
    assert trigger.has_attr('disabled')
    assert trigger.find_parent('wa-dropdown').select('a, wa-dropdown-item') == []
    assert not any('.frames.' in e or '.music.' in e or '.video.' in e for e in endpoints)
