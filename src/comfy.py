"""ComfyUI workflow adapter for application-level image, video, and audio jobs.

This module provides a small stable boundary around ComfyUI's files, HTTP API,
CLI conversion, and progress WebSocket. Application code uses it to load named
templates, submit either UI- or API-format workflows, and observe progress
without depending on transport details.

## Consumers and contract

Callers in ``styler``, ``artifact_editor``, ``websocket``, and ``images`` use
``Workflow``, ``workflow_type``, ``load_workflow_template``,
``ui_to_api_workflow``, ``run_workflow``, ``run_workflow_fn``, ``free_vram``,
and the ``workflow_progress`` context variable. Workflows are dictionaries;
templates and workflow files are JSON. API workflows contain nodes with
``class_type`` and ``inputs``; UI workflows contain a top-level ``nodes`` key.
Template substitutions recursively replace ``{{KEY}}`` in string values.
``run_workflow`` submits the prompt and returns ``None`` after the matching
ComfyUI completion event. Optional progress callbacks receive integer
percentages and human-readable status text. ``duration_seconds`` is retained
for callers that describe generated media duration; the configured job timeout
continues to bound inference independently.

## Responsibility and design

This adapter owns transport, workflow-format detection, template file access,
and progress translation. It does not interpret generated artifacts or decide
where a workflow writes its outputs; those belong to the feature that requested
the job. Functions below separate file/CLI boundaries, workflow preparation,
and job event handling so each contract can be reasoned about independently.
When adding behavior, preserve the public names and event meaning unless all
consumers are updated in the same change. Never log workflow payloads: they may
contain user content.

## Settings and provenance

Retry windows and protocol timeouts are local safety bounds; the overall job
timeout and endpoint come from ``const``. The GPU free-memory threshold and
recovery windows are operational assumptions that should be measured against
production distributions before they are tuned.

Rewritten with contract-first-rewrite v1.2.0; last used 2026-10-26T22:01:10Z.
"""

import glob
import json
import os
import subprocess
import tempfile
import time
import uuid
from contextvars import ContextVar
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx
import simple_websocket

import const
import logger

log = logger.log(__name__)

# TODO(MONITOR): collect aggregate free-VRAM ranges at image-tag job start and
# after model unloading/restarts; use them to validate this operational floor.
DEFAULT_MINIMUM_FREE_VRAM_GIB = 8.0
DEFAULT_VRAM_WAIT_SECONDS = 90.0

# Conversion retries only while ComfyUI is starting; invalid workflows fail fast.
WORKFLOW_CONVERSION_RETRY_SECONDS = 60.0
WORKFLOW_CONVERSION_RETRY_INTERVAL_SECONDS = 2.0

# A progress connection must be established before submission to avoid losing
# the completion event from a workflow that finishes immediately.
WEBSOCKET_PING_INTERVAL_SECONDS = 25
WORKFLOW_HTTP_TIMEOUT = httpx.Timeout(
    connect=10.0,
    read=30.0,
    write=30.0,
    pool=10.0,
)

# Scope progress callbacks to the current render job without changing every
# existing workflow caller's signature.
workflow_progress = ContextVar("comfy_workflow_progress", default=None)


