import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from styler import image_tags
from styler.jobs import sample_task


class ImageTagTests(unittest.TestCase):
    def test_analysis_matches_tags_before_saving_and_consensus(self):
        with TemporaryDirectory() as tmp:
            folder = Path(tmp) / 'style_exploration'
            folder.mkdir()
            path = folder / 'artist_qwen.png'
            path.write_bytes(b'image')
            task = sample_task(dict(style_tag='artist', engine='qwen'))
            task['sha256'] = image_tags.fingerprint(path)
            client = MagicMock()
            client.get.return_value = None
            client.smembers.side_effect = lambda key: {'oil painting'} if key == 'tags' else set()
            raw = ['oil on canvas', 'warm palette', 'brushwork', 'soft edges']
            canonical = ['oil painting', 'warm palette', 'brushwork', 'soft edges']
            with patch.dict(sys.modules, {'const': SimpleNamespace(LIBRARY_DIR=tmp)}), \
                    patch.object(image_tags, 'analyze', return_value=raw), \
                    patch('tag_matching.run_llm', return_value=json.dumps({'tags': canonical})):
                image_tags.run_job(client, task)
            result = json.loads(path.with_suffix('.json').read_text())
            self.assertEqual(result['raw_tags'], raw)
            self.assertEqual(result['tags'], canonical)
            self.assertEqual(result['tag_matches'][0], dict(source='oil on canvas', tag='oil painting'))
            record = json.loads(client.pipeline.return_value.__enter__.return_value.set.call_args.args[1])
            self.assertEqual(record['tags'], canonical)
            self.assertEqual(json.loads(client.publish.call_args.args[1])['image_tags'], canonical)

    def test_parse_accepts_up_to_eight_distinct_tags(self):
        self.assertEqual(image_tags.parse_tags('{"tags":["Oil Painting","warm  palette","Brushwork","soft edges"]}'),
                         ['oil painting','warm palette','brushwork','soft edges'])
        self.assertEqual(image_tags.parse_tags('{"tags":["a","a","b"]}'), ['a', 'b'])
        self.assertEqual(len(image_tags.parse_tags(json.dumps({'tags': list('abcdefgh')}))), 8)
        for text in (json.dumps({'tags': list('abcdefghi')}), '{"tags":[1,2,3]}', 'not json'):
            with self.assertRaises(ValueError):
                image_tags.parse_tags(text)

    def test_explicit_abstention_is_valid(self):
        self.assertEqual(image_tags.parse_tags('{"tags":[]}'), [])

    def test_common_model_formatting_is_tolerated(self):
        examples = [
            '{"tags": ["Oil Painting", "soft edges",],}',
            '```json\n{"tags": ["Oil Painting", "soft edges",]}\n```',
            'Here are the tags:\n{"tags": ["Oil Painting", "soft edges"]}\nDone.',
            "{'tags': ['Oil Painting', 'soft edges',],}",
            '<think>Consider [medium] first.</think>\n["Oil Painting", "soft edges",]',
            '\ufeff["Oil Painting", "soft edges"]',
        ]
        for text in examples:
            with self.subTest(text=text):
                self.assertEqual(image_tags.parse_tags(text), ['oil painting', 'soft edges'])

    def test_format_repair_preserves_string_contents(self):
        tags = ['comma,]', 'comma,}', 'quoted "word"', "artist's marks", 'back\\slash']
        text = json.dumps({'tags': tags})
        text = text[:-2] + ',],}'
        self.assertEqual(image_tags.parse_tags(text), tags)

    def test_format_repair_does_not_accept_invalid_or_truncated_results(self):
        for text in ('{"tags": ["ink",', '{"wrong": ["ink"]}',
                     '{"tags": ["ink", 1,]}', '{"tags": [""]}',
                     '{"tags": "ink"}', "{'tags': ('ink',)}", '{"tags": ["' + 'a' * 81 + '"]}'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                image_tags.parse_tags(text)

    def test_unchanged_samples_reuse_tags_and_new_samples_queue_once(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'image.png'; path.write_bytes(b'image')
            task = sample_task(dict(style_tag='artist', engine='qwen'))
            client = MagicMock(); client.get.return_value = None
            client.set.side_effect = [True, False]
            image_tags.schedule(client, task, path)
            image_tags.schedule(client, task, path)
            client.rpush.assert_called_once()
            client.get.return_value = json.dumps(dict(sha256=image_tags.fingerprint(path), tags=['a','b','c','d'], analysis_version=image_tags.ANALYSIS_VERSION, prompt_sha256=image_tags.hashlib.sha256(b'{prompt}').hexdigest()))
            self.assertEqual(image_tags.schedule(client, task, path), ['a','b','c','d'])
            client.rpush.assert_called_once()

    def test_analysis_persists_image_specific_tags_and_publishes(self):
        with TemporaryDirectory() as tmp:
            folder = Path(tmp) / 'style_exploration'; folder.mkdir()
            path = folder / 'artist_qwen.png'; path.write_bytes(b'image')
            task = sample_task(dict(style_tag='artist', engine='qwen'))
            task['sha256'] = image_tags.fingerprint(path)
            client = MagicMock(); client.get.return_value = None
            with patch.dict(sys.modules, {'const':SimpleNamespace(LIBRARY_DIR=tmp)}), patch.object(image_tags, 'analyze', return_value=['oil painting','warm palette','brushwork','soft edges']):
                image_tags.run_job(client, task)
            record = json.loads(path.with_suffix('.tags.json').read_text())
            self.assertEqual(record['engine'], 'qwen')
            self.assertEqual(record['sha256'], task['sha256'])
            event = json.loads(client.publish.call_args.args[1])
            self.assertEqual(event['state'], 'prompt_tagged')
            self.assertEqual(event['image_tags'], record['tags'])
            self.assertEqual(event['tags'], [], 'One engine alone cannot tag the prompt')
            client.pipeline.return_value.__enter__.return_value.sadd.assert_any_call('styler:image-tag-names', *record['tags'])

    def test_manual_retag_bypasses_cached_tags_but_deduplicates_pending_jobs(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'image.png'
            path.write_bytes(b'image')
            task = sample_task(dict(style_tag='artist', engine='qwen'))
            client = MagicMock()
            client.get.return_value = json.dumps(dict(
                sha256=image_tags.fingerprint(path), tags=['a', 'b', 'c', 'd'],
                analysis_version=image_tags.ANALYSIS_VERSION,
                prompt_sha256=image_tags.hashlib.sha256(b'{prompt}').hexdigest()))
            client.set.side_effect = [True, False]
            image_tags.schedule(client, task, path, force=True)
            image_tags.schedule(client, task, path, force=True)
            client.rpush.assert_called_once()
            self.assertEqual(json.loads(client.rpush.call_args.args[1])['job_id'], task['job_id'])
            client.delete.assert_not_called()

    def test_replaced_image_is_requeued_without_old_tags(self):
        with TemporaryDirectory() as tmp:
            folder = Path(tmp) / 'style_exploration'; folder.mkdir()
            path = folder / 'artist_zit.png'; path.write_bytes(b'old')
            task = sample_task(dict(style_tag='artist', engine='zit'))
            task['sha256'] = image_tags.fingerprint(path)
            client = MagicMock()
            def analyze(_):
                path.write_bytes(b'replacement')
                return ['a','b','c']
            with patch.dict(sys.modules, {'const':SimpleNamespace(LIBRARY_DIR=tmp)}), patch.object(image_tags, 'analyze', side_effect=analyze), patch.object(image_tags, 'schedule') as schedule:
                image_tags.run_job(client, task)
                schedule.assert_called_once()
            client.publish.assert_not_called()
            client.pipeline.assert_not_called()
