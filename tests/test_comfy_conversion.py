"""Workflow conversion failures must retain the CLI error and allow recovery."""
import json
import os
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock
from urllib.parse import urlsplit

from test_comfy_progress import ROOT, load_definitions


class ConversionTests(unittest.TestCase):
    def converter(self, responses, times=(0, 1)):
        runner = MagicMock(side_effect=[subprocess.CompletedProcess(
            [], code, json.dumps(body).encode(), b'') for code, body in responses])
        clock = SimpleNamespace(monotonic=MagicMock(side_effect=times), sleep=MagicMock())
        ns = dict(Any=object, json=json, os=os, tempfile=tempfile, urlsplit=urlsplit,
                  const=SimpleNamespace(COMFYUI_API_URL='http://render:9191/'),
                  subprocess=SimpleNamespace(run=runner), time=clock, log=MagicMock())
        load_definitions(ROOT / 'src/comfy.py',
                         {'Workflow', 'workflow_type', 'ui_to_api_workflow'}, ns)
        return lambda: ns['ui_to_api_workflow'](ns['Workflow']({'nodes': []})), runner, clock

    def assert_cleaned(self, runner):
        args = runner.call_args.args[0]
        self.assertFalse(os.path.exists(args[args.index('--workflow') + 1]))

    def test_connection_failure_then_recovery(self):
        convert, runner, clock = self.converter([
            (1, {'ok': False, 'data': None, 'error': {
                'code': 'connection_error', 'message': 'Connection refused'}}),
            (0, {'ok': True, 'data': {'prompt': {'1': {'class_type': 'Test'}}}}),
        ])
        self.assertIn('1', convert().serialize())
        self.assertEqual(runner.call_count, 2)
        clock.sleep.assert_called_once_with(2)
        args = runner.call_args.args[0]
        self.assertEqual(args[args.index('--host') + 1], 'render')
        self.assertEqual(args[args.index('--port') + 1], '9191')
        self.assert_cleaned(runner)

    def test_persistent_connection_failure_reports_original_error(self):
        convert, runner, clock = self.converter([(1, {'data': None, 'error': {
            'code': 'connection_error', 'message': 'Connection refused'}})], times=(0, 61))
        with self.assertRaisesRegex(RuntimeError, 'Connection refused'):
            convert()
        clock.sleep.assert_not_called()
        self.assert_cleaned(runner)

    def test_invalid_workflow_is_not_retried(self):
        convert, runner, clock = self.converter([(1, {'data': None, 'error': {
            'code': 'validation_error', 'message': 'Unknown node'}})])
        with self.assertRaisesRegex(RuntimeError, 'Unknown node'):
            convert()
        clock.sleep.assert_not_called()
        self.assert_cleaned(runner)

    def test_missing_prompt_is_reported(self):
        convert, runner, _ = self.converter([(0, {'ok': True, 'data': {}})])
        with self.assertRaisesRegex(RuntimeError, 'no workflow prompt'):
            convert()
        self.assert_cleaned(runner)
