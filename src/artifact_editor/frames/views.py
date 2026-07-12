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
    frames,
    htmx,
)

log = logger.log(__name__)

bp = Blueprint(
    "frames",
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
@bp.route("/<int:frame_index>")
@bp.route("/<aspect>/<int:frame_index>")
def base(
    author, title, chapter_number, language, aspect="widescreen", frame_index=0
):
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)

    frame_index = int(frame_index)
    phrase_xml = htmx.frame_to_phrase(chapter, frame_index)

    if phrase_xml is None:
        if frame_index == 0:
            # trixy, perhaps we don't have a masterplan?  Generate one.
            log.info("No frame_index provided, generating masterplan")
            masterplan.generate_masterplan(chapter)
            phrase_xml = htmx.frame_to_phrase(chapter, frame_index)

        if phrase_xml is None:
            if frame_index == 0:
                log.warning(
                    "Frames require a duration, generate audio to establish a timeline."
                )
                return

            log.warning(
                "No phrase found for frame %s, redirecting to last frame..", frame_index
            )
            # just go to the last frame
            return redirect(
                f"/{author.name}/{title}/{chapter_number}/frames/{max(0, frame_index - 1)}"
            )

    book = phrase_xml.find_parent("book")

    aspect = chapter.get_aspect()

    framedir = os.path.join(chapter.chapterdir, "frames", aspect)
    pretty_author = chapter.config.get("author", author)
    pretty_title = chapter.config.get("title", title)

    frames_dir = os.path.join(const.LIBRARY_DIR, framedir)
    os.makedirs(frames_dir, exist_ok=True)

    # this cache is too tight and is a pain to invalidate.
    # so..
    # total_frames = int(book.attrs.get("total_frames", 0))
    total_frames = 0
    if total_frames == 0:
        for phrase in chapter.get_xml().find_all("phrase"):
            total_frames += int(phrase.attrs.get("frames", "0"))

        book.attrs["total_frames"] = total_frames
        chapter.save_xml()

    buttons = "\n".join(
        [
            htmx.image_durations_button(chapter),
            htmx.set_fragment_id_button(chapter),
            htmx.clear_broken_frames(chapter),
            htmx.clear_cache(chapter),
            video_htmx.clear_all_frames_button(chapter),
        ]
    )

    # frame_pfn = os.path.join(
    #     framedir, f"frame_{frame_index:06}.png"
    # )
    frame_pfn = htmx.frame_pfn(chapter, aspect, frame_index)

    return render_template(
        "frames.html",
        frame_pfn=frame_pfn,
        language=language,
        pretty_language=language.capitalize(),
        aspect=aspect,
        frame_navigator=htmx.frame_navigator,
        frame_display=htmx.frame_display,
        frame_index=frame_index,
        author=author,
        pretty_author=pretty_author,
        title=title,
        pretty_title=pretty_title,
        chapter=chapter,
        chapterurl=chapter.url,
        buttons=buttons,
        section="frames",
        section_cosmetic="Frames",
    )

@bp.route("/open_directory")
def open_directory(author, title, chapter_number, language):
    from showinfm import show_in_file_manager

    fn = request.args.get("fn")
    show_in_file_manager(os.path.join(const.LIBRARY_DIR, fn))

    # chapterdir = get_chapterdir(author, title, chapter_number)
    # framedir = os.path.join(const.LIBRARY_DIR, chapterdir, "frames")
    # os.makedirs(framedir, exist_ok=True)

    # import webbrowser
    # webbrowser.open(framedir)
    return "", 200

# http://localhost:5000/L.%20Frank%20Baum/The%20Marvelous%20Land%20of%20Oz/1/frames/1220.hx
@bp.route("/<aspect>/<int:frame_index>.hx")
@bp.route("/<int:frame_index>.hx")
def htmx_frame(author, title, chapter_number, language, aspect=None, frame_index=None):
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)

    # use an oob-swap to set the src of the central image
    frame_display = htmx.frame_display(chapter, frame_index)

    frame_pfn = htmx.frame_pfn(chapter, aspect, frame_index)
    
    return f"""
<div id="frame-display" hx-swap-oob="true" class="wa-stack">
    {frame_display}
</div>

<div id="frame-pfn" hx-swap-oob="true">
    <wa-button
        hx-get="open_directory?fn={frame_pfn}"
        hx-swap="none"
    >Show in File Manager</wa-button>
</div>
"""


def camera_frame_override(image_dict, frame_index):
    image = image_dict["image"]
    
    camera_frame = os.path.join(
        "image_frames",
        "image_%06d" % image_dict["index"],
        f"frame_{frame_index:06}.png"
    )

    camera_frame_pfn = os.path.join(
        const.LIBRARY_DIR,
        image_dict["paragraph_dir"],
        camera_frame
    )

    if os.path.exists(camera_frame_pfn):
        image = camera_frame
    return image