def free_vram(
    minimum_free_gib: float = DEFAULT_MINIMUM_FREE_VRAM_GIB,
    timeout_seconds: float = DEFAULT_VRAM_WAIT_SECONDS,
) -> None:
    """Unload managed models and wait for the requested CUDA memory capacity.

    If the endpoint cannot release enough memory before the first wait window
    expires, restart ComfyUI once to clear caches outside its model manager.
    A second full wait window is allowed for the restarted service to recover.
    """
    api_url = const.COMFYUI_API_URL.rstrip("/")
    required_bytes = minimum_free_gib * 1024**3
    deadline = time.monotonic() + timeout_seconds
    restarted = False

    with httpx.Client(timeout=httpx.Timeout(connect=5.0, read=5.0, write=5.0, pool=5.0)) as client:
        response = client.post(
            f"{api_url}/free",
            json={"unload_models": True, "free_memory": True},
        )
        response.raise_for_status()

        while True:
            try:
                response = client.get(f"{api_url}/system_stats")
                response.raise_for_status()
                devices = response.json().get("devices", [])
                free_values = [
                    device["vram_free"]
                    for device in devices
                    if device.get("type") == "cuda"
                    and isinstance(device.get("vram_free"), (int, float))
                ]
            except httpx.HTTPError:
                if not restarted:
                    raise
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise
                time.sleep(min(1.0, remaining))
                continue

            available_bytes = max(free_values, default=0)
            if free_values and available_bytes >= required_bytes:
                log.info(
                    "ComfyUI GPU models unloaded",
                    free_vram_gib=round(available_bytes / 1024**3, 2),
                )
                return

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                if not restarted:
                    restarted = True
                    log.warning(
                        "ComfyUI retained GPU memory after unloading models; restarting it",
                        free_vram_gib=round(available_bytes / 1024**3, 2),
                        required_free_vram_gib=minimum_free_gib,
                    )
                    from comfy_recovery import restart_comfyui

                    recovery = restart_comfyui()
                    log.info("ComfyUI VRAM recovery result", result=recovery)
                    deadline = time.monotonic() + timeout_seconds
                    continue

                available_gib = available_bytes / 1024**3
                raise TimeoutError(
                    "ComfyUI did not free enough VRAM for image tagging "
                    f"({available_gib:.2f} GiB free; need {minimum_free_gib:.2f} GiB)"
                )

            time.sleep(min(0.5, remaining))


class Workflow:
    """Wrap a workflow dictionary so callers can pass it as a named value."""

    def __init__(self, workflow: dict[Any, Any]):
        """Store the workflow without copying or changing its JSON structure."""
        self.workflow = workflow

    def serialize(self) -> dict[Any, Any]:
        """Return the underlying dictionary in the format expected by callers."""
        return self.workflow


def workflow_type(workflow: Workflow) -> str:
    """Classify a workflow as UI or API format, rejecting unknown structures."""
    if "nodes" in workflow.workflow:
        return "ui"

    nodes = workflow.workflow
    if nodes and all(
        isinstance(node, dict)
        and isinstance(node.get("class_type"), str)
        and isinstance(node.get("inputs"), dict)
        for node in nodes.values()
    ):
        return "api"

    raise ValueError("Unknown workflow type.")


def ui_to_api_workflow(workflow: Workflow) -> Workflow:
    """Convert a UI workflow with the Comfy CLI, retrying service startup only.

    The CLI is the compatibility boundary for ComfyUI's evolving UI graph
    format. Its temporary input is always removed, including when serialization
    itself fails before the CLI is invoked.
    """
    kind = workflow_type(workflow)
    if kind != "ui":
        raise ValueError(f"Provided workflow is {kind}, not 'ui' format.")

    with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".json") as tmp_file:
        temporary_path = tmp_file.name

    try:
        with open(temporary_path, "w") as workflow_file:
            json.dump(workflow.workflow, workflow_file, indent=2)

        log.info("Temporary workflow file created", path=temporary_path)
        endpoint = urlsplit(const.COMFYUI_API_URL)
        command = [
            "comfy",
            "run",
            "--host",
            endpoint.hostname or "comfyui",
            "--port",
            str(endpoint.port or (443 if endpoint.scheme == "https" else 8188)),
            "--print-prompt",
            "--workflow",
            temporary_path,
            "--where=local",
        ]
        deadline = time.monotonic() + WORKFLOW_CONVERSION_RETRY_SECONDS
        startup_requested = False

        while True:
            output = subprocess.run(command, capture_output=True, check=False)
            try:
                result = json.loads(output.stdout.decode("utf-8"))
            except (ValueError, UnicodeError) as error:
                detail = output.stderr.decode("utf-8", errors="replace").strip()
                stdout = output.stdout.decode("utf-8", errors="replace").strip()
                raise RuntimeError(
                    "ComfyUI workflow conversion returned invalid JSON "
                    f"(exit {output.returncode}): {detail or stdout}"
                ) from error

            if not isinstance(result, dict):
                raise TypeError("ComfyUI workflow conversion returned an invalid response")

            data = result.get("data")
            prompt = data.get("prompt") if isinstance(data, dict) else None
            if output.returncode == 0 and result.get("ok") is not False and isinstance(prompt, dict) and prompt:
                return Workflow(prompt)

            error_data = result.get("error") or {}
            if not isinstance(error_data, dict):
                error_data = {"message": str(error_data)}
            message = error_data.get("message") or "CLI response contains no workflow prompt"
            remaining = deadline - time.monotonic()
            if error_data.get("code") != "connection_error" or remaining <= 0:
                raise RuntimeError(f"ComfyUI workflow conversion failed: {message}")

            if not startup_requested:
                startup_requested = True
                log.warning("ComfyUI is unavailable; requesting service startup", error=message)
                try:
                    from worker_startup import ensure_worker_services

                    ensure_worker_services(comfyui=True)
                except Exception:
                    log.exception("Unable to start ComfyUI after a workflow conversion connection failure")

            log.warning("Waiting for ComfyUI before workflow conversion", error=message)
            time.sleep(min(WORKFLOW_CONVERSION_RETRY_INTERVAL_SECONDS, remaining))
    finally:
        os.unlink(temporary_path)


