"""Timeline relocation checks without booting the application."""
import importlib.util
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from bs4 import BeautifulSoup

spec = importlib.util.spec_from_file_location('chapter_timeline', Path(__file__).parents[1] / 'timeline.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class TimelineTests(unittest.TestCase):
    def setUp(self):
        self.soup = BeautifulSoup('''<chapter><paragraph index="0"><image index="4" src="picture.png"/><phrase index="0" frames="10">First</phrase></paragraph><paragraph index="1"><phrase index="1" frames="20">Second</phrase><image index="7"/><phrase index="2" frames="30">Third</phrase></paragraph></chapter>''', 'xml')
        self.saved = False
        self.chapter = SimpleNamespace(get_xml=lambda: self.soup, get_paragraphdir=lambda index: f'paragraphs/{index:06d}', save_xml=self.save)

    def save(self):
        self.saved = True

    def test_import_images_use_nearest_phrase_starts_and_include_pauses(self):
        soup = BeautifulSoup('''<chapter>
            <paragraph index="0"><phrase index="0" duration="5" frames="50"/>
            <silence index="0" duration="0.5" frames="5"/>
            <phrase index="1" duration="4" frames="40"/></paragraph>
            <paragraph index="1"><phrase index="2" duration="6" frames="60"/>
            <phrase index="3" duration="4" frames="40"/>
            <silence index="1" duration="2" frames="20"/></paragraph>
            </chapter>''', 'xml')
        module.add_import_images(soup)
        images = soup.find_all('image')
        self.assertEqual([image['index'] for image in images], ['0', '1', '2'])
        self.assertEqual([image.find_next_sibling('phrase')['index'] for image in images],
                         ['0', '2', '3'])
        self.assertEqual([image['frames'] for image in images], ['95', '60', '60'])
        self.assertEqual(images[0].find_parent('paragraph')['index'], '0')
        self.assertEqual(images[1].find_parent('paragraph')['index'], '1')

    def test_import_images_deduplicate_long_phrase_and_handle_empty_chapter(self):
        soup = BeautifulSoup('<chapter><paragraph><phrase index="0" duration="25" frames="250"/></paragraph></chapter>', 'xml')
        module.add_import_images(soup)
        self.assertEqual(len(soup.find_all('image')), 1)
        self.assertEqual(soup.image['frames'], '250')
        empty = BeautifulSoup('<chapter/>', 'xml')
        module.add_import_images(empty)
        self.assertEqual(empty.find_all('image'), [])

    def test_import_images_accepts_chapter_tag_and_detached_chapter(self):
        for detached in (False, True):
            with self.subTest(detached=detached):
                document = BeautifulSoup('<chapter><paragraph index="0">'
                    '<phrase index="0" duration="8" frames="200">First</phrase>'
                    '<phrase index="1" duration="4" frames="100">Second</phrase>'
                    '</paragraph></chapter>', 'xml')
                chapter = document.chapter
                if detached:
                    chapter.extract()
                module.add_import_images(chapter)
                images = chapter.find_all('image')
                self.assertEqual([image['index'] for image in images], ['0', '1'])
                self.assertEqual([image['frames'] for image in images], ['200', '100'])
                self.assertEqual([image.find_next_sibling('phrase')['index']
                                  for image in images], ['0', '1'])

    def test_delete_each_panel_preserves_other_indices_and_updates_frames(self):
        for kind, index in [('phrase', 1), ('image', 7), ('silence', 0)]:
            with self.subTest(kind=kind):
                self.setUp()
                silence = module.add_silence(self.chapter, 'phrase', 0, 8)
                self.saved = False
                module.delete_timeline_item(self.chapter, kind, index)
                self.assertIsNone(self.soup.find(kind, index=str(index)))
                self.assertTrue(self.saved)
                self.assertEqual([tag['index'] for tag in self.soup.find_all('phrase')],
                                 ['0', '2'] if kind == 'phrase' else ['0', '1', '2'])
                expected_frames = {'phrase': '14', 'image': '64', 'silence': '30'}
                self.assertEqual(self.soup.find('image', index='4')['frames'], expected_frames[kind])

    def test_delete_rejects_missing_and_invalid_items_without_saving(self):
        before = str(self.soup)
        for kind, index in [('paragraph', 0), ('phrase', 99), ('image', 99), ('silence', 99)]:
            with self.assertRaises(ValueError):
                module.delete_timeline_item(self.chapter, kind, index)
        self.assertEqual(str(self.soup), before)
        self.assertFalse(self.saved)

    def test_silence_persists_and_offsets_following_timings(self):
        for phrase in self.soup.find_all('phrase'):
            phrase['duration'] = '1'
        silence = module.add_silence(self.chapter, 'phrase', 0, 8)
        self.assertEqual(silence['duration'], '0.500')
        module.save_silence(self.chapter, silence, '1.125', 8)
        self.assertEqual(silence['frames'], '9')
        self.assertEqual(str(module.phrase_timings(self.soup)['1']['start']), '2.125')
        self.assertEqual(str(module.image_timings(self.soup)['4']['duration']), '3.125')
        timing = module.phrase_timings(self.soup, item_type='silence')['0']
        self.assertEqual((str(timing['start']), str(timing['end'])), ('1', '2.125'))
        self.assertEqual((timing['frame_start'], timing['frame_end'], timing['frames']), (10, 18, 9))
        before = str(self.soup)
        for invalid in ['nan', 'Infinity', '-0.001', '3.001', 'garbage', '']:
            with self.assertRaises(ValueError):
                module.save_silence(self.chapter, silence, invalid, 8)
        self.assertEqual(str(self.soup), before)
        with self.assertRaises(ValueError):
            module.add_silence(self.chapter, 'phrase', 999, 8)

    def test_silence_panel_precision_and_tick_marks(self):
        from jinja2 import Environment, FileSystemLoader

        silence = module.add_silence(self.chapter, 'phrase', 0, 8)
        self.chapter.kwargs = {}
        env = Environment(loader=FileSystemLoader(Path(__file__).parents[1] / 'templates'))
        env.globals['url_for'] = lambda *args, **kwargs: '/silence'
        rendered = env.get_template('chapter_timeline_silence.html').render(
            chapter=self.chapter, silence=silence)
        self.assertIn('value="0.500"', rendered)
        self.assertIn('step="0.001"', rendered)
        self.assertEqual(rendered.count('slot="reference"'), 7)
        for tick in range(7):
            self.assertIn('<span slot="reference">' + str(tick / 2) + '</span>', rendered)

    def test_add_phrase_below_inherits_speaker_and_preserves_existing_content(self):
        target = self.soup.find('phrase', index='0')
        target['speaker'] = 'Alice'
        target['src'] = 'existing.wav'
        target['duration'] = '1.5'
        phrase = module.add_phrase_below(self.chapter, 0)
        self.assertIs(target.find_next_sibling(), phrase)
        self.assertEqual(phrase['speaker'], 'Alice')
        self.assertEqual(phrase.get_text(), '')
        self.assertNotIn('src', phrase.attrs)
        self.assertNotIn('duration', phrase.attrs)
        self.assertEqual(target['src'], 'existing.wav')
        self.assertEqual([tag['index'] for tag in self.soup.find_all('phrase')], ['0', '1', '2', '3'])
        self.assertEqual([tag['index'] for tag in self.soup.find_all('image')], ['4', '7'])
        self.assertTrue(self.saved)
        before = str(self.soup)
        with self.assertRaises(ValueError):
            module.add_phrase_below(self.chapter, 999)
        self.assertEqual(str(self.soup), before)

    def test_phrase_editor_delta_for_new_and_existing_phrases(self):
        import ast
        import json
        from unittest.mock import Mock

        # Load the real wrapper without importing the application's services.
        source = ast.parse((Path(__file__).parents[1] / 'chapter.py').read_text())
        phrase_class = next(node for node in source.body
                            if isinstance(node, ast.ClassDef) and node.name == 'Phrase')
        namespace = {'json': json, 'log': Mock(), 'as_int': int,
                     'Tag': type(self.soup.find('phrase'))}
        exec(compile(ast.Module(body=[phrase_class], type_ignores=[]),
                     'chapter.py', 'exec'), namespace)
        Phrase = namespace['Phrase']
        new_phrase = module.add_phrase_below(self.chapter, 0)
        self.assertEqual(json.loads(Phrase(self.chapter, new_phrase).delta), [])
        legacy = self.soup.find('phrase', index='0')
        self.assertEqual(json.loads(Phrase(self.chapter, legacy).delta), [{'insert': 'First'}])
        legacy['delta'] = '[{"insert":"Styled", "attributes":{"bold":true}}]'
        self.assertEqual(Phrase(self.chapter, legacy).delta, legacy['delta'])

    def test_audio_tracks_include_real_silence_in_document_order(self):
        import ast
        import contextlib
        import decimal
        import os
        import wave
        from unittest.mock import Mock

        source = ast.parse((Path(__file__).parents[2] / 'phrases/phrases.py').read_text())
        routine = next(node for node in source.body if isinstance(node, ast.FunctionDef)
                       and node.name == 'wav_append_delay')
        scope = dict(os=os, contextlib=contextlib, wave=wave, decimal=decimal, log=Mock())
        exec(compile(ast.Module(body=[routine], type_ignores=[]), 'phrases.py', 'exec'), scope)
        with TemporaryDirectory() as temp:
            self.chapter.get_paragraph_dir = self.chapter.get_paragraphdir
            for phrase in self.soup.find_all('phrase'):
                phrase['src'] = f"phrase_{phrase['index']}.wav"
                folder = Path(temp) / self.chapter.get_paragraph_dir(int(phrase.parent['index']))
                folder.mkdir(parents=True, exist_ok=True)
                with wave.open(str(folder / phrase['src']), 'wb') as wav:
                    wav.setparams((2, 2, 24000, 0, 'NONE', 'not compressed'))
                    wav.writeframes(b'\x01\x00' * 2 * 2400)
            silence = module.add_silence(self.chapter, 'phrase', 0, 8)
            module.save_silence(self.chapter, silence, '0.750', 8)
            zero = module.add_silence(self.chapter, 'phrase', 2, 8)
            module.save_silence(self.chapter, zero, '0', 8)
            for duration in ['0.750', '0.750', '1.125']:
                silence['duration'] = duration
                tracks = module.chapter_audio_tracks(self.chapter, temp, scope['wav_append_delay'])
                self.assertEqual([Path(track).name for track in tracks],
                                 ['phrase_0.wav', 'silence_0.wav', 'phrase_1.wav', 'phrase_2.wav'])
                with wave.open(tracks[1], 'rb') as wav:
                    self.assertEqual((wav.getnchannels(), wav.getsampwidth(), wav.getframerate()),
                                     (2, 2, 24000))
                    self.assertEqual(wav.getnframes(), round(float(duration) * 24000))
                    self.assertEqual(set(wav.readframes(wav.getnframes())), {0})

    def test_import_silences_follow_blank_lines_and_end_chapter(self):
        soup = BeautifulSoup('<chapter><paragraph index="0">'
                             '<phrase index="0">First line\nsecond line\n \n</phrase>'
                             '<phrase index="1">Next paragraph.</phrase>'
                             '<phrase index="2">\n\nAnother paragraph.\n\n</phrase>'
                             '</paragraph></chapter>', 'xml')
        module.add_import_silences(soup, 8)
        tags = soup.find_all(['phrase', 'silence'])
        self.assertEqual([tag.name for tag in tags],
                         ['phrase', 'silence', 'phrase', 'silence', 'phrase', 'silence'])
        self.assertEqual([(tag['index'], tag['duration'], tag['frames'])
                          for tag in soup.find_all('silence')],
                         [('0', '0.500', '4'), ('1', '0.500', '4'), ('2', '2.000', '16')])

    def test_import_single_line_breaks_only_add_final_silence(self):
        soup = BeautifulSoup('<chapter><paragraph><phrase index="0">\n\nFirst\n</phrase>'
                             '<phrase index="1">Second\n\n</phrase></paragraph></chapter>', 'xml')
        module.add_import_silences(soup, 8)
        self.assertEqual([tag['duration'] for tag in soup.find_all('silence')], ['2.000'])
        empty = BeautifulSoup('<chapter/>', 'xml')
        module.add_import_silences(empty, 8)
        self.assertEqual(empty.find_all('silence'), [])

    def test_audio_badge_checks_file_and_renders_without_timings(self):
        from jinja2 import Environment, FileSystemLoader

        phrase = self.soup.find('phrase')
        self.chapter.get_paragraph_dir = self.chapter.get_paragraphdir
        with TemporaryDirectory() as temp:
            self.assertTrue(module.phrase_audio_missing(self.chapter, phrase, temp))
            phrase['src'] = 'audio.wav'
            self.assertTrue(module.phrase_audio_missing(self.chapter, phrase, temp))
            folder = Path(temp) / self.chapter.get_paragraph_dir(0)
            folder.mkdir(parents=True)
            (folder / 'audio.wav').write_bytes(b'audio')
            self.assertFalse(module.phrase_audio_missing(self.chapter, phrase, temp))
        env = Environment(loader=FileSystemLoader(Path(__file__).parents[1] / 'templates'))
        template = env.get_template('chapter_phrase_timing.html')
        for missing in [True, False]:
            rendered = template.render(timing_id='chapter-phrase-timing-0', timing=None,
                                       missing_audio=missing, timing_oob=True)
            self.assertEqual('variant="danger">Audio</wa-badge>' in rendered, missing)

    def test_recent_images_include_older_current_selection(self):
        import os

        with TemporaryDirectory() as temp:
            paths = []
            for index in range(20):
                path = Path(temp) / f'img_4_{index}.png'
                path.write_bytes(b'image')
                os.utime(path, (index + 1, index + 1))
                paths.append(path)
            result = module.recent_image_candidates(paths, paths[0])
            self.assertEqual([item['filename'] for item in result],
                             [paths[index].name for index in list(range(19, 2, -1)) + [0]])
            self.assertEqual(sum(item['selected'] for item in result), 1)
            result = module.recent_image_candidates(paths, paths[19])
            self.assertEqual(len(result), 18)
            self.assertTrue(result[0]['selected'])
            self.assertEqual(module.recent_image_candidates([Path(temp) / 'missing.png']), [])

    def test_thumbnail_picker_renders_selected_image_and_selection_request(self):
        from jinja2 import Environment, FileSystemLoader

        env = Environment(loader=FileSystemLoader(Path(__file__).parents[1] / 'templates'), autoescape=True)
        env.globals['url_for'] = lambda endpoint, **kwargs: '/image/' + str(kwargs.get('filename', ''))
        self.chapter.kwargs = {}
        self.chapter.author = SimpleNamespace(name='Author')
        self.chapter.title = 'Book'
        self.chapter.number = 1
        self.chapter.language = 'english'
        rendered = env.get_template('chapter_timeline_image_editor.html').render(
            chapter=self.chapter, image=self.soup.find('image'),
            thumbnails=[{'filename': 'img_4_example.png', 'selected': True}],
            preview_version=1, default_image_template='default')
        self.assertIn('aria-pressed="true"', rendered)
        self.assertIn('"action": "select"', rendered)
        self.assertIn('/image/img_4_example.png', rendered)

    def test_audio_timings_accumulate_across_images_and_paragraphs(self):
        phrases = self.soup.find_all('phrase')
        phrases[0]['duration'] = '0.4'
        phrases[1]['duration'] = '0.8'
        timings = module.phrase_timings(self.soup)
        self.assertNotIn('2', timings)
        self.assertEqual(timings['0']['frame_start'], 0)
        self.assertEqual(timings['0']['frame_end'], 9)
        self.assertEqual(str(timings['1']['start']), '0.4')
        self.assertEqual(str(timings['1']['end']), '1.2')
        self.assertEqual(timings['1']['frame_start'], 10)
        self.assertEqual(timings['1']['frame_end'], 29)
        phrases[0]['duration'] = '0.6'
        phrases[0]['frames'] = '15'
        updated = module.phrase_timings(self.soup)['1']
        self.assertEqual(str(updated['start']), '0.6')
        self.assertEqual(updated['frame_start'], 15)

    def test_timing_template_and_oob_refresh(self):
        from jinja2 import Environment, FileSystemLoader

        phrase = self.soup.find('phrase')
        phrase['duration'] = '0.4'
        env = Environment(loader=FileSystemLoader(Path(__file__).parents[1] / 'templates'), autoescape=True)
        template = env.get_template('chapter_phrase_timing.html')
        rendered = template.render(phrase=SimpleNamespace(index=0), timing=module.phrase_timings(self.soup)['0'], timing_oob=True)
        for value in ['0.00s (0) - 0.40s (9): 0.40s (10)', 'hx-swap-oob="outerHTML"']:
            self.assertIn(value, rendered)
        empty = template.render(phrase=SimpleNamespace(index=1), timing=None)
        self.assertNotIn('0.00s (', empty)

    def test_image_timing_spans_paragraphs_and_final_phrase(self):
        self.assertEqual(module.image_timings(self.soup), {})
        for phrase, duration in zip(self.soup.find_all('phrase'), ['0.4', '0.8', '1.2']):
            phrase['duration'] = duration
        timings = module.image_timings(self.soup)
        first, last = timings['4'], timings['7']
        self.assertEqual(str(first['duration']), '1.2')
        self.assertEqual((first['frame_start'], first['frame_end'], first['frames']), (0, 29, 30))
        self.assertEqual((str(last['start']), str(last['end'])), ('1.2', '2.4'))
        self.assertEqual((last['frame_start'], last['frame_end'], last['frames']), (30, 59, 30))
        module.add_image_below(self.chapter, 0)
        timings = module.image_timings(self.soup)
        self.assertEqual(str(timings['4']['duration']), '0.4')
        self.assertEqual(str(timings['8']['duration']), '0.8')

    def test_consecutive_images_have_empty_span(self):
        self.soup.find('phrase')['duration'] = '0.4'
        image = self.soup.new_tag('image', attrs={'index': '9'})
        self.soup.find('image').insert_before(image)
        timing = module.image_timings(self.soup)['9']
        self.assertEqual(timing['frames'], 0)
        self.assertEqual(timing['duration'], 0)
        self.assertIsNone(timing['frame_end'])

    def test_image_duration_warning_threshold_and_refresh(self):
        from jinja2 import Environment, FileSystemLoader

        phrases = self.soup.find_all('phrase')
        phrases[0]['duration'] = '4'
        phrases[1]['duration'] = '4'
        template = Environment(loader=FileSystemLoader(Path(__file__).parents[1] / 'templates')).get_template('chapter_phrase_timing.html')
        for duration, expected in [('4', False), ('4.001', True), ('3', False)]:
            phrases[1]['duration'] = duration
            timing = module.image_timings(self.soup)['4']
            self.assertEqual(timing['exceeds_animation_duration'], expected)
            rendered = template.render(timing=timing, timing_id='chapter-image-timing-4', timing_oob=True)
            self.assertEqual('animation-duration-warning' in rendered, expected)

    def test_add_image_below_preserves_ids_and_is_draggable(self):
        from jinja2 import Environment, FileSystemLoader

        image = module.add_image_below(self.chapter, 0)
        self.assertEqual(image['index'], '8')
        self.assertNotIn('src', image.attrs)
        self.assertIs(self.soup.find('phrase', index='0').find_next_sibling(), image)
        self.assertEqual([tag['index'] for tag in self.soup.find_all('image')], ['4', '8', '7'])
        self.assertTrue(self.saved)
        self.chapter.kwargs = {}
        env = Environment(loader=FileSystemLoader(Path(__file__).parents[1] / 'templates'), autoescape=True)
        env.globals['url_for'] = lambda endpoint, **kwargs: (
            '/static/' + kwargs['filename'] if endpoint == 'static'
            else '/image/' + str(kwargs.get('image_index', ''))
        )
        rendered = env.get_template('chapter_timeline_image.html').render(chapter=self.chapter, image=image)
        self.assertIn('draggable="true"', rendered)
        self.assertIn('data-timeline-index="8"', rendered)
        self.assertIn('Not generated', rendered)
        preview = BeautifulSoup(rendered, 'html.parser').find('img')
        self.assertEqual(preview['src'], '/static/images/x.png')
        image['src'] = 'generated.png'
        rendered = env.get_template('chapter_timeline_image.html').render(chapter=self.chapter, image=image)
        self.assertEqual(BeautifulSoup(rendered, 'html.parser').find('img')['src'], '/image/8')

    def test_remove_all_metadata_preserves_image_assets_timing_and_workflows(self):
        preserved = {'index': '4', 'src': 'picture.png', 'animated_src': 'animation.mp4',
                     'frames': '100', 'duration': '4', 'workflow_image_template': 'test',
                     'workflow_i2v_template': 'animation-test', 'image_lock': 'off'}
        generated = {'style': 'ink', 'description': 'Scene', 'setting': 'Garden',
                     'tod': 'day', 'camera': 'wide', 'scene_characters': 'Alice',
                     'focus_character': 'Alice', 'lighting_source': 'sun',
                     'Alice_pose': 'standing', 'Alice_action': 'looking',
                     'Alice_location': 'left', 'Alice_description': 'Tall',
                     'Alice_motivation': 'curious', 'animation_prompt': 'Blink',
                     'prompt': 'Old prompt', 't5_prompt': 'Old prose'}
        for image in self.soup.find_all('image'):
            image.attrs.update(generated)
        image = self.soup.find('image', index='4')
        image.attrs.update(preserved)
        self.soup.find('phrase')['src'] = 'audio.wav'
        module.remove_all_image_metadata(self.chapter)
        self.assertTrue(self.saved)
        self.assertEqual(dict(image.attrs), preserved)
        self.assertTrue(all(not (set(tag.attrs) & set(generated)) for tag in self.soup.find_all('image')))
        self.assertEqual(self.soup.find('phrase')['src'], 'audio.wav')

    def test_detach_all_images_preserves_slots_metadata_and_other_assets(self):
        image = self.soup.find('image', index='4')
        image['description'] = 'Keep this description'
        image['animated_src'] = 'animation.mp4'
        phrase = self.soup.find('phrase')
        phrase['src'] = 'narration.wav'
        before = [(tag.name, tag.get('index')) for tag in self.soup.find_all(['image', 'phrase'])]
        module.detach_all_images(self.chapter)
        self.assertTrue(self.saved)
        self.assertTrue(all('src' not in tag.attrs for tag in self.soup.find_all('image')))
        self.assertEqual(image['description'], 'Keep this description')
        self.assertEqual(image['animated_src'], 'animation.mp4')
        self.assertEqual(phrase['src'], 'narration.wav')
        self.assertEqual(before, [(tag.name, tag.get('index')) for tag in self.soup.find_all(['image', 'phrase'])])

    def test_collapsed_image_thumbnail_url_tracks_selected_asset(self):
        from jinja2 import Environment, FileSystemLoader
        from urllib.parse import urlencode

        self.chapter.kwargs = {}
        env = Environment(loader=FileSystemLoader(Path(__file__).parents[1] / 'templates'), autoescape=True)
        env.globals['url_for'] = lambda endpoint, **kwargs: '/image?' + urlencode(kwargs)
        template = env.get_template('chapter_timeline_image.html')
        image = self.soup.find('image', index='4')
        urls = []
        for filename in ['first.png', 'replacement.png']:
            image['src'] = filename
            rendered = template.render(chapter=self.chapter, image=image)
            url = BeautifulSoup(rendered, 'html.parser').find('img')['src']
            self.assertIn('v=' + filename, url)
            self.assertIn('image_index=4', url)
            urls.append(url)
        self.assertNotEqual(*urls)

    def test_missing_image_editor_uses_placeholder_for_preview_and_refresh(self):
        from jinja2 import Environment, FileSystemLoader

        self.chapter.kwargs = {}
        env = Environment(loader=FileSystemLoader(Path(__file__).parents[1] / 'templates'), autoescape=True)
        env.globals['url_for'] = lambda endpoint, **kwargs: (
            '/static/' + kwargs['filename'] if endpoint == 'static' else '/image/7'
        )
        image = self.soup.find('image', index='7')
        template = env.get_template('chapter_timeline_image_editor.html')
        for src, expected in [('', '/static/images/x.png'), ('generated.png', '/image/7')]:
            image['src'] = src
            rendered = template.render(chapter=self.chapter, image=image, thumbnails=[], preview_version=1)
            previews = BeautifulSoup(rendered, 'html.parser').select('img.chapter-mini-image-preview, img[hx-swap-oob]')
            label = BeautifulSoup(rendered, 'html.parser').select_one('#timeline-image-label-7')
            self.assertEqual(label['hx-swap-oob'], 'outerHTML')
            self.assertEqual('Not generated' in label.text, not bool(src))
            self.assertIn('Image 7', label.text)
            self.assertEqual(len(previews), 2)
            self.assertTrue(all(preview['src'] == expected for preview in previews))

    def test_add_to_empty_image_timeline_and_missing_phrase(self):
        for image in self.soup.find_all('image'):
            image.decompose()
        self.assertEqual(module.add_image_below(self.chapter, 2)['index'], '0')
        before = str(self.soup)
        with self.assertRaises(ValueError):
            module.add_image_below(self.chapter, 99)
        self.assertEqual(str(self.soup), before)

    def test_cross_paragraph_move_preserves_assets_and_updates_frames(self):
        with TemporaryDirectory() as temp:
            source = Path(temp) / 'paragraphs/000000'
            source.mkdir(parents=True)
            (source / 'picture.png').write_bytes(b'image')
            animation = source / 'animation/image_000004_00'
            animation.mkdir(parents=True)
            (animation / 'frame.png').write_bytes(b'frame')
            module.move_image(self.chapter, 4, 'phrase', 1, 'before', temp)
            image = self.soup.find('image', index='4')
            self.assertEqual(image.parent['index'], '1')
            self.assertEqual(image['frames'], '20')
            self.assertEqual(self.soup.find('image', index='7')['frames'], '30')
            self.assertEqual((Path(temp) / 'paragraphs/000001/picture.png').read_bytes(), b'image')
            self.assertTrue((Path(temp) / 'paragraphs/000001/animation/image_000004_00/frame.png').exists())
            self.assertTrue(self.saved)

    def test_move_after_image_and_reject_invalid_destination(self):
        with TemporaryDirectory() as temp:
            module.move_image(self.chapter, 4, 'image', 7, 'after', temp)
            tags = self.soup.find_all(['image', 'phrase'])
            self.assertEqual([(tag.name, tag['index']) for tag in tags], [('phrase', '0'), ('phrase', '1'), ('image', '7'), ('image', '4'), ('phrase', '2')])
            before = str(self.soup)
            with self.assertRaises(ValueError):
                module.move_image(self.chapter, 4, 'phrase', 99, 'before', temp)
            self.assertEqual(str(self.soup), before)

    def test_conflicting_asset_does_not_move_or_overwrite(self):
        with TemporaryDirectory() as temp:
            for index, data in [(0, b'original'), (1, b'other')]:
                folder = Path(temp) / f'paragraphs/{index:06d}'
                folder.mkdir(parents=True)
                (folder / 'picture.png').write_bytes(data)
            with self.assertRaises(ValueError):
                module.move_image(self.chapter, 4, 'phrase', 1, 'before', temp)
            self.assertEqual(self.soup.find('image', index='4').parent['index'], '0')
            self.assertFalse(self.saved)


if __name__ == '__main__':
    unittest.main()
