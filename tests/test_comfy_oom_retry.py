"""OOM recovery retries only after shutdown and readiness, without running inference."""
import ast
from contextvars import ContextVar
from functools import wraps
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
import comfy_recovery


class OOMRetryTests(unittest.TestCase):
    def setUp(self):
        source = ast.parse((ROOT / 'src/comfy.py').read_text())
        wrapper = next(node for node in source.body
                       if isinstance(node, ast.FunctionDef) and node.name == '_retry_after_vram_oom')
        self.progress = Mock()
        progress = ContextVar('progress', default=self.progress)
        namespace = dict(wraps=wraps, workflow_progress=progress, log=Mock())
        exec(compile(ast.Module(body=[wrapper], type_ignores=[]), '<oom-retry>', 'exec'), namespace)
        self.wrap = namespace['_retry_after_vram_oom']

    def test_success_and_other_failures_never_restart(self):
        with patch.object(comfy_recovery, 'restart_comfyui') as restart:
            self.assertEqual(self.wrap(Mock(return_value=['video.mp4']))(), ['video.mp4'])
            with self.assertRaisesRegex(RuntimeError, 'invalid workflow'):
                self.wrap(Mock(side_effect=RuntimeError('invalid workflow')))()
            restart.assert_not_called()

    def test_restart_and_readiness_precede_unchanged_retry(self):
        order = []
        request = {'workflow': {'1': {'class_type': 'Sampler'}}}
        def execute(**kwargs):
            self.assertEqual(kwargs, request)
            order.append('execute')
            if order.count('execute') == 1:
                raise comfy_recovery.VRAMOutOfMemoryError('CUDA out of memory')
            return ['video.mp4']
        with patch.object(comfy_recovery, 'restart_comfyui', side_effect=lambda: order.append('restart')), \
                patch.object(comfy_recovery, 'wait_for_comfyui', side_effect=lambda: order.append('ready')):
            self.assertEqual(self.wrap(execute)(**request), ['video.mp4'])
        self.assertEqual(order, ['execute', 'restart', 'ready', 'execute'])

    def test_second_oom_does_not_restart_again(self):
        execute = Mock(side_effect=comfy_recovery.VRAMOutOfMemoryError('CUDA out of memory'))
        with patch.object(comfy_recovery, 'restart_comfyui') as restart, \
                patch.object(comfy_recovery, 'wait_for_comfyui'):
            with self.assertRaises(comfy_recovery.VRAMOutOfMemoryError):
                self.wrap(execute)()
            restart.assert_called_once()
        self.assertEqual(execute.call_count, 2)

    def test_failed_recovery_does_not_resubmit(self):
        for step in ('restart_comfyui', 'wait_for_comfyui'):
            with self.subTest(step=step), \
                    patch.object(comfy_recovery, 'restart_comfyui'), \
                    patch.object(comfy_recovery, 'wait_for_comfyui'):
                execute = Mock(side_effect=comfy_recovery.VRAMOutOfMemoryError('CUDA out of memory'))
                with patch.object(comfy_recovery, step, side_effect=RuntimeError('recovery unavailable')):
                    with self.assertRaisesRegex(RuntimeError, 'CUDA out of memory.*\n.*recovery unavailable'):
                        self.wrap(execute)()
                execute.assert_called_once()