def load_workflow_template(interface: str, mode: str, workflow_name: str) -> Workflow:
    """Load and wrap the first matching named workflow template JSON file."""
    log.info(
        "Loading workflow template",
        interface=interface,
        mode=mode,
        workflow_name=workflow_name,
    )
    filename_pattern = f"{interface}.{mode}.{workflow_name}*.json"
    template_dir = const.COMFY_DIRS["artifactserver"]["WORKFLOW_TEMPLATES_DIR"]
    matches = glob.glob(os.path.join(template_dir, filename_pattern))
    if not matches:
        log.error("Workflow file not found", glob=filename_pattern)
        raise ValueError(
            f"Workflow file not found for interface '{interface}', mode '{mode}', "
            f"workflow name '{workflow_name}'."
        )

    workflow_path = matches[0]
    if not os.path.exists(workflow_path):
        log.error("Workflow file does not exist", workflow_fn=workflow_path)
        raise ValueError(f"Workflow file {workflow_path} does not exist.")

    with open(workflow_path, "r") as workflow_file:
        return Workflow(json.load(workflow_file))


def _render_template_values(value: Any, environment: dict[str, Any]) -> Any:
    """Recursively substitute template markers while preserving JSON types."""
    if isinstance(value, str):
        for key, replacement in environment.items():
            value = value.replace("{{" + key + "}}", str(replacement))
        return value
    if isinstance(value, list):
        return [_render_template_values(item, environment) for item in value]
    if isinstance(value, dict):
        return {key: _render_template_values(item, environment) for key, item in value.items()}
    return value


def _websocket_url(api_url: str, client_id: str) -> str:
    """Build the matching ws/wss endpoint while retaining API path prefixes."""
    parts = urlsplit(api_url)
    scheme = "wss" if parts.scheme == "https" else "ws"
    return urlunsplit((scheme, parts.netloc, parts.path + "/ws", f"clientId={client_id}", ""))


def _node_title(prompt: dict[str, Any], node_id: Any, fallback: str = "Processing") -> str:
    """Choose a useful progress label from node metadata or its class name."""
    node = prompt.get(str(node_id), {})
    metadata = node.get("_meta", {})
    return metadata.get("title") or node.get("class_type", fallback)


def _handle_execution_failure(kind: str, data: dict[str, Any], job_id: str, report) -> None:
    """Report execution failure and recover only contradictory CUDA OOMs."""
    message = data.get("exception_message", kind)
    if kind == "execution_error":
        from comfy_recovery import contradictory_oom, restart_comfyui

        if contradictory_oom(message):
            report(0, "ComfyUI: Inconsistent CUDA OOM; restarting ComfyUI")
            try:
                recovery = restart_comfyui()
            except Exception as error:
                log.exception("ComfyUI OOM recovery failed")
                recovery = f"ComfyUI restart failed: {error}"
            message = f"{message}\n{recovery}"

    report(0, f"ComfyUI: {message}")
    raise RuntimeError(f"ComfyUI workflow {job_id} {kind}: {message}")


