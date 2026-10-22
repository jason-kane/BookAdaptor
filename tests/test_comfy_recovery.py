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
    def test_only_contradictory_detailed_oom_matches(self):
        self.assertTrue(recovery.contradictory_oom(ERROR))
        for message in ('CUDA out of memory', ERROR.replace('10.94 GiB', '0.94 GiB'),
                        ERROR.replace('0 bytes', '11 GiB'), ERROR.replace('1.18 GiB', '12 GiB'),
                        ERROR.replace('Requested', 'Unknown'), ERROR.replace('1.18 GiB', '0 bytes')):
            self.assertFalse(recovery.contradictory_oom(message), message)

    def test_restart_validates_target_and_obeys_shared_cooldown(self):
        with TemporaryDirectory() as root, patch.dict(sys.modules, {'const': SimpleNamespace(LIBRARY_DIR=root)}), patch.object(recovery.httpx, 'Client') as factory:
            client = factory.return_value.__enter__.return_value
            client.get.return_value.json.return_value = {'Config': {'Labels': {'com.docker.compose.service': 'comfyui'}}}
            self.assertIn('was restarted', recovery.restart_comfyui())
            client.post.assert_called_once_with('/containers/comfyui-megapak/restart', params={'t': 10})
            self.assertIn('suppressed', recovery.restart_comfyui())
            self.assertEqual(client.post.call_count, 1)

    def test_wrong_container_service_is_never_restarted(self):
        with TemporaryDirectory() as root, patch.dict(sys.modules, {'const': SimpleNamespace(LIBRARY_DIR=root)}), patch.object(recovery.httpx, 'Client') as factory:
            client = factory.return_value.__enter__.return_value
            client.get.return_value.json.return_value = {'Config': {'Labels': {'com.docker.compose.service': 'redis'}}}
            with self.assertRaisesRegex(RuntimeError, 'Refusing'):
                recovery.restart_comfyui()
            client.post.assert_not_called()


if __name__ == '__main__':
    unittest.main()
