"""Workflow prerequisites survive reloads and invalidate stale later steps."""
from types import SimpleNamespace
import sys

from bs4 import BeautifulSoup
import pytest

import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location('chapter_workflow', Path(__file__).resolve().parents[1] / 'src/artifact_editor/chapter/workflow.py')
workflow = importlib.util.module_from_spec(spec)
spec.loader.exec_module(workflow)


@pytest.fixture
def chapter(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, 'const', SimpleNamespace(LIBRARY_DIR=str(tmp_path)))
    return SimpleNamespace(chapterdir='author/book/chapter/0001', language='english',
        get_xml=lambda: BeautifulSoup('<chapter/>', 'xml'))


def test_unimported_chapter_blocks_every_preparation_action(chapter):
    for action in [*workflow.ACTIONS[1:], 'chapter_add_all']:
        assert workflow.prerequisite_message(chapter, action) == 'Must import chapter first'
    assert workflow.prerequisite_message(chapter, 'chapter_import') is None
    with pytest.raises(ValueError, match='Must import chapter first'):
        workflow.begin_step(chapter, 'chapter_missing_characters')
    assert workflow.completed_steps(chapter) == set()


def test_legacy_text_unlocks_characters_but_does_not_guess_extraction_success(chapter):
    chapter.get_xml = lambda: BeautifulSoup('<chapter><phrase>Text</phrase></chapter>', 'xml')
    assert workflow.prerequisite_message(chapter, 'chapter_missing_characters') is None
    assert workflow.prerequisite_message(chapter, 'chapter_add_all') is None
    assert workflow.prerequisite_message(chapter, 'chapter_assign_characters') == 'Must add missing characters first'


def test_successive_steps_unlock_next_and_reruns_invalidate_later_steps(chapter):
    for position, action in enumerate(workflow.ACTIONS):
        assert workflow.prerequisite_message(chapter, action) is None
        workflow.begin_step(chapter, action)
        assert action not in workflow.completed_steps(chapter)
        workflow.complete_step(chapter, action)
        # Fresh chapter objects read persisted completion, including no-op steps.
        reloaded = SimpleNamespace(**vars(chapter))
        assert set(workflow.ACTIONS[:position + 1]) == workflow.completed_steps(reloaded)
    workflow.begin_step(chapter, 'chapter_assign_characters')
    assert workflow.completed_steps(chapter) == set(workflow.ACTIONS[:2])
    assert workflow.prerequisite_message(chapter, 'chapter_missing_audio') == 'Must assign characters first'
    workflow.begin_step(chapter, 'chapter_import')
    assert workflow.completed_steps(chapter) == set()


def test_add_all_keeps_import_and_clears_later_completion(chapter):
    for action in workflow.ACTIONS:
        workflow.complete_step(chapter, action)
    workflow.begin_step(chapter, 'chapter_add_all')
    assert workflow.completed_steps(chapter) == {'chapter_import'}
    chapter.language = 'french'
    assert workflow.completed_steps(chapter) == set()


def test_disabled_button_has_exact_hover_message_and_unlocks_after_import(monkeypatch, chapter):
    import ast
    from markupsafe import escape
    path = Path(__file__).resolve().parents[1] / 'src/artifact_editor/chapter/htmx.py'
    node = next(n for n in ast.parse(path.read_text()).body
                if isinstance(n, ast.FunctionDef) and n.name == 'missing_asset_button')
    monkeypatch.setitem(sys.modules, 'artifact_editor.chapter.workflow', workflow)
    scope = {'__package__': 'artifact_editor.chapter', 'escape': escape, 'websocket_url': lambda: 'ws://localhost/ws'}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), scope)
    chapter.author, chapter.title, chapter.number = SimpleNamespace(name='Writer'), 'Book', 1
    def button():
        return BeautifulSoup(scope['missing_asset_button'](chapter, 'chapter_missing_characters', 'Add Missing Characters'), 'html.parser')
    blocked = button()
    assert blocked.find('wa-button').has_attr('disabled')
    assert blocked.find('wa-button')['title'] == 'Must import chapter first'
    assert blocked.find('div')['title'] == 'Must import chapter first'
    workflow.complete_step(chapter, 'chapter_import')
    assert not button().find('wa-button').has_attr('disabled')
