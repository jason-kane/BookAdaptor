import os

from PIL import Image

import const
import logger
from camera import Camera, registry

log = logger.log(__name__)


def click_to_side(x1, y1, width, height):
    log.info(f"click_to_side(x1={x1}, y1={y1}, width={width}, height={height})")
    
    margin_ratio = 0.33

    # simple score based system
    left = 0
    right = 0
    top = 0
    bottom = 0

    # new widget, auto-slide.  picker for rough start/stop, just slide for frame count.  it does the rest.
    
    # (x1, y1) is the first click location, is it on the left side?
    if x1 < width * margin_ratio:
        # yes, on the left side.
        left += 2
    elif x1 > width * (1 - margin_ratio):
        # right side
        right += 2

    # on the top?
    if y1 < height * margin_ratio:
        # top left
        top += 2          
    # on the bottom?
    elif y1 > height * (1 - margin_ratio):
        # bottom left
        bottom += 2


    # now, extra points for orientation, except the
    # height/width we have are from _after_ resizing.

    # if height > width:
    #     # portrait, y matters more
    #     if y1 < height * 0.49:
    #         top += 2
    #     elif y1 > height * 0.49:
    #         bottom += 2
    # else:
    #     # landscape, x matters more
    #     if x1 < width * 0.49:
    #         left += 2
    #     elif x1 > width * 0.49:
    #         right += 2

    # and a final tip to break ties
    if height < width:
        # prefer top to bottom
        top += 1
        bottom -= 1
        left -= 1
        right -= 1

    elif height > width:
        # prefer left to right
        top -= 1
        bottom -= 1
        left += 1
        right -= 1

    origin_value, origin_side = max(
        (top, 'top'), 
        (bottom, 'bottom'), 
        (left, 'left'), 
        (right, 'right')
    )

    log.info(f"click_to_side => {origin_side}")
    return origin_side

def side_to_cropbox(side, width, height, G, fullscreen=False):

    if fullscreen:
        target_aspect_ratio = G['ASPECT_RATIO']
        crop_width = G["HSIZE"]
        crop_height = G["VSIZE"]        
    else:
        # not fullscreen, just a normal image.
        target_aspect_ratio = const.STANDARD_ASPECT_RATIO
        crop_width = const.IMG_TARGET_WIDTH
        crop_height = const.IMG_TARGET_HEIGHT

    if side == "left":
        # height * aspect ratio = width
        x1 = int((crop_height * target_aspect_ratio) / 2)
        y1 = height // 2
    elif side == "right":
        x1 = int(width - (crop_height * target_aspect_ratio) / 2)
        y1 = height // 2
    elif side == "top":
        x1 = width // 2
        y1 = int((crop_height / target_aspect_ratio) / 2)
    elif side == "bottom":
        x1 = width // 2
        y1 = int(height - (crop_width / target_aspect_ratio) / 2)

    # origin, the top edge is inside the image.
    ocleft, oupper, oright, olower = (
        x1 - crop_width // 2,
        y1 - crop_height // 2,
        x1 + crop_width // 2,
        y1 + crop_height // 2,
    )

    while oupper < 0:
        y1 += 1
        oupper = y1 - crop_height // 2
        olower = y1 + crop_height // 2

    while ocleft < 0:
        x1 += 1
        ocleft = x1 - crop_width // 2
        oright = x1 + crop_width // 2

    return (ocleft, oupper, oright, olower)

