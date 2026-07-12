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
from .chapter import Chapter

from artifact_editor.styles import htmx as styles_htmx

log = logger.log(__name__)

bp = Blueprint(
    "chapter",
    __name__,
    template_folder=os.path.join(os.path.dirname(__file__), "templates"),
)


# PUT /Oscar%20Wilde/The%20Picture%20of%20Dorian%20Gray/1/set_chapter_cover
@bp.route("/set_chapter_cover", methods=["PUT"])
def set_chapter_cover(author, title, chapter_number, language):
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)

    filename = request.form.get("filename")
    image_index = int(request.form.get("index"))

    image_xml = chapter.get_image(image_index)
    paragraph_xml = image_xml.find_parent("paragraph")

    source = os.path.join(
        tools.const.LIBRARY_DIR,
        chapter.get_paragraph_dir(paragraph_xml['index']),
        filename,
    )

    destination = os.path.join(
        const.LIBRARY_DIR,
        chapter.chapterdir,
        "cover.png",
    )

    # Set the chapter cover image
    shutil.copyfile(source, destination)

    return redirect(
        url_for(
            "library.book.chapter.chapter_base",
            author=author.name,
            title=title,
            chapter_number=chapter.number,
            language=language,
        )
    )


@bp.route("/set_mood", methods=["PUT"])
def set_mood(author, title, chapter_number, language):
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)

    new_mood = request.form.get("mood")
    
    chapter.mood = new_mood
    log.info(f"Setting chapter.config['mood'] to {new_mood}")
    chapter.config["mood"] = new_mood
    chapter.save_config()

    return "", 200


@bp.route("/set_theme", methods=["PUT"])
def set_theme(author, title, chapter_number, language):
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)

    new_theme = request.form.get("theme")
    chapter.theme = new_theme
    chapter.config["theme"] = new_theme
    chapter.save_config()

    return "", 200


@bp.route("")
@bp.route("/")
def chapter_base(author, title, chapter_number, language):
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)

    chapter_xml = chapter.get_xml()
    image_list = []
    for image_index, image in enumerate(chapter_xml.find_all("image")):
        log.info("Adding image..")
        if "src" in image.attrs:
            image_list.append(
                {
                    "url": url_for(
                        "library.book.chapter.images.show_image_by_index",
                        author=author.name,
                        title=title,
                        chapter_number=chapter.number,
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

    chapter_style_selector = styles_htmx.get_chapter_style_selector(chapter)

    return render_template(
        "chapter.html",
        chapter_style_selector=chapter_style_selector,
        chapter_mood=htmx.chapter_mood_widget(chapter),
        chapter_theme=htmx.chapter_theme_widget(chapter),
        language="english",
        pretty_language="English",
        chapterurl=chapter.url,
        author=author,
        pretty_author=chapter.config.get("author", author),
        title=title,
        pretty_title=chapter.config.get("pretty_title", title),
        chapter=chapter,
        chapter_image_url=url_for(
            'library.chapter_cover',
            author=author.name,
            title=title,
            chapter_number=chapter.number,
        ),
        image_list=image_list,
    )

# PUT /library/Aesop/Fables/31/english/set_chapter_style
@bp.route("/set_chapter_style", methods=["PUT"])
def set_chapter_style(author, title, chapter_number, language):
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)

    new_style = request.form.get("chapter_style")
    chapter.set_chapter_style(new_style)

    return styles_htmx.get_chapter_style_selector(chapter)