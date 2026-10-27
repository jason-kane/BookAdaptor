import importlib.util
from pathlib import Path
from unittest.mock import Mock, patch

import httpx
import pytest
from home.app import create_app

spec = importlib.util.spec_from_file_location('worker_startup', Path(__file__).resolve().parents[1] / 'src/worker_startup.py')
startup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(startup)


def test_startup_starts_consumers_and_waits_for_comfy_before_enqueue():
    with patch.object(startup.httpx, 'post') as post, patch.object(startup.httpx, 'get', side_effect=[
        Mock(json=lambda: {'ready': False}), Mock(json=lambda: {'ready': True})
    ]) as get, patch.object(startup.time, 'sleep') as sleep:
        startup.ensure_worker_services(comfyui=True)
    assert post.call_args.kwargs['json'] == {'comfyui': True}
    assert post.call_args.args[0].endswith('/programs/workers/start')
    assert get.call_count == 2
    sleep.assert_called_once_with(1)


def test_metadata_only_starts_consumers_and_startup_failure_is_reported():
    with patch.object(startup.httpx, 'post') as post, patch.object(startup.httpx, 'get') as get:
        startup.ensure_worker_services()
    assert post.call_args.kwargs['json'] == {'comfyui': False}
    get.assert_not_called()
    with patch.object(startup.httpx, 'post', side_effect=httpx.ConnectError('offline')):
        with pytest.raises(RuntimeError, match='Unable to start generation services'):
            startup.ensure_worker_services()


def test_home_starts_only_fixed_generation_services():
    client = create_app({'TESTING': True}).test_client()
    with patch('home.programs.start_services') as start:
        assert client.post('/programs/workers/start', json={'comfyui': True, 'services': ['anything']}).json == {'started': True}
        start.assert_called_once_with(
            ['video_worker', 'render_worker', 'cpu_worker', 'websocket', 'comfyui'],
            ('video_worker', 'render_worker', 'cpu_worker', 'websocket', 'comfyui'), recreate=False)
    with patch('home.programs.start_services', side_effect=RuntimeError('offline')):
        assert client.post('/programs/workers/start', json={}).status_code == 503


def test_worker_startup_never_recreates_the_websocket_serving_its_request():
    from home import programs
    with patch.object(programs, 'compose_command', return_value=['docker', 'compose']), patch.object(
        programs.subprocess, 'run'
    ) as run:
        programs.start_services(['websocket'], ('websocket',), recreate=False)
    assert run.call_args.args[0] == [
        'docker', 'compose', 'up', '-d', '--no-build', '--no-recreate', 'websocket']


def test_cpu_startup_does_not_start_model_workers_or_providers():
    with patch.object(startup.httpx, 'post') as post, patch.object(startup.httpx, 'get') as get:
        startup.ensure_worker_services(cpu_only=True)
    assert post.call_args.kwargs['json'] == {'cpu_only': True}
    get.assert_not_called()
    client = create_app({'TESTING': True}).test_client()
    with patch('home.programs.start_services') as start:
        assert client.post('/programs/workers/start', json={'cpu_only': True, 'comfyui': True}).status_code == 200
    assert start.call_args.args[0] == ['cpu_worker', 'websocket']
