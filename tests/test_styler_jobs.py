from contextvars import ContextVar
import importlib.util
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

spec = importlib.util.spec_from_file_location('styler_jobs', Path(__file__).resolve().parents[1] / 'src/styler/jobs.py')
jobs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(jobs)


class StylerJobTests(unittest.TestCase):
    def test_sample_displays_sidecar_tags_instead_of_redis_tags(self):
        from styler.image_tags import fingerprint, ANALYSIS_VERSION
        from styler.tag_consensus import prompt_version
        with TemporaryDirectory() as tmp:
            folder = Path(tmp) / 'style_exploration'
            folder.mkdir()
            path = folder / 'artist_zit.png'
            path.write_bytes(b'image')
            sidecar = path.with_suffix('.json')
            sidecar.write_text(json.dumps(dict(sha256=fingerprint(path), tags=['sidecar tag'])))
            client = Mock()
            client.smembers.return_value = set()
            client.get.return_value = json.dumps(dict(
                sha256=fingerprint(path), tags=['redis tag'],
                analysis_version=ANALYSIS_VERSION, prompt_sha256=prompt_version(client, 'artist')))
            task = jobs.sample_task(dict(style_tag='artist', engine='zit'))
            response = jobs.request_sample(client, task, 'queue', tmp)
            self.assertEqual(response['image_tags'], ['sidecar tag'])
            self.assertEqual(json.loads(sidecar.read_text())['tags'], ['sidecar tag'])

    def test_socket_response_returns_filename_or_loading_wrapper_and_queues(self):
        with TemporaryDirectory() as tmp:
            task = jobs.sample_task(dict(style_tag='joe_quesada', engine='zit'))
            client = Mock()
            pending = jobs.request_sample(client, task, 'queue', tmp)
            self.assertIn('id="' + pending['id'] + '"', pending['wrapper'])
            self.assertIn('wa-spinner', pending['wrapper'])
            client.lpush.assert_called_once()
            folder = Path(tmp) / 'style_exploration'
            folder.mkdir()
            (folder / 'joe_quesada_zit.png').write_bytes(b'image')
            client.reset_mock()
            ready = jobs.request_sample(client, task, 'queue', tmp)
            self.assertEqual(ready['state'], 'loaded')
            self.assertEqual(ready['filename'], '/styles/show_sample/joe_quesada_zit_sampler.png')
            self.assertNotIn('wrapper', ready)
            client.lpush.assert_not_called()

    def test_qwen_samples_use_a_distinct_image_url(self):
        task = jobs.sample_task(dict(style_tag='van_gogh', engine='qwen'))
        self.assertEqual(jobs.sample_response(task, 'complete')['filename'],
                         '/styles/show_sample/van_gogh_qwen_sampler.png')

    def test_pending_samples_are_enqueued_once(self):
        client = Mock()
        client.set.side_effect = [True, False]
        task = jobs.sample_task(dict(style_tag='joe_quesada', engine='zit'))
        jobs.enqueue(client, task, 'queue')
        jobs.enqueue(client, task, 'queue')
        client.lpush.assert_called_once_with('queue', json.dumps(task))

    def test_invalid_paths_and_engines_are_rejected(self):
        for tag, engine in [('../file', 'zit'), ('x', 'unknown'), ('x', 'flux'), ('', 'qwen')]:
            with self.assertRaises(ValueError):
                jobs.sample_task(dict(style_tag=tag, engine=engine))

    def test_worker_publishes_each_completion_and_skips_cached_generation(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'sample.png'
            progress_context = ContextVar('sample_progress', default=None)
            def render(*args, **kwargs):
                progress_context.get()(45, 'Sampling')
                path.write_bytes(b'image')
                return str(path)
            draw = Mock(side_effect=render)
            style = Mock(return_value=SimpleNamespace(get_image_path=lambda _: str(path)))
            with patch.dict(sys.modules, {'comfy': SimpleNamespace(workflow_progress=progress_context), 'styler.styles': SimpleNamespace(Style=style),
                                         'styler.views': SimpleNamespace(draw_sample=draw)}):
                task = jobs.sample_task(dict(style_tag='joe_quesada', engine='zit'))
                client = Mock()
                jobs.run_sample_job(client, task)
                jobs.run_sample_job(client, task)
            draw.assert_called_once_with('joe_quesada', engine='zit')
            self.assertEqual(client.publish.call_count, 4)
            updates = [json.loads(call.args[1]) for call in client.publish.call_args_list]
            self.assertTrue(any(e.get('percent') == 45 and e['state'] == 'running' for e in updates))
            self.assertIsNone(progress_context.get())
            event = json.loads(client.publish.call_args.args[1])
            self.assertEqual(event['state'], 'complete')
            self.assertEqual(event['style_tag'], 'joe_quesada')

    def test_worker_reports_failures_and_releases_pending_key(self):
        with patch.dict(sys.modules, {'comfy': SimpleNamespace(workflow_progress=ContextVar('test_progress', default=None)), 'styler.styles': SimpleNamespace(Style=Mock(side_effect=ValueError('missing style'))),
                                     'styler.views': SimpleNamespace(draw_sample=Mock())}):
            client = Mock()
            task = jobs.sample_task(dict(style_tag='missing', engine='qwen'))
            jobs.run_sample_job(client, task)
        client.delete.assert_any_call(f"styler_pending:{task['job_id']}")
        self.assertEqual(json.loads(client.publish.call_args.args[1])['state'], 'failed')
