import os
import json
import uuid

from artifact_editor.author.author import Author
from artifact_editor.chapter.chapter import (
    Chapter,
    chapter_url_defaults_helper,
    chapter_url_value_preprocessor,
)
from flask import (
    Blueprint,
    render_template,
    send_file,
    g,
)
import redis
from redis.maint_notifications import MaintNotificationsConfig

import const
import logger
from artifact_editor import (
    config,
)
from artifact_editor.masterplan import htmx as masterplan_htmx
from artifact_editor.tools import (
    get_chapterdir,
    get_chapterurl,
)

from . import (
    htmx,
)

log = logger.log(__name__)

bp = Blueprint(
    'video', 
    __name__,
    template_folder=os.path.join(
        os.path.dirname(__file__),
        "templates"
    )
)

@bp.url_defaults
def my_url_defaults(endpoint, values):
    chapter_url_defaults_helper(endpoint, values)

@bp.url_value_preprocessor
def my_url_value_preprocessor(endpoint, values):
    chapter_url_value_preprocessor(endpoint, values) 


@bp.route("/actions/clear_adjusted_images", methods=["POST"])
def clear_adjusted_images(author, title, chapter):
    chapterurl = get_chapterurl(author, title, chapter)
    
    # /chapter/0001/paragraphs/000000/img_2_image001.jpg.adj.png 
    chapterdir = get_chapterdir(author, title, chapter)
    for paragraph in os.listdir(
        os.path.join(
            neobreaker.const.LIBRARY_DIR,
            chapterdir,
            "paragraphs"
        )
    ):
        for img in os.listdir(
            os.path.join(
                neobreaker.const.LIBRARY_DIR,
                chapterdir,
                "paragraphs",
                paragraph
            )
        ):
            if img.endswith(".adj.png"):
                log.info(f'Removing adjusted image {img}')
                os.remove(os.path.join(
                    neobreaker.const.LIBRARY_DIR,
                    chapterdir,
                    "paragraphs",
                    paragraph,
                    img
                ))

    return htmx.clear_adjusted_images_button(chapterurl)


@bp.route("/actions/calculate_paragraph_durations", methods=["POST"])
def calculate_paragraph_durations(author, title, chapter):
    chapterdir = get_chapterdir(author, title, chapter)
    chapterurl = get_chapterurl(author, title, chapter)

    phrases.recalculate_paragraph_durations(chapterdir)
    
    return htmx.calculate_paragraph_durations_button(chapterurl)


@bp.route("/actions/render_masterplan_widescreen", methods=["POST"])
def render_masterplan_widescreen_handler(author, title, chapter_number, language):
    author = Author(author)
    chapter = Chapter(author=author, title=title, number=chapter_number, language=language)
    return enqueue_video_render(chapter, "widescreen")


@bp.route("/actions/render_masterplan_portrait", methods=["POST"])
def render_masterplan_portrait_handler(author, title, chapter_number, language):
    author = Author(author)
    chapter = Chapter(author=author, title=title, number=chapter_number, language=language)
    return enqueue_video_render(chapter, "portrait")


def enqueue_video_render(chapter, aspect):
    job_id = uuid.uuid4().hex
    task = {
        "job_id": job_id,
        "author": chapter.author.name,
        "title": chapter.title,
        "chapter_number": chapter.number,
        "language": chapter.language,
        "aspect": aspect,
    }
    client = redis.Redis(
        host=os.environ.get("REDIS_HOST", "redis"),
        port=6379,
        protocol=3,
        decode_responses=True,
        maint_notifications_config=MaintNotificationsConfig(enabled=False),
    )
    client.hset(f"video_job:{job_id}", mapping={
        "job_id": job_id, "percent": 0, "message": "Queued", "state": "queued"
    })
    client.expire(f"video_job:{job_id}", 86400)
    client.lpush("video_render_jobs", json.dumps(task))
    return (
        f'<div id="video-progress-{job_id}" class="wa-stack">'
        '<wa-progress-bar value="0"></wa-progress-bar>'
        '<div>Queued for video rendering</div></div>',
        202,
    )


