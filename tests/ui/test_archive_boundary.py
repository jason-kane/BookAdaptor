"""Archiving must remove definitions/routes without introducing runtime imports."""
import ast
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import load_views

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = json.loads((ROOT / 'src_archive/manifest.json').read_text())


@pytest.mark.parametrize('entry', MANIFEST['definitions'], ids=lambda e: e['original_path'] + ':' + e['symbol'])
def test_archived_definition_exists_only_in_archive(entry):
    archive = ast.parse((ROOT / entry['archive_path']).read_text())
    assert entry['symbol'] in {n.name for n in archive.body if isinstance(n, (ast.FunctionDef, ast.ClassDef))}
    active = ROOT / entry['original_path']
    if active.exists():
        tree = ast.parse(active.read_text())
        assert entry['symbol'] not in {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef))}
        # No direct global reference left in the originating module.
        assert not any(isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)
                       and n.id == entry['symbol'] for n in ast.walk(tree))


def test_runtime_does_not_reference_archive():
    for path in (ROOT / 'src').rglob('*.py'):
        if '.pre-' in path.name:
            continue
        text = path.read_text()
        assert 'src_archive' not in text, str(path)
    for asset in MANIFEST['assets']:
        assert not (ROOT / asset).exists()
        assert (ROOT / asset.replace('src/', 'src_archive/', 1)).exists()


@pytest.mark.parametrize('url', ['/styles/draw_all_styles', '/styles/draw_all_lora', '/styles/add_style', '/styles/export_styles'])
def test_archived_styler_routes_are_not_registered(styler_ui, url):
    assert styler_ui.client.post(url).status_code == 405  # sample DELETE route still matches this path


def test_archived_workflow_open_route_is_not_registered(workflow_ui):
    assert workflow_ui.client.get('/workflow_templates/open/unused.json').status_code == 404


def test_empty_book_keeps_reachable_chapter_breakdown_form(book_ui, tmp_path):
    # Exercise the real chapter-list helper, including its conditional template.
    helpers, _ = load_views('artifact_editor/chapter/htmx.py',
        const=SimpleNamespace(LIBRARY_DIR=str(tmp_path)), Chapter=SimpleNamespace)
    book_ui.scope['chapter_htmx'] = helpers
    response = book_ui.client.get(book_ui.url + '/')
    assert response.status_code == 200
    assert 'Breakdown Chapters' in response.text
    assert '/library.book.breakdown_chapters' in response.text
    assert 'book.breakdown_chapters' in {rule.endpoint for rule in book_ui.client.application.url_map.iter_rules()}


def test_favorite_action_keeps_card_adapter(styler_ui):
    assert 'Card' in styler_ui.scope
    assert 'styles.choose_this_style' in {rule.endpoint for rule in styler_ui.client.application.url_map.iter_rules()}
    # The adapter can still be constructed; it must not log an obsolete warning.
    card = styler_ui.views.Card('Ink', tier=2)
    assert card.style_name == 'Ink' and card.tier == 2
    styler_ui.scope['log'].warning.assert_not_called()
