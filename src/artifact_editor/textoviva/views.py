from collections import defaultdict
import math
import shutil
import unicodedata
import redis
import glob
import os
import random
import json
from bs4 import BeautifulSoup, Tag
import shutil
import artifact_editor.masterplan.masterplan as masterplan

from torch import div

import comfy
import flask

from text_to_image.registry import registry as t2i_registry

import artifact_editor.styles.styles as styles
import artifact_editor.images.images as images
import artifact_editor.chapter.chapter as chapter

from flask import (
    Blueprint,
    render_template,
    g,
    request,
    send_from_directory,
    url_for,
)

import artifact_editor.styles.htmx as htmx
import artifact_editor.images.htmx as images_htmx
import const
import logger
from artifact_editor import (
    config,
    llm,
    tools,
)

from artifact_editor.chapter.chapter import (
    Chapter,
    chapter_url_defaults_helper,
    chapter_url_value_preprocessor,
)

from artifact_editor.author.author import Author
from artifact_editor.cache import cache

log = logger.log(__name__)

bp = Blueprint(
    "textoviva",
    __name__,
    template_folder=os.path.join(os.path.dirname(__file__), "templates"),
)

@bp.url_defaults
def my_url_defaults(endpoint, values):
    chapter_url_defaults_helper(endpoint, values)


@bp.url_value_preprocessor
def my_url_value_preprocessor(endpoint, values):
    chapter_url_value_preprocessor(endpoint, values)


@bp.route("/pause")
def pause():
    pauseTime = request.args.get("pauseTime", None)
    if pauseTime is None:
        return "Missing pauseTime parameter", 400

    # convert to float
    try:
        pauseTime = float(pauseTime)
    except ValueError:
        return "Invalid pauseTime parameter", 400

    mplan = g.chapter.get_masterplan(force=True)

    # get the nearest frame number for this time
    frame_number = g.chapter.time_to_frame(pauseTime)

    # get the image for this frame number
    mp_image = g.chapter.frame_to_image(mplan, frame_number)

    # a masterplan image object gives us lots good stuff.
    # in this case:
    image_xml = g.chapter.get_image(index=mp_image['index'])

    # return the image path as JSON
    if image_xml is None:
        # index and masterplan are out of sync.  This is critical.
        log.critical(f"Masterplan image index {mp_image['index']} was not found in chapter_xml for {g.chapter.title}")
        raise IndexError(f"Masterplan image index {mp_image['index']} was not found in chapter_xml for {g.chapter.title}")
        return "No image found for index %s" % mp_image['index'], 404
    
    out = small_image_workshop(image_xml)
    return out, 200


def player_stack(chapter: Chapter, image_xml: Tag) -> str:
    video_missing = not os.path.exists(chapter.get_video_filename())
    notice = (
        '<p id="video-generation-notice">No rendered video yet. Use Re-render to create it.</p>'
        if video_missing
        else ""
    )

    return f"""<div id="player-stack" class="wa-stack">
    {notice}
    <wa-video
        id="chapter-video"
        title="Chapter {chapter.title} Video"
        controls="standard"
        loop="false"
        poster="{ url_for('library.book.chapter.images.show_image_by_index', height=256, image_index=0)}"
        src="{ url_for('library.book.chapter.video.deliver_video', filename='chapter') }"
    >
    </wa-video>
</div>"""


def small_image_workshop(image_xml: Tag | None) -> str:
    if image_xml is None:
        return '<div id="image_base_img" hx-swap-oob="true"></div>'

    return f"""<img 
    id="image_base_img"
    hx-swap-oob="true"
    hx-get="{url_for('library.book.chapter.textoviva.expand_image_workshop', image_index=image_xml.attrs['index'])}"
    style="height: 100%; width: 100%"
    src="{ url_for('library.book.chapter.images.show_image_by_index', height=256, image_index=image_xml.attrs['index'])}"
    alt="Chapter Cover Image" 
/>"""