def _report_workflow_event(event: dict[str, Any], prompt: dict[str, Any], active_node: Any, report) -> Any:
    """Translate one matching ComfyUI event into a user-facing progress update."""
    kind = event.get("type")
    data = event.get("data", {})

    if kind == "execution_start":
        report(0, "ComfyUI: Starting workflow")
    elif kind == "executing":
        active_node = data.get("node")
        if active_node is None:
            report(100, "ComfyUI: Task complete")
        else:
            report(0, f"ComfyUI: {_node_title(prompt, active_node, str(active_node))}")
    elif kind == "progress":
        # TODO(MONITOR): measure raw progress max/value ranges before clamping;
        # this will show whether unusual node protocols need distinct handling.
        maximum = float(data.get("max", 0))
        value = float(data.get("value", 0))
        percent = max(0, min(100, int(100 * value / maximum))) if maximum > 0 else 0
        node_id = data.get("node", active_node)
        title = _node_title(prompt, node_id)
        report(percent, f"ComfyUI: {title} ({value:g}/{maximum:g})")

    return active_node


def run_workflow(
    workflow: Workflow | dict[Any, Any],
    template_environment: dict[str, Any] | None = None,
    duration_seconds: float | None = None,
) -> None:
    """Submit a UI/API workflow and wait for its matching completion event.

    ``duration_seconds`` remains accepted because feature callers use it to
    describe the media they requested. Job safety is bounded by the configured
    ComfyUI timeout, since generation time is not proportional to clip length.
    """
    workflow_data = workflow.serialize() if isinstance(workflow, Workflow) else workflow
    if template_environment:
        workflow_data = _render_template_values(workflow_data, template_environment)

    wrapped_workflow = Workflow(workflow_data)
    if workflow_type(wrapped_workflow) == "ui":
        prompt = ui_to_api_workflow(wrapped_workflow).serialize()
    else:
        prompt = workflow_data

    client_id = uuid.uuid4().hex
    request_body = {"prompt": prompt, "client_id": client_id}
    timeout_seconds = const.COMFYUI_JOB_TIMEOUT_SECONDS
    deadline = time.monotonic() + timeout_seconds
    api_url = const.COMFYUI_API_URL.rstrip("/")
    callback = workflow_progress.get()

    def report(percent: int, message: str) -> None:
        """Forward progress only when a caller installed a context callback."""
        if callback:
            callback(percent, message)

    connection = simple_websocket.Client.connect(
        _websocket_url(api_url, client_id),
        ping_interval=WEBSOCKET_PING_INTERVAL_SECONDS,
    )
    try:
        with httpx.Client(timeout=WORKFLOW_HTTP_TIMEOUT) as client:
            response = client.post(f"{api_url}/prompt", json=request_body)
            if response.status_code != 200:
                raise ValueError(f"Failed to create workflow: {response.text}")
            job_id = response.json().get("prompt_id")
            if not job_id:
                raise RuntimeError("ComfyUI accepted workflow without returning a prompt_id")

        report(0, "ComfyUI: Queued")
        active_node = None
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(
                    f"ComfyUI workflow {job_id} exceeded its {timeout_seconds:g}s timeout"
                )

            raw_event = connection.receive(timeout=remaining)
            if raw_event is None:
                raise TimeoutError(
                    f"ComfyUI workflow {job_id} exceeded its {timeout_seconds:g}s timeout"
                )
            if raw_event == "":
                raise RuntimeError(f"ComfyUI WebSocket disconnected during workflow {job_id}")
            if not isinstance(raw_event, str):
                continue  # Binary image previews are not progress events.

            event = json.loads(raw_event)
            data = event.get("data", {})
            if data.get("prompt_id") != job_id:
                continue
            kind = event.get("type")
            if kind in {"execution_error", "execution_interrupted"}:
                _handle_execution_failure(kind, data, job_id, report)
            if kind == "execution_success":
                report(100, "ComfyUI: Task complete")
                break
            active_node = _report_workflow_event(event, prompt, active_node, report)
            if kind == "executing" and data.get("node") is None:
                break
    finally:
        connection.close()


def run_workflow_fn(workflow_fn: str, duration_seconds: float | None = None) -> None:
    """Read a named workflow JSON file and submit it through ``run_workflow``."""
    log.info("Running %s with direct API call", workflow_fn)
    workflow_path = os.path.join(
        const.COMFY_DIRS["artifactserver"]["WORKFLOWS_DIR"],
        workflow_fn,
    )
    with open(workflow_path, "r") as workflow_file:
        workflow = json.load(workflow_file)
    return run_workflow(workflow, duration_seconds=duration_seconds)
