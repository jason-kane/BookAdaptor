# Should only be sourced on the UI server side.
from text_to_image.registry import registry
from flask import request, url_for
from text_to_image.base import TextToImageProviderUI
from . import config
import html
import logger
from urllib.parse import urlparse, parse_qs
import artifact_editor.styles.htmx as styles_htmx
log = logger.log(__name__)
from artifact_editor import tools


# little harness so we play nice.
class StableDiffusionProviderUI(TextToImageProviderUI):
    key = "sd.1.4"
    cosmetic = "Stable Diffusion 1.4"

    def generate_ui(self, image_xml, with_class="") -> bytes:
        """
        Response is a string of HTML components.
        """
        chapterurl = tools.requestToChapterURL(request.url)
        chapterdir = tools.chapterurl_to_chapterdir(chapterurl)
        # prompt_template_ui = prompt_template(chapterurl, image_xml)

        CLIP = f"""<div style="width: 49%"><wa-textarea 
                class="smooth {with_class}"
                hx-post="/{chapterurl}/images/{image_xml.attrs['index']}/actions/set_clip_prompt"
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
        
        style = image_xml.attrs.get("style", "")
        if style == "":
            book_config = config.get_config(chapterdir=chapterdir)
            style = book_config.get("default_style", "")
            image_xml.attrs["style"] = style

        apply_style_url = url_for(
            "library.book.chapter.images.apply_style",
            **self.chapter.kwargs,
            image_index=image_xml.attrs["index"],
        )

        style_widget = styles_htmx.add_style_widget(
            selected_style=image_xml.attrs.get("style", ""),
            url=apply_style_url,
            target="#prompt-textareas",
        )        
        
        selector = registry.selector(
            put_url=f"{chapterurl}/images/{image_xml.attrs['index']}",
            selected_key=image_xml.attrs.get("t2i", ""),
        )
        
        out = f"""
        <div id="prompt_panel" class="wa-stack wa-gap-sm" hx-swap-oob="true">
            {selector}
            <div style="width: 100%" class="wa-cluster" id="prompt-textareas">
                {CLIP}
                {style_widget}
            </div>
        </div>"""
        return out
        

registry.add(StableDiffusionProviderUI)