@bp.route("/shrink_image_workshop")
def shrink_image_workshop():
    image_index = int(request.args.get("image_index", 0))
    image_xml = g.chapter.get_image(index=image_index)

    # restore main
    out = f"""<main
    id="main"
    hx-swap-oob="true"
>
    <div class="wa-stack">
        {player_stack(g.chapter, image_xml)}
    </div>
</main>"""

    # restore aside
    out += f"""<aside
    id="aside"
    hx-swap-oob="true"
>
    { small_image_workshop(image_xml) }
</aside>"""
    return out


def large_image_workshop(image_xml: Tag, chapter: Chapter) -> str:
    # take over main

            # <img
            #     src="{url_for('library.book.chapter.images.show_image_by_index', height=512, image_index=image_xml.attrs['index'])}"
            #     alt="Chapter image {image_xml.attrs['index']}"
            #     style="max-height: 50vh; max-width: 100%; object-fit: contain"
            # />
    
    return f"""
    <main
        id="main"
        hx-swap-oob="true"
    >
        <div class="wa-stack">
            {images_htmx.image_workshop(image_xml=image_xml, chapter=chapter)}
        </div>
    </main>
    
    <aside
        id="aside"
        hx-swap-oob="true"
    >
        <img 
        id="image_base_img"
        hx-swap-oob="true"
        hx-get="{url_for('library.book.chapter.textoviva.shrink_image_workshop', image_index=image_xml.attrs['index'])}"
        style="height: 100%; width: 100%"
        src="{ url_for('library.book.chapter.images.show_image_by_index', height=256, image_index=image_xml.attrs['index'])}"
        alt="Chapter Cover Image" 
        />
    </aside>"""


@bp.route("/expand_image_workshop")
def expand_image_workshop():
    image_index = int(request.args.get("image_index", 0))
    image_xml = g.chapter.get_image(index=image_index)

    # a masterplan image object gives us lots good stuff.
    # in this case:

    # switch to the large image workshop
    out = large_image_workshop(image_xml, g.chapter)
    return out, 200


@bp.route("/rerender", methods=["POST"])
def rerender():
    """Regenerate image animations and rebuild the chapter video frames."""
    chapter = g.chapter
    frames_dir = os.path.join(
        const.LIBRARY_DIR,
        chapter.chapterdir.lstrip("/"),
        "frames",
    )

    # Remove rendered frame images so the video renderer redraws them from the
    # updated image and animation assets.
    for aspect in ("portrait", "widescreen"):
        aspect_dir = os.path.join(frames_dir, aspect)
        if not os.path.isdir(aspect_dir):
            continue
        for filename in os.listdir(aspect_dir):
            if filename.startswith("frame_") and filename.endswith(".png"):
                os.remove(os.path.join(aspect_dir, filename))

    # The renderer skips an existing output video, so clear its output before rebuilding.
    videofile = chapter.get_video_filename()
    if os.path.isfile(videofile):
        os.remove(videofile)

    chapter.load_xml(force=True)
    log.info(
        "Starting Textoviva re-render from button",
        video_filename=chapter.get_video_filename(),
    )
    chapter.textoviva.create_video(force=True)
    return '<span>Re-render complete.</span>', 200


@bp.route("/")
def base():
    # Keep page loads responsive. Video generation is started explicitly by
    # the Re-render action so the controls remain available while no video exists.
    g.chapter.load_xml(force=True)
    image_xml = next(iter(g.chapter.images()), None)

    return render_template(
        "textoviva.html",
        title="Textoviva",
        author=g.author,
        chapter=g.chapter,
        section="textoviva",
        player_stack=player_stack,
        small_image_workshop=small_image_workshop(image_xml),
        rerender_url=url_for(
            "library.book.chapter.textoviva.rerender",
            **g.chapter.kwargs,
        ),
    )


