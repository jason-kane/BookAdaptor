import glob
import io
import copy
from tempfile import TemporaryDirectory
import textwrap
import json
import html
from pygments.filters import Filter
import random
from pygments import highlight, format, lex
from pygments.formatters import HtmlFormatter
from pygments.lexers import HtmlLexer
import httpx
import os
import shutil
import time

from typing import TypedDict
from animations.registry import registry as animation_registry


# from ComfyUI.app.node_replace_manager import NodeStruct
class NodeStruct(TypedDict):
    inputs: dict[str, str | int | float | bool | tuple[str, int]]
    class_type: str
    _meta: dict[str, str]


from pyparsing import with_class

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
from artifact_editor import (
    chapter,
    config,
    llm,
    tools,
)
from artifact_editor.author.author import Author
from artifact_editor.cache import cache
from artifact_editor.chapter.chapter import Chapter
from artifact_editor.characters import characters
from artifact_editor.images import (
    htmx,
    images,
    scene,
    editor,
)
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

from .selector import htmx as selector_htmx
from .selector.views import bp as images_selector
from .selector import selector as selector
from .scene.views import bp as images_scene
from .editor.views import bp as images_editor
from .camera.views import bp as images_camera
from .camera import htmx as camera_htmx


FIFO_FN = os.path.join(os.path.dirname(__file__), "..", "..", "drawing.fifo")

log = logger.log(__name__)

bp = Blueprint(
    "images",
    __name__,
    template_folder=os.path.join(os.path.dirname(__file__), "templates"),
)

bp.register_blueprint(images_selector, url_prefix="/<int:image_index>/selector")
bp.register_blueprint(images_editor, url_prefix="/<int:image_index>/editor")
bp.register_blueprint(images_scene, url_prefix="/<int:image_index>/scene")
bp.register_blueprint(images_camera, url_prefix="/<int:image_index>/camera")

MIN_TOKENS = 128
MAX_TOKENS = 256

# library.book.chapter.image.comfyui_workflow_choose

# trim_animation_to_audio
@bp.route("/<int:image_index>/trim_animation_to_audio", methods=["POST"])
def trim_animation_to_audio(author, title, chapter_number, language, image_index=0):
    """
    We have more frames of video than we need to cover
    the audio track.  The problem comes in when we want
    the next audio to visually match.
    """
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)
    image_index = int(image_index)

    image_xml = chapter.get_image(image_index)
    video_index = int(request.form.get("video_index", 0))

    audio_frames = int(float(image_xml.attrs.get("frames")))
    frame_dir = os.path.join(
        const.LIBRARY_DIR,
        chapter.get_paragraph_dir(image_xml.find_parent("paragraph").attrs["index"]),
        "animation",
        f"image_{int(image_xml.attrs['index']):06d}_{video_index:02d}",
    )

    all_frames = sorted(os.listdir(frame_dir))
    video_frames = len(all_frames)

    while video_frames > audio_frames:
        os.unlink(os.path.join(frame_dir, all_frames.pop()))
        video_frames -= 1

    # re-generate the video from the remaining frames
    image_filename = chapter.get_image_filename(image_xml)
    if video_index == 0:
        video_filename = image_filename.replace(".png", ".mp4")
    else:
        video_filename = image_filename.replace(".png", f"_{video_index:02d}.mp4")

    tools.assemble_mp4(
        fps=const.FPS,
        framedir=frame_dir,
        wavfile=None,
        videofile=video_filename,
        image_match="frame_%06d.png",
    )

    return "", 200


@bp.route("/<int:image_index>/workflow_choose", methods=["PUT"])
def comfyui_workflow_choose(author, title, chapter_number, language, image_index=0):
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)
    image_index = int(image_index)

    image_xml = chapter.get_image(image_index)
    workflow_image_template = request.form.get("workflow_image_template", "")
    log.info("Selected workflow image template: %s", workflow_image_template)

    # image_mode, workflow_image_template = workflow_image_template.split('.')
    # log.info('Trimmed workflow image template: %s', workflow_image_template)

    image_xml.attrs["workflow_image_template"] = workflow_image_template
    # image_xml.attrs["image_mode"] = image_mode

    chapter.save_xml()
    return "", 200


@bp.route("/<int:image_index>/workflow_run", methods=["POST"])
def comfyui_workflow_run(author, title, chapter_number, language, image_index=0):
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)

    client = httpx.Client()
    image_xml = chapter.get_image(image_index)
    image_index = int(image_index)

    workflow_template = image_xml.attrs.get("workflow_image_template", "")
    if "." in workflow_template:
        mode = workflow_template.split(".")[0]
        workflow_template = workflow_template.split(".")[-1]
        image_xml.attrs["workflow_image_template"] = workflow_template

    # is this the first image in the paragraph?  Then we want t2i,
    # otherwise we will ti2i and base our image on the previous image.
    # if chapter.is_first_image(image_xml):
    #     mode = "t2i"
    # else:
    #     mode = "ti2i"

    workflow = chapter.get_comfy_workflow(
        image_xml,
        interface="api",
        mode=mode,
        workflow_template=workflow_template,
    )

    json_workflow = {"prompt": workflow}

    # start the comfyui workflow
    response = client.post(
        const.COMFYUI_API_URL + "api/prompt",
        json=json_workflow,
    )

    # this generally means we did something wrong.
    if response.status_code != 200:
        # log.info(f"POST: {json.dumps(workflow, indent=2)}")
        log.error(f"Failed to create workflow: {response.text}")
        return f"Failed to create workflow: {response.text}", 500

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

    # get the image prompt save it to the image_xml
    # output/baum-marv-001-bhgv_img_10_zit-paragraph.prompt.txt

    workflow_template = image_xml.attrs.get("workflow_template", "")
    prompt_fn = os.path.join(
        const.COMFY_DIRS["artifactserver"]["OUTPUT_DIR"],
        f"{chapter.nice}_img_{image_xml.attrs['index']}_{workflow_template}.prompt.txt",
    )

    prompt = ""
    if os.path.exists(prompt_fn):
        with open(prompt_fn, "r") as h:
            prompt = h.read().strip()
            image_xml.attrs["prompt"] = prompt
    else:
        log.error(f"Prompt file {prompt_fn} not found after workflow completion.")
        image_xml.attrs["prompt"] = ""
    chapter.save_xml()

    # the workflow has finished, the "final" job_dict should reflect the
    # finished state.  Gather the output image and copy it to the expected
    # location in the library, then update the image_xml to point to the new
    # image.

    for nodeId in job_dict.get("outputs", {}):
        for image in job_dict["outputs"][nodeId].get("images", []):
            if "filename" in image:
                filename = image["filename"]
                pfn = images.get_image_fn(
                    prompt=prompt + "_comfyui",
                    loras=[],
                    paragraph_dir=chapter.get_paragraph_dir(
                        image_xml.find_parent("paragraph").attrs["index"]
                    ),
                    image_index=image_xml.attrs["index"],
                    randomized=False,
                )

                shutil.copy(
                    os.path.join(
                        const.COMFY_DIRS["artifactserver"]["OUTPUT_DIR"], filename
                    ),
                    os.path.join(const.LIBRARY_DIR, pfn),
                )

                image_xml.attrs["src"] = os.path.basename(pfn)
                chapter.save_xml()

    return "", 200

# /6/workflow_link/*/1/animation
@bp.route("/<int:image_index>/workflow_link/<mode>/<int:video_index>/", methods=["GET"])
@bp.route("/<int:image_index>/workflow_link/<mode>/<int:video_index>/<animation>", methods=["GET"])
def comfyui_workflow_open(
    author,
    title,
    chapter_number,
    language,
    image_index=0,
    mode="t2i",
    video_index=0,
    animation=False,
):
    """
    We're already in a new tab
    the user wants us to "become" the comfyui workflow page
    for this image.

    So we need to:
    1. Create the workflow in comfyui based on the selected template and the image prompt.
    2. Redirect to the workflow page for that workflow in comfyui.
    3. This workflow needs to become the one we use from now on for this image.
    4. Unless the user (UX TBD) chooses to reset it.

    Fortunately these are all easy.
    """
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)
    image_xml = chapter.get_image(image_index)
    image_index = int(image_index)
    video_index = int(video_index)

    video_tag = f"_{video_index:02d}"

    if isinstance(animation, str):
        animation = animation.lower() in ["true", "animation"]
    else:
        animation = bool(animation)
    log.info(f"animation is {animation}")

    if animation:
        # the kind of template used to animate this image over time
        # ie:  the "LTX23" part ^^^^^
        workflow_template = image_xml.attrs.get(
            f"workflow_animation_template{video_tag}", ""
        )

        if "." in workflow_template:
            # ui.t2i.LTX23 -> LTX23
            workflow_template = workflow_template.split(".")[-1]
            image_xml.attrs[f"workflow_animation_mode{video_tag}"] = mode
            image_xml.attrs[f"workflow_animation_template{video_tag}"] = (
                workflow_template
            )
    else:
        # the kind of template used to draw this image
        # ie:  the "flux-phrase-chain" part ^^^^^
        workflow_template = image_xml.attrs.get("workflow_image_template", "")

        if "." in workflow_template:
            workflow_template = workflow_template.split(".")[-1]
            image_xml.attrs["workflow_image_mode"] = mode
            image_xml.attrs["workflow_image_template"] = workflow_template

    if request.args.get("prompt"):
        log.info("Overriding prompt with provided arg: %s", request.args.get("prompt"))
        if animation:
            image_xml.attrs[f"animation_prompt{video_tag}"] = request.args.get("prompt")
        else:
            image_xml.attrs["prompt"] = request.args.get("prompt")
    elif request.form.get("prompt"):
        log.info(
            "Overriding prompt with provided form arg: %s", request.form.get("prompt")
        )
        if animation:
            image_xml.attrs[f"animation_prompt{video_tag}"] = request.form.get("prompt")
        else:
            image_xml.attrs["prompt"] = request.form.get("prompt")
    else:
        log.info(
            "Prompt override not provided.  Using existing prompt: %s",
            image_xml.attrs.get(
                f"animation_prompt{video_tag}" if animation else "prompt", ""
            ),
        )

    # enough to determine which worflow we want, this is also what does the
    # customization from template -> instance.
    # workflow is the json-ready dict representing a workflow.
    log.warning("Generating workflow", workflow_template=workflow_template)
    workflow = chapter.get_comfy_workflow(
        image_xml,
        interface="ui",
        mode=mode,
        workflow_template=workflow_template,
        video_index=video_index,
    )

    if not workflow:
        return "No workflow available for this image.", 400

    # this is the pisser that will show up in your workflow list.
    workflow_name = f"{workflow_template}_{chapter.nice}_img_{image_xml.attrs['index']}_{video_index:02d}"
    workflow_fn = os.path.join(const.COMFY_DIRS["comfyui"]["WORKFLOWS_DIR"], workflow_name + ".json")
    with open(workflow_fn, "w") as h:
        json.dump(workflow, h)

    # 2. Redirect to the workflow page for that workflow in comfyui.
    workflow_url = const.COMFY_DIRS["comfyui"]["UI_URL"] + f"?workflow={workflow_name}.json"

    # 3. This workflow needs to become the one we use from now on for this image.
    # image_xml.attrs["workflow_name"] = workflow_name
    chapter.save_xml()

    # 4. Unless the user (UX TBD) chooses to reset it.
    # TODO

    return redirect(workflow_url, code=302)


@bp.route("/<int:image_index>/actions/generate_meta", methods=["POST"])
def generate_meta(author, title, chapter_number, language, image_index=0):
    """
    Generate metadata for the image based on the source material
    """
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)
    image_xml = chapter.get_xml().findAll("image")[image_index]
    images.generate_metadata_from_text(chapter, image_xml, force_all=True)
    chapter.save_xml()

    return htmx.image_strip_centerpiece(
        chapter,
        image_xml,
        default="image",
    ), 200


@bp.route("/<int:image_index>/actions/rebuild_prompt", methods=["POST"])
def rebuild_prompt(author, title, chapter_number, language, image_index=0):
    """
    Action behind the "Rebuild prompt from Meta" button.  This will take the
    meta_prompt and substitute in the various metadata fields to create a new
    prompt for the image.

    These values are stored on the <image> tag in the chapter XML file.
    """
    log.info(f"{request.form}")
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)

    image_index = int(image_index)

    image_xml = chapter.get_xml().findAll("image")[image_index]
    log.info("Rebuilding prompts for image:")
    log.info(f"""
    <{image_xml.name}
        {'\n        '.join([f'{k}="{v}"' for k, v in image_xml.attrs.items()])}
    >
    """)

    # persist the meta prompt on the image
    image_xml.attrs["meta_prompt"] = (
        "[SETTING] [TOD] [CAMERA] [FOCUS_CHARACTER] [CHARACTERS]"
    )

    # start with all global characters
    all_characters = characters.get_all_characters(chapter, is_global=True)

    # update with all chapter-specific characters
    all_characters.update(characters.get_all_characters(chapter, is_global=False))

    images.build_replacement_prompt(all_characters, chapter.bookdir, image_xml)

    value = htmx.prompt_panel(chapter, image_xml)
    chapter.save_xml()
    return value, 201


@bp.route("/<int:image_index>/actions/save_style", methods=["PUT"])
def save_style(author, title, chapter_number, language, image_index=0):
    log.info("Saving style for image %s", image_index)
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)

    image = chapter.get_xml().findAll("image")[image_index]

    image.attrs["style"] = request.form["style"]
    chapter.save_xml()

    return htmx.style(chapter, image_index)


