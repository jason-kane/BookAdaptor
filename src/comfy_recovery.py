"""Release ComfyUI's CUDA process after VRAM OOMs without restart loops."""
import fcntl
import os
from pathlib import Path
import re
import time

import httpx


class VRAMOutOfMemoryError(RuntimeError):
    """A failed ComfyUI execution exhausted GPU memory."""


def is_vram_oom(message, exception_type=''):
    text = str(message).casefold()
    return ('out of memory' in text or 'outofmemoryerror' in text) and (
        'cuda' in text or 'allocation on device' in text
        or 'outofmemoryerror' in str(exception_type).casefold()
    )


def wait_for_comfyui(timeout_seconds=300):
    """Wait for the restarted process to finish loading and serve requests."""
    import const

    deadline = time.monotonic() + timeout_seconds
    with httpx.Client(timeout=httpx.Timeout(5, connect=2)) as client:
        while time.monotonic() < deadline:
            try:
                response = client.get(const.COMFYUI_API_URL.rstrip('/') + '/system_stats')
                if response.status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(1)
    raise TimeoutError(f'ComfyUI did not become ready within {timeout_seconds} seconds after OOM recovery.')


def restart_comfyui():
    """Restart only the configured Compose comfyui container, at most once per 5 minutes."""
    import const

    lock_path = Path(const.LIBRARY_DIR) / '.comfyui-recovery.lock'
    with lock_path.open('a+') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('ComfyUI recovery is already in progress.')
        lock.seek(0)
        previous = lock.read().strip()
        if previous and time.time() - float(previous) < 300:
            raise RuntimeError('ComfyUI recovery was recently attempted; restart suppressed for 5 minutes.')
        container = os.environ.get('COMFYUI_CONTAINER_NAME', 'comfyui-megapak')
        # Container names are a path component, never shell input.
        if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]*', container):
            raise ValueError('Invalid COMFYUI_CONTAINER_NAME')
        transport = httpx.HTTPTransport(uds='/var/run/docker.sock')
        with httpx.Client(transport=transport, base_url='http://docker', timeout=45) as client:
            inspect = client.get(f'/containers/{container}/json')
            inspect.raise_for_status()
            labels = inspect.json().get('Config', {}).get('Labels', {})
            if labels.get('com.docker.compose.service') != 'comfyui':
                raise RuntimeError('Refusing to restart a container outside the comfyui Compose service')
            # Record the attempt before restarting, preventing loops even on API timeouts.
            lock.seek(0)
            lock.truncate()
            lock.write(str(time.time()))
            lock.flush()
            # Stop completes only after the old process exits and releases its CUDA context.
            response = client.post(f'/containers/{container}/stop', params={'t': 10})
            response.raise_for_status()
            response = client.post(f'/containers/{container}/start')
            response.raise_for_status()
        return 'ComfyUI was restarted with a full process shutdown to clear VRAM.'
