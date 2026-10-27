"""GPU provider admission without live Redis, Home, or container operations."""
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import gpu_service


class GPUServiceAdmissionTests(unittest.TestCase):
    def test_active_provider_requests_do_not_acquire_another_workload_lock(self):
        for service in ('llama', 'comfy'):
            for stored in (service, service.encode()):
                with self.subTest(service=service, stored=stored):
                    client = Mock()
                    client.get.return_value = stored
                    client.exists.return_value = False
                    with patch.dict(os.environ, GPU_SERVICE_SWITCHING='1'), patch(
                            'gpu_service._switch_with_task') as switch:
                        with gpu_service.use_gpu_service(client, service):
                            self.assertEqual(gpu_service._active_service.get(), service)
                    client.lock.return_value.acquire.assert_not_called()
                    switch.assert_not_called()
                    self.assertIsNone(gpu_service._active_service.get())

    def test_active_provider_exception_restores_local_context(self):
        client = Mock()
        client.get.return_value = 'llama'
        client.exists.return_value = False
        with patch.dict(os.environ, GPU_SERVICE_SWITCHING='1'):
            with self.assertRaisesRegex(ValueError, 'request failed'):
                with gpu_service.use_gpu_service(client, 'llama'):
                    raise ValueError('request failed')
        self.assertIsNone(gpu_service._active_service.get())
        client.lock.return_value.release.assert_not_called()

    def test_provider_handoff_still_acquires_exclusive_lock(self):
        client = Mock()
        client.get.return_value = 'comfy'
        client.exists.return_value = False
        client.lock.return_value.acquire.return_value = True
        client.lock.return_value.local.token = 'test-token'
        with patch.dict(os.environ, GPU_SERVICE_SWITCHING='1'), patch(
                'gpu_service._switch_with_task') as switch:
            with gpu_service.use_gpu_service(client, 'llama'):
                pass
        client.lock.return_value.acquire.assert_called_once()
        switch.assert_called_once_with(
            client, 'comfy', 'llama', override_cooldown=False, transition_task=None,
        )
        client.lock.return_value.release.assert_called_once()

    def test_transition_in_progress_prevents_active_provider_fast_path(self):
        client = Mock()
        client.get.return_value = 'llama'
        client.exists.return_value = True
        client.lock.return_value.acquire.return_value = True
        client.lock.return_value.local.token = 'test-token'
        with patch.dict(os.environ, GPU_SERVICE_SWITCHING='1'), patch(
                'gpu_service._home_provider_status',
                return_value={'running_providers': ['llama']}), patch(
                'gpu_service._wait_until_ready'):
            with gpu_service.use_gpu_service(client, 'llama'):
                pass
        client.lock.return_value.acquire.assert_called_once()


if __name__ == '__main__':
    unittest.main()
