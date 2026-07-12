import os

from PIL import Image

import const
import logger
from camera import Camera, registry

log = logger.log(__name__)


def click_to_side(x1, y1, width, height):
    left = 0
    right = 0
    top = 0
    bottom = 0

    # new widget, auto-slide.  picker for rough start/stop, just slide for frame count.  it does the rest.
    if x1 < width * 0.33:
        # left side
        if y1 < height * 0.33:
            # top left
            top += 1
            left += 1
            # intention is unclear
        elif y1 > height * 0.66:
            # bottom left
            bottom += 1
            left += 1
        else:
            # middle left, which is at least clearly left.
            left += 2

    elif x1 > width * 0.66:
        # right side
        if y1 < height * 0.33:
            # bottom right
            bottom += 1
            right += 1
        elif y1 > height * 0.66:
            # top right
            top += 1
            right += 1
        else:
            # middle right
            right += 2

    # now, extra points for orientation
    if height > width:
        # portrait, y matters more
        if y1 < height * 0.49:
            top += 2
        elif y1 > height * 0.49:
            bottom += 2
    else:
        # landscape, x matters more
        if x1 < width * 0.49:
            left += 2
        elif x1 > width * 0.49:
            right += 2

    # and a final tip to break ties
    if height > width:
        # prefer top to bottom
        top += 1
        bottom -= 1
        left -= 1
        right -= 1
    elif height < width:
        # prefer left to right
        top -= 1
        bottom -= 1
        left += 1
        right -= 1

    origin_value, origin_side = max(
        (top, "top"), (bottom, "bottom"), (left, "left"), (right, "right")
    )

    return origin_side


def side_to_cropbox(side, width, height, target_aspect_ratio):
    if target_aspect_ratio > 0.9 and target_aspect_ratio < 1.1:
        crop_width = const.IMG_TARGET_WIDTH
        crop_height = const.IMG_TARGET_HEIGHT

    elif target_aspect_ratio >= 1.7 and target_aspect_ratio <= 1.8:
        # fullscreen
        crop_width = const.HSIZE
        crop_height = const.VSIZE

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


def slide_resize(in_image, fullscreen=False):
    """
    Resize the image to be suitable for sliding.  The shorter dimension is fixed
    to desired output size, then the longer dimension is resized to maintain
    aspect ratio.  This lets us slide the viewport along the longer dimension.
    """
    # this is all dumb we have two points x1,y1 and x2,y2 in the image
    # "in_image" where in_image.size is width x height
    #
    # we want the closest point on the line between the two square
    # centerpoints at the image extremes.  Lets not be stupid, we're talking
    # about portrait, landscape and square.  We calculate the correct
    # centerpoints based on the selected aspect ratio.
    # resize
    width, height = in_image.size
    if width > height:
        # landscape - we want the image to be exactly const.IMG_TARGET_HEIGHT
        # high so we can pan side to side, expanding or contracting width as needed to make that
        # happen.
        # pil resize:
        if fullscreen:
            in_image = in_image.resize(
                (const.VSIZE, int(const.VSIZE * (width / height))),
                resample=Image.LANCZOS,
            )
        else:
            in_image = in_image.resize(
                (
                    int(const.IMG_TARGET_HEIGHT * (width / height)),
                    const.IMG_TARGET_HEIGHT,
                ),
                resample=Image.LANCZOS,
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
                (const.HSIZE, int(const.HSIZE * (height / width))),
                resample=Image.LANCZOS,
            )
        else:
            in_image = in_image.resize(
                (
                    const.IMG_TARGET_WIDTH,
                    int(const.IMG_TARGET_WIDTH * (width / height)),
                ),
                resample=Image.LANCZOS,
            )
        width, height = in_image.size
        log.info(f"portrait resized from {owidth}x{oheight} to {width}x{height}")
    else:
        # square - we want the image to be exactly const.IMG_TARGET_WIDTH
        # wide so we can pan up and down, expanding or contracting height as needed to make that
        # happen.
        log.error("edge case I have not handled.")
        pass

    return in_image


