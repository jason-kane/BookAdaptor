import glob
import os

from flask import (
    Blueprint,
    make_response,
    request,
    send_file,
)
from PIL import Image

import const
import logger
from artifact_editor.author.author import Author
from artifact_editor.cache import cache
from artifact_editor.chapter.chapter import Chapter
from artifact_editor.images import htmx as images_htmx
from artifact_editor.images import (
    images,
)
from artifact_editor.tools import (
    get_chapterdir,
    get_chapterurl,
)
from text_to_image.registry import registry as t2i_registry

from . import htmx, selector
from .selector import registry as selector_registry
import json

FIFO_FN = os.path.join(os.path.dirname(__file__), "..", "..", "drawing.fifo")

log = logger.log(__name__)

# /<author>/<path:title>/<chapter>/images
# we will be registered as a child of "images"
bp = Blueprint(
    "selector",
    __name__,
    template_folder=os.path.join(os.path.dirname(__file__), "templates"),
)


# POST /Aesop/Fables/0020/images/0/set_mode
@bp.route("/set_mode", methods=["POST"])
def set_image_mode(author, title, chapter, image_index=0):
    log.info(f"Setting image mode for {author}/{title}/{chapter}/images/{image_index}")

    new_mode = request.form.get("mode", "image")
    log.info(f"New mode: {new_mode}")

    chapterdir = get_chapterdir(author, title, chapter)
    chapterurl = get_chapterurl(author, title, chapter)
    mybook = booklib.get_book(chapterdir)

    all_images = mybook.soup.findAll("image")
    image_xml = all_images[image_index]

    image_xml.attrs["mode"] = new_mode
    mybook.save_xml()

    return images_htmx.image_strip_centerpiece(chapter, image_xml, default="image")


# POST /Aesop/Fables/0020/images/0/actions/apply_transformation
# @bp.route("/apply_transformation", methods=["POST"])
# def apply_transformation(author, title, chapter_number, language, image_index):
#     log.info("apply_transformation()")
#     author = Author(author)
#     chapter = Chapter(
#         author=author, title=title, number=chapter_number, language=language
#     )

#     transformation_type = request.form.get("mode")
#     src = request.form.get("src")

#     image_xml = chapter.get_image(image_index)

#     # image_dict = {
#     #     "mode": transformation_type,
#     #     "fullscreen": paragraph.attrs.get("fullscreen", "false") == "true",
#     #     "paragraph_dir": chapter.get_paragraph_dir(paragraph.attrs["index"]),
#     #     "image": image_xml.attrs.get("src", ""),
#     # }

#     # image_pfn = os.path.join(
#     #     const.LIBRARY_DIR,
#     #     image_dict["paragraph_dir"],
#     #     os.path.basename(image_dict["image"]),
#     # )

#     # log.info(f'calling apply_image_adjustments({image_pfn=}, {image_dict=})')
#     pil_image, transformed_image_pfn = selector.apply_image_adjustments(
#         chapter=chapter,
#         image_xml=image_xml,
#         transformation_type=transformation_type,
#         source_image_fn=src,
#         force=True,
#     )

#     # No, this isn't the new image.  It should show up as an option.
#     # if transformed_image_pfn:
#     #    image_xml.attrs["src"] = os.path.basename(transformed_image_pfn)
#     #    chapter.save_xml()

#     return images_htmx.prompt_panel(chapter, image_xml), 200


# /<author>/<path:title>/<chapter>/images/selector/<int:image_index>/actions/use_image
@bp.route("/actions/use_image", methods=["POST"])
def use_image(author, title, chapter_number, language, image_index=0):
    author = Author(author)
    chapter = Chapter(
        author=author, title=title, number=chapter_number, language=language
    )

    log.info(f"{request.form}")
    selected_image_src = request.form["selectedImage"].split("?")[
        0
    ]  # remove cache buster if present
    image_index = int(request.form["image_index"])

    image_xml = chapter.get_image(image_index)
    selected_image = os.path.basename(selected_image_src)

    log.info(f"Selected image: {selected_image}")

    image_xml.attrs["src"] = selected_image
    chapter.save_xml()

    cache.clear()  # clear cache to ensure new image is shown

    response = make_response("", 200)
    response.headers["HX-Refresh"] = "true"
    return response


# POST /Anton%20Chekhov/The%20Lady%20with%20the%20Dog/chapter/0001/images/38/actions/verify
@bp.route("/actions/verify", methods=["POST"])
def verify_image(author, title, chapter_number, language, image_index):
    author = Author(author)
    chapter = Chapter(author=author, title=title, number=chapter_number, language=language)

    image_xml = chapter.get_image(image_index)

    image_fn = image_xml.attrs["src"]

    try:
        image = Image.open(
            chapter.get_image_filename(image_index)
        )
        image.verify()

        verify_variant = "success"
    except (IOError, SyntaxError) as e:
        log.error(f"Image {image_fn} verification failed: {e}")
        verify_variant = "danger"

    return images_htmx.image_strip_centerpiece(
        chapter, image_xml, default="image", verify_variant=verify_variant
    )


