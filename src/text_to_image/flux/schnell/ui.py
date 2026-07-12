# Should only be sourced on the UI server side.
import html
import os
from urllib.parse import parse_qs, urlparse

from flask import request, url_for

import artifact_editor.images.htmx as images_htmx
import artifact_editor.styles.htmx as styles_htmx
from artifact_editor.author.author import Author
import const
import logger
from text_to_image.base import TextToImageProviderUI
from text_to_image.registry import registry
from PIL import Image
from artifact_editor import config

log = logger.log(__name__)
from artifact_editor import (
    images,
    tools,
)


# little harness so we play nice.
class FluxSchnellProviderUI(TextToImageProviderUI):
    key = "flux.schnell"
    cosmetic = "Flux Schnell"

    def generate_ui(self, image_xml, with_class="") -> bytes:
        """
        Response is a string of HTML components.
        """
        from artifact_editor.chapter.chapter import Chapter
        
        chapter_kwargs = tools.requestToChapterKwargs(request.url)
        chapter_kwargs['author'] = Author(chapter_kwargs['author'])
        chapter = Chapter(**chapter_kwargs)

        CLIP = f"""<div style="width: 49%"><wa-textarea 
                class="smooth {with_class}"
                hx-post="{chapter.url}/{chapter.language}/images/{image_xml.attrs['index']}"
                hx-swap="outerHTML transition:true"
                hx-trigger="change delay:500ms"
                hx-target="#prompt-textareas"
                label="CLIP Prompt"
                name="clip_prompt"
                id="prompt-clip-textarea"
                cols=70
                rows=7
                value="{html.escape(image_xml.attrs.get('clip_prompt', ''), quote=True)}"></wa-textarea>
            </div>"""
        T5 = f"""<div style="width: 49%"><wa-textarea 
                class="smooth {with_class}"
                hx-put="{chapter.url}/images/{image_xml.attrs['index']}"
                hx-swap="outerHTML transition:true"
                hx-trigger="change delay:500ms"
                hx-target="#prompt-textareas"
                label="T5 Prompt"
                name="t5_prompt"
                id="prompt-t5-textarea"
                cols=70
                rows=7
                value="{html.escape(image_xml.attrs.get('t5_prompt', ''), quote=True)}"></wa-textarea>
            </div>"""
        
        style = image_xml.attrs.get("style", "")
        if style == "":
            style = chapter.config.get("default_style", "")
            image_xml.attrs["style"] = style

        apply_style_url = url_for(
            "library.book.chapter.images.apply_style",
            **chapter.kwargs,
            image_index=image_xml.attrs["index"],
        )

        style_widget = styles_htmx.add_style_widget(
            selected_style=image_xml.attrs.get("style", ""),
            url=apply_style_url,
            target="#prompt-textareas",
        )        

        draw_button = images_htmx.draw_prompt_button(chapter, image_xml)
        scene_to_prompt_button = images_htmx.scene_to_prompt_button(chapter, image_xml)

        out = f"""
        <div class="wa-stack" style="width: 100%">
            <div style="width: 100%" class="wa-cluster" id="prompt-textareas">
                {CLIP}
                {T5}
            </div>
            {style_widget}
            {scene_to_prompt_button}
            {draw_button}
        </div>
        """
        return out

    def generate_image(self, image_xml, force=False) -> bytes:
        # the old way, obsolete.
        clip_prompt = image_xml.attrs.get("clip_prompt", "")
        t5_prompt = image_xml.attrs.get("t5_prompt", "")
        
        paragraph = image_xml.getparent("paragraph")

        # fallback to the old "prompt" if there is one
        if not clip_prompt and not t5_prompt:
            if 'prompt' in image_xml.attrs:
                clip_prompt = image_xml.attrs['prompt']
                t5_prompt = clip_prompt
                del image_xml.attrs['prompt']
                image_xml.attrs['clip_prompt'] = clip_prompt
                image_xml.attrs['t5_prompt'] = t5_prompt
        
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

registry.add(FluxSchnellProviderUI)