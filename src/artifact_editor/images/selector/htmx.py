import glob
import os
import shutil
from typing import TYPE_CHECKING

from bs4 import Tag
from flask import url_for
from PIL import Image

import const
import logger
from artifact_editor.images import (
    images,
)
from artifact_editor.tools import (
    as_int,
)

if TYPE_CHECKING:
    from artifact_editor.chapter.chapter import Chapter

from .selector import registry as selector_registry

log = logger.log(__name__)


def gather_candidate_images(image_xml: Tag, chapter: Chapter):
    image_paths = set()
    paragraph = image_xml.find_parent("paragraph")
    paragraph_dir = chapter.get_paragraph_dir(paragraph.attrs["index"])
    
    comfy_prefix = f"{chapter.nice}_img_{as_int(image_xml.attrs['index'])}"
    for fn in glob.glob(
        os.path.join(
            const.COMFY_DIRS["artifactserver"]["OUTPUT_DIR"], 
            comfy_prefix + "*.png"
        )
    ):
        destination = os.path.join(
            const.LIBRARY_DIR,
            paragraph_dir,
            os.path.basename(fn).removeprefix(chapter.nice + "_")
        )

        if not os.path.exists(destination):
            # copy, not move so it doesn't disappear from comfyui
            shutil.copy(fn, destination)
            log.info(f"Copied {fn} to {destination}")

    # if 'index' in image_xml.attrs:
    image_index = as_int(image_xml.attrs["index"])

    #     for image_path in images.all_images_by_image_index(
    #         paragraph_dir,
    #         image_index
    #     ):
    #         image_paths.add(image_path)
   
    # log.info(f"Candidate images: {sorted(list(image_paths))}")
    return sorted(
        images.all_images_by_image_index(
            paragraph_dir,
            image_index
        )
    )
    

def gather_candidate_videos(image_xml: Tag, chapter: Chapter):
    image_paths = set()
    paragraph = image_xml.find_parent("paragraph")
    paragraph_dir = chapter.get_paragraph_dir(paragraph.attrs["index"])
    
    comfy_prefix = f"{chapter.nice}_img_{as_int(image_xml.attrs['index'])}"
    comfy_output_dir = const.COMFY_DIRS["artifactserver"]["OUTPUT_DIR"]
    comfy_globs = [
        os.path.join(comfy_output_dir, comfy_prefix + "*.mp4"),
        os.path.join(comfy_output_dir, "video", comfy_prefix + "*.mp4"),
    ]
    comfy_videos = []
    for comfy_glob in comfy_globs:
        matches = sorted(glob.glob(comfy_glob))
        log.info(
            "Searching ComfyUI output for animation candidates",
            image_index=image_xml.attrs["index"],
            pattern=comfy_glob,
            matches=matches,
        )
        comfy_videos.extend(matches)

    for fn in comfy_videos:
        destination = os.path.join(
            const.LIBRARY_DIR,
            paragraph_dir,
            os.path.basename(fn).removeprefix(chapter.nice + "_")
        )

        if not os.path.exists(destination):
            # copy, not move so it doesn't disappear from comfyui
            shutil.copy(fn, destination)
            log.info(f"Copied {fn} to {destination}")

    # if 'index' in image_xml.attrs:
    image_index = as_int(image_xml.attrs["index"])

    #     for image_path in images.all_images_by_image_index(
    #         paragraph_dir,
    #         image_index
    #     ):
    #         image_paths.add(image_path)
   
    # log.info(f"Candidate images: {sorted(list(image_paths))}")
    return sorted(
        images.all_videos_by_image_index(
            paragraph_dir,
            image_index
        )
    )
    

def get_mode_selector(src, aspect, image_xml):

    mode_selector = f"""
    <form>
    <input type="hidden" name="src" value="{src}"></input>
    <input type="hidden" name="image_index" value="{image_xml.attrs.get("index", 0)}"></input>
    <div class="wa-cluster">
        <wa-select
            hx-put="selector/transformation_options"
            hx-target="#transformation_options"
            hx-swap="innerHTML"
            hx-trigger="change delay:500ms"
            name="mode"
            id="mode-select"
            value="{image_xml.attrs.get("mode","scale")}">"""

    for key, transformation in selector_registry.iterate_transformations():
        mode_selector += f'<wa-option value="{key}" {"selected" if key==image_xml.attrs.get("mode", "") else ""}>{transformation.cosmetic}</wa-option>'

    mode_selector += """</wa-select>
    <div id="transformation_options">
    </div>

    </div>
    </form>
    """
    return mode_selector


