"""User image requests switch providers even with background maintenance off."""
import ast
import sys
import redis
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import task_queue

ROOT = Path(__file__).resolve().parents[1]


def server_function(name, **scope):
    path = ROOT / 'src/websocket/server.py'
    node = next(node for node in ast.parse(path.read_text()).body
                if isinstance(node, ast.FunctionDef) and node.name == name)
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), scope)
    return scope[name]


def test_comfy_priority_request_is_selected_while_llama_has_background_work(monkeypatch):
    import sys
    task = {'job_id': 'clicked', 'action': 'timeline_image', 'provider': 'comfyui', 'priority': True}
    pop = Mock(return_value=task)
    monkeypatch.setitem(sys.modules, 'styler.image_tags', SimpleNamespace(pop_tag_job=pop))
    active = Mock(side_effect=AssertionError('Do not batch background work ahead of a user request'))
    dispatch = server_function('_pop_priority_task_for_provider',
        pop_gpu_service_switch=Mock(return_value=None), PRIORITY_QUEUE='priority',
        _active_task_provider=active)
    client = Mock()
    assert dispatch(client, allow_background=False) == task
    pop.assert_called_once_with(client, 'priority', allow_background=False)


def test_user_request_switches_provider_despite_other_queued_work(monkeypatch):
    import sys
    from contextlib import nullcontext
    switch = Mock(return_value=nullcontext())
    monkeypatch.setitem(sys.modules, 'gpu_service', SimpleNamespace(
        service_switching_enabled=lambda: True,
        use_gpu_service=switch))
    enqueue = Mock()
    dispatch = server_function('run_tracked_task',
        _active_task_provider=lambda _: 'llama',
        _has_queued_provider_task=lambda *_: True,
        enqueue_task=enqueue, PRIORITY_QUEUE=task_queue.PRIORITY_QUEUE,
        VIDEO_QUEUE=task_queue.VIDEO_QUEUE, TAG_QUEUE=task_queue.TAG_QUEUE,
        IDLE_QUEUE=task_queue.IDLE_QUEUE, log=Mock())
    client, callback = Mock(), Mock()
    task = {'job_id': 'clicked', 'action': 'timeline_image', 'provider': 'comfyui', 'priority': True}
    assert dispatch(client, callback, client, task) is None
    enqueue.assert_called_once_with(client, task_queue.PRIORITY_QUEUE, task,
                                    front=True, ensure_ascii=False)
    assert switch.call_args.args == (client, 'comfy')
    assert switch.call_args.kwargs['transition_task']['trigger_job_id'] == 'clicked'
    callback.assert_not_called()
    client.publish.assert_not_called()


def test_lora_captions_requeued_after_provider_switch_do_not_enter_video_renderer(monkeypatch):
    import sys
    caption_job = Mock(return_value='captioned')
    monkeypatch.setitem(sys.modules, 'artifact_editor.book.lora_captioning', SimpleNamespace(run_job=caption_job))
    dispatch = server_function('run_video_job')
    client = Mock()
    task = {'job_id': 'caption', 'action': 'lora_captions', 'provider': 'llama',
            'bookdir': 'Writer/Book', 'draft_id': 'draft'}
    assert dispatch(client, task) == 'captioned'
    caption_job.assert_called_once_with(client, task)


