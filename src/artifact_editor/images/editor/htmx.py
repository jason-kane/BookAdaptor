import html
from PIL import Image

from torch import div
import logger
from flask import request, url_for, make_response
import os

from artifact_editor.characters import characters
from artifact_editor.tools import (
    generic_button
)

from . import const as editor_const

import const

log = logger.log(__name__)


def image_editor_workshop(
    chapter,
    image_xml,
):
    image_index = image_xml.attrs["index"]
    paragraph = image_xml.find_parent("paragraph")
    aspect = chapter.get_aspect()
   
    if "src" not in image_xml.attrs:
        # gonna have to pull that ripcord jeff, we're not going make it back
        # to the carrier.
        return """
        <div id="viewer" hx-swap-oob="true" hx-swap="innerHTML" class="wa-stack">
            <h3>No Image</h3>
        </div>"""
        
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
                **chapter.kwargs,
                height=0,  # full size
                image_index=image_index,
            )
            + f"?t={os.path.getmtime(image_pfn)}"
        )  # cache buster
        i_width, i_height = image.size

        default_outpaint_prompt = (
            "Expand the provided image to the new "
            "dimensions while maintaining "
            "the original style and composition.")

        # fullscreen == no text, we want the fullscreen dimensions
        outpaint_button = ""
        crop_button = ""
        if paragraph.attrs.get('fullscreen', 'false').lower() == 'true':   
            # have we already altered this image?
            if i_width == 1024 and i_height == 1024:
                outpaint_button = f"""
                <div class="wa-stack wa-gap-3xs">
                    <wa-textarea
                        name="outpaint_description"
                        label="Outpaint description"
                        value="{html.escape(image_xml.attrs.get('outpaint_description', default_outpaint_prompt))}"
                    ></wa-textarea>
                    <div class="wa-cluster wa-gap-3xs wa-align-items-start">
                        <wa-button 
                            hx-post="editor/outpaint_to_fullscreen" 
                            hx-on::before-request="beforeRequest(this,event)" 
                            hx-on::after-request="afterRequest(this,event)" 
                            hx-vals="js:{{image_index: {image_index}}}"
                            hx-include="[name='outpaint_description']"
                            hx-swap="outerHTML" 
                            variant="neutral" 
                            appearance="accent" 
                            class="">Outpaint to Fullscreen</wa-button>

                        <wa-button 
                            href="editor/comfy_outpaint_to_fullscreen"
                            hx-get="editor/comfy_outpaint_to_fullscreen"
                            hx-include="[name='outpaint_description']"
                            target="_blank"
                            variant="brand" 
                            appearance="accent"
                            pill>
                            <wa-icon src="/static/images/comfyui.svg"></wa-icon>
                        </wa-button>                
                    </div>
                </div>"""                       

            if (
                (
                    i_width >= const.GEOMETRY[aspect]["HSIZE"]
                    and
                    i_height >= const.GEOMETRY[aspect]["VSIZE"]
                ) and (
                    i_width != const.GEOMETRY[aspect]["HSIZE"]
                    or
                    i_height != const.GEOMETRY[aspect]["VSIZE"]
                )
            ):
                # it's too big, button to crop it down (centered)
                crop_button = f"""<wa-button 
                    hx-post="editor/crop_to_fullscreen" 
                    hx-swap="outerHTML" 
                    variant="neutral" 
                    appearance="accent" 
                    class="">Crop to {const.GEOMETRY[aspect]["HSIZE"]}x{const.GEOMETRY[aspect]["VSIZE"]}</wa-button>"""

        wrap = f"""
        <div id="viewer" hx-swap-oob="true" hx-swap="innerHTML" class="wa-stack">
            <div class="wa-cluster wa-gap-sm wa-align-items-start">
                <img style="max-width: 50%" src="{imageurl}"></img>
                <div>
                    <h3>{basesrc}</h3>
                    <h4>{image.size[0]}x{image.size[1]} {image.mode}</h4>
                    {outpaint_button}
                    {crop_button}
                </div>
            </div>
        </div>"""

    else:
        wrap = """<div id="viewer" hx-swap-oob="true" hx-swap="innerHTML" class="wa-stack">
            <h3>No Image File</h3>
        </div>"""

    return wrap




def datastack(chapter, image_xml):
    """
    """
    if image_xml is None:
        return "<div>No image data available.</div>" 
    
    out = """
    <div class="wa-stack wa-gap-sm">
    </div>
    """
    return out


