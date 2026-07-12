import glob
import html
import json
import os
import shutil
import time

from bs4 import BeautifulSoup
from flask import request, url_for
from PIL import Image

import animations
import const
import logger
import text_to_image
import transitions
from artifact_editor import (
    tools,
)
from artifact_editor.characters import characters
from artifact_editor.styles import htmx as styles_htmx
from artifact_editor.styles import styles as styles

from .camera import htmx as camera_htmx
from .scene import const as scene_const

log = logger.log(__name__)


CAMERA_CHOICES = scene_const.CAMERA_CHOICES

# initialize the registry to import the modules
# text_to_image.registry.search(gpu=False)


def workflow_link_button(chapter, image_xml, video_index=0, animation=False):
    """
    This will be a two-part, the button runs a local to take the template,
    replace the prompt and save it _as_ a workflow in comfyui.  _then_ we
    redirect to the workflow page for that workflow in a new tab.

    If animation is True, we want a workflow suitable for generating frames
    between the beginning of image_xml and the end.
    """
    log.info(
        "workflow_link_button",
        chapter=chapter.url,
        image_index=image_xml.attrs["index"],
        animation=animation,
    )
    animation = "animation" if animation else ""

    video_tag = f"_{video_index:02d}"

    workflow_url = url_for(
        "library.book.chapter.images.comfyui_workflow_open",
        **chapter.kwargs,
        image_index=image_xml.attrs["index"],
        animation=animation,
        mode="*",
        video_index=video_index,
    )

    if animation:
        # video
        workflow_template = image_xml.attrs.get(
            f"workflow_animation_template{video_tag}"
        )
    else:
        # image
        workflow_template = image_xml.attrs.get("workflow_image_template")

    tag = f"{chapter.nice}_img_{image_xml.attrs['index']}{video_tag}"
    if workflow_template:
        # the template is assigned when first applied, missing doesn't matter,
        # but we want to include it in the UI when it is available.
        tag = f"{workflow_template}_{tag}"

    workflow_button = f"""<wa-button
        hx-swap-oob="true"
        id="workflow-open-button-{image_xml.attrs['index']}{video_tag}"
        href="{workflow_url}"
        target="_blank"
        variant="brand" 
        appearance="accent"
        pill>
        <wa-icon src="/static/images/comfyui.svg"></wa-icon>
    </wa-button>
    """

    return workflow_button


def prompt_template(chapterurl, image_xml):
    # option widget to choose from a variety of pre-baked templates
    # for things like the book cover, author page, translator page and chapter page.
    out = f"""
    <div class="wa-cluster" id="prompt-templates">
        <div class="wa-card">
            <h3>Prompt Templates</h3>
            <wa-select 
                name="prompt_template"
                hx-trigger="change"
                hx-target="#prompt-textareas"
                hx-post="/{chapterurl}/images/{image_xml.attrs['index']}/actions/apply_prompt_template"
                id="prompt-template-select">
                <wa-option value="">Apply Simple Prompt Template</wa-option>
                <wa-option value="book_cover">Book Cover</wa-option>
                <wa-option value="author_page">Author Page</wa-option>
                <wa-option value="translator_page">Translator Page</wa-option>
                <wa-option value="chapter_page">Chapter Page</wa-option>
                <wa-option value="metadata_prompt">Metadata Prompt</wa-option>
            </wa-select>
        </div>
    </div>
    """
    return out


def prompt_panel(chapter, image_xml, with_class=""):
    """
    Returns html for an editor to modify the prompt.

    Things that call prompt_panel ought to save image_xml after calling this
    function; it may include a default value.
    """
    # which LLM are we prompting for?
    t2i = image_xml.attrs.get("t2i", "")
    if t2i == "":
        t2i = chapter.config.get("default_t2i", "flux.schnell")
        image_xml.attrs["t2i"] = t2i

    # text_to_image.registry.search(gpu=False)
    selector = text_to_image.registry.selector(
        put_url=url_for(
            "library.book.chapter.images.update",
            **chapter.kwargs,
            image_index=image_xml.attrs["index"],
        ),
        selected_key=image_xml.attrs.get("t2i", ""),
    )

    try:
        t2i_config = text_to_image.registry.get(t2i)(chapter).generate_ui(
            image_xml, with_class=with_class
        )
    except TypeError as e:
        log.error(f"Error generating T2I config UI for {t2i}: {e}")
        t2i_config = "<p>Error generating T2I configuration UI.</p>"
        raise

    image_index = image_xml.attrs["index"]
    # paragraph_index = image_xml.find_parent("paragraph").attrs.get("index", "")

    if "src" in image_xml.attrs:
        img_url = (
            url_for(
                "library.book.chapter.images.show_image_by_index",
                **chapter.kwargs,
                height=512,
                image_index=image_index,
            )
            + f"?t={int(time.time())}"
        )  # cache buster
        # img_url = f"{chapterurl}/paragraphs/{paragraph_index}/{image_xml.attrs['src']}"
    else:
        img_url = "/static/images/x.png"

    # respond with a swap-oob on the "prompt_panel" div
    return f"""
    <div id="prompt" class="wa-stack wa-gap-sm" hx-swap-oob="true">
        <img 
            class="image_{image_index}"
            style="height: 50%; width: 50%; align-self: center;"
            src="{img_url}"
        ></img>

        {selector}
        <div style="width: 100%" class="wa-cluster" id="prompt-textareas">
            {t2i_config}
        </div>
    </div>"""


def draw_all_missing_images_button(chapter):
    button_id = "missing-images-button"
    url = url_for(
        "library.book.chapter.images.draw_all_missing_images", **chapter.kwargs
    )

    # <wa-tooltip for="{button_id}">Use original text to create a prompt</wa-tooltip>
    out = f"""<div>
<wa-button
    id="{button_id}"
    hx-post="{url}"
    hx-on::before-request="beforeRequest(this,event)"
    hx-on::after-request="afterRequest(this,event)"
    variant="brand"
    pill
>Draw All Missing Images</wa-button></div>"""

    return out


def draw_all_missing_tmi_images_button(chapterurl):
    return tools.generic_button(
        chapterurl,
        category="images",
        tag="draw_all_missing_tmi_images",
        cosmetic="Draw All Missing TMI Images",
    )


def generate_missing_image_metadata_button(chapterurl):
    return tools.generic_button(
        chapterurl,
        category="images",
        tag="generate_missing_image_metadata",
        cosmetic="Generate Missing Image Metadata",
    )


def image_side_panel(image_xml, chapter, datastack=None, label="Previous"):
    tag = label.lower()
    out = f"""
    <div id="{tag}_panel" hx-swap-oob="true" style="width: 24%;">
        <div class="wa-stack wa-align-items-center">
    """

    if image_xml:
        idx = image_xml.attrs["index"]
        out += f"""<a 
    hx-on::click="chooseImage('{chapter.url}', '{chapter.language}', {idx}, 'image_strip')"
    href="{image_page_url(chapter, image_xml)}"><h4>{label}</h4></a>"""

        out += image_widget(
            chapter=chapter,
            image_xml=image_xml,
        )

    if datastack:
        out += f"""<div id="{tag}_image_datastack">
            {datastack(chapter=chapter, image_xml=image_xml)}
        </div>"""
    else:
        out += f"""<div id="{tag}_image_datastack"></div>"""

    out += """</div>
    </div>"""

    return out


