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
from typing import Any

import httpx
import const
import logger

log = logger.log(__name__)


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

    output = subprocess.run(
        [
            "comfy",
            "run",
            "--host", "comfyui",
            "--print-prompt",
            "--workflow",
            tmp_file.name,
            "--where=local",
        ],
        capture_output=True,
    )

    return Workflow(json.loads(output.stdout.decode("utf-8"))["data"]["prompt"])


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
    json_workflow = {"prompt": prompt}
    timeout_seconds = const.COMFYUI_JOB_TIMEOUT_SECONDS
    deadline = time.monotonic() + timeout_seconds
    client_timeout = httpx.Timeout(connect=10.0, read=30.0, write=30.0, pool=10.0)
    with httpx.Client(timeout=client_timeout) as client:
        response = client.post(const.COMFYUI_API_URL + "prompt", json=json_workflow)

        if response.status_code != 200:
            log.error("Failed to create workflow", response_text=response.text)
            raise ValueError(f"Failed to create workflow: {response.text}")

        job_id = response.json().get("prompt_id")
        if not job_id:
            raise RuntimeError(
                f"ComfyUI accepted workflow without returning a prompt_id: {response.text}"
            )

        log.info(
            "Polling ComfyUI workflow",
            job_id=job_id,
            timeout_seconds=timeout_seconds,
        )
        last_status = None
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                log.error(
                    "ComfyUI workflow timed out",
                    job_id=job_id,
                    timeout_seconds=timeout_seconds,
                    last_status=last_status,
                )
                raise TimeoutError(
                    f"ComfyUI workflow {job_id} exceeded its {timeout_seconds:g}s "
                    f"timeout (last status: {last_status!r})"
                )

            workflow_response = client.get(
                const.COMFYUI_API_URL + f"api/jobs/{job_id}",
                timeout=min(30.0, remaining),
            )
            if workflow_response.status_code != 200:
                raise RuntimeError(
                    f"Failed to get ComfyUI workflow {job_id}: "
                    f"HTTP {workflow_response.status_code}: {workflow_response.text}"
                )

            job_dict = workflow_response.json()
            job_status = str(job_dict.get("status", "")).lower()
            if job_status != last_status:
                log.info(
                    "ComfyUI workflow status changed",
                    job_id=job_id,
                    status=job_status or "unknown",
                )
                last_status = job_status

            if job_status in {"completed", "success", "succeeded"}:
                break
            if job_status in {
                "error", "failed", "failure", "cancelled", "canceled", "interrupted"
            }:
                log.error(
                    "ComfyUI workflow attempted duration",
                    job_id=job_id,
                    duration_seconds=duration_seconds,
                )
                log.error(
                    "ComfyUI workflow did not complete",
                    job_id=job_id,
                    status=job_status,
                    job=job_dict,
                )
                raise RuntimeError(f"ComfyUI workflow {job_id} {job_status}: {job_dict}")

            time.sleep(min(1.0, max(0.0, deadline - time.monotonic())))

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
