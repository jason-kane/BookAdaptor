"""Idle timing, current-version repairs, fairness, and queue ownership."""
from contextlib import nullcontext
import json
import os
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch
import uuid

from PIL import Image
import pytest

from styler import idle_maintenance as idle
from styler.image_tags import ANALYSIS_VERSION, fingerprint
from styler.jobs import sample_task
from tag_matching import VERSION, tags_digest


def planner(tmp_path, state=None):
    client = Mock()
    client.get.side_effect = lambda key: json.dumps(state) if key == idle.STATE and state else None
    client.exists.return_value = False
    client.llen.return_value = 0
    client.hget.return_value = '{prompt}'
    client.sismember.return_value = False
    client.hmget.return_value = ['Artist', '{prompt}']
    client.lock.return_value = nullcontext()
    client.pipeline.return_value = MagicMock()
    client.eval.return_value = 1
    return idle.IdleMaintenance(client, tmp_path, 'video', 'priority', 'tags'), client


def sample(tmp_path, *, version=ANALYSIS_VERSION, matching=True):
    path = tmp_path / 'style_exploration' / 'artist_zit.png'
    path.parent.mkdir(exist_ok=True)
    Image.new('RGB', (32, 24), 'red').save(path)
    metadata = dict(sha256=fingerprint(path), analysis_version=version, tags=['red'])
    if matching:
        metadata.update(tag_matching_version=VERSION, tag_matching_tags_sha256=tags_digest(metadata['tags']))
    path.with_suffix('.json').write_text(json.dumps(metadata))
    return path, metadata


def test_idle_threshold_and_busy_queues_do_not_scan(tmp_path):
    worker, client = planner(tmp_path)
    client.get.side_effect = lambda key: '100' if key == idle.ACTIVITY else None
    worker._next_style = Mock(return_value=None)
    worker._next_stockpile = Mock(return_value=None)
    assert worker.tick(now=129) is None
    worker._next_style.assert_not_called()
    worker.tick(now=130)
    worker._next_style.assert_called_once_with(130)
    worker._next_style.reset_mock()
    client.llen.return_value = 1
    assert worker.tick(now=200) is None
    worker._next_style.assert_not_called()
    client.set.assert_any_call(idle.ACTIVITY, '200')


def test_unknown_idle_time_starts_a_new_quiet_period(tmp_path):
    worker, client = planner(tmp_path)
    assert worker.tick(now=100) is None
    client.scan.assert_not_called()
    client.eval.assert_not_called()
    client.set.assert_called_once_with(idle.ACTIVITY, '100')


def test_missing_sample_queues_generation_but_existing_sample_uses_vision_version(tmp_path):
    worker, client = planner(tmp_path)
    task, pending = worker._style_candidate('artist', 'zit')
    assert task['action'] == 'styler_sample'
    assert task['background_category'] == 'idle maintenance'
    assert pending == 'styler_pending:' + task['job_id']
    path, _ = sample(tmp_path, version='old')
    task, pending = worker._style_candidate('artist', 'zit')
    assert task['action'] == 'styler_tag_sample'
    assert task['analysis_version'] == ANALYSIS_VERSION
    assert task['sha256'] == fingerprint(path)
    assert pending.endswith(ANALYSIS_VERSION)


def test_current_analysis_only_normalizes_when_matching_is_stale(tmp_path):
    path, metadata = sample(tmp_path, matching=False)
    worker, client = planner(tmp_path)
    client.get.side_effect = lambda key: json.dumps(metadata) if key.startswith('styler:image-tags:') else None
    task, pending = worker._style_candidate('artist', 'zit')
    assert task['action'] == 'styler_scan_image_tags'
    assert task['scan_version'] == 'idle:' + VERSION
    assert task['sample_job_id'] == sample_task(dict(style_tag='artist', engine='zit'))['job_id']
    assert pending.startswith('styler:styles-scan:pending:')
    metadata.update(tag_matching_version=VERSION, tag_matching_tags_sha256=tags_digest(metadata['tags']))
    path.with_suffix('.json').write_text(json.dumps(metadata))
    assert worker._style_candidate('artist', 'zit') is None


