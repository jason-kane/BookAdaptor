"""Frame cache invalidation and successful-write metadata publication."""
import importlib.util
import ast
import os
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('frame_cache', ROOT/'src/artifact_editor/frames/frame_cache.py')
cache = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cache)


class FrameCacheTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.frame = str(Path(self.directory.name)/'frame_000005.png')
        self.inputs = {'frame_index': 5, 'image_filename': 'image_5.png', 'text_scroll_offset': 123.5}

    def test_missing_legacy_and_matching_frames(self):
        self.assertFalse(cache.frame_is_current(self.frame, self.inputs))
        Path(self.frame).write_bytes(b'png')
        self.assertFalse(cache.frame_is_current(self.frame, self.inputs))
        cache.save_frame_metadata(self.frame, self.inputs)
        self.assertTrue(cache.frame_is_current(self.frame, self.inputs))
        self.assertEqual(Path(cache.metadata_filename(self.frame)).name, 'frame_000005.json')
        self.assertEqual(json.loads(Path(cache.metadata_filename(self.frame)).read_text()), self.inputs)

    def test_each_changed_input_invalidates_frame(self):
        Path(self.frame).write_bytes(b'png')
        cache.save_frame_metadata(self.frame, self.inputs)
        for key, value in [('frame_index', 6), ('image_filename', 'other.png'), ('text_scroll_offset', 124)]:
            with self.subTest(key=key):
                self.assertFalse(cache.frame_is_current(self.frame, dict(self.inputs, **{key: value})))

    def test_missing_png_corrupt_metadata_and_invalidation(self):
        cache.save_frame_metadata(self.frame, self.inputs)
        self.assertFalse(cache.frame_is_current(self.frame, self.inputs))
        Path(self.frame).write_bytes(b'png')
        Path(cache.metadata_filename(self.frame)).write_text('{broken')
        self.assertFalse(cache.frame_is_current(self.frame, self.inputs))
        cache.save_frame_metadata(self.frame, self.inputs)
        cache.invalidate_frame_metadata(self.frame)
        cache.invalidate_frame_metadata(self.frame)
        self.assertFalse(cache.frame_is_current(self.frame, self.inputs))

    def test_renderer_reuses_actual_animation_frame_before_composition(self):
        tree = ast.parse((ROOT/'src/artifact_editor/frames/frames.py').read_text())
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'draw_frame')
        attempt = next(n for n in ast.walk(function) if isinstance(n, ast.Try))
        body = attempt.body
        start = next(i for i, n in enumerate(body) if isinstance(n, ast.Assign)
                     and any(isinstance(t, ast.Name) and t.id == 'last_image_pfn' for t in n.targets))
        end = next(i for i, n in enumerate(body) if isinstance(n, ast.Expr)
                   and isinstance(n.value, ast.Call) and isinstance(n.value.func, ast.Name)
                   and n.value.func.id == 'invalidate_frame_metadata')
        probe = ast.FunctionDef(name='probe', args=ast.arguments(posonlyargs=[], args=[], kwonlyargs=[], kw_defaults=[], defaults=[]),
                                body=body[start:end + 1] + [ast.Return(value=ast.Constant(value='render'))], decorator_list=[])
        module = ast.fix_missing_locations(ast.Module(body=[probe], type_ignores=[]))
        text_image = Path(self.directory.name) / 'chapter-1.png'
        text_image.write_bytes(b'text')
        animation_image = Path(self.directory.name) / 'animation_000005.png'
        animation_image.write_bytes(b'old image')
        ns = dict(os=os, const=SimpleNamespace(LIBRARY_DIR=self.directory.name),
                  chapter=SimpleNamespace(chapterdir='', language=''),
                  frame_is_current=cache.frame_is_current,
                  invalidate_frame_metadata=cache.invalidate_frame_metadata,
                  image_cycle=[str(animation_image.with_name('animation_000004.png')), str(animation_image)],
                  frame_index=5, first_frame_index=4, max_frame_index=8,
                  scroll_lock=123.5, phrase_id=2, image_dict={}, force=False, frame_fn=self.frame)
        exec(compile(module, '<renderer-cache-probe>', 'exec'), ns)
        Path(self.frame).write_bytes(b'png')
        inputs = dict(self.inputs, text_revision=text_image.stat().st_mtime_ns, text_source='chapter-png', image_filename='animation_000005.png',
                      image_path=str(animation_image), image_revision=animation_image.stat().st_mtime_ns,
                      image_size=animation_image.stat().st_size, phrase_id=2,
                      fullscreen=False, mode='fit-to-width', animate='', first_frame_index=4, max_frame_index=8)
        cache.save_frame_metadata(self.frame, inputs)
        self.assertIsNone(ns['probe']())
        # Regenerated animations keep the same frame filenames. A replacement
        # must invalidate the composed frame even when its byte length matches.
        previous_image_revision = animation_image.stat().st_mtime_ns
        animation_image.write_bytes(b'new image')
        os.utime(animation_image, ns=(previous_image_revision + 1, previous_image_revision + 1))
        self.assertEqual(ns['probe'](), 'render')
        inputs['image_revision'] = animation_image.stat().st_mtime_ns
        cache.save_frame_metadata(self.frame, inputs)
        self.assertIsNone(ns['probe']())
        # Identical basenames from a different animation are also distinct.
        other_directory = Path(self.directory.name) / 'replacement'
        other_directory.mkdir()
        other_image = other_directory / animation_image.name
        other_image.write_bytes(animation_image.read_bytes())
        os.utime(other_image, ns=(inputs['image_revision'], inputs['image_revision']))
        ns['image_cycle'][1] = str(other_image)
        self.assertEqual(ns['probe'](), 'render')
        ns['image_cycle'][1] = str(animation_image)
        cache.save_frame_metadata(self.frame, inputs)
        previous_revision = text_image.stat().st_mtime_ns
        os.utime(text_image, ns=(previous_revision + 1, previous_revision + 1))
        self.assertEqual(ns['probe'](), 'render')
        cache.save_frame_metadata(self.frame, dict(inputs, text_revision=text_image.stat().st_mtime_ns))
        ns['scroll_lock'] = 124
        self.assertEqual(ns['probe'](), 'render')
        self.assertFalse(Path(cache.metadata_filename(self.frame)).exists())

    def test_failed_metadata_publication_cleans_temporary_file(self):
        with patch.object(cache.os, 'replace', side_effect=OSError('write failed')):
            with self.assertRaises(OSError):
                cache.save_frame_metadata(self.frame, self.inputs)
        self.assertEqual(list(Path(self.directory.name).iterdir()), [])


if __name__ == '__main__':
    unittest.main()