def image_strip(chapter, image_xml):
    """ """
    previous_image = image_xml.find_previous("image")
    next_image = image_xml.find_next("image")

    out = image_side_panel(previous_image, chapter, label="Previous")

    out += image_strip_centerpiece(
        chapter=chapter,
        image_xml=image_xml,
    )

    out += image_side_panel(next_image, chapter, label="Next")
    return out


def image_transition_form(
    soup, filename, chapterurl, chapterdir, image_index, tag="solo"
):
    out = f'<div class="wa-stack" id="{tag}_image_transition_form_{image_index}">'
    # "transition_000004.mp4" on load
    log.info("filename:     %s", filename)

    all_images = soup.findAll("image")
    image = None
    try:
        image = all_images[image_index]
    except IndexError:
        # this happens with the last image.  if we want to transition out, we have to add
        # blank/black image to the end first.
        pass

    # /home/jkane/books/active/Aesop/Fables/chapter/0016/transitions/transition_000004.mp4
    #                          Aesop/Fables/chapter/0016/transitions/transition_000004.mp4
    full_filename = os.path.join(const.LIBRARY_DIR, chapterdir, "transitions", filename)

    if filename and os.path.exists(full_filename):
        out += f"""
                <video
                id="my-video"
                class="video-js"
                controls
                preload="auto"
                width="300"
                height="300"
                data-setup="{{}}"
              >
                <source src="transition/{filename}" type="video/mp4" />
                <p class="vjs-no-js">
                  To view this video please enable JavaScript, and consider upgrading to a
                  web browser that
                  <a href="https://videojs.com/html5-video-support/" target="_blank"
                    >supports HTML5 video</a
                  >
                </p>
              </video>
            """
    else:
        log.info("No transition video found at %s", full_filename)
        out += """
                <div style="height:300px;width:300px;border:1px solid white">No transition found</div>
            """

    # if we don't have an image, we don't have a place to put the transtion.
    # TODO figure out how to allow transitions at the end.
    if image:
        out += transitions.registry.selector(
            get_url=f"/{chapterurl}/images/{image_index}/transition/configuration",
            selected_key=image.attrs.get("transition_type", ""),
        )

        out += '<div id="transition_configuration">'
        out += get_transition_configuration_widgets(soup=soup, image_index=image_index)
        out += "</div>"

        # buttons
        out += f"""<div class="wa-cluster">
                    <wa-button 
                        hx-post="/{chapterurl}/images/{image_index}/transition/{tag}"
                        hx-target="#{tag}_image_transition_form_{image_index}"
                        hx-on::before-request="beforeRequest(this,event)"
                        hx-on::after-request="afterRequest(this,event)"
                        name="button">Build Transition</wa-button>
                    <wa-button 
                        hx-delete="/{chapterurl}/images/{image_index}/transition/{tag}"
                        hx-target="#{tag}_image_transition_form_{image_index}"
                        hx-on::before-request="beforeRequest(this,event)"
                        hx-on::after-request="afterRequest(this,event)"
                        variant="danger"
                        name="button">Delete</wa-button>
                </div>
            </div>"""

    return out


def get_transition_configuration_widgets(soup, image_index):
    all_images = soup.findAll("image")
    image = all_images[image_index]

    new_transition_type = request.args.get("transition_type", None)

    transition_type = image.attrs.get("transition_type", "")
    log.info(
        f"Got new_transition_type={new_transition_type} current_transition_type={transition_type}"
    )

    if new_transition_type is not None and transition_type != new_transition_type:
        log.info(f"saving new transition type = {new_transition_type}")
        image.attrs["transition_type"] = new_transition_type

    out = ""
    if transition_type:
        transition_obj = transitions.registry.get(transition_type)

        transition = transition_obj()
        log.info(f"Got transition {transition} for type {transition_type}")
        out += transition.get_configuration_widgets()

    log.info(f"Transition configuration widgets (empty is fine): {out}")
    return out


def previous_attempt(chapter, image_index):
    """
    wrapped here to make it easy to just regenerate the whole wiget.
    """
    out = """
        <div style="width: 22em; display: inline-block;" class="wa-stack" hx-swap-oob="true" hx-swap="innerHtml" id="previous_attempts">
        <div class="wa-cluster">
            <wa-button variant="success"
                hx-post="choose_video"
                hx-swap="none"
                hx-include="#video_playlist"
                hx-vals='js:{...get_selected_video(event, video_index=0)}'
                hx-trigger="click"
                appearance="filled" 
                >Choose Primary</wa-button>
            
            <wa-button variant="success"
                hx-post="choose_video"
                hx-swap="none"
                hx-include="#video_playlist"
                hx-vals='js:{...get_selected_video(event, video_index=1)}'
                hx-trigger="click"
                appearance="filled" 
                >Choose Secondary</wa-button>

            <wa-button 
                hx-delete="delete_video"
                hx-swap="none"
                hx-include="#video_playlist"
                hx-vals='js:{...get_selected_video(event, video_index=0)}'
                hx-trigger="click"
                appearance="filled" 
                variant="danger">Delete</wa-button>
        </div>

        <wa-video-playlist id="video_playlist" controls="standard">
    """
    # "video/baum-marv-001-bhgv_img_4_LTX23_flf__00"

    # these are all alternative videos that haven't been removed.
    for subdir, glob_str in [
        ("", f"{chapter.nice}_img_{image_index}_*_*_*.mp4"),
        ("video", f"{chapter.nice}_img_{image_index}_*_*_00001_.mp4"),
    ]:
        base_output_dir = const.COMFY_DIRS["artifactserver"]["OUTPUT_DIR"]
        if subdir:
            glob_path = os.path.join(base_output_dir, subdir, glob_str)
        else:
            glob_path = os.path.join(base_output_dir, glob_str)

        for fn in glob.glob(glob_path):
            #     f"{chapter.nice}_img_{image_index}_*.mp4"

            #      + list(glob.glob(
            #         os.path.join(
            #         const.COMFY_DIRS["artifactserver"]["OUTPUT_DIR"],
            #         "video",
            #         ffn
            #     )
            # )))):
            out += f"""
                <wa-video title="{os.path.basename(fn)}" poster="comfy{fn}.png">
                    <source src="comfy{fn}" type="video/mp4" />
                </wa-video>
            """

    out += """
        </wa-video-playlist>
        </div>
    """
    return out


def get_animation_configuration_widgets(
    chapter, image_index, video_index, default_method=None
):
    image_index = int(image_index)

    image_xml = chapter.get_image(image_index)
    video_tag = f"_{video_index:02d}"

    animation_method = image_xml.attrs.get(
        f"animation_method{video_tag}", default_method
    )
    log.info("Got animation method: %s", animation_method)

    out = ""
    if animation_method and animation_method != "disabled":
        animation_obj = animations.registry.get(animation_method)
        if animation_obj:
            animation = animation_obj()
            log.info(f"Got animation {animation} for method {animation_method}")
            out += animation.get_configuration_widgets(
                chapter, image_xml, video_index=video_index
            )
        else:
            log.error(f"No animation object found for method {animation_method}")

    return out, 200


