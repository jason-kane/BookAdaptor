import importlib.util
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from bs4 import BeautifulSoup

spec = importlib.util.spec_from_file_location('prop_records', Path(__file__).parents[1] / 'props.py')
props = importlib.util.module_from_spec(spec)
with patch.dict(sys.modules, {'const': SimpleNamespace(LIBRARY_DIR='/unused')}):
    spec.loader.exec_module(props)


class PropTests(unittest.TestCase):
    def test_description_uses_story_phrases_and_current_object_name(self):
        story = BeautifulSoup('<chapter><paragraph><phrase>She held a silver cup.</phrase>'
                              '<image description="Invented golden goblet"/></paragraph></chapter>', 'xml')
        model = Mock(return_value='A silver cup.')
        with TemporaryDirectory() as root, patch.object(props.const, 'LIBRARY_DIR', root), patch.dict(
            sys.modules, {'artifact_editor': SimpleNamespace(llm=SimpleNamespace(str_prompt=model))}
        ):
            chapter = SimpleNamespace(chapterdir='a/b/1', get_xml=lambda: story)
            props.save_props(chapter, {'cup': {'name': 'Old name', 'description': ''}})
            self.assertEqual(props.generate_description(chapter, 'cup', name='Cup'), 'A silver cup.')
            prompt = model.call_args.kwargs['prompt']
            self.assertIn('Target object: Cup', prompt)
            self.assertIn('She held a silver cup.', prompt)
            self.assertNotIn('golden', prompt)
            self.assertEqual(props.get_all_props(chapter)['cup'],
                             {'name': 'Cup', 'description': 'A silver cup.'})
            story.find('phrase').decompose()
            model.reset_mock()
            self.assertEqual(props.generate_description(chapter, 'cup'), 'No description found.')
            model.assert_not_called()

    @patch.object(props, 'character_exclusions', return_value=['Alice', 'Mr. Stone', 'Roger', 'Tin Man'])
    def test_extraction_adds_supported_objects_and_preserves_existing_props(self, exclusions):
        story = BeautifulSoup('<chapter><paragraph><phrase>A silver cup and a brass key lay on the table.</phrase>'
                              '<image description="An invented sword"/></paragraph></chapter>', 'xml')
        model = Mock(return_value=[
            {'name': ' CUP ', 'description': 'Changed', 'evidence': 'A silver cup'},
            {'name': 'Key', 'description': 'A brass key', 'evidence': 'a brass key'},
            {'name': 'key', 'description': 'Duplicate', 'evidence': 'a brass key'},
            {'name': 'Sword', 'description': 'A sword', 'evidence': 'An invented sword'},
            *[{'name': name, 'description': 'A character', 'evidence': 'A silver cup'}
              for name in ('ALICE', 'Mr_Stone', 'roger', 'Tin-Man')],
            {'name': 'Broken'}, None,
        ])
        with TemporaryDirectory() as root, patch.object(props.const, 'LIBRARY_DIR', root), patch.dict(
            sys.modules, {'artifact_editor': SimpleNamespace(llm=SimpleNamespace(json_prompt=model))}
        ):
            chapter = SimpleNamespace(chapterdir='a/b/1', get_xml=lambda: story)
            original = {'name': 'Cup', 'description': 'My custom description'}
            props.save_props(chapter, {'cup': original})
            self.assertEqual(props.extract_props(chapter), 1)
            records = props.get_all_props(chapter)
            self.assertEqual(records['cup'], original)
            self.assertEqual(len(records), 2)
            self.assertNotIn('invented sword', model.call_args.args[0])
            self.assertIn('Excluded characters and aliases:', model.call_args.args[0])
            self.assertIn('Mr. Stone', model.call_args.args[0])
            self.assertEqual(props.extract_props(chapter), 0)
            model.return_value = {'invalid': 'response'}
            with self.assertRaises(ValueError):
                props.extract_props(chapter)
            self.assertEqual(props.get_all_props(chapter), records)
            story.find('phrase').decompose()
            model.reset_mock()
            self.assertEqual(props.extract_props(chapter), 0)
            model.assert_not_called()

    def test_delete_removes_only_chosen_prop_and_scene_references(self):
        with TemporaryDirectory() as root, patch.object(props.const, 'LIBRARY_DIR', root):
            xml = BeautifulSoup('<chapter><image scene_props="cup,key"/>'
                                '<image scene_props="cup"/><image scene_props="key"/></chapter>', 'xml')
            chapter = SimpleNamespace(chapterdir='a/b/1', get_xml=lambda: xml, save_xml=Mock())
            props.save_props(chapter, {'cup': {'name': 'Cup'}, 'key': {'name': 'Key'}})
            props.delete_prop(chapter, 'cup')
            self.assertEqual(props.get_all_props(chapter), {'key': {'name': 'Key'}})
            self.assertEqual([image['scene_props'] for image in xml.find_all('image')], ['key', '', 'key'])
            chapter.save_xml.assert_called_once()
            with self.assertRaises(KeyError):
                props.delete_prop(chapter, 'missing')

    def test_catalog_is_isolated_and_selection_uses_current_description(self):
        with TemporaryDirectory() as root, patch.object(props.const, 'LIBRARY_DIR', root):
            chapter = SimpleNamespace(chapterdir='author/title/0001')
            other = SimpleNamespace(chapterdir='author/title/0002')
            records = {'cup': {'name': 'Cup', 'description': 'A silver cup'},
                       'key': {'name': 'Key', 'description': 'A brass key'}}
            props.save_props(chapter, records)
            self.assertEqual(props.get_all_props(chapter), records)
            self.assertEqual(props.get_all_props(other), {})
            image = BeautifulSoup('<image scene_props="cup,missing"/>', 'xml').image
            prompt = props.include_in_prompt(chapter, image, 'A table.')
            self.assertIn('A silver cup', prompt)
            self.assertNotIn('brass', prompt)
            self.assertEqual(props.include_in_prompt(chapter, image, prompt), prompt)
            records['cup']['description'] = 'A red cup'
            props.save_props(chapter, records)
            self.assertIn('A red cup', props.include_in_prompt(chapter, image, 'A table.'))
            image['scene_props'] = ''
            self.assertEqual(props.include_in_prompt(chapter, image, 'A table.'), 'A table.')


if __name__ == '__main__':
    unittest.main()