# POST /H.%20P.%20Lovecraft/Cool%20Air/chapter/0001/frames/redraw
@bp.route(
    "/redraw<aspect>",
    methods=["POST"],
)
def redraw_frame(author, title, chapter_number, language, aspect="_widescreen"):
    """
    TODO: the handling of 'aspect' here is sloppy.
    """
    force = True

    log.info("============= redraw_frame()")
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)

    start_frame = int(request.form.get("start_frame", 0))
    end_frame = int(request.form.get("end_frame", 0))
    frame_index = int(request.form.get("frame_index", 0))
    log.info("Redrawing frame", frame_index=frame_index)

    aspect = chapter.get_aspect()

    # Generate a fresh master plan index.
    masterplan.delete_masterplan(chapter)
    mp = masterplan.generate_masterplan(chapter)

    framedir = os.path.join(
        const.LIBRARY_DIR,
        chapter.chapterdir,
        "frames",
        aspect
    )
    os.makedirs(framedir, exist_ok=True)

    frame_fn = os.path.join(
        framedir, f"frame_{frame_index:06}.png"
    )

    # "camera", in a bad name. this is the translation layer between the nice sequential frame count
    # with the scrolling rate of the text side, which attempts the illusion of linear with subtlety.
    
    # thats all abstracted into 'give me a frame index, I'll quickly tell you
    # exactly how many pixels to offset the image of the text you have correctly for this exact
    # moment. ie: we display it, camera is responsible for appropriate positioning in the timeline.

    camera.load_camera(chapter, aspect)
    scroll_lock = camera.frame_to_camera(frame_index)
    
    # a bunch of metadata about the text for this frame and the image currently displayed.
    word_dict, image_dict = masterplan.from_frame(mp, frame_index)
    
    # so damn lazy.
    paragraph_tags = word_dict.get("paragraph_tags", {})
    image_dict["paragraph_tags"] = paragraph_tags

    # override the image due to a camera configuration?  This would be something
    # like a slow pan over an image, wobble or zoom.
    image_dict["image"] = camera_frame_override(
        image_dict,
        frame_index
    )

    # how do you make it behave like the real thing?
    # use the real thing.  But this is so clean now.. umm.
    animate_lock = multiprocessing.Manager().Lock()

    if start_frame and start_frame != end_frame:
        # Multiple frames
        with multiprocessing.Pool() as pool:
            for frame_index in range(start_frame, end_frame + 1):
                pool.apply(frames.draw_frame, args=(
                    chapter.chapterdir,
                    chapter.get_aspect(),
                    frame_fn,
                    scroll_lock,
                    frame_index,
                    frame_index,
                    frame_index,
                    image_dict,
                    {},
                    animate_lock,
                    word_dict['paragraph_index'],
                    word_dict['index'],
                ), kwds={"force": force})
    else:
        # single frame
        # No need for all that fancy pants, it messes up the logging.  Same call, less overhead.
        frames.draw_frame(
            chapter.chapterdir,
            chapter.get_aspect(),
            frame_fn,
            scroll_lock,
            first_frame_index=image_dict['start_frame'],
            frame_index=frame_index,
            max_frame_index=image_dict['end_frame'],
            image_dict=image_dict,
            previous_image_dict={},
            animate_lock=animate_lock,
            paragraph_index=word_dict['paragraph_index'],
            phrase_id=word_dict['index'],
            force=force
        )

    # mybook = chapter.get_book(chapter.chapterdir)
    # log.info(f"Invoking {mybook.redraw_frame}({frame_index})")
    # mybook.redraw_frame(frame_index, aspect=aspect, force=True)

    # response = make_response(
    #     "Success"
    # )
    # response.headers["HX-Refresh"] = "true"
    # return response
    # _now_ we are done.
    G = const.GEOMETRY[aspect]
    img_width = G["HSIZE"]

    frame_image_url = url_for(
        "library.book.chapter.frames.frame_image",
        **chapter.kwargs,
        aspect=aspect,
        frame_index=frame_index,
    )

    response = make_response(
        f"""
        <div 
            hx-swap-oob="true" 
            id="frame_image" 
            class="wa-stack" 
            style="width: 40%; align-items: center; justify-content: center;"
        >
            <img 
                border="1px solid white" style="width: {img_width}px;" 
                src="{frame_image_url}?{time.time()}" 
                alt="Frame {frame_index}"
            ></img>
        </div>"""
    )
    return response


@bp.route(
    "/<aspect>/<int:frame_index>.png"
)
def frame_image(author, title, chapter_number, language, aspect, frame_index=0):
    log.info("frame_image()")
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)

    return send_from_directory(
        os.path.join(
            const.LIBRARY_DIR,
            chapter.chapterdir,
            "frames",
            aspect,
        ),
        f"frame_{frame_index:06}.png",
    )


# POST /Mark%20Twain/A%20Connecticut%20Yankee%20in%20King%20Arthurs%20Court/chapter/0001/frames/actions/clear_cache
@bp.route(
    "/actions/clear_cache", methods=["POST"]
)
def clear_cache(author, title, chapter_name, language):
    """
    Clear the page_segment cache for this chapter.
    """
    log.info("clear_cache()")
    author = Author(author)
    chapter = Chapter(author, title, chapter_name, language)

    frames.clear_cache(chapter)
    return htmx.clear_cache(chapter)


# @bp.route("/<author>/<path:title>/<chapter>/frames/actions/redraw", methods=["POST"])
# def redraw_frame(author, title, chapter):
# current_frame = int(request.args.get("frame_index", 0))
# # this will be interesting.  Draw a specific frame, by number.
# bookdir = get_chapterdir(None, title)
# framedir = os.path.join(const.LIBRARY_DIR, bookdir, "frames")

# mybook = book.Book(bookdir)
# mybook.load_master_plan()

# word = mybook.frame_to_word(current_frame)
# image = mybook.frame_to_image(current_frame)

# frame_fn = os.path.join(
#     framedir, f"frame_{current_frame:06}.png"
# )

# # given a frame #, we want the _paragraph_ so we can get
# # scroll_lock and pixels_per_frame without having to recalculate
# # from the beginning.

# scroll_lock = get_scroll_lock(current_frame)

# book.draw_frame(
#     bookdir,
#     frame_fn,
#     scroll_lock,
#     frame_start,
#     current_frame,
#     frame_end,
#     text_image_fn,
#     image,
#     previous_image,
#     animate_lock,
# )

# return ""
