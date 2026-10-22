"""Scene character updates must preserve the activity tabs for remaining characters."""
import ast
import logging
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from bs4 import BeautifulSoup
from flask import Flask, g, request


def load_functions(filename, names, namespace):
    path = Path(__file__).parents[1] / filename
    tree = ast.parse(path.read_text())
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    for node in nodes:
        node.decorator_list = []
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), namespace)
    return SimpleNamespace(**{name: namespace[name] for name in names})


class SceneCharacterTests(unittest.TestCase):
    def test_props_are_with_scene_controls_before_character_tabs(self):
        namespace = {
            'url_for': lambda *args, **kwargs: '/style',
            'os': __import__('os'),
            'styles_htmx': SimpleNamespace(style_selector=lambda **kwargs: ''),
            'generic_button': lambda *args, **kwargs: '',
            'characters_section': lambda *args: '<div id="characters_section"><wa-tab-group><wa-tab-panel name="alice">Alice</wa-tab-panel></wa-tab-group></div>',
            **{name: (lambda *args, _name=name: f'<div id="{_name}"></div>')
               for name in ('description', 'setting', 'tod', 'mood', 'camera_direction',
                            'camera_lighting', 'focus_character', 'scene_props')},
        }
        ui = load_functions('htmx.py', {'image_scene_workshop'}, namespace)
        chapter = SimpleNamespace(get_image_style=lambda image: '', kwargs={}, url='/chapter')
        image = BeautifulSoup('<image index="0"/>', 'xml').image
        rendered = BeautifulSoup(ui.image_scene_workshop(chapter, image), 'html.parser')
        props = rendered.select_one('#scene_props')
        self.assertEqual(props.find_previous_sibling()['id'], 'setting')
        self.assertEqual(props.find_next_sibling()['id'], 'tod')
        self.assertIsNone(props.find_parent('wa-tab-panel'))
        self.assertIsNone(props.find_parent(id='characters_section'))

    def test_remove_one_then_all_characters_refreshes_full_section(self):
        soup = BeautifulSoup('<image index="0" scene_characters="Alice,Bob"/>', 'xml')
        image = soup.image
        catalog = {name: {'name': name, 'tag': name} for name in ('Alice', 'Bob')}
        namespace = {
            'characters': SimpleNamespace(get_all_characters=lambda chapter: catalog),
            'character_breakdown': lambda chapter, image: ''.join(
                f'<wa-tab>{name}</wa-tab>' for name in image['scene_characters'].split(',') if name
            ),
        }
        ui = load_functions('htmx.py', {'scene_characters', 'characters_section'}, namespace)
        chapter = SimpleNamespace(get_image=lambda index: image, save_xml=Mock())
        views = load_functions('views.py', {'save_scene_characters', 'generate_scene_characters'}, {
            'g': g, 'request': request, 'log': logging.getLogger(__name__), 'htmx': ui,
            'scene': SimpleNamespace(generate_scene_characters=lambda chapter, image: ['Bob']),
        })
        app = Flask(__name__)
        for selected in (['Bob'], []):
            with self.subTest(selected=selected), app.test_request_context('/', method='PUT', data={'scene_characters': selected}):
                g.chapter = chapter
                rendered = BeautifulSoup(views.save_scene_characters(), 'html.parser')
                self.assertEqual(image['scene_characters'], ','.join(selected))
                self.assertIsNotNone(rendered.select_one('#characters_section'))
                self.assertEqual([tab.text for tab in rendered.select('wa-tab')], selected)
                self.assertEqual([option['value'] for option in rendered.select('wa-option[selected]')], selected)
                for control in rendered.select('[hx-target]'):
                    self.assertEqual(control['hx-target'], 'closest #characters_section')
        self.assertEqual(chapter.save_xml.call_count, 2)
        with app.test_request_context('/', method='POST'):
            g.chapter = chapter
            rendered = BeautifulSoup(views.generate_scene_characters(), 'html.parser')
            self.assertIsNotNone(rendered.select_one('#characters_section'))
            self.assertEqual([tab.text for tab in rendered.select('wa-tab')], ['Bob'])


if __name__ == '__main__':
    unittest.main()
