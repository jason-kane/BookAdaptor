"""Start optional job consumers through the independent Home service."""
import os
import time

import httpx


def ensure_worker_services(*, comfyui=False, cpu_only=False):
    home_url = os.environ.get('HOME_INTERNAL_URL', 'http://home:8080').rstrip('/')
    try:
        payload = {'cpu_only': True} if cpu_only else {'comfyui': comfyui}
        response = httpx.post(home_url + '/programs/workers/start', json=payload, timeout=130)
        response.raise_for_status()
        if comfyui and not cpu_only:
            deadline = time.monotonic() + 300
            while time.monotonic() < deadline:
                status = httpx.get(home_url + '/programs/comfyui/status', timeout=10)
                status.raise_for_status()
                if status.json().get('ready') is True:
                    return
                time.sleep(1)
            raise RuntimeError('ComfyUI is still starting. Please try again shortly.')
    except httpx.HTTPError as exc:
        raise RuntimeError('Unable to start generation services. Please try again.') from exc
