
from flask import (
    request,
)
import json
from artifact_editor import app, tools
import image_to_video
import booklib
import logging
log = logging.getLogger(__name__)

# /L.%20Frank%20Baum/The%20Marvelous%20Land%20of%20Oz/chapter/0001/paragraphs/000000/images/0/image_to_video/recenter/set_pixel
@app.route("/<author>/<path:title>/<chapter>/paragraphs/<int:paragraph_index>/images/<int:image_index>/image_to_video/<action>/set_pixel", methods=["POST"]) 
def image_to_video_set_pixel(author, title, chapter, paragraph_index, image_index, action):
    widget_class = image_to_video.registry.get(action)
    mybook = booklib.get_book(tools.get_chapterdir(author, title, chapter))
    image_xml = mybook.soup.findAll("image")[image_index]

    paragraph = mybook.soup.findAll("paragraph")[paragraph_index]
    paragraphdir = paragraph.attrs["dir"]

    widget = widget_class(
        image_file=request.form.get("image_file"),
        paragraphdir=paragraphdir
    )
    log.info(f"request.form: {request.form}")
    xy = json.loads(request.form.get("pos", '{"x":0,"y":0}'))

    x, y = xy.get("x", 0), xy.get("y", 0)
    
    # not pixels, percent of image size.
    # need to convert to pixels.  But not here.
    # pixel_chooser has a 200px wide image selector
    # we're okay with this being a float.
    x = int(x / 2)
    y = int(y / 2)

    log.info(f"set_pixel x: {x}, y: {y}")
    image_xml.attrs[f"{action}_x1"] = x
    image_xml.attrs[f"{action}_y1"] = y
    mybook.save_xml()
    return widget.get_modal(image_xml)