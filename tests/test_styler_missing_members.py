import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock

PATH = Path(__file__).resolve().parents[1] / 'src/styler/styles.py'


class MissingMemberTests(unittest.TestCase):
    def test_missing_and_concurrently_deleted_styles_do_not_break_category(self):
        method = next(node for node in ast.walk(ast.parse(PATH.read_text()))
                      if isinstance(node, ast.FunctionDef) and node.name == 'with_tag')
        redis = MagicMock()
        redis.smembers.return_value = [b'valid', b'verbing', b'deleted', b'also_valid']
        redis.pipeline.return_value.__enter__.return_value.execute.return_value = ['hash','none','hash','hash']
        def style(member):
            if member == 'deleted':raise ValueError('Style deleted not found')
            return member
        namespace = dict(r=redis, Style=Mock(side_effect=style), check_upgrade=Mock(),
                         as_id=lambda value:value, log=Mock())
        exec(compile(ast.Module(body=[method],type_ignores=[]),str(PATH),'exec'),namespace)
        self.assertEqual(list(namespace['with_tag'](SimpleNamespace(),'category')),['valid','also_valid'])
        self.assertEqual(redis.eval.call_count,2)
        self.assertNotIn(('verbing',),[call.args for call in namespace['Style'].call_args_list])
