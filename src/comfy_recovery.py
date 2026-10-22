"""Recover contradictory CUDA allocation failures without restart loops."""
import fcntl
import os
from pathlib import Path
import re
import time

import httpx


def contradictory_oom(message):
    """Require explicit allocator statistics and substantial reported headroom."""
    if not isinstance(message, str) or 'out of memory' not in message.lower():
        return False
    if 'exceed allowed memory' not in message.lower():
        return False
    def amount(label):
        match = re.search(label + r'\s*:\s*([\d.]+)\s*(bytes?|[KMGT]i?B)\b', message, re.I)
        if not match:
            return None
        powers = {'b': 0, 'byte': 0, 'bytes': 0, 'kb': 1, 'mb': 2, 'gb': 3, 'tb': 4,
                  'kib': 1, 'mib': 2, 'gib': 3, 'tib': 4}
        unit = match[2].lower()
        return float(match[1]) * (1024 if 'i' in unit else 1000) ** powers[unit]
    allocated, requested, limit, free = (
        amount(label) for label in ('Currently allocated', 'Requested', 'Device limit', r'Free \(according to CUDA\)'))
    return (all(value is not None for value in (allocated, requested, limit, free))
            and requested > 0 and limit > 0 and free >= requested * 1.25
            and allocated + requested <= limit * 0.9)


def restart_comfyui():
    """Restart only the configured Compose comfyui container, at most once per 5 minutes."""
    import const

    lock_path = Path(const.LIBRARY_DIR) / '.comfyui-recovery.lock'
    with lock_path.open('a+') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 'ComfyUI recovery is already in progress.'
        lock.seek(0)
        previous = lock.read().strip()
        if previous and time.time() - float(previous) < 300:
            return 'ComfyUI recovery was recently attempted; restart suppressed for 5 minutes.'
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
            response = client.post(f'/containers/{container}/restart', params={'t': 10})
            response.raise_for_status()
        return 'ComfyUI was restarted after an inconsistent CUDA OOM. Retry once ComfyUI is ready.'
