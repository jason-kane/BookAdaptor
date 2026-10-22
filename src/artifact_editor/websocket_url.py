"""Browser-facing address of the separately published WebSocket service."""

from urllib.parse import urlsplit, urlunsplit

from flask import request


def websocket_url():
    host = urlsplit(request.host_url).hostname
    # Preserve brackets for IPv6 addresses when attaching the service port.
    if ":" in host:
        host = f"[{host}]"
    scheme = "wss" if request.is_secure else "ws"
    return urlunsplit((scheme, f"{host}:5001", "/ws", "", ""))
