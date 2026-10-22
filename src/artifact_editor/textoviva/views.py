from artifact_editor.websocket_url import websocket_url
from html import escape
from collections import defaultdict
import math
import shutil
import unicodedata
import redis
import glob
import os
import random
import json
import uuid
from .workflow import animation_workflow_name
from bs4 import BeautifulSoup, Tag
import shutil
import artifact_editor.masterplan.masterplan as masterplan


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
    if not math.isfinite(pauseTime) or pauseTime < 0:
        return "Invalid pauseTime parameter", 400

    mplan = g.chapter.get_masterplan(force=True)

    # get the nearest frame number for this time
    frame_number = g.chapter.time_to_frame(pauseTime)

    # get the image for this frame number
    mp_image = g.chapter.frame_to_image(mplan, frame_number)

    if mp_image is None:
        return animation_panel(None, oob=True), 200

    # a masterplan image object gives us lots good stuff.
    # in this case:
    image_xml = g.chapter.get_image(index=mp_image['index'])

    # return the image path as JSON
    if image_xml is None:
        # index and masterplan are out of sync.  This is critical.
        log.critical(f"Masterplan image index {mp_image['index']} was not found in chapter_xml for {g.chapter.title}")
        raise IndexError(f"Masterplan image index {mp_image['index']} was not found in chapter_xml for {g.chapter.title}")
        return "No image found for index %s" % mp_image['index'], 404
    
    out = small_image_workshop(image_xml) + animation_panel(image_xml, oob=True)
    return out, 200


def animation_panel(image_xml, oob=False):
    swap = ' hx-swap-oob="true"' if oob else ''
    if image_xml is None:
        return f'<section id="video-base"{swap}>Pause on an image to edit its animation.</section>'
    index = int(image_xml.attrs['index'])
    duration = g.chapter.get_image_frames(image_xml) / const.FPS
    phrase_indices = []
    for point in image_xml.next_elements:
        if not isinstance(point, Tag):
            continue
        if point.name == 'image':
            break
        if point.name == 'phrase':
            phrase_indices.append(str(point.attrs['index']))
    phrase_indices_attr = escape(json.dumps(phrase_indices), quote=True)
    prompt = escape(image_xml.attrs.get('animation_prompt', ''), quote=True)
    action = url_for('library.book.chapter.textoviva.regenerate_animation', image_index=index)
    save_action = url_for('library.book.chapter.textoviva.save_animation_description', image_index=index)
    workflow_action = url_for('library.book.chapter.textoviva.open_animation_workflow', image_index=index)
    return f'''<section id="video-base" class="wa-stack" data-phrase-indices="{phrase_indices_attr}"{swap}>
        <strong>Animation · Image {index}</strong>
        <div>Visualization duration: {duration:.2f} seconds</div>
        <wa-textarea id="animation-description" label="Animation description"
            name="animation_prompt" rows="5" value="{prompt}"
            hx-put="{save_action}" hx-trigger="input changed delay:500ms, change"
            hx-vals='js:{{animation_prompt: this.value}}'
            hx-target="#animation-save-status" hx-swap="innerHTML"
            hx-sync="this:replace"></wa-textarea>
        <div id="animation-save-status" role="status" aria-live="polite"></div>
        <div class="wa-cluster">
        <wa-button hx-post="{action}" hx-include="#animation-description"
            hx-vals='js:{{animation_prompt: document.getElementById("animation-description").value}}'
            hx-target="#animation-status" hx-swap="innerHTML"
            hx-disabled-elt="this" variant="brand">Re-generate animation</wa-button>
        <wa-button href="{workflow_action}" target="_blank"
            onclick="this.href = '{workflow_action}?' + new URLSearchParams({{animation_prompt: document.getElementById('animation-description').value}})"
            variant="brand" appearance="accent" pill label="Open animation workflow in ComfyUI">
            <wa-icon src="/static/images/comfyui.svg"></wa-icon>
        </wa-button>
        </div>
        <div id="animation-status" role="status" aria-live="polite"></div>
    </section>'''


@bp.route('/animation/<int:image_index>/workflow')
def open_animation_workflow(image_index):
    g.chapter.load_xml(force=True)
    image_xml = g.chapter.get_image(index=image_index)
    if image_xml is None:
        return 'Image not found', 404
    prompt = request.args.get('animation_prompt', image_xml.attrs.get('animation_prompt', ''))
    workflow_template = animation_workflow_name(
        request.args.get('workflow_i2v_template') or image_xml.attrs.get('workflow_i2v_template') or const.DEFAULT_I2V_TEMPLATE
    )
    workflow_fn = g.chapter.get_workflow(
        image_xml,
        workflow_name=workflow_template,
        video_index=0,
        template_environment={'ANIMATION_PROMPT': prompt},
        use_image_source=True,
    )
    if not workflow_fn:
        return 'No workflow available for this image.', 400
    return flask.redirect(const.COMFYUI_UI_URL + f'?workflow={workflow_fn}', code=302)