@bp.route("/<int:image_index>/actions/create_clip_prompt", methods=["POST"])
def create_prompt(author, title, chapter_number, language, image_index=0):
    log.info(f"{request.form}")
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)

    image_xml = chapter.get_xml().findAll("image")[image_index]
    paragraph = image_xml.find_parent("paragraph")

    prompt_fn = os.path.abspath(
        os.path.join(
            const.LIBRARY_DIR,
            chapter.get_paragraph_dir(paragraph.attrs["index"]),
            f"img_{image_xml.attrs['fragdex']}.prompt",
        )
    )

    # use meta_prompt combined with the text to try and generate an image_xml prompt.
    # this is cringe, it drops most of the provided detail and makes up stupid stuff.
    paragraph_text = get_surrounding_paragraphs(paragraph, context_min=400)

    # create the t5 prompt based on the selected and paragraph text only if it
    # doesn't already exist.
    t5_prompt = image_xml.attrs.get("t5_prompt", "")
    if not t5_prompt:
        # kwargs don't work here (yet)
        # gpu.text._text_to_image_prompt(text, paragraph_text, meta_prompt, prompt_fn):
        t5_prompt = llm.trigger_llm_task_str(
            "text_to_image_prompt",  # text,
            paragraph_text,  # paragraph_text
            "",  # meta_prompt
            prompt_fn,  # prompt_fn
        )
        # t5_prompt = text._text_to_image_prompt(
        #     text=text_to_next,
        #     paragraph_text=paragraph_text,
        #     meta_prompt="",  # doesn't do anything right now.
        #     prompt_fn=prompt_fn,
        # )
        image_xml.attrs["t5_prompt"] = t5_prompt

    text_to_next = get_text_to_next(
        image_xml=image_xml, next_image_xml=image_xml.find_next("image")
    )

    # Now rebuild the CLIP prompt
    clip_prompt = images.text_to_image_clip_prompt(
        text=text_to_next,
        paragraph_text=paragraph_text,
        t5_prompt=t5_prompt,
        prompt_fn=prompt_fn,
    )

    with open(prompt_fn, "w") as h:
        h.write(
            json.dumps(
                {
                    "t5_prompt": t5_prompt,
                    "clip_prompt": clip_prompt,
                }
            )
        )

    image_xml.attrs["clip_prompt"] = clip_prompt

    if "prompt" in image_xml.attrs:
        del image_xml.attrs["prompt"]

    chapter.save_xml()

    return htmx.prompt_panel(chapter, image_xml), 200


@bp.route("/<int:image_index>/actions/condense_prompt", methods=["POST"])
def condense_prompt(author, title, chapter_number, language, image_index=0):
    log.info(f"{request.form}")
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)

    image_xml = chapter.get_xml().findAll("image")[image_index]
    paragraph = image_xml.find_parent("paragraph")

    prompt_data = None
    prompt_fn = chapter.get_image_prompt_filename(image_xml)
    if os.path.exists(prompt_fn):
        with open(prompt_fn, "r") as h:
            try:
                prompt_data = json.load(h)
            except json.JSONDecodeError:
                log.error(
                    f"Failed to decode JSON from:\n\n{h.read()}\n\n. Cannot condense an empty prompt."
                )
                prompt_data = None

    if prompt_data is None:
        prompt_data = {
            "prompt": image_xml.attrs.get("prompt", ""),
            "clip_prompt": image_xml.attrs.get("clip_prompt", ""),
        }

        with open(os.path.join(const.LIBRARY_DIR, prompt_fn), "w") as h:
            h.write(json.dumps(prompt_data))

    text_to_next = get_text_to_next(
        image_xml=image_xml, next_image_xml=image_xml.find_next_sibling("image")
    )

    paragraph_text = get_surrounding_paragraphs(paragraph)

    prompt = llm.trigger_llm_task_str(
        "condense_image_prompt",
        os.path.join(const.LIBRARY_DIR, prompt_fn),  # full path prompt_fn
        text_to_next,  # text,
        paragraph_text,  # paragraph_text
        "",  # meta_prompt
    )

    with open(os.path.join(const.LIBRARY_DIR, prompt_fn), "w") as h:
        h.write(
            json.dumps(
                {
                    "prompt": prompt,
                    "clip_prompt": prompt_data.get(
                        "clip_prompt", image_xml.attrs.get("clip_prompt", "")
                    ),
                }
            )
        )

    # clip_prompt = image_xml.attrs.get("clip_prompt", "")

    # with open(prompt_fn, "w") as h:
    #     h.write(json.dumps({
    #         "t5_prompt": t5_prompt,
    #         "clip_prompt": clip_prompt,
    #     }))

    image_xml.attrs["prompt"] = prompt
    chapter.save_xml()

    return htmx.prompt_panel(chapter, image_xml), 200


def set_fancyprompt(chapter, image_xml):
    """
    Replace the prompt in an image with a fanciful style still based, at least
    somewhat, on the text of the story.
    """
    paragraph = image_xml.find_parent("paragraph")

    prompt_fn = chapter.get_image_prompt_filename(image_xml)
    #     os.path.join(
    #         const.LIBRARY_DIR,
    #         paragraph.attrs["dir"],
    #         f"img_{image_xml.attrs['fragdex']}.prompt",
    #     )
    # )

    text_to_next = get_text_to_next(
        image_xml=image_xml, next_image_xml=image_xml.find_next_sibling("image")
    )

    # use meta_prompt combined with the text to try and generate an image prompt.
    # this is cringe, it drops most of the provided detail and makes up stupid stuff.
    paragraph_text = get_surrounding_paragraphs(paragraph)

    # this is ridiculously, painfully slow
    prompt = llm.trigger_llm_task_str(
        "text_to_fanciful_image_prompt",
        text_to_next,  # text,
        paragraph_text,  # paragraph_text
        "",  # meta_prompt
    )

    clip_prompt = image_xml.attrs.get("clip_prompt", "")

    os.makedirs(os.path.dirname(prompt_fn), exist_ok=True)
    with open(prompt_fn, "w") as h:
        h.write(
            json.dumps(
                {
                    "prompt": prompt,
                    "clip_prompt": clip_prompt,
                }
            )
        )

    image_xml.attrs["prompt"] = prompt
    image_xml.attrs["clip_prompt"] = clip_prompt
    return image_xml


def _apply_zmi(chapter, image_xml):
    # first we do a scene generate meta from text
    # /images/<int:image_index>/actions/generate_meta
    # looks like a neighbor...
    images.generate_metadata_from_text(chapter.chapterdir, image_xml, force_all=True)
    # then choose zimage as our type
    image_model = "tsqn.zimageturbo"
    image_xml.attrs["t2i"] = image_model

    # then import that scene as the zimage prompt
    # /actions/rebuild_prompt
    # persist the meta prompt on the image
    image_xml.attrs["meta_prompt"] = (
        "[SETTING] [TOD] [CAMERA] [FOCUS_CHARACTER] [CHARACTERS]"
    )

    # start with all global characters
    all_characters = characters.get_all_characters(chapter, is_global=True)

    # update with all chapter-specific characters
    all_characters.update(characters.get_all_characters(chapter, is_global=False))

    images.build_replacement_prompt(all_characters, chapter.bookdir, image_xml)

    prompt = image_xml.attrs["prompt"]
    image_xml.attrs["raw_prompt"] = prompt

    # then apply the chapter style
    style = chapter.config.get("style", "wizardofoz")
    image_xml.attrs["style"] = style

    prompt_filter, negative_prompt = styles.get_style(style)

    if prompt_filter:
        prompt = prompt_filter.format(prompt=image_xml.attrs.get("raw_prompt", ""))
        image_xml.attrs["t5_prompt"] = prompt
        image_xml.attrs["prompt"] = prompt

        if negative_prompt:
            image_xml.attrs["negative_prompt"] = negative_prompt
        log.info(f"Updated prompt to {prompt}")


# /{{chapterurl}}/images/action/apply_zmi_here_to_end
@bp.route("/<int:image_index>/actions/apply_zmi_here_to_end", methods=["POST"])
def apply_zmi_here_to_end(author, title, chapter_number, language, image_index=0):
    log.info(f"{request.form}")
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)

    all_images = chapter.get_xml().findAll("image")
    image_xml = all_images[image_index]
    for image_index, image_xml in enumerate(all_images[image_index:]):
        if (image_xml.attrs.get("t2i") != "tsqn.zimageturbo") and (
            image_xml.attrs.get("src") in [None, ""]
        ):
            _apply_zmi(chapter, image_xml)
            chapter.save_xml()

            image_model = "tsqn.zimageturbo"
            # finally generate the new image.
            image_module = t2i_registry.get(image_model)
            image_module().generate_image(image_xml)
            chapter.save_xml()

    return htmx.prompt_panel(chapter, image_xml), 200


# /{{chapterurl}}/images/action/apply_zmi
@bp.route("/<int:image_index>/actions/apply_zmi", methods=["POST"])
def apply_zmi(author, title, chapter_number, language, image_index=0):
    log.info(f"{request.form}")
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)
    image_index = int(image_index)

    image_xml = chapter.get_image(image_index)
    _apply_zmi(chapter, image_xml)
    chapter.save_xml()

    image_model = "tsqn.zimageturbo"

    # finally generate the new image.
    image_module = t2i_registry.get(image_model)
    image_module().generate_image(image_xml)
    chapter.save_xml()

    return htmx.prompt_panel(chapter, image_xml), 200


@bp.route("/<int:image_index>/actions/create_fanciful_prompt", methods=["POST"])
def create_fanciful_prompt(author, title, chapter_number, language, image_index=0):
    log.info(f"{request.form}")
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)
    image_index = int(image_index)

    image_xml = chapter.get_image(image_index)
    set_fancyprompt(chapter, image_xml)

    chapter.save_xml()

    # return "", 200

    return htmx.prompt_panel(chapter, image_xml), 200


@bp.route("/<int:image_index>/actions/create_t5_prompt", methods=["POST"])
def create_t5_prompt(author, title, chapter_number, language, image_index=0):
    log.info(f"{request.form}")
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)
    image_index = int(image_index)

    image_xml = chapter.get_image(image_index)
    paragraph = image_xml.find_parent("paragraph")

    prompt_fn = os.path.abspath(
        os.path.join(
            const.LIBRARY_DIR,
            paragraph.attrs["dir"],
            f"img_{image_xml.attrs["fragdex"]}.prompt",
        )
    )

    text_to_next = get_text_to_next(
        image_xml=image_xml, next_image_xml=image_xml.find_next_sibling("image")
    )

    # use meta_prompt combined with the text to try and generate an image prompt.
    # this is cringe, it drops most of the provided detail and makes up stupid stuff.
    paragraph_text = get_surrounding_paragraphs(paragraph)

    t5_prompt = llm.trigger_llm_task_str(
        "text_to_image_prompt",
        prompt_fn,  # prompt_fn
        text_to_next,  # text,
        paragraph_text,  # paragraph_text
        "",  # meta_prompt
    )

    clip_prompt = image_xml.attrs.get("clip_prompt", "")

    with open(prompt_fn, "w") as h:
        h.write(
            json.dumps(
                {
                    "t5_prompt": t5_prompt,
                    "clip_prompt": clip_prompt,
                }
            )
        )

    image_xml.attrs["prompt"] = t5_prompt
    image_xml.attrs["t5_prompt"] = t5_prompt
    image_xml.attrs["clip_prompt"] = clip_prompt

    chapter.save_xml()

    # return "", 200

    return htmx.prompt_panel(chapter, image_xml), 200


@bp.route("/<int:image_index>/actions/enhance_prompt", methods=["POST"])
def enhance_prompt(author, title, chapter_number, language, image_index=0):
    log.info(f"{request.form}")
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)
    image_index = int(image_index)

    image_xml = chapter.get_image(image_index)
    paragraph = image_xml.find_parent("paragraph")

    # image.attrs["t5_prompt"] = request.form["prompt"]
    t5_prompt = image_xml.attrs["t5_prompt"]

    # use meta_prompt combined with the text to try and generate an image prompt.
    # this is cringe, it drops most of the provided detail and makes up stupid stuff.
    prompt = images.prompt_enhance(
        prompt=t5_prompt,
        prompt_fn=os.path.abspath(
            os.path.join(
                const.LIBRARY_DIR,
                paragraph.attrs["dir"].lstrip("/"),
                f"img_{image_xml.attrs["fragdex"]}.prompt",
            )
        ),
    )
    image_xml.attrs["t5_prompt"] = prompt

    chapter.save_xml()

    return htmx.image_strip_centerpiece(chapter, image_xml, default="image")


@bp.route("/<int:image_index>/actions/set_meta_prompt", methods=["POST"])
def set_meta_prompt(author, title, chapter_number, language, image_index=0):
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)
    image_index = int(image_index)

    image_xml = chapter.get_image(image_index)

    image_xml.attrs["meta_prompt"] = request.form.get("meta_prompt", "")
    chapter.save_xml()

    return htmx.meta_prompt(chapter, image_xml, image_index), 200


# # /W.%20W.%20Jacobs/The%20Monkeys%20Paw/0001/images/4/scene_characters
# @bp.route("/<int:image_index>/scene_characters", methods=["PUT"])
# def scene_characters(author, title, chapter_number, language, image_index=0):
#     log.info(f"{request.form}")
#     author = Author(author)
#     chapter = Chapter(author, title, chapter_number, language)

#     image_xml = chapter.get_image(image_index)

#     image_xml.attrs["scene_characters"] = ",".join(
#         request.form.getlist("scene_characters", str)
#     )

#     # image.attrs["scene_characters"] = request.form.get("scene_characters", "")
#     chapter.save_xml()

#     return htmx.characters_section(
#         chapter=chapter,
#         image_index=image_index,
#         image_xml=image_xml,
#     ), 200


# @bp.route("/<int:image_index>/actions/generate_scene_characters", methods=["POST"])
# def generate_scene_characters(author, title, chapter_number, language, image_index=0):
#     log.info(f"{request.form}")
#     author = Author(author)
#     chapter = Chapter(author, title, chapter_number, language)

#     image_xml = chapter.get_image(image_index)

#     images.generate_metadata_scene_characters(chapter, image_xml, force=True)

#     chapter.save_xml()

#     return htmx.characters_section(
#         chapter=chapter,
#         image_index=image_index,
#         image_xml=image_xml,
#     ), 200