def test_missing_portable_tags_are_repaired_even_with_current_redis_analysis(tmp_path):
    path, metadata = sample(tmp_path)
    worker, client = planner(tmp_path)
    client.get.side_effect = lambda key: json.dumps(metadata) if key.startswith('styler:image-tags:') else None
    path.with_suffix('.json').unlink()
    task, _ = worker._style_candidate('artist', 'zit')
    assert task['action'] == 'styler_tag_sample'


def test_planner_skips_abyss_and_non_style_hashes_with_a_bounded_scan(tmp_path):
    worker, client = planner(tmp_path)
    worker.budget = 3
    client.scan.return_value = (0, ['progress', 'abyss', 'artist', 'later'])
    client.hmget.side_effect = lambda key, *_: [None, None] if key == 'progress' else ['Artist', '{prompt}']
    client.sismember.side_effect = lambda key, tag: tag == 'abyss'
    task, _ = worker._next_style(100)
    assert task['style_tag'] == 'artist'
    assert worker.state['style_keys'] == ['later']
    assert client.hmget.call_count == 3


def test_checkpoint_resumes_next_engine_without_rescanning_the_registry(tmp_path):
    worker, client = planner(tmp_path, dict(style='artist', engine=1))
    task, _ = worker._next_style(100)
    assert task['engine'] == 'krea2'
    client.scan.assert_not_called()


def test_stockpile_cursor_handles_deleted_files_and_nested_paths(tmp_path):
    worker, client = planner(tmp_path, dict(stockpile_cursor='deleted.png'))
    root = tmp_path / 'stockpile'
    (root / 'nested').mkdir(parents=True)
    Image.new('RGB', (32, 24), 'red').save(root / 'nested' / 'image.png')
    task, _ = worker._next_stockpile(100)
    assert task['filename'] == 'nested/image.png'
    assert task['action'] == 'stockpile_image_metadata'
    from stockpile import ANALYSIS_VERSION as stock_version
    assert task['analysis_version'] == stock_version


def test_round_robin_enqueues_at_most_one_candidate_per_tick(tmp_path):
    worker, client = planner(tmp_path)
    client.get.side_effect = lambda key: '100' if key == idle.ACTIVITY else None
    worker._next_style = Mock(return_value=(dict(job_id='style', action='styler_sample'), 'style-pending'))
    worker._next_stockpile = Mock(return_value=(dict(job_id='stock', action='stockpile_image_metadata'), 'stock-pending'))
    assert worker.tick(now=130)['job_id'] == 'style'
    worker._next_stockpile.assert_not_called()
    assert worker.tick(now=160)['job_id'] == 'stock'
    assert client.eval.call_count == 2


def test_stockpile_old_analysis_refreshes_tags_and_preserves_description(tmp_path):
    import stockpile
    root = tmp_path / 'stockpile'
    root.mkdir()
    path = root / 'image.png'
    Image.new('RGB', (32, 24), 'red').save(path)
    metadata = dict(width=32, height=24, sha256=fingerprint(path), description='Manual description', tags=['old'],
                    analysis_version='old', tag_matching_version=VERSION, tag_matching_tags_sha256=tags_digest(['old']))
    path.with_suffix('.json').write_text(json.dumps(metadata))
    worker, client = planner(tmp_path)
    task, _ = worker._next_stockpile(100)
    matching = dict(tags=['fresh'], raw_tags=['fresh'], tag_matching_version=VERSION, tag_matching_tags_sha256=tags_digest(['fresh']))
    with patch.object(stockpile, 'analyze', return_value=dict(description='New description', tags=['fresh'])) as analyze, patch.object(stockpile, 'match_tags', return_value=matching):
        assert stockpile.run_job(client, task, tmp_path)['state'] == 'stockpile_complete'
        analyze.assert_called_once()
    updated = json.loads(path.with_suffix('.json').read_text())
    assert updated['description'] == 'Manual description'
    assert updated['tags'] == ['fresh']
    assert updated['analysis_version'] == stockpile.ANALYSIS_VERSION
    worker.stockpile_paths = None
    worker.state = {}
    assert worker._next_stockpile(101) is None


