from email.mime import message
import glob
import markdown_it
# from html_to_delta import html_to_delta

import redis
import json
import os
import shutil
import tempfile
from flask import (
    Blueprint,
    make_response,
    redirect,
    render_template,
    request,
    send_file,
    send_from_directory,
    url_for,
)
from PIL import Image
from transformers import T5Tokenizer

import camera
import const
import logger
from artifact_editor.chapter.chapter import Chapter
from artifact_editor.author.author import Author

from artifact_editor import (
    config,
    llm,
    tools,
)
from artifact_editor.characters import characters
from artifact_editor.images import htmx, images, selector
from artifact_editor.styles import styles
from artifact_editor.tools import (
    get_bookdir,
    get_bookurl,
    get_chapterdir,
    get_chapterurl,
    get_surrounding_paragraphs,
    get_text_to_next,
)
from artifact_editor.video import video
from text_to_image.registry import registry as t2i_registry

from . import htmx


md = markdown_it.MarkdownIt('commonmark', {'breaks': True, 'html': True})

FIFO_FN = os.path.join(
    os.path.dirname(__file__), 
    "..", "..",
    "drawing.fifo"
)

log = logger.log(__name__)

bp = Blueprint(
    'workflow_templates', 
    __name__,
    template_folder=os.path.join(
        os.path.dirname(__file__),
        "templates"
    )
)

MIN_TOKENS = 128
MAX_TOKENS = 256 


@bp.route("/", methods=["GET"])
def workflow_templates_base():
    templates = []
    modes = {}
    for template_name in sorted(
        os.listdir(
            const.COMFY_DIRS['artifactserver']['WORKFLOW_TEMPLATES_DIR']
        )
    ):  
        template_name = template_name.rstrip(".json")
        
        try:
            interface, mode, template = template_name.split(".", maxsplit=2)
        except ValueError:
            log.error('Invalid template name: %s', template_name)
            continue

        if interface != "ui":
            continue

        if mode not in modes:
            modes[mode] = []

        if "-" in template:
            try:
                template_name, template_cosmetic = template.split("-", maxsplit=1)
            except ValueError:
                log.error('Invalid template: %s', template)
                template_name = template
                template_cosmetic = template
        else:
            template_name = template
            template_cosmetic = template

        modes[mode].append({
            'template_name': template_name,
            'template_cosmetic': template_cosmetic
        })

    return render_template(
        "workflow_templates.html",
        modes=modes,
        mode_descriptions={
            "flf2v": "First Key Frame to Video: Generate the video between two key frame images.",
            "i2v": "Image to Video: Generate a video starting from a single image.",
            "t2a": "Text to Audio: Generate an audio file from text.",
            "t2i": "Text to Image: Generate an image from text.",
            "t2json": "Text to JSON: Generate a JSON file from text.",
            "t2t": "Text to Text: Generate text from text.",
            "ti2i": "Text and Image to Image: Generate an image from text and an input image.",
        }
    )


@bp.route("/new", methods=["POST"])
def new_workflow_template():
    mode = ""
    name = ""
    for arg in request.form:
        if arg.startswith("new_"):
            mode = arg.split("_", maxsplit=1)[1]
            name = request.form.get(arg)
            break

    if not mode or not name:
        return "Invalid request", 400

    # Copy the mode-template file as the baseline for the new template
    source_template_path = os.path.join(
        const.COMFY_DIRS['artifactserver']['WORKFLOW_TEMPLATES_DIR'],
        f"prime.{mode}.json"
    )
    if not os.path.exists(source_template_path):
        # we don't have a prime template for this mode.  Copy the generic prime.json instead.
        source_template_path = os.path.join(
            const.COMFY_DIRS['artifactserver']['WORKFLOW_TEMPLATES_DIR'],
            "prime.json"
        )

    slugname = name.lower().replace(" ", "_").replace("-", "_")

    # copy it into the workflow directory with the new name
    dest_template_path = os.path.join(
        const.COMFY_DIRS['artifactserver']['WORKFLOW_TEMPLATES_DIR'],
        f"ui.{mode}.{slugname}-{name}.json"
    )

    shutil.copyfile(source_template_path, dest_template_path)
    return redirect(url_for('workflow_templates.workflow_templates_base'))