def get_animation_url(chapter, image_index: int):
    # so.. you want to "play" the frames of this chapter that include the given
    # image.  This is just vanity buddy, you know that, right?
    image_xml = chapter.get_image(image_index)

    # if there is an animation, that is what we want.
    if image_xml.attrs.get("animation_method", "disabled") != "disabled":
        # looks like the .attrs will fail first..
        # like a stairway with no bannister.
        video_filename = os.path.splitext(os.path.basename(image_xml.attrs["src"]))[0]

        return url_for(
            "library.book.chapter.images.deliver_animation_video",
            **chapter.kwargs,
            image_index=image_index,
            filename=video_filename,
        )

    if image_xml.attrs.get("camera_motion", "static") != "static":
        video_filename = f"camera_{image_index}.mp4"
        return url_for(
            "library.book.chapter.images.camera.serve_camera_video",
            **chapter.kwargs,
            image_index=image_index,
            image_index2=image_index,
        )

    log.warning(
        "No animation or camera motion found for image %s, returning URL for the still image",
        image_index,
    )
    return url_for(
        "library.book.chapter.images.show_image_by_index",
        **chapter.kwargs,
        height=150,
        image_index=image_index,
    )


def skeleton():
    return """
    <div class="skeleton">
        <wa-skeleton style="
            border-radius: 0;
            clip-path: polygon(
            20% 0%,
            0% 20%,
            30% 50%,
            0% 80%,
            20% 100%,
            50% 70%,
            80% 100%,
            100% 80%,
            70% 50%,
            100% 20%,
            80% 0%,
            50% 30%
            );
            width: 372px;
            height: 372px;
        "></wa-skeleton>
    </div>    
    """


