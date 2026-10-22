import ast
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import unittest
from bs4 import BeautifulSoup


class AliasTests(unittest.TestCase):
    def setUp(self):
        path = Path(__file__).parents[1] / 'characters.py'
        names = {'name_to_tag', 'character_names', 'resolve_character_tag', 'set_aliases',
                 'merge_characters', 'rewrite_character_references'}
        nodes = [n for n in ast.parse(path.read_text()).body if isinstance(n, ast.FunctionDef) and n.name in names]
        self.records = {'Mr_Stone': {'tag': 'Mr_Stone', 'name': 'Mr. Stone', 'description': 'Gray coat',
                                    'voices': [{'id': 'voice'}], 'alias': ['Roger']},
                        'Rob': {'tag': 'Rob', 'name': 'Rob', 'description': 'Blue coat', 'alias': ['Mr. S']}}
        self.xml = BeautifulSoup('<chapter><paragraph><phrase speaker="Rob">Hello</phrase>'
            '<image scene_characters="Mr_Stone,Rob" focus_character="Mr_S" Rob_pose="standing" '
            'Mr_Stone_description="Target" Rob_description="Source"/></paragraph></chapter>', 'xml')
        self.chapter = SimpleNamespace(get_xml=lambda: self.xml, save_xml=lambda: None)
        def save(chapter, records):
            self.records = deepcopy(records)
        self.ns = {'get_all_characters': lambda chapter: deepcopy(self.records), 'save_characters': save}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), self.ns)

    def test_merge_rewrites_references_and_keeps_target_settings(self):
        self.ns['merge_characters'](self.chapter, 'Rob', 'Mr_Stone')
        self.assertNotIn('Rob', self.records)
        target = self.records['Mr_Stone']
        self.assertEqual(target['description'], 'Gray coat')
        self.assertEqual(target['voices'], [{'id': 'voice'}])
        self.assertEqual(target['merged_characters'][0]['description'], 'Blue coat')
        for name in ('roger', 'Mr. Stone', 'rob', 'Mr. S', 'Mr_S'):
            self.assertEqual(self.ns['resolve_character_tag'](self.records, name), 'Mr_Stone')
        self.assertEqual(self.xml.phrase['speaker'], 'Mr_Stone')
        self.assertEqual(self.xml.image['scene_characters'], 'Mr_Stone')
        self.assertEqual(self.xml.image['focus_character'], 'Mr_Stone')
        self.assertEqual(self.xml.image['Mr_Stone_pose'], 'standing')
        self.assertEqual(self.xml.image['Mr_Stone_description'], 'Target')
        self.assertNotIn('Rob_description', self.xml.image.attrs)
        with self.assertRaises(ValueError):
            self.ns['merge_characters'](self.chapter, 'Rob', 'Mr_Stone')

    def test_alias_conflicts_do_not_mutate_catalog(self):
        original = deepcopy(self.records)
        with self.assertRaises(ValueError):
            self.ns['set_aliases'](self.chapter, 'Mr_Stone', ['Rob'])
        self.assertEqual(self.records, original)
        self.ns['set_aliases'](self.chapter, 'Mr_Stone', ['Roger', 'ROGER', 'Stone'])
        self.assertEqual(self.records['Mr_Stone']['alias'], ['Roger', 'Stone'])
        self.records['Rob']['alias'] = ['Stone']
        with self.assertRaises(ValueError):
            self.ns['resolve_character_tag'](self.records, 'Stone')
