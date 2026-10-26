from unittest.mock import patch
import subprocess

from home.app import create_app
from home import programs


def test_members_have_independent_action_and_ui_rows():
    page = create_app({'TESTING': True}).test_client().get('/').get_data(as_text=True)
    assert 'data-member-start="redis"' in page
    assert 'data-start-url="/programs/panels/redis/members/redis/start"' in page
    assert 'data-member-select' not in page
    assert 'data-member-detail="redisinsight" aria-label="Redis Insight"' in page
    for service in ('redis', 'redisinsight'):
        row = page.split(f'data-member-row="{service}"', 1)[1].split('data-member-row=', 1)[0]
        assert f'data-member-start="{service}"' in row
        assert f'data-member-detail="{service}"' in row
        assert ' hidden' not in row.split('<section', 1)[1].split('>', 1)[0]
    assert 'id="redis-member-redis"' in page
    assert 'id="redis-member-redisinsight"' in page
    assert 'hx-get="/programs/redis/panel-status"' in page
    assert page.count('data-service-members') == 8


def test_member_start_is_scoped_to_its_panel():
    client = create_app({'TESTING': True}).test_client()
    with patch('home.programs.start_service') as start:
        assert client.post('/programs/panels/redis/members/redis/start').json == {'started': True}
        start.assert_called_once_with('redis', ('redis', 'redisinsight'))
    with patch('home.programs.start_service') as start:
        assert client.post('/programs/panels/redis/members/artifactserver/start').status_code == 404
        assert client.post('/programs/panels/missing/members/redis/start').status_code == 404
    start.assert_not_called()


def test_open_members_are_explicit_ui_entries_not_dependencies():
    app = create_app({'TESTING': True})
    members = app.extensions['home_panel_members']
    assert members['library'] == ('artifactserver',)
    assert members['tests'] == ('test',)
    assert members['redis'] == ('redis', 'redisinsight')
    assert app.extensions['home_panel_services']['library'] == ('artifactserver', 'redis')
    page = app.test_client().get('/').get_data(as_text=True)
    assert page.count('data-member-row="redis"') == 1
    assert 'data-member-row="nginx"' not in page
    for action in ('start', 'stop'):
        assert app.test_client().post(f'/programs/panels/library/members/redis/{action}').status_code == 404


def test_member_start_error_is_reported():
    client = create_app({'TESTING': True}).test_client()
    with patch('home.programs.start_service', side_effect=RuntimeError('daemon offline')):
        response = client.post('/programs/panels/redis/members/redis/start')
    assert response.status_code == 503
    assert 'Unable to start' in response.json['error']


def test_member_stop_is_scoped_and_errors_are_reported():
    client = create_app({'TESTING': True}).test_client()
    with patch('home.programs.stop_service') as stop:
        assert client.post('/programs/panels/redis/members/redis/stop').json == {'stopped': True}
        stop.assert_called_once_with('redis', ('redis', 'redisinsight'))
    with patch('home.programs.stop_service') as stop:
        assert client.post('/programs/panels/redis/members/artifactserver/stop').status_code == 404
        assert client.post('/programs/panels/missing/members/redis/stop').status_code == 404
        stop.assert_not_called()
    with patch('home.programs.stop_service', side_effect=RuntimeError('offline')):
        response = client.post('/programs/panels/redis/members/redis/stop')
        assert response.status_code == 503
        assert 'Unable to stop' in response.json['error']


def test_member_stop_only_targets_its_compose_service_containers():
    container = {'Id': 'redis-container'}
    with patch('home.programs._docker', side_effect=[
        {'Config': {'Labels': {'com.docker.compose.project': 'example'}}},
        [container], None
    ]) as docker:
        programs.stop_service('redis', ('redis', 'redisinsight'))
    assert 'com.docker.compose.service%3Dredis' in docker.call_args_list[1].args[1]
    assert docker.call_args.args == ('POST', '/containers/redis-container/stop?t=5')
    with patch('home.programs._docker') as docker:
        try:
            programs.stop_service('artifactserver', ('redis',))
        except ValueError:
            pass
        else:
            raise AssertionError('Unexpected member was accepted')
        docker.assert_not_called()


def test_raw_members_use_compose_and_tests_enable_their_profile():
    with patch.object(programs, 'compose_command', return_value=['docker', 'compose']), patch.object(
        programs.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)
    ) as run:
        programs.start_service('redis', ('redis',))
        assert run.call_args.args[0] == ['docker', 'compose', 'up', '-d', '--no-build', 'redis']
        programs.start_service('test', ('test',))
        assert run.call_args.args[0] == ['docker', 'compose', '--profile', 'test', 'up', '-d', '--no-build', 'test']
    with patch.object(programs, 'compose_command') as command:
        try:
            programs.start_service('nginx', ('redis',))
        except ValueError:
            pass
        else:
            raise AssertionError('Unexpected member was accepted')
    command.assert_not_called()