def test_idle_normalization_does_not_complete_an_unrelated_manual_scan(tmp_path):
    from styler import style_scan
    path, metadata = sample(tmp_path, matching=False)
    worker, client = planner(tmp_path)
    client.get.side_effect = lambda key: json.dumps(metadata) if key.startswith('styler:image-tags:') else json.dumps(dict(version=7, status='running')) if key == style_scan.STATE else None
    client.scard.return_value = 0
    task, _ = worker._style_candidate('artist', 'zit')
    matching = dict(tags=['red'], raw_tags=['red'], tag_matching_version=VERSION, tag_matching_tags_sha256=tags_digest(['red']))
    with patch('tag_matching.match_tags', return_value=matching), patch('styler.tag_consensus.reconcile', return_value={}):
        assert style_scan.run_job(client, task, tmp_path)['state'] == 'styles_scan_image_complete'
    assert json.loads(path.with_suffix('.json').read_text())['scan_version'] == 'idle:' + VERSION
    assert not any(call.args[0] == style_scan.STATE for call in client.set.call_args_list)


class NamespacedRedis:
    """Exercise real Redis scripts while isolating every key and Pub/Sub channel."""
    def __init__(self, client, prefix):
        self.client, self.prefix = client, prefix
    def get(self, key): return self.client.get(self.prefix + key)
    def set(self, key, value, **kwargs): return self.client.set(self.prefix + key, value, **kwargs)
    def exists(self, key): return self.client.exists(self.prefix + key)
    def llen(self, key): return self.client.llen(self.prefix + key)
    def hmget(self, key, *fields): return self.client.hmget(self.prefix + key, *fields)
    def sismember(self, key, value): return self.client.sismember(self.prefix + key, value)
    def hget(self, key, field): return self.client.hget(self.prefix + key, field)
    def lock(self, key, **kwargs): return self.client.lock(self.prefix + key, **kwargs)
    def publish(self, channel, value): return self.client.publish(self.prefix + channel, value)
    def scan(self, **kwargs):
        # Skip irrelevant production key batches in this isolated test view.
        # The unit scan-budget check covers bounded production traversal separately.
        while True:
            cursor, keys = self.client.scan(match=self.prefix + '*', **kwargs)
            if keys or cursor == 0:
                return cursor, [key[len(self.prefix):] for key in keys]
            kwargs['cursor'] = cursor
    def eval(self, script, count, *args):
        return self.client.eval(script, count, *(self.prefix + key for key in args[:count]),
                                *(self.prefix + arg if arg == 'styler_sample_events' else arg for arg in args[count:]))


@pytest.fixture
def live_redis():
    if not os.environ.get('QUEUE_TEST_REDIS_URL'):
        pytest.skip('Optional isolated Redis integration')
    import redis
    client = redis.Redis.from_url(os.environ['QUEUE_TEST_REDIS_URL'], decode_responses=True)
    prefix = 'test:idle-maintenance:' + uuid.uuid4().hex + ':'
    isolated = NamespacedRedis(client, prefix)
    yield isolated
    keys = list(client.scan_iter(match=prefix + '*'))
    if keys:
        client.delete(*keys)