def test_llama_worker_runs_explicit_tag_job_with_maintenance_off(monkeypatch):
    import sys
    import pytest
    task = {'job_id': 'explicit-tag', 'action': 'styler_tag_group', 'provider': 'llama', 'style_tag': 'ink'}
    pop = Mock(return_value=task)
    monkeypatch.setitem(sys.modules, 'styler.image_tags', SimpleNamespace(pop_tag_job=pop))
    monkeypatch.setitem(sys.modules, 'styler.jobs', SimpleNamespace(
        pop_engine_sample=Mock(), pop_interactive_job=Mock()))
    monkeypatch.setitem(sys.modules, 'styler.style_scan', SimpleNamespace(pop_scan_job=Mock()))
    monkeypatch.setitem(sys.modules, 'gpu_service', SimpleNamespace(initialize_active_service=Mock()))
    monkeypatch.setitem(sys.modules, 'const', SimpleNamespace(LIBRARY_DIR='/unused'))
    class TaskStarted(BaseException):
        pass
    run = Mock(side_effect=TaskStarted)
    client = Mock()
    client.get.return_value = None
    worker = server_function('video_worker',
        os=SimpleNamespace(environ={}), redis_connection=lambda: client, wait_for_worker_redis=Mock(), redis=redis, log=Mock(),
        VIDEO_QUEUE='video', PRIORITY_QUEUE='priority', TAG_QUEUE='tags',
        IdleMaintenance=Mock(), mark_activity=Mock(), get_mode=lambda _: 'off',
        _active_task_provider=lambda _: 'llama', _pop_priority_task_for_provider=Mock(return_value=None),
        run_queued_tag_task=run)
    with pytest.raises(TaskStarted):
        worker()
    pop.assert_called_once_with(client, 'tags', None, provider='llama', allow_background=False)
    run.assert_called_once_with(client, task, '/unused')


def test_explicit_tag_job_counts_as_queued_provider_work_when_maintenance_off():
    import json
    task = {'job_id': 'explicit-tag', 'action': 'styler_tag_group', 'provider': 'llama'}
    client = Mock()
    client.lrange.side_effect = lambda queue, *_: [json.dumps(task)] if queue == 'tags' else []
    client.exists.return_value = False
    pending = server_function('_has_queued_provider_task', json=json, get_mode=lambda _: 'off',
        PRIORITY_QUEUE='priority', VIDEO_QUEUE='video', TAG_QUEUE='tags')
    assert pending(client, 'llama') is True
    task['background_category'] = 'LOST'
    assert pending(client, 'llama') is False


def test_promoted_background_job_counts_as_provider_work_while_maintenance_off():
    import json
    task = {'job_id': 'promoted-rebuild', 'action': 'styler_topic_taxonomy_rebuild',
            'provider': 'comfyui', 'idle_maintenance': True, 'promoted': True}
    client = Mock()
    client.lrange.side_effect = lambda queue, *_: [json.dumps(task)] if queue == 'priority' else []
    pending = server_function('_has_queued_provider_task', json=json, get_mode=lambda _: 'off',
        PRIORITY_QUEUE='priority', VIDEO_QUEUE='video', TAG_QUEUE='tags')
    assert pending(client, 'comfyui') is True
    task['promoted'] = False
    assert pending(client, 'comfyui') is False


def test_background_task_switches_immediately_when_active_provider_has_no_work(monkeypatch):
    from contextlib import nullcontext
    switch = Mock(return_value=nullcontext())
    monkeypatch.setitem(sys.modules, 'gpu_service', SimpleNamespace(
        service_switching_enabled=lambda: True, use_gpu_service=switch))
    enqueue = Mock()
    dispatch = server_function('run_tracked_task',
        _active_task_provider=lambda _: 'llama',
        _has_queued_provider_task=lambda *_: False,
        enqueue_task=enqueue, PRIORITY_QUEUE=task_queue.PRIORITY_QUEUE,
        VIDEO_QUEUE=task_queue.VIDEO_QUEUE, TAG_QUEUE=task_queue.TAG_QUEUE,
        IDLE_QUEUE=task_queue.IDLE_QUEUE, log=Mock())
    client, callback = Mock(), Mock()
    task = {'job_id': 'background', 'action': 'gpu_workflow', 'provider': 'comfyui'}
    assert dispatch(client, callback, client, task) is None
    switch.assert_called_once()
    assert switch.call_args.args == (client, 'comfy')
    enqueue.assert_called_once()
    callback.assert_not_called()
