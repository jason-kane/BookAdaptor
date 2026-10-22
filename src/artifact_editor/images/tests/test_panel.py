"""Workshop controls must keep targeting their own expanded image panel."""
import importlib.util
from pathlib import Path
import unittest

from bs4 import BeautifulSoup

spec = importlib.util.spec_from_file_location('image_panel', Path(__file__).parents[1] / 'panel.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class PanelTests(unittest.TestCase):
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
