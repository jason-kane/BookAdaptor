"""Goal searches persist before dispatch and create attributed, ungraded entries."""
import json
from pathlib import Path
import subprocess
from unittest.mock import patch
from uuid import uuid4

import pytest
from home.app import create_app
from home.polish import database
from home.polish.seeking import state_put, enqueue_search, jobs_snapshot, refresh_catalogue
from home.polish.worker import run_once
from home.polish.providers import generate

MODEL = {'id': 'ollama:example', 'provider': 'ollama', 'model': 'example',
         'label': 'Ollama · example', 'version': 'digest123456789'}


@pytest.fixture
def app(tmp_path):
    root = tmp_path / 'repo'
    root.mkdir()
    subprocess.run(['git', 'init', '-q', str(root)], check=True)
    (root / 'README.md').write_text('A slow thing.\n')
    app = create_app({'TESTING': True, 'TODO_DIR': str(tmp_path / 'data'), 'POLISH_WORKSPACE': str(root),
                      'OLLAMA_BASE_URL': 'http://ollama.test', 'OPENAI_API_KEY': ''})
    with app.app_context(), database() as connection:
        state_put(connection, 'models', {'models': [MODEL], 'errors': [], 'updated_at': 1})
    return app


def output(**changes):
    result = dict(kind='file', description='Make the thing fast.', path='README.md',
                  search='slow', replacement='fast', goal_id='', title='', evidence='')
    result.update(changes)
    return result


def enqueue(app, request_key=None):
    with app.app_context(), patch('home.polish.seeking.start_worker'):
        return enqueue_search('G001', MODEL['id'], request_key)


def test_acknowledgment_is_durable_and_duplicate_assignment_is_prevented(app):
    with patch('home.polish.seeking.start_worker') as start:
        def inspect_persisted():
            with app.app_context(), database() as connection:
                assert connection.execute('SELECT status FROM polish_jobs').fetchone()[0] == 'queued'
        start.side_effect = inspect_persisted
        response = app.test_client().post('/polish/goals/G001/improvements', json={'model': MODEL['id']})
    assert response.status_code == 202
    job = response.json['job']
    assert job['goal_id'] == 'G001'
    assert job['model_id'] == MODEL['id']
    assert enqueue(app)['id'] == job['id']
    with app.app_context(), database() as connection:
        assert connection.execute('SELECT COUNT(*) FROM polish_jobs').fetchone()[0] == 1
    assert app.test_client().get('/polish/jobs').json['jobs'][0]['id'] == job['id']
    assert app.test_client().post('/polish/goals/missing/improvements', json={'model': MODEL['id']}).status_code == 404
    assert app.test_client().post('/polish/goals/G001/improvements', json={'model': 'unknown'}).status_code == 400


def test_success_creates_one_review_entry_without_applying_or_rating(app):
    job = enqueue(app)
    with app.app_context(), patch('home.polish.worker.generate', return_value=(output(), {'model': 'example'})):
        assert run_once() is True
        assert run_once() is False
        with database() as connection:
            row = connection.execute('SELECT * FROM polish_jobs').fetchone()
            entry = connection.execute('SELECT * FROM entries').fetchone()
            assert row['status'] == 'completed'
            assert row['entry_id'] == entry['id']
            assert row['prompt_version'] == 'goal-improvement-v4'
            assert json.loads(row['context_json'])['sources']['README.md'] == 'A slow thing.\n'
            assert entry['goal_id'] == 'G001' and entry['reaction'] is None
            assert entry['before_text'] == 'A slow thing.\n' and entry['after_text'] == 'A fast thing.\n'
            assert entry['model_label'] == 'Ollama · example · digest123456'
            assert json.loads(entry['target_json'])['before'] == entry['before_text']
    assert (Path(app.config['POLISH_WORKSPACE']) / 'README.md').read_text() == 'A slow thing.\n'
    assert app.test_client().get('/polish/jobs').json['jobs'][0]['status'] == 'completed'


