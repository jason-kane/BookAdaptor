"""Diagnostics distinguish model abstention from parser rejection."""
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from styler.image_tags import parse_analysis


class ImageTagLoggingTests(unittest.TestCase):
    def test_empty_vision_response_is_logged_as_model_output(self):
        response = json.dumps(dict(tags=[], character_placement='uncertain'))
        with self.assertLogs('styler.image_tags', level='INFO') as captured:
            result = parse_analysis(response, context='artist/qwen (classic)')
        self.assertEqual(result['tags'], [])
        output = '\n'.join(captured.output)
        self.assertIn('artist/qwen (classic)', output)
        self.assertIn('model supplied an empty tags array', output)
        self.assertIn(response, output)

    def test_invalid_tag_values_and_schema_log_the_rejection_reason(self):
        examples = [
            ({'tags': [''], 'character_placement': 'uncertain'}, 'empty after normalization', None),
            ({'tags': ['x' * 81], 'character_placement': 'uncertain'}, 'exceeds 80 characters', None),
            ({'tags': [42], 'character_placement': 'uncertain'}, 'expected an array of strings', None),
            ({'tags': ['watercolor'], 'character_placement': 'unknown'}, 'invalid character_placement', None),
            ({'tags': ['watercolor'], 'character_placement': 'uncertain', 'primary_category': 'wrong'},
             "primary_category 'wrong'", ['allowed']),
        ]
        for payload, reason, categories in examples:
            with self.subTest(reason=reason), self.assertLogs('styler.image_tags', level='WARNING') as captured:
                with self.assertRaises(ValueError):
                    parse_analysis(json.dumps(payload), categories, context='artist/krea2 (classic)')
            output = '\n'.join(captured.output)
            self.assertIn(reason, output)
            self.assertIn('artist/krea2 (classic)', output)
            self.assertIn(repr(payload['tags'][0]), output)

    def test_invalid_json_logs_the_actual_response(self):
        response = '{"tags":["cut off'
        with self.assertLogs('styler.image_tags', level='WARNING') as captured:
            with self.assertRaises(ValueError):
                parse_analysis(response, context='artist/qwen')
        self.assertIn(response, '\n'.join(captured.output))
