"""Queue ordering, snapshot history, and the real-completion/cached-load boundary."""
import ast
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from styler import queue_history

SERVER = Path(__file__).resolve().parents[1] / 'src/websocket/server.py'


def queue_functions():
    tree = ast.parse(SERVER.read_text())
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef)
             and node.name in ('styler_queue_entry', 'styler_queue_snapshot')]
    scope = dict(json=json, VIDEO_QUEUE='video', IDLE_QUEUE='styler_idle_jobs', recent_completions=queue_history.recent_completions, recent_failures=queue_history.recent_failures, get_mode=lambda _: 'walk')
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SERVER), 'exec'), scope)
    return scope


def client_with_queues(priority=(), video=(), tags=(), running=None, engine=None):
    client = Mock()
    values = {'styler:worker-running': json.dumps(running) if running else None,
              'styler:worker-model': engine}
    client.get.side_effect = values.get
    client.lrange.side_effect = lambda key, *_: [json.dumps(job) for job in dict(priority=priority, video=video, tags=tags).get(key, ())]
    client.hgetall.return_value = {}
    client.exists.return_value = False
    client.zrangebyscore.return_value = []
    return client


def job(identity, action='styler_sample', **extra):
    return dict(job_id=identity, action=action, **extra)


def test_snapshot_worker_order_priority_model_interactive_scan_background():
    # Video work is LPUSH/BRPOP FIFO; priority tags and background tags use LPOP.
    client = client_with_queues(
        priority=[job('urgent', 'styler_tag_sample')],
        video=[job('background', background_category='LOST'), job('scan', 'styler_scan_image_tags'),
               job('interactive', 'scene_image'), job('same-model', engine='qwen')],
        tags=[job('tag', 'styler_tag_sample')], running=job('running', engine='qwen'), engine='qwen')
    snapshot = queue_functions()['styler_queue_snapshot'](client, 'priority', 'tags', 10)
    assert [item['job_id'] for item in snapshot['jobs']] == ['urgent', 'same-model', 'interactive', 'scan', 'background', 'tag']
    assert sum(item['isNext'] for item in snapshot['jobs']) == 1
    assert snapshot['running']['job_id'] == 'running'
    assert snapshot['queue_count'] == 7
    assert snapshot['overflow_count'] == 0


def test_snapshot_counts_deduplication_and_progress_identity():
    client = client_with_queues(priority=[job('a')], video=[job('a'), job('running'), job('b')], running=job('running'))
    original_get = client.get.side_effect
    client.get.side_effect = lambda key: json.dumps(dict(job_id='wrong', state='complete', percent=62)) if key.startswith('styler_progress:') else original_get(key)
    snapshot = queue_functions()['styler_queue_snapshot'](client, 'priority', 'tags', 1)
    assert snapshot['running']['state'] == 'running'
    assert snapshot['running']['job_id'] == 'running'
    assert snapshot['running']['percent'] == 62
    assert len(snapshot['jobs']) == 1
    assert snapshot['queue_count'] == 3
    assert snapshot['overflow_count'] == 1


def test_snapshot_includes_recent_completions_with_original_time_and_labels():
    client = client_with_queues()
    client.zrangebyscore.return_value = [json.dumps(dict(job('done', engine='qwen'), completed_at=995))]
    snapshot = queue_functions()['styler_queue_snapshot'](client, 'priority', 'tags')
    assert snapshot['completed'][0]['completed_at'] == 995
    assert snapshot['completed'][0]['label'] == 'Sample image · Qwen 2.1'
    assert snapshot['queue_count'] == 0


def test_only_actual_worker_success_is_retained_and_marked():
    client = Mock()
    client.get.return_value = json.dumps(job('done', style_tag='artist', engine='qwen'))
    event = dict(job_id='done', state='complete')
    with patch.object(queue_history.time, 'time', return_value=1000):
        queue_history.publish_queue_event(client, 'events', event)
    client.eval.assert_called_once()
    entry = json.loads(client.eval.call_args.args[5])
    assert entry['completed_at'] == 1000
    assert entry['style_tag'] == 'artist'
    delivered = json.loads(client.publish.call_args.args[1])
    assert delivered['queue_completed'] is True
    assert delivered['completed_at'] == 1000
    for state in ('running', 'loaded'):
        client.reset_mock()
        queue_history.publish_queue_event(client, 'events', dict(job_id='done', state=state))
        client.eval.assert_not_called()
    client.reset_mock()
    client.get.return_value = json.dumps(job('other'))
    queue_history.publish_queue_event(client, 'events', event)
    client.eval.assert_not_called()
    assert 'queue_completed' not in json.loads(client.publish.call_args.args[1])


