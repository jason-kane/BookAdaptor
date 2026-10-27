"""Normalization events must identify exactly one image prompt variant."""
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from styler import style_scan
from styler.image_tags import fingerprint


class ScanVariantEventsTests(unittest.TestCase):
    def test_skipped_event_preserves_the_prompt_variant(self):
        for variant in ('classic', 'kid_friendly'):
            client = MagicMock()
            client.sismember.return_value = True
            task = dict(job_id='scan', style_tag='artist', engine='krea2',
                        scan_version='v', base_style_prompt=variant)
            with patch('styler.style_scan.publish_queue_event'), patch(
                    'styler.style_scan._finish_scan_job'):
                result = style_scan.run_job(client, task, '/unused')
            self.assertEqual(result['base_style_prompt'], variant)
            self.assertEqual(result['image_tags'], [])

    def test_successful_scan_targets_the_same_variant_it_read(self):
        with TemporaryDirectory() as folder:
            directory = Path(folder) / 'style_exploration' / 'kid_friendly'
            directory.mkdir(parents=True)
            path = directory / 'artist_krea2.png'
            path.write_bytes(b'image')
            path.with_suffix('.json').write_text(json.dumps({
                'tags': ['paint'], 'prompt': 'saved prompt',
            }))
            client = MagicMock()
            client.sismember.return_value = False
            client.get.return_value = None
            task = dict(job_id='scan', style_tag='artist', engine='krea2',
                        scan_version='v', base_style_prompt='kid_friendly',
                        sha256=fingerprint(path))
            with patch('tag_matching.match_tags', return_value={'tags': ['watercolor']}) as match, \
                    patch('styler.tag_consensus.reconcile', return_value={}), \
                    patch('styler.style_scan.publish_queue_event'), \
                    patch('styler.style_scan._finish_scan_job'):
                result = style_scan.run_job(client, task, folder)
            self.assertEqual(result['state'], 'styles_scan_image_complete')
            self.assertEqual(result['base_style_prompt'], 'kid_friendly')
            self.assertEqual(result['image_tags'], ['watercolor'])
            self.assertEqual(match.call_args.kwargs['base_style_prompt'], 'kid_friendly')
            self.assertEqual(json.loads(path.with_suffix('.json').read_text())['base_style_prompt'], 'kid_friendly')