def image_metadata(chapter: Chapter, image_xml: Tag, src:str):   
    log.debug(f"Getting image metadata for {chapter=}, {image_xml=}, {src=}")
    aspect = chapter.get_aspect()
    
    image_index = image_xml.attrs["index"]
    paragraph_xml = image_xml.find_parent("paragraph")

    use_image_url = url_for(
        "library.book.chapter.images.selector.use_image",
        **chapter.kwargs,
        image_index=image_index,
    )
    verify_image_url = url_for(
        "library.book.chapter.images.selector.verify_image",
        **chapter.kwargs,
        image_index=image_index,
    )

    image_src = image_xml.attrs.get("src")

    if image_src and src and (os.path.basename(src) == os.path.basename(image_src)):
        log.info(
            "src matches image_xml.attrs['src'], disabling 'use' button", 
            src=src, image_xml_src=image_src
        )
        verify_variant = "brand"
        disabled=" disabled"
    else:
        log.info(
            "src does not match image_xml.attrs['src'], enabling 'use' button",
            src=src, image_xml_src=image_src
        )
        verify_variant = "neutral"
        disabled=""

    use_button = f"""
        <wa-button 
            hx-post="{use_image_url}" 
            hx-on::before-request="beforeRequest(this,event)" 
            hx-on::after-request="afterRequest(this,event)" 
            hx-vals="js:{{image_index: {image_index}, selectedImage: getSelectedImageSrc()}}" 
            hx-swap="outerHTML" 
            variant="brand"
            appearance="accent" 
            class=""{disabled}>Choose</wa-button>
    """
    
    verify_button = f"""
        <wa-button 
            hx-post="{verify_image_url}" 
            hx-on::before-request="beforeRequest(this,event)" 
            hx-on::after-request="afterRequest(this,event)" 
            hx-vals="js:{{image_index: {image_index}, selectedImage: getSelectedImageSrc()}}" 
            hx-swap="outerHTML" 
            variant="{verify_variant}" 
            appearance="accent" 
            class="">Verify</wa-button>
    """

    delete_button = f"""
        <wa-button 
            hx-delete="selector/" 
            hx-on::before-request="beforeRequest(this,event)" 
            hx-on::after-request="removeCarouselImage(this,event)"
            hx-vals="js:{{image_index: {image_index}, selectedImage: getSelectedImageSrc()}}" 
            hx-swap="none" 
            variant="danger" 
            appearance="accent" 
            class="">Delete</wa-button>
    """
        
    # image_metadata_buttons(image_index, 'selector', verify_variant="neutral")

    if src:
        base_fn = os.path.basename(src)

        image_pfn = os.path.join(
            const.LIBRARY_DIR,
            chapter.get_paragraph_dir(paragraph_xml.attrs['index']),
            base_fn
        )

        if os.path.exists(image_pfn) and os.path.isfile(image_pfn):
            out = """
            <div id="image_metadata" style="max-width: 50em;">
            """

            image = Image.open(image_pfn)
            i_width, i_height = image.size
            
            mode_selector = get_mode_selector(
                os.path.basename(src), aspect, image_xml
            )
            
            out += f"""
                <div><b>{os.path.basename(src)}</b></div>
                <div>Dimensions: {i_width}x{i_height}</div>
                <div>Mode: {image.mode}</div>
                {mode_selector}
                <div class="wa-cluster wa-gap-3xs">
                    {use_button}
                    {verify_button}
                    {delete_button}
                </div>
            </div>
            """
        else:
            log.warning(f"Image file {image_pfn} not found.")
            out = f"""
            <div id="image_metadata" style="max-width: 50em;">
                <div><b>{os.path.basename(src)}</b></div>
                <div>Image details are unavailable for this file.</div>
                <div class="wa-cluster wa-gap-3xs">
                    {use_button}
                    {verify_button}
                    {delete_button}
                </div>
            </div>
            """
    else:
        log.info("No src provided, cannot display image metadata.")
        out = """
        <div id="image_metadata" style="max-width: 50em;">
            <div>No image selected.</div>
        </div>
        """          

    return out