def test_goal_output_is_a_proposal_not_an_accepted_goal(app):
    enqueue(app)
    proposal = output(kind='goal', path='', search='', replacement='', title='A new goal', evidence='Observable improvement')
    with app.app_context(), patch('home.polish.worker.generate', return_value=(proposal, {})):
        run_once()
        with database() as connection:
            assert connection.execute('SELECT COUNT(*) FROM goals').fetchone()[0] == 1
            target = json.loads(connection.execute('SELECT target_json FROM entries').fetchone()[0])
            assert target == {'kind': 'goal', 'id': 'G002', 'before': None,
                              'after': {'title': 'A new goal', 'evidence': 'Observable improvement'}}


def test_invalid_or_failed_model_never_creates_a_fake_entry(app):
    enqueue(app)
    with app.app_context(), patch('home.polish.worker.generate', return_value=(output(search='not present'), {})):
        run_once()
        assert jobs_snapshot()[0]['status'] == 'failed'
        with database() as connection:
            assert connection.execute('SELECT COUNT(*) FROM entries').fetchone()[0] == 0
    enqueue(app)
    with app.app_context(), patch('home.polish.worker.generate', side_effect=RuntimeError('Provider unavailable')):
        run_once()
        assert jobs_snapshot()[0]['status'] == 'failed'


def test_network_retry_request_key_cannot_duplicate_completed_work(app):
    key = str(uuid4())
    job = enqueue(app, key)
    with app.app_context(), patch('home.polish.worker.generate', return_value=(output(), {})):
        run_once()
    assert enqueue(app, key)['id'] == job['id']
    with app.app_context(), database() as connection:
        assert connection.execute('SELECT COUNT(*) FROM polish_jobs').fetchone()[0] == 1
        assert connection.execute('SELECT COUNT(*) FROM entries').fetchone()[0] == 1


def test_worker_start_failure_is_visible_and_not_stuck_submitting(app):
    with app.app_context(), patch('home.polish.seeking.start_worker', side_effect=RuntimeError('Docker unavailable')):
        with pytest.raises(RuntimeError, match='saved'):
            enqueue_search('G001', MODEL['id'])
        assert jobs_snapshot()[0]['status'] == 'failed'


def test_discovery_keeps_providers_distinct_and_does_not_generate(app):
    def provider(url, **kwargs):
        assert kwargs.get('payload') is None
        if url.endswith('/api/tags'):
            return {'models': [{'name': 'example', 'digest': 'abc', 'capabilities': ['completion']},
                               {'name': 'embedding', 'capabilities': ['embedding']} ]}
        assert kwargs['key'] == 'test-key'
        return {'data': [{'id': 'gpt-test'}, {'id': 'text-embedding-test'}, {'id': 'gpt-audio-test'}]}
    with app.app_context(), patch('home.polish.seeking.http_json', side_effect=provider):
        app.config['OPENAI_API_KEY'] = 'test-key'
        refresh_catalogue()
    models = app.test_client().get('/polish/models').json['models']
    assert {model['id'] for model in models} == {'ollama:example', 'openai:gpt-test'}
    assert 'test-key' not in app.test_client().get('/polish/models').get_data(as_text=True)


def test_provider_adapters_use_selected_model_and_validate_structured_output(app):
    requests = []
    def provider(url, **kwargs):
        requests.append((url, kwargs))
        if url.endswith('/api/tags'):
            return {'models': [{'name': 'example', 'digest': MODEL['version']}]}
        if url.endswith('/api/chat'):
            return {'message': {'content': json.dumps(output())}, 'model': 'example', 'done_reason': 'stop'}
        return {'status': 'completed', 'model': 'gpt-test-snapshot', 'output': [
            {'content': [{'type': 'output_text', 'text': json.dumps(output())}]}]}
    with app.app_context(), patch('home.polish.providers.http_json', side_effect=provider):
        assert generate(MODEL, 'prompt')[0]['kind'] == 'file'
        app.config['OPENAI_API_KEY'] = 'test-key'
        proposal, metadata = generate({'provider': 'openai', 'model': 'gpt-test'}, 'prompt')
        assert proposal['kind'] == 'file' and metadata['model'] == 'gpt-test-snapshot'
    assert requests[1][1]['payload']['model'] == 'example'
    assert requests[1][1]['payload']['stream'] is False
    assert requests[2][1]['payload']['store'] is False
    assert requests[2][1]['payload']['text']['format']['strict'] is True