def test_real_redis_idle_gate_single_job_and_user_work_precedence(tmp_path, live_redis):
    client = live_redis
    client.client.hset(client.prefix + 'artist', mapping=dict(name='Artist', prompt='{prompt}'))
    worker = idle.IdleMaintenance(client, tmp_path, 'video', 'priority', 'tags', budget=100)
    idle.mark_activity(client, 100)
    assert worker.tick(now=129) is None
    task = worker.tick(now=130)
    assert task['idle_maintenance'] is True
    assert client.llen(idle.QUEUE) == 1
    assert worker.tick(now=131) is None
    assert client.llen(idle.QUEUE) == 1
    client.client.lpush(client.prefix + 'priority', '{"job_id":"interactive"}')
    assert idle.pop_idle_job(client, 'video', 'priority', 'tags') is None
    assert client.llen(idle.QUEUE) == 1
    client.client.delete(client.prefix + 'priority')
    assert idle.pop_idle_job(client, 'video', 'priority', 'tags')['job_id'] == task['job_id']
    assert client.llen(idle.QUEUE) == 0


def test_real_redis_atomic_recheck_rejects_a_task_if_user_work_arrives(tmp_path, live_redis):
    client = live_redis
    client.client.hset(client.prefix + 'artist', mapping=dict(name='Artist', prompt='{prompt}'))
    worker = idle.IdleMaintenance(client, tmp_path, 'video', 'priority', 'tags', budget=100)
    idle.mark_activity(client, 100)
    original = worker._style_candidate
    def arrival(*args):
        task = original(*args)
        client.client.lpush(client.prefix + 'video', '{"job_id":"interactive"}')
        return task
    worker._style_candidate = arrival
    assert worker.tick(now=130) is None
    assert client.llen(idle.QUEUE) == 0
    assert client.llen('video') == 1


def test_real_redis_failed_attempt_is_backed_off_and_stale_pending_is_recovered(tmp_path, live_redis, monkeypatch):
    client = live_redis
    monkeypatch.setattr(idle, 'ENGINES', ('zit',))
    client.client.hset(client.prefix + 'artist', mapping=dict(name='Artist', prompt='{prompt}'))
    worker = idle.IdleMaintenance(client, tmp_path, 'video', 'priority', 'tags', budget=100)
    task = sample_task(dict(style_tag='artist', engine='zit'))
    client.set('styler_pending:' + task['job_id'], 'stale')
    idle.mark_activity(client, 100)
    scheduled = worker.tick(now=130)
    assert scheduled
    assert client.get('styler_pending:' + task['job_id']) == '1'
    idle.pop_idle_job(client, 'video', 'priority', 'tags')
    idle.mark_activity(client, 130)
    assert worker.tick(now=160) is None
    worker.state['styles_after'] = 0
    client.client.delete(client.prefix + worker._retry_key(scheduled))
    assert worker.tick(now=200)


def test_real_redis_idle_jobs_can_be_promoted_and_return_to_idle_queue(tmp_path, live_redis):
    from styler.queue_priority import promote_job
    client = live_redis
    client.client.hset(client.prefix + 'artist', mapping=dict(name='Artist', prompt='{prompt}'))
    worker = idle.IdleMaintenance(client, tmp_path, 'video', 'priority', 'tags', budget=100)
    idle.mark_activity(client, 100)
    task = worker.tick(now=130)
    promoted = promote_job(client, task['job_id'], 'priority', 'video', 'tags')
    assert promoted['job']['promoted'] is True
    assert client.llen('priority') == 1
    assert client.llen(idle.QUEUE) == 0
    demoted = promote_job(client, task['job_id'], 'priority', 'video', 'tags', enabled=False)
    assert demoted['job']['promoted'] is False
    assert client.llen(idle.QUEUE) == 1
    assert client.llen('video') == 0


