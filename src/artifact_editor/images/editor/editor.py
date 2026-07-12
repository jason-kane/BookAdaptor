import json
import os
from PIL import Image

from artifact_editor import llm
from artifact_editor.tools import (
    get_surrounding_paragraphs,
    get_text_to_next
)
import comfy
import httpx
import time
import const
import logger
log = logger.log(__name__)

from . import const as scene_const
from artifact_editor.characters import characters

default_outpaint_prompt = (
    "Expand the provided image to the new "
    "dimensions while maintaining "
    "the original style and composition."
)

def outpaint_to_aspect(chapter, image_xml, aspect, outpaint_description=None):
    """
    Outpaint the given image to the specified aspect ratio.

    Args:
        chapter (Chapter): The chapter object containing the image.
        image_xml (str): The XML representation of the image.
        aspect (str): The desired aspect ratio ('portrait' or 'widescreen').

    Returns:
        bool: True if outpainting was successful, False otherwise.
    """

    # padding
    left = 32
    top = 448
    right = 32
    bottom = 448
    feathering = 24

    prompt = outpaint_description or default_outpaint_prompt

    # invoke the comfyui job through comfy.py
    workflow = chapter.get_comfy_workflow(
        image_xml=image_xml,
        interface="api",
        mode="ti2i",
        workflow_template="flux_fill_outpaint",
        template_environment={
            "PROMPT": prompt,
            "LEFT": left,
            "TOP": top,
            "RIGHT": right,
            "BOTTOM": bottom,
            "FEATHERING": feathering,
        }
    )

    client = httpx.Client()
    json_workflow = {"prompt": workflow}

    response = client.post(
        const.COMFYUI_API_URL + "api/prompt",
        json=json_workflow,
    )

    if response.status_code != 200:
        # log.info(f"POST: {json.dumps(workflow, indent=2)}")
        log.error(f"Failed to create workflow: {response.text}")
        raise ValueError(f"Failed to create workflow: {response.text}")

    finished = False
    job_id = response.json().get("prompt_id")
    # 1 second polling loop
    while not finished:
        workflow_response = client.get(const.COMFYUI_API_URL + f"api/jobs/{job_id}")
        if workflow_response.status_code != 200:
            log.error(f"Failed to get workflow: {workflow_response.text}")
            return f"Failed to get workflow: {workflow_response.text}", 500
        job_dict = workflow_response.json()
        if job_dict.get("status") in ["error", "cancelled", "completed"]:
            finished = True

        if not finished:
            time.sleep(1)

    log.info("ComfyUI Workflow Complete")
    with open("/output/comfyui_workflow.json", "w") as f:
        json.dump(job_dict, f, indent=2)
    
    # crop the image down to fullscreen dimensions
    # copy the cropped image into paragraph directory
    # make it the new selected image (image_xml.attrs["src"])


def crop_to_aspect(chapter, image_xml, aspect):
    """
    Crop the given image to the specified aspect ratio.

    Args:
        chapter (Chapter): The chapter object containing the image.
        image_xml (str): The XML representation of the image.
        aspect (str): The desired aspect ratio ('portrait' or 'widescreen').

    Returns:
        bool: True if cropping was successful, False otherwise.
    """
    image = Image.open(os.path.join(
        const.LIBRARY_DIR,
        chapter.get_paragraph_dir(image_xml.find_parent("paragraph").attrs["index"]),
        os.path.basename(image_xml.attrs["src"]),
    ))
    # determine the target size based on the aspect
    target_width = const.GEOMETRY[aspect]["HSIZE"]
    target_height = const.GEOMETRY[aspect]["VSIZE"]

    # calculate the cropping box (centered)
    left = (image.width - target_width) // 2
    top = (image.height - target_height) // 2
    right = left + target_width
    bottom = top + target_height

    cropped_image = image.crop((left, top, right, bottom))
    cropped_image.save(os.path.join(
        const.LIBRARY_DIR,
        chapter.get_paragraph_dir(image_xml.find_parent("paragraph").attrs["index"]),
        os.path.basename(image_xml.attrs["src"]),
    ))

    return True
    