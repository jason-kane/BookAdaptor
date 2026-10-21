"""Exercise the shared extractor using real ffmpeg and old frame sequences."""
import ast
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock


@unittest.skipUnless(shutil.which('ffmpeg'), 'ffmpeg required')
class FrameExtractionTests(unittest.TestCase):
    def test_clean_resampled_sequence_and_failed_extraction_cleanup(self):
        source = Path(__file__).parents[1] / 'src/artifact_editor/tools.py'
        function = next(n for n in ast.parse(source.read_text()).body
                        if isinstance(n, ast.FunctionDef) and n.name == 'extract_frames')
        ns = dict(os=os, shutil=shutil, subprocess=subprocess,
                  const=SimpleNamespace(FPS=25), log=MagicMock())
        exec(compile(ast.Module(body=[function], type_ignores=[]), str(source), 'exec'), ns)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            clip = root / 'source with spaces.mp4'
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                            'testsrc2=size=32x32:rate=24:duration=1', str(clip)], check=True)
            frames = root / 'frames'
            frames.mkdir()
            for name in ['frame_0001.png', 'frame_000001.png', 'frame_999999.png', 'done.flag']:
                (frames / name).write_bytes(b'stale')
            ns['extract_frames'](str(clip), str(frames))
            self.assertEqual(sorted(p.name for p in frames.iterdir()),
                             [f'frame_{i:06d}.png' for i in range(1, 26)])
            with self.assertRaises(subprocess.CalledProcessError):
                ns['extract_frames'](str(root / 'missing.mp4'), str(frames))
            self.assertFalse(frames.exists())