def test_real_redis_styles_and_stockpile_alternate_and_resume(tmp_path, live_redis):
    client = live_redis
    client.client.hset(client.prefix + 'artist', mapping=dict(name='Artist', prompt='{prompt}'))
    (tmp_path / 'stockpile').mkdir()
    Image.new('RGB', (32, 24), 'red').save(tmp_path / 'stockpile' / 'image.png')
    worker = idle.IdleMaintenance(client, tmp_path, 'video', 'priority', 'tags', budget=100)
    idle.mark_activity(client, 100)
    first = worker.tick(now=130)
    assert first['action'] == 'styler_sample'
    idle.pop_idle_job(client, 'video', 'priority', 'tags')
    idle.mark_activity(client, 130)
    # Restarting the planner must preserve its fairness turn and engine checkpoint.
    worker = idle.IdleMaintenance(client, tmp_path, 'video', 'priority', 'tags', budget=100)
    second = worker.tick(now=160)
    assert second['action'] == 'stockpile_image_metadata'
    idle.pop_idle_job(client, 'video', 'priority', 'tags')
    idle.mark_activity(client, 160)
    third = worker.tick(now=190)
    assert third['action'] == 'styler_sample'
    assert third['engine'] != first['engine']


def test_worker_activity_tracks_start_and_finish_and_prepares_idle_services(monkeypatch):
    import ast
    import sys
    from types import SimpleNamespace
    path = Path(__file__).resolve().parents[1] / 'src/websocket/server.py'
    node = next(node for node in ast.parse(path.read_text()).body if isinstance(node, ast.FunctionDef) and node.name == 'run_tracked_task')
    activity, prepare = Mock(), Mock()
    monkeypatch.setitem(sys.modules, 'worker_startup', SimpleNamespace(ensure_worker_services=prepare))
    scope = dict(json=json, time=SimpleNamespace(time=lambda: 100), mark_activity=activity, IdleMaintenance=idle.IdleMaintenance, publish_queue_event=Mock())
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), scope)
    client = Mock()
    task = dict(job_id='idle', action='stockpile_image_metadata', idle_maintenance=True)
    callback = Mock(return_value='done')
    assert scope['run_tracked_task'](client, callback, client, task) == 'done'
    prepare.assert_called_once_with(comfyui=True)
    assert activity.call_count == 2
    assert json.loads(client.set.call_args_list[0].args[1])['idle_maintenance'] is True
    client.set.assert_any_call(idle.IdleMaintenance._retry_key(task), '1', ex=idle.RETRY_SECONDS)
    client.delete.assert_called_once_with('styler:worker-running')
    activity.reset_mock()
    client.reset_mock()
    callback.side_effect = RuntimeError('failed task')
    with pytest.raises(RuntimeError):
        scope['run_tracked_task'](client, callback, client, task)
    assert activity.call_count == 2
    client.delete.assert_called_once_with('styler:worker-running')
    scope['publish_queue_event'].assert_called_once_with(client, 'styler_sample_events',
        {**task, 'state': 'failed', 'message': 'failed task'})
    assert json.loads(client.publish.call_args.args[1])['state'] == 'queue_changed'


