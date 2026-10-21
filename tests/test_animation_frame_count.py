import importlib.util
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('animation_source', Path(__file__).parents[1] / 'src/artifact_editor/images/animation_source.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class AnimationFrameCountTests(unittest.TestCase):
    def test_counts_only_png_frames_across_segments(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            for segment in ['00', '01']:
                folder = root / 'animation' / f'image_000004_{segment}'
                folder.mkdir(parents=True)
                (folder / 'frame_000001.png').write_bytes(b'frame')
                (folder / 'done.flag').touch()
            self.assertEqual(module.animation_frame_count({'index': '4'}, root), 2)
            self.assertEqual(module.animation_frame_count({'index': '9'}, root), 0)

    def test_video_fallback(self):
        with TemporaryDirectory() as temp:
            (Path(temp) / 'selected.mp4').touch()
            with patch.object(module, '_video_frame_count', return_value=48):
                self.assertEqual(module.animation_frame_count(
                    {'index': '4', 'animated_src': 'selected.mp4'}, temp, 25), 48)

    def test_selected_video_takes_precedence_over_stale_expansion(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'selected.mp4').touch()
            frames = root / 'animation' / 'image_000004_00'
            frames.mkdir(parents=True)
            (frames / 'frame_0001.png').touch()
            with patch.object(module, '_video_frame_count', return_value=100):
                self.assertEqual(module.animation_frame_count(
                    {'index': '4', 'animated_src': 'selected.mp4'}, root, 25), 100)

    def test_native_video_frames_are_converted_to_output_fps(self):
        import json
        from types import SimpleNamespace

        for stream in [
            {"duration": "4.000", "nb_frames": "96", "avg_frame_rate": "24/1"},
            {"nb_frames": "96", "avg_frame_rate": "24/1"},
            {"duration": "4.000", "nb_frames": "120", "avg_frame_rate": "30/1"},
        ]:
            module._video_frame_count.cache_clear()
            with patch('subprocess.run', return_value=SimpleNamespace(
                    stdout=json.dumps({"streams": [stream]}))):
                self.assertEqual(module._video_frame_count('clip.mp4', 1, 1, 25), 100)
