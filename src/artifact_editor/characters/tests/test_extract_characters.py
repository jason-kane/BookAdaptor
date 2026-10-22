import ast
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from bs4 import BeautifulSoup


class ExtractionTests(unittest.TestCase):
    def test_only_supported_new_characters_are_added(self):
        path = Path(__file__).parents[1] / 'characters.py'
        tree = ast.parse(path.read_text())
        nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef)
                 and n.name in {'extract_characters', 'name_to_tag', 'resolve_character_tag', 'character_names'}]
        original = {'name': 'Alice', 'tag': 'Alice', 'description': 'Custom', 'voices': [{'id': 'voice'}]}
        catalog = {'Alice': original}
        model = Mock(return_value=[
            {'name': 'alice', 'description': 'Replacement', 'evidence': 'Alice'},
            {'name': 'Fox', 'description': 'A red fox', 'evidence': 'a red fox'},
            {'name': 'fox', 'description': '', 'evidence': 'a red fox'},
            {'name': 'Dragon', 'description': '', 'evidence': 'invented dragon'},
            {'name': 'Narrator', 'description': '', 'evidence': 'Alice'}, None,
        ])
        save = Mock()
        namespace = {'json': json, 'llm': SimpleNamespace(json_prompt=model),
                     'get_all_characters': lambda chapter: dict(catalog), 'save_characters': save}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), namespace)
        story = BeautifulSoup('<chapter><paragraph><phrase>Alice saw a red fox.</phrase>'
                              '<image description="invented dragon"/></paragraph></chapter>', 'xml')
        chapter = SimpleNamespace(get_xml=lambda: story)
        self.assertEqual(namespace['extract_characters'](chapter), 1)
        records = save.call_args.args[1]
        self.assertEqual(records['Alice'], original)
        self.assertEqual(records['Fox']['description'], 'A red fox')
        self.assertEqual(records['Fox']['voices'], [])
        self.assertNotIn('invented dragon', model.call_args.args[0])
        catalog.update(records)
        self.assertEqual(namespace['extract_characters'](chapter), 0)
        story.find('phrase').decompose()
        model.reset_mock()
        self.assertEqual(namespace['extract_characters'](chapter), 0)
        model.assert_not_called()