def test_real_consensus_broadcasts_lost_resolution_after_atomic_membership_update(tmp_path, live_redis):
    from styler.tag_consensus import ENGINES, reconcile
    client = live_redis
    events = []
    # The consensus script also uses reserved category keys and computed reverse indexes.
    # Redirect those to this fixture's namespace, just like its declared Redis keys.
    original_eval = client.eval
    def scoped_eval(script, count, *args):
        for key in ('LOST/tags', 'LOST/index', 'tags', 'categories'):
            script = script.replace("'" + key + "'", "'" + client.prefix + key + "'")
        script = script.replace("tag .. '/tags'", "'" + client.prefix + "' .. tag .. '/tags'")
        return original_eval(script, count, *args)
    client.eval = scoped_eval
    client.type = lambda key: client.client.type(client.prefix + key)
    client.mget = lambda keys: client.client.mget([client.prefix + key for key in keys])
    client.publish = lambda channel, value: events.append((channel, json.loads(value)))
    client.client.hset(client.prefix + 'artist', mapping=dict(name='Artist', prompt='{prompt}'))
    client.client.sadd(client.prefix + 'LOST/tags', 'artist', 'other')
    client.client.zadd(client.prefix + 'LOST/index', {'artist':0, 'other':0})
    folder = tmp_path / 'style_exploration'
    folder.mkdir()
    for engine in ENGINES:
        path = folder / f'artist_{engine}.png'
        Image.new('RGB', (32, 24), 'red').save(path)
        task = sample_task(dict(style_tag='artist', engine=engine))
        record = dict(sha256=fingerprint(path), analysis_version=ANALYSIS_VERSION, tags=['painting', 'red'])
        client.set('styler:image-tags:' + task['job_id'], json.dumps(record))
    agreement = reconcile(client, 'artist', tmp_path)
    assert agreement['tags'] == ['painting', 'red']
    assert not client.sismember('LOST/tags', 'artist')
    assert client.client.zscore(client.prefix + 'LOST/index', 'artist') is None
    event = events[-1][1]
    assert event['state'] == 'lost_resolved'
    assert event['lost'] is False
    assert event['lost_count'] == 1
    assert event['style_tag'] == 'artist'
    assert isinstance(event['counts'], list)
    # An open stale card still receives current membership even when tags no longer change.
    reconcile(client, 'artist', tmp_path)
    assert events[-1][1]['state'] == 'style_tags_reconciled'
    assert events[-1][1]['lost'] is False
    assert events[-1][1]['added'] == []


def test_rejected_consensus_commit_does_not_broadcast_a_resolution(tmp_path):
    from styler.tag_consensus import reconcile
    client = Mock()
    client.type.return_value = 'hash'
    client.mget.return_value = [None, None, None]
    client.hget.return_value = '{prompt}'
    client.eval.return_value = None
    assert reconcile(client, 'artist', tmp_path)['tags'] == []
    client.publish.assert_not_called()


def test_mode_default_validation_and_persistence(tmp_path):
    worker, client = planner(tmp_path)
    assert idle.get_mode(client) == 'walk'
    assert idle.set_mode(client, 'run') == 'run'
    client.set.assert_called_with(idle.MODE, 'run')
    assert json.loads(client.publish.call_args.args[1]) == dict(state='maintenance_mode', mode='run')
    with pytest.raises(ValueError):
        idle.set_mode(client, 'sprint')
    with pytest.raises(ValueError):
        idle.set_mode(client, False)


def test_off_does_not_scan_and_run_uses_five_second_delay(tmp_path):
    worker, client = planner(tmp_path)
    mode = ['off']
    client.get.side_effect = lambda key: mode[0] if key == idle.MODE else '100' if key == idle.ACTIVITY else None
    worker._next_style = Mock(return_value=None)
    worker._next_stockpile = Mock(return_value=None)
    assert worker.tick(now=999) is None
    worker._next_style.assert_not_called()
    assert not idle.background_ready(client, now=999)
    mode[0] = 'run'
    worker.tick(now=104)
    worker._next_style.assert_not_called()
    worker.tick(now=105)
    worker._next_style.assert_called_once_with(105)
    assert idle.background_ready(client, now=105)
    mode[0] = 'walk'
    assert not idle.background_ready(client, now=129)
    assert idle.background_ready(client, now=130)


def test_real_redis_off_rechecked_if_changed_during_planning(tmp_path, live_redis):
    client = live_redis
    client.client.hset(client.prefix + 'artist', mapping=dict(name='Artist', prompt='{prompt}'))
    worker = idle.IdleMaintenance(client, tmp_path, 'video', 'priority', 'tags', budget=100)
    idle.mark_activity(client, 100)
    original = worker._style_candidate
    def turn_off(*args):
        candidate = original(*args)
        idle.set_mode(client, 'off')
        return candidate
    worker._style_candidate = turn_off
    assert worker.tick(now=130) is None
    assert client.llen(idle.QUEUE) == 0
    assert worker.state == {}, 'Rejected admission retains its checkpoint'


