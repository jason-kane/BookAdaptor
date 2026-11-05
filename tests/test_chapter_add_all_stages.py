"""Exercise Add All orchestration without loading models or generating assets."""
import ast
from contextlib import nullcontext
from pathlib import Path
from threading import Lock
from types import SimpleNamespace
from unittest.mock import Mock
import sys

import pytest


def run_pipeline(monkeypatch, failures=()):
    path = Path(__file__).resolve().parents[1] / 'src/websocket/server.py'
    tree = ast.parse(path.read_text())
    branch = next(n for n in ast.walk(tree) if isinstance(n, ast.If)
                  and ast.unparse(n.test) == "task.get('action') == 'chapter_add_all'")
    function = ast.FunctionDef(name='run', args=ast.arguments(posonlyargs=[], args=[],
        kwonlyargs=[], kw_defaults=[], defaults=[]), body=branch.body, decorator_list=[])
    calls = []
    def operation(name, result=None):
        def perform(*args, **kwargs):
            calls.append((name, kwargs))
            if kwargs.get('progress'):
                kwargs['progress'](50, name)
            return result
        return perform
    monkeypatch.setitem(sys.modules, 'artifact_editor.characters.characters', SimpleNamespace(
        extract_characters=operation('characters'), assign_phrase_speakers=operation('assign'),
        extract_all_description_timelines=operation('appearances', {'failures': failures})))
    monkeypatch.setitem(sys.modules, 'artifact_editor.props.props', SimpleNamespace(extract_props=operation('props')))
    monkeypatch.setitem(sys.modules, 'artifact_editor.images.images', SimpleNamespace(generate_missing_metadata=operation('metadata')))
    monkeypatch.setitem(sys.modules, 'artifact_editor.phrases.phrases', SimpleNamespace(generate_all_audio=operation('audio')))
    monkeypatch.setitem(sys.modules, 'artifact_editor.chapter.timeline', SimpleNamespace(place_image_slots=operation('slots')))
    import importlib.util
    spec = importlib.util.spec_from_file_location('test_workflow_stages', path.parents[1] / 'artifact_editor/chapter/workflow.py')
    workflow = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(workflow)
    ACTIONS = workflow.ACTIONS
    monkeypatch.setitem(sys.modules, 'artifact_editor.chapter.workflow', SimpleNamespace(ACTIONS=ACTIONS, complete_step=Mock()))
    monkeypatch.setitem(sys.modules, 'gpu_tasks', SimpleNamespace(task_label=lambda _: nullcontext()))
    progress = Mock()
    scope = dict(chapter=SimpleNamespace(textoviva=SimpleNamespace(
        generate_missing_images=operation('images'), generate_missing_animations=operation('animations'))),
        client=object(), job_id='test', report_progress=progress, Lock=Lock)
    exec(compile(ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[])), str(path), 'exec'), scope)
    return scope['run'], calls, progress


def test_add_all_prepares_all_assets_in_order_without_assembling_video(monkeypatch):
    run, calls, progress = run_pipeline(monkeypatch)
    run()
    assert [name for name, _ in calls] == [
        'characters', 'assign', 'appearances', 'audio', 'slots', 'props', 'metadata', 'images', 'animations']
    options = dict(calls)
    assert options['assign']['reassign_all'] is True
    assert options['audio']['force'] is False
    assert options['audio']['batch'] is True
    assert not options['metadata'].get('skip_existing_images', False)
    assert options['animations']['regenerate_insufficient'] is True
    percentages = [call.args[2] for call in progress.call_args_list]
    assert percentages == sorted(percentages)
    assert percentages[-1] == 100
    assert progress.call_args.args[-1] == 'complete'
    assert 'separately' in progress.call_args.args[-2]


def test_appearance_failure_stops_before_audio_and_does_not_report_success(monkeypatch):
    run, calls, progress = run_pipeline(monkeypatch, failures=['Jack: model failed'])
    with pytest.raises(RuntimeError, match='Jack: model failed'):
        run()
    assert [name for name, _ in calls] == ['characters', 'assign', 'appearances']
    assert all(call.args[-1] != 'complete' for call in progress.call_args_list)