# open_workflow
@bp.route("/open/<workflow>", methods=["GET"])
def open_workflow(workflow: str):
    # redirect to comfyui with this workflow loaded.
    return redirect(
        const.COMFYUI_UI_URL + "?workflow=" + os.path.basename(workflow),
        code=302,
    )

@bp.route("/edit/<mode>/<template_name>", methods=["GET"])
def workflow_edit(mode: str, template_name: str):
    # is there an existing edit workflow for this template?
    template_pfn = glob.glob(
        os.path.join(
            const.COMFY_DIRS['artifactserver']['WORKFLOW_TEMPLATES_DIR'], 
            f"ui.{mode}.{template_name}-*.json"
        )
    )[0]

    workflow_pfn = os.path.join(
        const.COMFY_DIRS['artifactserver']['WORKFLOWS_DIR'], 
        f"edit.{mode}.{template_name}.json"
    )

    if os.path.exists(workflow_pfn):
        # there is.  Copy it to template dir
        shutil.copyfile(
            workflow_pfn,
            template_pfn
        )

    else:
        if not os.path.exists(template_pfn):
            # the template doesn't exist either.
            prime_template_pfn = os.path.join(const.COMFY_DIRS['artifactserver']['WORKFLOW_TEMPLATES_DIR'], f"prime.{mode}.json")
            if not os.path.exists(prime_template_pfn):
                # we don't even have a prime template for this mode.
                shutil.copyfile(
                    os.path.join(const.COMFY_DIRS['artifactserver']['WORKFLOW_TEMPLATES_DIR'], f"prime.json"),
                    prime_template_pfn
                )

            shutil.copyfile(
                prime_template_pfn,
                template_pfn
            )

    chapter = Chapter(
        author=Author("Mary Wollstonecraft Shelley"),
        title="Frankenstein",
        number=1,
        language="english"
    )

    image_xml = chapter.get_image(0)
    if image_xml is None:
        log.error('No image 0 found in chapter %s', chapter.kwargs)
        return f"No image 0 found in chapter {chapter.kwargs}", 400

    workflow = chapter.get_workflow_by_filename(
        image_xml=image_xml,
        workflow_filename=template_pfn,
        video_index=None,
        template_environment={},
        force_name=os.path.basename(workflow_pfn)
    )
    
    return redirect(
        const.COMFYUI_UI_URL + "?workflow=" + os.path.basename(workflow),
        code=302,
    )

# POST to /prime_edit
@bp.route("/prime_edit", methods=["GET"])
def prime_edit():
    mode = request.args.get("mode")
    if not mode:
        return "Missing mode", 400

    force_name=f"prime.{mode}.json"
    # does this prime template exist in workflow_templates?
    if os.path.exists(
        os.path.join(
            const.COMFY_DIRS['artifactserver']['WORKFLOWS_DIR'], 
            force_name
        )
    ):
        # it _does_ exist.  Copy it into templates.
        shutil.copyfile(
            os.path.join(
                const.COMFY_DIRS['artifactserver']['WORKFLOWS_DIR'], 
                force_name
            ),
            os.path.join(
                const.COMFY_DIRS['artifactserver']['WORKFLOW_TEMPLATES_DIR'], 
                force_name
            )
        )
        # let the rest of this play out

    # we need to choose a default chapter/image_xml 
    # source for all the metadata.  g.chapter won't work
    # we don't have author/title/chapter context.
    chapter = Chapter(
        author=Author("Mary Wollstonecraft Shelley"),
        title="Frankenstein",
        number=1,
        language="english"
    )

    image_xml = chapter.get_image(0)
    if image_xml is None:
        log.error('No image 0 found in chapter %s', chapter.kwargs)
        return f"No image 0 found in chapter {chapter.kwargs}", 400

    workflow = chapter.get_workflow_by_filename(
        image_xml=image_xml,
        workflow_filename=os.path.join(
            const.COMFY_DIRS['artifactserver']['WORKFLOW_TEMPLATES_DIR'],
            f"prime.{mode}.json",
        ),
        video_index=None,
        template_environment={},
        force_name=force_name
    )

    # redirect to comfyui with this workflow loaded.
    return redirect(
        const.COMFYUI_UI_URL + "?workflow=" + os.path.basename(workflow),
        code=302,
    )