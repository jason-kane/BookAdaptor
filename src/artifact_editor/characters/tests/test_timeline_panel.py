"""Render the real chapter timeline template without loading model services."""
from pathlib import Path
from types import SimpleNamespace
import unittest

from bs4 import BeautifulSoup
from flask import Flask, render_template


class CharacterTimelinePanelTests(unittest.TestCase):
    def setUp(self):
        templates = Path(__file__).parents[1] / "templates"
        self.app = Flask(__name__, template_folder=str(templates))
        prefix = "/<author>/<title>/<int:chapter_number>/<language>/characters/<character_tag>/timeline"
        for action, suffix in (
            ("save_character_timeline_event", ""),
            ("extract_character_timeline", "/extract"),
            ("delete_character_timeline_event", "/<int:phrase_index>"),
        ):
            self.app.add_url_rule(
                prefix + suffix,
                endpoint="library.book.chapter.characters." + action,
                view_func=lambda **kwargs: "",
            )
        self.chapter = SimpleNamespace(
            number=3,
            kwargs=dict(author="Author", title="Book", chapter_number=3, language="english"),
        )

    def render_panel(self, characters, starting_descriptions):
        with self.app.test_request_context():
            html = render_template(
                "character_timeline_panel.html",
                chapter=self.chapter,
                characters=characters,
                starting_descriptions=starting_descriptions,
            )
        return BeautifulSoup(html, "html.parser")

    def test_renders_character_and_current_chapter_changes_with_action_urls(self):
        panel = self.render_panel({
            "Tip": {
                "name": "Tip",
                "description_timeline": [
                    {"chapter": 2, "phrase": 7, "description": "Red shirt"},
                    {"chapter": 3, "phrase": 14, "description": "Blue shirt",
                     "evidence": "He put on the blue shirt."},
                ],
            },
        }, {"Tip": "Red shirt"})

        self.assertIn("Tip", panel.summary.get_text())
        self.assertIn("Starting appearance in this chapter: Red shirt", panel.get_text(" ", strip=True))
        history = panel.select_one(".character-appearance-history")
        self.assertIn("Appearance change history (2)", history.summary.get_text())
        self.assertIn("Chapter 2, phrase 7: Red shirt", history.get_text(" ", strip=True))
        self.assertIn("Chapter 3, phrase 14: Blue shirt", history.get_text(" ", strip=True))
        self.assertIsNone(panel.select_one(".character-appearance-status"))
        self.assertEqual(
            panel.select_one('[hx-post]')["hx-post"],
            "/Author/Book/3/english/characters/Tip/timeline/extract",
        )
        self.assertEqual(panel.select_one('wa-input[value]')["value"], "14")
        self.assertEqual(panel.select_one('wa-textarea[name="description"][value]')["value"], "Blue shirt")
        self.assertEqual(
            panel.select_one('[hx-delete]')["hx-delete"],
            "/Author/Book/3/english/characters/Tip/timeline/14",
        )
        self.assertEqual(len(panel.select('form[hx-put]')), 2)

    def test_empty_catalog_shows_explanation(self):
        panel = self.render_panel({}, {})
        self.assertIn("No book characters yet", panel.get_text())
        self.assertIsNone(panel.summary)

    def test_empty_chapter_status_distinguishes_scanned_from_unscanned(self):
        panel = self.render_panel({
            "Tip": {"name": "Tip", "description_timeline_scanned": [3]},
            "Jack": {"name": "Jack Pumpkinhead"},
        }, {})

        self.assertIn(
            "No appearance changes were found in this chapter.",
            panel.select_one(".character-appearance-status").get_text(" ", strip=True),
        )
        self.assertIn(
            "Appearance changes have not been scanned for this chapter yet.",
            panel.select(".character-appearance-status")[1].get_text(" ", strip=True),
        )
