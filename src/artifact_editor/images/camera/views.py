import os
import json
from artifact_editor.author.author import Author
from artifact_editor.chapter.chapter import Chapter
from artifact_editor.characters import characters
import logger
from camera import registry as camera_registry
from flask import url_for, make_response, send_file
from artifact_editor.images import images

from artifact_editor.video import video

from . import htmx

from flask import (
    Blueprint,
    request,
    make_response,
)
import const

log = logger.log(__name__)

bp = Blueprint(
    "camera",
    __name__,
    template_folder=os.path.join(os.path.dirname(__file__), "templates"),
)
# we are "/<int:image_index>/camera/*"


# TODO: fixme
# @bp.route("/<author>/<path:title>/chapter/<chapter>/paragraphs/<int:paragraph_index>/<category>/<int:image_index>/<motion>/<varname>/set_<key>", methods=["POST"])
# set/slide/frames/minmax
@bp.route("/set/<module_name>/<key>/<datatype>", methods=["POST"])
def set_camera_value(
    author, title, chapter_number, language, image_index, module_name, key, datatype
):
    """
    This is called by the camera selector to set the camera motion for an image.

    """
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)
    category = "camera"
    image_index = int(image_index)

    image_xml = chapter.get_image(image_index)

    if datatype == "pixel":
        position = json.loads(request.form.get("pos"))
        key = "_".join([category, module_name])
        if key:
            key.append(key)
        image_xml.attrs[f"{module_name}_x"] = position["x"]
        image_xml.attrs[f"{module_name}_y"] = position["y"]

    elif datatype == "minmax":
        image_xml.attrs[f"{category}_{module_name}_{key}_min"] = request.form.get(
            f"{key}_min"
        )
        image_xml.attrs[f"{category}_{module_name}_{key}_max"] = request.form.get(
            f"{key}_max"
        )

    elif datatype == "value":
        log.info(f"Form data: {request.form}")
        image_xml.attrs[f"{category}_{module_name}_{key}"] = request.form.get(key)

    log.info(f"Saving {image_xml.attrs}")
    chapter.save_xml()

    return htmx.get_camera_configuration_widgets(
        chapter=chapter, image_index=image_index
    ), 200


# POST /L.%20Frank%20Baum/The%20Marvelous%20Land%20of%20Oz/0001/images/0/actions/build_camera
@bp.route("/", methods=["POST"])
def build_camera(author, title, chapter_number, language, image_index):
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)
    image_index = int(image_index)

    image_xml = chapter.get_image(image_index)
    paragraphdir = chapter.get_paragraph_dir(
        image_xml.find_parent("paragraph").attrs["index"]
    )
    frame_directory = os.path.join(
        const.LIBRARY_DIR, paragraphdir, "image_frames", f"image_{image_index:06d}"
    )

    if "camera_motion" not in image_xml.attrs:
        image_xml.attrs["camera_motion"] = "static"
    else:  # we've got some motion on the ocean.
        log.info(f"Camera motion is {image_xml.attrs['camera_motion']}")
        registry_obj = camera_registry.get(image_xml.attrs["camera_motion"])
        if registry_obj:
            os.makedirs(frame_directory, exist_ok=True)

            # wiping old frames
            for f in os.listdir(frame_directory):
                os.remove(os.path.join(frame_directory, f))

            camera_instance = registry_obj()
            log.info(f"Got camera instance {camera_instance}")

            camera_instance.apply(chapter, image_xml, frame_directory)
            camera_video = os.path.join(
                const.LIBRARY_DIR,
                paragraphdir,
                f"camera_{image_index}.mp4",
            )
            video.assemble_mp4(
                fps=const.FPS,
                framedir=frame_directory,
                wavfile=None,
                videofile=camera_video,
                image_match="frame_%06d.png",
            )

    chapter.save_xml()

    response = make_response(
        htmx.camera_workshop(
            image_xml=image_xml,
            chapter=chapter,
        )
    )
    # response.headers["HX-Refresh"] = "true"
    return response


# DELETE /L.%20Frank%20Baum/The%20Marvelous%20Land%20of%20Oz/0001/images/0/camera
@bp.route("/", methods=["DELETE"])
def delete_camera(author, title, chapter_number, language, image_index):
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)
    image_index = int(image_index)

    image_xml = chapter.get_image(image_index)
    paragraphdir = chapter.get_paragraph_dir(
        image_xml.find_parent("paragraph").attrs["index"]
    )
    frame_directory = os.path.join(
        const.LIBRARY_DIR, paragraphdir, "image_frames", f"image_{image_index:06d}"
    )

    log.info(f"Camera motion is {image_xml.attrs.get('camera_motion', 'none')}")
    registry_obj = camera_registry.get(image_xml.attrs.get("camera_motion", ""))
    if registry_obj:
        # wiping old frames
        for f in os.listdir(frame_directory):
            os.remove(os.path.join(frame_directory, f))

        camera_video = os.path.join(
            const.LIBRARY_DIR, paragraphdir, f"camera_{image_index}.mp4"
        )
        os.remove(camera_video)

    if "camera_motion" in image_xml.attrs:
        del image_xml.attrs["camera_motion"]

    chapter.save_xml()

    response = make_response(
        htmx.camera_workshop(
            image_xml=image_xml,
            chapter=chapter,
        )
    )
    # response.headers["HX-Refresh"] = "true"
    return response


# GET /Aesop/Fables/0024/images/camera.mp4
@bp.route("/camera_<image_index2>.mp4", methods=["GET"])
def serve_camera_video(author, title, chapter_number, language, image_index, image_index2):
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)
    image_index = int(image_index)

    image_xml = chapter.get_image(image_index)
    paragraph = image_xml.find_parent("paragraph")

    camera_video = os.path.join(
        const.LIBRARY_DIR,
        chapter.get_paragraph_dir(paragraph.attrs["index"]),
        f"camera_{image_index}.mp4",
    )

    if os.path.exists(camera_video):
        return send_file(camera_video)
    else:
        return f"Camera video not found for {author.name}/{title}/{chapter}.", 404


# /L.%20Frank%20Baum/The%20Marvelous%20Land%20of%20Oz/0001/images/0/camera/set_motion?camera_motion=slide
@bp.route("/attribute/<attr>", methods=["GET"])
def set_camera_attribute(author, title, chapter_number, language, image_index, attr):
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)

    image_index = int(image_index)

    attribute = attr.lower().split("_")[-1]
    value = request.args.get(f"camera_{attribute}", "")
    log.info(f"Setting camera {attribute} for image {image_index} to {value}")

    image_xml = chapter.get_image(image_index)
    image_xml.attrs[f"camera_{attribute}"] = value
    chapter.save_xml()

    return htmx.camera_workshop(image_xml=image_xml, chapter=chapter)