def slide_resize(in_image, fullscreen=False, aspect="widescreen"):
    """
    Resize the image to be suitable for sliding.  The shorter dimension is fixed
    to desired output size, then the longer dimension is resized to maintain
    aspect ratio.  This lets us slide the viewport along the longer dimension.
    """
    log.info('slide_resize(in_image, fullscreen, aspect)', in_image=in_image, fullscreen=fullscreen, aspect=aspect)
    # this is all dumb we have two points x1,y1 and x2,y2 in the image
    # "in_image" where in_image.size is width x height
    #
    # we want the closest point on the line between the two square
    # centerpoints at the image extremes.  Lets not be stupid, we're talking
    # about portrait, landscape and square.  We calculate the correct
    # centerpoints based on the selected aspect ratio.
    # resize
    G = const.GEOMETRY[aspect]
    width, height = in_image.size
    log.info(f"original image size: {width}x{height}")
    
    if width > height:
        # landscape - we want the image to be exactly const.IMG_TARGET_HEIGHT
        # high so we can pan side to side, expanding or contracting width as needed to make that
        # happen.
        # pil resize:
        if fullscreen:
            in_image = in_image.resize(
                (
                    G["VSIZE"],
                    int(G["VSIZE"] * (width / height))
                ),
                resample=Image.LANCZOS
            )
        else:
            in_image = in_image.resize(
                (
                    int(const.IMG_TARGET_HEIGHT * (width / height)),
                    const.IMG_TARGET_HEIGHT
                ),
                resample=Image.LANCZOS
            )
        width, height = in_image.size
        log.info(f"landscape resized to {width}x{height}")

    elif height > width:
        # portrait - we want the image to be exactly const.IMG_TARGET_WIDTH
        # wide so we can pan up and down, expanding or contracting height as needed to make that
        # happen.
        owidth, oheight = in_image.size
        
        if fullscreen:
            in_image = in_image.resize(
                (
                    G["HSIZE"],
                    int(G["HSIZE"] * (height / width))
                ),
                resample=Image.LANCZOS
            )
        else:
            in_image = in_image.resize(
                (
                    const.IMG_TARGET_WIDTH,
                    int(const.IMG_TARGET_WIDTH * (height / width))
                ),
                resample=Image.LANCZOS
            )
        width, height = in_image.size
        log.info(f"resized from {owidth}x{oheight} to {width}x{height}")
    else:
        # square - we want the image to be exactly const.IMG_TARGET_WIDTH
        # wide so we can pan up and down, expanding or contracting height as needed to make that
        # happen.
        log.error('edge case I have not handled.')
        pass

    return in_image