class Zoom(Camera):
    cosmetic_name = "Zoom"
    name = "zoom"
    description = "Zoom In/Out from the selected point."

    values = {}

    def apply_half_right(self, image_xml, frame_directory):
        """
        Apply the zoom effect to the image.
        the configuration is all inside image_xml
        """
        # TODO
        book = image_xml.find_parent("book")
        if "aspect" not in book.attrs:
            log.warning("No aspect provided, defaulting to widescreen")
        aspect = book.attrs.get("aspect", "widescreen")

        paragraph_xml = image_xml.find_parent("paragraph")
        paragraphdir = paragraph_xml.attrs.get("dir", "")
        in_image = Image.open(
            os.path.join(
                const.LIBRARY_DIR,
                paragraphdir,
                image_xml.attrs.get("src", None),
            )
        )
        width, height = in_image.size
        log.info(f"image_xml.attrs: {dict(image_xml.attrs)}")

        # width/height reflect the 'real' current image size,
        # but the start_x/start_y are based on a 200px max dimension.
        ratio = 1
        if width > height:
            ratio = width / 200

        elif height > width:
            ratio = height / 200

        else:
            ratio = height / 200

        x = int(image_xml.attrs["camera_zoom_x"]) * ratio
        y = int(image_xml.attrs["camera_zoom_y"]) * ratio

        # snap anything close to the center to the exact center
        if abs(x - width / 2) < 50 and abs(y - height / 2) < 50:
            log.info('Snapping zoom center to exact image center')
            x = width / 2
            y = height / 2
            image_xml.attrs["camera_zoom_x"] = int(x / ratio)
            image_xml.attrs["camera_zoom_y"] = int(y / ratio)
        else:
            log.info(f'x deviation from center: {(x - width / 2)}px')
            log.info(f'y deviation from center: {(y - height / 2)}px')
            log.info(f"Zoom center at {x},{y} (image size {width}x{height})")

        frames = int(image_xml.attrs.get("camera_zoom_frames", 1))
        # Slide from an AR rectangle centered on {x1},{y1}
        # to the same rectangle centered on {x2,y2}
        # _and_ you're going to do it over {frames} frames.

        # we need to know if we're fullscreen or not, otherwise we don't know
        # what aspect ratio to target.
        fullscreen = paragraph_xml.attrs.get("fullscreen", "false").lower() == "true"
        if fullscreen:
            target_aspect_ratio = const.GEOMETRY[aspect]["ASPECT_RATIO"]
            log.info(f"Fullscreen paragraph, using aspect ratio {target_aspect_ratio}")
        else:
            log.info(
                f"Not fullscreen, using standard aspect ratio {const.STANDARD_ASPECT_RATIO}"
            )
            target_aspect_ratio = const.STANDARD_ASPECT_RATIO

        # we have a centerpoint, a starting zoom, an ending zoom and a number of frames.
        start_distance = float(image_xml.attrs.get("camera_zoom_start_distance", 1.0))
        end_distance = float(image_xml.attrs.get("camera_zoom_end_distance", 1.0))

        # we want to output `frames` image frames into frame_directory named like;
        # f"frame_{frame:06d}.png"
        slope = (end_distance - start_distance) / frames
        for frame in range(frames):
            frame_filename = f"frame_{frame:06d}.png"
            frame_pfn = os.path.join(frame_directory, frame_filename)

            log.info(f"{frame=} {frames=} {start_distance=} {end_distance=}")

            our_distance = (slope * frame) + start_distance
            log.info(f"our zoom distance: {our_distance}")

            # resize the image according to our distance
            zoomed_width = int(width * our_distance)
            zoomed_height = int(height * our_distance)
            if zoomed_width != width or zoomed_height != height:
                log.info(
                    "Resizing from %sx%s to %sx%s"
                    % (width, height, zoomed_width, zoomed_height)
                )
                zoomed_image = in_image.resize(
                    (zoomed_width, zoomed_height), resample=Image.LANCZOS
                )
            else:
                # no zoom needed
                zoomed_image = in_image

            # now, crop out the target size centered on (x * our_distance, y * our_distance)
            center_x = int(x * our_distance)
            center_y = int(y * our_distance)
            log.info(f"Centering on {center_x},{center_y}")
            if fullscreen:
                left = max(0, center_x - const.GEOMETRY[aspect]["HSIZE"] // 2)
                upper = max(0, center_y - const.GEOMETRY[aspect]["VSIZE"] // 2)
                right = min(
                    const.GEOMETRY[aspect]["HSIZE"],
                    center_x + const.GEOMETRY[aspect]["HSIZE"] // 2,
                )
                lower = min(
                    const.GEOMETRY[aspect]["VSIZE"],
                    center_y + const.GEOMETRY[aspect]["VSIZE"] // 2,
                )
            else:
                left = max(center_x - const.IMG_TARGET_WIDTH // 2, 0)
                upper = max(center_y - const.IMG_TARGET_HEIGHT // 2, 0)
                right = min(
                    center_x + const.IMG_TARGET_WIDTH // 2, const.IMG_TARGET_WIDTH
                )
                lower = min(
                    center_y + const.IMG_TARGET_HEIGHT // 2, const.IMG_TARGET_HEIGHT
                )

            log.info(f"Cropping to {left},{upper},{right},{lower}")

            out_image = zoomed_image.crop((left, upper, right, lower))

            if fullscreen:
                out_image = out_image.resize(
                    (
                        const.GEOMETRY[aspect]["HSIZE"],
                        const.GEOMETRY[aspect]["VSIZE"],
                    ),
                    resample=Image.LANCZOS,
                )
            else:
                out_image = out_image.resize(
                    (
                        const.IMG_TARGET_WIDTH,
                        const.IMG_TARGET_HEIGHT,
                    ),
                    resample=Image.LANCZOS,
                )

            log.info("Saving frame to %s" % frame_pfn)
            out_image.save(frame_pfn)

        # in_image = slide_resize(in_image=in_image, fullscreen=fullscreen)

        # width, height = in_image.size

        # # which side is indicated in the click location?
        # origin_side = click_to_side(x1, y1, width, height)

        # # make an aspect ratio crop box on the requested portion of the image.
        # ocleft, oupper, oright, olower = side_to_cropbox(
        #     side=origin_side,
        #     width=width,
        #     height=height,
        #     target_aspect_ratio=target_aspect_ratio
        # )

        # # which side is the destination?
        # destination_side = {
        #     "left": "right",
        #     "right": "left",
        #     "top": "bottom",
        #     "bottom": "top",
        # }[origin_side]

        # dcleft, dupper, dright, dlower = side_to_cropbox(
        #     side=destination_side,
        #     width=width,
        #     height=height,
        #     target_aspect_ratio=target_aspect_ratio
        # )

        # log.info(
        #     f"Sliding from {origin_side}:{ocleft},{oupper},{oright},{olower} to {destination_side}:{dcleft},{dupper},{dright},{dlower} over {frames} frames"
        # )

        # crop_width = const.IMG_TARGET_WIDTH
        # crop_height = const.IMG_TARGET_HEIGHT

        # for frame in range(frames):
        #     # we want to follow the stright line from origin to destination, spread over frames
        #     frac = frame / frames

        #     cleft = int(frac * (dcleft - ocleft) + ocleft)
        #     cupper = int(frac * (dupper - oupper) + oupper)
        #     cright = int(frac * (dright - oright) + oright)
        #     clower = int(frac * (dlower - olower) + olower)

        #     log.info(f"Cropping to {cleft},{cupper},{cright},{clower}")
        #     log.info(f"%s {cleft=} should be >= 0" % (cleft >= 0))
        #     if cleft < 0:
        #         # if we hit a wall, stick to it in that dimension.
        #         # left to right
        #         cright = (cright - cleft)
        #         cleft = 0

        #     if cright > width:
        #         # right to left
        #         cright = width
        #         cleft = width - crop_width

        #     if clower > height:
        #         # up and down
        #         clower = height
        #         cupper = height - crop_height

        #     if cupper < 0:
        #         # down and up
        #         cupper = 0
        #         clower = crop_height

        #     log.info(f"%s {cupper=} is >= 0" % (cupper >= 0))
        #     log.info(f"%s {cright=} is <= {width}" % (cright <= width))
        #     log.info(f"%s {clower=} is <= {height}" % (clower <= height))

        #     out_image = in_image.crop(
        #         (
        #             cleft, cupper,
        #             cright, clower
        #         )
        #     )

        # return out_image


    def apply(self, chapter, image_xml, frame_directory):
        """
        Apply the zoom effect to the image.
        the configuration is all inside image_xml
        """
        # TODO
        book = image_xml.find_parent("book")
        if "aspect" not in book.attrs:
            log.warning("No aspect provided, defaulting to widescreen")
        aspect = book.attrs.get("aspect", "widescreen")
        G = const.GEOMETRY[aspect]

        paragraph_xml = image_xml.find_parent("paragraph")
        paragraphdir = paragraph_xml.attrs.get("dir", "")
        in_image = Image.open(
            os.path.join(
                const.LIBRARY_DIR,
                paragraphdir,
                image_xml.attrs.get("src", None),
            )
        )
        width, height = in_image.size
        log.info(f"image_xml.attrs: {dict(image_xml.attrs)}")

        # width/height reflect the 'real' current image size,
        # but the start_x/start_y are based on a 200px max dimension.
        ratio = 1
        if width > height:
            ratio = width / 200

        elif height > width:
            ratio = height / 200

        else:
            ratio = height / 200

        x = int(image_xml.attrs["camera_zoom_x"]) * ratio
        y = int(image_xml.attrs["camera_zoom_y"]) * ratio

        # snap anything close (within 50 pixels) to the center to the exact center
        if abs(x - width / 2) < 50 and abs(y - height / 2) < 50:
            log.info('Snapping zoom center to exact image center')
            x = width / 2
            y = height / 2
            image_xml.attrs["camera_zoom_x"] = int(x / ratio)
            image_xml.attrs["camera_zoom_y"] = int(y / ratio)
        else:
            log.info(f'x deviation from center: {(x - width / 2)}px')
            log.info(f'y deviation from center: {(y - height / 2)}px')
            log.info(f"Zoom center at {x},{y} (image size {width}x{height})")

        frames = int(image_xml.attrs.get("camera_zoom_frames", 1))
        
        # Slide from an AR rectangle centered on {x1},{y1}
        # to the same rectangle centered on {x2,y2}
        # _and_ you're going to do it over {frames} frames.

        # we need to know if we're fullscreen or not, otherwise we don't know
        # what aspect ratio to target.
        fullscreen = paragraph_xml.attrs.get("fullscreen", "false").lower() == "true"
        if fullscreen:
            target_aspect_ratio = G["ASPECT_RATIO"]
            log.info(f"Fullscreen paragraph, using aspect ratio {target_aspect_ratio}")
        else:
            log.info(
                f"Not fullscreen, using aspect ratio of conventional image portion {const.STANDARD_ASPECT_RATIO}"
            )
            target_aspect_ratio = const.STANDARD_ASPECT_RATIO

        # we have a centerpoint, a starting zoom, an ending zoom and a number of frames.
        start_distance = float(image_xml.attrs.get("camera_zoom_start_distance", 1.0))
        end_distance = float(image_xml.attrs.get("camera_zoom_end_distance", 1.0))

        # we want to output `frames` image frames into frame_directory named like;
        # f"frame_{frame:06d}.png"
        zoom_slope = (end_distance - start_distance) / frames
        for frame in range(frames):
            frame_filename = f"frame_{frame:06d}.png"
            frame_pfn = os.path.join(frame_directory, frame_filename)

            log.info(f"{frame=} {frames=} {start_distance=} {end_distance=}")

            our_distance = (zoom_slope * frame) + start_distance
            log.info(f"our zoom distance: {our_distance}")

            # resize the image according to our distance
            # what is the actual aspect ratio of in_image?
            source_aspect_ratio = width / height
            
            # width to nearest even pixel, because ffmpeg is ultimately going
            # to be scaling this and it requires even dimensions
            zoomed_width = int((width * our_distance) // 2) * 2

            # height based on width and aspect ratio, but almost must divisible by 2.
            # which means.. yeah, a pixel of stretch.  sorry.
            zoomed_height = int((zoomed_width / target_aspect_ratio // 2) * 2)

            # crop to the new size, centered on (width, height).
            cropped = in_image.crop(
                (
                    int(x - (zoomed_width / 2)),  # left
                    int(y - (zoomed_height / 2)),  # upper
                    int(x + (zoomed_width / 2)),  # right
                    int(y + (zoomed_height / 2)),  # lower
                )
            )

            if fullscreen:
                zoomed_image = cropped.resize(
                    (G["HSIZE"], G["VSIZE"]),
                    resample=Image.LANCZOS
                )
            else:
                zoomed_image = cropped.resize(
                    (const.IMG_TARGET_WIDTH, const.IMG_TARGET_HEIGHT), 
                    resample=Image.LANCZOS
                )
            log.info("Saving frame to %s" % frame_pfn)
            zoomed_image.save(frame_pfn)

    def get_configuration_data(self, image_xml):
        """
        Return a list of dictionaries.  Each dictionary describes a parameter
        supported by this widget.
        """
        # definition driven config, you describe what you want here and it gets built.
        return [
            {
                "name": "",
                "widget": "pixel_chooser",
                "value": (
                    image_xml.attrs.get("camera_zoom_x", None),
                    image_xml.attrs.get("camera_zoom_y", None),
                ),
            },
            {
                "name": "frames",
                "label": "Frames",
                "widget": "slider",
                "value": image_xml.attrs.get("camera_zoom_frames", 1),
                "minimum": 1,
                "maximum": int(image_xml.attrs.get("frames", "100")),
            },
            {
                "name": "start_distance",
                "label": "Start Zoom (< 1 is zoom in, > 1 is zoom out)",
                # so at 0.5, the first frame has the image at half its initial size
                # (what do we fill the space with?)
                "widget": "slider",
                "value": image_xml.attrs.get("camera_zoom_start_distance", 1),
                "minimum": 0.1,
                "maximum": 3.0,
                "step": 0.1,
            },
            {
                "name": "end_distance",
                "label": "End Distance",
                # so at 1.5, we're zoomed in such that if the original image was
                # blown up to 150% of the original size, our final frame is the
                # cropped section of that blown up image centered on the
                # selected point.
                "widget": "slider",
                "value": image_xml.attrs.get("camera_zoom_end_distance", 1),
                "minimum": 0.1,
                "maximum": 3.0,
                "step": 0.1,
            },
        ]


registry.add(Zoom)
