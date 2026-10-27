"""Workflow conversion failures must retain the CLI error and allow recovery."""
import json
import os
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch
from urllib.parse import urlsplit

from test_comfy_progress import ROOT, load_definitions


class ConversionTests(unittest.TestCase):
    def converter(self, responses, times=(0, 1)):
        runner = MagicMock(side_effect=[subprocess.CompletedProcess(
            [], code, json.dumps(body).encode(), b'') for code, body in responses])
        clock = SimpleNamespace(monotonic=MagicMock(side_effect=times), sleep=MagicMock())
        ns = dict(Any=object, json=json, os=os, tempfile=tempfile, urlsplit=urlsplit,
                  const=SimpleNamespace(COMFYUI_API_URL='http://render:9191/'),
                  subprocess=SimpleNamespace(run=runner), time=clock, log=MagicMock(),
                  WORKFLOW_CONVERSION_RETRY_SECONDS=60,
                  WORKFLOW_CONVERSION_RETRY_INTERVAL_SECONDS=2)
        load_definitions(ROOT / 'src/comfy.py',
                         {'Workflow', 'workflow_type', 'ui_to_api_workflow'}, ns)
        def convert():
            return ns['ui_to_api_workflow'](ns['Workflow']({'nodes': []}))
        convert.log = ns['log']
        return convert, runner, clock

    def assert_cleaned(self, runner):
        args = runner.call_args.args[0]
        self.assertFalse(os.path.exists(args[args.index('--workflow') + 1]))

    def test_connection_failure_then_recovery(self):
        convert, runner, clock = self.converter([
            (1, {'ok': False, 'data': None, 'error': {
                'code': 'connection_error', 'message': 'Connection refused'}}),
            (0, {'ok': True, 'data': {'prompt': {'1': {'class_type': 'Test'}}}}),
        ])
        startup = SimpleNamespace(ensure_worker_services=MagicMock())
        with patch.dict(sys.modules, {'worker_startup': startup}):
            self.assertIn('1', convert().serialize())
        self.assertEqual(runner.call_count, 2)
        startup.ensure_worker_services.assert_called_once_with(comfyui=True)
        clock.sleep.assert_called_once_with(2)
        args = runner.call_args.args[0]
        self.assertEqual(args[args.index('--host') + 1], 'render')
        self.assertEqual(args[args.index('--port') + 1], '9191')
        self.assert_cleaned(runner)

    def test_retries_connection_failures_without_repeating_startup_request(self):
        convert, runner, clock = self.converter([
            (1, {'error': {'code': 'connection_error', 'message': 'starting'}}),
            (1, {'error': {'code': 'connection_error', 'message': 'still starting'}}),
            (0, {'data': {'prompt': {'1': {'class_type': 'Node'}}}}),
        ], times=(0, 1, 2))
        startup = SimpleNamespace(ensure_worker_services=MagicMock())
        with patch.dict(sys.modules, {'worker_startup': startup}):
            self.assertIn('1', convert().serialize())
        self.assertEqual(runner.call_count, 3)
        self.assertEqual(clock.sleep.call_count, 2)
        startup.ensure_worker_services.assert_called_once_with(comfyui=True)

    def test_startup_request_failure_is_logged_but_conversion_keeps_retrying(self):
        convert, runner, _ = self.converter([
            (1, {'error': {'code': 'connection_error', 'message': 'starting'}}),
            (0, {'data': {'prompt': {'1': {'class_type': 'Node'}}}}),
        ])
        startup = SimpleNamespace(
            ensure_worker_services=MagicMock(side_effect=RuntimeError('startup failed'))
        )
        with patch.dict(sys.modules, {'worker_startup': startup}):
            self.assertIn('1', convert().serialize())
        convert.log.exception.assert_called_once()
        self.assertEqual(runner.call_count, 2)

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

    def test_invalid_json_includes_cli_diagnostics_and_cleans_temp_file(self):
        convert, runner, _ = self.converter([])
        runner.side_effect = [subprocess.CompletedProcess(
            [], 2, b'not-json', b'conversion crashed')]
        with self.assertRaisesRegex(RuntimeError, 'conversion crashed'):
            convert()
        self.assert_cleaned(runner)

    def test_non_object_json_response_is_rejected(self):
        convert, runner, _ = self.converter([])
        runner.side_effect = [subprocess.CompletedProcess([], 0, b'[]', b'')]
        with self.assertRaisesRegex(TypeError, 'invalid response'):
            convert()
        self.assert_cleaned(runner)

    def test_non_object_cli_error_is_preserved_as_text(self):
        convert, runner, _ = self.converter([(1, {'error': 'unexpected CLI error'})])
        with self.assertRaisesRegex(RuntimeError, 'unexpected CLI error'):
            convert()
        self.assert_cleaned(runner)

    def test_non_ui_workflow_is_rejected_before_cli_invocation(self):
        from test_comfy_progress import ROOT, load_definitions
        from urllib.parse import urlsplit

        ns = dict(Any=object, json=json, os=os, tempfile=tempfile, urlsplit=urlsplit,
                  const=SimpleNamespace(COMFYUI_API_URL='http://render:9191/'),
                  subprocess=SimpleNamespace(run=MagicMock()), time=SimpleNamespace(),
                  log=MagicMock(), WORKFLOW_CONVERSION_RETRY_SECONDS=60,
                  WORKFLOW_CONVERSION_RETRY_INTERVAL_SECONDS=2)
        load_definitions(ROOT / 'src/comfy.py',
                         {'Workflow', 'workflow_type', 'ui_to_api_workflow'}, ns)
        with self.assertRaisesRegex(ValueError, "not 'ui' format"):
            ns['ui_to_api_workflow'](ns['Workflow']({'1': {
                'class_type': 'Node', 'inputs': {}}}))
        ns['subprocess'].run.assert_not_called()
