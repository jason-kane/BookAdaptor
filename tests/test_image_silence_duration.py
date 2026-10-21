"""Run production duration loops without loading GPU/application services."""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from bs4 import BeautifulSoup, Tag

ROOT = Path(__file__).parents[1] / 'src/artifact_editor'


class ImageSilenceDurationTests(unittest.TestCase):
    def setUp(self):
        self.soup = BeautifulSoup('''<chapter><paragraph index="0">
            <image index="0"/><phrase duration="1.5" frames="12"/>
            <silence duration="0.750" frames="999"/>
            </paragraph><paragraph index="1"><silence duration="0.250"/>
            <image index="1"/><silence duration="0.000"/>
            <silence duration="1.125"/><phrase duration="0.5" frames="4"/>
            </paragraph></chapter>''', 'xml')
        self.chapter = SimpleNamespace(
            images=lambda: self.soup.find_all('image'),
            paragraphs=lambda: self.soup.find_all('paragraph'))

    def test_textoviva_includes_silence_until_next_image(self):
        tree = ast.parse((ROOT / 'textoviva/textoviva.py').read_text())
        loop = next(node for node in ast.walk(tree) if isinstance(node, ast.For)
                    and isinstance(node.target, ast.Name) and node.target.id == 'image_xml'
                    and any(isinstance(child, ast.Name) and child.id == 'duration_frames'
                            for child in ast.walk(node)))
        engine = SimpleNamespace(chapter=self.chapter, speak=Mock())
        audio = SimpleNamespace(get_wav_duration=Mock())
        exec(compile(ast.Module(body=[loop], type_ignores=[]), 'textoviva.py', 'exec'),
             {'self': engine, 'Tag': Tag, 'const': SimpleNamespace(FPS=8),
              'phrases': audio, 'log': Mock()})
        images = self.soup.find_all('image')
        self.assertEqual([(image['frames'], image['duration']) for image in images],
                         [('20', '2.5'), ('13', '1.625')])
        self.assertEqual(self.soup.find('silence')['frames'], '6')
        engine.speak.assert_not_called()
        audio.get_wav_duration.assert_not_called()

    def test_masterplan_recalculation_keeps_silence_frames(self):
        tree = ast.parse((ROOT / 'masterplan/masterplan.py').read_text())
        loop = next(node for node in ast.walk(tree) if isinstance(node, ast.For)
                    and isinstance(node.target, ast.Name) and node.target.id == 'paragraph'
                    and any(isinstance(child, ast.Constant) and
                            child.value == 'Assigning frame durations to images in paragraph: %s'
                            for child in ast.walk(node)))
        scope = {'chapter': self.chapter, 'Tag': Tag, 'const': SimpleNamespace(FPS=8),
                 'as_int': int, 'as_float': float, 'log': Mock(), 'frames': 0,
                 'last_image': None}
        exec(compile(ast.Module(body=[loop], type_ignores=[]), 'masterplan.py', 'exec'), scope)
        self.assertEqual(self.soup.find('image')['frames'], '20')
        self.assertEqual(scope['frames'], 13)
        self.assertEqual(self.soup.find('silence')['frames'], '6')

    def test_highlight_timeline_covers_pauses_and_final_silence(self):
        tree = ast.parse((ROOT / 'masterplan/masterplan.py').read_text())
        classes = [node for node in tree.body if isinstance(node, ast.ClassDef)
                   and node.name in ('WordPlan', 'ImagePlan')]
        loop = next(node for node in ast.walk(tree) if isinstance(node, ast.For)
                    and isinstance(node.target, ast.Name) and node.target.id == 'paragraph'
                    and any(isinstance(child, ast.Name) and child.id == 'our_words'
                            for child in ast.walk(node)))
        soup = BeautifulSoup('''<chapter><paragraph index="0">
            <silence frames="2"/><image index="0" frames="24" src="test.png"/>
            <phrase index="0" frames="8">First</phrase><silence frames="4"/>
            </paragraph><paragraph index="1">
            <phrase index="1" frames="8">Second</phrase><silence frames="4"/>
            </paragraph></chapter>''', 'xml')
        scope = {
            'chapter': SimpleNamespace(paragraphs=lambda: soup.find_all('paragraph'),
                                       get_paragraph_dir=lambda index: str(index)),
            'Tag': Tag, 'as_int': int, 'as_float': float, 'as_str': str,
            'tags_to_dict': lambda _: {}, 'log': Mock(),
            'frame_index': 0, 'image_frame_index': 0, 'image_index': 0,
            'phrase_index': 0, 'words': [], 'images': [],
        }
        exec(compile(ast.Module(body=classes + [loop], type_ignores=[]),
                     'masterplan.py', 'exec'), scope)
        words = scope['words']
        self.assertEqual([(w.start_frame, w.end_frame, w.frames) for w in words],
                         [(0, 13, 14), (14, 25, 12)])
        # Every frame, including the final pause, has exactly one highlight.
        for frame in range(26):
            self.assertEqual(sum(w.start_frame <= frame <= w.end_frame for w in words), 1)


if __name__ == '__main__':
    unittest.main()
