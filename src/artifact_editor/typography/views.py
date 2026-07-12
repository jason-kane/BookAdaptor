from cmd import PROMPT
import glob
import os
import random
import shutil
import subprocess

from artifact_editor.author.author import Author
import numpy as np
from flask import (
    Blueprint,
    render_template,
    request,
    send_file,
    send_from_directory,
)
from PIL import Image, ImageDraw
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import get_lexer_by_name

import artifact_editor.camera as camera
import artifact_editor.pageimage as pageimage
import const
import logger
from artifact_editor import (
    config,
)
from artifact_editor.audio import audio
from artifact_editor.chapter.chapter import Chapter
from artifact_editor.characters import characters
from artifact_editor.frames import frames
from artifact_editor.masterplan.masterplan import (
    delete_masterplan,
    generate_masterplan,
    get_masterplan,
    save_masterplan,
)
from artifact_editor.tools import (
    get_bookurl,
    get_chapterdir,
    get_chapterurl,
    tags_to_dict,
)

from . import (
    htmx,
    page_segment,
    typography,
)

log = logger.log(__name__)

bp = Blueprint(
    "typography",
    __name__,
    template_folder=os.path.join(os.path.dirname(__file__), "templates"),
)


@bp.route("/actions/clear_all_text", methods=["POST"])
def clear_all_text(author, title, chapter_number, language):
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)

    aspect = chapter.get_aspect()

    masterplan = get_masterplan(chapter)

    xml_to_latex = typography.XmlConverter(
        chapter=chapter,
        xml=chapter.get_xml().find("book"),
    )

    words = masterplan["words"]
    for word in words:
        if "paragraph_dir" in word:
            phrase_xml = chapter.get_phrase(word["index"])

            if phrase_xml is None:
                log.warning("Phrase %s not found in book XML", word["id"])
                continue

            # paragraph_index, fragdex = word["id"].split("_")
            paragraph_tags = word.get("paragraph_tags", {})

            # paragraph_index = int(paragraph_index)
            # fragdex = int(fragdex)

            has_text = paragraph_tags.get("has-text", True)

            paragraph_dir = word["paragraph_dir"]

            for image_file in glob.glob(
                os.path.join(
                    const.LIBRARY_DIR,
                    paragraph_dir.lstrip("/"),
                    aspect,
                    "*",
                )
            ):
                log.info("Removing typography file: %s", image_file)
                os.unlink(image_file)

            # dinosaurs
            if has_text:
                log.info("Wiping text layer for fragment %s", word["id"])
                xml_to_latex.wipe(
                    os.path.join(const.LIBRARY_DIR, paragraph_dir.lstrip("/")),
                    word["id"],
                )

    # clear the redis cache for this chapter
    frames.clear_cache(chapter)
    # clear the examples
    for fn in ["chapter.tex", "chapter.pdf"]:
        pfn = os.path.join(
            const.LIBRARY_DIR,
            chapter.chapterdir,
            fn,
        )
        if os.path.exists(pfn):
            os.unlink(pfn)

    # clear obsolete files (pre-hyper)
    for paragraph in chapter.get_xml().find_all("paragraph"):
        paragraph_dir = chapter.get_paragraph_dir(paragraph.attrs["index"])

        pdir = os.path.join(
            const.LIBRARY_DIR,
            paragraph_dir.lstrip("/"),
        )

        for pattern in [
            "highlighted_*.pdf",
            "highlighted_*.tex",
            "text_highlighted_*.pdf",
            "text_highlighted_*.tex",
            "text_layer_*.json",
            "text_layer_*.png",
        ]:
            for fn in glob.glob(os.path.join(pdir, pattern)):
                log.info("Removing obsolete %s", fn)
                os.unlink(fn)

    return htmx.clear_all_text_button(chapter)


@bp.route("/actions/draw_missing_text_widescreen", methods=["POST"])
def draw_missing_text_widescreen(author, title, chapter):
    """
    draw all missing latex highlights
    """
    log.info("draw_missing_text_widescreen invoked")
    chapterdir = get_chapterdir(author, title, chapter)
    chapterurl = get_chapterurl(author, title, chapter)

    draw_missing_text(chapterdir, chapter, aspect="widescreen")

    # recalculate_image_frames(chapterdir)
    return htmx.draw_missing_text_button_widescreen(chapterurl)


@bp.route("/actions/draw_missing_text_portrait", methods=["POST"])
def draw_missing_text_portrait(author, title, chapter):
    """
    draw all missing latex highlights
    """
    chapterdir = get_chapterdir(author, title, chapter)
    chapterurl = get_chapterurl(author, title, chapter)

    draw_missing_text(chapterdir, chapter, aspect="portrait")

    return htmx.draw_missing_text_button_portrait(chapterurl)


def draw_missing_text(chapterdir, chapter, aspect="widescreen"):
    """
    draw all missing latex highlights
    """
    masterplan = get_masterplan(chapterdir)
    if masterplan is None:
        masterplan = generate_masterplan(chapterdir)
        save_masterplan(chapterdir, masterplan)

    chapter = page_segment.Chapter(chapterdir, aspect=aspect)

    for phrase in chapter.phrases():
        # we're acting under the perhaps incorrect assumption that force=False
        # will be both correct and fast.
        page_segment.draw_text_layers(chapter, phrase, force=False)


