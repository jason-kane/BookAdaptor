import os
import const
import logger as mylogger

from camera import registry as camera_registry
from flask import url_for, make_response


logger = mylogger.log(__name__)


def get_camera_configuration_widgets(chapter, image_index):
    log = logger.bind(chapter=chapter.key, image_index=image_index)
    
    image_index = int(image_index)

    all_images = chapter.get_xml().findAll("image")
    image_xml = all_images[image_index]
    log.info("Working with image xml", image_xml=image_xml)

    camera_motion = image_xml.attrs.get("camera_motion", "")

    out = ""
    if camera_motion:
        camera_obj = camera_registry.get(camera_motion)
        if camera_obj is None:
            log.error("No camera object found for motion type", camera_motion=camera_motion)
            return out

        camera_instance = camera_obj()
        log.info("Got camera instance", camera_instance=camera_instance, method=camera_motion)
        out += camera_instance.get_configuration_widgets(
            chapter=chapter,
            image_xml=image_xml,
            effect_config_list=camera_instance.get_configuration_data(image_xml),
        )

    return out




def camera_workshop(image_xml, chapter):
    image_index = image_xml.attrs["index"]
    log = logger.bind(chapter=chapter.key, image_index=image_index)

    paragraph = image_xml.find_parent("paragraph")
    paragraph_dir = chapter.get_paragraphdir(paragraph.attrs["index"])

    camera_workshop = '<div class="wa-stack" width="60%">'

    # player for the video if there are already frames drawn
    camera_video = os.path.join(
        const.LIBRARY_DIR, 
        paragraph_dir,
        f"camera_{image_index}.mp4"
    )

    if os.path.exists(camera_video):
        camera_workshop += f"""<p>
        The camera workshop is for plugins that emulate camera actions, like
        pan, zoom, rotate, shake, etc..  The idea is to allow these kinds of
        actions to occur without interfering with the underlying animation
        frames or transition frames.  Doesn't quite work yet, but that is the 
        plan. </p>

        <video
            id="camera" class="video-js" controls preload="auto" width="372"
            height="372" data-setup="{{}}"> <source
            src="camera/camera_{image_index}.mp4"
            type="video/mp4"> <p class="vjs-no-js">
                To view this video please enable JavaScript, and consider
                upgrading to a web browser that <a
                href="https://videojs.com/html5-video-support/"
                target="_blank">supports HTML5 video</a>
            </p>
        </video>
        """
    else:
        log.info("Expected video file", camera_video=camera_video)
        camera_workshop += "<p>No video available</p>"

    camera_workshop += camera_registry.selector(
        get_url=url_for(
            'library.book.chapter.images.camera.set_camera_attribute',
            **chapter.kwargs,
            image_index=image_index,
            attr="motion"
        ),
        #"set_motion",
        selected_key=image_xml.attrs.get("camera_motion", ""),
    )

    camera_workshop += '<div id="camera_configuration">'
    camera_workshop += get_camera_configuration_widgets(
        chapter=chapter, 
        image_index=image_index
    )
    camera_workshop += "</div>"

    build_camera_url = url_for(
        'library.book.chapter.images.camera.build_camera',
        **chapter.kwargs,
        image_index=image_xml.attrs['index']
    )

    delete_camera_url = url_for(
        'library.book.chapter.images.camera.delete_camera',
        **chapter.kwargs,
        image_index=image_xml.attrs['index']
    )

    buttons = [
        f"""
        <wa-button
            hx-post="{build_camera_url}" 
            hx-on::before-request="beforeRequest(this,event)" 
            hx-on::after-request="afterRequest(this,event)" 
            hx-target="#camera-panel" 
            hx-swap="outerHTML" 
            variant="brand" 
            appearance="accent" 
            size="m" 
            class="">Build Camera</wa-button>
        """,f"""
        <wa-button
            hx-delete="{delete_camera_url}"
            hx-on::before-request="beforeRequest(this,event)"
            hx-on::after-request="afterRequest(this,event)"
            hx-target="#strip-centerpiece" 
            hx-swap="outerHTML"
            variant="danger"
            appearance="accent"
            size="m">Delete Camera</wa-button>
        """,
    ]

    # video player if there are already animation frames
    # selector for the animation function
    # on selection, calls for the function's widgets
    # each function has its own widgets

    # all settings are persistent in the image tag
    # IE: pretty much all exactly the same as transitions

    camera_workshop += "<div class='wa-cluster'>"
    for b in buttons:
        camera_workshop += b
    camera_workshop += "</div>"  # button cluster

    camera_workshop += "</div>"  # top level -stack
    return camera_workshop