def image_selector(
    chapter,
    image_xml: Tag,
    verify_variant="neutral",
):
    """
    This is the content of the "selector" tab in the image workshop.
    """
    # list of image filenames
    all_images = set(
        gather_candidate_images(
            image_xml=image_xml,
            chapter=chapter
        )
    )
    # edge case that isn't really an edge case:
    #  if the previous image is animated, we want the
    #  _last_ frame of the animation to be one of the 
    #  image options so we can pick up where we left off.
    previous_image_xml = image_xml.find_previous("image")
    if previous_image_xml is not None:
        # is it animated?
        if previous_image_xml.attrs.get("animation_method_00", "false").lower() != "false":
            # yes, it is animated.
            destination_frame = os.path.join(
                const.LIBRARY_DIR,
                chapter.get_paragraph_dir(image_xml.find_parent("paragraph").attrs["index"]),
                f"img_{image_xml.attrs['index']}_last_frame_of_previous.png"
            )
            
            if not os.path.exists(destination_frame):
                frame_filename = chapter.get_last_frame(previous_image_xml)
                
                if frame_filename is None:
                    log.warning(
                        f"Previous image {previous_image_xml.attrs['index']} is animated, but no frames found."
                    )
                    chapter.video_to_frames(previous_image_xml)

                # copy it to the current paragraph dir with a name suitable for
                # the current image index.
                if frame_filename:
                    source_frame = frame_filename

                    shutil.copy(source_frame, destination_frame)
                    log.info(f"Copied last frame of animation from {source_frame} to {destination_frame}")
                    all_images.add(os.path.basename(destination_frame))

    log.info(f"All images: {all_images}")

    camera_action = ""

    image_index = image_xml.attrs["index"]
    
    paragraph = image_xml.find_parent("paragraph")
    fullscreen = paragraph.attrs.get("fullscreen", "false").lower() == "true"

    # this file ought to exist, it's the currently selected image for this image
    # slot.
    img_src = os.path.basename(image_xml.attrs.get("src", ""))
    image_file = ""
    if img_src:
        image_file = os.path.join(
            const.LIBRARY_DIR,
            chapter.get_paragraph_dir(paragraph.attrs["index"]),
            img_src
        )

    if os.path.exists(image_file):       
        camera_action = images.get_camera_options(chapter, image_file, image_xml=image_xml)
        if camera_action:
            camera_action = f'<div class="wa-cluster">{camera_action}</div>'
        
    # TODO: skip the carousel when there is only one image.
    log.info(f"impedence fullscreen={fullscreen} all_images={all_images}")
    
    src = None
    if "src" in image_xml.attrs:
        src = os.path.join(
            const.LIBRARY_DIR, 
            chapter.get_paragraph_dir(paragraph.attrs["index"]), 
            image_xml.attrs["src"]
        )

    if src is None and len(all_images) > 0:
        # if there is no src, but there are images, pick the first one.
        src = os.path.join(
            const.LIBRARY_DIR, 
            chapter.get_paragraph_dir(paragraph.attrs["index"]), 
            list(all_images)[0]
        )
    
    if src and os.path.exists(src):
        imageurl = url_for(
            'library.book.chapter.images.show_image_by_index',
            author=chapter.author.name,
            title=chapter.title,
            chapter_number=chapter.number,
            language=chapter.language,
            height=400,
            image_index=image_index
        ) + f"?t={os.path.getmtime(src)}"  # cache buster

        i_metadata = image_metadata(chapter, image_xml, src)

                    # <img 
                    #     class="carousel-big-picture img_{image_index}" 
                    #     style="width:400px;height:400px;object-fit:contain;--aspect-ratio:1/1"
                    #     src="{imageurl}"></img>

        out = f"""
            <div id="image_selection_widget">
                <div class="wa-cluster">

<div id="cropper-container">
    <cropper-canvas style="min-width:400px;width:400px;min-height:400px;height:400px;" background>
        <img 
            class="carousel-big-picture" 
            src="{imageurl}"
            style="width:400px;height:400px;object-fit:contain;--aspect-ratio:1/1"
        ></img>
        <cropper-selection id="selection-area" aspect-ratio="1"></cropper-selection>
    </cropper-canvas>
</div>

                    {i_metadata}

                </div>
            </div>"""
    else:
        # onClick="chooseRegion(this,event)"
        # blank slate, we've got nothing worth having.
        i_metadata = image_metadata(chapter, image_xml, None)

        out = f"""
           <div id="image_selection_widget">
                <img 
                    class="carousel-big-picture" 
                    src="/static/images/x.png"
                    style="width:400px;height:400px;object-fit:contain;--aspect-ratio:1/1"
                ></img>
                {i_metadata}
           </div>"""

    out += """
           <div class="thumbnails">
               <div class="scroller">
    """

    for imagefile in all_images:
        imagefile = os.path.basename(imagefile)

        iurl = url_for(
            'library.book.chapter.images.selector.get_alternate_image',
            author=chapter.author.name,
            title=chapter.title,
            chapter_number=chapter.number,
            language=chapter.language,
            image_index=image_index,
            filename=imagefile
        )
        metadata_url = url_for(
            'library.book.chapter.images.selector.get_image_metadata',
            author=chapter.author.name,
            title=chapter.title,
            chapter_number=chapter.number,
            language=chapter.language,
            image_index=image_index,
        )

        if src and imagefile == os.path.basename(src):
            out += f'<img class="image active" src="{iurl}" data-metadata-url="{metadata_url}"></img>\n'
        else:
            out += f'<img class="image" src="{iurl}" data-metadata-url="{metadata_url}"></img>\n'

    out += """
        </div>
    </div>"""

    return out
