"""Appearance continuity across chapter and phrase boundaries."""
import ast
from pathlib import Path
import unittest


class AppearanceInheritanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = Path(__file__).parents[1] / "characters.py"
        names = {"_appearance_description", "character_description_at"}
        functions = [
            node for node in ast.parse(source.read_text()).body
            if isinstance(node, ast.FunctionDef) and node.name in names
        ]
        namespace = {}
        exec(compile(ast.Module(body=functions, type_ignores=[]), str(source), "exec"), namespace)
        cls.resolve = staticmethod(namespace["character_description_at"])

    def test_chapter_starts_with_final_state_from_previous_chapter(self):
        character = {
            "description": "Red shirt",
            "description_timeline": [
                {"chapter": 3, "phrase": 0, "description": "Yellow shirt"},
                {"chapter": 2, "phrase": 23, "description": "Blue shirt"},
                {"chapter": 2, "phrase": 4, "description": "Green shirt"},
            ],
        }
        self.assertEqual(self.resolve(character, 3, -1), "Blue shirt")
        self.assertEqual(self.resolve(character, 3, 0), "Yellow shirt")

    def test_chapter_with_no_changes_carries_earlier_appearance_forward(self):
        character = {
            "description": "Red shirt",
            "description_timeline": [
                {"chapter": 1, "phrase": 19, "description": "Blue shirt"},
            ],
        }
        self.assertEqual(self.resolve(character, 3, -1), "Blue shirt")

    def test_no_previous_state_falls_back_to_book_description(self):
        character = {
            "description": "Red shirt",
            "description_timeline": [
                {"chapter": 3, "phrase": 8, "description": "Blue shirt"},
            ],
        }
        self.assertEqual(self.resolve(character, 1, -1), "Red shirt")
        self.assertEqual(self.resolve(character, 3, -1), "Red shirt")
        self.assertEqual(self.resolve(character, 3, 7), "Red shirt")
        self.assertEqual(self.resolve(character, 3, 8), "Blue shirt")

    def test_empty_or_failed_extraction_preserves_previous_state(self):
        for missing in (None, "", "  ", "No description found.", "no description found"):
            with self.subTest(missing=missing):
                character = {
                    "description": "Red shirt",
                    "description_timeline": [
                        {"chapter": 1, "phrase": 19, "description": "Blue shirt"},
                        {"chapter": 2, "phrase": 0, "description": missing},
                    ],
                }
                self.assertEqual(self.resolve(character, 3, -1), "Blue shirt")

    def test_failed_extraction_with_no_previous_state_uses_book_description(self):
        character = {
            "description": "Red shirt",
            "description_timeline": [
                {"chapter": 2, "phrase": 0, "description": "No description found."},
            ],
        }
        self.assertEqual(self.resolve(character, 3, -1), "Red shirt")

    def test_missing_book_description_can_still_inherit_a_previous_state(self):
        character = {
            "description": None,
            "description_timeline": [
                {"chapter": 2, "phrase": 23, "description": "Blue shirt"},
            ],
        }
        self.assertEqual(self.resolve(character, 3, -1), "Blue shirt")
        self.assertEqual(self.resolve(character, 1, -1), "")