@bp.route("/actions/redraw_all_text_old", methods=["POST"])
def redraw_all_text_old(author, title, chapter):
    """
    Redraw all the latex highlights
    """

    chapterdir = get_chapterdir(author, title, chapter)
    chapterurl = get_chapterurl(author, title, chapter)
    mybook = booklib.get_book(chapterdir)

    masterplan = get_masterplan(chapterdir)
    if masterplan is None:
        masterplan = generate_masterplan(chapterdir)
        save_masterplan(chapterdir, masterplan)

    xml_to_latex = typography.XmlConverter(
        chapter=chapter,
        xml=mybook.soup.find("book"),
    )

    words = masterplan["words"]
    for word in words:
        if "paragraph_dir" in word:
            fragment = mybook.soup.find(id=word["id"])

            if fragment is None:
                log.warning("Fragment %s not found in book XML", word["id"])
                continue

            paragraph_index, fragdex = word["id"].split("_")
            paragraph_tags = word.get("paragraph_tags", {})

            paragraph_index = int(paragraph_index)
            fragdex = int(fragdex)

            has_text = paragraph_tags.get("has-text", True)

            paragraph_dir = word["paragraph_dir"]

            # this is what we want, the fully rendered PNG of the text with our
            # fragment highlighted.

            if has_text:
                log.info("Wiping text layer for fragment %s", fragdex)
                xml_to_latex.wipe(
                    os.path.join(const.LIBRARY_DIR, paragraph_dir.lstrip("/")), fragdex
                )

                character_dict = characters.get_all_characters(mybook, chapterdir)
                log.info("Found %d characters in book", len(character_dict))

                # for Plays
                xml_to_latex.clear_characters()
                for character_name in character_dict:
                    xml_to_latex.add_character(character_name)

                xml_to_latex.write_as_latex(
                    paragraph_dir=paragraph_dir,
                    highlight_phrase=fragment,
                )

                _, ct_layers = xml_to_latex.combine_text_layers(paragraph_dir, fragdex)
                if ("top" not in ct_layers) or ("bottom" not in ct_layers):
                    log.error("Invalid ct_layers: %s", ct_layers)
                else:
                    fragment.attrs["top"] = ct_layers["top"]
                    fragment.attrs["bottom"] = ct_layers["bottom"]

                mybook.save_xml()

    mybook.save_xml()

    return htmx.redraw_all_text_button_widescreen(chapterurl)


@bp.route("/actions/redraw_all_text<aspect>", methods=["POST"])
def redraw_all_text(author, title, chapter, aspect="_widescreen"):
    """
    Redraw all the latex highlights
    """
    chapterdir = get_chapterdir(author, title, chapter)
    chapterurl = get_chapterurl(author, title, chapter)
    aspect = aspect.lstrip("_")

    masterplan = get_masterplan(chapterdir)
    if masterplan is None:
        masterplan = generate_masterplan(chapterdir)
        save_masterplan(chapterdir, masterplan)

    chapter = page_segment.Chapter(chapterdir, aspect=aspect)

    first = True  # force the first iteration
    for phrase in chapter.phrases():
        log.info("Drawing text for phrase %s", phrase.attrs.get("id", "unknown"))
        page_segment.draw_text_layers(chapter, phrase, force=first, rainbow=False)
        first = False

    # xml_to_latex = typography.XmlConverter(
    #     chapterdir=chapterdir,
    #     xml=mybook.soup.find("book"),
    # )

    # words = masterplan['words']
    # for word in words:
    #     if 'paragraph_dir' in word:
    #         fragment = mybook.soup.find(id=word['id'])

    #         if fragment is None:
    #             log.warning('Fragment %s not found in book XML', word['id'])
    #             continue

    #         paragraph_index, fragdex = word['id'].split("_")
    #         paragraph_tags = word.get("paragraph_tags", {})

    #         paragraph_index = int(paragraph_index)
    #         fragdex = int(fragdex)

    #         has_text = paragraph_tags.get("has-text", True)

    #         paragraph_dir = word['paragraph_dir']

    #         # this is what we want, the fully rendered PNG of the text with our
    #         # fragment highlighted.

    #         if has_text:
    #             log.info('Wiping text layer for fragment %s', fragdex)
    #             xml_to_latex.wipe(
    #                 os.path.join(
    #                     const.LIBRARY_DIR,
    #                     paragraph_dir.lstrip("/")
    #                 ),
    #                 fragdex
    #             )

    #             character_dict = characters.get_all_characters(mybook, chapterdir)
    #             log.info('Found %d characters in book', len(character_dict))

    #             # for Plays
    #             xml_to_latex.clear_characters()
    #             for character_name in character_dict:
    #                 xml_to_latex.add_character(character_name)

    #             xml_to_latex.write_as_latex(
    #                 paragraph_dir=paragraph_dir,
    #                 highlight_phrase=fragment,
    #             )

    #             _, ct_layers = xml_to_latex.combine_text_layers(
    #                 paragraph_dir,
    #                 fragdex
    #             )
    #             if ('top' not in ct_layers) or ('bottom' not in ct_layers):
    #                 log.error('Invalid ct_layers: %s', ct_layers)
    #             else:
    #                 fragment.attrs['top'] = ct_layers['top']
    #                 fragment.attrs['bottom'] = ct_layers['bottom']

    #             mybook.save_xml()

    # mybook.save_xml()
    if aspect == "widescreen":
        return htmx.redraw_all_text_button_widescreen(chapterurl)
    else:
        return htmx.redraw_all_text_button_portrait(chapterurl)


