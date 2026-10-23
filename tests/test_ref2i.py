import importlib.util
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location('ref2i', ROOT / 'src/artifact_editor/ref2i.py')
ref2i = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ref2i)


class Ref2iTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.scene = self.root / 'scene.png'
        self.scene.write_bytes(b'scene')
        self.xml = {'scene_characters': 'tip, tip', 'scene_props': 'hat'}
        for kind, tag, name in [('characters', 'tip', 'Tip'), ('props', 'hat', 'Magic Hat')]:
            (self.root / kind).mkdir()
            (self.root / (kind + '.json')).write_text(json.dumps({tag: {'name': name}}))
            (self.root / kind / (tag + '.png')).write_bytes(kind.encode())

    def inputs(self):
        return ref2i.reference_inputs(self.scene, self.root, self.xml)

    def workflow(self):
        return json.loads(next((ROOT / 'templates').glob('ui.ref2i.qwen*.json')).read_text())

    def test_mapping_and_export_use_same_order_and_remove_unused_loaders(self):
        references = self.inputs()
        self.assertEqual([r['name'] for r in references], ['Current scene', 'Tip', 'Magic Hat'])
        data = self.workflow()
        ref2i.prepare_workflow(data, references, self.root / 'input')
        nodes = {n.get('title'): n for n in data['nodes']}
        self.assertNotIn('IMAGE_4', nodes)
        manifest = json.loads(nodes['REFERENCE_IMAGES']['widgets_values'][0])
        self.assertEqual([r['image'] for r in manifest], ['<image1>', '<image2>', '<image3>'])
        for reference in references:
            filename = nodes[reference['slot']]['widgets_values'][0]
            self.assertEqual((self.root / 'input' / filename).read_bytes(), reference['path'].read_bytes())
        self.assert_graph(data)
        mapping_id = nodes['REFERENCE_IMAGES']['id']
        self.assertEqual({link[3] for link in data['links'] if link[1] == mapping_id}, {142, 143})

    def assert_graph(self, data):
        nodes = {n['id']: n for n in data['nodes']}
        links = {l[0]: l for l in data['links']}
        for lid, origin, slot, target, target_slot, _ in data['links']:
            self.assertIn(lid, nodes[origin]['outputs'][slot]['links'])
            self.assertEqual(nodes[target]['inputs'][target_slot]['link'], lid)
        for node in data['nodes']:
            for socket in node.get('inputs', []):
                if socket.get('link') is not None:
                    self.assertIn(socket['link'], links)

    def test_scene_only_has_no_stale_reference_connections(self):
        self.xml = {}
        data = self.workflow()
        ref2i.prepare_workflow(data, self.inputs(), self.root / 'input')
        self.assert_graph(data)
        encoder = next(n for n in data['nodes'] if n['type'] == 'TextEncodeQwenImage21')
        self.assertEqual(sum(s.get('link') is not None for s in encoder['inputs'] if s['name'].startswith('images.')), 1)

    def test_missing_scene_or_selected_image_is_an_error(self):
        with self.assertRaisesRegex(ValueError, 'current scene image'):
            ref2i.reference_inputs(None, self.root, self.xml)
        (self.root / 'props/hat.png').unlink()
        with self.assertRaisesRegex(ValueError, 'Magic Hat'):
            self.inputs()

    def test_unknown_selection_is_an_error(self):
        self.xml['scene_characters'] = 'missing'
        with self.assertRaisesRegex(ValueError, 'selected characters record'):
            self.inputs()

    def test_overflow_reports_instead_of_truncating(self):
        records = {str(i): {'name': str(i)} for i in range(10)}
        (self.root / 'props.json').write_text(json.dumps(records))
        for tag in records:
            (self.root / 'props' / (tag + '.png')).write_bytes(tag.encode())
        self.xml = {'scene_props': ','.join(records)}
        with self.assertRaisesRegex(ValueError, 'at most nine'):
            self.inputs()

    def test_same_basenames_do_not_overwrite(self):
        (self.root / 'props/tip.png').write_bytes(b'prop')
        (self.root / 'props.json').write_text(json.dumps({'tip': {'name': 'Prop Tip'}}))
        self.xml['scene_props'] = 'tip'
        data = self.workflow()
        ref2i.prepare_workflow(data, self.inputs(), self.root / 'input')
        files = list((self.root / 'input').iterdir())
        self.assertEqual(len(files), 3)
        self.assertEqual({p.read_bytes() for p in files}, {b'scene', b'characters', b'prop'})

    def test_chapter_export_populates_slots_and_preserves_dotted_name(self):
        import ast
        import os
        import shutil
        import sys
        from types import SimpleNamespace
        from unittest.mock import Mock, patch
        from bs4 import BeautifulSoup

        path = ROOT / 'src/artifact_editor/chapter/chapter.py'
        tree = ast.parse(path.read_text())
        method = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
                      and n.name == 'get_workflow_by_filename')
        method.decorator_list = []
        for directory in ['input', 'output', 'workflows']:
            (self.root / directory).mkdir()
        dirs = dict(INPUT_DIR=str(self.root / 'input'), OUTPUT_DIR=str(self.root / 'output'),
                    WORKFLOWS_DIR=str(self.root / 'workflows'))
        ns = dict(json=json, os=os, shutil=shutil, Tag=object, as_int=int,
                  const=SimpleNamespace(FPS=25, LIBRARY_DIR=str(self.root),
                                        COMFY_DIRS={'artifactserver': dirs, 'comfyui': dirs}),
                  log=Mock(), styles=SimpleNamespace(Style=lambda _: SimpleNamespace(name='none')))
        exec(compile(ast.Module(body=[method], type_ignores=[]), str(path), 'exec'), ns)
        image = BeautifulSoup('<image index="0" scene_characters="tip" scene_props="hat"/>', 'xml').image
        chapter = SimpleNamespace(chapterdir='', nice='book', get_image_frames=lambda _: 1,
                                  get_image_filename=lambda _: str(self.scene), get_image=lambda _: None,
                                  get_meta_dict=lambda: {}, get_scene=lambda _: {},
                                  get_prompt=lambda _: 'scene', get_image_style=lambda _: 'none')
        template = next((ROOT / 'templates').glob('ui.ref2i.qwen*.json'))
        scene_spec = importlib.util.spec_from_file_location('chapter_scenes', ROOT / 'src/artifact_editor/chapter/scenes.py')
        scenes = importlib.util.module_from_spec(scene_spec)
        scene_spec.loader.exec_module(scenes)
        with patch.dict(sys.modules, {'artifact_editor.ref2i': ref2i, 'artifact_editor.chapter.scenes': scenes}):
            filename = ns['get_workflow_by_filename'](chapter, image, str(template))
        self.assertIn('qwen-image-2.1-paragraph', filename)
        data = json.loads((self.root / 'workflows' / filename).read_text())
        self.assert_graph(data)
        nodes = {n.get('title'): n for n in data['nodes']}
        self.assertEqual(len(json.loads(nodes['REFERENCE_IMAGES']['widgets_values'][0])), 3)
        self.assertTrue(nodes['IMAGE_1']['widgets_values'][0].startswith('ref2i_image_1_'))

    def test_full_template_has_ten_connected_slots(self):
        data = self.workflow()
        self.assert_graph(data)
        self.assertEqual(sum(n.get('title', '').startswith('IMAGE_') for n in data['nodes']), 10)


if __name__ == '__main__':
    unittest.main()
