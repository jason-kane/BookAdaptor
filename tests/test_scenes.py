"""Scene boundaries share setting descriptions and artwork in document order."""
import importlib.util
import ast
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import Mock, patch
from bs4 import BeautifulSoup
from jinja2 import Environment, FileSystemLoader

ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location('chapter_scenes', ROOT / 'src/artifact_editor/chapter/scenes.py')
scenes = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scenes)
spec = importlib.util.spec_from_file_location('chapter_timeline', ROOT / 'src/artifact_editor/chapter/timeline.py')
timeline = importlib.util.module_from_spec(spec)
spec.loader.exec_module(timeline)


class SceneTests(unittest.TestCase):
    def test_image_and_scene_description_prompts_have_separate_roles_and_outputs(self):
        scene = scenes.add_scene(self.chapter, 0)
        scene_llm = SimpleNamespace(str_prompt=Mock(return_value='Stone hall'))
        with patch.dict(sys.modules, {'artifact_editor': SimpleNamespace(llm=scene_llm)}):
            scenes.generate_description(self.chapter, scene)
        image_llm = SimpleNamespace(get_cached_prompt=Mock(return_value=None), set_cached_prompt=Mock())
        completion = Mock(return_value='A bird approaches a sign')
        namespace = {
            'llm': image_llm,
            'os': os,
            'redis': SimpleNamespace(Redis=Mock()),
            'get_text_to_next': lambda **kwargs: 'A bird approaches a sign.',
            'get_image_narration_context': lambda image: '',
        }
        tree = ast.parse((ROOT / 'src/artifact_editor/images/images.py').read_text())
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                        and node.name == 'generate_image_description')
        exec(compile(ast.Module(body=[function], type_ignores=[]), 'images.py', 'exec'), namespace)
        image = self.soup.find('image', index='1')
        image['Pigeon_action'] = 'Invented action: drinks from a golden fountain'
        image['Pigeon_description'] = 'Invented costume: a velvet cape'
        with patch.dict(sys.modules, {'artifact_editor.chapter.scenes': scenes,
                                     'llama_inference': SimpleNamespace(chat_completion=completion),
                                     'gpu_tasks': SimpleNamespace(gpu_label=SimpleNamespace(get=lambda: 'Description'))}):
            namespace['generate_image_description'](image, force=True)
        image_prompt = completion.call_args.args[1]
        scene_prompt = scene_llm.str_prompt.call_args.kwargs
        self.assertIn('visible characters, animals, props, actions', image_prompt)
        self.assertIn('CURRENT SOURCE PASSAGE (authoritative)', image_prompt)
        self.assertIn('minimal introductory composition', image_prompt)
        self.assertNotIn('golden fountain', image_prompt)
        self.assertNotIn('velvet cape', image_prompt)
        self.assertIn('empty environmental backdrop', scene_prompt['system_prompt'])
        for word in ('signboard', 'goblet', 'pigeon'):
            self.assertNotIn(word, scene_prompt['system_prompt'].lower())
        self.assertNotEqual(image_prompt, scene_prompt['prompt'])
        self.assertEqual(scene['description'], 'Stone hall')
        self.assertEqual(image['description'], 'A bird approaches a sign')

    def setUp(self):
        self.soup = BeautifulSoup('<chapter><paragraph index="0"><image index="0" setting="Old setting"/>'
                                  '<phrase index="0" frames="10">Old place.</phrase><image index="1"/>'
                                  '</paragraph><paragraph index="1"><phrase index="1" frames="20">A stone hall.</phrase>'
                                  '<image index="2"/><phrase index="2" frames="30">Tall windows.</phrase>'
                                  '</paragraph></chapter>', 'xml')
        self.chapter = SimpleNamespace(get_xml=lambda: self.soup, save_xml=Mock(), chapterdir='book/chapter', kwargs={})

    def test_add_empty_scene_after_image_and_inherit_across_paragraphs(self):
        scene = scenes.add_scene(self.chapter, 0)
        self.assertIs(self.soup.image.find_next_sibling(), scene)
        self.assertEqual(scene['description'], '')
        self.assertIsNone(scenes.active_scene(self.soup.image))
        scene['description'] = 'A stone hall'
        self.assertEqual(scenes.setting_description(self.soup.find('image', index='2')), 'A stone hall')
        self.assertEqual(scenes.setting_description(self.soup.image), 'Old setting')
        next_scene = scenes.add_scene(self.chapter, 1)
        self.assertEqual(next_scene['index'], '1')
        self.assertEqual(scenes.setting_description(self.soup.find('image', index='2')), '')

    def test_time_of_day_is_shared_by_scene_and_ignores_image_override(self):
        self.soup.image['tod'] = 'morning'
        self.assertEqual(scenes.time_of_day(self.soup.image), 'morning')
        scene = scenes.add_scene(self.chapter, 0)
        image = self.soup.find('image', index='2')
        image['tod'] = 'day'
        scenes.save_time_of_day(scene, 'night')
        self.assertEqual(scene['tod'], 'night')
        self.assertEqual(scenes.time_of_day(image), 'night')
        self.assertEqual(scenes.time_of_day(self.soup.find('image', index='1')), 'night')
        with self.assertRaises(ValueError):
            scenes.save_time_of_day(scene, 'invalid')
        self.assertEqual(scene['tod'], 'night')

    def test_lighting_is_owned_by_scene_and_inherited_across_paragraphs(self):
        scene = scenes.add_scene(self.chapter, 0)
        image = self.soup.find('image', index='2')
        image['lighting_quality'] = 'harsh'
        scenes.save_lighting(scene, {'direction': 'side', 'source': 'sunlight', 'quality': 'soft'})
        self.assertEqual(scenes.lighting(image), {'direction': 'side', 'source': 'sunlight', 'quality': 'soft'})
        with self.assertRaises(ValueError):
            scenes.save_lighting(scene, {'source': 'invalid'})
        self.assertEqual(scene['lighting_source'], 'sunlight')

    def test_scope_move_delete_and_timing(self):
        scene = scenes.add_scene(self.chapter, 0)
        scene['description'] = 'Hall'
        timings = timeline.image_timings(self.soup)
        scenes.move_scene(self.chapter, 0, 'image', 2, 'before')
        self.assertIsNone(scenes.active_scene(self.soup.find('image', index='1')))
        self.assertIs(scenes.active_scene(self.soup.find('image', index='2')), scene)
        self.assertEqual(timeline.image_timings(self.soup), timings)
        self.assertEqual(scene.find_parent('paragraph')['index'], '1')
        timeline.delete_timeline_item(self.chapter, 'scene', 0)
        self.assertEqual(self.soup.find_all('scene'), [])
        self.assertEqual(timeline.image_timings(self.soup), timings)

    def test_description_source_stops_at_next_scene(self):
        scene = scenes.add_scene(self.chapter, 0)
        scenes.add_scene(self.chapter, 1)
        self.assertEqual(scenes.source_text(scene), 'Old place.')
        self.assertNotIn('stone hall', scenes.source_text(scene))

    def test_scene_artwork_path_is_independent_of_paragraph(self):
        scene = scenes.add_scene(self.chapter, 0)
        path = scenes.scene_image_path(self.chapter, scene, '/library')
        scenes.move_scene(self.chapter, 0, 'image', 2, 'after')
        self.assertEqual(scenes.scene_image_path(self.chapter, scene, '/library'), path)
        self.assertEqual(path, Path('/library/book/chapter/scenes/scene_0.png'))

    def test_invalid_add_and_move_preserve_xml(self):
        before = str(self.soup)
        with self.assertRaises(ValueError):
            scenes.add_scene(self.chapter, 999)
        with self.assertRaises(ValueError):
            scenes.move_scene(self.chapter, 0, 'chapter', 0, 'before')
        self.assertEqual(str(self.soup), before)

    def test_scene_panels_render_controls_escape_description_and_drag_handle(self):
        scene = scenes.add_scene(self.chapter, 0)
        scene['description'] = '<script>bad()</script> "quoted"'
        env = Environment(loader=FileSystemLoader(ROOT / 'src/artifact_editor/chapter/templates'), autoescape=True)
        env.globals.update(url_for=lambda endpoint, **kwargs: '/test', websocket_url=lambda: '/ws')
        html = env.get_template('chapter_timeline_scene.html').render(chapter=self.chapter, scene=scene)
        self.assertIn('data-timeline-type="scene"', html)
        self.assertIn('draggable="true"', html)
        self.assertIn('Generate description', html)
        self.assertIn('Generate image', html)
        self.assertNotIn('<script>', html)
        self.assertIn('data-image-editor-url', html)

    def test_lighting_generation_accepts_plain_and_fenced_json(self):
        values = {'direction': 'side', 'source': 'natural', 'quality': 'soft'}
        for response in [json.dumps(values), '```json\n' + json.dumps(values) + '\n```',
                         '  ```\n' + json.dumps(values) + '\n```\n']:
            with self.subTest(response=response):
                scene = scenes.add_scene(self.chapter, 0)
                llm = SimpleNamespace(str_prompt=Mock(return_value=response))
                with patch.dict(sys.modules, {'artifact_editor': SimpleNamespace(llm=llm)}):
                    scenes.generate_lighting(self.chapter, scene)
                self.assertEqual(scenes.lighting(scene), values)

    def test_lighting_generation_rejects_invalid_values_without_changes(self):
        scene = scenes.add_scene(self.chapter, 0)
        before = str(scene)
        for response in ['', '```json\n{}\n```',
                         '{"direction":"side","source":"invalid","quality":"soft"}']:
            with self.subTest(response=response):
                llm = SimpleNamespace(str_prompt=Mock(return_value=response))
                with patch.dict(sys.modules, {'artifact_editor': SimpleNamespace(llm=llm)}):
                    with self.assertRaises(ValueError):
                        scenes.generate_lighting(self.chapter, scene)
                self.assertEqual(str(scene), before)

    def test_description_generation_uses_only_scene_span(self):
        scene = scenes.add_scene(self.chapter, 0)
        scenes.add_scene(self.chapter, 1)
        llm = SimpleNamespace(str_prompt=Mock(return_value='Stone hall'))
        with patch.dict(sys.modules, {'artifact_editor': SimpleNamespace(llm=llm)}):
            scenes.generate_description(self.chapter, scene)
        self.assertEqual(scene['description'], 'Stone hall')
        self.assertIn('Old place.', llm.str_prompt.call_args.kwargs['prompt'])
        self.assertNotIn('Tall windows.', llm.str_prompt.call_args.kwargs['prompt'])

    def test_generated_artwork_is_saved_with_scene_and_refreshed_thumbnail(self):
        scene = scenes.add_scene(self.chapter, 0)
        with TemporaryDirectory() as root:
            output = Path(root) / 'output'
            output.mkdir()
            (output / 'generated.png').write_bytes(b'new scene')
            comfy = SimpleNamespace(run_workflow=Mock())
            reference_workflow = Mock(return_value=({}, {'FILENAME_PREFIX': 'generated'}))
            with patch.dict(sys.modules, {
                'comfy': comfy,
                'const': SimpleNamespace(LIBRARY_DIR=root, COMFY_DIRS={'artifactserver': {'OUTPUT_DIR': str(output)}}),
                'artifact_editor.reference_images': SimpleNamespace(reference_workflow=reference_workflow),
            }):
                scenes.generate_image(self.chapter, scene, 'Stone hall')
            reference_workflow.assert_called_once_with('scenes', "Stone hall\nTime of day: unknown\nLighting: {'direction': '', 'source': '', 'quality': ''}")
            self.assertEqual(scenes.scene_image_path(self.chapter, scene, root).read_bytes(), b'new scene')
            self.assertEqual(scene['src'], 'scene_0.png')
            self.assertTrue(scene['image_version'])

    def test_scene_editor_saves_description_and_queues_image_job(self):
        import ast
        import os
        from flask import Flask, request
        scene = scenes.add_scene(self.chapter, 0)
        self.chapter.author = SimpleNamespace(name='Author')
        self.chapter.title = 'Title'
        self.chapter.number = 1
        self.chapter.language = 'en'
        path = ROOT / 'src/artifact_editor/chapter/views.py'
        node = next(n for n in ast.parse(path.read_text()).body
                    if isinstance(n, ast.FunctionDef) and n.name == 'timeline_scene_editor')
        node.decorator_list = []
        client = Mock()
        from task_queue import enqueue_task, VIDEO_QUEUE
        namespace = dict(g=SimpleNamespace(chapter=self.chapter), request=request, os=os, json=json,
                         enqueue_task=enqueue_task, VIDEO_QUEUE=VIDEO_QUEUE,
                         render_template=Mock(return_value='panel'))
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), namespace)
        app = Flask(__name__)
        with patch.dict(sys.modules, {'redis': SimpleNamespace(Redis=Mock(return_value=client)), 'artifact_editor.chapter.scenes': scenes}):
            with app.test_request_context('/', method='POST', data={'action': 'generate', 'description': 'A hall'}):
                response = namespace['timeline_scene_editor'](0)
        self.assertIn('queued', response)
        task = json.loads(client.lpush.call_args.args[1])
        self.assertEqual(task['action'], 'scene_image')
        self.assertEqual(task['scene_index'], 0)
        self.assertEqual(task['description'], 'A hall')
        self.assertEqual(scene['description'], 'A hall')
        with app.test_request_context('/', method='POST', data={'action': 'generate', 'description': ''}):
            self.assertEqual(namespace['timeline_scene_editor'](0)[1], 400)
        with app.test_request_context('/'):
            self.assertEqual(namespace['timeline_scene_editor'](999)[1], 404)

    def test_timing_refresh_skips_scenes_and_retains_all_timed_panels(self):
        import ast
        path = ROOT / 'src/artifact_editor/phrases/htmx.py'
        function = next(node for node in ast.parse(path.read_text()).body
                        if isinstance(node, ast.FunctionDef) and node.name == 'chapter_timing_updates')
        render = Mock(return_value='timing')
        namespace = {'render_template': render}
        exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), 'exec'), namespace)
        entries = [
            {'type': 'scene', 'scene': {'index': '0'}},
            {'type': 'phrase', 'phrase': SimpleNamespace(index='1'), 'timing': {'frames': 10}},
            {'type': 'image', 'image': {'index': '2'}, 'timing': None},
            {'type': 'silence', 'silence': {'index': '3'}, 'timing': {'frames': 5}},
        ]
        chapter = SimpleNamespace(timeline=lambda: iter(entries))
        self.assertEqual(namespace['chapter_timing_updates'](chapter), 'timingtimingtiming')
        self.assertEqual([call.kwargs['timing_id'] for call in render.call_args_list],
                         ['chapter-phrase-timing-1', 'chapter-image-timing-2', 'chapter-silence-timing-3'])
        self.assertIsNone(render.call_args_list[1].kwargs['timing'])

    def test_scene_worker_dispatches_description_and_image_jobs(self):
        import ast
        scene = scenes.add_scene(self.chapter, 0)
        path = ROOT / 'src/websocket/server.py'
        method = next(n for n in ast.parse(path.read_text()).body
                      if isinstance(n, ast.FunctionDef) and n.name == 'run_video_job')
        branch = next(n for n in ast.walk(method) if isinstance(n, ast.If)
                      and 'scene_description' in ast.unparse(n.test))
        wrapper = ast.FunctionDef(name='dispatch', args=ast.arguments(posonlyargs=[], args=[], kwonlyargs=[], kw_defaults=[], defaults=[]),
                                  body=[branch], decorator_list=[])
        description, image, tod, light, publish = Mock(), Mock(), Mock(), Mock(), Mock()
        for action in ['scene_description', 'scene_image', 'scene_tod', 'scene_lighting']:
            namespace = dict(task={'action': action, 'scene_index': 0, 'description': 'Hall'},
                             chapter=self.chapter, client=Mock(), job_id='job', publish_progress=publish)
            with patch.dict(sys.modules, {'artifact_editor.chapter.scenes': SimpleNamespace(generate_description=description, generate_image=image, generate_time_of_day=tod, generate_lighting=light)}):
                exec(compile(ast.fix_missing_locations(ast.Module(body=[wrapper], type_ignores=[])), str(path), 'exec'), namespace)
                namespace['dispatch']()
        description.assert_called_once_with(self.chapter, scene)
        image.assert_called_once_with(self.chapter, scene, 'Hall')
        tod.assert_called_once_with(self.chapter, scene)
        light.assert_called_once_with(self.chapter, scene)
        self.assertEqual(publish.call_count, 4)

    def test_ref2i_uses_scene_artwork_when_scene_exists(self):
        import ast
        import os
        path = ROOT / 'src/artifact_editor/chapter/chapter.py'
        method = next(n for n in ast.walk(ast.parse(path.read_text()))
                      if isinstance(n, ast.FunctionDef) and n.name == 'get_workflow_by_filename')
        branch = next(n for n in method.body if isinstance(n, ast.If)
                      and 'ui.ref2i.' in ast.unparse(n.test))
        scene = scenes.add_scene(self.chapter, 0)
        scene['src'] = 'scene_0.png'
        image = self.soup.find('image', index='2')
        reference_inputs = Mock(return_value=[])
        prepare_workflow = Mock()
        namespace = dict(os=os, workflow_filename='ui.ref2i.example.json', self=self.chapter,
                         const=SimpleNamespace(LIBRARY_DIR='/library', COMFY_DIRS={'artifactserver': {'INPUT_DIR': '/input'}}),
                         source_image='/current.png', image_xml=image, workflow={}, template_environment={})
        with patch.dict(sys.modules, {'artifact_editor.chapter.scenes': scenes,
                                     'artifact_editor.ref2i': SimpleNamespace(reference_inputs=reference_inputs, prepare_workflow=prepare_workflow)}):
            exec(compile(ast.Module(body=[branch], type_ignores=[]), str(path), 'exec'), namespace)
        self.assertEqual(reference_inputs.call_args.args[0], Path('/library/book/chapter/scenes/scene_0.png'))
        prepare_workflow.assert_called_once()


if __name__ == '__main__':
    unittest.main()
