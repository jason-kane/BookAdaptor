import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import stockpile
from tag_matching import VERSION, tags_digest


class MemoryRedis:
    def __init__(self):
        self.values = {}
        self.jobs = []
        self.events = []

    def set(self, key, value, nx=False, **kwargs):
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    def lpush(self, queue, payload):
        self.jobs.append(json.loads(payload))

    def delete(self, key):
        self.values.pop(key, None)

    def publish(self, channel, payload):
        self.events.append(json.loads(payload))

    def smembers(self, key):
        return set()


class StockpileTests(unittest.TestCase):
    def setUp(self):
        self.matcher = patch('tag_matching.run_llm', side_effect=lambda prompt:
                             json.dumps({'tags': json.loads(prompt.split('\n', 1)[1])['item_tags']}))
        self.llm = self.matcher.start()
        self.addCleanup(self.matcher.stop)

    def make_image(self, root, name='image.png', metadata=None):
        path = Path(root) / 'stockpile' / name
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new('RGB', (32, 24), 'red').save(path)
        if metadata is not None:
            if metadata.get('tags'):
                metadata.update(tag_matching_version=VERSION, tag_matching_tags_sha256=tags_digest(metadata['tags']))
            path.with_suffix('.json').write_text(json.dumps(metadata))
        return path

    def test_complete_existing_image_gets_matching_without_vision_analysis(self):
        client = MemoryRedis()
        with TemporaryDirectory() as tmp:
            path = self.make_image(tmp, metadata=dict(width=32, height=24,
                                                      description='Keep description', tags=['oil on canvas']))
            metadata = json.loads(path.with_suffix('.json').read_text())
            metadata.pop('tag_matching_version')
            path.with_suffix('.json').write_text(json.dumps(metadata))
            stockpile.scan_stockpile(client, tmp)
            self.assertEqual(len(client.jobs), 1)
            with patch.object(stockpile, 'analyze') as analyze:
                stockpile.run_job(client, client.jobs[0], tmp)
            analyze.assert_not_called()
            self.llm.assert_called_once()
            result = json.loads(path.with_suffix('.json').read_text())
            self.assertEqual(result['description'], 'Keep description')
            self.assertEqual(result['raw_tags'], ['oil on canvas'])
            self.assertEqual(result['tag_matching_version'], VERSION)
            self.assertEqual(stockpile.scan_stockpile(client, tmp)['jobs'], [])

    def test_scan_queues_only_incomplete_images_and_deduplicates(self):
        client = MemoryRedis()
        with TemporaryDirectory() as tmp:
            self.make_image(tmp, 'missing.png')
            self.make_image(tmp, 'nested/partial.jpg', {'description': 'Original description'})
            self.make_image(tmp, 'ready.webp', dict(width=32, height=24, description='A red image', tags=['red']))
            self.make_image(tmp, 'wrong-size.png', dict(width=1, height=1, description='Red', tags=['red']))
            (Path(tmp) / 'stockpile' / 'notes.txt').write_text('not an image')
            (Path(tmp) / 'stockpile' / 'corrupt.png').write_bytes(b'not an image')
            result = stockpile.scan_stockpile(client, tmp)
            self.assertEqual(result['scanned'], 5)
            self.assertEqual(result['complete'], 1)
            self.assertEqual(result['errors'], ['corrupt.png'])
            self.assertEqual(len(result['jobs']), 3)
            stockpile.scan_stockpile(client, tmp)
            self.assertEqual(len(client.jobs), 3)

    def test_dimensions_only_repair_avoids_llm_and_preserves_metadata(self):
        client = MemoryRedis()
        with TemporaryDirectory() as tmp:
            path = self.make_image(tmp, metadata=dict(description='Original description', tags=['original'], license='mine'))
            stockpile.scan_stockpile(client, tmp)
            with patch.object(stockpile, 'analyze') as analyze:
                event = stockpile.run_job(client, client.jobs[0], tmp)
            analyze.assert_not_called()
            self.assertEqual(event['state'], 'stockpile_complete')
            metadata = json.loads(path.with_suffix('.json').read_text())
            self.assertEqual((metadata['width'], metadata['height']), (32, 24))
            self.assertEqual(metadata['description'], 'Original description')
            self.assertEqual(metadata['license'], 'mine')
            self.assertFalse(client.values)
            self.assertEqual(stockpile.scan_stockpile(client, tmp)['jobs'], [])

    def test_analysis_fills_missing_fields_without_overwriting_valid_fields(self):
        client = MemoryRedis()
        with TemporaryDirectory() as tmp:
            path = self.make_image(tmp, metadata=dict(description='Keep this', source='camera'))
            stockpile.scan_stockpile(client, tmp)
            with patch.object(stockpile, 'analyze', return_value=dict(description='New description', tags=['red', 'plain background'])) as analyze:
                stockpile.run_job(client, client.jobs[0], tmp)
            self.assertEqual(analyze.call_args.kwargs['workflow_name'], 'stockpile-image-metadata')
            metadata = json.loads(path.with_suffix('.json').read_text())
            self.assertEqual(metadata['description'], 'Keep this')
            self.assertEqual(metadata['tags'], ['red', 'plain background'])
            self.assertEqual(metadata['source'], 'camera')

    def test_malformed_sidecar_is_backed_up_before_replacement(self):
        client = MemoryRedis()
        with TemporaryDirectory() as tmp:
            path = self.make_image(tmp)
            path.with_suffix('.json').write_text('{broken')
            stockpile.scan_stockpile(client, tmp)
            with patch.object(stockpile, 'analyze', return_value=dict(description='Red image', tags=['red'])):
                stockpile.run_job(client, client.jobs[0], tmp)
            backup = list(path.parent.glob('image.json.invalid-*'))
            self.assertEqual(len(backup), 1)
            self.assertEqual(backup[0].read_text(), '{broken')
            self.assertEqual(json.loads(path.with_suffix('.json').read_text())['tags'], ['red'])

    def test_failed_analysis_keeps_original_sidecar_and_can_be_retried(self):
        client = MemoryRedis()
        with TemporaryDirectory() as tmp:
            path = self.make_image(tmp, metadata={'source': 'camera'})
            original = path.with_suffix('.json').read_text()
            stockpile.scan_stockpile(client, tmp)
            with patch.object(stockpile, 'analyze', side_effect=ValueError('Invalid response')), self.assertLogs('stockpile', level='ERROR'):
                event = stockpile.run_job(client, client.jobs[0], tmp)
            self.assertEqual(event['state'], 'stockpile_failed')
            self.assertEqual(path.with_suffix('.json').read_text(), original)
            self.assertFalse(client.values)
            stockpile.scan_stockpile(client, tmp)
            self.assertEqual(len(client.jobs), 2)

    def test_replaced_image_is_not_given_stale_metadata(self):
        client = MemoryRedis()
        with TemporaryDirectory() as tmp:
            path = self.make_image(tmp)
            stockpile.scan_stockpile(client, tmp)
            Image.new('RGB', (16, 16), 'blue').save(path)
            with patch.object(stockpile, 'analyze') as analyze, self.assertLogs('stockpile', level='ERROR'):
                event = stockpile.run_job(client, client.jobs[0], tmp)
            self.assertEqual(event['state'], 'stockpile_failed')
            analyze.assert_not_called()
            self.assertFalse(path.with_suffix('.json').exists())

    def test_analysis_parser_handles_formatting_and_rejects_incomplete_results(self):
        self.assertEqual(stockpile.parse_analysis('```json\n{"description":"Red image", "tags":["Red",],}\n```'),
                         dict(description='Red image', tags=['red']))
        for text in ('{"tags":["red"]}', '{"description":"", "tags":["red"]}',
                     '{"description":"Red", "tags":[]}'):
            with self.assertRaises(ValueError):
                stockpile.parse_analysis(text)

    def test_metadata_helpers_cover_missing_malformed_and_normalized_values(self):
        with TemporaryDirectory() as tmp:
            self.assertEqual(list(stockpile.metadata_images(tmp)), [])
            self.assertEqual(stockpile.tagged_images(tmp, '  \t'), [])
            self.assertEqual(stockpile.tag_counts(tmp), {})
            stockpile._tag_index_cache.clear()

            valid = self.make_image(tmp, 'nested/valid.png', dict(tags=[' Oil   Painting ', 'RED']))
            invalid = self.make_image(tmp, 'invalid.png', {'tags': 'not a list'})
            malformed = self.make_image(tmp, 'malformed.png')
            malformed.with_suffix('.json').write_text('{broken')
            images = dict(stockpile.metadata_images(tmp))
            self.assertEqual(set(images), {'nested/valid.png', 'invalid.png', 'malformed.png'})
            self.assertEqual(images['malformed.png'], {})
            self.assertIsNone(stockpile.read_metadata(malformed))
            self.assertEqual(stockpile.tagged_images(tmp, ' oil painting ')[0]['filename'], 'nested/valid.png')
            self.assertEqual(stockpile.tag_counts(tmp), {'oil painting': 1, 'red': 1})

            outside = Path(tmp) / 'outside.png'
            Image.new('RGB', (2, 2), 'blue').save(outside)
            (Path(tmp) / 'stockpile' / 'outside-link.png').symlink_to(outside)
            self.assertNotIn('outside-link.png', dict(stockpile.metadata_images(tmp)))
            self.assertIsNone(stockpile.read_metadata(Path(tmp) / 'missing.png'))
            self.assertIsInstance(stockpile.read_metadata(valid.with_suffix('.json')), dict)

    def test_metadata_validation_helpers_define_completion_contract(self):
        digest = 'abc123'
        metadata = dict(width=32, height=24, description='A red image', tags=['red'], sha256=digest)
        self.assertTrue(stockpile.valid_description(' image '))
        self.assertFalse(stockpile.valid_description('  '))
        self.assertFalse(stockpile.valid_description(3))
        self.assertTrue(stockpile.valid_tags(['red', ' plain background ']))
        self.assertFalse(stockpile.valid_tags([]))
        self.assertFalse(stockpile.valid_tags(('red',)))
        self.assertFalse(stockpile.valid_tags([' ']))
        self.assertFalse(stockpile.valid_tags(['x' * 81]))
        self.assertTrue(stockpile.complete(metadata, (32, 24), digest))
        self.assertTrue(stockpile.complete({key: value for key, value in metadata.items() if key != 'sha256'}, (32, 24), digest))
        self.assertFalse(stockpile.complete(metadata, (24, 32), digest))
        self.assertFalse(stockpile.complete({**metadata, 'sha256': 'stale'}, (32, 24), digest))
        self.assertFalse(stockpile.complete(None, (32, 24), digest))

    def test_paths_outside_stockpile_are_rejected(self):
        client = MemoryRedis()
        with TemporaryDirectory() as tmp, self.assertLogs('stockpile', level='ERROR'):
            event = stockpile.run_job(client, dict(job_id='invalid', filename='../outside.png', sha256='x'), tmp)
        self.assertEqual(event['state'], 'stockpile_failed')

    def test_missing_stockpile_returns_empty_scan_result(self):
        client = MemoryRedis()
        with TemporaryDirectory() as tmp:
            self.assertEqual(stockpile.scan_stockpile(client, tmp), {
                'state': 'stockpile_queued', 'scanned': 0, 'complete': 0, 'jobs': [], 'errors': [],
            })
        self.assertEqual(client.jobs, [])

    def test_scan_releases_pending_lock_when_enqueue_fails(self):
        client = MemoryRedis()
        with TemporaryDirectory() as tmp:
            self.make_image(tmp)
            client.publish = Mock(side_effect=RuntimeError('redis publish failed'))
            with self.assertRaisesRegex(RuntimeError, 'redis publish failed'):
                stockpile.scan_stockpile(client, tmp)
        self.assertFalse(client.values, 'Failed enqueue publication must release its deduplication lock')

    def test_atomic_sidecar_writer_cleans_up_when_tempfile_creation_fails(self):
        with TemporaryDirectory() as tmp:
            sidecar = Path(tmp) / 'image.json'
            with patch.object(stockpile.tempfile, 'NamedTemporaryFile', side_effect=OSError('disk full')):
                with self.assertRaisesRegex(OSError, 'disk full'):
                    stockpile._write_metadata_atomically(sidecar, {'tags': ['red']})
            self.assertFalse(sidecar.exists())

    def test_worker_rejects_unsupported_extension_and_releases_pending_lock(self):
        client = MemoryRedis()
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / 'stockpile'
            root.mkdir()
            path = root / 'notes.txt'
            path.write_text('not an image')
            task = dict(job_id='unsupported', filename='notes.txt', sha256='unused')
            event = stockpile.run_job(client, task, tmp)
        self.assertEqual(event['state'], 'stockpile_failed')
        self.assertEqual(event['message'], 'Unsupported stock image')
        self.assertFalse(client.values)

    def test_stale_image_digest_discards_description_and_tags_before_analysis(self):
        client = MemoryRedis()
        with TemporaryDirectory() as tmp:
            path = self.make_image(tmp, metadata=dict(
                description='Stale description', tags=['stale'], source='camera', sha256='old-digest'))
            stockpile.scan_stockpile(client, tmp)
            with patch.object(stockpile, 'analyze', return_value=dict(description='Fresh description', tags=['fresh'])), \
                    patch.object(stockpile, 'matched', return_value=True):
                event = stockpile.run_job(client, client.jobs[0], tmp)
            self.assertEqual(event['state'], 'stockpile_complete')
            metadata = json.loads(path.with_suffix('.json').read_text())
            self.assertEqual(metadata['description'], 'Fresh description')
            self.assertEqual(metadata['tags'], ['fresh'])
            self.assertEqual(metadata['source'], 'camera')

    def test_image_changes_during_analysis_or_matching_abort_sidecar_write(self):
        for stage in ('analysis', 'matching'):
            with self.subTest(stage=stage), TemporaryDirectory() as tmp:
                client = MemoryRedis()
                metadata = {'tags': ['red']} if stage == 'analysis' else {'description': 'Keep me', 'tags': ['red']}
                path = self.make_image(tmp, metadata=metadata)
                current = json.loads(path.with_suffix('.json').read_text())
                current.pop('tag_matching_version', None)
                current.pop('tag_matching_tags_sha256', None)
                current.pop('tag_vocabulary_sha256', None)
                path.with_suffix('.json').write_text(json.dumps(current))
                original_sidecar = path.with_suffix('.json').read_text()
                stockpile.scan_stockpile(client, tmp)

                def change_image(*_args, image_path=path, **_kwargs):
                    Image.new('RGB', (8, 8), 'blue').save(image_path)
                    return dict(description='Fresh', tags=['blue'])

                if stage == 'analysis':
                    patcher = patch.object(stockpile, 'analyze', side_effect=change_image)
                else:
                    patcher = patch.object(stockpile, 'match_tags', side_effect=change_image)
                with patcher, self.assertLogs('stockpile', level='ERROR'):
                    event = stockpile.run_job(client, client.jobs[0], tmp)
                self.assertEqual(event['state'], 'stockpile_failed')
                self.assertEqual(path.with_suffix('.json').read_text(), original_sidecar)
                self.assertFalse(client.values)