@bp.route("/<int:image_index>/save_meta_prompt", methods=["POST"])
def save_meta_prompt(author, title, chapter_number, language, image_index=0):
    log.info(f"{request.form}")
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)
    image_index = int(image_index)

    image_xml = chapter.get_image(image_index)

    if request.form["button"] in ["Save"]:
        for key in request.form.keys():
            values = request.form.getlist(key)
            # if the key is "scene_characters", we want to join the values with a comma
            # otherwise, we just want the first value
            log.info(f"Processing {key}={values}")
            if key in [
                "scene_characters",
            ]:
                image_xml.attrs[key] = ",".join(values)

            elif key not in ["button"]:
                image_xml.attrs[key] = values[0]

        chapter.save_xml()

    elif request.form["button"] == "Copy Previous":
        # copy the previous image's meta fields, so we can adjust to fit the 'new' shot insead of statrting from scratch.
        prev_image = chapter.get_image(image_index - 1) if image_index > 0 else None

        log.info(f"Copying from {prev_image.attrs} to {image_xml.attrs}")
        for tag in [
            "tod",
            "camera",
            "setting",
            "scene_characters",
            "focus_character",
            "meta_prompt",
        ]:
            if tag in prev_image.attrs:
                image_xml.attrs[tag] = prev_image.attrs[tag]

        for character in prev_image.attrs.get("scene_characters", "").split(","):
            for tag in ["action", "pose", "location"]:
                character_attribute = f"{character}_{tag}"
                if character_attribute in prev_image.attrs:
                    image_xml.attrs[character_attribute] = prev_image.attrs[
                        character_attribute
                    ]

        chapter.save_xml()

    # TODO: this redirect is wrong
    return redirect(f"/image/{image_index}", code=302)


# http://localhost:5000/H.%20P.%20Lovecraft/Cool%20Air/0001/images/forex/5
@bp.route("/forex/<int:image_index>")
def htmx_forex(author, title, chapter_number, language, image_index=0):
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)

    all_images = chapter.get_xml().findAll("image")
    image_xml = all_images[image_index]

    image_forex = images.imageForex(chapter, image_xml, image_index)

    response = make_response(
        f"""<div id="forex">
        {image_forex}
    </div>""",
        200,
    )

    response.headers["HX-Push-Url"] = (
        f"/{chapter.url}/{chapter.language}/images/{image_index}/"
    )
    return response


# http://localhost:5000/L.%20Frank%20Baum/The%20Marvelous%20Land%20of%20Oz/images/0/actions/use_uploaded_image
@bp.route("/<int:image_index>/actions/use_uploaded_image", methods=["POST"])
def use_uploaded_image(author, title, chapter_number, language, image_index=0):
    log.info(f"{request.form}")
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)

    image = chapter.get_xml().findAll("image")[image_index]

    filename = request.form.get("filename", "")
    if not filename:
        return "No filename provided.", 400

    asset_path = os.path.join(
        const.LIBRARY_DIR, chapter.chapterdir, "..", "..", "assets", filename
    )

    if not os.path.exists(asset_path):
        return f"File {filename} not found.", 404

    paragraph = image.find_parent("paragraph")
    paragraph_dir = paragraph.attrs["dir"]

    # copy the file into the paragraph directory with a new name based on fragdex
    fragdex = image.attrs["fragdex"]
    new_filename = f"img_{fragdex}_{filename}"
    new_filepath = os.path.join(const.LIBRARY_DIR, paragraph_dir, new_filename)

    shutil.copy2(asset_path, new_filepath)
    log.info(f"Copied {asset_path} to {new_filepath}")

    # update the image src to point to the new file
    image.attrs["src"] = new_filename
    chapter.save_xml()

    response = make_response(
        htmx.image_strip_centerpiece(chapter, image, default="image")
    )
    response.headers["HX-Refresh"] = "true"

    return response


# http://localhost:5000/L.%20Frank%20Baum/The%20Marvelous%20Land%20of%20Oz/images/assets/image043.jpg
# @bp.route("/<author>/<path:title>/images/assets/<path:filename>")
# def serve_book_asset_image(author, title, filename):
#     bookdir = get_bookdir(author, title)
#     asset_dir = os.path.join(const.LIBRARY_DIR, bookdir, "assets")
#     log.info(f"Serving book asset image from {asset_dir}/{filename}")
#     return send_from_directory(asset_dir, filename)


# TODO: fixme
# /{chapterurl}/sources/{image_file}


@bp.route("/sources/<image_file>")
def serve_source_image(author, title, chapter_number, language, image_file):
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)

    source_dir = os.path.join(
        const.LIBRARY_DIR,
        chapter.chapterdir,
        "sources",
    )
    log.info(f"Serving source image from {source_dir}")
    if os.path.exists(os.path.join(source_dir, image_file)):
        return send_from_directory(source_dir, os.path.basename(image_file))
    else:
        return f"Source image {image_file} not found.", 404


# POST /Bible/Old%20Testament/0001/images/1/actions/set_image_source
@bp.route("/<int:image_index>/actions/set_image_source", methods=["POST"])
def set_image_source(author, title, chapter_number, language, image_index=0):
    """
    For choosing an image from /sources and bringing a copy of it in as the
    current image.
    """
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)
    source_dir = os.path.join(const.LIBRARY_DIR, chapter.chapterdir, "sources")

    image_source = request.form.get("image_source", "")
    image_filename = f"img_{image_index}_{os.path.basename(image_source)}"

    image_xml = chapter.get_xml().find("image", {"index": str(image_index)})
    paragraph = image_xml.parent

    s = os.path.join(source_dir, os.path.basename(image_source))
    d = os.path.join(const.LIBRARY_DIR, paragraph.attrs["dir"], image_filename)
    shutil.copyfile(s, d)
    log.info(f"Copied source image from {s} to {d}")

    image_xml.attrs["src"] = image_filename
    chapter.save_xml()

    response = make_response("<div></div>", 200)
    response.headers["HX-Refresh"] = "true"
    return response


@bp.route("/<int:image_index>/actions/copy_previous", methods=["POST"])
def copy_previous(author, title, chapter_number, language, image_index=0):
    log.info(
        f"Copying previous prompts for {author}/{title}/{chapter}/images/{image_index}"
    )
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)
    image_index = int(image_index)

    image_xml = chapter.get_image(image_index)
    previous_image = chapter.get_image(image_index - 1) if image_index > 0 else None

    if previous_image:
        image_xml.attrs["clip_prompt"] = previous_image.attrs.get("clip_prompt", "")
        image_xml.attrs["t5_prompt"] = previous_image.attrs.get("t5_prompt", "")
    chapter.save_xml()

    return htmx.prompt_panel(chapter, image_xml), 200


# rightsize_t5_prompt
@bp.route("/<int:image_index>/actions/rightsize_t5_prompt", methods=["POST"])
def htmx_rightsize_t5_prompt(author, title, chapter_number, language, image_index):
    """
    This is called by the prompt textarea to set the prompt for an image.
    """
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)
    image_index = int(image_index)

    log.info(f"Rightsizing T5 prompt for {chapter.chapterurl}/images/{image_index}")
    image_xml = chapter.get_image(image_index)

    t5_prompt = image_xml.attrs.get("t5_prompt", "")
    if not t5_prompt:
        return "No T5 prompt to rightsize.", 400

    # we don't even _need_ the tokens, just the length.
    tokenizer = T5Tokenizer.from_pretrained("t5-small")
    tokens = tokenizer.tokenize(t5_prompt)
    log.info(f"T5 prompt is {len(tokens)} tokens long.")

    adjust_hint = ""
    if len(tokens) > MAX_TOKENS:
        adjust_hint = f"This promopt is too long.  It is {len(tokens)} tokens long.  We need to reduce it to {MAX_TOKENS} tokens or less.\n"
    elif len(tokens) < MIN_TOKENS:
        adjust_hint = f"This prompt is too short.  It is {len(tokens)} tokens long.  We need to expand it to at least {MIN_TOKENS} tokens.\n"

    prompt = f"""
        We need to rewrite this:
                                    
        ```
        {t5_prompt}
        ```
        {adjust_hint}
        This needs to be rewritten as a description of an image that we want to draw.  Our description 
        needs to be shorter than {MAX_TOKENS} tokens and include as much of the provided description as possible.
        
        Return only the rewritten description, with no additional commentary.
    """

    new_t5_prompt = llm.str_prompt(prompt)

    log.info(f"New T5 prompt: {new_t5_prompt}")

    if len(new_t5_prompt) < 80:
        return "Failed to rightsize T5 prompt.", 500

    if len(new_t5_prompt) > 500:
        return "Failed to rightsize T5 prompt.", 500

    image_xml.attrs["t5_prompt"] = new_t5_prompt
    chapter.save_xml()

    return htmx.prompt_panel(chapter, image_xml), 200


@bp.route("/<int:image_index>/actions/set_t5_prompt", methods=["POST"])
def htmx_set_t5_prompt(author, title, chapter_number, language, image_index):
    """
    This is called by the prompt textarea to set the prompt for an image.
    """
    chapterurl = get_chapterurl(author, title, chapter)

    log.info(f"Setting prompt for {chapterurl}/images/{image_index}")
    new_prompt = request.form.get("t5_prompt", "")
    log.info(f"New prompt: {new_prompt}")

    chapterdir = get_chapterdir(author, title, chapter)
    chapterurl = get_chapterurl(author, title, chapter)
    mybook = booklib.get_book(chapterdir)

    all_images = mybook.soup.findAll("image")
    image_xml = all_images[image_index]

    image_xml.attrs["t5_prompt"] = new_prompt

    if "index" not in image_xml.attrs:
        images.assign_fragdex_and_index(mybook.soup)

    mybook.save_xml()
    return htmx.prompt_panel(chapterdir, chapterurl, image_xml), 200


@bp.route("/<int:image_index>/animation/<int:video_index>/set_method", methods=["GET"])
def set_animation_method(
    author, title, chapter_number, language, image_index, video_index
):
    """
    This is called by the animation selector to set the animation method for an image.
    """
    log.info(
        f"Setting animation method for {author}/{title}/{chapter_number}/images/{image_index}"
    )

    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)
    image_index = int(image_index)
    video_index = int(video_index)

    new_animation_method = request.args.get("animation_method", "")
    log.info(f"New animation method: {new_animation_method}")

    image_xml = chapter.get_image(image_index)

    video_tag = f"_{video_index:02d}"

    if new_animation_method:
        image_xml.attrs[f"animation_method{video_tag}"] = new_animation_method

        animation_module = animation_registry.get(new_animation_method)
        if animation_module is None:
            log.error(f"Animation module {new_animation_method} not found in registry.")
            return "Animation method not found.", 400

        image_xml.attrs[f"workflow_animation_template{video_tag}"] = (
            animation_module.workflow_animation_template
        )
        image_xml.attrs[f"animation_mode{video_tag}"] = animation_module.mode

        # {
        #     "comfy_ui_flf2v": ("LTX23_flf-LTX 2.3 First and Last Frame", "flf2v"),
        #     "comfy_ui_i2v": ("LTX23-LTX 2.3", "i2v")
        # }[new_animation_method]
        chapter.save_xml()

    return htmx.get_animation_configuration_widgets(
        chapter=chapter,
        image_index=image_index,
        video_index=video_index,
    )


@bp.route("/strip/<int:image_index>", methods=["GET"])
def image_strip(author, title, chapter_number, language, image_index=0):
    author = Author(author)
    chapter = Chapter(
        author=author, title=title, number=chapter_number, language=language
    )

    log.info(
        f"Generating image strip for {author}/{title}/{chapter}/images/{image_index}"
    )

    try:
        image_xml = chapter.findAll("image")[image_index]
    except IndexError:
        log.error(
            f"Image index {image_index} out of range for chapter {chapter.chapterdir}"
        )
        return "Image not found", 404

    response = make_response(htmx.image_strip(chapter, image_xml))

    # so a browser refresh will work correctly
    response.headers["HX-Push-Url"] = (
        f"/{chapter.url}/{chapter.language}/images/{image_index}/"
    )
    return response


@bp.route("/<int:image_index>/actions/build_animation", methods=["POST"])
def build_animation(author, title, chapter_number, language, image_index=0):
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)
    image_index = int(image_index)

    image_xml = chapter.get_image(image_index)
    image_xml.attrs["animation_method"] = request.form.get(
        "animation_method", "wan_2_2_5b"
    )
    image_xml.attrs["animation"] = "true"
    image_xml.attrs["animation_prompt"] = request.form.get("prompt", "")
    chapter.save_xml()

    log.info(f"Invoking images.create_animation({image_xml=})")
    filename = images.create_animation(chapter, image_xml, force=True)

    log.info(f"Created animation {filename} for image {image_index} in {chapter}")

    # add the tabpanel wrapper and activate the animation tab
    value = htmx.image_strip_centerpiece(chapter, image_xml, default="animation")
    chapter.save_xml()
    return value


@bp.route("/<int:image_index>/actions/animation", methods=["POST"])
def enable_animation(author, title, chapter_number, language, image_index=0):
    chapterdir = get_chapterdir(author, title, chapter)
    chapterurl = get_chapterurl(author, title, chapter)

    mybook = booklib.get_book(chapterdir)
    image_xml = mybook.soup.findAll("image")[image_index]

    if "animation" not in image_xml.attrs:
        image_xml.attrs["animation"] = "true"
        mybook.save_xml()
        log.info(f"Enabled animation for image {image_index} in {chapterdir}")

    # add the tabpanel wrapper and activate the animation tab
    value = htmx.image_strip_centerpiece(chapter, image_xml, default="animation")
    mybook.save_xml()
    return value


# POST /Aesop/Fables/0024/images/4/actions/extend_animation
@bp.route("/<int:image_index>/actions/extend_animation", methods=["POST"])
def extend_animation(author, title, chapter_number, language, image_index=0):
    """
    Extend existing animation by adding more frames

    We're taking the last frame and adding more frames based on that and the prompt/neg prompt.
    """
    log.info("Extending animation for image %s in chapter %s", image_index, chapter)
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)
    image_index = int(image_index)

    image_xml = chapter.get_image(image_index)

    log.info(f"Invoking images.extend_animation({image_index=}, {chapter=})")
    filename = images.create_animation(chapter, image_xml, extend=True)

    log.info(f"Extended animation {filename} for image {image_index} in {chapter}")

    value = htmx.animation_workshop(
        image_xml=image_xml,
        chapter=chapter,
    )

    return value


