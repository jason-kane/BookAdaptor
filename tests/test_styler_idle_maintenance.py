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


def test_mode_and_idle_helpers_accept_redis_bytes_and_recover_bad_activity(monkeypatch, tmp_path):
    worker, client = planner(tmp_path)
    client.get.side_effect = lambda key: b'run' if key == idle.MODE else b'not-a-time'
    assert idle.get_mode(client) == 'run'
    assert not idle.background_ready(client, now=200)
    client.set.assert_called_with(idle.ACTIVITY, '200')

    client.get.side_effect = lambda key: 'unknown' if key == idle.MODE else '170'
    assert idle.get_mode(client) == 'walk'
    assert idle.background_ready(client, now=200)
    monkeypatch.setattr(idle.time, 'time', lambda: 250)
    idle.mark_activity(client)
    client.set.assert_called_with(idle.ACTIVITY, '250')


def test_mode_off_and_idle_queue_pop_result_shapes(tmp_path):
    worker, client = planner(tmp_path)
    client.get.side_effect = lambda key: 'off' if key == idle.MODE else '0'
    assert not idle.background_ready(client, now=100)
    client.eval.return_value = json.dumps({'job_id': 'idle'})
    assert idle.pop_idle_job(client, 'video', 'priority', 'tags') == {'job_id': 'idle'}
    client.eval.return_value = None
    assert idle.pop_idle_job(client, 'video', 'priority', 'tags') is None


def test_json_metadata_and_text_helpers_handle_invalid_and_missing_data(tmp_path):
    assert idle._json(b'{"ok": true}') == {'ok': True}
    assert idle._json('[]') == {}
    assert idle._json(None) == {}
    metadata_path = tmp_path / 'image.json'
    metadata_path.write_text('{"tags": ["red"]}')
    assert idle._metadata(tmp_path / 'image.png') == {'tags': ['red']}
    metadata_path.write_bytes(b'\xff')
    assert idle._metadata(tmp_path / 'image.png') == {}
    assert idle._metadata(tmp_path / 'missing.png') == {}
    assert idle._text(b'key') == 'key'
    assert idle._text('key') == 'key'


def test_image_walk_skips_missing_roots_unreadable_directories_and_symlinks(tmp_path, monkeypatch):
    assert list(idle._image_paths(tmp_path / 'missing')) == []
    root = tmp_path / 'stockpile'
    root.mkdir()
    (root / 'z.png').write_bytes(b'not an image')
    (root / 'A.jpg').write_bytes(b'not an image')
    (root / 'ignored.txt').write_text('ignore')
    nested = root / 'nested'
    nested.mkdir()
    (nested / 'b.webp').write_bytes(b'not an image')
    link = root / 'linked.png'
    try:
        link.symlink_to(root / 'A.jpg')
    except OSError:
        pass
    else:
        assert [path.relative_to(root).as_posix() for path in idle._image_paths(root)] == [
            'A.jpg', 'nested/b.webp', 'z.png'
        ]

    def fail_to_list(_self):
        raise OSError('unavailable')

    with monkeypatch.context() as scoped:
        scoped.setattr(Path, 'iterdir', fail_to_list)
        assert list(idle._image_paths(root)) == []


def test_style_planner_cooldown_and_redis_hash_cursor_completion(tmp_path):
    worker, client = planner(tmp_path, dict(styles_after=150))
    assert worker._next_style(100) is None
    client.scan.assert_not_called()

    worker.state = dict(style_started=True, style_cursor=0)
    worker.budget = 1
    assert worker._next_style(100) is None
    assert worker.state['styles_after'] == 100 + idle.RESCAN_SECONDS
    client.scan.assert_not_called()