def test_history_failure_does_not_lose_completion_event():
    client = Mock()
    client.get.side_effect = RuntimeError('history unavailable')
    queue_history.publish_queue_event(client, 'events', dict(job_id='done', state='complete'))
    client.publish.assert_called_once()
    assert json.loads(client.publish.call_args.args[1])['state'] == 'complete'


def test_cached_sample_response_is_loaded_with_the_same_image_payload(tmp_path):
    from styler.jobs import request_sample, sample_task
    path = tmp_path / 'style_exploration' / 'artist_qwen.png'
    path.parent.mkdir()
    path.write_bytes(b'image')
    client = Mock()
    with patch('styler.image_tags.schedule'), patch('styler.sample_metadata.image_tags', return_value=['ink']), patch('styler.tag_consensus.saved_tags', return_value=['drawing']):
        result = request_sample(client, sample_task(dict(style_tag='artist', engine='qwen')), 'video', str(tmp_path))
    assert result['state'] == 'loaded'
    assert result['filename'] == '/styles/show_sample/artist_qwen_sampler.png'
    assert result['image_tags'] == ['ink']
    assert result['prompt_tags'] == ['drawing']
    client.publish.assert_not_called()
    client.lpush.assert_not_called()


@pytest.mark.skipif(not os.environ.get('QUEUE_TEST_REDIS_URL'), reason='Optional isolated Redis integration')
def test_history_lua_on_redis(monkeypatch):
    import redis
    import uuid
    client = redis.Redis.from_url(os.environ['QUEUE_TEST_REDIS_URL'], decode_responses=True)
    key = 'test:queue-history:' + uuid.uuid4().hex
    monkeypatch.setattr(queue_history, 'KEY', key)
    monkeypatch.setattr(queue_history, 'FAILED_KEY', key + ':failed')
    clock = [1000]
    monkeypatch.setattr(queue_history.time, 'time', lambda: clock[0])
    # Delegate only history operations to Redis. Never touch the live worker marker or live channels.
    proxy = SimpleNamespace(get=lambda _: json.dumps(job(str(clock[0]))), eval=client.eval,
                            publish=lambda *_: None, zrangebyscore=client.zrangebyscore)
    try:
        for index in range(12):
            clock[0] = 1000 + index
            queue_history.publish_queue_event(proxy, 'test-events', dict(job_id=str(clock[0]), state='complete'))
        assert [entry['job_id'] for entry in queue_history.recent_completions(proxy)] == ['1007', '1008', '1009', '1010', '1011']
        queue_history.publish_queue_event(proxy, 'test-events', dict(job_id='1011', state='complete'))
        assert client.zcard(key) == 5
        clock[0] = 1042
        assert queue_history.recent_completions(proxy) == []
    finally:
        client.delete(key, key + ':failed')


def test_running_progress_triggers_plan_for_an_unrequested_job(monkeypatch):
    import itertools
    import sys
    tree = ast.parse(SERVER.read_text())
    node = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'styler_websocket')
    node.decorator_list = []
    client = Mock()
    pubsub = client.pubsub.return_value
    pubsub.get_message.side_effect = [dict(type='message', data=json.dumps(dict(job_id='other-page-job', state='running', percent=62))), None]
    ws = Mock()
    ws.receive.side_effect = [None, StopIteration]
    snapshot = Mock(side_effect=[dict(running=dict(job_id='other-page-job', percent=10)),
                               dict(running=dict(job_id='other-page-job', percent=62))])
    scope = dict(json=json, redis_connection=lambda: client, VIDEO_EVENTS='video_events', VIDEO_QUEUE='video',
                 time=SimpleNamespace(monotonic=lambda: next(ticks)), styler_queue_snapshot=snapshot)
    ticks = iter(itertools.count())
    monkeypatch.setitem(sys.modules, 'const', SimpleNamespace())
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(SERVER), 'exec'), scope)
    with pytest.raises(StopIteration):
        scope['styler_websocket'](ws)
    delivered = [json.loads(call.args[0]) for call in ws.send.call_args_list]
    assert [event['running']['percent'] for event in delivered] == [10, 62]
    pubsub.close.assert_called_once()
    client.close.assert_called_once()


