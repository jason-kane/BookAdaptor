import importlib.util
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

spec = importlib.util.spec_from_file_location('animation_source', Path(__file__).parents[1] / 'animation_source.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class AnimationSourceTests(unittest.TestCase):
    def test_absolute_image_source_uses_generated_video_basename(self):
        with TemporaryDirectory() as directory:
            image = {'index': '4', 'src': '/active/Robert Frost/Selected Poems/chapter/0037/paragraphs/000001/img_4_still.png'}
            for filename in ('img_4_still.mp4', 'img_4_minimax_h3-MiniMax H3_00_00001_.mp4', 'img_1_other.mp4'):
                (Path(directory) / filename).touch()
            self.assertEqual(module.animation_filename(image, directory), 'img_4_minimax_h3-MiniMax H3_00_00001_.mp4')
            image['animated_src'] = '/active/img_4_still.mp4'
            self.assertEqual(module.animation_filename(image, directory), 'img_4_still.mp4')

    def test_companion_camera_and_missing_video(self):
        with TemporaryDirectory() as directory:
            image = {'index': '4', 'src': '/active/img_4_still.png'}
            self.assertIsNone(module.animation_filename(image, directory))
            (Path(directory) / 'camera.mp4').touch()
            self.assertEqual(module.animation_filename(image, directory), 'camera.mp4')
            (Path(directory) / 'img_4_still.mp4').touch()
            self.assertEqual(module.animation_filename(image, directory), 'img_4_still.mp4')

    def test_source_motion_accepts_generated_video_but_not_camera_companion(self):
        with TemporaryDirectory() as directory:
            image = {
                'index': '4', 'src': '/active/img_4_still.png',
                'source_motion': {
                    'first': {'x': .5, 'y': .5, 'zoom': 1},
                    'last': {'x': .5, 'y': .5, 'zoom': 1},
                },
                'source_motion_src': '/active/img_4_still.png',
            }
            (Path(directory) / 'camera.mp4').touch()
            (Path(directory) / 'img_4_still.mp4').touch()
            self.assertIsNone(module.animation_filename(image, directory))
            generated = Path(directory) / 'img_4_minimax_h3_00.mp4'
            generated.touch()
            self.assertEqual(module.animation_filename(image, directory), generated.name)
            image['animated_src'] = '/active/selected.mp4'
            selected = Path(directory) / 'selected.mp4'
            selected.touch()
            self.assertEqual(module.animation_filename(image, directory), selected.name)