def test_context_excludes_credentials_and_runtime_data(app):
    root = Path(app.config['POLISH_WORKSPACE'])
    (root / '.env').write_text('PRIVATE KEY')
    (root / 'secret.py').write_text('PRIVATE KEY')
    (root / 'tmp').mkdir()
    (root / 'tmp' / 'private.py').write_text('PRIVATE KEY')
    enqueue(app)
    def inspect_prompt(model, prompt):
        assert 'PRIVATE KEY' not in prompt
        assert 'A slow thing.' in prompt
        return output(), {}
    with app.app_context(), patch('home.polish.worker.generate', side_effect=inspect_prompt):
        run_once()
        assert jobs_snapshot()[0]['status'] == 'completed'


def test_opening_padding_is_repaired_only_for_a_unique_literal_match():
    from home.polish.worker import exact_edit
    # Recorded failure: the model copied opening indentation that was not in source.
    before = "STOP_WORDS = {'the', 'goal', 'goals',\n              'make', 'keep'}\n"
    search = "            'goal', 'goals',\n              'make', 'keep'"
    replacement = "              'make', 'keep'"
    after, normalized = exact_edit(before, search, replacement)
    assert normalized is True
    assert after == "STOP_WORDS = {'the',   'make', 'keep'}\n"
    import ast
    ast.parse(after)
    with pytest.raises(ValueError, match='unique'):
        exact_edit('name\nname\n', '  name', '  renamed')
    with pytest.raises(ValueError, match='unique'):
        exact_edit('value = 1\n', '  value=1', '  value=2')
    with pytest.raises(ValueError, match='unique'):
        exact_edit('value = 1\n', '  value = 1', 'outdented')
    assert exact_edit('value = 1\n', 'value = 1', 'value = 2') == ('value = 2\n', False)


def test_condensation_context_targets_one_small_goal_wording_polish(app):
    from home.polish.worker import build_context
    with app.app_context():
        with database() as connection:
            connection.execute("INSERT INTO goals (id,title,evidence) VALUES ('G057','Condense goals','Reduce evaluation context while preserving intent.')")
            connection.execute("INSERT INTO goals (id,title,evidence) VALUES ('G100','A long goal title','Preserve this distinct requirement.')")
            goal = dict(connection.execute("SELECT * FROM goals WHERE id='G057'").fetchone())
        context = build_context({'goal_json':json.dumps(goal)})
    assert context['sources'] == {}
    assert 'one small wording simplification' in context['prompt']
    assert 'NOT expected to complete or solve the whole goal' in context['prompt']
    assert 'Preserve this distinct requirement.' in context['prompt']


def test_recoverable_padding_creates_a_review_entry_and_preserves_raw_output(app):
    root = Path(app.config['POLISH_WORKSPACE'])
    (root/'README.md').write_text('Text before the slow thing.\n')
    enqueue(app)
    proposal = output(search='    slow', replacement='    fast')
    with app.app_context(), patch('home.polish.worker.generate',return_value=(proposal,{})):
        run_once()
        with database() as connection:
            job = connection.execute('SELECT * FROM polish_jobs').fetchone()
            assert job['status'] == 'completed'
            saved = json.loads(job['response_json'])
            assert saved['proposal']['search'] == '    slow'
            assert 'exactly once' in saved['edit_validation']
            entry = connection.execute('SELECT * FROM entries').fetchone()
            assert entry['before_text'] == 'Text before the slow thing.\n'
            assert entry['after_text'] == 'Text before the fast thing.\n'
            assert entry['reaction'] is None
    assert (root/'README.md').read_text() == 'Text before the slow thing.\n'