@bp.route(
    "/<int:image_index>/actions/<video_index>/delete_animation", methods=["DELETE"]
)
def delete_animation(
    author, title, chapter_number, language, image_index=0, video_index=0
):
    """
    Delete all animation frames for this image
    """
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)
    image_index = int(image_index)
    video_index = int(video_index)

    image_xml = chapter.get_image(image_index)
    paragraph = image_xml.find_parent("paragraph")

    animation_dir = os.path.join(
        const.LIBRARY_DIR,
        chapter.get_paragraph_dir(paragraph.attrs["index"]),
        "animation",
        f"image_{image_index:06d}_{video_index:02d}",
    )

    if os.path.exists(animation_dir):
        for image_pfn in os.listdir(animation_dir):
            os.unlink(os.path.join(animation_dir, image_pfn))

    # and the video file (if there is one)
    video_pfn = os.path.join(
        const.LIBRARY_DIR,
        chapter.get_paragraph_dir(paragraph.attrs["index"]),
        image_xml.attrs["src"].replace(".png", ".mp4"),
    )
    if os.path.exists(video_pfn):
        log.info(f"Deleting video file {video_pfn}")
        os.unlink(video_pfn)
    else:
        log.info(f"Video file {video_pfn} does not exist.")

    # if there is a comfyui output file we need to delete that too
    # or it will be auto-imported back in.
    last_index = int(image_xml.attrs.get("animation_count", "1"))
    for video_index in range(last_index):
        video_tag = f"_{video_index:02d}"

        workflow_template = image_xml.attrs.get(
            f"workflow_animation_template{video_tag}", ""
        )
        all_mp4_filenames = glob.glob(
            os.path.join(
                const.COMFY_DIRS["artifactserver"]["OUTPUT_DIR"],
                f"{chapter.nice}_img_{image_xml.attrs['index']}_{workflow_template}*_00001_.mp4",
            )
        )

        for mp4_filename in all_mp4_filenames:
            if os.path.exists(mp4_filename):
                log.info(f"Deleting comfyui output video file {mp4_filename}")
                os.unlink(mp4_filename)

    value = htmx.image_strip_centerpiece(chapter, image_xml)
    chapter.save_xml()
    return value


class SafeAttrsOnly(Filter):
    def __init__(self, **options):
        super().__init__(**options)

    def filter(self, lexer, stream):
        log.info(f"Filtering stream with options: {self.options}", lexer=lexer)
        for ttype, value in stream:
            log.info("Filtering", ttype=ttype, value=value)
            if hasattr(value, "attrs"):
                value.attrs = {
                    k: v
                    for k, v in value.attrs.items()
                    if k in self.options["safe_attrs"]
                }
            yield ttype, value


@bp.route("/surrounding_text/<int:image_index>", methods=["GET"])
def surrounding_text(author, title, chapter_number, language, image_index):
    """
    Get the text surrounding the image at image_index, with a highlight on the image's
    paragraph.  Called by chooseImage()
    """
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)
    image_index = int(image_index)

    image_xml = chapter.get_image(image_index)

    paragraph = image_xml.find_parent("paragraph")

    safe_attrs = ["index", "highlight"]

    stripped_paragraph = copy.deepcopy(paragraph)
    stripped_paragraph.attrs = {
        k: v for k, v in paragraph.attrs.items() if k in safe_attrs
    }

    depth = 0
    pre_highlight = [f'<paragraph index="{paragraph.attrs.get("index", "")}">\n']
    highlight = []
    post_highlight = []
    in_pre = True
    in_post = False
    in_highlight = False

    for tag in stripped_paragraph.findAll(True):
        depth = 1
        log.info(f"Processing tag {tag} with attributes {tag.attrs}")

        if hasattr(tag, "attrs"):
            log.info("stripping attributes from tag %s: %s", tag.name, tag.attrs)
            for attr in list(tag.attrs.keys()):
                if attr not in safe_attrs:
                    del tag.attrs[attr]
        else:
            log.info("tag %s has no attributes", tag.name)

        if tag.name == "image":
            # this is the _right_ image
            if tag.attrs.get("index", "") == str(image_index):
                highlight.append("  " * depth + str(tag) + "\n")
                # highlight.append("  " * depth + str(tag) + "\n")
                in_pre = False
                in_highlight = True

            else:
                if in_pre:
                    pre_highlight.append("  " * depth + str(tag) + "\n")
                else:
                    # sorry love, if you're an image object and you're the wrong
                    # one, and you're not in_pre, you've got to be in post.
                    in_post = True
                    in_highlight = False
                    in_pre = False
                    post_highlight.append("  " * depth + str(tag) + "\n")

        else:
            wrapped = textwrap.wrap(
                tag.get_text().strip(),
                width=80,
                initial_indent="  " * (depth + 1),
                subsequent_indent="  " * (depth + 1),
                drop_whitespace=True,
            )
            tag.string = "\n" + "\n".join(wrapped) + "\n" + "  " * depth

            if in_highlight:
                highlight.append("  " * depth + str(tag) + "\n")
            elif in_pre:
                pre_highlight.append("  " * depth + str(tag) + "\n")
            elif in_post:
                post_highlight.append("  " * depth + str(tag) + "\n")

    # pretty_paragraph.append("  " * depth + str(tag) + "\n")

    post_highlight.append("</paragraph>")

    lexer = HtmlLexer()
    # lexer.add_filter(SafeAttrsOnly(), safe_attrs=safe_attrs)

    # text_with_highlight = get_surrounding_paragraphs(paragraph, with_tags=True)
    muted_formatter = HtmlFormatter(
        full=False, style="material", cssclass="muted", classprefix="m"
    )
    highlight_formatter = HtmlFormatter(
        full=False, style="colorful", cssclass="highlight"
    )

    text_with_highlight = format(lex("".join(pre_highlight), lexer), muted_formatter)

    text_with_highlight += format(lex("".join(highlight), lexer), highlight_formatter)

    text_with_highlight += format(lex("".join(post_highlight), lexer), muted_formatter)
    # next_image = chapter.get_image(image_index + 1)

    # text_to_next = get_text_to_next(image_xml=image_xml, next_image_xml=next_image)

    # # standardize whitespace, this isn't viewer facing.
    # text_to_next = tools.tightstring(text_to_next)

    # if text_to_next in text_with_highlight:
    #     # I know, this won't catch the second occurance of a repeated word.
    #     # TODO: fix this.  one day.

    #     text_with_highlight = text_with_highlight.replace(
    #         text_to_next, '<p class="highlight">' + text_to_next + "</p>", 1
    #     )
    #     # text_with_highlight = "<div class='#editor'></div>"
    # else:
    #     log.error(f"Could not find '{text_to_next}' in '{text_with_highlight}'")

    # can you inject css?
    # formatter = HtmlFormatter(full=True, style="colorful")

    return text_with_highlight, 200


@bp.route("/<int:image_index>", methods=["POST"])
def create_new_image(
    author, title, chapter_number, language, image_index=0, styled=False, force=True
):
    """
    Generate a NEW Image, and make it the new selected image.
    """
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)
    image_index = int(image_index)

    styled = styled or request.args.get("styled", "false").lower() == "true"

    loras = []
    lora = request.form.get("lora", "")
    if lora:
        # future downstream support for multiple loras.
        loras = [
            lora.strip(),
        ]

    prompt = ""
    clip_prompt = ""

    image_xml = chapter.get_image(image_index)
    image_xml.attrs["loras"] = json.dumps(loras)

    image_model = image_xml.attrs.get("t2i", "flux.schnell")
    paragraph = image_xml.find_parent("paragraph")

    if styled:
        # styled means they want us to apply the current style before we render the image.
        prompt = image_xml.attrs.get("prompt", "")
        # does the image itself have a style?  if so, we use that.
        style = image_xml.attrs.get("style", "")

        if style in ["", None]:
            # no?  okay, does the chapter have a style?
            style = chapter.config.get("default_style", "")

        if style in ["", None]:
            # still no style?  okay, does the book have a style?
            book_config = config.get_config(chapter.bookdir)
            style = book_config.get("default_style", "")

        if style in ["", None]:
            # still no style?  whatever asshole.  We can't style
            # a prompt if we dont't have a style.  Jerk.
            # fall through to the unstyled prompt
            pass
        else:
            prompt_filter, negative_prompt = styles.get_style(style)
            if prompt_filter:
                prompt = prompt_filter.format(prompt=prompt)
                image_xml.attrs["styled_prompt"] = prompt
                image_xml.attrs["styled"] = True
                image_xml.attrs["negative_prompt"] = negative_prompt
    else:
        image_xml.attrs["styled"] = False

    image_module = t2i_registry.get(image_model)
    # this will be the UI side of the t2i module, 'generate_image' will place an entry
    # in a redis queue.  It will also block, until the image is generated.
    image_module(chapter).generate_image(chapter.key, image_xml)
    chapter.save_xml()

    # record the prompt that we used for this image, next to the image.
    prompt_src = os.path.join(
        const.LIBRARY_DIR,
        chapter.get_paragraphdir(paragraph.attrs["index"]),
        os.path.splitext(os.path.basename(image_xml.attrs["src"]))[0] + ".prompt",
    )

    with open(prompt_src, "w") as f:
        f.write(json.dumps({"prompt": prompt, "clip_prompt": clip_prompt}))

    if request.form.get("tab", "") == "prompt":
        return htmx.prompt_panel(chapter, image_xml, with_class="pulse-green"), 201
    else:
        return "", 201


@bp.route("/<int:image_index>/action/swap_with_previous_image", methods=["POST"])
def swap_with_previous_image(author, title, chapter_number, language, image_index=0):
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)
    image_index = int(image_index)

    image_xml = chapter.get_image(image_index)
    previous_image_xml = chapter.get_image(image_index - 1)

    # swap the src attributes of the two images
    current_src = image_xml.attrs["src"]
    previous_src = previous_image_xml.attrs["src"]

    # and some renaming, so the 'selector' works correctly, because it relies on
    # filenames instead of using a directory like a sane person would.
    # FIXME
    previous_index = previous_image_xml.attrs["src"].split("_")[1]
    current_index = image_xml.attrs["src"].split("_")[1]

    assert image_xml.attrs["src"] == "img_" + current_index + "_" + "_".join(
        image_xml.attrs["src"].split("_")[2:]
    )

    # image_12_0_abcdef.png (where 12 is the 'index' that needs to reflect the destination image index)
    new_current_src = (
        "img_" + previous_index + "_" + "_".join(image_xml.attrs["src"].split("_")[2:])
    )
    new_previous_src = (
        "img_"
        + current_index
        + "_"
        + "_".join(previous_image_xml.attrs["src"].split("_")[2:])
    )

    image_xml.attrs["src"] = new_current_src
    previous_image_xml.attrs["src"] = new_previous_src

    # thats cute, you thought that was enough?
    current_paragraph = image_xml.find_parent("paragraph")
    previous_paragraph = previous_image_xml.find_parent("paragraph")

    current_paragraph_dir = chapter.get_paragraph_dir(current_paragraph.attrs["index"])
    previous_paragraph_dir = chapter.get_paragraph_dir(
        previous_paragraph.attrs["index"]
    )

    current_image_path = os.path.join(const.LIBRARY_DIR, current_paragraph_dir)
    previous_image_path = os.path.join(const.LIBRARY_DIR, previous_paragraph_dir)

    c_to_p_source = os.path.join(current_image_path, os.path.basename(current_src))
    c_to_p_destination = os.path.join(
        previous_image_path, os.path.basename(new_current_src)
    )

    log.info("Copying %s to %s", c_to_p_source, c_to_p_destination)

    shutil.copy2(c_to_p_source, c_to_p_destination)

    p_to_c_source = os.path.join(previous_image_path, os.path.basename(previous_src))
    p_to_c_destination = os.path.join(
        current_image_path, os.path.basename(new_previous_src)
    )

    log.info("Copying %s to %s", p_to_c_source, p_to_c_destination)

    # copy previous image to current paragraph directory
    shutil.copy2(p_to_c_source, p_to_c_destination)
    log.info(f"Cross Copied {current_image_path} and {previous_image_path}")

    chapter.save_xml()
    return "", 200


@bp.route("/<int:image_index>/viewer", methods=["GET"])
def tab_pre_viewer(author, title, chapter_number, language, image_index):
    return base(author, title, chapter_number, language, image_index, override="viewer")


@bp.route("/<int:image_index>/selector", methods=["GET"])
def tab_pre_selector(author, title, chapter_number, language, image_index):
    return base(
        author, title, chapter_number, language, image_index, override="selector"
    )


@bp.route("/<int:image_index>/editor", methods=["GET"])
def tab_pre_editor(author, title, chapter_number, language, image_index):
    return base(author, title, chapter_number, language, image_index, override="editor")


@bp.route("/<int:image_index>/scene", methods=["GET"])
def tab_pre_scene(author, title, chapter_number, language, image_index):
    return base(author, title, chapter_number, language, image_index, override="scene")


@bp.route("/<int:image_index>/prompt", methods=["GET"])
def tab_pre_prompt(author, title, chapter_number, language, image_index):
    return base(author, title, chapter_number, language, image_index, override="prompt")


@bp.route("/<int:image_index>/upload", methods=["GET"])
def tab_pre_upload(author, title, chapter_number, language, image_index):
    return base(author, title, chapter_number, language, image_index, override="upload")


@bp.route("/<int:image_index>/citation", methods=["GET"])
def tab_pre_citation(author, title, chapter_number, language, image_index):
    return base(
        author, title, chapter_number, language, image_index, override="citation"
    )