# /Aesop/Fables/0025/typography/actions/hyper_redraw_all_text_portrait
@bp.route("/actions/hyper_redraw_all_text_<aspect>", methods=["POST"])
def hyper_redraw_all_text(author, title, chapter_number, language, aspect):
    """
    Hyper Redraw all the latex highlights
    """
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)
    aspect = aspect.lstrip("_")

    G = const.GEOMETRY[aspect]

    masterplan = chapter.get_masterplan()
    if masterplan is None:
        masterplan = generate_masterplan(chapter)
        save_masterplan(chapter, masterplan)

    # draw the entire text with rainbow highlighting
    # prime the hyper redraw process..
    page_segment.draw_text_layers(chapter, phrase_xml=None, force=True, rainbow=True)

    # expire cached snippets
    for phrase_xml in chapter.phrases():
        fn = chapter.get_highlighted_text_snippet_fn(phrase_xml)
        if os.path.exists(fn):
            os.unlink(fn)

    return htmx.hyper_redraw_all_text_button(chapter, aspect)

    # okay, now instead of regenerating the latex for each phrase, we can
    # filter the rainbow image, determine the bounding box for each
    # highlighted phrase, and draw our own highlight box.  Even with all
    # that, it should be massively faster than regenerating the entire book
    # latex for each phrase.
    rainbow_fn = os.path.join(
        const.LIBRARY_DIR, chapter.chapterdir, "text_layer_rainbow.png"
    )

    plain_fn = os.path.join(
        const.LIBRARY_DIR, chapter.chapterdir, "text_layer_plain.png"
    )

    rainbow_image = Image.open(rainbow_fn).convert("RGBA")
    plain_image = Image.open(plain_fn)
    # go through each phrase, find its bounding box in the rainbow
    # image, and save that as its own text_layer image.

    total_phrases = len(list(chapter.phrases()))

    for phrase in chapter.phrases():
        paragraph = phrase.find_parent("paragraph")
        paragraph_tags = tags_to_dict(paragraph.attrs.get("tags", ""))

        if paragraph_tags.get("has-text", True) is False:
            # this paragraph has no text, skip it.
            log.info("Paragraph %s has has_text=False tag, skipping...", paragraph)
            continue

        log.info(
            "Drawing text for phrase %s/%s",
            phrase.attrs.get("index", "unknown"),
            total_phrases,
        )
        index = 10 + int(phrase.attrs.get("index", "0"))
        rainbow_int = index % 16777216  # limit to 24 bits, (R,G,B) w/8 bits each

        rainbow_bytes = rainbow_int.to_bytes(3, "big")
        r, g, b = rainbow_bytes

        # get the bounding box of the highlight color
        pixel_array = np.array(rainbow_image)
        highlight_mask = (
            (pixel_array[:, :, 0] != r)
            | (pixel_array[:, :, 1] != g)
            | (pixel_array[:, :, 2] != b)
            | (pixel_array[:, :, 3] != 255)
        )  # alpha channel must be 255 to harden the highlight edges so we don't get messed up by anti-aliasing noise.

        log.info(
            f"Filtering with mask for color R:{r:X} G:{g:X} B:{b:X} for phrase %s",
            phrase.attrs.get("index", "unknown"),
        )

        pixel_array[highlight_mask] = (0, 0, 0, 0)  # .astype(np.uint8) * 255
        try:
            highlight_only_text_image = Image.fromarray(pixel_array, mode="RGBA")
            highlight_only_text_image.save(f"/tmp/{index}_hot.png")
        except ValueError:
            log.error("Invalid image: %s", pixel_array)
            raise

        try:
            # only tells part of the story
            hleft, htop, hright, hbottom = highlight_only_text_image.getbbox()
            # ImageDraw.Draw(highlight_only_text_image).rectangle(
            #     (hleft, htop, hright, hbottom), outline="green", width=2
            # )
        except TypeError as err:
            log.error(err)
            log.error(
                "No highlighted region found in phrase %s",
                phrase.attrs.get("index", "unknown"),
            )
            continue

        log.info(
            "Highlight bounding box for phrase %s: %d, %d, %d, %d",
            phrase.attrs.get("index", "unknown"),
            hleft,
            htop,
            hright,
            hbottom,
        )
        if hbottom - htop > 1000:
            highlight_only_text_image.save(f"/tmp/{index}_huge.png")
            raise ValueError("Unreasonably large highlight box detected!")

        highlight_layer = Image.new(
            "RGBA", highlight_only_text_image.size, (0, 0, 0, 0)
        )

        # draw the highlight box for this phrase on the
        # plain-text-only rendered text.
        # my_plain_image = plain_image.copy()

        # one pixel offset to accommodate anti-aliasing
        top_pixel_strip = highlight_only_text_image.crop(
            (0, htop + 1, highlight_only_text_image.width, htop + 2)
        )

        try:
            tleft, ttop, tright, tbottom = top_pixel_strip.getbbox()
        except TypeError as err:
            log.error(err)
            log.error(
                "[%s] No top pixel strip found in phrase %s",
                index,
                phrase.attrs.get("index", "unknown"),
            )
            highlight_only_text_image.save(f"/tmp/{index}_hot_error.png")
            top_pixel_strip.save(f"/tmp/{index}_no_top_error.png")
            log.error(
                f"top_pixel_strip cropped at (0, {htop + 1=}, {highlight_only_text_image.width=}, {htop + 2=})"
            )
            raise

        bottom_pixel_strip = highlight_only_text_image.crop(
            (0, hbottom - 3, highlight_only_text_image.width, hbottom - 2)
        )

        try:
            bleft, btop, bright, bbottom = bottom_pixel_strip.getbbox()
        except TypeError as err:
            log.error(err)
            log.error(
                "No bottom pixel strip found in phrase %s",
                phrase.attrs.get("index", "unknown"),
            )

        lineheight = 51

        # middle line(s)
        gutter = 4

        first_edge = htop + lineheight + gutter
        last_edge = hbottom - lineheight - 1

        log.info("range(%d, %d, %d)", first_edge, last_edge, lineheight + gutter)

        # first line
        ImageDraw.Draw(highlight_layer).rectangle(
            [tleft, htop, hright, htop + lineheight], fill="yellow"
        )

        if abs(last_edge - first_edge) > lineheight:
            for i in range(first_edge, last_edge, lineheight + gutter + 1):
                # giving all the 'middle' lines uniform pixel perfect width
                # looks very organized on the page.
                ImageDraw.Draw(highlight_layer).rectangle(
                    [hleft, i, hright, i + lineheight + 1], fill="yellow"
                )

        # last line
        ImageDraw.Draw(highlight_layer).rectangle(
            [bleft, hbottom - lineheight, bright, hbottom], fill="yellow"
        )

        # (debug) draw a rectangle around the highlight
        # ImageDraw.Draw(highlight_layer).rectangle(
        #     [hleft, htop, hright, hbottom],
        #     outline="red",
        #     width=2
        # )

        # paste the plain layer on top of the highlight layer
        highlight_layer.paste(plain_image, (0, 0), plain_image)

        # not kidding
        # phrase_id = phrase.attrs.get("id", "").split("_")[-1]
        phrase_index = phrase.attrs["index"]
        _, _, _, bottom = highlight_layer.getbbox()

        if bottom + G["VSIZE"] < highlight_layer.height:
            # trim off everything more than a full screen height below
            # the last pixel of text.
            highlight_layer = highlight_layer.crop(
                (0, 0, highlight_layer.width, bottom + G["VSIZE"])
            )
        else:
            log.info(
                f'{bottom=} + {G["VSIZE"]=} <= {highlight_layer.height=} : Not cropping',
            )

        aspect_dir = os.path.join(
            const.LIBRARY_DIR,
            chapter.get_paragraph_dir(paragraph.attrs["index"]),
            aspect,
        )
        os.makedirs(aspect_dir, exist_ok=True)

        highlight_layer.save(os.path.join(aspect_dir, f"text_layer_{phrase_index}.png"))

        # Finally, call draw_text_layers, it should be all cache.
        log.info(
            "Finalizing text layer for phrase %s", phrase.attrs.get("index", "unknown")
        )
        page_segment.draw_text_layers(chapter, phrase, force=False, rainbow=False)

    return htmx.hyper_redraw_all_text_button(chapter, aspect)


