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
        for service in ('llama', 'comfy', 'python'):
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

    def test_python_always_takes_exclusive_lock_and_checks_free_gpu(self):
        client = Mock()
        client.get.return_value = 'python'
        client.lock.return_value.acquire.return_value = True
        client.lock.return_value.local.token = 'python-token'
        with patch.dict(os.environ, GPU_SERVICE_SWITCHING='1'), patch(
                'gpu_service._home_provider_status',
                return_value={'running_providers': []}), patch(
                'gpu_service._switch_with_task') as switch:
            with gpu_service.use_gpu_service(client, 'python'):
                pass
        client.lock.return_value.acquire.assert_called_once()
        switch.assert_not_called()
        client.lock.return_value.release.assert_called_once()

    def test_python_rpc_provider_and_cpu_tokenizer(self):
        from task_queue import task_provider
        for operation in ('sentences', 'whisper_ipa'):
            self.assertEqual(task_provider({'action': 'worker_rpc', 'operation': operation}), 'python')
        self.assertEqual(task_provider({'action': 'worker_rpc', 'operation': 't5_tokens'}), 'none')
        self.assertEqual(task_provider({'provider': 'python'}), 'python')

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
            client, 'comfy', 'llama', transition_task=None,
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


def test_recent_switch_does_not_delay_next_handoff():
    import time
    client = Mock()
    client.get.return_value = str(time.time())
    with patch('gpu_service._publish_switch_progress'), patch('gpu_service._switch') as switch, \
            patch('gpu_service.time.sleep') as sleep, \
            patch('styler.queue_history.publish_queue_event'):
        gpu_service._switch_with_task(client, 'llama', 'comfy')
    switch.assert_called_once()
    sleep.assert_not_called()
