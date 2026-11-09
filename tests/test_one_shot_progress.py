"""Exercise orchestration without model requests or generated assets."""
import ast
from contextlib import nullcontext
from pathlib import Path
import sys
from threading import Lock
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock


def test_asset_steps_report_progress_and_complete_correct_milestones(monkeypatch):
    completed = []
    calls = []

    def operation(name):
        def run(*args, **kwargs):
            calls.append(name)
            if kwargs.get('progress'):
                kwargs['progress'](50, 'detail')
            return {'failures': []}
        return run

    modules = {
        'artifact_editor.characters.characters': {
            'extract_characters': operation('characters'),
            'assign_phrase_speakers': operation('speakers'),
            'extract_all_description_timelines': operation('appearances')},
        'artifact_editor.props.props': {'extract_props': operation('props')},
        'artifact_editor.images.images': {'generate_missing_metadata': operation('metadata')},
        'artifact_editor.phrases.phrases': {'generate_all_audio': operation('audio')},
        'artifact_editor.chapter.timeline': {'place_image_slots': operation('slots')},
        'artifact_editor.chapter.workflow': {'complete_step': lambda chapter, action: completed.append(action)},
        'gpu_tasks': {'task_label': lambda label: nullcontext()},
    }
    for name, attributes in modules.items():
        module = ModuleType(name)
        module.__dict__.update(attributes)
        monkeypatch.setitem(sys.modules, name, module)
    source = Path('src/websocket/server.py').read_text()
    function = next(n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == 'run_chapter_add_all')
    namespace = {'Lock': Lock}
    exec(compile(ast.Module(body=[function], type_ignores=[]), '<workflow>', 'exec'), namespace)
    chapter = SimpleNamespace(textoviva=SimpleNamespace(
        generate_missing_images=operation('images'),
        generate_missing_animations=operation('animations')))
    report = Mock()
    namespace['run_chapter_add_all'](chapter, report, step_offset=1, total_steps=12)
    assert completed == ['chapter_missing_characters', 'chapter_assign_characters',
                         'chapter_character_appearances', 'chapter_missing_audio',
                         'chapter_place_image_slots', 'chapter_missing_props',
                         'chapter_missing_metadata', 'chapter_missing_images',
                         'chapter_missing_animations']
    assert calls[-3:] == ['animations', 'images', 'animations']
    updates = [call.args for call in report.call_args_list]
    assert updates[0] == (0, 'Step 2/12: Adding missing characters')
    assert updates[-1] == (100, 'Step 11/12: Generating missing animations complete')
    assert any('Step 3/12: Assigning characters — detail' == message for _, message in updates)
    assert [percent for percent, _ in updates] == sorted(percent for percent, _ in updates)


def test_one_shot_uses_cpu_coordinator_instead_of_gpu_queue():
    import json
    from task_queue import enqueue_task, VIDEO_QUEUE, RENDER_QUEUE

    client = Mock()
    enqueue_task(client, VIDEO_QUEUE, {
        'job_id': 'one-shot-routing', 'action': 'textoviva_one_shot'})
    queue, payload = client.lpush.call_args.args
    task = json.loads(payload)
    assert queue == RENDER_QUEUE
    assert task['execution_queue'] == 'render'
    assert task['provider'] == 'none'
    status = client.hset.call_args.kwargs['mapping']
    assert status['state'] == 'queued'
    assert status['action'] == 'textoviva_one_shot'
