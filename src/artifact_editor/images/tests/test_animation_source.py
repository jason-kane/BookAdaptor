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
