import json
import os

from flask import (
    Blueprint,
    make_response,
    redirect,
    request,
)

import const
import logger
from artifact_editor.author.author import Author
from artifact_editor.chapter.chapter import Chapter

from . import editor, htmx

log = logger.log(__name__)

bp = Blueprint(
    'editor', 
    __name__,
    template_folder=os.path.join(
        os.path.dirname(__file__),
        "templates"
    ),
)

@bp.route("/outpaint_to_fullscreen", methods=["POST"])
def outpaint_to_fullscreen(author, title, chapter_number, language, image_index):
    chapter = Chapter(
        author=Author(author),
        title=title,
        number=int(chapter_number),
        language=language,
    )
    image_index = int(image_index)
    
    outpaint_description = request.form.get(
        "outpaint_description",
        editor.default_outpaint_prompt,
    ).strip()

    aspect = chapter.get_aspect()

    image_xml = chapter.get_image(image_index)

    if not image_xml:
        return make_response("Image not found", 404)

    image_xml.attrs["outpaint_description"] = outpaint_description

    if aspect not in ["portrait", "widescreen"]:
        return make_response("Invalid aspect ratio", 400)

    # Call the outpainting function
    result = editor.outpaint_to_aspect(
        chapter, 
        image_xml, 
        aspect, 
        outpaint_description=outpaint_description
    )
    
    chapter.save_xml()
    
    if result:
        return htmx.image_editor_workshop(chapter, image_xml)
    else:
        return make_response("Outpainting failed", 500)


@bp.route("/comfy_outpaint_to_fullscreen", methods=["GET"])
def outpaint_to_fullscreen_ui(author, title, chapter_number, language, image_index):
    chapter = Chapter(
        author=Author(author),
        title=title,
        number=int(chapter_number),
        language=language,
    )
    image_index = int(image_index)
    outpaint_description = request.args.get("outpaint_description", "").strip()
    aspect = chapter.get_aspect()

    image_xml = chapter.get_image(image_index)

    if not image_xml:
        return make_response("Image not found", 404)

    if aspect not in ["portrait", "widescreen"]:
        return make_response("Invalid aspect ratio", 400)

    # padding (1024x1024 -> portrait)
    left = 32
    top = 448
    right = 32
    bottom = 448
    feathering = 24
    workflow_template = "flux_fill_outpaint"

    prompt = outpaint_description or editor.default_outpaint_prompt

    image_xml.attrs["outpaint_description"] = prompt

    workflow = chapter.get_comfy_workflow(
        image_xml=image_xml,
        interface="ui",
        mode="ti2i",
        workflow_template=workflow_template,
        template_environment={
            "PROMPT": prompt,
            "LEFT": left,
            "TOP": top,
            "RIGHT": right,
            "BOTTOM": bottom,
            "FEATHERING": feathering,
        }
    )

    if not workflow:
        return "No workflow available for this image.", 400
    
    workflow_name = f"{workflow_template}_{chapter.nice}_img_{image_xml.attrs['index']}"
    workflow_fn = os.path.join(const.COMFYUI_WORKFLOWS_DIR, workflow_name + ".json")
    with open(workflow_fn, "w") as h:
        json.dump(workflow, h)
    
    log.info('Opening workflow in ComfyUI: %s', workflow_name)
    workflow_url = const.COMFYUI_UI_URL + f"?workflow={workflow_name}.json"

    chapter.save_xml()

    return redirect(workflow_url, code=302)


# POST /library/Aesop/Fables/31/english/images/2/editor/crop_to_fullscreen
@bp.route("/crop_to_fullscreen", methods=["POST"])
def crop_to_fullscreen(author, title, chapter_number, language, image_index):
    chapter = Chapter(
        author=Author(author),
        title=title,
        number=int(chapter_number),
        language=language,
    )
    image_index = int(image_index)
    
    aspect = chapter.get_aspect()
    image_xml = chapter.get_image(image_index)

    if not image_xml:
        return make_response("Image not found", 404)

    if aspect not in ["portrait", "widescreen"]:
        return make_response("Invalid aspect ratio", 400)

    # crop the image down to fullscreen dimensions
    result = editor.crop_to_aspect(chapter, image_xml, aspect)
    
    chapter.save_xml()
    
    if result:
        return htmx.image_editor_workshop(chapter, image_xml)
    else:
        return make_response("Cropping failed", 500)