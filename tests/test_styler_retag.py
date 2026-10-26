from pathlib import Path
import ast
import json
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from styler import image_tags


class RetagTests(unittest.TestCase):
    def test_completed_style_resolves_lost_only_with_all_image_and_style_tags(self):
        with TemporaryDirectory() as tmp:
            folder = Path(tmp) / 'style_exploration'
            folder.mkdir()
            client = Mock()
            client.eval.return_value = 3
            for engine in ('zit', 'krea2', 'qwen'):
                path = folder / f'artist_{engine}.png'
                path.write_bytes(b'image')
                path.with_suffix('.json').write_text(json.dumps(dict(tags=['ink'])))
            self.assertEqual(image_tags.completed_style_status(client, 'artist', tmp),
                             dict(state='lost_resolved', style_tag='artist', lost_count=3))
            client.eval.return_value = -1
            self.assertIsNone(image_tags.completed_style_status(client, 'artist', tmp))
            client.reset_mock()
            (folder / 'artist_zit.json').write_text(json.dumps(dict(tags=[])))
            self.assertIsNone(image_tags.completed_style_status(client, 'artist', tmp))
            client.eval.assert_not_called()

    def test_priority_request_warns_when_worker_is_busy(self):
        from styler.jobs import sample_task
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'artist_zit.png'
            path.write_bytes(b'image')
            path.with_suffix('.json').write_text(json.dumps(dict(tags=[])))
            client = Mock()
            client.sismember.return_value = False
            client.get.return_value = None
            client.exists.return_value = True
            with self.assertLogs(image_tags.log, level='WARNING') as logs:
                image_tags.schedule(client, sample_task(dict(style_tag='artist', engine='zit')),
                                    path, force=True, priority=True)
            self.assertIn('Tag task blocked by running task', logs.output[0])
            client.exists.assert_called_once_with('styler:worker-running')

    def test_expansion_subscribes_to_missing_tag_completion(self):
        source = Path(__file__).resolve().parents[1] / 'src/websocket/server.py'
        node = next(n for n in ast.parse(source.read_text()).body
                    if isinstance(n, ast.FunctionDef) and n.name == 'styler_websocket')
        node.decorator_list = []
        client, ws = Mock(), Mock()
        task = dict(job_id='sample-id', style_tag='artist', engine='zit')
        completed = dict(task, state='prompt_tagged', image_tags=['ink'])
        ws.receive.side_effect = [json.dumps(dict(action='missing_tags', style_tag='artist')), StopIteration]
        client.pubsub.return_value.get_message.return_value = dict(type='message', data=json.dumps(completed))
        namespace = dict(redis_connection=lambda: client, json=json, VIDEO_QUEUE='video')
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), 'exec'), namespace)
        with patch.dict(sys.modules, {'const': SimpleNamespace(LIBRARY_DIR='/library')}), patch.object(image_tags, 'request_missing_tags', return_value=[task]) as request:
            with self.assertRaises(StopIteration):
                namespace['styler_websocket'](ws)
        request.assert_called_once_with(client, 'artist', '/library')
        self.assertEqual(json.loads(ws.send.call_args.args[0]), completed)

    def test_expansion_retries_null_and_empty_tags_without_cooldown(self):
        with TemporaryDirectory() as tmp:
            folder = Path(tmp) / 'style_exploration'
            folder.mkdir()
            for engine, tags in [('zit', None), ('krea2', []), ('qwen', ['ink'])]:
                path = folder / f'artist_{engine}.png'
                path.write_bytes(b'image')
                path.with_suffix('.json').write_text(json.dumps(dict(tags=tags)))
            client = Mock()
            client.sismember.return_value = False
            client.set.return_value = False
            with patch.object(image_tags, 'schedule') as schedule:
                for _ in range(2):
                    tasks = image_tags.request_missing_tags(client, 'artist', tmp)
                    self.assertEqual([task['engine'] for task in tasks], ['zit', 'krea2'])
                self.assertEqual(schedule.call_count, 4)
                self.assertTrue(all(call.kwargs == dict(force=True, priority=True)
                                    for call in schedule.call_args_list))

    def test_visible_style_retries_only_untagged_samples_at_priority(self):
        with TemporaryDirectory() as tmp:
            folder = Path(tmp) / 'style_exploration'
            folder.mkdir()
            for engine in ('zit', 'krea2', 'qwen'):
                path = folder / f'Aarhus_{engine}.png'
                path.write_bytes(b'image')
                path.with_suffix('.json').write_text(json.dumps(
                    dict(tags=[] if engine == 'zit' else ['ink drawing'])))
            client = Mock()
            client.sismember.return_value = False
            client.set.side_effect = [True, False]
            with patch.object(image_tags, 'schedule') as schedule:
                self.assertTrue(image_tags.auto_retag_if_empty(client, 'Aarhus', tmp))
                self.assertFalse(image_tags.auto_retag_if_empty(client, 'Aarhus', tmp))
            schedule.assert_called_once()
            self.assertEqual(schedule.call_args.args[1]['engine'], 'zit')
            self.assertEqual(schedule.call_args.kwargs, dict(force=True, priority=True))
            event = json.loads(client.publish.call_args.args[1])
            self.assertEqual(event['engines'], ['zit'])
            client.sismember.return_value = True
            self.assertFalse(image_tags.auto_retag_if_empty(client, 'Aarhus', tmp))

    def test_websocket_skips_non_job_events_and_keeps_forwarding(self):
        source = Path(__file__).resolve().parents[1] / 'src/websocket/server.py'
        node = next(n for n in ast.parse(source.read_text()).body
                    if isinstance(n, ast.FunctionDef) and n.name == 'styler_websocket')
        node.decorator_list = []
        client, ws = Mock(), Mock()
        task = dict(job_id='sample-id', style_tag='artist', engine='qwen')
        completed = dict(task, state='prompt_tagged')
        payloads = [json.dumps(dict(style_tag='artist', state='retag_queued', automatic=True)),
                    'invalid json', '[]', 'null', json.dumps(dict(job_id=[])),
                    json.dumps(dict(job_id='another-job')), json.dumps(completed)]
        ws.receive.side_effect = [json.dumps(dict(action='retag', style_tag='artist'))] + [None] * (len(payloads) - 1) + [StopIteration]
        client.pubsub.return_value.get_message.side_effect = [
            dict(type='message', data=payload) for payload in payloads]
        namespace = dict(redis_connection=lambda: client, json=json, VIDEO_QUEUE='video')
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), 'exec'), namespace)
        with patch.dict(sys.modules, {'const': SimpleNamespace(LIBRARY_DIR='/library')}), patch.object(image_tags, 'request_retag', return_value=[task]):
            with self.assertRaises(StopIteration):
                namespace['styler_websocket'](ws)
        responses = [json.loads(call.args[0]) for call in ws.send.call_args_list]
        self.assertEqual(len(responses), 2)
        self.assertEqual(responses[-1], completed)
        client.pubsub.return_value.close.assert_called_once()
        client.close.assert_called_once()

    def test_websocket_scans_stockpile_and_forwards_completions(self):
        import stockpile
        source = Path(__file__).resolve().parents[1] / 'src/websocket/server.py'
        node = next(n for n in ast.parse(source.read_text()).body
                    if isinstance(n, ast.FunctionDef) and n.name == 'styler_websocket')
        node.decorator_list = []
        client = Mock()
        ws = Mock()
        ws.receive.side_effect = [json.dumps({'action': 'stockpile_scan'}), StopIteration]
        response = dict(state='stockpile_queued', scanned=1, complete=0, jobs=['stock-id'], errors=[])
        completed = dict(job_id='stock-id', filename='photo.png', state='stockpile_complete')
        client.pubsub.return_value.get_message.return_value = dict(type='message', data=json.dumps(completed))
        namespace = dict(redis_connection=lambda: client, json=json, VIDEO_QUEUE='video')
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), 'exec'), namespace)
        with patch.dict(sys.modules, {'const': SimpleNamespace(LIBRARY_DIR='/library')}), patch.object(stockpile, 'scan_stockpile', return_value=response) as scan:
            with self.assertRaises(StopIteration):
                namespace['styler_websocket'](ws)
        scan.assert_called_once_with(client, '/library', 'video')
        self.assertEqual([json.loads(call.args[0]) for call in ws.send.call_args_list], [response, completed])

    def test_websocket_forwards_retag_acknowledgment_and_completion(self):
        source = Path(__file__).resolve().parents[1] / 'src/websocket/server.py'
        node = next(n for n in ast.parse(source.read_text()).body
                    if isinstance(n, ast.FunctionDef) and n.name == 'styler_websocket')
        node.decorator_list = []
        client = Mock()
        ws = Mock()
        ws.receive.side_effect = [json.dumps({'action': 'retag', 'style_tag': 'artist'}), StopIteration]
        task = dict(job_id='sample-id', style_tag='artist', engine='qwen')
        completed = dict(task, state='prompt_tagged', image_tags=['ink drawing'],
                         tags=['ink drawing'], added=['ink drawing'], removed=[], counts=[])
        client.pubsub.return_value.get_message.return_value = dict(type='message', data=json.dumps(completed))
        namespace = dict(redis_connection=lambda: client, json=json, VIDEO_QUEUE='video')
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), 'exec'), namespace)
        with patch.dict(sys.modules, {'const': SimpleNamespace(LIBRARY_DIR='/library')}), patch.object(image_tags, 'request_retag', return_value=[task]) as request:
            with self.assertRaises(StopIteration):
                namespace['styler_websocket'](ws)
        request.assert_called_once_with(client, 'artist', '/library')
        responses = [json.loads(call.args[0]) for call in ws.send.call_args_list]
        self.assertEqual(responses[0], dict(state='retag_queued', style_tag='artist', engines=['qwen']))
        self.assertEqual(responses[1], completed)
        client.pubsub.return_value.close.assert_called_once()

    def test_style_retags_each_existing_image_with_priority(self):
        with TemporaryDirectory() as tmp:
            client = Mock()
            client.sismember.return_value = False
            with patch.object(image_tags, 'schedule') as schedule:
                with self.assertRaises(ValueError):
                    image_tags.request_retag(client, 'artist', tmp)
                schedule.assert_not_called()
                folder = Path(tmp) / 'style_exploration'
                folder.mkdir()
                for engine in ('zit', 'qwen'):
                    (folder / f'artist_{engine}.png').write_bytes(b'image')
                tasks = image_tags.request_retag(client, 'artist', tmp)
                self.assertEqual([task['engine'] for task in tasks], ['zit', 'qwen'])
                self.assertEqual([call.args[1]['engine'] for call in schedule.call_args_list], ['zit', 'qwen'])
                self.assertTrue(all(call.kwargs == {'force': True, 'priority': True} for call in schedule.call_args_list))
                schedule.reset_mock()
                with self.assertRaises(ValueError):
                    image_tags.request_retag(client, '../invalid', tmp)
                schedule.assert_not_called()
