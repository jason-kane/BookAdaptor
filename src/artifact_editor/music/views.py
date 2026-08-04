import glob
import multiprocessing
import os
import time
import webbrowser

import PIL
from flask import (
    Blueprint,
    make_response,
    redirect,
    render_template,
    request,
    send_from_directory,
    url_for,
    g,
)

import const
import logger
from artifact_editor import (
    camera,
    config,
)
from artifact_editor.audio.audio import recalculate_image_frames
from artifact_editor.author.author import Author
from artifact_editor.chapter.chapter import (
    Chapter,
    chapter_url_defaults_helper,
    chapter_url_value_preprocessor,
)
from artifact_editor.images import images
from artifact_editor.masterplan import masterplan
from artifact_editor.tools import (
    get_bookdir,
    get_bookurl,
    get_chapterdir,
    get_chapterurl,
)
from artifact_editor.video import htmx as video_htmx

from . import (
    music,
    htmx,
)

log = logger.log(__name__)

bp = Blueprint(
    "music",
    __name__,
    template_folder=os.path.join(
        os.path.dirname(__file__),
        "templates",
    ),
)

@bp.url_defaults
def my_url_defaults(endpoint, values):
    chapter_url_defaults_helper(endpoint, values)

@bp.url_value_preprocessor
def my_url_value_preprocessor(endpoint, values):
    chapter_url_value_preprocessor(endpoint, values) 


@bp.route("/")
def base():
    pretty_author = g.chapter.config.get("author", g.author)
    pretty_title = g.chapter.config.get("title", g.title)

    buttons = "\n".join(
        []
    )

    return render_template(
        "music.html",
        language=g.language,
        pretty_language=g.language.capitalize(),
        author=g.author,
        pretty_author=pretty_author,
        title=g.title,
        pretty_title=pretty_title,
        chapter=g.chapter,
        chapterurl=g.chapter.url,
        buttons=buttons,
        section="music",
        section_cosmetic="Music",
    )

