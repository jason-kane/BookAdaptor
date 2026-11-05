"""Director room actions exercise HTTP and persisted state without GPU workers."""
import json
import pytest
from bs4 import BeautifulSoup


def saved(ui):
    return ui.views._read_project(ui.project_id)


def update(ui, **changes):
    data = saved(ui)
    data.update(changes)
    ui.views._save_project(ui.project_id, data)


def test_index_and_director_room_expose_all_project_controls(theater_ui):
    ui = theater_ui
    index = ui.client.get('/theater/')
    assert index.status_code == 200
    assert ui.url in index.text
    response = ui.client.get(ui.url)
    assert response.status_code == 200
    soup = BeautifulSoup(response.data, 'html.parser')
    room = soup.select_one('[data-chat-url]')
    for attribute, suffix in {
        'configure': 'configure', 'reset': 'reset', 'reset-images': 'reset-images',
        'read': 'read', 'chat': 'chat', 'create-script': 'script/create',
        'rough-draft': 'rough-draft', 'rough-film': 'rough-film',
        'final-render': 'final-render', 'script': 'script',
    }.items():
        assert room[f'data-{attribute}-url'] == f'{ui.url}/{suffix}'
    assert soup.select_one('#styler-job-queue')


@pytest.mark.parametrize('data,status', [({}, 400), ({'raw_story': 'story'}, 400),
    ({'raw_story': 'story', 'director_profile': 'missing'}, 400)])
def test_creation_requires_story_and_valid_director(theater_ui, data, status):
    assert theater_ui.client.post('/theater/projects', data=data).status_code == status
    assert len(list(theater_ui.root.iterdir())) == 1


def test_read_story_is_cached_and_persists_understanding(theater_ui):
    ui = theater_ui
    ui.gpu.submit.return_value = json.dumps({'reply': 'A threshold.', 'film_title': 'Threshold', 'story_read': {'summary': 'A door at sea.'}})
    first = ui.client.post(ui.url + '/read')
    assert first.status_code == 200 and first.json['cached'] is False
    assert ui.client.post(ui.url + '/read').json['cached'] is True
    ui.gpu.submit.assert_called_once()
    assert ui.gpu.submit.call_args.kwargs['priority'] is True
    assert saved(ui)['story_read']['summary'] == 'A door at sea.'


def test_chat_persists_conversation_without_changing_script(theater_ui):
    ui = theater_ui
    ui.gpu.submit.return_value = json.dumps({'reply': 'Open the door.', 'script': {'scenes': [{'id': 'one', 'shots': []}]}})
    response = ui.client.post(ui.url + '/chat', json={'message': 'Make the threshold mysterious.'})
    assert response.status_code == 200
    assert response.json['reply'] == 'Open the door.'
    assert saved(ui)['script']['scenes'] == []
    assert saved(ui)['conversation'][-1]['content'] == 'Open the door.'


def test_read_along_configuration_persists(theater_ui):
    ui = theater_ui
    assert ui.client.post(ui.url + '/configure', data={'read_along': 'on'}).json == {'ok': True}
    assert saved(ui)['read_along'] is True
    ui.client.post(ui.url + '/configure')
    assert saved(ui)['read_along'] is False


@pytest.mark.parametrize('payload', [[], {'scenes': 'invalid'}])
def test_script_editor_rejects_invalid_structure(theater_ui, payload):
    ui = theater_ui
    before = saved(ui)['script']
    assert ui.client.post(ui.url + '/script', json=payload).status_code == 400
    assert saved(ui)['script'] == before


def test_script_editor_persists_changes(theater_ui):
    ui = theater_ui
    script = {'scenes': [{'id': 'scene-one', 'shots': []}]}
    assert ui.client.post(ui.url + '/script', json=script).json == {'ok': True}
    assert saved(ui)['script'] == script
    assert saved(ui)['render_status'] == 'script_ready'


def test_create_script_persists_revision_and_archives_previous_cut(theater_ui):
    ui = theater_ui
    update(ui, story_read={'summary': 'A threshold'}, title='Threshold')
    (ui.folder / 'rough-draft.mp4').write_bytes(b'old cut')
    ui.gpu.submit.return_value = json.dumps({'reply': 'Script ready.', 'script': {'scenes': [], 'story_alignment': {'source_beats_preserved': ['door']}}})
    response = ui.client.post(ui.url + '/script/create')
    assert response.status_code == 200
    assert response.json['previous_cut_archived'] is True
    assert saved(ui)['render_status'] == 'script_ready'
    assert saved(ui)['script']['source_alignment']['source_beats_preserved'] == ['door']
    assert not (ui.folder / 'rough-draft.mp4').exists()
    assert next((ui.folder / 'previous-cuts').glob('*/rough-draft.mp4')).read_bytes() == b'old cut'