def seed_goal_score(connection, goal_id, reaction=None):
    connection.execute('INSERT OR IGNORE INTO goals (id,title,evidence) VALUES (?,?,?)',
                       (goal_id, 'Goal '+goal_id, 'A small improvement.'))
    if reaction:
        connection.execute('''INSERT INTO entries (goal_id,model_label,description,before_text,after_text,reaction)
            VALUES (?,?,?,?,?,?)''',(goal_id,'Model','Earlier polish','','Result',reaction))


def test_automatic_search_uses_lowest_goal_total_and_skips_reserved_goal(app):
    with app.app_context(), database() as connection:
        seed_goal_score(connection,'G001','Awesome')
        seed_goal_score(connection,'G100','Bad')
        seed_goal_score(connection,'G200','Trash')
        seed_goal_score(connection,'G300')
    with patch('home.polish.seeking.start_worker'):
        first=app.test_client().post('/polish/rub-one-out',json={'model':MODEL['id']})
        assert first.status_code==202
        job=first.json['job']
        assert job['goal_id']=='G200'
        assert job['selection']=={'strategy':'lowest_score','score':-2.5,'tie_count':1}
        second=app.test_client().post('/polish/rub-one-out',json={'model':MODEL['id']})
        assert second.json['job']['goal_id']=='G100'
    assert app.test_client().get('/polish/jobs').json['automatic_job']['id']==second.json['job']['id']


def test_six_pack_queues_six_distinct_goals_for_one_model_and_tracks_progress(app):
    with app.app_context(),database() as connection:
        for number in range(2,8):
            seed_goal_score(connection,f'G{number:03d}')
    key=str(uuid4())
    with patch('home.polish.seeking.start_worker') as start, patch('home.polish.seeking.random.sample',side_effect=lambda values,count:list(values)[:count]):
        response=app.test_client().post('/polish/six-pack',json={'model':MODEL['id'],'request_key':key})
        assert response.status_code==202
        start.assert_called_once()
        batch=response.json['six_pack']
        assert batch['requested']==batch['created']==6
        assert len({job['goal_id'] for job in batch['jobs']})==6
        assert all(job['model_id']==MODEL['id'] and job['selection']['strategy']=='six_pack' for job in batch['jobs'])
        repeated=app.test_client().post('/polish/six-pack',json={'model':MODEL['id'],'request_key':key})
        assert repeated.json['six_pack']['batch_id']==batch['batch_id']
        different_model=app.test_client().post('/polish/six-pack',json={'model':'ollama:other','request_key':key})
        assert different_model.status_code==400
    jobs=app.test_client().get('/polish/jobs').json
    assert jobs['six_pack']['batch_id']==batch['batch_id']
    with app.app_context(),database() as connection:
        connection.execute("UPDATE polish_jobs SET status='completed' WHERE json_extract(selection_json,'$.batch_id')=?",(batch['batch_id'],))
    jobs=app.test_client().get('/polish/jobs').json
    assert jobs['six_pack']['status']=='completed' and jobs['six_pack']['completed']==6


def test_six_pack_requires_six_available_distinct_goals(app):
    with patch('home.polish.seeking.start_worker') as start:
        response=app.test_client().post('/polish/six-pack',json={'model':MODEL['id']})
        assert response.status_code==409
        assert 'six distinct available goals' in response.json['error']
        start.assert_not_called()


def test_automatic_ties_choose_only_among_matching_minimum_scores(app):
    with app.app_context(), database() as connection:
        seed_goal_score(connection,'G100')
        seed_goal_score(connection,'G200','Good')
    with (patch('home.polish.seeking.start_worker'),
          patch('home.polish.seeking.random.choice',side_effect=lambda candidates:candidates[-1]) as choose):
        response=app.test_client().post('/polish/rub-one-out',json={'model':MODEL['id']})
    assert response.json['job']['goal_id']=='G100'
    assert response.json['job']['selection']['score']==0
    assert response.json['job']['selection']['tie_count']==2
    assert [row['id'] for row in choose.call_args.args[0]]==['G001','G100']


