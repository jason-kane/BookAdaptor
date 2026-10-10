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


def run_workflow_fn(workflow_fn: str):
    log.info('Running %s with direct API call', workflow_fn)
    with open(
        os.path.join(
            const.COMFY_DIRS["artifactserver"]["WORKFLOWS_DIR"],
            workflow_fn,
        ), "r"
    ) as h:
        workflow = json.load(h)

    json_workflow = {
        "prompt": ui_to_api_workflow(Workflow(workflow)).serialize()
    }

    client = httpx.Client()

    response = client.post(const.COMFYUI_API_URL + "prompt", json=json_workflow)

    if response.status_code != 200:
        log.error("Failed to create workflow", response_text=response.text)
        raise ValueError(f"Failed to create workflow: {response.text}")

    finished = False
    job_id = response.json().get("prompt_id")
    
    # TODO: don't poll.  Setup the websocket so we get a push when
    # tasks are finished.
    # 1 second polling loop
    while not finished:
        workflow_response = client.get(const.COMFYUI_API_URL + f"api/jobs/{job_id}")
        if workflow_response.status_code != 200:
            log.error("Failed to get workflow", response_text=workflow_response.text)
            return f"Failed to get workflow: {workflow_response.text}", 500

        job_dict = workflow_response.json()
        if job_dict.get("status") in ["error", "cancelled", "completed"]:
            finished = True

        if not finished:
            time.sleep(1)

    # handle the response?  
    # The expectation is that the workflow created a file in an expected location with a predicatable name.
    return None