def test_real_redis_run_mode_interval_is_enforced_at_atomic_admission(tmp_path, live_redis):
    client = live_redis
    client.client.hset(client.prefix + 'artist', mapping=dict(name='Artist', prompt='{prompt}'))
    worker = idle.IdleMaintenance(client, tmp_path, 'video', 'priority', 'tags', budget=100)
    idle.mark_activity(client, 100)
    idle.set_mode(client, 'run')
    assert worker.tick(now=104) is None
    assert worker.tick(now=105)
    assert client.llen(idle.QUEUE) == 2
    # The persisted mode is visible to a new planner and other viewers.
    assert idle.get_mode(client) == 'run'


def test_background_image_finishes_each_stage_before_advancing_engine(tmp_path):
    worker, client = planner(tmp_path, dict(style='artist', engine=0))
    generated, _ = worker._next_style(100)
    assert generated['action'] == 'styler_sample'
    assert worker.state['engine'] == 0
    path, metadata = sample(tmp_path, version='old')
    analyzed, _ = worker._next_style(130)
    assert analyzed['action'] == 'styler_tag_sample'
    assert analyzed['engine'] == 'zit'
    assert worker.state['engine'] == 0
    metadata['analysis_version'] = ANALYSIS_VERSION
    path.with_suffix('.json').write_text(json.dumps(metadata))
    client.get.side_effect = lambda key: json.dumps(metadata) if key.startswith('styler:image-tags:') else None
    next_image, _ = worker._next_style(160)
    assert next_image['action'] == 'styler_sample'
    assert next_image['engine'] == 'krea2'


def test_background_generation_leaves_tag_followup_to_rate_controlled_planner(tmp_path, monkeypatch):
    import ast
    import sys
    from contextvars import ContextVar
    from types import SimpleNamespace
    from styler.jobs import sample_response
    path = Path(__file__).resolve().parents[1] / 'src/styler/jobs.py'
    node = next(node for node in ast.parse(path.read_text()).body if isinstance(node, ast.FunctionDef) and node.name == 'run_sample_job')
    image = tmp_path / 'sample.png'
    image.write_bytes(b'image')
    style = SimpleNamespace(tags=[], get_image_path=lambda _: str(image))
    schedule = Mock()
    monkeypatch.setitem(sys.modules, 'comfy', SimpleNamespace(workflow_progress=ContextVar('idle_rate', default=None)))
    monkeypatch.setitem(sys.modules, 'styler.styles', SimpleNamespace(Style=lambda _: style))
    monkeypatch.setitem(sys.modules, 'styler.views', SimpleNamespace(draw_sample=Mock()))
    monkeypatch.setitem(sys.modules, 'styler.image_tags', SimpleNamespace(schedule=schedule))
    monkeypatch.setitem(sys.modules, 'styler.sample_metadata', SimpleNamespace(image_tags=lambda _: []))
    scope = dict(os=os, json=json, sample_response=sample_response, EVENTS='events', publish_queue_event=Mock())
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), scope)
    client = Mock()
    client.sismember.return_value = False
    for flags in ({'idle_maintenance':True}, {'background_category':'painting'}):
        task = dict(job_id='job', style_tag='artist', engine='zit', **flags)
        scope['run_sample_job'](client, task)
    schedule.assert_not_called()


def test_real_redis_run_batch_contains_style_and_stockpile_work(tmp_path, live_redis):
    client = live_redis
    client.client.hset(client.prefix + 'artist', mapping=dict(name='Artist', prompt='{prompt}'))
    (tmp_path / 'stockpile').mkdir()
    Image.new('RGB', (32, 24), 'red').save(tmp_path / 'stockpile' / 'image.png')
    worker = idle.IdleMaintenance(client, tmp_path, 'video', 'priority', 'tags', budget=100)
    idle.set_mode(client, 'run')
    idle.mark_activity(client, 100)
    worker.tick(now=105)
    queued = [json.loads(raw) for raw in client.client.lrange(client.prefix + idle.QUEUE, 0, -1)]
    assert [task['action'] for task in queued] == ['styler_sample', 'stockpile_image_metadata']
    assert len({task['job_id'] for task in queued}) == 2
    assert worker.tick(now=110) is None
    assert client.llen(idle.QUEUE) == 2, 'A pending batch prevents further additions'


