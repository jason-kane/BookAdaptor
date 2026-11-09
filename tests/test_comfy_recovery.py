import importlib.util
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import MagicMock, patch

spec = importlib.util.spec_from_file_location('comfy_recovery', Path(__file__).parents[1] / 'src/comfy_recovery.py')
recovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recovery)

ERROR = '''Allocation on device 0 would exceed allowed memory. (out of memory)
Currently allocated : 0 bytes
Requested : 1.18 GiB
Device limit : 11.63 GiB
Free (according to CUDA): 10.94 GiB'''


class RecoveryTests(unittest.TestCase):
    def test_real_vram_ooms_match_without_recovering_cpu_or_other_errors(self):
        for message in (ERROR, 'CUDA out of memory', ERROR.replace('10.94 GiB', '0.06 GiB')):
            self.assertTrue(recovery.is_vram_oom(message), message)
        for message in ('DefaultCPUAllocator: out of memory', 'Invalid workflow', 'execution interrupted'):
            self.assertFalse(recovery.is_vram_oom(message), message)

    def test_restart_validates_target_and_obeys_shared_cooldown(self):
        with TemporaryDirectory() as root, patch.dict(sys.modules, {'const': SimpleNamespace(LIBRARY_DIR=root)}), patch.object(recovery.httpx, 'Client') as factory:
            client = factory.return_value.__enter__.return_value
            client.get.return_value.json.return_value = {'Config': {'Labels': {'com.docker.compose.service': 'comfyui'}}}
            self.assertIn('was restarted', recovery.restart_comfyui())
            self.assertEqual(client.post.call_args_list, [
                unittest.mock.call('/containers/comfyui-megapak/stop', params={'t': 10}),
                unittest.mock.call('/containers/comfyui-megapak/start'),
            ])
            with self.assertRaisesRegex(RuntimeError, 'suppressed'):
                recovery.restart_comfyui()
            self.assertEqual(client.post.call_count, 2)

    def test_wrong_container_service_is_never_restarted(self):
        with TemporaryDirectory() as root, patch.dict(sys.modules, {'const': SimpleNamespace(LIBRARY_DIR=root)}), patch.object(recovery.httpx, 'Client') as factory:
            client = factory.return_value.__enter__.return_value
            client.get.return_value.json.return_value = {'Config': {'Labels': {'com.docker.compose.service': 'redis'}}}
            with self.assertRaisesRegex(RuntimeError, 'Refusing'):
                recovery.restart_comfyui()
            client.post.assert_not_called()


if __name__ == '__main__':
    unittest.main()
