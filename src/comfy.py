# generic helper for comfyUI interactions
import glob
import html
import tempfile
import json
import os
import re
import shutil
import subprocess
import time
import uuid
from contextvars import ContextVar
from urllib.parse import urlsplit, urlunsplit

import simple_websocket
from typing import Any

import httpx
import const
import logger

log = logger.log(__name__)

# Scope progress to the current render job without changing every workflow caller.
workflow_progress = ContextVar("comfy_workflow_progress", default=None)


class Workflow:
    """
    Represents a specific comfyui workflow.  Workflows are read from JSON files
    and have a somewhat complex internal structure.  This class exists so we can
    pass around a 'Workflow' type object.

    This will always be a UI style workflow.  API calls are transformed from the UI format
    on the fly.
    """
    def __init__(self, workflow: dict[Any, Any]):
        self.workflow = workflow

    def serialize(self) -> dict[Any, Any]:
        # returns a serializable representation of the workflow (hehe)
        return self.workflow


def workflow_type(workflow: Workflow) -> str:
    """
    Determine the type of a workflow based on its structure.

    Returns:
        str: The type of the workflow ('image', 'text', 'other').
    """
    if "nodes" in workflow.workflow:
        return "ui"
    if "1" in workflow.workflow:
        return "api"

    raise ValueError("Unknown workflow type.")


def ui_to_api_workflow(workflow: Workflow) -> Workflow:
    """
    Convert a workflow from the UI format to the API format.

    workflow is an instance of the Workflow class representing a comfy ui workflow.
    """
    wft = workflow_type(workflow)
    if wft != "ui":
        raise ValueError(f"Provided workflow is {wft}, not 'ui' format.")

    with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".json") as tmp_file:
        json.dump(workflow.workflow, tmp_file, indent=2)

    log.info(f"Temporary workflow file created at {tmp_file.name}")

    endpoint = urlsplit(const.COMFYUI_API_URL)
    command = [
        "comfy", "run",
        "--host", endpoint.hostname or "comfyui",
        "--port", str(endpoint.port or (443 if endpoint.scheme == "https" else 8188)),
        "--print-prompt", "--workflow", tmp_file.name, "--where=local",
    ]
    # A worker can resume while ComfyUI is still restarting after a failed
    # render. Only retry conversion, which does not submit a render job.
    deadline = time.monotonic() + 60
    try:
        while True:
            output = subprocess.run(command, capture_output=True)
            try:
                result = json.loads(output.stdout.decode("utf-8"))
            except (ValueError, UnicodeError) as error:
                detail = output.stderr.decode("utf-8", errors="replace").strip()
                raise RuntimeError(
                    f"ComfyUI workflow conversion returned invalid JSON (exit {output.returncode}): "
                    f"{detail or output.stdout.decode('utf-8', errors='replace').strip()}"
                ) from error
            if not isinstance(result, dict):
                raise RuntimeError("ComfyUI workflow conversion returned an invalid response")
            data = result.get("data")
            if output.returncode == 0 and result.get("ok") is not False and isinstance(data, dict):
                prompt = data.get("prompt")
                if isinstance(prompt, dict) and prompt:
                    return Workflow(prompt)
            error = result.get("error") or {}
            if not isinstance(error, dict):
                error = {"message": str(error)}
            message = error.get("message") or "CLI response contains no workflow prompt"
            remaining = deadline - time.monotonic()
            if error.get("code") != "connection_error" or remaining <= 0:
                raise RuntimeError(f"ComfyUI workflow conversion failed: {message}")
            log.warning("Waiting for ComfyUI before workflow conversion", error=message)
            time.sleep(min(2, remaining))
    finally:
        os.unlink(tmp_file.name)


def load_workflow_template(interface: str, mode: str, workflow_name: str) -> Workflow:
    log.info(
        "Loading workflow template",
        interface=interface,
        mode=mode,
        workflow_name=workflow_name,
    )
    try:
        workflow_fn = glob.glob(
            os.path.join(
                const.COMFY_DIRS["artifactserver"]["WORKFLOW_TEMPLATES_DIR"],
                f"{interface}.{mode}.{workflow_name}*.json",
            )
        )[0]
    except IndexError:
        log.error(
            "Workflow file not found", glob=f"{interface}.{mode}.{workflow_name}*.json"
        )
        raise ValueError(
            f"Workflow file not found for interface '{interface}', mode '{mode}', workflow name '{workflow_name}'."
        )

    workflow_fn = glob.glob(
        os.path.join(
            const.COMFY_DIRS["artifactserver"]["WORKFLOW_TEMPLATES_DIR"],
            f"{interface}.{mode}.{workflow_name}*.json",
        )
    )[0]

    if not os.path.exists(workflow_fn):
        log.error("Workflow file does not exist", workflow_fn=workflow_fn)
        raise ValueError(f"Workflow file {workflow_fn} does not exist.")

    with open(workflow_fn, "r") as h:
        workflow = json.load(h)

    return Workflow(workflow)