def test_promoted_flag_survives_queue_snapshot_and_completion_history():
    client = client_with_queues(priority=[job('clicked', promoted=True)], running=job('running', promoted=True))
    client.zrangebyscore.return_value = [json.dumps(dict(job('done', promoted=True), completed_at=995))]
    snapshot = queue_functions()['styler_queue_snapshot'](client, 'priority', 'tags')
    assert snapshot['jobs'][0]['promoted'] is True
    assert snapshot['jobs'][0]['isNext'] is True
    assert snapshot['running']['promoted'] is True
    assert snapshot['completed'][0]['promoted'] is True


@pytest.mark.skipif(not os.environ.get('QUEUE_TEST_REDIS_URL'), reason='Optional isolated Redis integration')
def test_promotions_atomically_move_work_and_keep_only_one_copy():
    import redis
    import uuid
    from styler.queue_priority import promote_job
    client = redis.Redis.from_url(os.environ['QUEUE_TEST_REDIS_URL'], decode_responses=True)
    prefix = 'test:queue-promotion:' + uuid.uuid4().hex
    keys = [prefix + suffix for suffix in (':priority', ':video', ':tags', ':running')]
    priority, video, tags, running = keys
    channel = prefix + ':events'
    try:
        client.rpush(video, json.dumps(job('a', engine='qwen')), json.dumps(job('b', 'scene_image')))
        client.rpush(tags, json.dumps(job('c', 'styler_tag_sample', style_tag='artist')))
        result = promote_job(client, 'a', priority, video, tags, running, channel)
        assert result['job']['promoted'] is True
        assert result['job']['action'] == 'styler_sample'
        assert result['job']['engine'] == 'qwen'
        promote_job(client, 'c', priority, video, tags, running, channel)
        assert [json.loads(raw)['job_id'] for raw in client.lrange(priority, 0, -1)] == ['c', 'a']
        promote_job(client, 'a', priority, video, tags, running, channel)
        assert [json.loads(raw)['job_id'] for raw in client.lrange(priority, 0, -1)] == ['a', 'c']
        assert client.llen(tags) == 0
        assert [json.loads(raw)['job_id'] for raw in client.lrange(video, 0, -1)] == ['b']
        assert promote_job(client, 'missing', priority, video, tags, running, channel) is None
        client.set(running, json.dumps(job('b', 'scene_image')), ex=60)
        client.delete(video)
        assert promote_job(client, 'b', priority, video, tags, running, channel)['state'] == 'running'
        assert json.loads(client.get(running))['promoted'] is True
        assert 0 < client.ttl(running) <= 60
        assert client.llen(priority) == 2, 'A task that started meanwhile must never be enqueued again'
    finally:
        client.delete(*keys)


def test_priority_dispatch_uses_original_job_handlers(monkeypatch):
    import sys
    tree = ast.parse(SERVER.read_text())
    node = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'run_priority_task')
    sample, tag, scan, stock, rpc, video, tracked = [Mock() for _ in range(7)]
    monkeypatch.setitem(sys.modules, 'styler.jobs', SimpleNamespace(run_sample_job=sample))
    monkeypatch.setitem(sys.modules, 'styler.image_tags', SimpleNamespace(run_job=tag))
    monkeypatch.setitem(sys.modules, 'styler.style_scan', SimpleNamespace(run_job=scan))
    monkeypatch.setitem(sys.modules, 'stockpile', SimpleNamespace(run_job=stock))
    monkeypatch.setitem(sys.modules, 'const', SimpleNamespace(LIBRARY_DIR='/library'))
    scope = dict(run_tracked_task=tracked, run_worker_rpc=rpc, run_video_job=video)
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(SERVER), 'exec'), scope)
    client = Mock()
    for action, callback in [('styler_sample', sample), ('styler_tag_sample', tag),
                             ('styler_scan_image_tags', scan), ('stockpile_image_metadata', stock),
                             ('worker_rpc', rpc), ('scene_image', video)]:
        task = job('clicked', action, promoted=True, engine='qwen')
        returned_engine = scope['run_priority_task'](client, task)
        assert tracked.call_args.args[1] is callback
        assert tracked.call_args.args[3] is task
        assert returned_engine == ('qwen' if action == 'styler_sample' else None)


