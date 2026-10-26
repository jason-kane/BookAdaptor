import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

from PIL import Image, PngImagePlugin

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from styler.jobs import sample_task
from styler.sample_metadata import write_metadata, image_tags


class SampleMetadataTests(unittest.TestCase):
    def test_display_reads_current_sidecar_and_handles_missing_or_stale_data(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'artist_zit.png'
            path.write_bytes(b'image')
            self.assertEqual(image_tags(path), [])
            metadata = write_metadata(path, sample_task(dict(style_tag='artist', engine='zit')))
            metadata['tags'] = ['from sidecar', '<literal text>']
            path.with_suffix('.json').write_text(json.dumps(metadata))
            self.assertEqual(image_tags(path), metadata['tags'])
            path.write_bytes(b'replacement image')
            self.assertEqual(image_tags(path), [])
            path.with_suffix('.json').write_text('broken JSON')
            self.assertEqual(image_tags(path), [])

    def test_recovers_inlined_positive_prompt_from_old_sampler(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'artist_zit.png'
            info = PngImagePlugin.PngInfo()
            info.add_text('prompt', json.dumps({
                '1': {'class_type': 'CLIPTextEncode', 'inputs': {'text': 'Exact old prompt'}},
                '2': {'class_type': 'KSampler', 'inputs': {'positive': ['1', 0]}}}))
            Image.new('RGB', (2, 2)).save(path, pnginfo=info)
            result = write_metadata(path, sample_task(dict(style_tag='artist', engine='zit')))
            self.assertEqual(result['prompt'], 'Exact old prompt')

    def test_recovers_original_prompt_and_keeps_it_when_tags_change(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'artist_qwen.png'
            prompt = 'Original prompt, with exact whitespace.\n  Watercolor ♥'
            info = PngImagePlugin.PngInfo()
            info.add_text('prompt', json.dumps({'1': {
                'class_type': 'PrimitiveStringMultiline', '_meta': {'title': 'PROMPT'},
                'inputs': {'value': prompt}}}))
            Image.new('RGB', (2, 2)).save(path, pnginfo=info)
            task = sample_task(dict(style_tag='artist', engine='qwen'))
            first = write_metadata(path, task)
            self.assertEqual(first['prompt'], prompt)
            self.assertEqual(first['tags'], [])
            record = dict(sha256=first['sha256'], tags=['watercolor', 'soft light'], analysis_version='v3')
            write_metadata(path, task, record=record)
            result = json.loads(path.with_suffix('.json').read_text())
            self.assertEqual(result['prompt'], prompt)
            self.assertEqual(result['tags'], record['tags'])
            self.assertEqual(list(Path(tmp).glob('*.tmp')), [])

    def test_replacement_does_not_inherit_old_tags_or_prompt(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'artist_zit.png'
            Image.new('RGB', (2, 2)).save(path)
            task = sample_task(dict(style_tag='artist', engine='zit'))
            old = write_metadata(path, task, prompt='Old prompt')
            old.update(tags=['old tags'])
            path.with_suffix('.tags.json').write_text(json.dumps(old))
            Image.new('RGB', (2, 2), 'red').save(path)
            result = write_metadata(path, task, prompt='New prompt')
            self.assertEqual(result['prompt'], 'New prompt')
            self.assertEqual(result['tags'], [])

    def test_backfill_imports_matching_tags_and_leaves_unknown_prompt_null(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'artist_zit.png'
            Image.new('RGB', (2, 2)).save(path)
            task = sample_task(dict(style_tag='artist', engine='zit'))
            old = write_metadata(path, task)
            old.update(tags=['ink drawing'])
            path.with_suffix('.tags.json').write_text(json.dumps(old))
            result = write_metadata(path, task)
            self.assertIsNone(result['prompt'])
            self.assertEqual(result['tags'], ['ink drawing'])