class Slide(Camera):
    cosmetic_name = "Slide"
    name = "slide"
    description = "Slide the image on the selected point."

    values = {}

    def apply(self, chapter, image_xml, frame_directory):
        """
        Apply the slide effect to the image.
        the configuration is all inside image_xml
        """
        # TODO
        paragraph_xml = image_xml.find_parent("paragraph")
        paragraphdir = chapter.get_paragraph_dir(paragraph_xml.attrs["index"])

        in_image = Image.open(
            os.path.join(
                const.LIBRARY_DIR, paragraphdir, image_xml.attrs.get("src", None)
            )
        )
        width, height = in_image.size

        # image_xml.attrs:
        # {
        #     "animation": "true",
        #     "camera": "close_up",
        #     "camera_motion": "slide",
        #     "camera_slide_end_x": "68",
        #     "camera_slide_end_y": "167",
        #     "camera_slide_frames": "44",
        #     "camera_slide_start_x": "65",
        #     "camera_slide_start_y": "29",
        #     "clip_prompt": "north,Mombi,rivers,Tip,Gillikins,pumpkin head,mountains,Land of Oz,village of Mombi,forests",
        #     "focus_character": "Tip",
        #     "fragdex": "0",
        #     "frames": "44",
        #     "index": "0",
        #     "recenter_x1": "46",
        #     "recenter_y1": "72",
        #     "setting": "Country of the Gillikins",
        #     "src": "img_2_image001.jpg",
        #     "t5_prompt": 'The front cover of a detailed carved and painted cover of a masterfully crafted leather-bound handmade special edition of "The Marvelous Land of Oz" by "L. Frank Baum".',
        #     "tod": "dawn",
        # }
        #
        # where in this width x height image are we starting the slide?? I know,
        # where is the precision?  It is good enough.
        log.info(f"image_xml.attrs: {dict(image_xml.attrs)}")
        
        # start_x and start_y are exact pixels _but_ with the image rendered
        # at 200px max width/height.
        # 
        # width/height reflect the 'real' current image size
        ratio = 1
        if width > height:
            ratio = width / 200
        
        elif height > width:
            ratio = height / 200
        
        else:
            ratio = height / 200

        x1 = int(image_xml.attrs["camera_slide_start_x"]) * ratio
        y1 = int(image_xml.attrs["camera_slide_start_y"]) * ratio

        x2 = int(image_xml.attrs["camera_slide_end_x"]) * ratio
        y2 = int(image_xml.attrs["camera_slide_end_y"]) * ratio

        # frames = int(image_xml.attrs.get("camera_slide_frames", 1))
        first_frame = int(image_xml.attrs.get("camera_slide_frames_min", 1))
        last_frame = int(image_xml.attrs.get("camera_slide_frames_max", 1))
        frames = int(image_xml.attrs["frames"])

        # Slide from an AR rectangle centered on {x1},{y1}
        # to the same rectangle centered on {x2,y2}
        # _and_ you're going to do it over {frames} frames.

        # we need to know if we're fullscreen or not, otherwise we don't know
        # what aspect ratio to target.
        aspect = chapter.get_aspect()
        G = const.GEOMETRY[aspect]

        fullscreen = paragraph_xml.attrs.get("fullscreen", "false").lower() == "true"
        if fullscreen:
            log.info('Fullscreen paragraph')
            image_xml.attrs["fullscreen"] = "true"
            target_aspect_ratio = G['ASPECT_RATIO']
        else:
            log.info(f'Not fullscreen, using standard aspect ratio {const.STANDARD_ASPECT_RATIO}')
            target_aspect_ratio = const.STANDARD_ASPECT_RATIO

        in_image = slide_resize(
            in_image=in_image, 
            fullscreen=fullscreen,
            aspect=aspect
        )

        width, height = in_image.size

        # which side is indicated in the click location?
        origin_side = click_to_side(x1, y1, width, height)

        # make an aspect ratio crop box on the requested portion of the image.
        # target_aspect_ratio=target_aspect_ratio
        ocleft, oupper, oright, olower = side_to_cropbox(
            side=origin_side,
            width=width,
            height=height,
            G=G,
            fullscreen=fullscreen
        )

        # which side is the destination?
        destination_side = {
            "left": "right",
            "right": "left",
            "top": "bottom",
            "bottom": "top",
        }[origin_side]

        dcleft, dupper, dright, dlower = side_to_cropbox(
            side=destination_side,
            width=width,
            height=height,
            G=G,
            fullscreen=fullscreen
        )

        log.info(
            f"Sliding from {origin_side}:{ocleft},{oupper},{oright},{olower} to {destination_side}:{dcleft},{dupper},{dright},{dlower} over {frames} frames"
        )

        crop_width = const.IMG_TARGET_WIDTH
        crop_height = const.IMG_TARGET_HEIGHT

        for frame in range(frames):
            # we want to follow the stright line from origin to destination, spread over frames
            if frame < first_frame:
                frac = 0.0
            elif frame > last_frame:
                frac = 1.0
            else:
                frac = (frame - first_frame) / (last_frame - first_frame)

            cleft = int(frac * (dcleft - ocleft) + ocleft)
            cupper = int(frac * (dupper - oupper) + oupper)
            cright = int(frac * (dright - oright) + oright)
            clower = int(frac * (dlower - olower) + olower)

            log.debug(f"Cropping to {cleft},{cupper},{cright},{clower}")
            log.debug(f"%s {cleft=} should be >= 0" % (cleft >= 0))
            if cleft < 0:
                # if we hit a wall, stick to it in that dimension.
                # left to right
                cright = (cright - cleft)
                cleft = 0

            if cright > width:
                # right to left
                cright = width
                cleft = width - crop_width

            if clower > height:
                # up and down
                clower = height
                cupper = height - crop_height

            if cupper < 0:
                # down and up
                cupper = 0
                clower = crop_height

            log.debug(f"%s {cupper=} is >= 0" % (cupper >= 0))
            log.debug(f"%s {cright=} is <= {width}" % (cright <= width))
            log.debug(f"%s {clower=} is <= {height}" % (clower <= height))

            out_image = in_image.crop(
                (
                    cleft, cupper,
                    cright, clower
                )
            )

            out_image.save(os.path.join(frame_directory, f"frame_{frame:06d}.png"))

        return out_image

    def get_configuration_data(self, image_xml):
        """
        Return a list of dictionaries.  Each dictionary describes a parameter
        supported by this widget.
        """
        # definition driven config, you describe what you want here and it gets built.
        return [
            {
                "name": "start",
                "widget": "pixel_chooser",
                "value": (
                    image_xml.attrs.get(f"camera_{self.name}_start_x", None),
                    image_xml.attrs.get(f"camera_{self.name}_start_y", None),
                ),
            },
            {
                "name": "end",
                "widget": "pixel_chooser",
                "value": (
                    image_xml.attrs.get(f"camera_{self.name}_end_x", None),
                    image_xml.attrs.get(f"camera_{self.name}_end_y", None),
                ),
            },
            {
                "name": "frames",
                "label": "Frames",
                "widget": "slider",
                "min-value": image_xml.attrs.get(f"camera_{self.name}_frames_min", 1),
                "max-value": image_xml.attrs.get(f"camera_{self.name}_frames_max", 1),
                "minimum": 1,
                "maximum": int(image_xml.attrs.get("frames", "100")),
                "range": True
            },
        ]

        # image_xml.attrs[f"{category}_{motion}_{varname}_min"] = request.form.get(f"{varname}_min")
        # image_xml.attrs[f"{category}_{motion}_{varname}_max"] = request.form.get(f"{varname}_max")


registry.add(Slide)
