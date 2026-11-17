"""Sentence RPC labels carry chapter identity and real input-block progress."""
import ast
import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace
from unittest.mock import Mock
import uuid

import pytest
import worker_client


def server_function(name, **scope):
    path = Path(__file__).resolve().parents[1] / 'src/websocket/server.py'
    node = next(node for node in ast.parse(path.read_text()).body
                if isinstance(node, ast.FunctionDef) and node.name == name)
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), scope)
    return scope[name]


def test_client_sends_progress_as_metadata_and_restores_context(monkeypatch):
    connection = Mock()
    def response(**kwargs):
        task = json.loads(connection.send.call_args.args[0])
        return json.dumps({'job_id': task['job_id'], 'result': []})
    connection.receive.side_effect = response
    monkeypatch.delenv('VIDEO_WORKER_PROCESS', raising=False)
    monkeypatch.setitem(sys.modules, 'simple_websocket', SimpleNamespace(
        Client=SimpleNamespace(connect=lambda _: connection)))
    with worker_client.worker_task_context(chapter_number=11, text_block=24, text_blocks=180):
        worker_client.sentence_pipeline('Hello.')
    task = json.loads(connection.send.call_args.args[0])
    assert task['context'] == dict(chapter_number=11, text_block=24, text_blocks=180)
    assert task['arguments'] == {'text': 'Hello.'}
    worker_client.sentence_pipeline('Next.')
    assert 'context' not in json.loads(connection.send.call_args.args[0])


def test_gateway_persists_progress_and_queue_view_preserves_it(monkeypatch):
    client, ws = Mock(), Mock()
    job_id = uuid.uuid4().hex
    ws.receive.return_value = json.dumps(dict(job_id=job_id, operation='sentences',
        arguments={'text':'Hello.'}, context=dict(chapter_number=11, text_block=24, text_blocks=180)))
    client.blpop.return_value = ('key', json.dumps(dict(job_id=job_id, result=[])))
    enqueue = Mock()
    handler = server_function('handle_worker_rpc', json=json, uuid=uuid, time=time,
        redis_connection=lambda: client, enqueue_task=enqueue, VIDEO_QUEUE='gpu')
    handler(ws)
    task = enqueue.call_args.args[2]
    assert task['job_type'] == 'Splitting chapter 11 into sentences · block 24 of 180'
    assert task['arguments'] == {'text':'Hello.'}
    monkeypatch.setitem(sys.modules, 'task_queue', SimpleNamespace(task_provider=lambda _: 'python'))
    monkeypatch.setitem(sys.modules, 'task_timing', SimpleNamespace(prepare=lambda task: task))
    entry = server_function('styler_queue_entry')(task)
    assert entry['chapter_number'] == 11
    assert entry['text_block'] == 24 and entry['text_blocks'] == 180
    assert entry['label'] == task['job_type']


@pytest.mark.parametrize('context', [[], {'chapter_number':True}, {'text_block':1},
    {'text_block':3,'text_blocks':2}, {'text_block':0,'text_blocks':2}])
def test_gateway_rejects_invalid_progress_without_queueing(context):
    client, ws, enqueue = Mock(), Mock(), Mock()
    ws.receive.return_value = json.dumps(dict(job_id=uuid.uuid4().hex, operation='sentences',
        arguments={'text':'Hello.'}, context=context))
    handler = server_function('handle_worker_rpc', json=json, uuid=uuid, time=time,
        redis_connection=lambda: client, enqueue_task=enqueue, VIDEO_QUEUE='gpu')
    handler(ws)
    enqueue.assert_not_called()
    assert 'error' in json.loads(ws.send.call_args.args[0])


def test_render_sentence_rpc_uses_queue_without_browser_gateway(monkeypatch):
    import cpu_queue
    import gpu_tasks
    import task_queue
    client = Mock()
    submitted = []
    def enqueue(client, queue, task):
        submitted.append((queue, task))
    client.blpop.side_effect = lambda *a, **kw: ('result', json.dumps({
        'job_id': submitted[-1][1]['job_id'],
        'result': [{'text': 'Hello.', 'text_with_ws': 'Hello. '}],
    }))
    monkeypatch.setattr(cpu_queue, 'connection', lambda: client)
    monkeypatch.setattr(cpu_queue, 'cancellation_requested', lambda *args: False)
    monkeypatch.setattr(task_queue, 'enqueue_task', enqueue)
    monkeypatch.delenv('VIDEO_WORKER_PROCESS', raising=False)
    monkeypatch.setenv('RENDER_WORKER_PROCESS', '1')
    token = gpu_tasks.render_context.set({'parent_job_id': 'chapter-job'})
    try:
        with worker_client.worker_task_context(chapter_number=14, text_block=2, text_blocks=3):
            doc = worker_client.sentence_pipeline('Hello. ')
    finally:
        gpu_tasks.render_context.reset(token)
    queue, task = submitted[0]
    assert queue == task_queue.VIDEO_QUEUE
    assert task['operation'] == 'sentences'
    assert task['parent_job_id'] == 'chapter-job'
    assert task['job_type'] == 'Splitting chapter 14 into sentences · block 2 of 3'
    assert doc.sents[0].text_with_ws == 'Hello. '
    client.close.assert_called_once()