@bp.route("/actions/refresh_examples", methods=["POST"])
def refresh_examples(author, title, chapter_number, language):
    """
    Remove the xml and pdf examples so they are regenerated
    """
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)

    # where it should be
    latex_path = os.path.join(const.LIBRARY_DIR, chapter.chapterdir, "chapter.tex")
    if os.path.exists(latex_path):
        os.remove(latex_path)

    pdf_path = os.path.join(const.LIBRARY_DIR, chapter.chapterdir, "chapter.pdf")
    if os.path.exists(pdf_path):
        os.remove(pdf_path)

    return htmx.refresh_examples(chapter)


@bp.route("/actions/clear_highlight_dimensions", methods=["POST"])
def clear_highlight_dimensions(author, title, chapter_number, language):
    """
    Clear all highlight dimensions

    These are set in neobreaker.text.XmlToLatex.combine_text_layers() so
    anything that calls that needs to update top/bottom.
    """
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)

    for paragraph in chapter.get_xml().find_all("paragraph"):
        if "top" in paragraph.attrs:
            del paragraph["top"]
        if "bottom" in paragraph.attrs:
            del paragraph["bottom"]
        if "page_index" in paragraph.attrs:
            del paragraph["page_index"]
        if "page_offset" in paragraph.attrs:
            del paragraph["page_offset"]

        for phrase in paragraph.find_all("phrase"):
            # Clear the highlight dimensions
            if "top" in phrase.attrs:
                del phrase["top"]
            if "left" in phrase.attrs:
                del phrase["left"]
            if "right" in phrase.attrs:
                del phrase["right"]
            if "bottom" in phrase.attrs:
                del phrase["bottom"]

    chapter.save_xml()

    return htmx.clear_highlight_dimensions_button(chapter)


