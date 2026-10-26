"""Panel snapshots query Compose without starting services or trusting URL selectors."""
import json
import subprocess
from unittest.mock import patch

import pytest

from home.app import create_app
from home.service_info import _records, snapshot


def test_compose_json_array_and_line_formats():
    for text in ('[{"Service":"redis"},{"Service":"redisinsight"}]',
                 '{"Service":"redis"}\n{"Service":"redisinsight"}'):
        assert [item['Service'] for item in _records(text)] == ['redis', 'redisinsight']
    assert _records('') == []
    assert _records('{"Service":"redis"}') == [{'Service': 'redis'}]


def test_snapshot_preserves_stopped_missing_and_multiple_containers():
    containers = [
        {'Service': 'redis', 'Name': 'redis-1', 'State': 'running'},
        {'Service': 'redis', 'Name': 'redis-2', 'State': 'running'},
        {'Service': 'redisinsight', 'Name': 'insight', 'State': 'exited', 'ExitCode': 2},
        {'Service': 'unrelated', 'State': 'running'},
    ]
    stats = [{'Name': 'redis-1', 'CPUPerc': '1%', 'MemUsage': '3MiB / 1GiB'}]
    with patch('home.service_info.compose_command', return_value=['docker', 'compose']), patch(
        'home.service_info.subprocess.run', side_effect=[
            subprocess.CompletedProcess([], 0, json.dumps(containers)),
            subprocess.CompletedProcess([], 0, json.dumps(stats)),
        ]
    ) as run:
        result = snapshot(('redis', 'redisinsight', 'not-created'))
    assert run.call_count == 2
    for call in run.call_args_list:
        assert 'up' not in call.args[0]
        assert call.kwargs['timeout'] == 15
    assert run.call_args_list[0].args[0][-3:] == ['redis', 'redisinsight', 'not-created']
    redis, insight, absent = result['services']
    assert len(redis['containers']) == 2
    assert redis['stats'] == stats
    assert insight['containers'][0]['ExitCode'] == 2
    assert insight['stats'] == []
    assert absent['containers'] == []
    assert result['checked_at'].endswith('+00:00')


def test_stats_failure_keeps_available_status():
    with patch('home.service_info.compose_command', return_value=['docker', 'compose']), patch(
        'home.service_info.subprocess.run', side_effect=[
            subprocess.CompletedProcess([], 0, '[{"Service":"redis","State":"running"}]'),
            subprocess.TimeoutExpired('docker', 15),
        ]
    ):
        result = snapshot(('redis',))['services'][0]
    assert result['containers'][0]['State'] == 'running'
    assert result['stats_error']


def test_each_panel_has_info_and_declared_service_mapping():
    app = create_app({'TESTING': True})
    with patch('home.service_info.snapshot') as query:
        page = app.test_client().get('/').get_data(as_text=True)
    query.assert_not_called()
    assert page.count('<wa-tab panel="info">Info</wa-tab>') == 8
    for name in app.extensions['home_panel_services']:
        assert f'/programs/panels/{name}/info' in page
    assert app.extensions['home_panel_services']['redis'] == ('redis', 'redisinsight')


def test_info_route_uses_panel_services_and_escapes_data():
    client = create_app({'TESTING': True}).test_client()
    data = {'checked_at': '2026-10-23T21:00:00+00:00', 'services': [
        {'name': 'redis', 'containers': [{'Name': '<script>alert(1)</script>',
         'State': 'exited', 'Image': 'redis:8', 'ExitCode': 1}], 'stats': []},
        {'name': 'redisinsight', 'containers': [], 'stats': []},
    ]}
    with patch('home.service_info.snapshot', return_value=data) as query:
        response = client.get('/programs/panels/redis/info?services=home')
    query.assert_called_once_with(('redis', 'redisinsight'))
    assert response.status_code == 200
    assert response.headers['Cache-Control'] == 'no-store'
    assert b'<script>' not in response.data
    assert b'&lt;script&gt;' in response.data
    assert b'No container created.' in response.data
    assert b'Raw Compose data' in response.data
    with patch('home.service_info.snapshot') as query:
        assert client.get('/programs/panels/arbitrary/info').status_code == 404
    query.assert_not_called()


@pytest.mark.parametrize('error', [OSError('unavailable'),
    subprocess.CalledProcessError(1, 'docker'), subprocess.TimeoutExpired('docker', 15)])
def test_compose_errors_are_retryable(error):
    client = create_app({'TESTING': True}).test_client()
    with patch('home.service_info.snapshot', side_effect=error):
        response = client.get('/programs/panels/redis/info')
    assert response.status_code == 503
    assert response.headers['Cache-Control'] == 'no-store'