@bp.route("/<int:image_index>/", methods=["GET"])
def base(author, title, chapter_number, language, image_index=0, override=None):
    image_index = int(image_index)

    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)
    image_index = int(image_index)

    image_xml = chapter.get_image(image_index)

    if "/" in image_xml.attrs.get("src", ""):
        log.warning(f"Image src contains a path: {image_xml.attrs['src']}")
        image_xml.attrs["src"] = os.path.basename(image_xml.attrs["src"])
        chapter.save_xml()

    max_index = int(chapter.max_image_index())

    if image_index > max_index:
        return redirect(f"/{max_index}", code=302)

    image_forex = images.imageForex(
        chapter,
        image_xml,
        image_index,
    )

    # log.debug(f"{image_forex=}")

    pretty_author = chapter.config.get("author", chapter.author.pretty_name)
    pretty_title = chapter.config.get("title", title)

    text_with_highlight = surrounding_text(
        author.name,
        chapter.title,
        chapter.number,
        chapter.language,
        image_index,
    )[0]

    muted_formatter = HtmlFormatter(
        full=False, style="material", cssclass="muted", classprefix="m"
    )
    highlight_formatter = HtmlFormatter(
        full=False, style="colorful", cssclass="highlight"
    )
    css_styles = muted_formatter.get_style_defs() + highlight_formatter.get_style_defs()

    log.info("Rendering images.html template...")
    return render_template(
        "images.html",
        override=override,
        css_styles=css_styles,
        language=language,
        pretty_language=language.capitalize(),
        author=author,
        pretty_author=pretty_author,
        title=title,
        pretty_title=pretty_title,
        chapter=chapter,
        chapterurl=chapter.url,
        image_index=image_index,
        section="images",
        section_cosmetic="Images",
        image_forex=image_forex,
        draw_all_missing_images_button=htmx.draw_all_missing_images_button(chapter),
        draw_all_missing_tmi_images_button=htmx.draw_all_missing_tmi_images_button(
            chapter.url
        ),
        generate_missing_image_metadata_button=htmx.generate_missing_image_metadata_button(
            chapter.url
        ),
        text_with_highlight=text_with_highlight,
        image_strip=htmx.image_strip(
            chapter=chapter,
            image_xml=image_xml,
        ),
        image_metadata=selector_htmx.image_metadata(
            chapter=chapter,
            image_xml=image_xml,
            src=image_xml.attrs.get("src", ""),
        ),
    )


# lets try a generic attribute updater
# PUT /Aesop/Fables/0024/image/4
@bp.route("/<int:image_index>", methods=["PUT"])
@bp.route("/<int:image_index>/", methods=["PUT"])
def update(author, title, chapter_number, language, image_index=0):
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)
    image_index = int(image_index)

    image_xml = chapter.get_image(image_index)

    for key in request.form:
        if key in [
            "respond_with",
        ]:
            continue

        log.info("Setting image attribute %s to %s", key, request.form[key])
        image_xml.attrs[key] = request.form[key]

    chapter.save_xml()

    # what is our return when we get a generic attribute update?
    respond_with = request.form.get("respond_with", "")
    if respond_with == "animation":
        return htmx.animation_workshop(
            image_xml=image_xml,
            chapter=chapter,
        )
    elif respond_with == "camera":
        return camera_htmx.camera_workshop(image_xml=image_xml, chapter=chapter)
    elif respond_with == "prompt":
        return htmx.prompt_panel(chapter, image_xml, with_class="pulse-green"), 200
    elif respond_with == "citation":
        return htmx.citation_panel(chapter, image_xml, force=True), 200
    else:
        return htmx.image_strip_centerpiece(chapter, image_xml)


# POST /Mark%20Twain/A%20Connecticut%20Yankee%20in%20King%20Arthurs%20Court/0001/images/3/action/fancy
@bp.route("/<int:image_index>/action/fancy", methods=["POST"])
def fancy_image(author, title, chapter_number, language, image_index):
    """
    Handle the "fancy" action for an image.
    """
    # we're junking the current prompt, using the
    # zimage "create fanciful prompt" feature
    # then apply the chapter style, then generate the image.
    # all stuff that works independantly, we just want to chain them together.
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)
    image_index = int(image_index)

    image_xml = chapter.get_image(image_index)

    # get the new prompt
    set_fancyprompt(chapter, image_xml)

    image_xml.attrs["t2i"] = "tsqn.zimageturbo"

    # apply the chapter style
    style = chapter.config.get("style", "")
    if style:
        log.info(
            f"Applying style {style} to prompt for image {image_xml.attrs.get('id')}"
        )
        prompt_filter, negative_prompt = styles.get_style(style)
        if prompt_filter:
            image_xml.attrs["prompt"] = prompt_filter.format(
                prompt=image_xml.attrs.get("prompt", "")
            )
        image_xml.attrs["negative_prompt"] = negative_prompt
    else:
        log.warning("No style found for chapter, skipping style application")
    # save in case there is a problem with 'generate_image'
    chapter.save_xml()

    # draw the image
    image_module = t2i_registry.get("tsqn.zimageturbo")
    image_module().generate_image(image_xml, force=True)
    chapter.save_xml()

    return "", 200


# http://localhost:5000/Oscar%20Wilde/The%20Picture%20of%20Dorian%20Gray/0001/images/actions/apply_fancy_here_to_end
@bp.route("/<int:image_index>/actions/apply_fancy_here_to_end", methods=["POST"])
def apply_fancy_here_to_end(author, title, chapter_number, language, image_index):
    log.info(f"{request.form}")
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)

    # apply the chapter style
    style = chapter.config.get("style", "")

    all_images = chapter.get_xml().findAll("image")
    for image_xml in all_images:
        if int(image_xml.attrs.get("index", -1)) >= image_index:
            # build the prompt if there isn't a prompt
            if "prompt" not in image_xml.attrs or image_xml.attrs["prompt"] in [
                "",
                None,
            ]:
                set_fancyprompt(chapter, image_xml)
                if "src" in image_xml.attrs:
                    # new prompt, new image
                    del image_xml.attrs["src"]

                image_xml.attrs["t2i"] = "tsqn.zimageturbo"

                # wrap in the selected chapter style, if there is one.
                if style:
                    log.info(
                        f"Applying style {style} to prompt for image {image_xml.attrs.get('id')}"
                    )
                    prompt_filter, negative_prompt = styles.get_style(style)
                    if prompt_filter:
                        image_xml.attrs["prompt"] = prompt_filter.format(
                            prompt=image_xml.attrs.get("prompt", "")
                        )
                    image_xml.attrs["negative_prompt"] = negative_prompt
                else:
                    log.warning(
                        "No style found for chapter, skipping style application: %s",
                        chapter.config,
                    )

            if (
                "src" not in image_xml.attrs
                or image_xml.attrs["src"] in ["", None]
                or not os.path.exists(
                    os.path.join(
                        const.LIBRARY_DIR,
                        chapter.get_paragraph_dir(
                            image_xml.find_parent("paragraph").attrs["index"]
                        ),
                        os.path.basename(image_xml.attrs["src"]),
                    )
                )
            ):
                # draw the image
                image_module = t2i_registry.get("tsqn.zimageturbo")
                image_module().generate_image(image_xml, force=True)
                chapter.save_xml()

    response = make_response("")
    response.headers["HX-Refresh"] = "true"
    return response


# copy_previous_character_action
# http://localhost:5000/L.%20Frank%20Baum/The%20Marvelous%20Land%20of%20Oz/0002/images/41/actions/copy_previous_character_action
@bp.route("/<int:image_index>/actions/copy_previous_character_action", methods=["POST"])
def copy_previous_character_action(
    author, title, chapter_number, language, image_index=0
):
    chapterdir = get_chapterdir(author, title, chapter)

    mybook = booklib.get_book(chapterdir)
    all_images = mybook.soup.findAll("image")

    image_xml = all_images[image_index]
    previous_image = image_xml.find_previous("image")

    character_name = request.form.get("character", "")

    if f"{character_name}_action" in previous_image.attrs:
        image_xml.attrs[f"{character_name}_action"] = previous_image.attrs.get(
            f"{character_name}_action", ""
        )
        mybook.save_xml()
        return "", 200
    else:
        return "No previous image found", 404


@bp.route("/<int:image_index>/action/choose_file", methods=["GET", "POST"])
def choose_file(author, title, chapter_number, language, image_index=0):
    path_fn = request.files["file"]
    if path_fn.filename == "":
        return redirect(request.url)

    chapterdir = get_chapterdir(author, title, chapter)

    mybook = booklib.get_book(chapterdir)
    mybook, image, paragraph, text_to_next = images.get_xml_for_image(
        chapterdir, mybook, image_index
    )

    image = mybook.soup.findAll("image")[image_index]

    # where should we put them?
    # Bible/Old Testament/01 Torah/01 Genesis/paragraphs/000000/img_2_A_de
    image_fn = os.path.join(paragraph.attrs["dir"], os.path.basename(path_fn.filename))
    # _without_ a leading /
    image.attrs["src"] = image_fn

    path_fn.save(os.path.join(const.LIBRARY_DIR, image_fn))

    mybook.save_xml()
    return "", 201


# http://localhost:8080/library/L.%20Frank%20Baum/The%20Marvelous%20Land%20of%20Oz/1/english/images/4/choose_video
@bp.route("/<int:image_index>/choose_video", methods=["GET", "POST"])
def choose_video(author, title, chapter_number, language, image_index=0):
    """
    Select a video (from comfyui output) to become the chosen video for this image_index/video_index.
    Any existing video _frames_ will be cleared.
    Any existing video _file_ will be renamed (so you can change your mind)
    """
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)
    image_index = int(image_index)
    image_xml = chapter.get_image(image_index)

    # request.form:
    #   selected_video=%7B%22title%22%3A%22aeso-fabl-031-twm6_img_8_LTX23_00_00004_.mp4%22%2C%22poster%22%3A%22comfy%2Foutput%2Faeso-fabl-031-twm6_img_8_LTX23_00_00004_.mp4.png%22%2C%22sources%22%3A%5B%5D%2C%22tracks%22%3A%5B%5D%7D
    #   &video_index=0

    # step 1: find the source file.
    comfyui_output_dir = const.COMFY_DIRS["artifactserver"]["OUTPUT_DIR"]
    selected_video = json.loads(request.form.get("selected_video", "{}"))
    # selected_video: {
    #     'title': '<basename of filename in /output>', 
    #     'poster': '<filename with relative path in comfy/output/>', 
    #     'sources': [], 
    #     'tracks': []
    # }
    video_index = int(request.form.get("video_index", "0"))
    video_filename = selected_video.get("title", "")

    video_source_fn = os.path.join(comfyui_output_dir, video_filename)
    if os.path.exists(video_source_fn):
        log.info(f"Found video source file {video_source_fn}")
        # we are golden.  Now we just need to put it in the right place.
    else:
        return f"Video source file {video_source_fn} not found", 404

    paragraph_dir = chapter.get_paragraph_dir(
        image_xml.find_parent("paragraph").attrs["index"]
    )
    # for now...
    if video_index == 0:
        video_destination_fn = os.path.join(
            const.LIBRARY_DIR,
            paragraph_dir,
            os.path.basename(image_xml.attrs["src"].replace(".png", ".mp4")),
        )
    else:
        video_tag = f"_{video_index:02d}"
        video_destination_fn = os.path.join(
            const.LIBRARY_DIR,
            paragraph_dir,
            os.path.basename(image_xml.attrs["src"].replace(".png", f"{video_tag}.mp4")),
        )

    if os.path.exists(video_destination_fn):
        log.info(f"Video destination file {video_destination_fn} already exists, renaming it.")
        # rename the existing video file to a backup name
        count = -1
        backup_fn = "does not exist"
        while count < 0 or os.path.exists(backup_fn):
            count += 1
            backup_fn = os.path.join(
                const.LIBRARY_DIR,
                paragraph_dir,
                os.path.basename(image_xml.attrs["src"].replace(".png", f"{video_index:02d}_{count:02d}.mp4")),
            )

        shutil.move(video_destination_fn, backup_fn)
        log.info(f"Renamed existing video file to {backup_fn}")

    # /0001/paragraphs/000003/img_4_zit-phrase-chain__00_00004__00.mp4
    # /0001/paragraphs/000003/baum-marv-001-bhgv_img_4_LTX23_flf__00_00001_.
    # mp4'
    # Oz/chapter/0001/paragraphs/000003/img_4_zit-phrase-chain__00_00004__00.mp4,

    shutil.copy(video_source_fn, video_destination_fn)
    log.info(f"Copied video from {video_source_fn} to {video_destination_fn}")

    # if there is a poster remove it.  Posters are .mp4.png
    try:
        os.unlink(
            os.path.join(
                const.LIBRARY_DIR,
                paragraph_dir,
                os.path.basename(video_filename) + ".png",
            )
        )
    except FileNotFoundError:
        pass

    # update image xml holy shit, it just doesn't care.
    # its a directory.
    frame_dir = os.path.join(
        const.LIBRARY_DIR,
        paragraph_dir,
        "animation",
        f"image_{image_index:06d}_{video_index:02d}",
    )

    if os.path.exists(frame_dir):
        log.info(f"Removing existing animation frame directory {frame_dir}")
        shutil.rmtree(frame_dir)
    
    # expand the new video into frames
    tools.extract_frames(video_destination_fn, frame_dir)

    # chapterdir = get_chapterdir(author, title, chapter_number)

    # mybook = booklib.get_book(chapterdir)
    # mybook, image, paragraph, text_to_next = images.get_xml_for_image(
    #     chapterdir, mybook, image_index
    # )

    # image = mybook.soup.findAll("image")[image_index]

    # # where should we put them?
    # # Bible/Old Testament/01 Torah/01 Genesis/paragraphs/000000/img_2_A_de
    # video_fn = os.path.join(paragraph.attrs["dir"], os.path.basename(path_fn.filename))
    # # _without_ a leading /
    # image.attrs["src"] = video_fn

    # path_fn.save(os.path.join(const.LIBRARY_DIR, video_fn))
    response = make_response("")
    response.headers["HX-Refresh"] = "true"

    return response