@bp.route("/actions/reset_camera", methods=["POST"])
def reset_camera(author, title, chapter_number, language):
    """
    Rebuild the camera file
    """
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)

    aspect = chapter.get_aspect()

    delete_masterplan(chapter)
    log.info("Regenerate master plan for %s", chapter)
    masterplan = generate_masterplan(chapter)
    save_masterplan(chapter, masterplan)

    audio.recalculate_paragraph_durations(chapter)

    camera.delete_camera(chapter)
    chapter.build_frame_to_camera(aspect, force=True)

    return htmx.reset_camera_button(chapter)


paragraph_confidence = {}


def scroll_rate_adjust(chapter, interval, aspect="widescreen") -> bool:
    """
    Adjust the scroll rate for the chapter
    """
    threshold = 100  # pixels
    # rate_adjustment = 0.05  # 5% pixel nudge
    # rate_adjustment = 0.50  # 50% pixel shove
    # min_distance = 5    # frames
    # max_distance = 100  # frames

    paragraph_confidence_threshold = 5

    # post-render evaluation of camera rate every `interval` frames, inspect the
    # simulated frame from redis. determine where on the page the highlighted text is.
    # compared to the vertical center line.

    # If the bottom of the highlighted region in the last highlighted frame is
    # above centerline - threshold, we need to decrease the scroll rate.

    # If the top of the highlighted region in the first highlighted frame is
    # below the centerline + threshold, we need to increase the scroll rate.

    # we adjust the scroll rate by `adjustment` percentage pixels per second
    # retroactively, starting `distance` frames before the evaluated frame.

    # chapter = page_segment.Chapter(chapterdir, aspect=aspect)

    current_frame = 0
    evaluation_phrase = interval
    phrase_counter = 0

    camera.load_camera(chapter, aspect)
    previous_paragraph = None

    for paragraph in chapter.paragraphs():
        paragraph_tags = tags_to_dict(paragraph.attrs.get("tags", ""))
        confidence = paragraph_confidence.get(paragraph["index"], 0)

        if confidence >= paragraph_confidence_threshold:
            log.info(
                "Confidence %d for paragraph %s is high enough, skipping camera rate evaluation",
                confidence,
                paragraph["index"],
            )
            current_frame += int(paragraph.attrs["frames"])
            phrase_counter += len(paragraph.find_all("phrase"))

            while phrase_counter >= evaluation_phrase:
                evaluation_phrase += random.randrange(1, 5)
            continue

        log.info(f"{paragraph_tags=}")
        has_highlight = paragraph_tags.get("has-highlight", True)
        has_text = paragraph_tags.get("has-text", True)

        if not has_highlight or not has_text:
            log.info(
                f'Skipping camera rate evaluation for paragraph {paragraph['index']} with {has_highlight=} or {has_text=}'
            )

            current_frame += int(paragraph.attrs["frames"])
            phrase_counter += len(paragraph.find_all("phrase"))

            previous_paragraph = paragraph
            continue

        paragraph_tags = tags_to_dict(paragraph.attrs.get("tags", ""))
        if paragraph_tags.get("has-text", "false") == "true":
            log.info(
                "Skipping camera rate evaluation for fullscreen paragraph %s",
                paragraph["index"],
            )
            current_frame += int(paragraph.attrs["frames"])
            phrase_counter += len(paragraph.find_all("phrase"))

            previous_paragraph = paragraph
            continue

        first_phrase = False
        for phrase_xml in paragraph.find_all("phrase"):
            htop = None
            log.info(
                "Evaluating camera rate for phrase %s",
                phrase_xml.attrs.get("index", "unknown"),
            )

            if first_phrase or ((phrase_counter + 1) >= evaluation_phrase):
                # time to evaluate
                log.info("Evaluating camera rate at frame %d", current_frame)

                #########
                # first frame of this phase

                # get the scroll lock from camera
                scroll_lock = camera.frame_to_camera(current_frame)

                if scroll_lock is None:
                    log.error("No scroll lock for frame %d", current_frame)
                    # move on to the next phrase
                    continue

                if has_highlight:
                    # generate/find the text image for this frame this gives us
                    # an image segment where phrase_xml is highlighted, and
                    # at exactly the right scrolling offset.
                    text_image = page_segment.from_offset(
                        chapter=chapter,
                        phrase_xml=phrase_xml,
                        top_index=scroll_lock,
                        force=True,
                        no_background=True,
                    ).convert("RGBA")

                    # text_image would be pasted to (0,0), it's exactly what we need to test
                    # against.

                    text_height = text_image.height
                    center_line = text_height // 2

                    # get the bounding box of the highlight color
                    # We need the _REAL_ highlight box.
                    #
                    # height x width x 4 (RGBA)
                    pixel_array = np.array(text_image)
                    # red, green, blue, alpha = pixel_array.T

                    # the highlight color is #ff f2 00
                    # pixel_array[..., :-1][
                    #     (red == 255) & (green == 242) & (blue == 0)
                    # ] = (255, 255, 255, 255)  # set to transparent

                    # All pixels that aren't highlighter yellow should be transparent
                    highlight_mask = (
                        (pixel_array[:, :, 0] < 255)
                        | (pixel_array[:, :, 1] < 242)
                        | (pixel_array[:, :, 2] != 0)
                    )
                    pixel_array[highlight_mask, 3] = 0

                    highlight_only_text_image = Image.fromarray(pixel_array)
                    try:
                        hleft, htop, hright, hbottom = (
                            highlight_only_text_image.getbbox(alpha_only=True)
                        )
                    except TypeError as err:
                        log.error(err)
                        log.error(
                            "No highlighted region found in phrase %s at frame %d",
                            phrase_xml.attrs.get("index", "unknown"),
                            current_frame,
                        )
                        # log.info("Saving debug image to /tmp/debug_no_highlight.png")
                        # text_image.save('/tmp/debug_text.png')
                        # highlight_only_text_image.save('/tmp/debug_highlight.png')

                # text_image.save('/tmp/debug_highlight.png')

                # If the top of the highlighted region in the first highlighted
                # frame is below the centerline + threshold, we need to increase
                # the scroll rate.

                log.info(f"{htop=}, {center_line=}, {threshold=}")
                log.info(
                    f"Current scroll lock: {scroll_lock} [{htop}] --> ({center_line - threshold} - {center_line + threshold})"
                )

                if htop:
                    adj = False
                    # what would put us exactly at the center line?
                    perfect_adjust = htop - (center_line)

                    if htop > center_line + threshold:
                        log.info("adding")
                        # aim for the top of the 'window'
                        distance_to_adjust = perfect_adjust - threshold

                    elif htop < center_line - threshold:
                        log.info("subtracting")
                        # aim for the bottom of the window
                        distance_to_adjust = perfect_adjust + threshold
                    else:
                        log.info(
                            f"Top of highlighted phrase {htop} is within window ({center_line - threshold} - {center_line + threshold}) - continuing scan"
                        )
                        distance_to_adjust = 0
                        adj = False

                    if distance_to_adjust:
                        if first_phrase and previous_paragraph:
                            # if this is the first phrase of this paragraph, and
                            # the pevious paragraph was full screen, we want to
                            # jump the scrolllock to exactly where we need to be
                            # so we are perfectly centered.
                            previous_paragraph_tags = tags_to_dict(
                                previous_paragraph.attrs.get("tags", "")
                            )

                            if (
                                previous_paragraph_tags.get("has-text", "false")
                                == "true"
                            ):
                                # previous paragraph was fullscreen
                                # so.. we can cheat.  as much as we want.
                                # teleport the camera to exactly where we want it
                                adj = camera.set_scrollrate(
                                    current_frame, scroll_lock + perfect_adjust
                                )
                                camera.save_camera(chapter)
                                return adj

                        adj = camera.boost_scrollrate(
                            current_frame, scroll_lock + distance_to_adjust
                        )
                        camera.save_camera(chapter)
                        paragraph_confidence[paragraph["index"]] = 0

                        return adj
                else:
                    log.error("No highlighted region found, unable to adjust.")

                evaluation_phrase += random.randrange(1, 5)

            current_frame += int(float(phrase_xml.attrs["frames"]))
            phrase_counter += 1
            first_phrase = False

        log.info(
            "Increasing confidence for paragraph %s to %d of %d",
            paragraph["index"],
            confidence + 1,
            paragraph_confidence_threshold,
        )
        paragraph_confidence[paragraph["index"]] = confidence + 1

    # for current_frame in range(0, mybook.total_frames(), interval):
    #     # get the scroll lock from camera
    #     scroll_lock = camera.frame_to_camera(current_frame)

    #     # generate/find the text image for this frame
    #     text_image = typography.page_segment.from_offset(
    #         chapter=chapter,
    #         phrase_xml=phrase_xml,
    #         top_index=scroll_lock,
    #         force=force,
    #     )

    # text_image would be pasted to (0,0), it's exactly what we need to test
    # against.

    # done
    return False