@pytest.mark.parametrize('action', ['rough-draft', 'rough-film', 'final-render'])
def test_render_requires_named_film(theater_ui, action):
    ui = theater_ui
    assert ui.client.post(ui.url + '/' + action).status_code == 409
    ui.queue.enqueue_task.assert_not_called()


@pytest.mark.parametrize('action,stage,task_action', [
    ('rough-draft', 'rough', 'textoviva_rerender'),
    ('rough-film', 'rough_film', 'textoviva_rerender'),
    ('final-render', 'final', 'textoviva_missing_animation')])
def test_render_controls_queue_correct_stage(theater_ui, action, stage, task_action):
    ui = theater_ui
    update(ui, title='Threshold')
    ui.scope['read_chapter_source'] = lambda _: object()
    (ui.folder / 'rough-draft.mp4').write_bytes(b'cut')
    response = ui.client.post(ui.url + '/' + action)
    assert response.status_code == 200
    task = ui.queue.enqueue_task.call_args.args[2]
    assert task['theater_stage'] == stage and task['action'] == task_action
    assert task['theater_project_id'] == ui.project_id and task['priority'] is True
    assert saved(ui)['render_job_id'] == response.json['job_id']
    ui.workers.ensure_worker_services.assert_called_once()


@pytest.mark.parametrize('action', ['rough-film', 'final-render'])
def test_animation_render_requires_rough_cut(theater_ui, action):
    ui = theater_ui
    update(ui, title='Threshold')
    assert ui.client.post(ui.url + '/' + action).status_code == 409
    ui.queue.enqueue_task.assert_not_called()


def test_render_queue_failure_is_visible_and_persisted(theater_ui):
    ui = theater_ui
    update(ui, title='Threshold')
    ui.queue.enqueue_task.side_effect = RuntimeError('queue unavailable')
    response = ui.client.post(ui.url + '/rough-draft')
    assert response.status_code == 500
    assert 'queue unavailable' in response.json['error']
    assert saved(ui)['render_status'] == 'render_failed'
    ui.scope['redis'].Redis.return_value.close.assert_called_once()


@pytest.mark.parametrize('variant,filename', [('rough', 'rough-draft.mp4'), ('final', 'final.mp4')])
def test_media_and_render_status_show_available_cuts(theater_ui, variant, filename):
    ui = theater_ui
    (ui.folder / filename).write_bytes(b'film bytes')
    response = ui.client.get(ui.url + f'/media/{variant}.mp4')
    assert response.status_code == 200 and response.mimetype == 'video/mp4'
    assert response.data == b'film bytes'
    status = ui.client.get(ui.url + '/render-status')
    assert status.status_code == 200
    assert status.json[f'{variant}_exists'] is True
    assert status.json[f'{variant}_version']


@pytest.mark.parametrize('variant', ['rough', 'final', 'other'])
def test_missing_media_is_404(theater_ui, variant):
    assert theater_ui.client.get(theater_ui.url + f'/media/{variant}.mp4').status_code == 404


@pytest.mark.parametrize('action', ['reset', 'reset-images', 'script/create'])
def test_reset_and_script_revision_are_blocked_during_render(theater_ui, action):
    ui = theater_ui
    update(ui, render_status='rough_rendering')
    assert ui.client.post(ui.url + '/' + action).status_code == 409
    assert saved(ui)['render_status'] == 'rough_rendering'


def test_reset_retains_source_and_director_but_removes_generated_files(theater_ui):
    ui = theater_ui
    update(ui, title='Threshold', script={'scenes': [{'id': 'one'}]})
    (ui.folder / 'rough-draft.mp4').write_bytes(b'old')
    (ui.folder / 'chapter').mkdir()
    response = ui.client.post(ui.url + '/reset')
    assert response.status_code == 200
    data = saved(ui)
    assert data['raw_story'] == 'A traveler finds a door at sea.'
    assert data['director_profile'] == ui.profile
    assert data['title'] == '' and data['script']['scenes'] == []
    assert sorted(p.name for p in ui.folder.iterdir()) == ['project.json']


def test_image_reset_archives_cuts_and_retains_script_and_narration(theater_ui):
    ui = theater_ui
    script = {'scenes': [{'id': 'one'}]}
    update(ui, script=script)
    (ui.folder / 'rough-draft.mp4').write_bytes(b'old')
    response = ui.client.post(ui.url + '/reset-images')
    assert response.status_code == 200
    assert response.json['previous_cut_archived'] is True
    assert saved(ui)['script'] == script
    assert saved(ui)['render_status'] == 'script_ready'
    assert next((ui.folder / 'previous-cuts').glob('*/rough-draft.mp4')).read_bytes() == b'old'
