"""Start optional services through the mounted Docker socket."""
import http.client
import json
import subprocess
import socket
from urllib.parse import urlencode, quote


class DockerConnection(http.client.HTTPConnection):
    def __init__(self):
        super().__init__("localhost", timeout=10)

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect("/var/run/docker.sock")


def _docker(method, path, body=None, allowed=(200,)):
    connection = DockerConnection()
    try:
        connection.request(method, path, body=json.dumps(body) if body is not None else None,
                           headers={"Content-Type": "application/json"})
        response = connection.getresponse()
        data = response.read()
        if response.status not in allowed:
            raise RuntimeError(f"Docker {method} {path}: {response.status} {data.decode()}")
        return json.loads(data) if data else None
    except http.client.HTTPException as exc:
        raise RuntimeError("Unable to communicate with Docker") from exc
    finally:
        connection.close()


PROGRAMS = {
    "comfyui": {"name": "Comfy UI", "port": 8188, "timeout": 300, "image": "comfyui.png"},
    "artifactserver": {"name": "Library", "port": 8082, "internal_port": 8080,
                       "path": "/library/", "timeout": 300, "image": "library.png"},
    "styler": {"name": "Styler", "port": 8082, "internal_port": 8080,
               "path": "/styles/", "timeout": 300, "image": "styler.png",
               "services": ("artifactserver", "websocket"),
               "ready_service": "artifactserver"},
    "glances": {"name": "Glances", "port": 61208, "image": "glances.png"},
    "redisinsight": {"name": "Redis Insight", "port": 5540, "image": "redis_insight.png"},
}


def start_glances():
    start_program("glances")


def start_program(service_name):
    if service_name == "styler":
        start_services(PROGRAMS[service_name]["services"], ("artifactserver", "websocket"))
        return
    start_service(service_name, PROGRAMS)


def start_service(service_name, allowed_services):
    start_services((service_name,), allowed_services)


def start_services(service_names, allowed_services):
    if not service_names or any(name not in allowed_services for name in service_names):
        raise ValueError("Unknown service")
    command = compose_command()
    if "test" in service_names:
        command += ["--profile", "test"]
    # Resolve relative bind mounts against the HOST checkout, not /app in Home.
    # Compose owns commands, health checks, GPU settings, and dependency startup.
    try:
        subprocess.run(command + ["up", "-d", "--no-build", *service_names], check=True, capture_output=True, text=True, timeout=120)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(exc.stderr or "Compose could not start the program") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Compose startup timed out; please retry") from exc


def compose_command():
    """Use this Home container's Compose identity for launches and inspection."""
    app = _docker("GET", f"/containers/{quote(socket.gethostname(), safe='')}/json")
    labels = app["Config"]["Labels"]
    return [
        "docker", "compose", "--file", "/docker-compose.yaml",
        "--project-directory", labels["com.docker.compose.project.working_dir"],
        "--project-name", labels["com.docker.compose.project"],
        "--profile", "on-demand",
    ]


def _program_containers(service_name, allowed_services=PROGRAMS):
    if service_name not in allowed_services:
        raise ValueError("Unknown program")
    # Read the running app's Compose identity rather than assuming a project name
    # or using container paths as host bind-mount paths.
    app = _docker("GET", f"/containers/{quote(socket.gethostname(), safe='')}/json")
    labels = app["Config"]["Labels"]
    project = labels["com.docker.compose.project"]
    filters = urlencode({"filters": json.dumps({"label": [
        f"com.docker.compose.project={project}", f"com.docker.compose.service={service_name}",
        "com.docker.compose.oneoff=False",
    ]})})
    containers = _docker("GET", f"/containers/json?all=1&{filters}")
    return app, containers


def program_running(service_name):
    _, containers = _program_containers(service_name)
    return any(container["State"] == "running" for container in containers)


def stop_program(service_name):
    stop_service(service_name, PROGRAMS)


def stop_service(service_name, allowed_services):
    _, containers = _program_containers(service_name, allowed_services)
    for container in containers:
        # A short grace period keeps stop requests bounded by the socket timeout.
        _docker("POST", f"/containers/{container['Id']}/stop?t=5", allowed=(204, 304))


def glances_ready():
    return program_ready("glances")


def program_ready(service_name):
    program = PROGRAMS[service_name]
    ready_service = program.get("ready_service", service_name)
    connection = http.client.HTTPConnection(
        ready_service, program.get("internal_port", program["port"]),
        timeout=10 if ready_service == "artifactserver" else 1,
    )
    try:
        connection.request("GET", program.get("path", "/"))
        response = connection.getresponse()
        return response.status == 200
    except (OSError, http.client.HTTPException):
        return False
    finally:
        connection.close()