# evaluate_camera_rate
@bp.route("/actions/evaluate_camera_rate_<aspect>", methods=["POST"])
def evaluate_camera_rate(author, title, chapter_number, language, aspect):
    """
    Evaluate the camera rate
    """
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)

    # max_phrase = len(mybook.soup.find_all("phrase"))
    # interval: how often we align the camera, in number of phrases.

    # I don't know what the best settings really are.
    #
    # So this would realign once in the middle and once at the end.
    # interval = max(1, max_phrase // 2)

    # six times
    # interval = max(1, max_phrase // 6)

    # Every other phrase would just be
    # interval = 2

    # but I have ten fingers.
    interval = 10

    # if max_phrase > 100:
    #     interval = random.randrange(10, max_phrase // 10)

    while True:
        r = scroll_rate_adjust(chapter, interval, aspect)
        log.info("Scroll rate adjustment result: %s", r)

        if not r:
            break

    if aspect == "widescreen":
        return htmx.evaluate_camera_rate_widescreen_button(chapter)
    else:
        return htmx.evaluate_camera_rate_portrait_button(chapter)


@bp.route("/typography/actions/verify_text_images", methods=["POST"])
def verify_text_images(author, title, chapter_number, language):
    """
    Verify all text images exist
    """
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)

    xml_to_latex = typography.XmlConverter(
        chapter=chapter,
        xml=chapter.get_xml().find("book"),
        aspect="widescreen",
    )
    xml_to_latex.verify_text_images()

    xml_to_latex = typography.XmlConverter(
        chapter=chapter,
        xml=chapter.get_xml().find("book"),
        aspect="portrait",
    )
    xml_to_latex.verify_text_images()

    return htmx.verify_text_images_button(chapter)


