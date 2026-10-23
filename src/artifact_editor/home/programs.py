"""Start optional services through the mounted Docker socket."""
import http.client
import json
import os
from pathlib import Path
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
    "glances": {"name": "Glances", "port": 61208},
    "redisinsight": {"name": "Redis Insight", "port": 5540},
}


def start_glances():
    start_program("glances")


def start_program(service_name):
    app, containers = _program_containers(service_name)
    project = app["Config"]["Labels"]["com.docker.compose.project"]
    labels = app["Config"]["Labels"]
    if containers:
        container_id = containers[0]["Id"]
    else:
        container_id = _create_program(service_name, app, project, labels)
    _docker("POST", f"/containers/{container_id}/start", allowed=(204, 304))


def _program_containers(service_name):
    if service_name not in PROGRAMS:
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
    _, containers = _program_containers(service_name)
    for container in containers:
        # A short grace period keeps stop requests bounded by the socket timeout.
        _docker("POST", f"/containers/{container['Id']}/stop?t=5", allowed=(204, 304))


def _create_program(service_name, app, project, labels):
    import yaml
    with open("/docker-compose.yaml") as handle:
        service = yaml.safe_load(handle)["services"][service_name]
    workdir = Path(labels["com.docker.compose.project.working_dir"])
    binds = []
    for volume in service.get("volumes", []):
        source, destination = volume.split(":", 1)
        binds.append(f"{os.path.normpath(workdir / source)}:{destination}")
    ports = {}
    for port in service["ports"]:
        host_port, container_port = port.split(":")
        ports[f"{container_port}/tcp"] = [{"HostPort": host_port}]
    network = next(iter(app["NetworkSettings"]["Networks"]))
    spec = {
        "Image": service["image"], "Env": service.get("environment", []),
        "Labels": {"com.docker.compose.project": project,
                   "com.docker.compose.service": service_name,
                   "com.docker.compose.oneoff": "False"},
        "ExposedPorts": {port: {} for port in ports},
        "NetworkingConfig": {"EndpointsConfig": {network: {"Aliases": [service_name]}}},
        "HostConfig": {"Binds": binds, "PortBindings": ports,
                       "PidMode": service.get("pid", ""), "Tmpfs": {p: "" for p in service.get("tmpfs", [])},
                       "RestartPolicy": {"Name": "no"}, "NetworkMode": network},
    }
    name = quote(service.get("container_name", f"{project}-{service_name}-1"), safe="")
    # A simultaneous launch can create the same container first.
    result = _docker("POST", f"/containers/create?name={name}", spec, allowed=(201, 409))
    return result.get("Id", name)


def glances_ready():
    return program_ready("glances")


def program_ready(service_name):
    connection = http.client.HTTPConnection(service_name, PROGRAMS[service_name]["port"], timeout=1)
    try:
        connection.request("GET", "/")
        response = connection.getresponse()
        return response.status == 200
    except (OSError, http.client.HTTPException):
        return False
    finally:
        connection.close()