def animation_workshop(image_xml, chapter):
    llog = log.bind(
        prefix="[animation_workshop]",
        chapter=chapter.url,
        image_index=image_xml.attrs["index"],
    )
    
    players = []
    last_index = int(image_xml.attrs.get("animation_count", "1"))
    next_image = ""

    image_index = int(image_xml.attrs["index"])
    image_src = image_xml.attrs.get("src", "")

    for video_index in range(last_index):
        video_tag = f"_{video_index:02d}"

        if video_index == 0:
            video_filename = image_src.replace(".png", ".mp4")
        else:
            video_filename = image_src.replace(".png", f"_{video_index:02d}.mp4")

        workflow_template = image_xml.attrs.get(
            f"workflow_animation_template{video_tag}", ""
        )
        if workflow_template == "":
            log.warning('No workflow_animation_template found for %s', video_tag)

        paragraph = image_xml.find_parent("paragraph")

        full_filename = os.path.join(
            const.LIBRARY_DIR,
            chapter.get_paragraph_dir(paragraph.attrs["index"]),
            video_filename,
        )

        frame_dir = os.path.join(
            const.LIBRARY_DIR,
            chapter.get_paragraph_dir(paragraph.attrs["index"]),
            "animation",
            f"image_{int(image_xml.attrs['index']):06d}_{video_index:02d}",
        )

        replace_button_label = "Create Animation"
        replace_button_variant = "danger"
        buttons = []

        slgf_button = ""
        lgf_target = ""

        #
        # baum-marv-001-bhgv_img_3_LTX23__01
        # expected:
        #'/output/video/baum-marv-001-bhgv_img_3_LTX23*_00001_.mp4'

        # the video does not exist.  Try to find it and put
        # a copy where it belongs.
        if not os.path.exists(full_filename) and video_index == 0:
            llog.info('Looking for %s', full_filename)
            # backwards compatability, no tag == tag 0
            tagless_video_filename = (
                os.path.splitext(os.path.basename(image_src))[0] + ".mp4"
            )

            tagless_full_filename = os.path.join(
                const.LIBRARY_DIR,
                chapter.get_paragraph_dir(paragraph.attrs["index"]),
                tagless_video_filename,
            )

            if os.path.exists(tagless_full_filename):
                log.info(
                    "Found untagged animation video, copying it.",
                    tagless_full_filename=tagless_full_filename,
                    full_filename=full_filename,
                )
                shutil.copy(tagless_full_filename, full_filename)

        if video_index == 0:
            # this is animation 0, so if it's longer than the audio, we need to provide
            # a "Trim to Audio" button.  Trimming for videos after 0 is more
            # complicated.  Solve it when we need it.
            audio_frames = int(float(image_xml.attrs.get("frames")))
            os.makedirs(frame_dir, exist_ok=True)
            video_frames = len(os.listdir(frame_dir))
                
            if video_frames > audio_frames:
                # tempting to just auto-trim, but if the audio isn't actually
                # done that would really fork us up.
                trim_to_audio_url = url_for(
                    "library.book.chapter.images.trim_animation_to_audio",
                    **chapter.kwargs,
                    image_index=image_index,
                    video_index=video_index,
                )
                trim_to_audio_button = f"""
                <wa-button hx-post="{trim_to_audio_url}"
                    hx-swap="none"
                    hx-trigger="click"
                    variant="brand">Trim {int(video_frames - audio_frames)} Excess Frames</wa-button>"""
                buttons.append(trim_to_audio_button)


        if not os.path.exists(full_filename):
            # _best_ match

            # /output/aeso-fabl-031-twm6_img_5_LTX23_flf_00*.mp4
            # /output/aeso-fabl-031-twm6_img_5_LTX23_flf_00*.mp4
            # /output/aeso-fabl-031-twm6_img_5_LTX23_flf_00_00001_.mp4
            # /output/video/aeso-fabl-031-twm6_img_5_LTX23_flf_00_00001_.mp4

            mp4_filename = os.path.join(
                const.COMFY_DIRS["artifactserver"]["OUTPUT_DIR"],
                f"{chapter.nice}_img_{image_xml.attrs['index']}_{workflow_template}{video_tag}*.mp4",
            )

            llog.info('looking for %s.. checking comfyui output dir for %s', full_filename, mp4_filename)
            # /output/aeso-fabl-031-twm6_img_4_LTX23_00_00001_.mp4
            # Check the comfyui output directory for a copy of this video.

            # sloppier matches
            # check the "video" subdirectory.
            video_files = glob.glob(mp4_filename)
            llog.info('Found %d matches for %s', len(video_files), mp4_filename)

            if not video_files:
                expected_glob = os.path.join(
                    const.COMFY_DIRS["artifactserver"]["OUTPUT_DIR"],
                    "video",
                    f"{chapter.nice}_img_{image_xml.attrs['index']}_{workflow_template}{video_tag}*.mp4",
                )
                
                video_files = glob.glob(expected_glob)
                llog.info('Found %d matches for %s', len(video_files), expected_glob)

                # Log for failure modes.
                if len(video_files) > 1:
                    log.info(
                        "Multiple matches found.  That is a bad thing.",
                        mp4_filename=video_files,
                    )
                elif len(video_files) == 0:
                    log.warning(
                        "Expected to find an animation video",
                        expected_video=expected_glob,
                    )

            if video_files:
                log.info(
                    "Found animation video, copying it.",
                    mp4_filename=video_files,
                    full_filename=full_filename,
                )
                shutil.copy(video_files[0], full_filename)
            else:
                log.info("No animation video found", mp4_filename=video_files, full_filename=full_filename)
                # .. do the frames exist?  We can work with that.
                if os.path.exists(frame_dir) and len(os.listdir(frame_dir)) > 0:
                    log.info(
                        "Animation frames found for %s, but no video.  Rebuilding video from frames.",
                        full_filename,
                    )

                    tools.assemble_mp4(
                        fps=const.FPS,
                        framedir=frame_dir,
                        wavfile=None,
                        videofile=full_filename,
                        image_match='frame_%06d.png'
                    )
        
        # some things only make sense after we've when we have
        if video_filename and os.path.exists(full_filename):

            if not os.path.exists(frame_dir) or len(os.listdir(frame_dir)) == 0:
                log.info(
                    f"Animation video found for {full_filename}, extracting frames..."
                )
                os.makedirs(frame_dir, exist_ok=True)
                tools.extract_frames(full_filename, frame_dir)

            video_player = f"""<wa-video
                    controls="standard"
                    title="",
                    poster="">
                <source src="animation/{video_filename}" type="video/mp4" />
                </wa-video>"""

            # hurl the widgets in because they are likely to be a
            # piggy bunch and we won't always want it.
            set_lgf_url = url_for(
                "library.book.chapter.images.get_last_good_frame_widget",
                **chapter.kwargs,
                image_index=image_index,
            )

            set_lgf_button = f"""
            <wa-button hx-post="{set_lgf_url}"
                hx-swap="none"
                hx-trigger="click"
                hx-target="#lgf"
                variant="brand">Set Last Good Frame</wa-button>"""
            buttons.append(set_lgf_button)

            lgf_target = '<div id="lgf"></div>'

        else:
            log.info("Video file not found.", full_filename=full_filename)
            # no video, just show the image.
            if video_index == 0:
                # for the first video, the proper image to show is the generated base image.
                img_url = url_for(
                    "library.book.chapter.images.show_image_by_index",
                    author=chapter.author.name,
                    title=chapter.title,
                    chapter_number=chapter.number,
                    language=chapter.language,
                    height=372,
                    image_index=image_index,
                )
            else:
                # for subsequent videos, the proper image to show is the last frame of the previous video_index video.
                frame_filename = chapter.get_last_frame(
                    image_xml=image_xml,
                    frame_index=None,
                    recursed=False,
                    video_index=video_index - 1,
                )
            
                destination_frame = chapter.get_image_filename(image_xml)
                destination_frame = destination_frame.replace(".png", f"_lastframe_{video_index - 1:02d}.png")

                shutil.copy(frame_filename, destination_frame)
                log.info(f"Copied last frame of animation from {frame_filename} to {destination_frame}")
                
                img_url = url_for(
                    "library.book.chapter.images.selector.get_alternate_image",
                    **chapter.kwargs,
                    image_index=image_index,
                    filename=os.path.basename(destination_frame)
                )

            video_player = f"""
                <div style="height:372px;width:372px;border:1px solid white">
                    <img src="{img_url}"></img>
                </div>
            """

            # is this the last image in the paragraph?
            if chapter.is_last_image(image_xml):
                # the last image gets a straight TI2V from this start frame
                next_image = skeleton()
            else:
                # all other images get first/last frame animations.
                next_img_url = url_for(
                    "library.book.chapter.images.show_image_by_index",
                    author=chapter.author.name,
                    title=chapter.title,
                    chapter_number=chapter.number,
                    language=chapter.language,
                    height=372,
                    image_index=image_index + 1,
                )

                next_image = f"""
                    <div style="height:372px;width:372px;border:1px solid white">
                        <img src="{next_img_url}"></img>
                    </div>
                """

            replace_button_label = "Create Animation"
            replace_button_variant = "brand"

        animation_module_selector = animations.registry.selector(
            get_url=url_for(
                "library.book.chapter.images.set_animation_method",
                **chapter.kwargs,
                image_index=image_index,
                video_index=video_index,
            ),
            selected_key=image_xml.attrs.get(f"animation_method{video_tag}", ""),
            video_index=video_index,
        )

        animation_module_ui = f'<div id="animation_configuration_{video_index:02d}">'
        animation_module_ui += get_animation_configuration_widgets(
            chapter=chapter,
            image_index=image_index,
            video_index=video_index,
        )[0]
        animation_module_ui += "</div>"

        build_animation_url = url_for(
            "library.book.chapter.images.build_animation",
            **chapter.kwargs,
            image_index=image_index,
        )

        build_animation_button = f"""<wa-button hx-post="{build_animation_url}"
            hx-swap="none"
            hx-trigger="click"
            hx-target="#strip-centerpiece"
            variant="{replace_button_variant}">{replace_button_label}</wa-button>"""
        buttons.append(build_animation_button)

        workflow_button = workflow_link_button(
            chapter,
            image_xml,
            video_index=video_index,
            animation=True,
        )
        buttons.append(workflow_button)

        delete_animation_url = url_for(
            "library.book.chapter.images.delete_animation",
            **chapter.kwargs,
            image_index=image_index,
            video_index=video_index,
        )

        delete_animation_button = f"""<wa-button hx-delete="{delete_animation_url}"
            hx-swap="none"
            hx-trigger="click"
            variant="danger"
            hx-target="#strip-centerpiece">Delete Animation</wa-button>"""
        buttons.append(delete_animation_button)

        # {full_filename}
        players.append(f"""
            <div style="max-width: 23em; display: inline-block;" class="wa-stack" hx-swap-oob="true" hx-swap="innerHtml" id="animation_workshop_{video_index:02d}">
                {video_player}                
                <form>
                    <input type="hidden" name="video_index" value="{video_index}"></input>
                    {animation_module_selector}
                    {animation_module_ui}

                    <div class='wa-cluster'>
                        {slgf_button}
                        {lgf_target}
                        {"".join(buttons)}
                    </div>
                </form>
            </div>
        """)

    if next_image:
        players.append(next_image)

    add_button = """
        <div>
            <wa-button
                pill
                hx-post="additional_video"
                hx-swap="none"
                hx-trigger="click"
                variant="outline"
            >+ Add Video</wa-button>
        </div>
    """
    players.append(add_button)
    # video player if there are already animation frames
    # selector for the animation function
    # on selection, calls for the function's widgets
    # each function has its own widgets

    # all settings are persistent in the image tag
    # IE: pretty much all exactly the same as transitions
    # out = f"""
    # <div id="animation_workshop" hx-swap-oob="true" class="wa-stack">
    out = """
        <div 
            id="animation_workshop"
            style="width: 100%; overflow-x: auto; white-space: nowrap;" 
            class="wa-cluster wa-align-items-start"
        >"""

    for player in players:
        out += player

    pa = previous_attempt(chapter, image_index)
    if pa:
        # no history, no problem.
        out += pa

    out += "</div>"

    return out