# @bp.route("/<int:image_index>/image.png", methods=["GET"])
# @bp.route("/<int:image_index>.png", methods=["GET"])

# /Grimm/Fairy%20Tales/1/english/audio/actions/add_delay/3


@bp.route("/<int:height>/<int:image_index>.png", methods=["GET"])
@cache.cached(timeout=600, query_string=True)
def show_image_by_index(
    author, title, chapter_number, language, height=0, image_index=0
):
    author = Author(author)
    chapter = Chapter(
        author=author,
        title=title,
        number=chapter_number,
        language=language,
    )
    image_index = int(image_index)

    image_xml = chapter.get_image(image_index)
    if not image_xml:
        return f"Image index {image_index} not found", 404

    log.info("Found image for image_index=%s", image_index)
    paragraph = image_xml.parent

    img_src = image_xml.attrs.get("src", "")
    if not img_src:
        log.warning(f"Image {image_xml} has no 'src'")
        img_src = "/static/images/x.png"
        image_fn = os.path.join(
            const.STATIC_DIR,
            "images",
            "x.png",
        )
    else:
        image_fn = os.path.join(
            const.LIBRARY_DIR,
            chapter.get_paragraph_dir(paragraph.attrs["index"]),
            os.path.basename(img_src),
        )

    log.info("Serving image %s", img_src)

    if img_src.endswith(".png"):
        mimetype = "image/png"
    elif img_src.endswith(".jpg") or img_src.endswith(".jpeg"):
        mimetype = "image/jpeg"
    else:
        log.error("Unknown image type for src: %s", img_src)
        mimetype = "application/octet-stream"

    # obsolete, may 17 2026
    # backward compatibility for the "phrase" layout
    if not os.path.exists(image_fn) and os.path.exists(
        os.path.join(const.LIBRARY_DIR, chapter.bookdir, "phrase")
    ):
        for phrase_dir in os.listdir(
            os.path.join(const.LIBRARY_DIR, chapter.bookdir, "phrase")
        ):
            potential_fn = os.path.join(
                const.LIBRARY_DIR,
                chapter.bookdir,
                "phrase",
                phrase_dir,
                os.path.basename(img_src),
            )

            if os.path.exists(potential_fn):
                os.makedirs(os.path.dirname(image_fn), exist_ok=True)
                shutil.copy(potential_fn, image_fn)

    with open(
        image_fn,
        "rb",
    ) as f:
        image_bytes = f.read()

        if height > 0:
            image = Image.open(io.BytesIO(image_bytes))
            # resize to height while maintaining aspect ratio
            image = image.resize((int(image.width * (height / image.height)), height))
            output = io.BytesIO()
            image.save(output, format="PNG")
            image_bytes = output.getvalue()

    response = make_response(image_bytes)
    response.headers["Content-Type"] = mimetype
    return response


@bp.route("/citation/<int:image_index>.png", methods=["GET"])
def show_citation_by_index(author, title, chapter_number, language, image_index=0):
    """
    Serve the (full resolution) citation image by index.  If no citation image
    exists, create one.
    """
    author = Author(author)
    chapter = Chapter(
        author=author, title=title, number=chapter_number, language=language
    )
    image_index = int(image_index)

    image_xml = chapter.get_image(image_index)
    paragraph_xml = image_xml.find_parent("paragraph")

    citation_image_fn = os.path.join(
        const.LIBRARY_DIR, chapter.get_image_citation_filename(image_index)
    )

    if citation_image_fn:
        # citation_image_pfn = os.path.join(
        #     const.LIBRARY_DIR,
        #     chapter.get_paragraphdir(paragraph_xml.attrs['index']),
        #     citation_image_fn
        # )
        if not os.path.exists(citation_image_fn):
            # create the citation image?
            cite_artist = image_xml.attrs.get("artist", "")
            cite_title = image_xml.attrs.get("title", "")
            cite_year = image_xml.attrs.get("year", "")
            cite_medium = image_xml.attrs.get("medium", "")
            cite_width = image_xml.attrs.get("source_width", "")
            cite_height = image_xml.attrs.get("source_height", "")
            cite_location = image_xml.attrs.get("location", "")

            images.create_cite_image(
                artist=cite_artist,
                title=cite_title,
                year=cite_year,
                medium=cite_medium,
                width=cite_width,
                height=cite_height,
                location=cite_location,
                output_image_pfn=citation_image_fn,
            )

        return send_file(citation_image_fn, mimetype="image/png")

    else:
        return f"Image {image_xml} has no 'src'", 404


#   ost:5000/Aesop   /Fables      /0017     /images/000013/img_6_The_background_of_the_sce_137fc39f_491f.png
# /Horace%20Hutchinson/The%20Greatest%20Story%20in%20the%20World%20period%201/chapter/0001/paragraphs/000000/img_2_The_front_of_a_record_alb_942f1a7d.png
# /W.%20W.%20Jacobs/The%20Monkeys%20Paw/image/active/W.%20W.%20Jacobs/The%20Monkeys%20Paw/paragraphs/000001/img_0_A_close_up_view_of_a_draw_c46145ac.png
# TODO: fixme
# @bp.route("/<author>/<path:title>/<chapter>/paragraphs/<int:phrase_index>/<imagefile>")
def active_images(author, title, chapter, phrase_index=0, imagefile=""):
    print("Looking for file...")
    chapterdir = get_chapterdir(author, title, chapter)

    return send_from_directory(
        os.path.join(
            "/home/jkane/books/active",
            chapterdir,
            "paragraphs",
            f"{phrase_index:06}",
        ),
        imagefile,
        mimetype="image/png",
    )


@bp.route("/actions/draw_all_missing_images", methods=["POST"])
def draw_all_missing_images(author, title, chapter_number, language):
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)

    image_index = 0
    all_images = chapter.get_xml().findAll("image")

    style = chapter.config.get("default_style", "")

    prompt_filter = None
    negative_prompt = None
    if style:
        # the currently selected style, applied to the current prompt.
        log.info(f"Looking up style {style} for chapter {chapter}")
        prompt_filter, negative_prompt = styles.get_style(style)
    else:
        log.warning(
            f"No style found for chapter {chapter}, skipping style application."
        )

    for image_index, image_xml in enumerate(all_images):
        if (
            "src" in image_xml.attrs
            and image_xml.attrs["src"]
            and os.path.exists(
                os.path.join(
                    const.LIBRARY_DIR,
                    chapter.get_paragraphdir(
                        image_xml.find_parent("paragraph").attrs["index"]
                    ),
                    os.path.basename(image_xml.attrs["src"]),
                )
            )
        ):
            log.info(f"Image {image_index} already has a source file, skipping.")
            continue

        paragraph = image_xml.find_parent("paragraph")
        paragraph_dir = chapter.get_paragraphdir(paragraph.attrs["index"])

        t2i_engine = image_xml.attrs.get("t2i", "tsqn.zimageturbo")

        if t2i_engine == "flux.schnell":
            # first the t5 prompt
            if not image_xml.attrs.get("t5_prompt", ""):
                log.info("No t5_prompt found, generating a new one.")

                try:
                    next_image = all_images[image_index + 1]

                    # aggregate all the text between this image and the next image
                    text_to_next = get_text_to_next(
                        image_xml=image_xml, next_image_xml=next_image
                    )

                except IndexError:
                    text_to_next = get_text_to_next(
                        image_xml=image, next_image_xml=None
                    )

                t5_prompt = images.text_to_prompt(
                    image=image_xml, paragraph=paragraph, text_to_next=text_to_next
                )
                log.info("Using prompt: %s", t5_prompt)
                image_xml.attrs["t5_prompt"] = t5_prompt
            t5_prompt = image_xml.attrs["t5_prompt"]

            # and the clip prompt
            if not image_xml.attrs.get("clip_prompt", ""):
                log.info("No clip_prompt found, generating a new one.")

                try:
                    next_image = all_images[image_index + 1]

                    # aggregate all the text between this image and the next image
                    text_to_next = get_text_to_next(
                        image_xml=image, next_image_xml=next_image
                    )

                except IndexError:
                    text_to_next = get_text_to_next(
                        image_xml=image, next_image_xml=None
                    )

                clip_prompt = images.text_to_clip_prompt(
                    image=image,
                    paragraph=paragraph,
                    text_to_next=text_to_next,
                    t5_prompt=t5_prompt,
                )
                log.info("Using prompt: %s", clip_prompt)
                image.attrs["clip_prompt"] = clip_prompt
            clip_prompt = image_xml.attrs["clip_prompt"]

            src = image_xml.attrs.get("src", "")
            if not src or not os.path.exists(
                os.path.join(const.LIBRARY_DIR, paragraph_dir, src)
            ):
                # there isn't a src image file, or there is but the image doesn't exist.  Either way
                # this is a good opportunity to give the image the most correct name we can.
                image_fn = images.get_image_fn(
                    prompt=f"{clip_prompt}_{t5_prompt}",
                    loras=[],
                    paragraph_dir=paragraph_dir,
                    image_index=image.attrs["index"],
                )
                src = os.path.basename(image_fn)
                image_xml.attrs["src"] = src

            log.info(f"Invoking get_flux_image({src=}, {clip_prompt=}, {t5_prompt=})")
            chapter.save_xml()

            # retrieve, or build, the image.
            r = images.get_flux_image(
                image_fn=os.path.join(
                    const.LIBRARY_DIR, paragraph_dir, src.split("/")[-1]
                ),
                clip_prompt=clip_prompt,
                t5_prompt=t5_prompt,
                force=False,
            )

            log.info(f"Finished with {src}: {r}")

        elif t2i_engine in ["tongyi.zimageturbo", "tsqn.zimageturbo"]:
            if not image_xml.attrs.get("prompt", ""):
                log.info("No prompt found")

                # Use fancyprompt to generate the image prompt
                set_fancyprompt(chapter, image_xml)
                chapter.save_xml()

            # style?
            prompt = image_xml.attrs.get("prompt", "")

            if prompt_filter and "styled_prompt" not in image_xml.attrs:
                styled_prompt = prompt_filter.format(prompt=prompt)
                image_xml.attrs["styled_prompt"] = styled_prompt
                chapter.save_xml()

            # draw the image
            image_module = t2i_registry.get(t2i_engine)
            image_module().generate_image(image_xml, force=True)
            chapter.save_xml()

        # is this a fullscreen?
        if paragraph.attrs.get("fullscreen", "false").lower() == "true":
            log.info("Image is fullscreen.  We need to outpaint.")

            aspect = chapter.get_aspect()  # portrait or widescreen
            transformation_type = "crop_100_outpaint_" + aspect
            image_pil, image_fn = selector.apply_image_adjustments(
                chapter,
                image_xml,
                transformation_type,
                image_xml.attrs["src"],
                force=True,
            )

            # finally, make this our new image src
            image_xml.attrs["src"] = os.path.basename(image_fn)
            chapter.save_xml()
            continue

    return htmx.draw_all_missing_images_button(chapter)


# draw_all_missing_tmi_images
@bp.route("/actions/draw_all_missing_tmi_images", methods=["POST"])
def draw_all_missing_tmi_images(author, title, chapter):
    chapterdir = get_chapterdir(author, title, chapter)
    chapterurl = get_chapterurl(author, title, chapter)
    bookdir = get_bookdir(author, title)

    mybook = booklib.get_book(chapterdir)
    all_images = mybook.soup.findAll("image")

    for image_xml in all_images:
        paragraph = image_xml.find_parent("paragraph")

        if (
            "src" not in image_xml.attrs
            or not image_xml.attrs["src"]
            or not os.path.exists(
                os.path.join(
                    const.LIBRARY_DIR, paragraph.attrs["dir"], image_xml.attrs["src"]
                )
            )
        ):
            # images.tmi_regenerate_image(
            #     mybook, author, title, chapter, image_xml,
            #     chapterdir, bookdir,
            # )
            images.zmi_regenerate_image(
                mybook,
                author,
                title,
                chapter,
                image_xml,
                chapterdir,
                bookdir,
            )

    return htmx.draw_all_missing_tmi_images_button(chapterurl)


@bp.route("/actions/generate_missing_image_metadata", methods=["POST"])
def generate_missing_image_metadata(author, title, chapter):
    """
    Go through all the images and try to fill in missing metadata based on the text.
    """
    chapterdir = get_chapterdir(author, title, chapter)
    chapterurl = get_chapterurl(author, title, chapter)
    mybook = booklib.get_book(chapterdir)

    log.info(f"Generating missing image metadata for {chapterdir}")
    for image_xml in mybook.soup.findAll("image"):
        images.generate_metadata_from_text(chapterdir=chapterdir, image_xml=image_xml)
        mybook.save_xml()
    return htmx.generate_missing_image_metadata_button(chapterurl)


@bp.route("/action/reprompt_redraw_all", methods=["POST"])
def reprompt_redraw_all(author, title, chapter):
    """
    TODO
    flux specific pipeline to reprompt and redraw all images in a chapter.
    """
    chapterdir = get_chapterdir(author, title, chapter)

    mybook = booklib.get_book(chapterdir)

    image_index = 0
    while True:
        # this will raise an exception when there are no more images.
        image = mybook.soup.find("image", image_index=str(image_index))

        paragraph = image.find_parent("paragraph")
        # all the text between this image and the next image.
        next_image = mybook.soup.find("image", image_index=image_index + 1)

        # aggregate all the text between this image and the next image
        text_to_next = get_text_to_next(image=image, next_image=next_image)

        # make a new detailed prompt
        image.attrs["t5_prompt"] = images.text_to_prompt(
            image=image, paragraph=paragraph, text_to_next=text_to_next
        )

        # make a new clip prompt
        image.attrs["clip_prompt"] = text_to_clip_prompt(
            image=image,
            paragraph=paragraph,
            text_to_next=text_to_next,
            t5_prompt=image.attrs["t5_prompt"],
        )

        # determine the correct new image_fn
        image_fn = images.get_image_fn(
            prompt=f"{image.attrs["clip_prompt"]}_{image.attrs["t5_prompt"]}",
            loras=[],
            paragraph_dir=paragraph.attrs["dir"],
            image_index=image.attrs["index"],
        )
        image.attrs["src"] = image_fn
        mybook.save_xml()

        t5_prompt = image.attrs["t5_prompt"]
        clip_prompt = image.attrs["clip_prompt"]

        if os.path.exists(image_fn):
            os.unlink(image_fn)

        log.info(
            f"Invoking {images.get_image}({image_fn=}, {clip_prompt=}, {t5_prompt=})"
        )
        images.get_flux_image(
            image_fn=image_fn, clip_prompt=clip_prompt, t5_prompt=t5_prompt
        )


