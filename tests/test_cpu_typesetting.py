"""CPU routing, durable status, and asynchronous typesetting responses."""
import ast
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import flask
import pytest
import cpu_queue

ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location('cpu_typesetting', ROOT / 'src/artifact_editor/chapter/typesetting.py')
typesetting = importlib.util.module_from_spec(spec)
spec.loader.exec_module(typesetting)


def chapter():
    return SimpleNamespace(author=SimpleNamespace(name='Author'), title='Title', number=1,
                           language='english', get_aspect=lambda: 'portrait',
                           kwargs=dict(author='Author', title='Title', chapter_number=1, language='english'))


def test_enqueue_is_cpu_only_durable_before_startup_and_does_not_wait(monkeypatch):
    import worker_startup
    client = MagicMock()
    monkeypatch.setattr(cpu_queue, 'connection', lambda: client)
    startup = Mock(side_effect=RuntimeError('startup offline'))
    monkeypatch.setattr(worker_startup, 'ensure_worker_services', startup)
    job_id = typesetting.enqueue_typesetting(chapter())
    calls = client.pipeline.return_value.__enter__.return_value
    status_key, serialized = calls.set.call_args.args
    status = json.loads(serialized)
    queue, payload = calls.lpush.call_args.args
    task = json.loads(payload)
    assert queue == cpu_queue.CPU_QUEUE
    assert task['action'] == 'typeset_latex' and task['chapter']['aspect'] == 'portrait'
    assert status_key == f'cpu_job:{job_id}' and status['state'] == 'queued'
    assert task['timing_key'] == status['timing_key']
    calls.execute.assert_called_once()
    startup.assert_called_once_with(cpu_only=True)
    client.hgetall.assert_not_called()  # no request-thread wait for compilation
    client.close.assert_called_once()


@pytest.mark.parametrize('error', [None, ValueError('Formatting differs from phrase 1')])
def test_worker_retains_typesetting_outcome_and_records_timing(monkeypatch, error):
    client = MagicMock()
    client.hget.return_value = None
    execute = Mock(side_effect=error)
    monkeypatch.setattr(cpu_queue, 'execute', execute)
    job = dict(job_id='job', batch_id='batch', action='typeset_latex', track_status=True,
               chapter={}, arguments={}, label='Typeset LaTeX')
    cpu_queue.run_task(client, json.dumps(job), '1', {}, 'claim:1')
    pipe = client.pipeline.return_value.__enter__.return_value
    key, raw = pipe.set.call_args.args
    status = json.loads(raw)
    assert key == 'cpu_job:job'
    assert status['state'] == ('failed' if error else 'complete')
    assert status['duration_seconds'] >= 0
    assert 'arguments' not in status
    if error:
        assert status['message'] == str(error)
        assert pipe.lpush.call_args.args[0] == cpu_queue.FAILURES
    else:
        assert pipe.lpush.call_args.args[0] == cpu_queue.RECENT
    client.eval.assert_called_once()  # durable timing record
    pipe.lrem.assert_called_once_with('claim:1', 1, json.dumps(job))


def status_app(monkeypatch, state='queued'):
    app = flask.Flask(__name__, template_folder=str(ROOT / 'src/artifact_editor/chapter/templates'))
    scope = dict(flask=flask, g=flask.g, json=json, render_template=flask.render_template,
                 url_for=flask.url_for, enqueue_typesetting=Mock(return_value='job'))
    source = ROOT / 'src/artifact_editor/chapter/views.py'
    nodes = [n for n in ast.parse(source.read_text()).body if isinstance(n, ast.FunctionDef)
             and n.name in ('typeset_latex', 'typeset_latex_status')]
    for node in nodes:
        node.decorator_list = []
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), 'exec'), scope)
    client = Mock()
    task = dict(job_id='job', action='typeset_latex', state=state,
                chapter=dict(author='Author', title='Title', chapter_number=1, language='english'),
                arguments={}, message='Bad formatting' if state == 'failed' else '')
    client.get.side_effect = lambda key: json.dumps(task) if key == 'cpu_job:job' else None
    client.hgetall.return_value = {}
    client.lrange.return_value = []
    monkeypatch.setattr(cpu_queue, 'connection', lambda: client)
    app.before_request(lambda: setattr(flask.g, 'chapter', chapter()))
    app.add_url_rule('/typeset', view_func=scope['typeset_latex'], methods=['POST'])
    app.add_url_rule('/status/<job_id>', view_func=scope['typeset_latex_status'])
    app.add_url_rule('/chapter.pdf', endpoint='library.book.chapter.typography.serve_chapter_pdf', view_func=lambda: '')
    return app, scope, task


def test_manual_action_returns_queued_cpu_task_and_polls_completion(monkeypatch):
    app, scope, _ = status_app(monkeypatch)
    response = app.test_client().post('/typeset')
    assert response.status_code == 202
    assert 'load delay:1s' in response.text
    assert 'Queued in CPU Queue' in response.text
    assert 'chapter-typeset-complete' not in response.headers.get('HX-Trigger', '')
    scope['enqueue_typesetting'].assert_called_once()


@pytest.mark.parametrize('state', ['complete', 'failed'])
def test_terminal_status_stops_polling_and_refreshes_only_on_success(monkeypatch, state):
    app, _, _ = status_app(monkeypatch, state)
    response = app.test_client().get('/status/job')
    assert response.status_code == 200
    assert 'hx-get=' not in response.text
    assert ('chapter-typeset-complete' in response.headers.get('HX-Trigger', '')) is (state == 'complete')
    if state == 'failed':
        assert 'Bad formatting' in response.text


def test_status_cannot_refresh_a_different_chapter(monkeypatch):
    app, _, task = status_app(monkeypatch)
    task['chapter']['chapter_number'] = 9
    assert app.test_client().get('/status/job').status_code == 404