def upload_image_workshop(chapter, image_xml):
    """
    Image upload tab content for the image workshop.
    """
    sources_directory = os.path.join(const.LIBRARY_DIR, chapter.chapterdir, "sources")
    os.makedirs(sources_directory, exist_ok=True)

    all_images_xml = chapter.get_xml().findAll("image")

    all_images = []
    for i in all_images_xml:
        src = i.attrs.get("src")
        if src:
            all_images.append(src.split("_")[-1])

    out = ""
    for image_file in sorted(os.listdir(sources_directory)):
        if os.path.splitext(image_file)[1] not in [
            ".png",
            ".jpg",
            ".jpeg",
            ".webp",
            ".bmp",
            ".gif",
        ]:
            continue

        if image_file in all_images:
            log.info("Skipping %s (already in use)", image_file)
            continue
        # else:
        #    log.info('%s is not already in use', image_file)

        image_path = os.path.join(sources_directory, image_file)
        img = Image.open(image_path)
        width, height = img.size

        set_image_source_url = url_for(
            "library.book.chapter.images.set_image_source",
            **chapter.kwargs,
            image_index=image_xml.attrs["index"],
        )

        out += f"""
        <div class="wa-card wa-clickable" 
            hx-post="{set_image_source_url}"
            hx-vals='js:{{"image_source": "{image_file}"}}'
            hx-target="#upload"
            >
            <img src="{url_for('library.book.chapter.images.serve_source_image', author=chapter.author.name, title=chapter.title, chapter_number=chapter.number, language=chapter.language, image_file=image_file)}" alt="{image_file}" style="max-width:200px;max-height:200px;"/>
            <div>
                <strong>{image_file}</strong><br/>
                {width} x {height} pixels
            </div>
        </div>
        """
    return out


def image_workshop(image_xml, chapter):
    image_index = int(image_xml.attrs["index"])

    upload_workshop_ui = upload_image_workshop(chapter, image_xml)

    tabs = []
    panels = []
    active = "prompt"
    image_tab_selector_url = url_for(
        "library.book.chapter.images.image_tab_selector",
        **chapter.kwargs,
        image_index=image_index,
    )

    show_viewer = image_xml.attrs.get("src", "") != ""
    if show_viewer:
        tabs.append(f"""
        <wa-tab
            hx-post="{image_tab_selector_url}"
            hx-vals='js:{{"group": "viewer"}}'
            hx-swap="none"
            panel="viewer">Viewer</wa-tab>""")

        panels.append("""<wa-tab-panel name="viewer">
            <div id="viewer" class="skeleton-paragraphs">
                <wa-skeleton class="square"></wa-skeleton>
            </div>
        </wa-tab-panel>""")

        # make viewer the default when it exists.
        active = "viewer"

    if True:
        # selector
        tabs.append(f"""
        <wa-tab
            hx-post="{image_tab_selector_url}"
            hx-vals='js:{{"group": "selector"}}'
            hx-swap="none"
            panel="selector">Selector</wa-tab>""")

        # tabs.append('<wa-tab panel="selector">Selector</wa-tab>')

        panels.append("""<wa-tab-panel name="selector">
            <div id="selector" class="skeleton-paragraphs">
                <wa-skeleton></wa-skeleton>
                <wa-skeleton></wa-skeleton>
                <wa-skeleton></wa-skeleton>
                <wa-skeleton></wa-skeleton>
                <wa-skeleton></wa-skeleton>
            </div>
        </wa-tab-panel>""")

        tabs.append(f"""
        <wa-tab
            hx-post="{image_tab_selector_url}"
            hx-vals='js:{{"group": "editor"}}'
            hx-swap="none"
            panel="editor">Editor</wa-tab>""")

        # tabs.append('<wa-tab panel="editor">Editor</wa-tab>')

        panels.append("""<wa-tab-panel name="editor">
            <div id="editor" class="skeleton-paragraphs">
                <wa-skeleton></wa-skeleton>
                <wa-skeleton></wa-skeleton>
                <wa-skeleton></wa-skeleton>
                <wa-skeleton></wa-skeleton>
                <wa-skeleton></wa-skeleton>
            </div>
        </wa-tab-panel>""")

        # Scene
        tabs.append(f"""
        <wa-tab
            hx-post="{image_tab_selector_url}"
            hx-vals='js:{{"group": "scene"}}'
            hx-swap="none"
            panel="scene">Scene</wa-tab>""")

        panels.append("""<wa-tab-panel name="scene">
            <div id="scene" class="skeleton-paragraphs">
                <wa-skeleton></wa-skeleton>
                <wa-skeleton></wa-skeleton>
                <wa-skeleton></wa-skeleton>
                <wa-skeleton></wa-skeleton>
                <wa-skeleton></wa-skeleton>
            </div>
        </wa-tab-panel>""")

        tabs.append(f"""
        <wa-tab
            hx-post="{image_tab_selector_url}"
            hx-vals='js:{{"group": "prompt"}}'
            hx-swap="none"
            panel="prompt">Prompt</wa-tab>""")

        panels.append("""<wa-tab-panel name="prompt">
            <div id="prompt" class="skeleton-paragraphs">
                <wa-skeleton></wa-skeleton>
                <wa-skeleton></wa-skeleton>
                <wa-skeleton></wa-skeleton>
                <wa-skeleton></wa-skeleton>
                <wa-skeleton></wa-skeleton>
            </div>                    
        </wa-tab-panel>""")

        # Upload
        tabs.append(f"""
        <wa-tab
            hx-post="{image_tab_selector_url}"
            hx-vals='js:{{"group": "upload"}}'
            hx-swap="none"
            panel="upload">Upload</wa-tab>""")

        panels.append(
            f"""<wa-tab-panel hx-swap="innerHTML" id="upload" name="upload">{upload_workshop_ui}</wa-tab-panel>"""
        )

        tabs.append(f"""
        <wa-tab
            hx-post="{image_tab_selector_url}"
            hx-vals='js:{{"group": "citation"}}'
            hx-swap="none"
            panel="citation">Citation</wa-tab>""")

        panels.append("""<wa-tab-panel hx-swap="innerHTML" id="citation" name="citation">
            <div class="skeleton-paragraphs">
                <wa-skeleton></wa-skeleton>
                <wa-skeleton></wa-skeleton>
                <wa-skeleton></wa-skeleton>
                <wa-skeleton></wa-skeleton>
                <wa-skeleton></wa-skeleton>
            </div>
        </wa-tab-panel>""")

    return f"""
    <wa-tab-group
        id="image-tab-group"
        active='{active}'
    >
        {''.join(tabs)}
        {''.join(panels)}
    </wa-tab-group>
    """