def test_style_planner_skips_missing_hash_and_abyss_candidates(tmp_path):
    worker, client = planner(tmp_path)
    worker.budget = 3
    client.scan.return_value = (0, [b'missing', b'abyss'])
    client.hmget.side_effect = [[None, None], ['Artist', '{prompt}']]
    client.sismember.side_effect = lambda key, tag: tag == 'abyss'
    assert worker._next_style(100) is None
    assert worker.state['style_keys'] == []
    assert not worker.state.get('style')


def test_style_candidate_uses_prompt_subdirectory_and_refuses_sample_symlinks(tmp_path):
    worker, _ = planner(tmp_path)
    task, pending = worker._style_candidate('artist', 'zit', 'kid_friendly')
    assert task['base_style_prompt'] == 'kid_friendly'
    assert pending == 'styler_pending:' + task['job_id']

    sample_dir = tmp_path / 'style_exploration' / 'kid_friendly'
    sample_dir.mkdir(parents=True)
    target = tmp_path / 'target.png'
    target.write_bytes(b'image')
    try:
        (sample_dir / 'artist_zit.png').symlink_to(target)
    except OSError:
        pytest.skip('symlinks are unavailable')
    assert worker._style_candidate('artist', 'zit', 'kid_friendly') is None


def test_style_cursor_handles_stale_style_empty_scan_and_abyss(tmp_path):
    worker, client = planner(tmp_path, {'style': 'stale'})
    worker.budget = 1
    client.hmget.return_value = [None, '{prompt}']
    assert worker._next_style(100) is None
    assert worker.state['style'] is None

    worker.state = {'style_cursor': 1}
    client.scan.return_value = (1, [])
    assert worker._next_style(100) is None
    assert worker.state['style_started'] is True

    worker.state = {'style': 'artist'}
    client.hmget.return_value = ['Artist', '{prompt}']
    client.sismember.return_value = True
    assert worker._next_style(100) is None
    assert worker.state['style'] is None


def test_style_cursor_wraps_engine_and_prompt_after_inspection_failure(tmp_path):
    worker, _ = planner(tmp_path, {
        'style': 'artist', 'engine': len(idle.ENGINES) - 1,
        'style_base_index': len(idle.BASE_STYLE_PROMPTS) - 1,
    })
    worker.budget = 1
    worker._style_candidate = Mock(side_effect=ValueError('unreadable sample'))

    assert worker._next_style(100) is None
    assert worker.state['style'] is None
    assert worker.state['engine'] == 0
    assert worker.state['style_base_index'] == 0

    worker.state = {'style': 'artist', 'engine': len(idle.ENGINES) - 1, 'style_base_index': 0}
    assert worker._next_style(100) is None
    assert worker.state['style'] == 'artist'
    assert worker.state['style_base_index'] == 1

def test_batch_planning_reserves_candidates_and_clears_reservations(tmp_path):
    worker, client = planner(tmp_path)
    worker.state = {'cursor': 'before', 'style': 'artist', 'style_cursor': 12, 'engine': 1}
    style_tasks = [
        {'job_id': 'style-one', 'style_tag': 'artist', 'engine': 'zit'},
        {'job_id': 'style-two', 'style_tag': 'artist', 'engine': 'krea2'},
    ]
    worker._next_style = Mock(side_effect=[(style_tasks[0], 'pending-one'), (style_tasks[1], 'pending-two')])
    worker._next_stockpile = Mock(return_value=None)

    checkpoint, candidates, checkpoints = worker._plan_batch(100, 'run')

    assert checkpoint == {
        'cursor': 'before', 'style': 'artist', 'style_cursor': 12, 'engine': 1,
    }
    assert [task['job_id'] for task, _ in candidates] == ['style-one', 'style-two']
    assert all(task['idle_maintenance'] for task, _ in candidates)
    assert len(checkpoints) == 2
    assert worker.batch_reserved == set()
    worker._next_stockpile.assert_called_once_with(100)


