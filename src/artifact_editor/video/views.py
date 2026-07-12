import os

from artifact_editor.author.author import Author
from artifact_editor.chapter import Chapter
from flask import (
    Blueprint,
    render_template,
    send_file,
)

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
    video,
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

    audio.recalculate_paragraph_durations(chapterdir)
    
    return htmx.calculate_paragraph_durations_button(chapterurl)


@bp.route("/actions/render_masterplan_widescreen", methods=["POST"])
def render_masterplan_widescreen_handler(author, title, chapter_number, language):
    author = Author(author)
    chapter = Chapter(author=author, title=title, number=chapter_number)

    video.render_masterplan_widescreen(chapter)

    return htmx.render_masterplan_widescreen_button(chapter)


@bp.route("/actions/render_masterplan_portrait", methods=["POST"])
def render_masterplan_portrait_handler(author, title, chapter_number, language):
    author = Author(author)
    chapter = Chapter(author=author, title=title, number=chapter_number)

    video.render_masterplan_portrait(chapter)

    return htmx.render_masterplan_portrait_button(chapter)


@bp.route("/<filename>.mp4")
def deliver_video(author, title, chapter_number, language, filename=""):
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)

    return send_file(
        chapter.get_video_filename(),
        mimetype="video/mp4",
        as_attachment=False,
        etag=True
    )


@bp.route("/")
def base_video(author, title, chapter_number, language):
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)
    
    filename = chapter.get_video_filename()    
    if os.path.exists(filename):
        log.info("Video file exists: %s", filename)
        filename = os.path.basename(filename)
    else:
        filename = None

    pretty_author = chapter.config.get("author", author)
    pretty_title = chapter.config.get("title", title)

    if chapter.get_aspect() == 'portrait':
        render_masterplan_button=htmx.render_masterplan_portrait_button(chapter)
    else:
        render_masterplan_button=htmx.render_masterplan_widescreen_button(chapter)

    return render_template(
        "video.html",
        language=language,
        pretty_language=language.capitalize(),
        author=author,
        pretty_author=pretty_author,
        title=title,
        pretty_title=pretty_title,
        chapter=chapter,
        filename=filename,
        replace_masterplan_button=masterplan_htmx.regenerate_masterplan_button(chapter),
        render_masterplan_button=render_masterplan_button,
        calculate_paragraph_durations_button=htmx.calculate_paragraph_durations_button(chapter),
        clear_adjusted_images_button=htmx.clear_adjusted_images_button(chapter),
        clear_all_frames_button=htmx.clear_all_frames_button(chapter),
        clear_all_transitions_button=htmx.clear_all_transitions_button(chapter),
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