def image_strip_centerpiece(
    chapter,
    image_xml,
    default="image",
    verify_variant="neutral",
):
    """
    Side-by-side previous-current-next image creation environment.

    default is the 'image'/'animation' tab to show by default when we load.
    """
    log.debug("image_strip_centerpiece called with %s", image_xml)
    paragraph = image_xml.find_parent("paragraph")
    image_index = int(image_xml.attrs.get("index"))

    aspect = chapter.get_aspect()

    out = '<div class="wa-stack" id="strip-centerpiece" style="width:50%;">'

    image_workshop_ui = image_workshop(image_xml=image_xml, chapter=chapter)

    # some workshops do not require images.  others do.

    if "src" in image_xml.attrs:
        camera_workshop_ui = camera_htmx.camera_workshop(
            image_xml=image_xml, chapter=chapter
        )
        log.debug("camera workshop ready")

        transition_workshop_ui = image_transition_form(
            soup=chapter.get_xml(),
            filename=f"transition_{image_index:06}.mp4",
            chapterurl=chapter.url,
            chapterdir=chapter.chapterdir,
            image_index=image_index,
            tag="previous",
        )
        log.info("transition workshop ready")

        animation_workshop_ui = animation_workshop(
            image_xml=image_xml,
            chapter=chapter,
        )
        log.debug("animation workshop ready")
    else:
        camera_workshop_ui = "<div>Image required</div>"
        transition_workshop_ui = "<div>Image required</div>"
        animation_workshop_ui = "<div>Image required</div>"
        log.info("no image found - skipping animation workshop")

    style = image_xml.attrs.get("style", "")
    if style == "":
        style = chapter.config.get("default_style", "")
        image_xml.attrs["style"] = style

    # given image_xml, how do we get audio duration?
    try:
        audio_duration = int(image_xml.attrs.get("frames")) / const.FPS
    except (ValueError, TypeError) as err:
        if "frames" in image_xml.attrs:
            # we have a corrupted/invalid frames attribute, might work if we float it.
            audio_duration = int(float(image_xml.attrs.get("frames"))) / const.FPS
        else:
            log.debug(f"{err=}")
            log.debug("image_xml has no 'frames' attribute")

            audio_duration = None

    video_duration = 0
    for video_index in range(int(image_xml.attrs.get("animation_count", "1"))):
        # /home/jkane/books/active/Aesop/Fables/chapter/0024/animation/animation_000004
        animation_dir = os.path.join(
            const.LIBRARY_DIR,
            chapter.get_paragraph_dir(paragraph.attrs["index"]),
            "animation",
            f"image_{image_index:06d}_{video_index:02d}",
        )

        if os.path.exists(animation_dir):
            video_duration += len(os.listdir(animation_dir)) / const.FPS

    out += f"""
        <div class="wa-cluster">
            <div>{aspect}</div>
            <div><strong>Audio Duration:</strong> <em>{audio_duration}s</em></div>
            <div><strong>Video Duration:</strong> <em>{video_duration}s</em></div>
        </div>

        <wa-tab-group active='{default}'>
            <wa-tab panel="image">Image</wa-tab>
            <wa-tab panel="camera">Camera</wa-tab>
            <wa-tab panel="transition">Transition</wa-tab>
            <wa-tab panel="animation">Animation</wa-tab>

            <wa-tab-panel id="image-panel" name="image">{image_workshop_ui}</wa-tab-panel>
            <wa-tab-panel id="camera-panel" name="camera">{camera_workshop_ui}</wa-tab-panel>
            <wa-tab-panel id="transition-panel" name="transition">{transition_workshop_ui}</wa-tab-panel>
            <wa-tab-panel id="animation-panel" name="animation">{animation_workshop_ui}</wa-tab-panel>
        </wa-tab-group>
        """

    out += "</div>"
    return out


def scene_to_prompt_button(chapter, image_xml):
    button_id = "scene-to-prompt-button"
    url = url_for(
        "library.book.chapter.images.rebuild_prompt",
        **chapter.kwargs,
        image_index=image_xml.attrs["index"],
    )

    # <wa-tooltip for="{button_id}">Use scene data to create a prompt</wa-tooltip>
    out = f"""<div>
<wa-button
    id="{button_id}"
    hx-post="{url}"
    hx-target="#prompt"
    hx-swap="innerHTML"
    hx-on::before-request="beforeRequest(this,event)"
    hx-on::after-request="afterRequest(this,event)"
    variant="success"
>Import Scene</wa-button></div>"""

    return out


def create_prompt_button(chapter, image_xml):
    button_id = "create-prompt-button"
    url = f"{chapter.url}/images/{image_xml.attrs['index']}/actions/create_t5_prompt"

    # <wa-tooltip for="{button_id}">Use original text to create a prompt</wa-tooltip>
    out = f"""<div>
<wa-button
    id="{button_id}"
    hx-post="{url}"
    hx-target="#prompt"
    hx-swap="none"
    hx-on::before-request="beforeRequest(this,event)"
    hx-on::after-request="afterRequest(this,event)"
    variant="brand"
>Create Prompt</wa-button></div>"""

    return out


def create_fanciful_prompt_button(chapter, image_xml):
    button_id = "create-prompt-button"
    url = url_for(
        "library.book.chapter.images.create_fanciful_prompt",
        **chapter.kwargs,
        image_index=image_xml.attrs["index"],
    )

    # f"{chapter.url}/images/{image_xml.attrs['index']}/actions/create_fanciful_prompt"

    # <wa-tooltip for="{button_id}">Prompt loosely based on original text</wa-tooltip>
    out = f"""<div>
<wa-button
    id="{button_id}"
    hx-post="{url}"
    hx-target="#prompt"
    hx-swap="none"
    hx-on::before-request="beforeRequest(this,event)"
    hx-on::after-request="afterRequest(this,event)"
    variant="brand"
>Create Fanciful Prompt</wa-button></div>"""

    return out


def condense_image_prompt_button(chapter, image_xml):
    button_id = "condense-image-prompt-button"
    url = url_for(
        "library.book.chapter.images.condense_prompt",
        **chapter.kwargs,
        image_index=image_xml.attrs["index"],
    )

    # <wa-tooltip for="{button_id}">Tighten up the prompt</wa-tooltip>
    out = f"""<div>
<wa-button
    id="{button_id}"
    hx-post="{url}"
    hx-target="#prompt"
    hx-swap="none"
    hx-on::before-request="beforeRequest(this,event)"
    hx-on::after-request="afterRequest(this,event)"
    variant="brand"
>Tighten Prompt</wa-button></div>"""
    return out


def draw_styled_prompt_button(chapter, image_xml):
    """
    chapterurl: starts with "/"
    """
    button_id = "draw-styled-prompt-button"

    # <wa-tooltip for="{button_id}">Create a new image based on this prompt</wa-tooltip>
    # {chapter.url}/images/{image_xml.attrs['index']}?styled=true&force=true
    out = f"""<div>
<wa-button
    id="{button_id}"
    hx-post="{url_for('library.book.chapter.images.create_new_image', author=chapter.author.name, title=chapter.title, chapter_number=chapter.number, language=chapter.language, image_index=image_xml.attrs['index'], styled=True, force=True)}"
    hx-target="#prompt"
    hx-swap="innerHTML"
    hx-on::before-request="beforeRequest(this,event)"
    hx-on::after-request="afterRequest(this,event)"
    variant="brand"
>Draw Styled Prompt</wa-button></div>"""

    return out


# hx-include="#styled-prompt"


