"""Read current Compose data for the services represented by a Home panel."""
from datetime import datetime, timezone
import json
import subprocess

from .programs import compose_command

PANEL_SERVICES = {
    "library": ("artifactserver", "redis"),
    "theater": ("artifactserver", "redis"),
    "therapy": ("artifactserver", "redis"),
    "styles": ("artifactserver", "redis"),
    "glances": ("glances",),
    "comfyui": ("comfyui",),
    "tests": ("test", "redis", "nginx"),
    "workflows": ("artifactserver", "redis"),
}


TABLE_ROWS = (
    {"panel": "library", "name": "Library", "service": "artifactserver", "program": "artifactserver", "status": "Beta"},
    {"panel": "theater", "name": "Theater", "service": "artifactserver", "path": "/theater/", "port": 8082, "status": "Beta"},
    {"panel": "therapy", "name": "Therapy", "service": "artifactserver", "path": "/therapy/", "port": 8082, "status": "Broken"},
    {"panel": "styles", "name": "Styler", "service": "artifactserver", "program": "styler", "status": "Beta"},
    {"panel": "glances", "name": "Glances", "service": "glances", "program": "glances"},
    {"panel": "comfyui", "name": "Comfy UI", "service": "comfyui", "path": "/", "port": 8188},
    {"panel": "redis", "name": "Redis", "service": "redis"},
    {"panel": "redis", "name": "Redis Insight", "service": "redisinsight", "program": "redisinsight"},
    {"panel": "postgres", "name": "PostgreSQL", "service": "postgres"},
    {"panel": "postgres", "name": "pgAdmin 4", "service": "pgadmin", "program": "pgadmin"},
    {"panel": "tests", "name": "Test Coverage", "service": "test", "path": "/htmlcov/", "port": 80},
    {"panel": "workflows", "name": "Comfy Workflow Templates", "service": "artifactserver", "path": "/workflow_templates", "port": 8082},
)


def _records(output):
    """Compose versions emit either a JSON array or one JSON object per line."""
    if not output.strip():
        return []
    try:
        result = json.loads(output)
    except json.JSONDecodeError:
        return [json.loads(line) for line in output.splitlines() if line.strip()]
    return result if isinstance(result, list) else [result]


def _query(command, arguments):
    return _records(subprocess.run(
        command + arguments, check=True, capture_output=True, text=True, timeout=15,
    ).stdout)


def snapshot(services):
    command = compose_command() + ["--profile", "test"]
    containers = _query(command, ["ps", "--all", "--no-trunc", "--format", "json", *services])
    result = []
    for name in services:
        members = [container for container in containers if container.get("Service") == name]
        stats = []
        stats_error = None
        if any(container.get("State") == "running" for container in members):
            try:
                stats = _query(command, ["stats", "--no-stream", "--no-trunc", "--format", "json", name])
            except (OSError, ValueError, subprocess.SubprocessError):
                stats_error = "Resource usage is temporarily unavailable."
        result.append({"name": name, "containers": members, "stats": stats,
                       "stats_error": stats_error})
    return {"services": result, "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}


def image_version(image, metadata=None):
    """Prefer published version labels, then the image reference, then its ID."""
    metadata = metadata or {}
    labels = (metadata.get("Config") or {}).get("Labels") or {}
    repository = image.split("@", 1)[0].rsplit("/", 1)[-1].split(":", 1)[0].lower()
    title = str(labels.get("org.opencontainers.image.title", "")).lower()
    base_systems = {"ubuntu", "debian", "alpine", "rockylinux", "rocky linux", "centos", "fedora"}
    inherited_os_version = title in base_systems and repository != title
    for key in ("org.opencontainers.image.version", "org.label-schema.version", "version"):
        if labels.get(key) and not inherited_os_version:
            return str(labels[key])
    if "@sha256:" in image:
        return "sha256:" + image.split("@sha256:", 1)[1][:12]
    if image.startswith("sha256:"):
        return image[:19]
    if ":" in image.rsplit("/", 1)[-1]:
        return image.rsplit(":", 1)[1]
    if image:
        return "latest"
    return metadata.get("Id", "Version unavailable")[:19]


def panel_versions(panels, compose_file="/docker-compose.yaml"):
    import socket
    from urllib.parse import quote, urlencode
    import yaml
    from .programs import _docker

    try:
        with open(compose_file) as handle:
            configured = (yaml.safe_load(handle) or {}).get("services", {})
    except (OSError, ValueError, yaml.YAMLError):
        configured = {}
    containers = []
    docker_available = False
    try:
        app = _docker("GET", f"/containers/{quote(socket.gethostname(), safe='')}/json")
        project = app["Config"]["Labels"]["com.docker.compose.project"]
        filters = urlencode({"filters": json.dumps({"label": [
            f"com.docker.compose.project={project}", "com.docker.compose.oneoff=False",
        ]})})
        containers = _docker("GET", f"/containers/json?all=1&{filters}")
        docker_available = True
    except (OSError, RuntimeError, ValueError, KeyError):
        pass
    # Inspect each actual image once, even when multiple panels share it.
    images = {}
    versions = {}
    for services in panels.values():
        for service in services:
            if service in versions:
                continue
            matches = [container for container in containers
                       if container.get("Labels", {}).get("com.docker.compose.service") == service]
            if matches:
                matches.sort(key=lambda container: (container.get("State") == "running",
                                                    container.get("Created", 0)), reverse=True)
                container = matches[0]
                image = container.get("Image", "")
                reference = container.get("ImageID") or image
            else:
                image = configured.get(service, {}).get("image", "")
                reference = image
            if reference and reference not in images:
                try:
                    images[reference] = _docker("GET", f"/images/{quote(reference, safe='')}/json")
                except (OSError, RuntimeError, ValueError):
                    images[reference] = {}
            state = matches[0].get("State", "unknown") if matches else ("not created" if docker_available else "unavailable")
            ports = []
            for port in matches[0].get("Ports", []) if matches else []:
                target = f"{port.get('PrivatePort', '')}/{port.get('Type', 'tcp')}"
                if port.get("PublicPort"):
                    target = f"{port.get('IP', '')}:{port['PublicPort']} → {target}"
                ports.append(target)
            versions[service] = {"service": service, "image": image,
                                 "version": image_version(image, images.get(reference)),
                                 "state": state, "ports": ", ".join(ports)}
    return {panel: [versions[service] for service in services] for panel, services in panels.items()}
