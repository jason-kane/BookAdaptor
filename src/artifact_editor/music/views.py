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
)

import const
import logger
from artifact_editor import (
    camera,
    config,
)
from artifact_editor.audio.audio import recalculate_image_frames
from artifact_editor.author.author import Author
from artifact_editor.chapter.chapter import Chapter
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


@bp.route(
    "/actions/image_durations", methods=["POST"]
)
def image_durations(author, title, chapter_number, language):
    """
    Go through the book.xml, sum the durations of phrases and assign that as the duration for the image.
    We must already have phrase durations.
    """
    chapterdir = get_chapterdir(author, title, chapter_number)
    bookurl = get_bookurl(author, title)

    recalculate_image_frames(chapterdir)
    return htmx.image_durations_button(bookurl)


@bp.route(
    "/actions/set_fragment_id",
    methods=["POST"],
)
def set_fragment_id(author, title, chapter_number, language):
    """
    Go through the book.xml, sum the durations of phrases and assign that as the duration for the image.
    """
    chapter = Chapter(author, title, chapter_number, language)

    paragraph_index = 0
    for paragraph in chapter.get_xml().find("book").children:
        if not str(paragraph).strip():
            continue

        fragdex = 1
        log.debug("Processing paragraph %s", paragraph_index)
        if paragraph.name in ["viewer", "stanza", "paragraph", "chapter"]:
            for fragment in paragraph.contents:
                if fragment.name in ["verse", "phrase"]:
                    fragdex = fragment.attrs.get("fragdex", fragdex)
                    fragment.attrs["id"] = f"{paragraph_index}_{fragdex}"

                    fragdex = int(fragdex) + 1
        paragraph_index += 1

    chapter.save_xml()

    return htmx.set_fragment_id_button(chapter)


# POST /Aesop/Fables/frames/actions/clear_broken_frames
@bp.route(
    "/actions/clear_broken_frames",
    methods=["POST"],
)
def clear_broken_frames(author, title, chapter_number, language):
    chapterdir = get_chapterdir(author, title, chapter)
    chapterurl = get_chapterurl(author, title, chapter)

    framedir = os.path.join(const.LIBRARY_DIR, chapterdir, "frames")
    for framefn in glob.glob(os.path.join(framedir, "frame_*.png")):
        try:
            img = PIL.Image.open(framefn)
        except PIL.UnidentifiedImageError as e:
            log.error(f"Image {framefn} cannot be opened: {e}")
            log.info(f"Removing broken image {framefn}")
            os.remove(framefn)
            continue

        try:
            img.verify()  # Verify that it is, in fact an image
        except (OSError, IOError, SyntaxError) as e:
            log.error(f"Image {framefn} is broken: {e}")
            log.info(f"Removing broken image {framefn}")
            os.remove(framefn)

    return htmx.clear_broken_frames(chapterurl)


# POST /Anton%20Chekhov/The%20Lady%20with%20the%20Dog/chapter/0001/frames/regenerate_w_tmi
@bp.route(
    "/regenerate_w_tmi", methods=["POST"]
)
def regenerate_w_tmi(author, title, chapter_number, language):
    """
    Regenerate the imaginative image used in this frame using TMI (Text->Metadata->Image).
    """
    log.info("regenerate_w_tmi()")
    frame_index = int(request.form.get("frame_index", 0))
    log.info(f"Regenerating frame {frame_index} with TMI")

    chapterdir = get_chapterdir(author, title, chapter)
    chapterurl = get_chapterurl(author, title, chapter)
    bookdir = get_bookdir(author, title)

    mybook = booklib.get_book(chapterdir)

    # find the image used in this frame
    mp = mybook.load_masterplan()
    for mp_phrase in mp.get("words", []):
        start_frame = mp_phrase.get("start_frame", 0)
        end_frame = mp_phrase.get("end_frame", 0)

        if frame_index >= start_frame and frame_index <= end_frame:
            phrase_id = mp_phrase.get("id")
            paragraph_index = int(phrase_id.split("_")[0])
            break

    # now we have the phrase_id (1_2 etc) and the paragraph_index (0-based)
    paragraph = mybook.soup.find_all("paragraph")[paragraph_index]
    phrase = paragraph.find("phrase", {"id": phrase_id})
    image_xml = phrase.find_previous("image")

    # we we trigger TMI regeneration for this image
    # flux
    # images.tmi_regenerate_image(
    #     mybook, author, title, chapter, image_xml,
    #     chapterdir, bookdir,
    # )

    # zimage
    images.zmi_regenerate_image(
        mybook,
        author,
        title,
        chapter,
        image_xml,
        chapterdir,
        bookdir,
    )

    # but we aren't done yet.  This is frames.  So draw the frames.
    # frames.redraw_frame is super unfriendly, but
    # calling through mybook makes this easy.

    # clean save
    mybook.save_xml()
    # cache clear
    booklib.get_book.cache_clear()
    # reload
    mybook = booklib.get_book(chapterdir)

    for frame_index in range(start_frame, end_frame + 1):
        log.info(f"Redrawing frame {frame_index} to use new TMI image")
        mybook.redraw_frame(frame_index, force=True)

    # _now_ we are done.
    response = make_response(
        f"""<div id="frame_image" class="wa-stack" style="width: 33%; align-items: center; justify-content: center;">
            <img 
                border="1px solid white" style="width: 1920px;" 
                src="{chapterurl}/frames/widescreen/{frame_index}.png/" 
                alt="Frame {frame_index}"
            >
        </div>"""
    )
    return response


@bp.route("/")
def base(
    author, title, chapter_number, language, aspect="widescreen", frame_index=0
):
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)

    pretty_author = chapter.config.get("author", author)
    pretty_title = chapter.config.get("title", title)

    buttons = "\n".join(
        [
        ]
    )

    return render_template(
        "music.html",
        language=language,
        pretty_language=language.capitalize(),
        author=author,
        pretty_author=pretty_author,
        title=title,
        pretty_title=pretty_title,
        chapter=chapter,
        chapterurl=chapter.url,
        buttons=buttons,
        section="music",
        section_cosmetic="Music",
    )