@pytest.mark.skipif(not os.environ.get('QUEUE_TEST_REDIS_URL'), reason='Optional isolated Redis integration')
def test_removing_priority_restores_normal_queues_and_selects_another_next():
    import redis
    import uuid
    from styler.queue_priority import promote_job
    from styler.jobs import pop_engine_sample, pop_interactive_job
    client = redis.Redis.from_url(os.environ['QUEUE_TEST_REDIS_URL'], decode_responses=True)
    prefix = 'test:queue-demotion:' + uuid.uuid4().hex
    keys = [prefix + suffix for suffix in (':priority', ':video', ':tags', ':running')]
    priority, video, tags, running = keys
    channel = prefix + ':events'
    try:
        client.rpush(video, json.dumps(job('clicked', engine='qwen')), json.dumps(job('other', engine='zit')))
        promote_job(client, 'clicked', priority, video, tags, running, channel)
        result = promote_job(client, 'clicked', priority, video, tags, running, channel, enabled=False)
        assert result['job']['promoted'] is False
        assert result['job']['priority'] is False
        assert client.llen(priority) == 0
        assert client.llen(video) == 2
        assert pop_engine_sample(client, video, 'qwen') is None, 'Removing priority also removes its immediate model-batching boost'
        assert json.loads(pop_interactive_job(client, video))['job_id'] == 'other'
        assert json.loads(client.rpop(video))['job_id'] == 'clicked'
        client.rpush(tags, json.dumps(job('tag', 'styler_tag_sample')), json.dumps(job('next-tag', 'styler_tag_sample')))
        promote_job(client, 'tag', priority, video, tags, running, channel)
        promote_job(client, 'tag', priority, video, tags, running, channel, enabled=False)
        assert [json.loads(raw)['job_id'] for raw in client.lrange(tags, 0, -1)] == ['next-tag', 'tag']
        # Priority tag jobs may have been created directly in the priority queue.
        client.lpush(priority, json.dumps(job('existing-priority', 'styler_tag_sample', priority=True)))
        promote_job(client, 'existing-priority', priority, video, tags, running, channel, enabled=False)
        assert client.llen(priority) == 0
        assert json.loads(client.lindex(tags, -1))['job_id'] == 'existing-priority'
        # A stale demotion request must never requeue an already-running job.
        client.set(running, json.dumps(job('started', promoted=True, priority=True)), ex=60)
        result = promote_job(client, 'started', priority, video, tags, running, channel, enabled=False)
        assert result['state'] == 'running'
        assert result['job']['promoted'] is True
        assert client.llen(video) == 0
    finally:
        client.delete(*keys)


def test_demoted_loaded_model_sample_returns_to_normal_fifo_order():
    client = client_with_queues(video=[job('demoted', engine='qwen', priority_removed=True), job('other', engine='zit')], engine='qwen')
    snapshot = queue_functions()['styler_queue_snapshot'](client, 'priority', 'tags')
    assert [item['job_id'] for item in snapshot['jobs']] == ['other', 'demoted']
    assert snapshot['jobs'][0]['isNext'] is True


def test_idle_work_is_visible_but_sorts_below_all_ordinary_jobs():
    client = client_with_queues(priority=[job('priority')], video=[job('ordinary')], tags=[job('tags', 'styler_tag_sample')])
    original = client.lrange.side_effect
    client.lrange.side_effect = lambda key, *args: [json.dumps(job('maintenance', idle_maintenance=True))] if key == 'styler_idle_jobs' else original(key, *args)
    snapshot = queue_functions()['styler_queue_snapshot'](client, 'priority', 'tags')
    assert [item['job_id'] for item in snapshot['jobs']] == ['priority', 'ordinary', 'tags', 'maintenance']
    assert snapshot['queue_count'] == 4
    assert snapshot['jobs'][-1]['idle_maintenance'] is True