@bp.route("/<filename>.mp4")
def deliver_video(filename=""):
    return send_file(
        g.chapter.get_video_filename(),
        mimetype="video/mp4",
        as_attachment=False,
        etag=True
    )


@bp.route("/")
def base_video():    
    filename = g.chapter.get_video_filename()    
    if os.path.exists(filename):
        log.info("Video file exists: %s", filename)
        filename = os.path.basename(filename)
    else:
        filename = None

    pretty_author = g.chapter.config.get("author", g.author)
    pretty_title = g.chapter.config.get("title", g.title)

    if g.chapter.get_aspect() == 'portrait':
        render_masterplan_button=htmx.render_masterplan_portrait_button(g.chapter)
    else:
        render_masterplan_button=htmx.render_masterplan_widescreen_button(g.chapter)

    return render_template(
        "video.html",
        language=g.language,
        pretty_language=g.language.capitalize(),
        author=g.author,
        pretty_author=pretty_author,
        title=g.title,
        pretty_title=pretty_title,
        chapter=g.chapter,
        filename=filename,
        replace_masterplan_button=masterplan_htmx.regenerate_masterplan_button(g.chapter),
        render_masterplan_button=render_masterplan_button,
        calculate_paragraph_durations_button=htmx.calculate_paragraph_durations_button(g.chapter),
        clear_adjusted_images_button=htmx.clear_adjusted_images_button(g.chapter),
        clear_all_frames_button=htmx.clear_all_frames_button(g.chapter),
        clear_all_transitions_button=htmx.clear_all_transitions_button(g.chapter),
        section="video",
        section_cosmetic="Video"
    )


@bp.route("/actions/clear_all_frames", methods=["POST"])
def clear_all_frames(author, title, chapter_number, language):
    """
    Go through the book.xml, sum the durations of phrases and assign that as the duration for the image.
    """
    author = Author(author)
    chapter = Chapter(author=author, title=title, number=chapter_number, language=language)

    framedir = os.path.join(
        const.LIBRARY_DIR,
        chapter.chapterdir.lstrip('/'),
        "frames"
    )
    log.info('Clearing all frames in %s', framedir)

    # Remove all frame images
    count = 0
    os.makedirs(framedir, exist_ok=True)

    for aspect in ["portrait", "widescreen"]:
        aspect_dir = os.path.join(framedir, aspect)
        os.makedirs(aspect_dir, exist_ok=True)
        for filename in os.listdir(aspect_dir):
            if filename.startswith("frame_") and filename.endswith(".png"):
                file_path = os.path.join(aspect_dir, filename)
                os.remove(file_path)
                count += 1

    log.info(f'Removing {count} frames')
    
    return htmx.clear_all_frames_button(chapter)


@bp.route("/actions/clear_all_transitions", methods=["POST"])
def clear_all_transitions(author, title, chapter):
    """
    clear frames for all transition animations forcing regeneration
    """
    chapterdir = get_chapterdir(author, title, chapter)
    chapterurl = get_chapterurl(author, title, chapter)

    framedir = os.path.join(
        const.LIBRARY_DIR,
        chapterdir.lstrip('/'),
        "transitions"
    )
    log.info('Clearing all transitions in %s', framedir)

    # Remove all frame images
    count = 0
    for filename in os.listdir(framedir):
        if filename.startswith("transition_") and filename.endswith(".mp4"):
            file_path = os.path.join(framedir, filename)
            os.remove(file_path)
            count += 1

        if os.path.isdir(os.path.join(framedir, filename)):
            dir_path = os.path.join(framedir, filename)
            for framefile in os.listdir(dir_path):
                if framefile.startswith("frame_") and framefile.endswith(".png"):
                    file_path = os.path.join(dir_path, framefile)
                    os.remove(file_path)
                    count += 1
                elif framefile == "done.flag":
                    os.remove(os.path.join(dir_path, framefile))
    
            try:
                os.rmdir(dir_path)
            except OSError as e:
                log.warning(f"Could not remove directory {dir_path}: {e}")

    log.info(f'Removing {count} files')
    
    return htmx.clear_all_transitions_button(chapterurl)