@bp.route("/actions/build_missing_highlight_geometry", methods=["POST"])
def build_missing_highlight_geometry(author, title, chapter_number, language):
    """
    Rebuild the highlight geometry

    I haven't needed this in a long while.
    No support for portrait mode.
    """
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)

    # Rebuild the geometry
    xml_to_latex = typography.XmlConverter(
        chapter=chapter,
        xml=chapter.get_xml().find("book"),
        aspect="widescreen",
    )

    for paragraph in chapter.get_xml().find_all("paragraph"):
        has_text = "has-text=false" not in paragraph.attrs.get("tags", "")
        paragraph_dir = paragraph.attrs.get("dir", "")

        if has_text:
            if "top" not in paragraph.attrs:
                previous_paragraph = paragraph.find_previous("paragraph")
                if previous_paragraph:
                    # If the top is not set, we can use the previous paragraph's bottom
                    paragraph.attrs["top"] = previous_paragraph.attrs.get("bottom", 0)
                else:
                    # we are the first paragraph.
                    paragraph.attrs["top"] = 0

            for phrase in paragraph.find_all("phrase"):
                # Check if the phrase has highlight dimensions
                if "top" not in phrase.attrs or "bottom" not in phrase.attrs:
                    # If not, we need to rebuild the geometry
                    log.info(
                        "Rebuilding highlight geometry for phrase %s",
                        phrase.attrs.get("id", "unknown"),
                    )

                    xml_to_latex.write_as_latex(
                        paragraph_dir=paragraph_dir,
                        highlight_phrase=phrase,
                    )

                    _, ct_layers = xml_to_latex.combine_text_layers(
                        paragraph_dir, phrase["fragdex"]
                    )

    for paragraph in chapter.get_xml().find_all("paragraph"):
        has_text = "has-text=false" not in paragraph.attrs.get("tags", "")
        paragraph_dir = paragraph.attrs.get("dir", "")

        if has_text:
            if (
                "page_index" not in paragraph.attrs
                or "page_offset" not in paragraph.attrs
            ):
                # how tall are the pages before this one?
                chapter.save_xml()
                log.info("Calculating page offsets for %s", paragraph_dir)
                pageimage.calculate_page_offsets(chapter)
                chapter.load_xml()

            for phrase in paragraph.find_all("phrase"):
                _, ct_layers = xml_to_latex.combine_text_layers(
                    paragraph_dir, phrase["fragdex"]
                )
                if ("top" not in ct_layers) or ("bottom" not in ct_layers):
                    log.error("Invalid ct_layers: %s", ct_layers)
                    continue

                # Check if the phrase has highlight dimensions
                if "top" not in phrase.attrs or "bottom" not in phrase.attrs:
                    # these are the _relative_ top and bottom, for this page.
                    # We don't want the relative, we want the absolute.  The paragraph provides the offset.
                    phrase.attrs["top"] = ct_layers[
                        "top"
                    ]  # + int(paragraph.attrs['page_offset'])
                    phrase.attrs["bottom"] = ct_layers[
                        "bottom"
                    ]  # + int(paragraph.attrs['page_offset'])
                # else:
                #     log.warning(f'Calculating missing {ct_layers=} (bottom and/or top)')
                #     ct_layers['bottom'] = int(phrase.attrs['bottom']) - int(paragraph.attrs['page_offset'])
                #     ct_layers['top'] = int(phrase.attrs['top']) - int(paragraph.attrs['page_offset'])

            if ct_layers:
                # the paragraph bottom is the bottom of its last phrase
                bottom_should_be = int(paragraph.attrs.get("page_offset", 0)) + int(
                    ct_layers["bottom"]
                )
                if paragraph.attrs.get("bottom", 0) != bottom_should_be:
                    # the log is meh,
                    log.info(
                        "Setting paragraph bottom from %s to %s",
                        paragraph.attrs.get("bottom", 0),
                        bottom_should_be,
                    )
                    paragraph.attrs["bottom"] = bottom_should_be
                    # but only calling save_xml() when there _is_ a change is golden.
                    chapter.save_xml()

    camera.delete_camera(chapter.chapterdir)
    return htmx.build_missing_highlight_geometry_button(chapter)