def test_worker_failure_states_are_retained_with_error_details():
    for state in queue_history.FAILURE_STATES:
        client = Mock()
        client.get.return_value = json.dumps(job('oom', promoted=True))
        with patch.object(queue_history.time, 'time', return_value=1000):
            queue_history.publish_queue_event(client, 'events', dict(job_id='oom', state=state, message='CUDA out of memory'))
        entry = json.loads(client.eval.call_args.args[5])
        assert entry['state'] == 'failed'
        assert entry['message'] == 'CUDA out of memory'
        assert entry['promoted'] is True
        delivered = json.loads(client.publish.call_args.args[1])
        assert delivered['queue_failed'] is True
        assert delivered['queue_completed'] is False


def test_failure_snapshot_keeps_finished_marker_but_excludes_actual_retries():
    failure = dict(job('oom'), state='failed', completed_at=1000, message='Out of memory')
    client = client_with_queues(running=job('oom', queue_started_at=990))
    client.zrangebyscore.side_effect = lambda key, *_: [json.dumps(failure)] if key == queue_history.FAILED_KEY else []
    snapshot = queue_functions()['styler_queue_snapshot'](client, 'priority', 'tags')
    assert len(snapshot['failed']) == 1, 'The failure event can arrive before the worker clears its marker'
    client.get.side_effect = lambda key: json.dumps(job('oom', queue_started_at=1001)) if key == 'styler:worker-running' else None
    assert queue_functions()['styler_queue_snapshot'](client, 'priority', 'tags')['failed'] == []
    client.get.side_effect = lambda _: None
    client.lrange.side_effect = lambda key, *_: [json.dumps(job('oom'))] if key == 'video' else []
    assert queue_functions()['styler_queue_snapshot'](client, 'priority', 'tags', 0)['failed'] == []


@pytest.mark.skipif(not os.environ.get('QUEUE_TEST_REDIS_URL'), reason='Optional isolated Redis integration')
def test_failed_history_five_minute_retention_and_retry_cleanup(monkeypatch):
    import redis
    import uuid
    from types import SimpleNamespace
    client = redis.Redis.from_url(os.environ['QUEUE_TEST_REDIS_URL'], decode_responses=True)
    prefix = 'test:failed-history:' + uuid.uuid4().hex
    monkeypatch.setattr(queue_history, 'KEY', prefix + ':complete')
    monkeypatch.setattr(queue_history, 'FAILED_KEY', prefix + ':failed')
    clock = [1000]
    current = [job('oom', promoted=True)]
    monkeypatch.setattr(queue_history.time, 'time', lambda: clock[0])
    proxy = SimpleNamespace(get=lambda _: json.dumps(current[0]), eval=client.eval, publish=lambda *_: None,
                            zrangebyscore=client.zrangebyscore, exists=client.exists)
    try:
        queue_history.publish_queue_event(proxy, 'test-events', dict(job_id='oom', state='failed', message='CUDA OOM'))
        assert queue_history.recent_failures(proxy, now=1299)[0]['message'] == 'CUDA OOM'
        assert queue_history.recent_failures(proxy, now=1301) == []
        for index in range(8):
            current[0] = job(f'success-{index}')
            clock[0] = 1001 + index
            queue_history.publish_queue_event(proxy, 'test-events', dict(job_id=current[0]['job_id'], state='complete'))
        assert len(queue_history.recent_completions(proxy)) == 5
        assert len(queue_history.recent_failures(proxy)) == 1, 'Success history cannot evict failure history'
        queue_history.clear_failed_job(proxy, 'oom')
        assert queue_history.recent_failures(proxy) == []
        current[0] = job('oom')
        queue_history.publish_queue_event(proxy, 'test-events', dict(job_id='oom', state='failed'))
        queue_history.publish_queue_event(proxy, 'test-events', dict(job_id='oom', state='complete'))
        assert queue_history.recent_failures(proxy) == [], 'A successful retry supersedes the old failure'
        assert queue_history.recent_completions(proxy)[-1]['job_id'] == 'oom'
    finally:
        client.delete(queue_history.KEY, queue_history.FAILED_KEY)
