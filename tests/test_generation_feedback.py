import ast
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).parents[1] / 'src/artifact_editor'
spec = importlib.util.spec_from_file_location('generation_feedback', ROOT / 'generation_feedback.py')
feedback = importlib.util.module_from_spec(spec)
spec.loader.exec_module(feedback)


class FeedbackTests(unittest.TestCase):
    def test_recovery_states_have_actionable_messages(self):
        cases = [('ComfyUI was restarted', 'then try again'),
                 ('ComfyUI recovery is already in progress', 'already in progress'),
                 ('restart suppressed', 'five minutes'),
                 ('ComfyUI restart failed', 'could not be restarted'),
                 ('CUDA out of memory', 'other GPU jobs')]
        for detail, expected in cases:
            self.assertIn(expected, feedback.generation_failure_message(RuntimeError(detail)))
        self.assertIn('timed out', feedback.generation_failure_message(TimeoutError()))

    def test_prop_extraction_returns_visible_error_fragment(self):
        tree = ast.parse((ROOT / 'props/views.py').read_text())
        node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'extract')
        node.decorator_list = []
        panel = Mock(return_value='visible panel')
        namespace = {'props': SimpleNamespace(extract_props=Mock(side_effect=RuntimeError(
            'CUDA out of memory\nComfyUI was restarted'))), 'g': SimpleNamespace(chapter=None),
            'panel': panel, 'generation_failure_message': feedback.generation_failure_message}
        exec(compile(ast.Module(body=[node], type_ignores=[]), 'views.py', 'exec'), namespace)
        self.assertEqual(namespace['extract'](), 'visible panel')
        self.assertIn('ComfyUI was restarted', panel.call_args.args[0])


if __name__ == '__main__':
    unittest.main()