def draw_prompt_button(chapter, image_xml):
    """
    chapterurl: starts with "/"
    """
    button_id = "draw-prompt-button"

    # <wa-tooltip for="{button_id}">Create a new image based on this prompt</wa-tooltip>
    draw_url = url_for(
        "library.book.chapter.images.create_new_image",
        author=chapter.author.name,
        title=chapter.title,
        chapter_number=chapter.number,
        language=chapter.language,
        image_index=image_xml.attrs["index"],
        force=True,
    )
    out = f"""<div>
<wa-button
    id="{button_id}"
    hx-post="{draw_url}"
    hx-include="#styled-prompt"
    hx-target="#prompt"
    hx-swap="innerHTML"
    hx-on::before-request="beforeRequest(this,event)"
    hx-on::after-request="afterRequest(this,event)"
    variant="brand"
>Draw Prompt</wa-button></div>"""

    return out


def image_href(chapter, image_xml):
    """
    returns the href string for the literal, raw selected image .png.
    """
    image_index = image_xml.attrs["index"]

    imageurl = url_for(
        "library.book.chapter.images.show_image_by_index",
        author=chapter.author.name,
        title=chapter.title,
        chapter_number=chapter.number,
        language=chapter.language,
        height=0,
        image_index=image_index,
    )
    return imageurl


def image_page_url(chapter, image_xml):
    """
    the URL to get all the fancy widgets for this image
    """
    return (
        url_for(
            "library.book.chapter.images.base",
            author=chapter.author.name,
            title=chapter.title,
            chapter_number=chapter.number,
            language=chapter.language,
            image_index=image_xml.attrs["index"],
        )
        + "#image-strip"
    )


def image_widget(chapter, image_xml):
    """
    returns an html string for a clickable image that will nav to the
    selected image efficiently.
    """
    image_page_href = image_page_url(chapter, image_xml)
    href = image_href(chapter, image_xml)
    image_index = image_xml.attrs.get("index", 0)

    if image_xml.attrs.get("src"):
        # the image might exists
        out = f"""<a onclick="chooseImage('{chapter.url}', '{chapter.language}', {image_index}, 'image_strip'); return false;" href="{image_page_href}">
    <img style="max-width: 200px" src="{href}"></img>
</a>"""
    else:
        # the image definitely does _not_ exist, so we give them a clickable placeholder
        image_strip_url = image_page_url(chapter, image_xml)

        out = f"""<a onclick="chooseImage('{chapter.url}', '{chapter.language}', {image_index}, 'image_strip'); return false;" href="{image_page_href}">
    <img style="height: 300px;background-color: #777" src="/static/images/x.png"></img>
</a>"""

    return out


def meta_prompt(chapter, image, image_index):
    meta_prompt = image.attrs.get(
        "meta_prompt", "[SETTING] [TOD] [CAMERA] [FOCUS_CHARACTER] [CHARACTERS]"
    )

    return f"""<wa-textarea 
        label="Meta Prompt" 
        name="meta_prompt"
        id="metaprompt-textarea"
        hx-post="/{chapter.url}/images/{image_index}/actions/set_meta_prompt" 
        hx-swap="outerHTML transition:true"
        hx-trigger="change"
        id="meta_prompt-textarea" 
        cols=70 
        rows=6 
        value="{meta_prompt}"></wa-textarea>"""


def copy_from_previous_button(chapter, image_index=0):
    return f"""
        <wa-button
            hx-post="/{chapter.url}/images/{image_index}/actions/copy_from_previous"
            hx-swap="outerHTML transition:true"
            name="copy_from_previous"
            variant="secondary"
            size="m">
            Copy from Previous
        </wa-button>
    """


def scene_tab_panel(chapter, image_index, image_xml):
    """
    the old cruft at the bottom of the image page
    """
    # a little backward compatability
    if (
        image_xml.attrs.get("t5_prompt", "") == ""
        and image_xml.attrs.get("prompt", "") != ""
    ):
        image_xml.attrs["t5_prompt"] = image_xml.attrs["prompt"]

    imageurl = f"{chapter.url}/images/{image_xml.attrs['index']}"

    prompt_buttons = [
        # tools.generic_button(
        #     imageurl,
        #     category=None,
        #     tag="rebuild_prompt",
        #     cosmetic="Rebuild Prompt from Meta",
        #     include="#metaprompt-textarea",
        #     target="#prompt-textareas",
        # ),
        tools.generic_button(
            imageurl,
            category=None,
            tag="text_to_image",
            cosmetic="Text->Meta->Image",
            include="#prompt-textareas",
            target="#strip-centerpiece",
            tooltip="Create new meta, prompt and draw an image based on this portion of the story",
        ),
        tools.generic_button(
            imageurl,
            category=None,
            tag="create_clip_prompt",
            cosmetic="Create CLIP Prompt",
            include="#prompt-textareas",
            target="#prompt-textareas",
            tooltip="Create a new prompt based on this portion of the story",
        ),
        # tools.generic_button(
        #     imageurl,
        #     category=None,
        #     tag="create_t5_prompt",
        #     cosmetic="Create T5 Prompt",
        #     include="#prompt-textareas",
        #     target="#prompt-textareas",
        #     tooltip="Create a new prompt based on this portion of the story",
        # ),
        tools.generic_button(
            imageurl,
            category=None,
            tag="rightsize_t5_prompt",
            cosmetic="Rightsize T5 Prompt",
            include="#prompt-textareas",
            target="#prompt-textareas",
            tooltip="Condense/Expand the prompt first the available window.",
        ),
        tools.generic_button(
            imageurl,
            category=None,
            tag="copy_previous",
            cosmetic="Copy Previous",
            include="#prompt-textareas",
            target="#prompt-textareas",
            tooltip="Copy the previous clip/t5 prompt",
        ),
    ]

    style = image_xml.attrs.get("style", "")
    if style == "":
        style = chapter.config.get("default_style", "")

    apply_style_url = url_for(
        "library.book.chapter.images.apply_style",
        **chapter.kwargs,
        image_index=image_xml.attrs["index"],
    )

    add_style_widget = styles_htmx.add_style_widget(
        selected_style=style,
        url=apply_style_url,
        target="#prompt-textareas",
    )

    # setting_ui = setting(
    #     chapterurl,
    #     image_xml
    # )

    # tod_ui = tod(
    #     author,
    #     title,
    #     chapter,
    #     image_index=image_index
    # )

    # camera_ui = camera_direction(
    #     author,
    #     title,
    #     chapter,
    #     image_index=image_index
    # )

    meta_prompt_ui = meta_prompt(chapter, image_xml, image_index)

    # characters_section_ui = characters_section(
    #     mybook,
    #     author, title, chapter,
    #     image_index, chapterurl,
    #     chapterdir, image_xml
    # )

    buttons = [copy_from_previous_button(chapter, image_index=image_index)]

    # {setting_ui}
    # {tod_ui}
    # {camera_ui}
    # {characters_section_ui}
    # {prompt_template_ui}
    return f"""        
        <div style="width: 100%" class="wa-cluster">
            {"\n".join(prompt_buttons)}
        </div>
        
        {meta_prompt_ui}
        <div style="width:450px" class="wa-cluster"> 
            {"\n".join(buttons)}
        </div>
    """


