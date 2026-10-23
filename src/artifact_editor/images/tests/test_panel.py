"""Workshop controls must keep targeting their own expanded image panel."""
import importlib.util
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from bs4 import BeautifulSoup

spec = importlib.util.spec_from_file_location('image_panel', Path(__file__).parents[1] / 'panel.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class PanelTests(unittest.TestCase):
    def test_regenerated_description_is_scoped_once_by_response_hook(self):
        image = BeautifulSoup('<image index="4"/>', 'xml').image
        chapter = SimpleNamespace(get_image=Mock(return_value=image), save_xml=Mock())
        images = SimpleNamespace(generate_image_description=Mock())
        markup = '<fieldset><wa-textarea id="4_description" value="New description"></wa-textarea></fieldset>'
        scope = {'g': SimpleNamespace(chapter=chapter), 'images': images,
                 'htmx': SimpleNamespace(description=Mock(return_value=markup))}
        path = Path(__file__).parents[1] / 'scene/views.py'
        function = next(node for node in ast.parse(path.read_text()).body
                        if isinstance(node, ast.FunctionDef) and node.name == 'regenerate_description')
        function.decorator_list = []
        exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), 'exec'), scope)
        body, status = scope['regenerate_description']('4')
        self.assertEqual(status, 200)
        images.generate_image_description.assert_called_once_with(image, force=True)
        chapter.save_xml.assert_called_once()
        panel = self.render(body)
        self.assertEqual(panel.find('wa-textarea')['id'], 'timeline-prompt-4-4_description')

    def render(self, markup, index=4):
        return BeautifulSoup(module.panel_controls(markup, index, f'/chapter/images/{index}/', f'/chapter/timeline/image/{index}'), 'html.parser')

    def test_relative_actions_and_targets_stay_with_image(self):
        markup = '<div id="setting-textarea"><wa-textarea hx-put="scene/save_setting" hx-target="#setting-textarea"></wa-textarea></div>'
        for index in (4, 7):
            panel = self.render(markup, index)
            self.assertEqual(panel.div['id'], f'timeline-prompt-{index}-setting-textarea')
            control = panel.find('wa-textarea')
            self.assertEqual(control['hx-put'], f'/chapter/images/{index}/scene/save_setting')
            self.assertEqual(control['hx-target'], '#' + panel.div['id'])

    def test_generation_refreshes_gallery_and_metadata_controls(self):
        panel = self.render('<wa-button id="workflow-run-button" hx-post="run" hx-swap="none"></wa-button><wa-button hx-post="generate_meta" hx-target="#strip-centerpiece"></wa-button>')
        for button, action in zip(panel.find_all('wa-button'), ('generate', 'metadata')):
            self.assertEqual(button['hx-post'], '/chapter/timeline/image/4')
            self.assertEqual(button['hx-target'], '#timeline-image-status-4')
            self.assertEqual(button['hx-swap'], 'innerHTML')
            self.assertIn(action, button['hx-vals'])

    def test_fragment_responses_keep_scoped_oob_and_labels(self):
        panel = self.render('<div id="camera" hx-swap-oob="true"><label for="camera-select">Camera</label><wa-select id="camera-select" hx-target="#camera"></wa-select></div>')
        self.assertEqual(panel.div['hx-swap-oob'], 'true')
        self.assertEqual(panel.label['for'], panel.find('wa-select')['id'])
        self.assertEqual(panel.find('wa-select')['hx-target'], '#' + panel.div['id'])


if __name__ == '__main__':
    unittest.main()
