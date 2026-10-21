# Should only be sourced on the UI server side.
import html
import os
from urllib.parse import parse_qs, urlparse

from flask import g, request, url_for

import artifact_editor.images.htmx as images_htmx
import artifact_editor.styles.htmx as styles_htmx
from artifact_editor.author.author import Author
import const
import logger
from text_to_image.base import TextToImageProviderUI
from text_to_image.registry import registry
from PIL import Image
from artifact_editor import config

# scene_htmx
from artifact_editor.images.scene.htmx import (
    image_scene_workshop,
)

log = logger.log(__name__)
from artifact_editor import (
    images,
    tools,
)


class WorkflowTemplate:
    def __init__(self, json_pfn):
        """
        json_pfn should be something like "flux_Flux_Schnell.json
        which will give us:
            id="flux"
            name="Flux Schnell"

        It is _your_ job to make sure the first this_ is unique.
        It should be easy.
        """
        self.path = json_pfn
        aslist = os.path.basename(json_pfn).split("_")

        self.id = ".".join(
            aslist[0].split(".")[1:]
        )  # remove the "ui." or "api." prefix
        # cosmetic name
        self.name = " ".join(aslist[1:]).replace(".json", "")


def get_workflow_templates(interface="ui", mode="t2i"):
    for fn in os.listdir(const.COMFY_DIRS["artifactserver"]["WORKFLOW_TEMPLATES_DIR"]):
        if fn.startswith(f"{interface}.{mode}.") and fn.endswith(".json"):
            yield WorkflowTemplate(
                os.path.join(const.COMFY_DIRS["artifactserver"]["WORKFLOW_TEMPLATES_DIR"], fn)
            )


def selected_image_workflow(image, templates, default):
    """Resolve legacy selections and recover still workflows overwritten by animation."""
    def match(value):
        value = os.path.basename(value or "").removesuffix(".json")
        for prefix in ("ui.", "api."):
            value = value.removeprefix(prefix)
        for template in templates:
            if value in (template.id, template.id.split(".", 1)[-1]):
                return template.id
            if value == os.path.basename(template.path).removesuffix(".json").removeprefix("ui.").removeprefix("api."):
                return template.id
        return None

    saved = match(image.get("workflow_image_template"))
    if saved:
        return saved
    filename = os.path.basename(image.get("src") or "")
    for template in templates:
        identifier = template.id.split(".", 1)[-1]
        if filename.startswith(f"img_{image.get('index')}_{identifier}_"):
            return template.id
    return match(default) or ""