@bp.route("/chapter.pdf")
def serve_chapter_pdf(author, title, chapter_number, language):
    """
    Serve the chapter PDF file
    """
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)

    pdf_path = os.path.join(
        const.LIBRARY_DIR,
        chapter.chapterdir,
        'regenerated',
        "chapter_override.pdf"
    )

    if not os.path.exists(pdf_path):
        log.info("%s not found for %s", pdf_path, chapter)
        pdf_path = os.path.join(
            const.LIBRARY_DIR, chapter.chapterdir, "text_highlighted_plain.pdf"
        )

    if not os.path.exists(pdf_path):
        chapters_dir = os.path.join(const.LIBRARY_DIR, chapter.chapterdir, "paragraphs")
        for aspect in ["widescreen", "portrait"]:
            for paragraph_name in sorted(os.listdir(chapters_dir)):
                # look for any highlighted pdf to copy
                for sample in glob.glob(
                    os.path.join(
                        chapters_dir, paragraph_name, aspect, "text_highlighted_*.pdf"
                    )
                ):
                    # use the first one as the chapter sample
                    shutil.copy(
                        os.path.join(
                            chapters_dir, paragraph_name, "widescreen", sample
                        ),
                        pdf_path,
                    )
                    return send_file(
                        pdf_path, as_attachment=False, mimetype="application/pdf"
                    )

        log.error("PDF file does not exist: %s", pdf_path)
        return "PDF file not found", 404

    return send_file(pdf_path, as_attachment=False, mimetype="application/pdf")


@bp.route("/chapter.tex")
def serve_chapter_latex(author, title, chapter_number, language):
    """
    Serve the chapter LaTeX file
    """
    aspect = "widescreen"
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)

    latex_path = typography.get_chapter_tex(chapter, aspect)
    if os.path.exists(latex_path):
        return send_file(latex_path, as_attachment=False, mimetype="application/x-tex")
    else:
        log.warning("LaTeX file does not exist: %s", latex_path)
        return "LaTeX file not found", 404


@bp.route("/set_text_structure", methods=["PUT"])
def set_text_structure(author, title, chapter_number, language):
    """
    Set the text structure for the chapter
    """
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)

    text_structure = request.form.get("TEXT_STRUCTURE")

    chapter.config["TEXT_STRUCTURE"] = text_structure
    chapter.save_config()

    return "Text structure updated successfully", 200


@bp.route("/")
def base_typography(author, title, chapter_number, language):
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)

    pretty_author = chapter.config.get("author", author)
    pretty_title = chapter.config.get("title", title)

    aspect = chapter.get_aspect()
    latex_path = typography.get_chapter_tex(chapter, aspect)

    highlighted_latex = ""
    pygments_css = ""
    if os.path.exists(latex_path):
        with open(latex_path, "r") as f:
            chapter_latex = f.read()

            # Highlight the LaTeX code
            # lexer = get_lexer_by_name("latex")
            # formatter = HtmlFormatter(style="default", full=False, noclasses=False)
            # highlighted_latex = highlight(chapter_latex, lexer, formatter)
        # pygments_css = formatter.get_style_defs()

    if aspect == "portrait":
        redraw_all_text_button = htmx.redraw_all_text_button_portrait(chapter)
        draw_missing_text_button = htmx.draw_missing_text_button_portrait(chapter)
        evaluate_camera_rate_button = htmx.evaluate_camera_rate_portrait_button(chapter)
    else:
        redraw_all_text_button = htmx.redraw_all_text_button_widescreen(chapter)
        draw_missing_text_button = htmx.draw_missing_text_button_widescreen(chapter)
        evaluate_camera_rate_button = htmx.evaluate_camera_rate_widescreen_button(
            chapter
        )

    return render_template(
        "typography.html",
        author=author,
        pretty_author=pretty_author,
        title=title,
        pretty_title=pretty_title,
        chapter=chapter,
        language=language,
        pretty_language=language.capitalize(),
        hyper_redraw_all_text_button=htmx.hyper_redraw_all_text_button(chapter, aspect),
        redraw_all_text_button=redraw_all_text_button,
        draw_missing_text_button=draw_missing_text_button,
        clear_all_text_button=htmx.clear_all_text_button(chapter),
        reset_camera_button=htmx.reset_camera_button(chapter),
        evaluate_camera_rate_button=evaluate_camera_rate_button,
        verify_text_images_button=htmx.verify_text_images_button(chapter),
        build_missing_highlight_geometry_button=htmx.build_missing_highlight_geometry_button(
            chapter
        ),
        clear_highlight_dimensions_button=htmx.clear_highlight_dimensions_button(
            chapter
        ),
        refresh_examples=htmx.refresh_examples(chapter),
        pygments_css=pygments_css,
        #chapter_latex=chapter_latex,  # highlighted_latex,
        section="Typography",
        section_cosmetic="Typography",
    )


@bp.route("/regenerate", methods=["POST"])
def regenerate(author, title, chapter_number, language):
    """
    Regenerate the PDF render for this chapter
    """
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)

    latex = request.form.get("latex", "")

    # render the latex str to a pdf
    chapter_latex_override_fn = os.path.join(
        const.LIBRARY_DIR, chapter.chapterdir, "chapter_override.tex"
    )
    with open(chapter_latex_override_fn, "w") as f:
        f.write(latex)

    out_dir = os.path.join(
        const.LIBRARY_DIR,
        chapter.chapterdir,
        "regenerated",
    )
    os.makedirs(out_dir, exist_ok=True)

    subprocess.run(["latexmk", f"-output-directory={out_dir}", chapter_latex_override_fn])

    return "", 200


@bp.route("/save", methods=["POST"])
def save(author, title, chapter_number, language):
    """
    Save the modified latex as the new "real" version.
    """
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)

    return "", 200


@bp.route("/discard", methods=["POST"])
def discard(author, title, chapter_number, language):
    """
    Discard the modified latex and revert to the generated text.
    """
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)

    return "", 200
