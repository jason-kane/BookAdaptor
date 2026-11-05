"""Redis restoration must delay worker startup rather than crash it."""
import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import redis


def load_wait(sleep, log):
    path = Path(__file__).resolve().parents[1] / 'src/websocket/server.py'
    node = next(n for n in ast.parse(path.read_text()).body
                if isinstance(n, ast.FunctionDef) and n.name == 'wait_for_worker_redis')
    scope = dict(redis=redis, time=SimpleNamespace(sleep=sleep), log=log)
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), scope)
    return scope['wait_for_worker_redis']


def test_waits_through_loading_and_connection_failures_without_log_spam():
    client, sleep, log = Mock(), Mock(), Mock()
    client.ping.side_effect = [redis.ConnectionError(), redis.exceptions.BusyLoadingError(),
                               redis.exceptions.BusyLoadingError(), redis.TimeoutError(), True]
    load_wait(sleep, log)(client)
    assert client.ping.call_count == 5
    assert sleep.call_count == 4
    sleep.assert_called_with(2)
    log.warning.assert_called_once()
    log.info.assert_called_once()


def test_ready_redis_does_not_delay_startup():
    client, sleep, log = Mock(), Mock(), Mock()
    load_wait(sleep, log)(client)
    sleep.assert_not_called()
    log.warning.assert_not_called()


def test_permanent_redis_errors_are_not_retried():
    client, sleep, log = Mock(), Mock(), Mock()
    client.ping.side_effect = redis.exceptions.AuthenticationError('bad credentials')
    with pytest.raises(redis.exceptions.AuthenticationError):
        load_wait(sleep, log)(client)
    sleep.assert_not_called()
