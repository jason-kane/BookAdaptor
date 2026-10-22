"""Catalog cleanup checks without importing generation services."""
import ast
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock


class CharacterValidationTests(unittest.TestCase):
    def test_invalid_characters_are_logged_and_removed_from_saved_catalog(self):
        source = Path(__file__).parents[1] / 'characters.py'
        tree = ast.parse(source.read_text())
        names = {'_get_characters_fn', 'save_characters', 'get_all_characters'}
        functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
        with TemporaryDirectory() as temp:
            log = Mock()
            namespace = {
                'os': os, 'json': json, 'log': log,
                'const': SimpleNamespace(LIBRARY_DIR=temp, GLOBAL_CHARACTERS_DIR=temp),
                'number_of_appearances': lambda chapter, characters: characters,
            }
            exec(compile(ast.Module(body=functions, type_ignores=[]), str(source), 'exec'), namespace)
            chapter = SimpleNamespace(chapterdir='chapter')
            for is_global in (False, True):
                with self.subTest(is_global=is_global):
                    log.reset_mock()
                    valid = {'name': 'Alice', 'tag': 'Alice', 'audio_appearances': 0, 'visual_appearances': 0}
                    catalog = {
                        'Alice': valid, '(': {'name': '('},
                        'Bad(Tag': {'name': 'Bad'}, '': {}, 'Null': None,
                        'List': [], 'BadStoredTag': {'tag': '('},
                    }
                    namespace['save_characters'](chapter, catalog, is_global=is_global)
                    cleaned = namespace['get_all_characters'](chapter, is_global=is_global)
                    self.assertEqual(cleaned, {'Alice': valid})
                    self.assertEqual(log.error.call_count, 6)
                    filename = namespace['_get_characters_fn'](chapter, is_global=is_global)
                    self.assertEqual(json.loads(Path(filename).read_text()), cleaned)
                    log.reset_mock()
                    self.assertEqual(namespace['get_all_characters'](chapter, is_global=is_global), cleaned)
                    log.error.assert_not_called()


if __name__ == '__main__':
    unittest.main()