def style_selector(chapterurl, image_index, value):
    # mybook = booklib.get_book(chapterdir)

    # image = mybook.soup.findAll("image")[image_index]
    # style = image.attrs.get("style", "")

    #
    # styles are from Mile High Styler, you can browse them
    # at https://enragedantelope.github.io/Styles-FluxDev/
    # https://civitai.com/user/Triple_Headed_Monkey
    #
    with open("styles.json", "r") as f:
        all_styles = json.loads(f.read())
        style_options = [
            f'<wa-option value="{styles.as_id(s['name'])}">{s['name']}</wa-option>'
            for s in all_styles
        ]

    return f"""
        <div>
        <wa-select
            value="{value}"
            hx-put="/{chapterurl}/images/{image_index}/actions/save_style"
            hx-target="#style-textarea"
            hx-swap="outerHTML transition:true"
            hx-trigger="change"
            name="style"
            id="style-textarea"
            label="Style">
            {"".join(style_options)}
        </wa-select>
        </div>"""


def uploaded_tab_panel(bookurl, chapterurl, chapterdir, image_index, image_xml):
    out = '<div class="wa-grid">'

    book_asset_dir = os.path.join(const.LIBRARY_DIR, chapterdir, "..", "..", "assets")
    os.makedirs(book_asset_dir, exist_ok=True)

    for fn in sorted(
        os.listdir(
            # book level assets.
            book_asset_dir
        )
    ):
        if not fn.lower().endswith((".png", ".jpg", ".jpeg", ".gif")):
            continue

        out += f"""
    <wa-card with-footer>
        <img src="/{bookurl}/images/assets/{os.path.basename(fn)}"/>
        {fn}
        <div slot="footer" class="wa-grid wa-gap-xs" style="--min-column-size: 10ch;">
            <wa-button 
                hx-post="/{chapterurl}/images/{image_index}/actions/use_uploaded_image"
                hx-vals='{{"filename": "{fn}"}}'
                hx-swap="outerHTML transition:true"
                hx-target="#strip-centerpiece"
                name="use_uploaded_image"
                size="m"
                variant="success"
                appearance="outlined">
                <wa-icon slot="start" src="/static/fontawesome7/svgs/solid/at.svg"></wa-icon>
                Import
            </wa-button>
        </div>
    </wa-card>
    """

    out += "</div>"
    return out


# def image_metadata(
#     chapter,
#     image_index,
# ):
#     # all_images = chapter.get_xml().findAll("image")

#     # image_xml = all_images[image_index]
#     # imageurl = f"{chapter.url}/images/{image_index}"

#     # scene_panel = scene_tab_panel(
#     #     chapter, image_index, image_xml
#     # )

#     # concept_panel = concept_tab_panel(
#     #     author, title, chapter, chapterurl, chapterdir, image_index, image_xml, imageurl
#     # )

#     # uploaded_panel = uploaded_tab_panel(
#     #     bookurl, chapterurl, chapterdir, image_index, image_xml
#     # )

#     # mybook.save_xml()

#     return ""

# <wa-tab-group id="metadata">
#     <wa-tab panel="scene">Scene</wa-tab>
#     <wa-tab panel="concept">Concept</wa-tab>
#     <wa-tab panel="geographic">Geography</wa-tab>
#     <wa-tab panel="uploaded">Uploaded</wa-tab>

#     <wa-tab-panel name="scene">
#         <div class="wa-stack">
#             {scene_panel}
#         </div>
#     </wa-tab-panel>

#     <wa-tab-panel name="concept">
#         {concept_panel}
#     </wa-tab-panel>

#     <wa-tab-panel name="geographic"> There are times when you want a
#     map.  This is intended to become a mapping service for the reality
#     of the book, as it has been presented in its own text.  The intent
#     is for perfect geographic consistency to be easily available.

#     Also upload a fictional map, or maps, and let AI fill in the gaps
#     for deep physical zoom into a whole world. Text + Image->Geography
#     AI has limited use cases but it might be really fun to build.

#     You feed it the text of the book, and it generates a whole
#     open-street-map style interactive globe.  It's created by a deep
#     blend of procedural techniques and AI governance.  Any maps included
#     with the book are root level included.</wa-tab-panel>

#     <wa-tab-panel name="uploaded">
#         {uploaded_panel}
#     </wa-tab-panel>

# </wa-tab-group>"""


def prompt_datastack(chapter, image_xml):
    return ""


def citation_panel(chapter, image_xml, force=False):
    image_index = image_xml.attrs.get("index", 0)

    imageurl = url_for(
        "library.book.chapter.images.show_image_by_index",
        author=chapter.author.name,
        title=chapter.title,
        chapter_number=chapter.number,
        language=chapter.language,
        height=300,
        image_index=image_index,
    )

    if force:
        # delete it, then the call to show_citation_by_index will regenerate it
        citation_image_fn = os.path.join(
            const.LIBRARY_DIR, chapter.get_image_citation_filename(image_index)
        )
        if os.path.exists(citation_image_fn):
            os.unlink(citation_image_fn)

    citation_imageurl = url_for(
        "library.book.chapter.images.show_citation_by_index",
        author=chapter.author.name,
        title=chapter.title,
        chapter_number=chapter.number,
        language=chapter.language,
        image_index=image_xml.attrs.get("index", 0),
    ) + "?cachebuster=%s" % int(time.time())

    return f"""
<div id="citation" hx-swap-oob="true" hx-swap="innerHTML" class="wa-cluster">
    <div class="wa-stack">
        <img src="{imageurl}"></img>
        <img src="{citation_imageurl}"></img>
    </div>
    <div class="wa-stack wa-gap-x1">
        <form method="PUT">
            <wa-input
                label="Artist"
                placeholder="Vincent van Gogh"
                name="artist"
                value="{html.escape(image_xml.attrs.get("artist", ""))}"
            ></wa-input>

            <wa-input 
                label="Title" 
                placeholder="The Starry Night"
                name="title"
                value="{html.escape(image_xml.attrs.get("title", ""))}"
            ></wa-input>    
            
            <wa-input 
                label="Year" 
                placeholder="1889"
                name="year"
                value="{html.escape(image_xml.attrs.get("year", ""))}"
            ></wa-input>

            <wa-input 
                label="Medium" 
                placeholder="Oil on canvas"
                name="medium"
                value="{html.escape(image_xml.attrs.get("medium", ""))}"
            ></wa-input>

            <div class="wa-grid" style="--min-column-size: 10ch">
                <wa-input 
                    label="Width" 
                    placeholder="73.7 cm"
                    name="source_width"
                    value="{html.escape(image_xml.attrs.get("source_width", ""))}"
                ></wa-input>

                <wa-input 
                    label="Height" 
                    placeholder="92.1 cm"
                    name="source_height"
                    value="{html.escape(image_xml.attrs.get("source_height", ""))}"
                ></wa-input>
            </div>            

            <wa-input 
                label="Location" 
                placeholder="Museum of Modern Art, New York City"
                name="location"
                value="{html.escape(image_xml.attrs.get("location", ""))}"
            ></wa-input>

            <wa-button 
                appearance="filled-outlined"
                hx-put=""
                hx-target="#citation"
                hx-vals='js:{{respond_with: "citation"}}'
                variant="brand">
            Generate Citation
            </wa-button>
        </form>
    </div>
</div>"""
