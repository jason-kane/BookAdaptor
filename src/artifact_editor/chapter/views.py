import glob
import json
import os
import re
import shutil
import roman

from flask import (
    Blueprint,
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
    config,
    tools,
)
from artifact_editor.author.author import Author
from artifact_editor.tools import (
    get_bookdir,
    get_chapterdir,
    get_chapterurl,
)

from . import htmx

from artifact_editor.chapter.chapter import (
    Chapter,
    chapter_url_defaults_helper,
    chapter_url_value_preprocessor,
)

from artifact_editor.styles import htmx as styles_htmx

log = logger.log(__name__)

bp = Blueprint(
    "chapter",
    __name__,
    template_folder=os.path.join(os.path.dirname(__file__), "templates"),
)

@bp.url_defaults
def my_url_defaults(endpoint, values):
    chapter_url_defaults_helper(endpoint, values)

@bp.url_value_preprocessor
def my_url_value_preprocessor(endpoint, values):
    chapter_url_value_preprocessor(endpoint, values) 


# PUT /Oscar%20Wilde/The%20Picture%20of%20Dorian%20Gray/1/set_chapter_cover
@bp.route("/set_chapter_cover", methods=["PUT"])
def set_chapter_cover():
    filename = request.form.get("filename")
    image_index = int(request.form.get("index"))

    image_xml = g.chapter.get_image(image_index)
    paragraph_xml = image_xml.find_parent("paragraph")

    source = os.path.join(
        tools.const.LIBRARY_DIR,
        g.chapter.get_paragraph_dir(paragraph_xml['index']),
        filename,
    )

    destination = os.path.join(
        const.LIBRARY_DIR,
        g.chapter.chapterdir,
        "cover.png",
    )

    # Set the chapter cover image
    shutil.copyfile(source, destination)

    return redirect(
        url_for(
            "library.book.chapter.chapter_base",
            author=g.author.name,
            title=g.title,
            chapter_number=g.chapter.number,
            language=g.language,
        )
    )


@bp.route("/set_mood", methods=["PUT"])
def set_mood():
    new_mood = request.form.get("mood")
    
    g.chapter.mood = new_mood
    log.info(f"Setting g.chapter.config['mood'] to {new_mood}")
    g.chapter.config["mood"] = new_mood
    g.chapter.save_config()

    return "", 200


@bp.route("/set_theme", methods=["PUT"])
def set_theme():
    new_theme = request.form.get("theme")
    g.chapter.theme = new_theme
    g.chapter.config["theme"] = new_theme
    g.chapter.save_config()

    return "", 200


@bp.route("")
@bp.route("/")
def chapter_base():
    chapter_xml = g.chapter.get_xml()
    image_list = []
    for image_index, image in enumerate(chapter_xml.find_all("image")):
        log.info("Adding image..")
        if "src" in image.attrs:
            image_list.append(
                {
                    "url": url_for(
                        "library.book.chapter.images.show_image_by_index",
                        author=g.author.name,
                        title=g.title,
                        chapter_number=g.chapter.number,
                        language="english",
                        height=0,
                        image_index=image_index,
                    ),
                    "alt": image.get("alt", ""),
                    "index": image_index,
                    "filename": os.path.basename(image.attrs["src"]),
                }
            )
        else:
            log.info("No src, no image")
            log.info(image)

    chapter_style_selector = styles_htmx.get_chapter_style_selector(g.chapter)

    return render_template(
        "chapter.html",
        chapter_style_selector=chapter_style_selector,
        chapter_mood=htmx.chapter_mood_widget(g.chapter),
        chapter_theme=htmx.chapter_theme_widget(g.chapter),
        language="english",
        pretty_language="English",
        chapterurl=g.chapter.url,
        author=g.author,
        pretty_author=g.chapter.config.get("author", g.author),
        title=g.title,
        pretty_title=g.chapter.config.get("pretty_title", g.title),
        chapter=g.chapter,
        chapter_image_url=url_for(
            'library.chapter_cover',
            author=g.author.name,
            title=g.title,
            chapter_number=g.chapter.number,
        ),
        image_list=image_list,
    )

# PUT /library/Aesop/Fables/31/english/set_chapter_style
@bp.route("/set_chapter_style", methods=["PUT"])
def set_chapter_style():
    new_style = request.form.get("chapter_style")
    g.chapter.set_chapter_style(new_style)

    return styles_htmx.get_chapter_style_selector(g.chapter)