def test_real_redis_two_style_jobs_keep_both_analysis_stages_reachable(tmp_path, live_redis):
    client = live_redis
    client.client.hset(client.prefix + 'artist', mapping=dict(name='Artist', prompt='{prompt}'))
    worker = idle.IdleMaintenance(client, tmp_path, 'video', 'priority', 'tags', budget=100)
    idle.set_mode(client, 'run')
    idle.mark_activity(client, 100)
    worker.tick(now=105)
    first = [idle.pop_idle_job(client, 'video', 'priority', 'tags') for _ in range(2)]
    assert [task['engine'] for task in first] == ['zit', 'krea2']
    assert all(task['action'] == 'styler_sample' for task in first)
    folder = tmp_path / 'style_exploration'
    folder.mkdir()
    for task in first:
        Image.new('RGB', (32, 24), 'red').save(folder / f"artist_{task['engine']}.png")
    idle.mark_activity(client, 105)
    # Restart as well: both generated images must still get their tagging stages.
    worker = idle.IdleMaintenance(client, tmp_path, 'video', 'priority', 'tags', budget=100)
    worker.tick(now=110)
    second = [idle.pop_idle_job(client, 'video', 'priority', 'tags') for _ in range(2)]
    assert [task['engine'] for task in second] == ['zit', 'krea2']
    assert all(task['action'] == 'styler_tag_sample' for task in second)
    assert len({task['job_id'] for task in second}) == 2


def test_real_redis_run_batches_two_stockpile_files_when_no_styles_need_work(tmp_path, live_redis):
    client = live_redis
    root = tmp_path / 'stockpile'
    root.mkdir()
    for name in ('a.png', 'b.png'):
        Image.new('RGB', (32, 24), 'red').save(root / name)
    worker = idle.IdleMaintenance(client, tmp_path, 'video', 'priority', 'tags', budget=100)
    idle.set_mode(client, 'run')
    idle.mark_activity(client, 100)
    worker.tick(now=105)
    tasks = [idle.pop_idle_job(client, 'video', 'priority', 'tags') for _ in range(2)]
    assert [task['filename'] for task in tasks] == ['a.png', 'b.png']


def test_real_redis_run_to_walk_race_admits_one_and_keeps_second_candidate(tmp_path, live_redis):
    client = live_redis
    client.client.hset(client.prefix + 'artist', mapping=dict(name='Artist', prompt='{prompt}'))
    (tmp_path / 'stockpile').mkdir()
    Image.new('RGB', (32, 24), 'red').save(tmp_path / 'stockpile' / 'image.png')
    worker = idle.IdleMaintenance(client, tmp_path, 'video', 'priority', 'tags', budget=100)
    idle.set_mode(client, 'run')
    idle.mark_activity(client, 100)
    original = worker._next_stockpile
    def slow_stockpile(now):
        task = original(now)
        idle.set_mode(client, 'walk')
        return task
    worker._next_stockpile = slow_stockpile
    # Enough idle time for Walk too; only the batch size should change here.
    worker.tick(now=130)
    assert client.llen(idle.QUEUE) == 1
    assert idle.pop_idle_job(client, 'video', 'priority', 'tags')['action'] == 'styler_sample'
    idle.mark_activity(client, 130)
    resumed = idle.IdleMaintenance(client, tmp_path, 'video', 'priority', 'tags', budget=100)
    assert resumed.tick(now=160)['filename'] == 'image.png'
    assert client.llen(idle.QUEUE) == 1