@bp.route("/action/clear_zero_duration_images", methods=["POST"])
def clear_zero_duration_images(author, title, chapter):
    chapterdir = get_chapterdir(author, title, chapter)
    mybook = booklib.get_book(chapterdir)

    # first force a recalculation of the number of frames each image should be displayed.
    audio._recalculate_all_phrase_frames(chapterdir)

    for image in mybook.soup.findAll("image"):
        if image.attrs.get("frames", "0") == "0":
            image.decompose()
            mybook.save_xml()

    return redirect(f"/{author}/{title}/{chapter}/images/", code=302)


@bp.route("/<int:image_index>/transition/configuration", methods=["GET"])
def get_transition_configuration_widgets(
    author, title, chapter_number, language, image_index
):
    image_index = int(image_index)
    chapterdir = get_chapterdir(author, title, chapter)
    mybook = booklib.get_book(chapterdir)
    value = htmx.get_transition_configuration_widgets(mybook.soup, image_index)
    mybook.save_xml()
    log.info(f"Transition configuration widgets: {value}")
    return value, 200


# TODO
# @bp.route("/<author>/<path:title>/<chapter>/transition/<filename>.mp4")
def deliver_transition_video(author, title, chapter, filename=""):
    # /home/jkane/books/active/Aesop/Fables/chapter/0018/
    # transitions/transition_000004.mp4
    chapterdir = get_chapterdir(author, title, chapter)
    # image_index = int(image_index)

    full_filename = os.path.join(
        const.LIBRARY_DIR, chapterdir, "transitions", filename + ".mp4"
    )

    # filename = f"transition_{image_index:06}.mp4"

    return send_file(
        full_filename,
        mimetype="video/mp4",
        as_attachment=False,
        etag=True,
    )


# http://localhost:8080/library/L.%20Frank%20Baum/The%20Marvelous%20Land%20of%20Oz/1/english/images/4/delete_video?title=baum-marv-001-bhgv_img_4_LTX23__00_00005_.mp4&poster=comfy%2Foutput%2Fbaum-marv-001-bhgv_img_4_LTX23__00_00005_.mp4.png
@bp.route("/<int:image_index>/delete_video", methods=["DELETE"])
def delete_video(author, title, chapter_number, language, image_index):
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)
    image_index = int(image_index)

    video_title = request.args.get("title", "")
    poster = request.args.get("poster", "")

    full_filename = poster.replace(".mp4.png", ".mp4").lstrip("comfy/output/")
    if not video_title:
        # there is only one video, the selected one.  Since there is only one
        # video, the selector that would populate the "title" field is absent.
        image_xml = chapter.get_image(image_index)
        image_filename = chapter.get_image_filename(image_xml)
        video_filename = image_filename.replace(".png", ".mp4")
        if os.path.exists(video_filename):
            os.remove(video_filename)
            log.info(f"Deleted video file {video_filename}")
            return "", 200
        else:
            return "Video not found", 400

    video_fn = os.path.join(
        const.COMFY_DIRS["artifactserver"]["OUTPUT_DIR"],
        full_filename,
    )

    if os.path.exists(video_fn):
        os.remove(video_fn)
        log.info(f"Deleted video file {video_fn}")
    else:
        log.warning(f"Video file {video_fn} not found for deletion")

    return (
        f"""<div id="previous_attempts" hx-swap-oob="true" hx-swap="innerHTML">
        {htmx.previous_attempt(chapter, image_index)}
    </div>
    """,
        200,
    )


@bp.route("/<int:image_index>/comfy/output/<filename>.mp4.png")
@bp.route("/<int:image_index>/comfy/output/<cat>/<filename>.mp4.png")
def deliver_comfy_video_portrait(
    author, title, chapter_number, language, image_index, cat="", filename=""
):
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)
    image_index = int(image_index)

    if cat:
        full_output_filename = os.path.join(
            const.COMFY_DIRS["artifactserver"]["OUTPUT_DIR"],
            cat,
            filename + ".mp4.png",
        )
        input_video_fn = os.path.join(
            const.COMFY_DIRS["artifactserver"]["OUTPUT_DIR"],
            cat,
            filename + ".mp4",
        )
    else:
        full_output_filename = os.path.join(
            const.COMFY_DIRS["artifactserver"]["OUTPUT_DIR"],
            filename + ".mp4.png",
        )
        input_video_fn = os.path.join(
            const.COMFY_DIRS["artifactserver"]["OUTPUT_DIR"],
            filename + ".mp4",
        )

    if os.path.exists(full_output_filename):
        # serve it from the comfy output directory.
        log.info(f"Comfy video portrait already exists at {full_output_filename}")
        return send_file(
            full_output_filename,
            mimetype="image/png",
            as_attachment=False,
            etag=True,
        )

    if not os.path.exists(input_video_fn):
        raise FileNotFoundError(f"Input video not found at {input_video_fn}")

    # we're going to open up input_video_fn, grab a frame from the middle, and serve that as a png.
    # we will use ffmpeg.
    with TemporaryDirectory() as tempdir:
        tools.extract_frames(videofile=input_video_fn, frame_dir=tempdir)
        frames = sorted(os.listdir(tempdir))

        if frames:
            middle_frame = frames[len(frames) // 2]
            middle_frame_path = os.path.join(tempdir, middle_frame)
            shutil.copy(middle_frame_path, full_output_filename)
            log.info(
                f"Extracted middle frame {middle_frame} from {input_video_fn} to {full_output_filename}"
            )

    return send_file(
        full_output_filename,
        mimetype="image/png",
        as_attachment=False,
        etag=True,
    )


@bp.route("/<int:image_index>/comfy/output/<filename>.mp4")
@bp.route("/<int:image_index>/comfy/output/<cat>/<filename>.mp4")
def deliver_comfy_video(
    author, title, chapter_number, language, image_index, cat="", filename=""
):
    author = Author(author)
    # chapter = Chapter(author, title, chapter_number, language)
    image_index = int(image_index)

    # image_xml = chapter.get_image(image_index)
    # paragraph = image_xml.find_parent("paragraph")

    if cat:
        full_filename = os.path.join(
            const.COMFY_DIRS["artifactserver"]["OUTPUT_DIR"],
            cat,
            filename + ".mp4",
        )
    else:
        full_filename = os.path.join(
            const.COMFY_DIRS["artifactserver"]["OUTPUT_DIR"],
            filename + ".mp4",
        )

    log.info(f"Serving comfy video {full_filename}")

    return send_file(
        full_filename,
        mimetype="video/mp4",
        as_attachment=False,
        etag=True,
    )


@bp.route("/<int:image_index>/animation/<filename>.mp4")
def deliver_animation_video(
    author, title, chapter_number, language, image_index, filename=""
):
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)
    image_index = int(image_index)

    image_xml = chapter.get_image(image_index)
    paragraph = image_xml.find_parent("paragraph")

    full_filename = os.path.join(
        const.LIBRARY_DIR,
        chapter.get_paragraph_dir(paragraph.attrs["index"]),
        filename + ".mp4",
    )

    if not os.path.exists(full_filename):
        # the animation doesn't exist.. maybe it's really just a camera motion?
        camera_filename = os.path.join(
            const.LIBRARY_DIR,
            chapter.get_paragraph_dir(paragraph.attrs["index"]),
            "camera.mp4",
        )
        if os.path.exists(camera_filename):
            full_filename = camera_filename

    log.info(f"Serving animation video {full_filename}")

    return send_file(
        full_filename,
        mimetype="video/mp4",
        as_attachment=False,
        etag=True,
    )


# http://localhost:8080/library/L.%20Frank%20Baum/The%20Marvelous%20Land%20of%20Oz/1/english/images/11/additional_video
@bp.route("/<int:image_index>/additional_video", methods=["POST"])
def create_additional_video(author, title, chapter_number, language, image_index):
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)
    image_index = int(image_index)

    image_xml = chapter.get_image(image_index)

    image_xml.attrs["animation_count"] = str(
        int(image_xml.attrs.get("animation_count", "1")) + 1
    )
    chapter.save_xml()

    # we need the re-drawn "Animation" page with a second 'empty' video input set.
    # one layer below animation-panel..
    # this will hx-swap-oob itself into the right place.
    return htmx.animation_workshop(image_xml, chapter), 200


# additional_video_form(
#         soup=soup,
#         filename=os.path.basename(filename),
#         chapterurl=get_chapterurl(author, title, chapter),
#         chapterdir=chapterdir,
#         image_index=image_index,
#     ), 200


@bp.route("/<int:image_index>/transition/<tag>", methods=["POST"])
def create_transition_route(author, title, chapter_number, language, image_index, tag):
    chapterurl = get_chapterurl(author, title, chapter)
    chapterdir = get_chapterdir(author, title, chapter)
    mybook = booklib.get_book(chapterdir)
    soup = mybook.soup

    image_index = int(image_index)

    filename = images.create_transition(
        image_index,
        chapterdir,
        force=True,
    )

    return htmx.image_transition_form(
        soup=soup,
        filename=os.path.basename(filename),
        chapterurl=chapterurl,
        chapterdir=chapterdir,
        image_index=image_index,
        tag=tag,
    ), 200


@bp.route("/<int:image_index>/transition/<tag>", methods=["DELETE"])
def delete_transition_route(author, title, chapter_number, language, image_index, tag):
    image_index = int(image_index)
    chapterdir = get_chapterdir(author, title, chapter)
    chapterurl = get_chapterurl(author, title, chapter)

    mybook = booklib.get_book(chapterdir)
    soup = mybook.soup

    transition_frames_directory = os.path.join(
        const.LIBRARY_DIR, chapterdir, "transitions", f"transition_{image_index:06}"
    )

    # the mp4 name
    filename = os.path.join(
        const.LIBRARY_DIR, chapterdir, "transitions", f"transition_{image_index:06}.mp4"
    )

    if os.path.exists(filename):
        log.info(f"Deleting transition video {filename}")
        os.unlink(filename)

    if os.path.exists(transition_frames_directory):
        log.info(f"Deleting transition frames directory {transition_frames_directory}")
        shutil.rmtree(transition_frames_directory)

    return htmx.image_transition_form(
        soup=soup,
        filename=os.path.basename(filename),
        chapterurl=chapterurl,
        chapterdir=chapterdir,
        image_index=image_index,
        tag=tag,
    ), 200


# POST /L.%20Frank%20Baum/The%20Marvelous%20Land%20of%20Oz/0001/images/action/reindex_images
@bp.route("/action/reindex_images", methods=["POST"])
def reindex_images(author, title, chapter_number, language):
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)

    all_images = chapter.get_xml().findAll("image")
    for image_index, image_xml in enumerate(all_images):
        if "index" not in image_xml.attrs or int(image_xml.attrs["index"]) != int(
            image_index
        ):
            log.warning(
                f"Fixing image index for image {image_index} in {chapter.get_chapterdir()}"
            )
            image_xml.attrs["index"] = int(image_index)

    chapter.save_xml()

    # can you redirect to an empty relative path?
    return redirect("", code=302)


# http://localhost:5000/H.%20P.%20Lovecraft/Cool%20Air/0001/images/5/actions/apply_style
@bp.route("/<int:image_index>/actions/apply_style", methods=["POST", "PUT"])
def apply_style(author, title, chapter_number, language, image_index):
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)
    image_index = int(image_index)

    image_xml = chapter.get_image(image_index)

    new_style = request.form.get("style", "")
    log.info(f"New style: {new_style}")

    if new_style:
        prompt_filter, negative_prompt = styles.get_style(new_style)

        if prompt_filter:
            image_xml.attrs["style"] = new_style
            if negative_prompt:
                image_xml.attrs["negative_prompt"] = negative_prompt

        chapter.save_xml()

    return htmx.prompt_panel(chapter, image_xml), 200


# hx-post="/{chapterurl}/images/{image_xml.attrs['index']}/actions/apply_prompt_template"
@bp.route("/<int:image_index>/actions/apply_prompt_template", methods=["POST"])
def apply_prompt_template(author, title, chapter_number, language, image_index):
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)
    image_index = int(image_index)

    log.info(f"{request.form}")
    image_xml = chapter.get_image(image_index)

    # start with all global characters
    all_characters = characters.get_all_characters(chapter, is_global=True)

    # update with all chapter-specific characters
    all_characters.update(characters.get_all_characters(chapter, is_global=False))

    try:
        paragraph_text = get_text_to_next(image_xml, image_xml.find_next("image"))
    except IndexError:
        paragraph_text = get_text_to_next(image_xml, None)

    template = request.form.get("prompt_template", "")
    log.info(f"New template: {template}")

    if template:
        if template == "metadata_prompt":
            images.build_replacement_prompt(
                all_characters, get_bookdir(author, title), image_xml
            )
            chapter.save_xml()

        else:
            prompt_templates = {
                "author_page": "Renown author {author} at a desk writing the famous story {title}",
                "translator_page": "Charming and clever {translator} making an important but difficult decision while sitting at a desk writing a letter with a fountain pen",
                "chapter_page": "A classic and elegant book title page with the title {paragraph_text} in large ornate letters",
            }

            subtitle = chapter.config.get("subtitle", "")
            if subtitle:
                # with a subtitle
                prompt_templates["book_cover"] = (
                    """The front cover of a detailed carved and painted cover of a """
                    """masterfully crafted leather-bound handmade special edition of "{title} - {subtitle}" by "{author}"."""
                )
            else:
                # without a subtitle
                prompt_templates["book_cover"] = (
                    """The front cover of a detailed carved and painted cover of a """
                    """masterfully crafted leather-bound handmade special edition of "{title}" by "{author}"."""
                )

            prompt_template = prompt_templates.get(template)

        if prompt_template:
            prompt = prompt_template.format(
                author=chapter.config.get("author", author),
                title=chapter.config.get("title", title),
                chapter_title=chapter.config.get("chapter_title", chapter),
                paragraph_text=paragraph_text,
                subtitle=subtitle,
                translator=chapter.config.get("translator", ""),
                prompt=image_xml.attrs.get("prompt", ""),
            )
            image_xml.attrs["prompt"] = prompt
            image_xml.attrs["prompt_template"] = template

            image_xml.attrs["tags"] = "has-text=false,spoken-only=true"
            log.info(f"Updated prompt to {prompt}")

        chapter.save_xml()

    return htmx.prompt_panel(chapter, image_xml), 200