# /H.%20P.%20Lovecraft/Cool%20Air/0001/images/4?fragdex=16&selectedImage=1
# /H.%20P.%20Lovecraft/Cool%20Air/0001/images/4/actions/image?fragdex=16&selectedImage=1
@bp.route("/", methods=["DELETE"])
def delete_image(author, title, chapter_number, language, image_index=0, force=True):
    """
    Delete an existing image.
    """
    author = Author(author)
    chapter = Chapter(
        author=author, title=title, number=chapter_number, language=language
    )
    image_index = int(image_index)

    image_xml = chapter.get_image(image_index)
    paragraph = image_xml.find_parent("paragraph")
    paragraph_dir = chapter.get_paragraph_dir(paragraph.attrs["index"])

    selectedImage = os.path.join(
        const.LIBRARY_DIR,
        paragraph_dir,
        os.path.basename(request.args["selectedImage"]),
    )

    # find this image on the filesystem.
    all_fragdex_images = glob.glob(
        os.path.join(const.LIBRARY_DIR, paragraph_dir, f"img_{image_index}_*")
    )

    if selectedImage in all_fragdex_images:
        fn_to_delete = selectedImage
    else:
        log.error(f"Selected image {selectedImage} not found in {all_fragdex_images}")
        return "Selected image not found", 404

    log.info(f"Deleting image file {fn_to_delete}")
    os.unlink(fn_to_delete)

    prompt_fn = os.path.join(
        const.LIBRARY_DIR,
        paragraph_dir,
        os.path.splitext(os.path.basename(fn_to_delete))[0] + ".prompt",
    )
    if os.path.exists(prompt_fn):
        log.info(f"Deleting prompt file {prompt_fn}")
        os.unlink(prompt_fn)
    else:
        log.info(f"No prompt file {prompt_fn} found")

    # was this the selected image?

    if image_xml.attrs.get("src", "") == os.path.basename(fn_to_delete):
        log.info("Deleted image was selected image.  Clearing src.")
        del image_xml.attrs["src"]

    # value = htmx.image_strip_centerpiece(
    #     mybook.soup,
    #     chapterurl,
    #     chapterdir,
    #     image_xml,
    #     default="image"
    # )
    # value = htmx.prompt_panel(chapterdir, chapterurl, image_xml)
    image_selector = htmx.image_selector(
        chapter=chapter, image_xml=image_xml, verify_variant="neutral"
    )
    chapter.save_xml()

    # response = make_response(f"""
    # <div id="selector" 
    #     hx-swap-oob="true"       
    # >
    #     {image_selector}
    # </div>""")
    # # hx-on::htmx:afterSwap="addCarouselListeners"
    # response.headers["Content-Type"] = "text/html"
    
    # add this event
    response = make_response("", 200)
    response.headers["HX-Trigger"] = "newCarouselReady"
    return response


@bp.route("/get_image_metadata", methods=["GET"])
def get_image_metadata(author, title, chapter_number, language, image_index=0):
    """
    Get metadata for an image.
    """
    author = Author(author)
    chapter = Chapter(
        author=author, title=title, number=chapter_number, language=language
    )
    image_index = int(image_index)

    src = request.args.get("src", None)

    image_xml = chapter.get_image(image_index)

    return htmx.image_metadata(chapter, image_xml, src), 200


##
# <img src="/Grimm/Fairy%20Tales/1/english/images/82/selector/alternate/img_82_Fairy_Tale_Art_mode__A_me_6f09a61f_1ab0.png">
# /alternate/img_1_court_of_king_arthur_tran_9965e754_43f8.png.adj.png.mask.png
@bp.route("/alternate/<path:filename>", methods=["GET"])
def get_alternate_image(author, title, chapter_number, language, image_index, filename):
    """
    Serve an alternate version of an image.
    """
    author = Author(author)
    chapter = Chapter(
        author=author, title=title, number=chapter_number, language=language
    )
    image_index = int(image_index)

    image_xml = chapter.get_image(image_index)

    paragraph = image_xml.find_parent("paragraph")
    paragraph_index = int(paragraph.attrs.get("index", 0))

    image_path = os.path.join(
        const.LIBRARY_DIR,
        chapter.chapterdir,
        "paragraphs",
        f"{paragraph_index:06}",
        filename,
    )
    if not os.path.exists(image_path):
        return "Image not found", 404
    return send_file(image_path)


@bp.route("/transformation_options", methods=["PUT"])
def get_transformation_options(author, title, chapter_number, language, image_index):
    """
    Get transformation options for an image.
    """
    author = Author(author)
    chapter = Chapter(
        author=author,
        title=title,
        number=chapter_number,
        language=language,
    )
    image_index = int(image_index)

    image_xml = chapter.get_image(image_index)
    src = request.form.get("src", None)
    mode = request.form.get("mode", "scale")
    tab = request.form.get("tab", "selector")

    # we want to respond with the form widgets needed
    # by 'mode'.
    transformation_class = selector_registry.get(mode)
    log.info("Creating configuration options for %s:%s", transformation_class, mode)
    return transformation_class().options(chapter, image_xml, src, tab)


@bp.route("apply_<transformation>", methods=["POST"])
def apply_transformation(
    author, title, chapter_number, language, image_index, transformation
):
    """
    Apply a transformation to an image.
    Obsolete - these are moving to the "editor" tab
    """
    author = Author(author)
    chapter = Chapter(
        author=author, title=title, number=chapter_number, language=language
    )
    image_index = int(image_index)

    image_xml = chapter.get_image(image_index)
    src = request.form.get("src", None)
    prompt = request.form.get("inpaint_prompt", "")
    region = json.loads(request.form.get("region", "{}"))

    image_pfn = selector_registry.get(transformation)().apply(
        chapter, image_xml, src, prompt, region
    )

    if image_pfn:
        image_xml.attrs["src"] = os.path.basename(image_pfn)
        chapter.save_xml()

    return images_htmx.prompt_panel(chapter, image_xml), 200