def test_batch_admission_restores_or_advances_checkpoint_by_accepted_count(tmp_path):
    worker, client = planner(tmp_path, {'cursor': 'planned'})
    worker.stockpile_paths = iter([Path('cached.png')])
    checkpoint = {'cursor': 'before'}
    tasks = [
        {'job_id': 'one', 'sha256': 'a', 'analysis_version': 'v'},
        {'job_id': 'two', 'sha256': 'b', 'analysis_version': 'v'},
    ]
    candidates = [(task, f'pending-{task["job_id"]}') for task in tasks]
    checkpoints = [{'cursor': 'first'}, {'cursor': 'last'}]

    client.eval.return_value = 0
    assert worker._admit_batch(100, checkpoint, candidates, checkpoints) is None
    assert worker.state == checkpoint
    assert worker.stockpile_paths is None

    worker.stockpile_paths = iter([Path('cached.png')])
    client.eval.return_value = 1
    assert worker._admit_batch(100, checkpoint, candidates, checkpoints) is tasks[0]
    assert worker.state == checkpoints[0]
    assert worker.stockpile_paths is None

    worker.stockpile_paths = iter([Path('cached.png')])
    client.eval.return_value = 2
    assert worker._admit_batch(100, checkpoint, candidates, checkpoints) is tasks[0]
    assert worker.state['cursor'] == 'first'
    assert worker.stockpile_paths is not None


def test_stockpile_candidate_skips_bad_image_without_losing_cursor(tmp_path):
    worker, _ = planner(tmp_path)
    root = tmp_path / 'stockpile'
    root.mkdir()
    (root / 'bad.png').write_bytes(b'not an image')
    worker.stockpile_paths = iter([root / 'bad.png'])
    assert worker._next_stockpile(100) is None
    assert worker.state['stockpile_cursor'] == ''


def test_stockpile_cursor_cooldown_and_retry_reserved_candidate(tmp_path):
    worker, client = planner(tmp_path, {'stockpile_after': 101})
    assert worker._next_stockpile(100) is None

    root = tmp_path / 'stockpile'
    root.mkdir()
    Image.new('RGB', (32, 24), 'red').save(root / 'image.png')
    worker.state = {}
    worker.stockpile_paths = iter([root / 'image.png'])
    worker.batch_reserved = {'retry'}
    worker._retry_key = Mock(return_value='retry')
    assert worker._next_stockpile(100) is None
    client.exists.assert_not_called()


def test_stockpile_cursor_skips_paths_already_processed(tmp_path, monkeypatch):
    worker, _ = planner(tmp_path, {'stockpile_cursor': 'b.png'})
    root = tmp_path / 'stockpile'
    root.mkdir()
    first = root / 'a.png'
    second = root / 'b.png'
    Image.new('RGB', (32, 24), 'red').save(first)
    Image.new('RGB', (32, 24), 'blue').save(second)
    worker.stockpile_paths = iter([first, second])

    assert worker._next_stockpile(100) is None
    assert worker.state['stockpile_cursor'] == ''

    from stockpile import ANALYSIS_VERSION

    worker.budget = 1
    worker.state = {}
    worker.stockpile_paths = iter([first])
    metadata = {
        'sha256': fingerprint(first),
        'tags': ['red'],
        'analysis_version': ANALYSIS_VERSION,
    }
    monkeypatch.setattr('stockpile.read_metadata', lambda _path: metadata)
    monkeypatch.setattr('stockpile.complete', lambda *_args: True)
    monkeypatch.setattr('tag_matching.matched', lambda _record: True)
    assert worker._next_stockpile(100) is None


def test_weekly_topic_rebuild_is_prioritized_before_idle_repairs(tmp_path, monkeypatch):
    from types import SimpleNamespace

    worker, client = planner(tmp_path)
    client.get.side_effect = lambda key: {
        idle.MODE: 'walk',
        idle.ACTIVITY: '0',
        'styler:topic-tree:last-success': '0',
    }.get(key)
    monkeypatch.setitem(
        __import__('sys').modules,
        'styler.topic_jobs',
        SimpleNamespace(enqueue_taxonomy_rebuild=Mock(return_value={
            'state': 'queued', 'job_id': 'weekly-job',
        })),
    )

    assert worker.tick(now=idle.TOPIC_REBUILD_SECONDS + idle.IDLE_SECONDS) == {
        'action': 'styler_topic_taxonomy_rebuild', 'job_id': 'weekly-job',
    }
    worker._plan_batch = Mock()
    worker._plan_batch.assert_not_called()