def standard_side_panels(image_xml, chapter, datastack=None):
    out = htmx.image_side_panel(
        image_xml=image_xml.find_previous("image"),
        chapter=chapter,
        datastack=datastack,
        label="Previous",
    )

    out += htmx.image_side_panel(
        image_xml=image_xml.find_next("image"),
        chapter=chapter,
        datastack=datastack,
        label="Next",
    )
    return out


# GET /Bible/Old%20Testament/0001/images/1/image_tab_selector
@bp.route("/<int:image_index>/image_tab_selector", methods=["POST"])
def image_tab_selector(author, title, chapter_number, language, image_index):
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)
    image_index = int(image_index)

    image_xml = chapter.get_image(image_index)
    paragraph = image_xml.find_parent("paragraph")

    tab = request.form.get("group", "")

    if tab == "viewer":
        log.info("Viewer Image Display")

        if "src" not in image_xml.attrs:
            # gonna have to pull that ripcord jeff, we're not going make it back
            # to the carrier.
            wrap = """
            <div id="viewer" hx-swap-oob="true" hx-swap="innerHTML" class="wa-stack">
                <h3>No Image</h3>
            </div>"""

            # maybe.. image dimensions?
            wrap += standard_side_panels(image_xml, chapter)
            response = make_response(wrap, 200)
            response.headers["HX-Push-Url"] = (
                f"/{chapter.url}/{chapter.language}/images/{image_index}/"
            )

            return response

        basesrc = os.path.basename(image_xml.attrs["src"])

        image_pfn = os.path.join(
            const.LIBRARY_DIR,
            chapter.get_paragraph_dir(paragraph.attrs["index"]),
            basesrc,
        )

        if os.path.exists(image_pfn):
            log.info("Loading image for viewer tab.")
            image = Image.open(image_pfn)

            imageurl = (
                url_for(
                    "library.book.chapter.images.show_image_by_index",
                    author=author.name,
                    title=chapter.title,
                    chapter_number=chapter.number,
                    language=chapter.language,
                    height=0,  # full size
                    image_index=image_index,
                )
                + f"?t={os.path.getmtime(image_pfn)}"
            )  # cache buster

            wrap = f"""
            <div id="viewer" hx-swap-oob="true" hx-swap="innerHTML" class="wa-stack">
                <h3>{image.size} {image.mode}:  {basesrc}</h3>
                <img src="{imageurl}"></img>            
            </div>"""

        else:
            wrap = """<div id="viewer" hx-swap-oob="true" hx-swap="innerHTML" class="wa-stack">
                <h3>No Image File</h3>
            </div>"""

        wrap += standard_side_panels(image_xml, chapter)

        response = make_response(wrap, 200)
        response.headers["HX-Push-Url"] = (
            url_for(
                "library.book.chapter.images.base",
                **chapter.kwargs,
                image_index=image_index,
            )
            + "viewer"
        )

        return response

    elif tab == "selector":
        log.info("Image tab selector requested.")

        image_fragdex_chooser = selector_htmx.image_selector(
            chapter,
            image_xml=image_xml,
        )
        chapter.save_xml()

        wrap = f"""<div id="selector" 
            hx-swap-oob="true"
            hx-on::htmx:afterSwap="addCarouselListeners()"
        >
            {image_fragdex_chooser}
        </div>"""

        wrap += standard_side_panels(image_xml, chapter)

        response = make_response(wrap, 200)
        response.headers["HX-Trigger-After-Settle"] = "newCarouselReady"
        response.headers["HX-Push-Url"] = (
            url_for(
                "library.book.chapter.images.base",
                **chapter.kwargs,
                image_index=image_index,
            )
            + "selector"
        )

        return response

    elif tab == "editor":
        log.info("Image tab editor requested.")

        log.info(
            "module 'artifact_editor.images.editor' has no attribute 'htmx'",
            editor=editor,
        )
        image_editor_form = editor.htmx.image_editor_workshop(
            chapter=chapter,
            image_xml=image_xml,
        )

        wrap = f"""<div id="editor" hx-swap-oob="true">
            {image_editor_form}
        </div>"""

        wrap += standard_side_panels(image_xml, chapter, editor.htmx.datastack)

        response = make_response(wrap, 200)
        response.headers["HX-Push-Url"] = (
            url_for(
                "library.book.chapter.images.base",
                **chapter.kwargs,
                image_index=image_index,
            )
            + "editor"
        )

        return response

    elif tab == "scene":
        log.info("Image tab scene requested.")

        image_scene_form = scene.htmx.image_scene_workshop(
            chapter=chapter,
            image_xml=image_xml,
        )

        wrap = f"""<div id="scene" hx-swap-oob="true">
            {image_scene_form}
        </div>"""

        wrap += standard_side_panels(image_xml, chapter, scene.htmx.datastack)
        response = make_response(wrap, 200)
        response.headers["HX-Push-Url"] = (
            url_for(
                "library.book.chapter.images.base",
                **chapter.kwargs,
                image_index=image_index,
            )
            + "scene"
        )

        return response

    elif tab == "upload":
        log.info("Image tab upload requested.")

        image_upload_form = htmx.upload_image_workshop(
            chapter=chapter,
            image_xml=image_xml,
        )

        wrap = f"""<div id="upload" hx-swap-oob="true">
            {image_upload_form}
        </div>"""

        wrap += standard_side_panels(image_xml, chapter)
        response = make_response(wrap, 200)
        response.headers["HX-Push-Url"] = (
            url_for(
                "library.book.chapter.images.base",
                **chapter.kwargs,
                image_index=image_index,
            )
            + "upload"
        )

        return response

    elif tab == "prompt":
        log.info("Image tab prompt requested.")
        wrap = htmx.prompt_panel(chapter, image_xml)
        wrap += standard_side_panels(image_xml, chapter, htmx.prompt_datastack)
        response = make_response(wrap, 200)
        response.headers["HX-Trigger-After-Settle"] = "newCarouselReady"
        response.headers["HX-Push-Url"] = (
            url_for(
                "library.book.chapter.images.base",
                **chapter.kwargs,
                image_index=image_index,
            )
            + "prompt"
        )

        return response

    elif tab == "citation":
        log.info("Image tab citation form")

        citation_panel = htmx.citation_panel(chapter, image_xml)
        citation_panel += standard_side_panels(image_xml, chapter, datastack=None)
        response = make_response(citation_panel, 200)
        response.headers["HX-Push-Url"] = (
            url_for(
                "library.book.chapter.images.base",
                **chapter.kwargs,
                image_index=image_index,
            )
            + "citation"
        )

        return response

    else:
        log.warning(f"Unknown tab requested: {tab}")
        return "", 400


# http://localhost:5000/Aesop/Fables/0024/images/4/frame/0.png
@bp.route(
    "/<int:image_index>/frame/<int:video_index>/<int:frame_index>.png", methods=["GET"]
)
def deliver_animation_frame(
    author, title, chapter_number, language, image_index, video_index, frame_index
):
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)
    image_index = int(image_index)
    video_index = int(video_index)

    image_xml = chapter.get_image(image_index)
    paragraph = image_xml.find_parent("paragraph")

    # /home/jkane/books/active/Aesop/Fables/chapter/0024/animation/animation_000004/000000.png
    framedir = os.path.join(
        const.LIBRARY_DIR,
        chapter.get_paragraph_dir(paragraph.attrs["index"]),
        "animation",
        f"image_{image_index:06d}_{video_index:02d}",
    )

    frame_filename = f"frame_{frame_index:06d}.png"

    log.info(
        "Attempting to deliver frame %s from directory %s", frame_filename, framedir
    )

    return send_from_directory(
        framedir,
        frame_filename,
        mimetype="image/png",
    )


# POST /Aesop/Fables/0024/images/4/actions/get_last_good_frame_widget
@bp.route("/<int:image_index>/actions/get_last_good_frame_widget", methods=["POST"])
def get_last_good_frame_widget(author, title, chapter_number, language, image_index):
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)
    image_index = int(image_index)

    chosen = int(request.form.get("chosen", "0"))
    video_index = int(request.form.get("video_index", "0"))

    image_xml = chapter.get_image(image_index)
    paragraph = image_xml.find_parent("paragraph")

    # we start with
    #   <div id="lgf"></div>
    # and are being delived via outerHTML swap.
    value = """
    <div id="lgf" hx-swap-oob="true">
    <h3>Choose the Last Good Frame</h3>
    <h4>Everything after it will be redrawn.</h4>

    <div class="wa-cluster wa-gap-0" style="position: absolute;">
    """

    # we want the user to be able to choose a particular frame from a list of
    # something like 50-200 images.

    # /home/jkane/books/active/Aesop/Fables/chapter/0024/animation/animation_000004/000000.png
    framedir = os.path.join(
        const.LIBRARY_DIR,
        chapter.get_paragraph_dir(paragraph.attrs["index"]),
        "animation",
        f"image_{image_index:06d}_{video_index:02d}",
    )

    all_frames = sorted(glob.glob(os.path.join(framedir, "*.png")))

    center_width = 200
    greater_width = 200
    minor_width = 100
    lesser_width = 2

    how_many_greater = 3
    how_many_minor = 3

    # frame count starts at 1
    for index, frame in enumerate(all_frames, 1):
        img_class = "bb-img"
        if index < (chosen - (how_many_greater + how_many_minor)):
            width = lesser_width
        elif index < (chosen - how_many_minor):
            width = minor_width
        elif index < chosen:
            width = greater_width
        elif index == chosen:
            width = center_width
            img_class = "bb-img-selected"
        elif index <= (chosen + how_many_minor):
            width = greater_width
        elif index <= (chosen + (how_many_greater + how_many_minor)):
            width = minor_width
        else:
            width = lesser_width

        last_good_frame_url = url_for(
            "library.book.chapter.images.get_last_good_frame_widget",
            **chapter.kwargs,
            image_index=image_index,
            video_index=video_index,
        )

        if width > 10:
            frame_url = url_for(
                "library.book.chapter.images.deliver_animation_frame",
                **chapter.kwargs,
                image_index=image_index,
                video_index=video_index,
                frame_index=index,
            )
            value += f"""
                <div 
                    class="basic-bar"
                    style="width: {width}px;"
                    hx-post="{last_good_frame_url}" 
                    hx-target="#lgf"
                    hx-vals='{{"chosen":{index}}}'
                >
                    <img class="{img_class}" src="{frame_url}"></img>
                </div>
                """
        else:
            value += f"""
                <div 
                    hx-post="{last_good_frame_url}" 
                    hx-vals='{{"chosen":{index}}}'
                    hx-target="#lgf"
                    class="basic-bar"
                    style="width: {width}px;"
                >
                </div>
                """

    remove_frames_after_url = url_for(
        "library.book.chapter.images.remove_frames_after",
        **chapter.kwargs,
        image_index=image_index,
        frame_index=chosen,
        video_index=video_index,
    )

    # the positioning here is a bit hacky.
    value += f"""</div>
    <wa-button
        style="position: relative; top: 13em; left: 40em;"
        hx-delete="{remove_frames_after_url}"
        hx-target="#lgf"
        variant="danger"
    >
        Delete everything after this frame
    </wa-button>
    </div>"""

    return value, 200


# DELETE /Aesop/Fables/0024/images/4/frames/remove_after/5
@bp.route(
    "/<int:image_index>/frame/<int:video_index>/remove_after/<int:frame_index>",
    methods=["DELETE"],
)
def remove_frames_after(
    author, title, chapter_number, language, image_index, video_index, frame_index
):
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)
    image_index = int(image_index)
    video_index = int(video_index)

    image_xml = chapter.get_image(image_index)
    if image_xml is None:
        log.error(
            f"Image index {image_index} not found in chapter {chapter.get_chapterdir()}"
        )
        return "Image not found", 404

    paragraph_xml = image_xml.find_parent("paragraph")

    # /home/jkane/books/active/Aesop/Fables/chapter/0024/animation/animation_000004/000000.png
    framedir = os.path.join(
        const.LIBRARY_DIR,
        chapter.get_paragraph_dir(paragraph_xml.attrs["index"]),
        "animation",
        f"image_{image_index:06d}_{video_index:02d}",
    )

    all_frames = sorted(glob.glob(os.path.join(framedir, "*.png")))

    for index, frame in enumerate(all_frames):
        if index > frame_index:
            log.info(f"Deleting frame {frame} after chosen {frame_index}")
            os.unlink(frame)

    image_filename = chapter.get_image_filename(image_xml)
    if video_index == 0:
        video_filename = image_filename.replace(".png", ".mp4")
    else:
        video_filename = image_filename.replace(".png", f"_{video_index:02d}.mp4")

    if os.path.exists(video_filename):
        log.info(
            f"Deleting video file {video_filename} to reassemble from remaining frames."
        )
        os.unlink(video_filename)

    # create a video named video_filename from the frames that remain in framedir.
    video.assemble_mp4(
        fps=const.FPS,
        framedir=framedir,
        wavfile=None,
        videofile=video_filename,
        image_match="%06d.png",
    )

    response = make_response("")
    # response.headers["HX-Trigger-After-Settle"] = "framesDeleted"
    response.headers["HX-Refresh"] = "true"
    return response, 204
