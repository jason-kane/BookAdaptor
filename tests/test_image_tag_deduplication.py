"""Exercise real queue Lua against isolated in-memory Redis, never live services."""
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

try:
    import fakeredis
except ImportError:
    fakeredis = None

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from styler import image_tags
from styler.jobs import sample_task
from task_queue import enqueue_task, TAG_QUEUE, PRIORITY_QUEUE


@unittest.skipIf(fakeredis is None, 'Requires fakeredis[lua] test dependency')
class ImageTagDeduplicationTests(unittest.TestCase):
    def setUp(self):
        self.client = fakeredis.FakeRedis(decode_responses=True)
        self.folder = TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.addCleanup(self.client.close)
        self.merge = patch('styler.style_duplicates.style_merge_locked', return_value=False)
        self.merge.start()
        self.addCleanup(self.merge.stop)

    def schedule(self, engine='zit', prompt='classic', **kwargs):
        """Submit one deterministic sample identity through production Lua."""
        task = sample_task(dict(style_tag='artist', engine=engine, base_style_prompt=prompt))
        path = Path(self.folder.name) / f'{prompt}_{engine}.png'
        if not path.exists():
            path.write_bytes(engine.encode())
        image_tags.schedule(self.client, task, path, **kwargs)
        return task, path

    def groups(self):
        """Read both admission queues without changing their order."""
        return [json.loads(value) for queue in (TAG_QUEUE, PRIORITY_QUEUE)
                for value in self.client.lrange(queue, 0, -1)]

    def test_repeated_requests_and_engines_make_one_group(self):
        for _ in range(3):
            for engine in ('zit', 'krea2', 'qwen'):
                self.schedule(engine, force=True)
        groups = self.groups()
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0]['items']), 3)
        self.assertEqual(set(groups[0]['engines']), {'zit', 'krea2', 'qwen'})

    def test_requeue_is_a_move_and_cleans_duplicate_copies(self):
        self.schedule()
        group = self.groups()[0]
        self.client.lpush(PRIORITY_QUEUE, json.dumps(group))
        for _ in range(3):
            enqueue_task(self.client, TAG_QUEUE, group, front=False)
        self.assertEqual(len(self.groups()), 1)
        self.assertEqual(self.client.llen(PRIORITY_QUEUE), 0)

    def test_missing_group_pointer_does_not_create_a_second_group(self):
        self.schedule()
        original = self.groups()[0]
        self.client.delete('styler:tag-group-open:' + original['group_key'])
        self.schedule('qwen', force=True)
        self.assertEqual(len(self.groups()), 1)
        self.assertEqual(self.groups()[0]['job_id'], original['job_id'])
        self.assertEqual(len(self.groups()[0]['items']), 2)

    def test_running_group_blocks_repeat_even_after_a_member_pending_key_is_cleared(self):
        self.schedule()
        group = image_tags.pop_tag_job(self.client, TAG_QUEUE)
        self.client.set('styler:worker-running', json.dumps(group))
        self.client.delete(group['items'][0]['pending_key'])
        self.schedule(force=True, priority=True)
        self.assertEqual(self.groups(), [])

    def test_new_image_revision_can_queue_while_older_revision_runs(self):
        _, path = self.schedule()
        group = image_tags.pop_tag_job(self.client, TAG_QUEUE)
        self.client.set('styler:worker-running', json.dumps(group))
        path.write_bytes(b'new image revision')
        self.schedule(force=True)
        self.assertEqual(len(self.groups()), 1)
        self.assertNotEqual(self.groups()[0]['items'][0]['sha256'], group['items'][0]['sha256'])

    def test_new_prompt_revision_can_queue_while_older_revision_runs(self):
        self.schedule()
        group = image_tags.pop_tag_job(self.client, TAG_QUEUE)
        self.client.set('styler:worker-running', json.dumps(group))
        self.client.hset('artist', 'prompt', 'updated {prompt}')
        self.schedule(force=True)
        self.assertEqual(len(self.groups()), 1)
        self.assertNotEqual(self.groups()[0]['items'][0]['pending_key'], group['items'][0]['pending_key'])

    def test_prompt_variants_remain_distinct_and_priority_promotes_existing_group(self):
        self.schedule()
        self.schedule(prompt='kid_friendly')
        self.schedule(force=True, priority=True)
        self.assertEqual(len(self.groups()), 2)
        self.assertEqual(self.client.llen(PRIORITY_QUEUE), 1)
        self.assertEqual(json.loads(self.client.lindex(PRIORITY_QUEUE, 0))['base_style_prompt'], 'classic')

    def test_legacy_sample_requeues_preserve_distinct_image_revisions(self):
        task = dict(action='styler_tag_sample', job_id='sample', sha256='old',
                    prompt_sha256='prompt', analysis_version='version')
        for _ in range(3):
            enqueue_task(self.client, TAG_QUEUE, task)
        enqueue_task(self.client, TAG_QUEUE, {**task, 'sha256': 'new'})
        self.assertEqual(self.client.llen(TAG_QUEUE), 2)

    def test_popping_identical_group_copies_only_returns_one_task(self):
        self.schedule()
        self.client.rpush(TAG_QUEUE, self.client.lindex(TAG_QUEUE, 0))
        self.assertIsNotNone(image_tags.pop_tag_job(self.client, TAG_QUEUE))
        self.assertIsNone(image_tags.pop_tag_job(self.client, TAG_QUEUE))

    def test_existing_duplicate_groups_merge_without_losing_an_engine(self):
        self.schedule()
        original = self.groups()[0]
        duplicate = json.loads(json.dumps(original))
        duplicate['job_id'] = 'another-group-id'
        duplicate['items'][0]['engine'] = 'qwen'
        duplicate['items'][0]['job_id'] = 'another-member-id'
        duplicate['engines'] = ['qwen']
        duplicate['priority'] = True
        self.client.lpush(PRIORITY_QUEUE, json.dumps(duplicate))
        self.schedule('krea2')
        self.assertEqual(len(self.groups()), 1)
        self.assertEqual(set(self.groups()[0]['engines']), {'zit', 'qwen', 'krea2'})
        self.assertEqual(self.client.llen(PRIORITY_QUEUE), 1)

    def test_concurrent_producers_admit_only_one_group(self):
        from concurrent.futures import ThreadPoolExecutor
        self.schedule()
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda _: self.schedule(force=True), range(12)))
        self.assertEqual(len(self.groups()), 1)
        self.assertEqual(len(self.groups()[0]['items']), 1)

    def test_failed_member_releases_its_prompt_specific_pending_marker(self):
        from types import SimpleNamespace
        self.schedule()
        member = self.groups()[0]['items'][0]
        pending = member['pending_key']
        self.assertTrue(self.client.exists(pending))
        with patch.dict(sys.modules, {'const': SimpleNamespace(LIBRARY_DIR=self.folder.name)}), \
                self.assertLogs('styler.image_tags', level='ERROR'):
            self.assertFalse(image_tags.run_job(self.client, member))
        self.assertFalse(self.client.exists(pending))

    def test_child_retag_queues_and_saves_only_the_requested_variant(self):
        root = Path(self.folder.name)
        classic = root / 'style_exploration' / 'artist_qwen.png'
        child = classic.parent / 'kid_friendly' / classic.name
        child.parent.mkdir(parents=True)
        classic.write_bytes(b'classic image')
        child.write_bytes(b'child image')
        classic.with_suffix('.json').write_text(json.dumps({'tags': ['classic tag']}))
        tasks = image_tags.request_retag(
            self.client, 'artist', root, engine='qwen', base_style_prompt='kid_friendly',
        )
        group = self.groups()[0]
        member = group['items'][0]
        self.assertEqual(tasks[0]['base_style_prompt'], 'kid_friendly')
        self.assertEqual(group['base_style_prompt'], 'kid_friendly')
        self.assertEqual(member['sha256'], image_tags.fingerprint(child))
        self.assertNotEqual(member['sha256'], image_tags.fingerprint(classic))
        record = {**member, 'tags': ['child tag']}
        image_tags._save_analysis_record(self.client, member, child, record, ['child tag'])
        self.assertEqual(json.loads(child.with_suffix('.json').read_text())['tags'], ['child tag'])
        self.assertEqual(json.loads(classic.with_suffix('.json').read_text())['tags'], ['classic tag'])
