"""Route workflow links through Home's on-demand launcher when ComfyUI is down."""
from urllib.parse import urlencode, urlsplit, urlunsplit

import httpx


def workflow_destination(workflow, api_url, ui_url, request_origin, home_url=None):
    try:
        ready = httpx.get(api_url.rstrip('/') + '/system_stats', timeout=2).status_code == 200
    except httpx.HTTPError:
        ready = False
    if ready:
        parts = urlsplit(ui_url)
        return urlunsplit(parts._replace(query=urlencode({'workflow': workflow}), fragment=''))
    if not home_url:
        origin = urlsplit(request_origin)
        hostname = origin.hostname
        if ':' in hostname:
            hostname = f'[{hostname}]'
        home_url = urlunsplit((origin.scheme, f'{hostname}:8080', '/', '', ''))
    return home_url.rstrip('/') + '/programs/comfyui?' + urlencode({'workflow': workflow})
