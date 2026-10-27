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
        self.assertEqual(set(payload['existing_tags']), {'Oil Painting', 'watercolor', 'warm colors'})
        self.assertEqual(payload['source_tags'], ['oil on canvas', 'oil painting', 'new texture'])
        self.assertEqual(result['tags'], ['Oil Painting', 'new texture'])
        self.assertEqual(result['raw_tags'], payload['source_tags'])
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
        self.assertEqual(len(payload['existing_tags']), 2000)

    def test_invalid_mappings_are_rejected(self):
        for text in ('{"tags":[]}', '{"tags":[1]}', '{"tags":[""]}'):
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

    def match_response(self, source, existing, response):
        """Exercise reconciliation without Redis, image files, or model calls."""
        catalog = dict(tags=existing, preferred_tags=[], image_counts={})
        with patch('tag_matching.vocabulary', return_value=catalog), patch(
                'tag_matching.run_llm', return_value=json.dumps(response)) as llm:
            result = match_tags(Mock(), '/unused', source)
        self.assertEqual(llm.call_count, 1)
        return result

    def test_common_presentation_variants_restore_supplied_spellings(self):
        source = ['oil on canvas', 'cel shading', 'artist marks']
        result = self.match_response(
            source, ['Oil Painting', 'flat shading', "artist's marks"],
            {'matches': {
                ' OIL_ON_CANVAS ': '“Ｏｉｌ　Ｐａｉｎｔｉｎｇ”',
                '`cel-shading`': ['flat‑shading'],
                'artist marks': 'artist’s marks',
            }, 'new_tags': None},
        )
        self.assertEqual(result['tags'], ['Oil Painting', 'flat shading', "artist's marks"])
        self.assertEqual([item['source'] for item in result['tag_matches']], source)

    def test_unknown_choices_are_retained_as_new_tags_for_classification(self):
        with self.assertLogs('tag_matching', level='INFO') as captured:
            result = self.match_response(
                ['oil on canvas', 'bold shapes', 'rough marks'], ['Oil Painting'],
                {'matches': {
                    'oil on canvas': 'Oil Painting',
                    'bold shapes': 'imaginary vocabulary tag',
                    'rough marks': 'another invented tag',
                }, 'new_tags': ['new descriptive label']},
            )
        self.assertEqual(result['tags'], [
            'Oil Painting', 'imaginary vocabulary tag', 'another invented tag',
            'new descriptive label',
        ])
        self.assertEqual(result['new_tags'], result['tags'][1:])
        self.assertEqual(result['tag_matches'][1], {
            'source': 'bold shapes', 'tag': 'imaginary vocabulary tag',
        })
        self.assertIn('for classification', '\n'.join(captured.output))

    def test_colliding_aliases_abstain_while_exact_names_still_resolve(self):
        result = self.match_response(
            ['a', 'b', 'c'], ['flat shading', 'flat-shading'],
            {'matches': {'a': 'flat_shading', 'b': 'flat shading', 'c': 'flat-shading'}},
        )
        self.assertEqual(result['tags'], ['flat shading', 'flat-shading'])
        self.assertIsNone(result['tag_matches'][0]['tag'])

    def test_significant_punctuation_is_not_erased(self):
        result = self.match_response(
            ['a', 'b'], ['C++'], {'matches': {'a': 'C', 'b': 'c++'}},
        )
        self.assertEqual(result['tags'], ['C', 'C++'])
        self.assertEqual(result['new_tags'], ['C'])

    def test_malformed_choices_and_null_spellings_abstain(self):
        result = self.match_response(
            ['a', 'b', 'c', 'd', 'e', 'f'], ['Oil Painting'],
            {'matches': {
                'a': 'null', 'b': 'no match', 'c': '', 'd': 42,
                'e': ['Oil Painting', 'watercolor'], 'f': {'tag': 'Oil Painting'},
            }},
        )
        self.assertEqual(result['tags'], [])
        self.assertTrue(all(item['tag'] is None for item in result['tag_matches']))

    def test_new_tag_string_must_still_resolve_to_a_source_tag(self):
        result = self.match_response(
            ['rough marks'], [], {'matches': {}, 'new_tags': '“rough-marks”'},
        )
        self.assertEqual(result['tags'], ['rough marks'])

    def test_source_alias_collision_does_not_assign_the_wrong_source(self):
        result = self.match_response(
            ['flat shading', 'flat-shading'], ['Oil Painting'],
            {'matches': {'flat_shading': 'Oil Painting'}},
        )
        self.assertEqual(result['tags'], [])
        self.assertEqual(len(result['tag_matches']), 2)

    def test_legacy_choices_can_also_introduce_new_labels(self):
        result = self.match_response(
            ['rough marks'], ['Oil Painting'], {'tags': ['expressive texture']},
        )
        self.assertEqual(result['tags'], ['expressive texture'])
        self.assertEqual(result['new_tags'], ['expressive texture'])

    def test_empty_and_quoted_null_labels_do_not_become_new_tags(self):
        result = self.match_response(
            ['rough marks'], [],
            {'matches': {'rough marks': '“null”'}, 'new_tags': ['``', '  ', 'None']},
        )
        self.assertEqual(result['tags'], [])
        self.assertEqual(result['new_tags'], [])

    def test_existing_new_tag_alias_reuses_the_canonical_spelling(self):
        result = self.match_response(
            ['a'], ['Oil Painting'],
            {'matches': {'a': None}, 'new_tags': ['oil_painting']},
        )
        self.assertEqual(result['tags'], ['Oil Painting'])
        self.assertEqual(result['new_tags'], [])

    def test_cut_off_new_tags_keep_complete_matches_and_novel_labels(self):
        catalog = dict(tags=['Oil Painting'], preferred_tags=[], image_counts={})
        text = '{"matches":{"oil on canvas":"Oil Painting"},"new_tags":["rough texture","unfinished'
        with patch('tag_matching.vocabulary', return_value=catalog), patch(
                'tag_matching.run_llm', return_value=text) as llm:
            result = match_tags(Mock(), '/unused', ['oil on canvas'])
        self.assertEqual(result['tags'], ['Oil Painting', 'rough texture'])
        self.assertEqual(result['new_tags'], ['rough texture'])
        llm.assert_called_once()

    def test_cut_off_mapping_never_completes_a_partial_choice(self):
        catalog = dict(tags=['Oil Painting'], preferred_tags=[], image_counts={})
        text = '{"matches":{"a":"Oil Painting","b":"unfinished'
        with patch('tag_matching.vocabulary', return_value=catalog), patch(
                'tag_matching.run_llm', return_value=text) as llm:
            result = match_tags(Mock(), '/unused', ['a', 'b'])
        self.assertEqual(result['tags'], ['Oil Painting'])
        self.assertEqual(result['tag_matches'][1], {'source': 'b', 'tag': None})
        self.assertEqual(result['raw_tags'], ['a', 'b'])
        llm.assert_called_once()

    def test_recovery_preserves_escaped_quotes_and_delimiters_in_labels(self):
        catalog = dict(tags=[], preferred_tags=[], image_counts={})
        label = 'texture "rough,]"'
        text = '```json\n' + json.dumps({'matches': {'a': label}})[:-1] + ',"new_tags":["cut'
        with patch('tag_matching.vocabulary', return_value=catalog), patch(
                'tag_matching.run_llm', return_value=text):
            result = match_tags(Mock(), '/unused', ['a'])
        self.assertEqual(result['tags'], [label])

    def test_recovery_keeps_new_tags_when_the_model_puts_them_first(self):
        catalog = dict(tags=[], preferred_tags=[], image_counts={})
        text = '{"new_tags":["novel texture","cut'
        with patch('tag_matching.vocabulary', return_value=catalog), patch(
                'tag_matching.run_llm', return_value=text):
            result = match_tags(Mock(), '/unused', ['a'])
        self.assertEqual(result['tags'], ['novel texture'])

    def test_unrecoverable_cut_off_response_still_retries(self):
        catalog = dict(tags=['Oil Painting'], preferred_tags=[], image_counts={})
        with patch('tag_matching.vocabulary', return_value=catalog), patch(
                'tag_matching.run_llm', side_effect=[
                    '{"matches":{"a":"Oil Paint',
                    '{"matches":{"a":"Oil Painting"},"new_tags":[]}',
                ]) as llm:
            result = match_tags(Mock(), '/unused', ['a'])
        self.assertEqual(result['tags'], ['Oil Painting'])
        self.assertEqual(llm.call_count, 2)

    def test_every_dropped_source_logs_its_value_engine_and_actual_reason(self):
        catalog = dict(tags=['flat shading', 'flat-shading'], preferred_tags=[], image_counts={})
        response = {
            'matches': {'null tag': None, 'bad type': 42, 'alias': 'flat_shading',
                        'unknown key': 'irrelevant choice'},
            'new_tags': [''],
        }
        with patch('tag_matching.vocabulary', return_value=catalog), patch(
                'tag_matching.run_llm', return_value=json.dumps(response)), \
                self.assertLogs('tag_matching', level='WARNING') as captured:
            result = match_tags(Mock(), '/unused', ['null tag', 'missing', 'bad type', 'alias'],
                                style_name='artist', engine='qwen')
        output = '\n'.join(captured.output)
        self.assertEqual(result['tags'], [])
        for value in ('null tag', 'missing', 'bad type', 'alias', 'unknown key', '42', 'flat_shading'):
            self.assertIn(value, output)
        for reason in ('matcher returned null/no match', 'matcher omitted',
                       'received int', 'ambiguous alias', 'mapping key is unknown',
                       'new tag is empty'):
            self.assertIn(reason, output)
        self.assertIn('style=artist engine=qwen', output)
