"""Time-of-day sequence checks independent of Flask and image services."""
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('time_align', Path(__file__).resolve().parents[1] / 'src/artifact_editor/textoviva/time_align.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class TimeAlignTests(unittest.TestCase):
    def test_equal_and_forward_times_are_kept(self):
        images = [{'tod': tod} for tod in ('predawn', 'dawn', 'dawn', 'day', 'night')]
        self.assertEqual(module.time_corrections(images), [])

    def test_all_backward_images_use_last_valid_time(self):
        images = [{'tod': tod} for tod in ('morning', 'afternoon', 'dawn', 'day', 'night')]
        self.assertEqual(module.time_corrections(images), [(images[2], 'afternoon'), (images[3], 'afternoon')])

    def test_unknown_does_not_reset_previous_time(self):
        images = [{'tod': tod} for tod in ('unknown', 'night', 'unknown', 'dawn')]
        self.assertEqual(module.time_corrections(images), [(images[3], 'night')])

    def test_document_order_and_case(self):
        images = [{'index': 9, 'tod': ' Evening '}, {'index': 1, 'tod': 'morning'}]
        self.assertEqual(module.time_corrections(images), [(images[1], 'evening')])

    def test_replaces_only_bad_slot_and_preserves_valid_assets(self):
        import sys
        from tempfile import TemporaryDirectory
        from types import SimpleNamespace
        from unittest.mock import Mock, patch
        from bs4 import BeautifulSoup

        with TemporaryDirectory() as directory:
            root = Path(directory)
            old, new, good = (root / name for name in ('img_2_old.png', 'img_2_new.png', 'img_1_good.png'))
            old.write_bytes(b'old')
            good.write_bytes(b'good')
            soup = BeautifulSoup(f'<chapter><paragraph><image index="1" tod="dusk" src="{good}"/><image index="2" tod="morning" src="{old}" animation_prompt="old animation"/></paragraph></chapter>', 'xml')
            tags = soup.find_all('image')
            chapter = SimpleNamespace(load_xml=Mock(), images=lambda: iter(tags), nice='test',
                                      save_xml=Mock(), get_workflow=Mock(return_value='workflow.json'))
            rerender = Mock()
            candidates = Mock(side_effect=[[str(old)], [str(old), str(new)]])
            modules = {
                'comfy': SimpleNamespace(run_workflow_fn=lambda _: new.write_bytes(b'new')),
                'const': SimpleNamespace(DEFAULT_IMAGE_TEMPLATE='default', LIBRARY_DIR=directory,
                                         COMFY_DIRS={'artifactserver': {'OUTPUT_DIR': directory}}),
                'artifact_editor.images.selector.htmx': SimpleNamespace(gather_candidate_images=candidates),
                'artifact_editor.textoviva.jobs': SimpleNamespace(rerender_chapter=rerender),
            }
            with patch.dict(sys.modules, modules):
                self.assertEqual(module.align_chapter_time(chapter), 1)
            self.assertEqual(tags[1]['tod'], 'dusk')
            self.assertEqual(tags[1]['src'], str(new))
            self.assertNotIn('animation_prompt', tags[1].attrs)
            self.assertFalse(old.exists())
            self.assertTrue(list(root.glob('.time-align-rejected/*/img_2_old.png')))
            self.assertEqual(good.read_bytes(), b'good')
            self.assertEqual(tags[0]['tod'], 'dusk')
            self.assertEqual(rerender.call_args.kwargs['regenerate_image_indices'], [2])