@bp.route('/animation/<int:image_index>/description', methods=['PUT'])
def save_animation_description(image_index):
    g.chapter.load_xml(force=True)
    image_xml = g.chapter.get_image(index=image_index)
    if image_xml is None:
        return 'Image not found', 404
    if 'animation_prompt' not in request.form:
        return 'Missing animation description', 400
    image_xml.attrs['animation_prompt'] = request.form['animation_prompt']
    g.chapter.save_xml()
    return 'Description saved.'


@bp.route('/animation/<int:image_index>/regenerate', methods=['POST'])
def regenerate_animation(image_index):
    g.chapter.load_xml(force=True)
    image_xml = g.chapter.get_image(index=image_index)
    if image_xml is None:
        return 'Image not found', 404
    prompt = request.form.get('animation_prompt', '')
    if not prompt.strip():
        return 'Enter an animation description.'
    image_xml.attrs['animation_prompt'] = prompt
    if request.form.get('workflow_i2v_template'):
        image_xml.attrs['workflow_i2v_template'] = request.form['workflow_i2v_template']
    g.chapter.save_xml()

    job_id = uuid.uuid4().hex
    task = {
        'action': 'textoviva_regenerate_animation', 'job_id': job_id,
        'author': g.chapter.author.name, 'title': g.chapter.title,
        'chapter_number': g.chapter.number, 'language': g.chapter.language,
        'image_index': image_index, 'animation_prompt': prompt,
    }
    client = redis.Redis(host=os.environ.get("REDIS_HOST", "redis"), decode_responses=True)
    client.lpush("video_render_jobs", json.dumps(task))
    return f'<div id="video-progress-{job_id}">Animation queued. The chapter video will be rebuilt.</div>'


def player_stack(chapter: Chapter, image_xml: Tag, embedded=False) -> str:
    job_id = uuid.uuid4().hex
    task = escape(json.dumps({
        "action": "textoviva_rerender", "job_id": job_id,
        "author": chapter.author.name, "title": chapter.title,
        "chapter_number": chapter.number, "language": chapter.language,
    }), quote=True)
    notice = f"""<div id="video-generation-notice" class="wa-stack"
        hx-ext="ws" ws-connect="{websocket_url()}">
        <wa-button ws-send hx-trigger="click" hx-vals='{task}'
            variant="brand" appearance="accent">Re-render</wa-button>
        <div id="video-progress-{job_id}" role="status" aria-live="polite"></div>
    </div>"""

    pause_url = "" if embedded else url_for('library.book.chapter.textoviva.pause', **chapter.kwargs)
    return f"""<div id="player-stack" class="wa-stack">
    <video
        id="chapter-video"
        class="video-js vjs-big-play-centered textoviva-video"
        aria-label="Chapter {escape(chapter.title, quote=True)} Video"
        controls playsinline preload="metadata"
        data-pause-url="{escape(pause_url, quote=True)}"
        data-timeline-url="{url_for('library.book.chapter.textoviva.playback_timeline', **chapter.kwargs)}"
        poster="{url_for('library.book.chapter.frames.frame_image', aspect=chapter.get_aspect(), frame_index=1, **chapter.kwargs)}"
        src="{ url_for('library.book.chapter.video.deliver_video', filename='chapter') }"
    >
        <p class="vjs-no-js">Enable JavaScript to use the video player.</p>
    </video>
    {notice}
</div>"""


@bp.get('/playback-timeline')
def playback_timeline():
    plan = g.chapter.get_masterplan()
    def entries(items):
        return [{"index": str(item.index), "start": item.start_frame, "end": item.end_frame}
                for item in items]
    return {"fps": const.FPS, "phrases": entries(plan.words), "images": entries(plan.images)}


@bp.get('/player')
def player():
    return player_stack(g.chapter, None, embedded=True)


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
    { animation_panel(image_xml) }
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
    {animation_panel(image_xml)}
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
        animation_panel=animation_panel(image_xml),
        rerender_task={
            "action": "textoviva_rerender",
            "job_id": uuid.uuid4().hex,
            "author": g.chapter.author.name,
            "title": g.chapter.title,
            "chapter_number": g.chapter.number,
            "language": g.chapter.language,
        },
    )


