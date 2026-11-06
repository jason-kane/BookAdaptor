"""Start optional services through the mounted Docker socket."""
import http.client
import json
import logging
import subprocess
import socket
import threading
import time
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
                       "services": ("artifactserver", "websocket"),
                       "path": "/library/", "timeout": 300, "image": "library.png"},
    "styler": {"name": "Styler", "port": 8082, "internal_port": 8080,
               "path": "/styles/", "timeout": 300, "image": "styler.png",
               "services": ("artifactserver", "websocket"),
               "ready_service": "artifactserver"},
    "glances": {"name": "Glances", "port": 61208, "image": "glances.png"},
    "redisinsight": {"name": "Redis Insight", "port": 5540, "image": "redis_insight.png"},
    "pgadmin": {"name": "pgAdmin 4", "port": 5050, "internal_port": 80,
                "timeout": 180, "image": "postgres.svg", "ready_path": "/misc/ping"},
}
GPU_PROVIDER_SERVICES = {"comfyui": "ComfyUI", "llama": "Llama"}
NVML_RECOVERY_SERVICES = {"websocket", "video_worker"}
_nvml_recovery_lock = threading.Lock()
_nvml_recovery_requested = {}
_NVML_RECOVERY_COOLDOWN_SECONDS = 120


def start_glances():
    start_program("glances")


def start_program(service_name):
    if service_name in ("artifactserver", "styler"):
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


def switch_gpu_provider(provider, *, stop_sibling=False):
    """Switch GPU providers, optionally stopping the sibling as a recovery."""
    if not isinstance(provider, str) or provider not in (*GPU_PROVIDER_SERVICES, "training", "python"):
        raise ValueError("GPU provider must be 'comfyui', 'llama', 'training', or 'python'")

    base_command = compose_command() + ["--profile", "llama"]
    if provider in ("training", "python"):
        for service in ("comfyui", "llama"):
            command = base_command + ["stop", "--timeout", "20", service]
            result = subprocess.run(command, check=False, capture_output=True, text=True, timeout=60)
            output = "\n".join(value.strip() for value in (result.stdout, result.stderr) if value and value.strip())
            if result.returncode:
                raise RuntimeError(f"Docker Compose failed to stop {service}: {output or f'exit status {result.returncode}'}")
        return

    commands = []
    if stop_sibling:
        inactive_service = "llama" if provider == "comfyui" else "comfyui"
        commands.append((
            base_command + ["stop", "--timeout", "20", inactive_service],
            f"stop {inactive_service}",
            60,
        ))
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


def request_nvml_recovery(service_name):
    """Restart a GPU-aware container when it reports stale NVML access."""
    if service_name not in NVML_RECOVERY_SERVICES:
        raise ValueError("NVML recovery is not enabled for this service")

    now = time.monotonic()
    with _nvml_recovery_lock:
        if now - _nvml_recovery_requested.get(service_name, float("-inf")) < _NVML_RECOVERY_COOLDOWN_SECONDS:
            return False
        _nvml_recovery_requested[service_name] = now

    def restart_after_response():
        time.sleep(2)
        command = compose_command() + ["restart", "--timeout", "10", service_name]
        try:
            subprocess.run(command, check=True, capture_output=True, text=True, timeout=60)
            log.warning("Restarted %s after it reported stale NVML access", service_name)
        except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
            log.exception("Unable to restart %s after stale NVML access: %s", service_name, exc)
            with _nvml_recovery_lock:
                _nvml_recovery_requested.pop(service_name, None)

    threading.Thread(target=restart_after_response, name=f"nvml-recovery-{service_name}", daemon=True).start()
    log.warning("Scheduled restart of %s after stale NVML access", service_name)
    return True


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
        connection.request("GET", program.get("ready_path", program.get("path", "/")))
        response = connection.getresponse()
        return response.status == 200
    except (OSError, http.client.HTTPException):
        return False
    finally:
        connection.close()
