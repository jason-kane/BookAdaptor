"""Durable goal searches and provider discovery shared by Home and its worker."""
import json
import random
import time
import uuid
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from flask import current_app
from . import database

PROMPT_VERSION = 'goal-improvement-v4'


def state_get(connection, key, default=None):
    row = connection.execute('SELECT value FROM polish_state WHERE key=?', (key,)).fetchone()
    return json.loads(row['value']) if row else default


def state_put(connection, key, value):
    connection.execute('INSERT INTO polish_state (key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                       (key, json.dumps(value)))


def start_worker():
    from home.programs import start_services
    start_services(('polish_worker',), ('polish_worker',), recreate=False)


def request_model_refresh():
    with database() as connection:
        state_put(connection, 'refresh_requested', True)
    start_worker()


def model_catalogue():
    with database() as connection:
        result = state_get(connection, 'models', {'models': [], 'errors': [], 'updated_at': None})
        result['refreshing'] = state_get(connection, 'refresh_requested', False)
    return result


def http_json(url, *, payload=None, key=None, timeout=5):
    headers = {'Content-Type': 'application/json'}
    if key:
        if '\r' in key or '\n' in key:
            raise RuntimeError('Provider credentials contain invalid characters.')
        headers['Authorization'] = 'Bearer ' + key
    request = Request(url, headers=headers, data=json.dumps(payload).encode() if payload is not None else None)
    try:
        with urlopen(request, timeout=timeout) as response:
            data = response.read(2_000_001)
    except HTTPError as error:
        raise RuntimeError(f'Provider returned HTTP {error.code}. Check its availability, model support, and credentials.') from error
    except (URLError, TimeoutError, OSError) as error:
        raise RuntimeError('Provider could not be reached or timed out.') from error
    if len(data) > 2_000_000:
        raise RuntimeError('Provider response exceeded the size limit.')
    try:
        result = json.loads(data)
        if not isinstance(result, dict):
            raise ValueError('Expected an object')
        return result
    except (ValueError, UnicodeError) as error:
        raise RuntimeError('Provider returned invalid JSON.') from error


def refresh_catalogue():
    models, errors = [], []
    try:
        data = http_json(current_app.config['OLLAMA_BASE_URL'].rstrip('/') + '/api/tags')
        for item in data.get('models', []):
            if item.get('capabilities') and 'completion' not in item['capabilities']:
                continue
            name = item.get('name')
            if isinstance(name, str):
                models.append({'id': 'ollama:' + name, 'provider': 'ollama', 'model': name,
                               'label': 'Ollama · ' + name, 'version': item.get('digest', '')})
    except RuntimeError as error:
        errors.append('Ollama: ' + str(error))
    key = current_app.config.get('OPENAI_API_KEY')
    if key:
        try:
            data = http_json('https://api.openai.com/v1/models', key=key)
            for item in data.get('data', []):
                name = item.get('id', '')
                if name.startswith(('gpt-', 'o1', 'o3', 'o4')) and not any(part in name for part in (
                    'audio', 'realtime', 'transcribe', 'tts', 'image', 'embedding', 'moderation', 'instruct', 'search')):
                    models.append({'id': 'openai:' + name, 'provider': 'openai', 'model': name,
                                   'label': 'OpenAI · ' + name, 'version': name})
        except RuntimeError as error:
            errors.append('OpenAI: ' + str(error))
    else:
        errors.append('OpenAI: configure OPENAI_API_KEY on polish_worker to enable API models.')
    with database() as connection:
        state_put(connection, 'models', {'models': sorted(models, key=lambda item: item['label']),
                                        'errors': errors, 'updated_at': time.time()})
        state_put(connection, 'refresh_requested', False)


def public_job(row, repair=None):
    result = {name: row[name] for name in ('id', 'goal_id', 'status', 'message', 'entry_id', 'created_at', 'updated_at')}
    result['model'] = json.loads(row['model_json'])['label']
    result['model_id'] = json.loads(row['model_json'])['id']
    result['goal_title'] = json.loads(row['goal_json'])['title']
    result['selection'] = json.loads(row['selection_json']) if row['selection_json'] else None
    if repair:
        result['message']=repair['message']
        if repair['status'] in ('queued','running'):
            result['status']='running'
        elif repair['status']=='failed':
            result['status']='failed'
        elif repair['result_entry_id']:
            result['entry_id']=repair['result_entry_id']
    return result


def jobs_snapshot():
    with database() as connection:
        # One latest search per goal is enough to restore its controls after a reload.
        rows=connection.execute('''SELECT * FROM polish_jobs
            WHERE id IN (SELECT id FROM (SELECT id, ROW_NUMBER() OVER (
                PARTITION BY goal_id ORDER BY created_at DESC) AS position FROM polish_jobs) WHERE position=1)
            ORDER BY created_at DESC''').fetchall()
        return [public_job(row,connection.execute('SELECT * FROM polish_repairs WHERE job_id=? ORDER BY created_at DESC LIMIT 1',(row['id'],)).fetchone()) for row in rows]


def latest_automatic_job():
    with database() as connection:
        rows = connection.execute('SELECT * FROM polish_jobs WHERE selection_json IS NOT NULL ORDER BY created_at DESC LIMIT 100').fetchall()
        row = next((item for item in rows if (json.loads(item['selection_json'] or '{}').get('strategy')=='lowest_score')),
                   None)
        repair=connection.execute('SELECT * FROM polish_repairs WHERE job_id=? ORDER BY created_at DESC LIMIT 1',(row['id'],)).fetchone() if row else None
        return public_job(row,repair) if row else None


def six_pack_snapshot(batch_id=None):
    with database() as connection:
        if batch_id is None:
            rows=connection.execute('SELECT * FROM polish_jobs WHERE selection_json IS NOT NULL ORDER BY created_at DESC LIMIT 200').fetchall()
            first=next((row for row in rows if json.loads(row['selection_json'] or '{}').get('strategy')=='six_pack'),None)
            batch_id=json.loads(first['selection_json'])['batch_id'] if first else None
        if not batch_id:
            return None
        rows=connection.execute('SELECT * FROM polish_jobs WHERE selection_json IS NOT NULL ORDER BY created_at').fetchall()
        jobs=[]
        for row in rows:
            selection=json.loads(row['selection_json'] or '{}')
            if selection.get('strategy')!='six_pack' or selection.get('batch_id')!=batch_id:
                continue
            repair=connection.execute('SELECT * FROM polish_repairs WHERE job_id=? ORDER BY created_at DESC LIMIT 1',(row['id'],)).fetchone()
            jobs.append(public_job(row,repair))
        states=[job['status'] for job in jobs]
        status='running' if any(state in ('queued','running') for state in states) else (
            'completed' if states and all(state=='completed' for state in states) else 'partial' if any(state=='completed' for state in states) else 'failed')
        return {'batch_id':batch_id,'jobs':jobs,'status':status,'completed':sum(state=='completed' for state in states),
                'requested':6,'created':len(jobs)}


def lowest_score_goal(connection):
    from . import POINTS
    goals = connection.execute('''SELECT goals.*, COALESCE(SUM(CASE entries.reaction
        WHEN 'Good' THEN ? WHEN 'Awesome' THEN ? WHEN 'Bad' THEN ? WHEN 'Trash' THEN ? ELSE 0 END),0) AS score
        FROM goals LEFT JOIN entries ON entries.goal_id=goals.id
        WHERE NOT EXISTS (SELECT 1 FROM polish_jobs WHERE polish_jobs.goal_id=goals.id
            AND polish_jobs.status IN ('queued','running'))
        AND NOT EXISTS (SELECT 1 FROM polish_repairs JOIN polish_jobs ON polish_jobs.id=polish_repairs.job_id
            WHERE polish_jobs.goal_id=goals.id AND polish_repairs.status IN ('queued','running'))
        GROUP BY goals.id ORDER BY score, goals.id''',
        (POINTS['Good'], POINTS['Awesome'], POINTS['Bad'], POINTS['Trash'])).fetchall()
    if not goals:
        raise LookupError('No goals are available; existing goal searches must finish first.')
    tied = [goal for goal in goals if goal['score'] == goals[0]['score']]
    chosen = dict(random.choice(tied))
    selection = {'strategy': 'lowest_score', 'score': chosen.pop('score'), 'tie_count': len(tied)}
    return chosen, selection


def enqueue_search(goal_id, model_id, request_key=None, *, automatic=False):
    try:
        job_id = uuid.UUID(request_key).hex if request_key is not None else uuid.uuid4().hex
    except (ValueError, TypeError, AttributeError) as error:
        raise ValueError('Invalid search request key.') from error
    with database() as connection:
        previous = connection.execute('SELECT * FROM polish_jobs WHERE id=?', (job_id,)).fetchone()
        if previous:
            was_automatic = previous['selection_json'] is not None
            if (was_automatic != automatic or (not automatic and previous['goal_id'] != goal_id)
                    or json.loads(previous['model_json'])['id'] != model_id):
                raise ValueError('Search request key belongs to a different request.')
            return public_job(previous)
        models = state_get(connection, 'models', {}).get('models', [])
        model = next((item for item in models if item['id'] == model_id), None)
        if not model:
            raise ValueError('Model unavailable. Refresh the model list and choose an available model.')
        from .prompt_profiles import load_profile
        model={**model,'prompt_profile':load_profile(model,create=True)}
        selection = None
        if automatic:
            # Selection and reservation share this serialized database transaction.
            goal, selection = lowest_score_goal(connection)
            goal_id = goal['id']
        else:
            goal = connection.execute('SELECT * FROM goals WHERE id=?', (goal_id,)).fetchone()
            if not goal:
                raise LookupError('Goal not found.')
        repair=connection.execute('''SELECT polish_repairs.* FROM polish_repairs JOIN polish_jobs ON polish_jobs.id=polish_repairs.job_id
            WHERE polish_jobs.goal_id=? AND polish_repairs.status IN ('queued','running')''',(goal_id,)).fetchone()
        if repair:
            return public_job(connection.execute('SELECT * FROM polish_jobs WHERE id=?',(repair['job_id'],)).fetchone(),repair)
        active = connection.execute("SELECT * FROM polish_jobs WHERE goal_id=? AND status IN ('queued','running')", (goal_id,)).fetchone()
        if active:
            return public_job(active)
        now = time.time()
        connection.execute('''INSERT INTO polish_jobs
            (id,goal_id,model_json,goal_json,prompt_version,created_at,updated_at,selection_json) VALUES (?,?,?,?,?,?,?,?)''',
            (job_id, goal_id, json.dumps(model), json.dumps(dict(goal)), PROMPT_VERSION, now, now,
             json.dumps(selection) if selection else None))
    try:
        start_worker()
    except (RuntimeError, OSError) as error:
        with database() as connection:
            connection.execute("UPDATE polish_jobs SET status='failed',message=?,updated_at=? WHERE id=?",
                               ('Worker could not start. Retry the search. ' + str(error), time.time(), job_id))
        raise RuntimeError('Search was saved but the worker could not start. Retry after checking Polish worker status.') from error
    with database() as connection:
        return public_job(connection.execute('SELECT * FROM polish_jobs WHERE id=?', (job_id,)).fetchone())


def enqueue_six_pack(model_id, request_key=None):
    try:
        batch_id=uuid.UUID(request_key).hex if request_key is not None else uuid.uuid4().hex
    except (ValueError,TypeError,AttributeError) as error:
        raise ValueError('Invalid six-pack request key.') from error
    already_exists=False
    with database() as connection:
        existing=connection.execute("SELECT id FROM polish_jobs WHERE json_extract(selection_json,'$.batch_id')=? AND json_extract(selection_json,'$.strategy')='six_pack' LIMIT 1",(batch_id,)).fetchone()
        if existing:
            recorded=connection.execute('SELECT model_json FROM polish_jobs WHERE id=?',(existing['id'],)).fetchone()
            if json.loads(recorded['model_json'])['id']!=model_id:
                raise ValueError('Six-pack request key belongs to a different model.')
            already_exists=True
        else:
            models=state_get(connection,'models',{}).get('models',[])
            selected=next((item for item in models if item['id']==model_id),None)
            if not selected:
                raise ValueError('Model unavailable. Refresh the model list and choose an available model.')
            from .prompt_profiles import load_profile
            model={**selected,'prompt_profile':load_profile(selected,create=True)}
            available=connection.execute('''SELECT goals.* FROM goals
                WHERE NOT EXISTS (SELECT 1 FROM polish_jobs WHERE polish_jobs.goal_id=goals.id
                    AND polish_jobs.status IN ('queued','running'))
                AND NOT EXISTS (SELECT 1 FROM polish_repairs JOIN polish_jobs ON polish_jobs.id=polish_repairs.job_id
                    WHERE polish_jobs.goal_id=goals.id AND polish_repairs.status IN ('queued','running'))
                ORDER BY goals.id''').fetchall()
            if len(available)<6:
                raise LookupError(f'Six-pack needs six distinct available goals; {len(available)} are available.')
            chosen=random.sample(list(available),6)
            now=time.time()
            for position,goal in enumerate(chosen,1):
                job_id=uuid.uuid4().hex
                selection={'strategy':'six_pack','batch_id':batch_id,'position':position,'batch_size':6}
                connection.execute('''INSERT INTO polish_jobs
                    (id,goal_id,model_json,goal_json,prompt_version,created_at,updated_at,selection_json)
                    VALUES (?,?,?,?,?,?,?,?)''',(job_id,goal['id'],json.dumps(model),json.dumps(dict(goal)),PROMPT_VERSION,now,now,json.dumps(selection)))
    if already_exists:
        return six_pack_snapshot(batch_id)
    try:
        start_worker()
    except (RuntimeError,OSError) as error:
        with database() as connection:
            connection.execute("UPDATE polish_jobs SET status='failed',message=?,updated_at=? WHERE json_extract(selection_json,'$.batch_id')=? AND json_extract(selection_json,'$.strategy')='six_pack'",
                ('Worker could not start. Retry the six-pack. '+str(error),time.time(),batch_id))
        raise RuntimeError('Six-pack was saved but the worker could not start. Retry after checking Polish worker status.') from error
    return six_pack_snapshot(batch_id)