# little harness so we play nice.
class ComfyWorkflowUI(TextToImageProviderUI):
    key = "comfyui.workflow"
    cosmetic = "Workflow"

    def generate_ui(self, image_xml, with_class="") -> bytes:
        """
        Response is a string of HTML components.
        """
        # from artifact_editor.chapter.chapter import Chapter
        # chapter_kwargs = tools.requestToChapterKwargs(request.url)
        # chapter_kwargs["author"] = Author(chapter_kwargs["author"])
        # chapter = Chapter(**chapter_kwargs)
        chapter = g.chapter

        # chapter.sync_comfyui_images(image_xml)
        # 'modes' are a simple shorthand for the expected inputs and outputs.
        # Here, if we have an input image, we provide
        # ti2i options that would utilize it.
        if chapter.is_first_image(image_xml):
            modes = ["t2i"]
        else:
            modes = ["ti2i", "t2i"]

        # new vs. existing workflow?
        #
        # new:
        #   choose a workflow template
        #   choose to "run" the workflow
        #   progress bar and see the new image in-place when it finishes

        # this is a URL that will create the requested workflow then
        # redirect to comfyui.
        choose_workflow_url = url_for(
            "library.book.chapter.images.comfyui_workflow_choose",
            **chapter.kwargs,
            image_index=image_xml.attrs["index"],
        )

        templates_by_mode = {
            mode: list(get_workflow_templates(interface="ui", mode=mode)) for mode in modes
        }
        selected_workflow = selected_image_workflow(
            image_xml, [template for group in templates_by_mode.values() for template in group],
            const.DEFAULT_IMAGE_TEMPLATE,
        )

        workflow_selector = f"""
        <div style="width: 100%">
            <wa-select 
                class="smooth {with_class}"
                label="Choose Workflow"
                name="workflow_image_template"
                id="workflow-selector-select"
                hx-put="{choose_workflow_url}"
                hx-swap="none"
                hx-trigger="change"
                value="{html.escape(selected_workflow, quote=True)}">
        """

        first = True
        many = len(modes) > 1

        for mode in modes:
            if not first:
                workflow_selector += "<wa-divider></wa-divider>"

            first = False
            if many:
                workflow_selector += f"<small>{mode.upper()} workflows:</small>"

            for template in templates_by_mode[mode]:
                selected = " selected" if selected_workflow == template.id else ""
                workflow_selector += (
                    f'<wa-option{selected} value="{html.escape(template.id, quote=True)}">'
                    f'{html.escape(template.name)}</wa-option>'
                )

        workflow_selector += """
            </wa-select>
        </div>"""

        workflow_url = url_for(
            "library.book.chapter.images.comfyui_workflow_run",
            **chapter.kwargs,
            image_index=image_xml.attrs["index"],
        )

        workflow_run_button = f"""<wa-button
            id="workflow-run-button"
            class="smooth {with_class}"
            hx-post="{workflow_url}"
            hx-swap="none"
            hx-trigger="click"
        >
            Generate Image
        </wa-button>"""

        workflow_url = url_for(
            "library.book.chapter.images.comfyui_workflow_open",
            **chapter.kwargs,
            image_index=image_xml.attrs["index"],
            animation=False,
            mode="*",
            video_index=0,
        )

        workflow_link_button = f"""
        <wa-button 
            href="{workflow_url}"
            hx-get="{workflow_url}"
            target="_blank"
            variant="brand" 
            appearance="accent"
            pill>
            <wa-icon src="/static/images/comfyui.svg"></wa-icon>
        </wa-button> 
        """

        # workflow_link_button = images.htmx.workflow_link_button(
        #     chapter,
        #     image_xml,
        #     animation=False,
        # )

        scene_workshop = image_scene_workshop(chapter, image_xml)

        # prompt = chapter.get_prompt(image_xml)

        out = f"""
        {workflow_selector}
    
        {scene_workshop}
       
        <div class="wa-cluster">
            {workflow_run_button}
            {workflow_link_button}
        </div>"""
        return out

        # <div class="wa-stack" style="width: 100%">
        #     <div class="wa-flank:end wa-align-items-end">
        #         <div><wa-textarea disabled resize="auto" name="scene" value="{html.escape(chapter.get_scene(image_xml))}"></wa-textarea></div>
        #         <wa-button>Edit Scene</wa-button>
        #     </div>
        # </div>

    def generate_image(self, image_xml, force=False) -> bytes:
        # the old way, obsolete.
        clip_prompt = image_xml.attrs.get("clip_prompt", "")
        t5_prompt = image_xml.attrs.get("t5_prompt", "")

        paragraph = image_xml.getparent("paragraph")

        # fallback to the old "prompt" if there is one
        if not clip_prompt and not t5_prompt:
            if "prompt" in image_xml.attrs:
                clip_prompt = image_xml.attrs["prompt"]
                t5_prompt = clip_prompt
                del image_xml.attrs["prompt"]
                image_xml.attrs["clip_prompt"] = clip_prompt
                image_xml.attrs["t5_prompt"] = t5_prompt

        image_fn = images.get_image_fn(
            prompt=f"{clip_prompt}_{t5_prompt}",  # for hash purposes
            loras=[],
            paragraph_dir=paragraph.attrs["dir"],
            image_index=image_xml.attrs["index"],
        )

        src = os.path.basename(image_fn)

        # retrieve, or build, the image.
        images.get_flux_image(
            image_fn=os.path.join(
                const.LIBRARY_DIR, paragraph.attrs["dir"], os.path.basename(src)
            ),
            clip_prompt=clip_prompt,
            t5_prompt=t5_prompt,
            force=force,
        )

        image = Image.open(image_fn)
        target_size = (const.IMG_TARGET_WIDTH, const.IMG_TARGET_HEIGHT)
        if image.size != target_size:
            log.info(f"Resizing image from {image.size} to {target_size}")
            image = image.resize(target_size)
            image.save(image_fn)

        image_xml.attrs["src"] = src


registry.add(ComfyWorkflowUI)
