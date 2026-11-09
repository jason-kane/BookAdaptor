import json
import ast
from pathlib import Path
from unittest.mock import Mock

import pytest

from artifact_editor.library import transfer_events as events
from artifact_editor.library.storage import status_key


@pytest.mark.parametrize('flags,expected', [('', 'Kh$'), ('Ex', 'ExKh$'), ('KA', None), ('Kh$', None)])
def test_notification_setup_preserves_existing_configuration(flags, expected):
    client = Mock()
    client.config_get.return_value = {'notify-keyspace-events': flags}
    events.enable_notifications(client)
    if expected is None:
        client.config_set.assert_not_called()
    else:
        client.config_set.assert_called_once_with('notify-keyspace-events', expected)


def test_cancelled_cpu_job_settles_transfer_without_http_polling():
    client = Mock()
    key = status_key('Writer', 'Book')
    client.hgetall.return_value = dict(author='Writer', title='Book', state='queued', job_id='move')
    task = dict(action='move_book', chapter=dict(author='Writer', title='Book'),
                job_id='move', state='cancelled', message='Cancelled by user')
    client.get.return_value = json.dumps(task)
    status = events.event_snapshot(client, {'channel': '__keyspace@0__:cpu_job:move'})
    assert status['state'] == 'cancelled'
    client.hset.assert_called_once_with(key, mapping=status)
    client.delete.assert_called_once()


def test_old_cpu_outcome_does_not_replace_new_move():
    client = Mock()
    old = dict(action='move_book', chapter=dict(author='Writer', title='Book'), job_id='old', state='failed')
    client.get.side_effect = lambda key: json.dumps(old if key.endswith(':old') else {'state': 'running'})
    client.hgetall.return_value = dict(author='Writer', title='Book', state='running', job_id='new')
    assert events.event_snapshot(client, {'channel': '__keyspace@0__:cpu_job:old'}) is None
    client.hset.assert_not_called()


def test_socket_subscribes_then_sends_snapshot_and_pushes_changes():
    class Disconnected(Exception):
        pass
    client, ws, pubsub = Mock(), Mock(), Mock()
    client.pubsub.return_value = pubsub
    client.config_get.return_value = {'notify-keyspace-events': 'Kh$'}
    key = status_key('Writer', 'Book')
    client.scan_iter.return_value = [key]
    client.hgetall.side_effect = [dict(author='Writer', title='Book', state='running', bytes_done='10'),
                                dict(author='Writer', title='Book', state='complete', bytes_done='100')]
    pubsub.get_message.side_effect = [dict(type='psubscribe'), dict(type='psubscribe'),
                                      dict(type='pmessage', channel='__keyspace@0__:' + key, data='hset')]
    ws.receive.side_effect = [None, Disconnected()]
    with pytest.raises(Disconnected):
        events.serve(ws, client)
    messages = [json.loads(call.args[0]) for call in ws.send.call_args_list]
    assert [message['state'] for message in messages] == ['running', 'complete']
    assert all(message['type'] == 'library_transfer' for message in messages)
    assert pubsub.get_message.call_args_list[1].kwargs == {'timeout': 5}
    pubsub.psubscribe.assert_called_once_with(events.STATUS_PATTERN, events.CPU_PATTERN)
    assert client.hgetall.call_count == 2  # initial snapshot + one notification
    pubsub.close.assert_called_once()
    client.close.assert_called_once()


@pytest.mark.parametrize('disconnected', [True, False])
def test_socket_route_distinguishes_disconnect_from_failure(monkeypatch, disconnected):
    from simple_websocket import ConnectionClosed
    error = ConnectionClosed(1005) if disconnected else RuntimeError('Redis unavailable')
    monkeypatch.setattr(events, 'serve', Mock(side_effect=error))
    source = Path(__file__).parents[1] / 'src/websocket/server.py'
    node = next(node for node in ast.parse(source.read_text()).body
                if isinstance(node, ast.FunctionDef) and node.name == 'library_transfers_websocket')
    node.decorator_list = []
    logger = Mock()
    scope = dict(log=logger, redis_connection=Mock())
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), 'exec'), scope)
    scope['library_transfers_websocket'](Mock())
    if disconnected:
        logger.debug.assert_called_once_with('Library transfer WebSocket disconnected', reason=1005)
        logger.exception.assert_not_called()
    else:
        logger.exception.assert_called_once_with('Library transfer WebSocket failed')
