import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from tag_matching import match_tags, matched, vocabulary


class TagMatchingTests(unittest.TestCase):
    def client(self):
        client = Mock()
        client.smembers.side_effect = lambda key: {'Oil Painting', 'watercolor'} if key == 'tags' else {'warm colors'}
        return client

    def test_full_vocabulary_includes_styles_stockpile_and_unpromoted_image_tags(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            for folder, name, tag in [('stockpile', 'photo', 'dark background'),
                                      ('style_exploration', 'artist_zit', 'loose brushwork')]:
                directory = root / folder
                directory.mkdir()
                (directory / (name + '.png')).write_bytes(b'image')
                (directory / (name + '.json')).write_text(json.dumps(dict(tags=[tag])))
            result = vocabulary(self.client(), tmp)
            self.assertEqual(set(result['tags']), {'Oil Painting', 'watercolor', 'warm colors', 'dark background', 'loose brushwork'})

    def test_synonyms_collapse_but_originals_and_mapping_are_recorded(self):
        with TemporaryDirectory() as tmp, patch('tag_matching.run_llm', return_value=
                '```json\n{"tags":["Oil Painting","Oil Painting","new texture",],}\n```') as llm:
            result = match_tags(self.client(), tmp, ['oil on canvas', 'oil painting', 'new texture'])
        payload = json.loads(llm.call_args.args[0].split('\n', 1)[1])
        self.assertEqual(set(payload['all_tags']), {'Oil Painting', 'watercolor', 'warm colors', 'oil on canvas', 'new texture'})
        self.assertEqual(payload['item_tags'], ['oil on canvas', 'oil painting', 'new texture'])
        self.assertEqual(result['tags'], ['Oil Painting', 'new texture'])
        self.assertEqual(result['raw_tags'], payload['item_tags'])
        self.assertEqual(result['tag_matches'][0], dict(source='oil on canvas', tag='Oil Painting'))
        self.assertTrue(matched(result))
        result['tags'].append('edited')
        self.assertFalse(matched(result))

    def test_no_shortlist_is_applied_to_the_vocabulary(self):
        client = self.client()
        client.smembers.side_effect = lambda key: {f'known tag {i}' for i in range(2000)}
        with TemporaryDirectory() as tmp, patch('tag_matching.run_llm', return_value='{"tags":["novel texture"]}') as llm:
            match_tags(client, tmp, ['novel texture'])
        payload = json.loads(llm.call_args.args[0].split('\n', 1)[1])
        self.assertEqual(len(payload['all_tags']), 2001)

    def test_invalid_mappings_are_rejected(self):
        for text in ('{"tags":[]}', '{"tags":["invented tag"]}', '{"tags":[1]}', '{"tags":[""]}'):
            with self.subTest(text=text), TemporaryDirectory() as tmp, patch('tag_matching.run_llm', return_value=text):
                with self.assertRaises(ValueError):
                    match_tags(self.client(), tmp, ['oil on canvas'])

    def test_explicit_mapping_handles_multiple_inputs_for_one_canonical_tag(self):
        with TemporaryDirectory() as tmp, patch('tag_matching.run_llm', return_value=json.dumps({
                'matches': {'oil on canvas': 'Oil Painting', 'oil painting': 'Oil Painting'}})):
            result = match_tags(self.client(), tmp, ['oil on canvas', 'oil painting'])
        self.assertEqual(result['tags'], ['Oil Painting'])
        self.assertEqual(len(result['tag_matches']), 2)

    def test_invalid_cached_shape_is_retried_with_explicit_coverage_instruction(self):
        with TemporaryDirectory() as tmp, patch('tag_matching.run_llm', side_effect=[
                '{"tags":[]}', '{"matches":{"oil on canvas":"Oil Painting"}}']) as llm:
            result = match_tags(self.client(), tmp, ['oil on canvas'])
        self.assertEqual(result['tags'], ['Oil Painting'])
        self.assertEqual(llm.call_count, 2)
        self.assertEqual(llm.call_args.kwargs, {'force': True})

    def test_abstention_does_not_call_llm(self):
        with TemporaryDirectory() as tmp, patch('tag_matching.run_llm') as llm:
            result = match_tags(self.client(), tmp, [])
        self.assertEqual(result['tags'], [])
        self.assertTrue(matched(result))
        llm.assert_not_called()