def test_failed_weekly_topic_enqueue_falls_through_to_idle_planning(tmp_path, monkeypatch):
    from types import SimpleNamespace

    worker, client = planner(tmp_path)
    client.get.side_effect = lambda key: {
        idle.MODE: 'walk',
        idle.ACTIVITY: '0',
        'styler:topic-tree:last-success': 'invalid',
    }.get(key)
    monkeypatch.setitem(
        __import__('sys').modules,
        'styler.topic_jobs',
        SimpleNamespace(enqueue_taxonomy_rebuild=Mock(return_value={'state': 'busy'})),
    )
    worker._plan_batch = Mock(return_value=({}, [], []))
    worker._admit_batch = Mock(return_value=None)

    assert worker.tick(now=idle.TOPIC_REBUILD_SECONDS + idle.IDLE_SECONDS) is None
    worker._plan_batch.assert_called_once()
    worker._admit_batch.assert_called_once_with(
        idle.TOPIC_REBUILD_SECONDS + idle.IDLE_SECONDS, {}, [], []
    )


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
    task, pending = worker._style_candidate('artist', 'zit', 'classic')
    assert task['action'] == 'styler_sample'
    assert task['background_category'] == 'idle maintenance'
    assert pending == 'styler_pending:' + task['job_id']
    path, _ = sample(tmp_path, version='old')
    task, pending = worker._style_candidate('artist', 'zit', 'classic')
    assert task['action'] == 'styler_tag_sample'
    assert task['analysis_version'] == ANALYSIS_VERSION
    assert task['sha256'] == fingerprint(path)
    assert pending == task['pending_key']
    assert pending.endswith(f":{ANALYSIS_VERSION}:{task['prompt_sha256']}")


def test_current_analysis_only_normalizes_when_matching_is_stale(tmp_path):
    path, metadata = sample(tmp_path, matching=False)
    worker, client = planner(tmp_path)
    client.get.side_effect = lambda key: json.dumps(metadata) if key.startswith('styler:image-tags:') else None
    task, pending = worker._style_candidate('artist', 'zit', 'classic')
    assert task['action'] == 'styler_scan_image_tags'
    assert task['scan_version'] == 'idle:' + VERSION
    assert task['sample_job_id'] == sample_task(dict(style_tag='artist', engine='zit'))['job_id']
    assert pending.startswith('styler:styles-scan:pending:')
    metadata.update(tag_matching_version=VERSION, tag_matching_tags_sha256=tags_digest(metadata['tags']))
    path.with_suffix('.json').write_text(json.dumps(metadata))
    assert worker._style_candidate('artist', 'zit', 'classic') is None


def test_missing_portable_tags_are_repaired_even_with_current_redis_analysis(tmp_path):
    path, metadata = sample(tmp_path)
    worker, client = planner(tmp_path)
    client.get.side_effect = lambda key: json.dumps(metadata) if key.startswith('styler:image-tags:') else None
    path.with_suffix('.json').unlink()
    task, _ = worker._style_candidate('artist', 'zit', 'classic')
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
    task, _ = worker._style_candidate('artist', 'zit', 'classic')
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
    client.delete.assert_any_call('styler:worker-running')
    activity.reset_mock()
    client.reset_mock()
    callback.side_effect = RuntimeError('failed task')
    with pytest.raises(RuntimeError):
        scope['run_tracked_task'](client, callback, client, task)
    assert activity.call_count == 2
    client.delete.assert_any_call('styler:worker-running')
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
