"""Start optional services through the mounted Docker socket."""
import http.client
import json
import logging
import subprocess
import socket
from urllib.parse import urlencode, quote

log = logging.getLogger(__name__)

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
GPU_PROVIDER_SERVICES = {"comfyui": "ComfyUI", "llama": "Llama"}


def start_glances():
    start_program("glances")


def start_program(service_name):
    if service_name == "styler":
        start_services(PROGRAMS[service_name]["services"], ("artifactserver", "websocket"))
        return
    start_service(service_name, PROGRAMS)


def start_service(service_name, allowed_services):
    start_services((service_name,), allowed_services)


def start_services(service_names, allowed_services, *, recreate=True):
    if not service_names or any(name not in allowed_services for name in service_names):
        raise ValueError("Unknown service")
    command = compose_command()
    if "test" in service_names:
        command += ["--profile", "test"]
    # Resolve relative bind mounts against the HOST checkout, not /app in Home.
    # Compose owns commands, health checks, GPU settings, and dependency startup.
    startup = ["up", "-d", "--no-build"]
    if not recreate:
        # A worker startup request can originate inside one of these services.
        # Recreating it would kill the request that is starting its consumers.
        startup.append("--no-recreate")
    try:
        subprocess.run(command + startup + list(service_names), check=True, capture_output=True, text=True, timeout=120)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(exc.stderr or "Compose could not start the program") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Compose startup timed out; please retry") from exc


def gpu_provider_status():
    """Report which Compose-managed service currently owns the shared GPU."""
    running = [
        service
        for service in GPU_PROVIDER_SERVICES
        if any(
            container.get("State") == "running"
            for container in _program_containers(
                service, GPU_PROVIDER_SERVICES
            )[1]
        )
    ]
    return {
        "active_provider": running[0] if len(running) == 1 else None,
        "running_providers": running,
    }


def switch_gpu_provider(provider):
    """Stop the current GPU service and bring up the requested Compose service."""
    if not isinstance(provider, str) or provider not in GPU_PROVIDER_SERVICES:
        raise ValueError("GPU provider must be 'comfyui' or 'llama'")

    inactive_service = "llama" if provider == "comfyui" else "comfyui"
    base_command = compose_command() + ["--profile", "llama"]
    commands = [
        (
            base_command
            + ["stop", "--timeout", "20", inactive_service],
            f"stop {inactive_service}",
            60,
        ),
    ]
    start_command = compose_command()
    if provider == "llama":
        start_command += ["--profile", "llama"]
    commands.append(
        (
            start_command + ["up", "-d", "--no-build", "--no-deps", provider],
            f"start {provider}",
            480,
        )
    )

    for command, description, timeout in commands:
        log.info("GPU provider handoff: docker compose %s", description)
        try:
            result = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"Docker Compose timed out while trying to {description}"
            ) from exc
        output = "\n".join(
            value.strip()
            for value in (result.stdout, result.stderr)
            if value and value.strip()
        )
        for line in output.splitlines():
            log.info("Docker Compose: %s", line)
        if result.returncode:
            raise RuntimeError(
                f"Docker Compose failed to {description}: "
                f"{output or f'exit status {result.returncode}'}"
            )
    log.info("GPU provider handoff complete: %s is running", provider)


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
