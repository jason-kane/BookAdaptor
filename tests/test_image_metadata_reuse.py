"""Existing character metadata is complete unless regeneration is forced."""
import ast
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


SOURCE = Path(__file__).resolve().parents[1] / 'src/artifact_editor/images/images.py'


def load_function(name, namespace):
    function = next(node for node in ast.parse(SOURCE.read_text()).body
                    if isinstance(node, ast.FunctionDef) and node.name == name)
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(SOURCE), 'exec'), namespace)
    return namespace[name]


class MetadataReuseTests(unittest.TestCase):
    def test_direct_generators_skip_existing_fields_even_when_empty(self):
        for field in ('pose', 'location', 'action'):
            for value in ('existing value', ''):
                with self.subTest(field=field, value=value):
                    context = Mock(side_effect=AssertionError('Must not request context'))
                    function = load_function('generate_metadata_character_' + field,
                                             dict(Tag=object, get_image_narration_context=context))
                    image = SimpleNamespace(attrs={'jack_' + field: value})
                    arguments = ({'tag': 'jack'}, image)
                    if field != 'action':
                        arguments += (object(),)
                    function(*arguments)
                    context.assert_not_called()

    def test_forced_action_bypasses_cache_and_replaces_existing_value(self):
        llm = SimpleNamespace(json_prompt=Mock(return_value={'action': 'new action'}))
        function = load_function('generate_metadata_character_action', dict(
            Tag=object, llm=llm, log=Mock(), get_text_to_next=Mock(return_value='story'),
            get_image_narration_context=Mock(return_value='context')))
        image = SimpleNamespace(attrs={'jack_action': 'old action'}, find_next=Mock())
        function({'tag': 'jack', 'name': 'Jack'}, image, force=True)
        self.assertEqual(image.attrs['jack_action'], 'new action')
        self.assertTrue(llm.json_prompt.call_args.kwargs['force'])

    def test_batch_only_generates_missing_fields_unless_forced(self):
        for missing, force in ((None, False), ('action', False), (None, True)):
            with self.subTest(missing=missing, force=force):
                character = {'tag': 'jack', 'name': 'Jack'}
                scene = Mock()
                scene.generate_scene_characters.return_value = ['Jack']
                namespace = dict(
                    Tag=object, log=Mock(), scene=scene,
                    characters=SimpleNamespace(
                        get_all_characters=lambda _: {'jack': character},
                        get_character=lambda *_: character),
                    generate_metadata_style=Mock(), generate_metadata_setting=Mock())
                generators = {}
                for field in ('pose', 'location', 'action'):
                    generators[field] = Mock()
                    namespace['generate_metadata_character_' + field] = generators[field]
                function = load_function('generate_metadata_from_text', namespace)
                image = SimpleNamespace(attrs=dict(
                    index='0', style='existing', camera='existing', focus_character='Jack',
                    scene_characters='Jack', jack_pose='standing', jack_location='CS',
                    jack_action='watching'))
                if missing:
                    del image.attrs['jack_' + missing]
                with patch.dict(sys.modules, {'artifact_editor.chapter.scenes':
                                             SimpleNamespace(active_scene=lambda _: object())}):
                    function(SimpleNamespace(save_xml=Mock()), image, force_all=force)
                for field, generator in generators.items():
                    if force or field == missing:
                        generator.assert_called_once()
                        self.assertEqual(generator.call_args.kwargs['force'], force)
                    else:
                        generator.assert_not_called()