def test_automatic_retries_do_not_reselect_after_scores_change(app):
    key=str(uuid4())
    with patch('home.polish.seeking.start_worker'):
        first=app.test_client().post('/polish/rub-one-out',json={'model':MODEL['id'],'request_key':key}).json['job']
        with app.app_context(),database() as connection:
            connection.execute("UPDATE polish_jobs SET status='completed' WHERE id=?",(first['id'],))
            seed_goal_score(connection,'G100','Trash')
        again=app.test_client().post('/polish/rub-one-out',json={'model':MODEL['id'],'request_key':key}).json['job']
    assert again['id']==first['id'] and again['goal_id']==first['goal_id']
    with app.app_context(), database() as connection:
        assert connection.execute('SELECT COUNT(*) FROM polish_jobs').fetchone()[0]==1
        assert 'score' not in json.loads(connection.execute('SELECT goal_json FROM polish_jobs').fetchone()[0])


def test_no_available_goal_does_not_duplicate_work_or_invoke_provider(app):
    enqueue(app)
    with patch('home.polish.seeking.start_worker') as start:
        response=app.test_client().post('/polish/rub-one-out',json={'model':MODEL['id']})
        assert response.status_code==409
        start.assert_not_called()
    assert app.test_client().post('/polish/rub-one-out',json={'model':'unknown'}).status_code==400


def test_concurrent_automatic_requests_reserve_different_goals(app):
    from concurrent.futures import ThreadPoolExecutor
    with app.app_context(), database() as connection:
        seed_goal_score(connection,'G100')
    def request():
        return app.test_client().post('/polish/rub-one-out',json={'model':MODEL['id']}).json['job']['goal_id']
    with (patch('home.polish.seeking.start_worker'),
          patch('home.polish.seeking.random.choice',side_effect=lambda candidates:candidates[0]),
          ThreadPoolExecutor(max_workers=2) as pool):
        futures=[pool.submit(request) for _ in range(2)]
        assert {future.result() for future in futures}=={'G001','G100'}


def test_rub_controls_appear_immediately_before_model_scores(app):
    page=app.test_client().get('/polish').get_data(as_text=True)
    assert 'Model for Rub one out' in page
    assert '>Rub one out</wa-button>' in page
    assert '>Six pack</wa-button>' in page
    assert page.index('data-six-start') < page.index('<section class="model-scores"')
    assert page.index('data-rub-start') < page.index('<section class="model-scores"')
    assert 'Rub one out' not in app.test_client().get('/polish?preview=1').get_data(as_text=True)


def test_goal_relevance_does_not_treat_history_as_story(app):
    from home.polish.worker import relevance_terms,build_context
    assert 'story' not in relevance_terms('src/styler/queue_history.py')
    assert relevance_terms('characters props places chapters')=={'character','prop','place','chapter'}
    root=Path(app.config['POLISH_WORKSPACE'])
    files=['src/styler/queue_history.py','src/artifact_editor/characters/voices.py',
           'src/artifact_editor/props/views.py','src/artifact_editor/images/scene/htmx.py',
           'src/artifact_editor/chapter/views.py']
    for name in files:
        path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('# Source\n')
    goal={'id':'G037','title':'Maintain story consistency across visual assets.',
          'evidence':'Characters, props, places, wardrobe, and scene context remain consistent and editable across chapters.'}
    with app.app_context(): context=build_context({'goal_json':json.dumps(goal)})
    assert 'src/styler/queue_history.py' not in context['sources']
    assert set(context['sources'])==set(files[1:])


def test_ambiguous_search_error_tells_the_model_how_to_correct_it():
    from home.polish.worker import exact_edit
    with pytest.raises(ValueError,match='matched 3 locations.*surrounding lines'):
        exact_edit("client.eval('''\nclient.eval('''\nclient.eval('''\n","client.eval('''","client.eval(''")