def run_workflow(
    workflow: Workflow | dict[Any, Any],
    template_environment: dict[str, Any] | None = None,
    duration_seconds: float | None = None,
):
    """Submit a UI or API-format workflow to ComfyUI and wait for completion."""
    workflow_data = workflow.serialize() if isinstance(workflow, Workflow) else workflow
    if template_environment:
        def render(value):
            if isinstance(value, str):
                for key, replacement in template_environment.items():
                    value = value.replace("{{" + key + "}}", str(replacement))
                return value
            if isinstance(value, list):
                return [render(item) for item in value]
            if isinstance(value, dict):
                return {key: render(item) for key, item in value.items()}
            return value
        workflow_data = render(workflow_data)

    wrapped_workflow = Workflow(workflow_data)
    if workflow_type(wrapped_workflow) == "ui":
        prompt = ui_to_api_workflow(wrapped_workflow).serialize()
    else:
        prompt = workflow_data
    client_id = uuid.uuid4().hex
    json_workflow = {"prompt": prompt, "client_id": client_id}
    timeout_seconds = const.COMFYUI_JOB_TIMEOUT_SECONDS
    deadline = time.monotonic() + timeout_seconds
    api_url = const.COMFYUI_API_URL.rstrip("/")
    parts = urlsplit(api_url)
    ws_url = urlunsplit(("wss" if parts.scheme == "https" else "ws",
                        parts.netloc, parts.path + "/ws", "clientId=" + client_id, ""))
    callback = workflow_progress.get()

    def report(percent, message):
        if callback:
            callback(percent, message)

    # Subscribe before submission so fast or cached workflows cannot finish
    # before their completion event can be received.
    connection = simple_websocket.Client.connect(ws_url, ping_interval=25)
    try:
        client_timeout = httpx.Timeout(connect=10.0, read=30.0, write=30.0, pool=10.0)
        with httpx.Client(timeout=client_timeout) as client:
            response = client.post(api_url + "/prompt", json=json_workflow)
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
                raise TimeoutError(f"ComfyUI workflow {job_id} exceeded its {timeout_seconds:g}s timeout")
            raw = connection.receive(timeout=remaining)
            if raw is None:
                raise TimeoutError(f"ComfyUI workflow {job_id} exceeded its {timeout_seconds:g}s timeout")
            if raw == "":
                raise RuntimeError(f"ComfyUI WebSocket disconnected during workflow {job_id}")
            if not isinstance(raw, str):
                continue  # Binary image previews are not progress events.
            event = json.loads(raw)
            data = event.get("data", {})
            if data.get("prompt_id") != job_id:
                continue
            kind = event.get("type")
            if kind in {"execution_error", "execution_interrupted"}:
                message = data.get("exception_message", kind)
                report(0, f"ComfyUI: {message}")
                raise RuntimeError(f"ComfyUI workflow {job_id} {kind}: {message}")
            if kind == "execution_success" or (kind == "executing" and data.get("node") is None):
                report(100, "ComfyUI: Task complete")
                break
            if kind == "execution_start":
                report(0, "ComfyUI: Starting workflow")
            elif kind == "executing":
                active_node = str(data["node"])
                node = prompt.get(active_node, {})
                title = node.get("_meta", {}).get("title") or node.get("class_type", active_node)
                report(0, f"ComfyUI: {title}")
            elif kind == "progress":
                maximum = float(data.get("max", 0))
                value = float(data.get("value", 0))
                percent = max(0, min(100, int(100 * value / maximum))) if maximum > 0 else 0
                node = prompt.get(str(data.get("node", active_node)), {})
                title = node.get("_meta", {}).get("title") or node.get("class_type", "Processing")
                report(percent, f"ComfyUI: {title} ({value:g}/{maximum:g})")
    finally:
        connection.close()

    # handle the response?  
    # The expectation is that the workflow created a file in an expected location with a predicatable name.
    return None


def run_workflow_fn(workflow_fn: str, duration_seconds: float | None = None):
    log.info('Running %s with direct API call', workflow_fn)
    with open(
        os.path.join(
            const.COMFY_DIRS["artifactserver"]["WORKFLOWS_DIR"],
            workflow_fn,
        ), "r"
    ) as h:
        workflow = json.load(h)
    return run_workflow(workflow, duration_seconds=duration_seconds)
