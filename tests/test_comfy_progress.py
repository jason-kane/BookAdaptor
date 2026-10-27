"""ComfyUI event monitoring and independent video progress, without services."""
import ast
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))


def load_definitions(path, names, namespace):
    tree = ast.parse(path.read_text())
    nodes = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name in names]
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), namespace)


class ComfyProgressTests(unittest.TestCase):
    def runner(self, events):
        import time
        import uuid
        from contextvars import ContextVar
        from urllib.parse import urlsplit, urlunsplit
        connection = MagicMock()
        connection.receive.side_effect = [json.dumps(event) if isinstance(event, dict) else event for event in events]
        response = SimpleNamespace(status_code=200, json=lambda: {'prompt_id': 'ours'})
        client = MagicMock()
        client.__enter__.return_value.post.return_value = response
        sink = ContextVar('test_progress', default=None)
        reports = []
        sink.set(lambda *args: reports.append(args))
        ns = dict(json=json, time=time, uuid=uuid, urlsplit=urlsplit, urlunsplit=urlunsplit,
                  Any=object, workflow_progress=sink,
                  const=SimpleNamespace(COMFYUI_JOB_TIMEOUT_SECONDS=30, COMFYUI_API_URL='http://comfy:8188/'),
                  httpx=SimpleNamespace(Client=lambda **kw: client, Timeout=lambda **kw: None),
                  simple_websocket=SimpleNamespace(Client=SimpleNamespace(connect=lambda *a, **kw: connection)),
                  workflow_type=lambda workflow: 'api',
                  WORKFLOW_HTTP_TIMEOUT=None, WEBSOCKET_PING_INTERVAL_SECONDS=25,
                  _websocket_url=lambda url, client_id: 'ws://comfy/ws?clientId=' + client_id,
                  log=MagicMock())
        load_definitions(ROOT/'src/comfy.py', {
            'Workflow', 'run_workflow', '_report_workflow_event', '_node_title',
            '_handle_execution_failure',
        }, ns)
        return ns['run_workflow'], connection, client, reports

    def test_progress_is_filtered_and_finishes_without_polling(self):
        run, ws, client, reports = self.runner([
            b'preview',
            {'type': 'executing', 'data': {'prompt_id': 'other', 'node': None}},
            {'type': 'executing', 'data': {'prompt_id': 'ours', 'node': '1'}},
            {'type': 'progress', 'data': {'prompt_id': 'ours', 'value': 3, 'max': 4}},
            {'type': 'executing', 'data': {'prompt_id': 'ours', 'node': None}},
        ])
        run({'1': {'class_type': 'KSampler'}})
        self.assertIn((75, 'ComfyUI: KSampler (3/4)'), reports)
        self.assertEqual(reports[-1], (100, 'ComfyUI: Task complete'))
        client.__enter__.return_value.get.assert_not_called()
        ws.close.assert_called_once()
        self.assertIn('client_id', client.__enter__.return_value.post.call_args.kwargs['json'])

    def test_cached_workflow_success_and_disconnect(self):
        run, ws, _, reports = self.runner([
            {'type': 'execution_success', 'data': {'prompt_id': 'ours'}}
        ])
        run({'1': {}})
        self.assertEqual(reports[-1][0], 100)
        ws.close.assert_called_once()
        run, ws, _, _ = self.runner([''])
        with self.assertRaisesRegex(RuntimeError, 'disconnected'):
            run({'1': {}})
        ws.close.assert_called_once()

    def test_execution_error_keeps_oom_text_for_retry(self):
        run, ws, _, _ = self.runner([{'type': 'execution_error', 'data': {
            'prompt_id': 'ours', 'exception_message': 'CUDA out of memory'}}])
        with self.assertRaisesRegex(RuntimeError, 'out of memory'):
            run({'1': {}})
        ws.close.assert_called_once()

    def test_contradictory_oom_restarts_and_reports_recovery(self):
        error = ('Allocation on device 0 would exceed allowed memory. (out of memory)\n'
                 'Currently allocated: 0 bytes\nRequested: 1.18 GiB\n'
                 'Device limit: 11.63 GiB\nFree (according to CUDA): 10.94 GiB')
        run, ws, _, reports = self.runner([{'type': 'execution_error', 'data': {
            'prompt_id': 'ours', 'exception_message': error}}])
        with patch('comfy_recovery.restart_comfyui', return_value='ComfyUI was restarted') as restart:
            with self.assertRaisesRegex(RuntimeError, 'ComfyUI was restarted'):
                run({'1': {}})
            restart.assert_called_once()
        ws.close.assert_called_once()
        self.assertIn('restarting ComfyUI', reports[1][1])

    def test_timeout_closes_connection(self):
        run, ws, _, _ = self.runner([None])
        with self.assertRaises(TimeoutError):
            run({'1': {}})
        ws.close.assert_called_once()

    def test_execution_start_and_zero_max_progress(self):
        run, _, _, reports = self.runner([
            {'type': 'execution_start', 'data': {'prompt_id': 'ours'}},
            {'type': 'progress', 'data': {'prompt_id': 'ours', 'node': '1', 'value': 3, 'max': 0}},
            {'type': 'progress', 'data': {'prompt_id': 'ours', 'node': '1', 'value': 5, 'max': 4}},
            {'type': 'execution_success', 'data': {'prompt_id': 'ours'}},
        ])
        run({'1': {'class_type': 'KSampler'}})
        self.assertIn((0, 'ComfyUI: Starting workflow'), reports)
        self.assertIn((0, 'ComfyUI: KSampler (3/0)'), reports)
        self.assertIn((100, 'ComfyUI: KSampler (5/4)'), reports)

    def test_interrupted_execution_is_reported_as_failure(self):
        run, ws, _, reports = self.runner([{'type': 'execution_interrupted', 'data': {
            'prompt_id': 'ours', 'exception_message': 'Cancelled by user'}}])
        with self.assertRaisesRegex(RuntimeError, 'execution_interrupted: Cancelled by user'):
            run({'1': {}})
        self.assertIn((0, 'ComfyUI: Cancelled by user'), reports)
        ws.close.assert_called_once()

    def test_unrecognized_matching_events_are_ignored(self):
        run, _, _, reports = self.runner([
            {'type': 'queue_remaining', 'data': {'prompt_id': 'ours'}},
            {'type': 'execution_success', 'data': {'prompt_id': 'ours'}},
        ])
        run({'1': {}})
        self.assertEqual(reports, [
            (0, 'ComfyUI: Queued'), (100, 'ComfyUI: Task complete')
        ])

    def test_deadline_expired_before_receive_times_out(self):
        run, ws, _, _ = self.runner([])
        run.__globals__['time'] = SimpleNamespace(monotonic=MagicMock(side_effect=[0, 31]))
        with self.assertRaises(TimeoutError):
            run({'1': {}})
        ws.receive.assert_not_called()
        ws.close.assert_called_once()

    def test_oom_recovery_exception_is_included_in_reported_failure(self):
        error = ('Allocation on device 0 would exceed allowed memory. (out of memory)\n'
                 'Currently allocated: 0 bytes\nRequested: 1.18 GiB\n'
                 'Device limit: 11.63 GiB\nFree (according to CUDA): 10.94 GiB')
        run, ws, _, _ = self.runner([{'type': 'execution_error', 'data': {
            'prompt_id': 'ours', 'exception_message': error}}])
        with patch('comfy_recovery.restart_comfyui', side_effect=OSError('docker unavailable')):
            with self.assertRaisesRegex(RuntimeError, 'restart failed: docker unavailable'):
                run({'1': {}})
        ws.close.assert_called_once()

    def test_secondary_progress_preserves_primary(self):
        ns = {'json': json}
        load_definitions(ROOT/'src/websocket/server.py',
                         {'publish_progress', 'publish_comfy_progress', 'progress_html'}, ns)
        ns['VIDEO_EVENTS'] = 'events'
        ns['publish_queue_event'] = lambda *args: None
        saved = {}
        client = MagicMock()
        client.hgetall.side_effect = lambda key: saved.copy()
        client.hset.side_effect = lambda key, mapping: saved.update(mapping)
        ns['publish_progress'](client, 'job', 40, 'Image 5: Generating Animation')
        ns['publish_comfy_progress'](client, 'job', 75, 'ComfyUI: Sampling <node>')
        self.assertEqual(saved['percent'], 40)
        html = ns['progress_html'](saved)
        self.assertEqual(html.count('<wa-progress-bar'), 2)
        self.assertIn('Sampling &lt;node&gt;', html)
        ns['publish_progress'](client, 'job', 65, 'Rendering frames')
        self.assertEqual(saved['comfy_percent'], 75)


if __name__ == '__main__':
    unittest.main()
