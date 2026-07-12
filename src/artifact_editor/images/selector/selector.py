import os
import shutil
import time
from PIL import Image, ImageFilter

import const
import logger
from artifact_editor import (
    llm,
)
from artifact_editor.images import (
    images,
)

FIFO_FN = os.path.join(os.path.dirname(__file__), "..", "..", "drawing.fifo")

log = logger.log(__name__)


def apply_image_adjustments(
    chapter, image_xml, transformation_type, source_image_fn, force=False
):
    """
    aspect needed for fullscreen image support.  These are the "Mode" options on
    the "selector" tab.

    The general theme is ways to take a source image of arbitrary size and
    aspect ratio and make it fit into the target image size and aspect ratio.

    The output is a new image that can be chosen as the 'viewer' image.
    """
    log.debug(f"{transformation_type=}, {source_image_fn=}")
    paragraph = image_xml.find_parent("paragraph")

    source_image_fn = os.path.join(
        const.LIBRARY_DIR,
        chapter.get_paragraph_dir(paragraph.attrs["index"]),
        os.path.basename(source_image_fn),
    )

    fullscreen = paragraph.attrs.get("fullscreen", "false").lower() == "true"

    # these adjustments are called the "mode" in the image dict and xml.
    aspect = chapter.get_aspect()

    is_portrait = aspect == "portrait"
    is_widescreen = aspect == "widescreen"

    modesplit = transformation_type.split("_")

    if fullscreen:
        # for fullscreen images, we want to fill the entire screen with no black
        # bars, so we need to match the aspect ratio of the target geometry.
        target_aspect_ratio = const.GEOMETRY[aspect]["ASPECT_RATIO"]
        log.debug("Fullscreen image requested.")
    else:
        target_aspect_ratio = const.STANDARD_ASPECT_RATIO
        log.debug(f"{target_aspect_ratio} aspect ratio image requested.")

    snippie = "".join([a[:1] for a in modesplit])

    adjusted_image_pfn = os.path.join(
        os.path.dirname(source_image_fn),
        os.path.basename(source_image_fn).replace(".png", f"_adj{snippie}.png"),
    )
    log.info('Transforming %s into %s', source_image_fn, adjusted_image_pfn)

    # we already have the adjusted image ready to go.
    if os.path.exists(adjusted_image_pfn) and not force:
        try:
            log.debug(f"Using adjusted image: {os.path.basename(adjusted_image_pfn)}")
            imaginative_image = images.load_image(adjusted_image_pfn)
            return imaginative_image, adjusted_image_pfn

        except images.UnidentifiedImageError:
            images.load_image.cache_clear()

            if os.path.exists(adjusted_image_pfn):
                log.error("Deleting broken image %s", adjusted_image_pfn)
                os.unlink(adjusted_image_pfn)

    if not os.path.exists(source_image_fn):
        log.error("Original image %s does not exist", source_image_fn)
        # nothing we can do here, we've got no image.
        return

    log.debug(f"Adjusting original image: {source_image_fn}")

    try:
        imaginative_image = images.load_image(source_image_fn)
        ii_pfn = source_image_fn
    except images.UnidentifiedImageError:
        log.error("Cannot load image %s. Deleting it..", source_image_fn)
        os.unlink(source_image_fn)
        images.load_image.cache_clear()
        raise images.UnidentifiedImageError("Error loading image")

    if not os.path.exists(source_image_fn):
        # nothing we can do here, we've got no image.
        return

    log.info("Applying image transformation: %s", modesplit)

    if "flip" in modesplit:
        if "horizontal" in modesplit:
            log.info("Flipping image horizontally")
            imaginative_image = imaginative_image.transpose(
                Image.Transpose.FLIP_LEFT_RIGHT
            )
        elif "vertical" in modesplit:
            log.info("Flipping image vertically")
            imaginative_image = imaginative_image.transpose(
                Image.Transpose.FLIP_TOP_BOTTOM
            )
        imaginative_image.save(adjusted_image_pfn)

    if "crop" in modesplit:
        distance = int(modesplit[modesplit.index("crop") + 1])
        # "crop_100_outpaint_portrait":
        # before we outpaint, crop in by 100 pixels on each side.  It's
        # very common for images to have borders or other artifacts at the
        # edges that mess up outpainting.
        imaginative_image = imaginative_image.crop(
            (
                distance,
                distance,
                imaginative_image.size[0] - distance,
                imaginative_image.size[1] - distance,
            )
        )
        imaginative_image.save(adjusted_image_pfn)

    if "outpaint" in modesplit:
        # outfill the image to achieve desired aspect ratio
        if round(imaginative_image.size[0] / imaginative_image.size[1], 2) == round(
            target_aspect_ratio, 2
        ):
            # we already have the right aspect ratio, just scale it to fit.
            log.info("Image already has the correct aspect ratio, scaling to fit.")
        else:
            # we need to add height or width to achieve the desired aspect ratio.
            # we will augment the image by outpainting with a diffusion model.
            #
            # first center our image on a black canvas.
            if fullscreen:
                target_size = const.GEOMETRY[aspect]["SIZE"]
            else:
                target_size = (const.IMG_TARGET_WIDTH, const.IMG_TARGET_HEIGHT)

            keep_color = (0, 0, 0, 0)
            replace_color = (255, 255, 255, 255)

            # create a new image to paste our original image onto
            # with a grey background.
            canvas = Image.new("RGB", target_size, (128, 128, 128))

            # calculate the position to paste the original image
            paste_position = (
                (target_size[0] - imaginative_image.size[0]) // 2,
                (target_size[1] - imaginative_image.size[1]) // 2,
            )
            # paste the original image onto the canvas
            canvas.paste(imaginative_image, paste_position)

            # bigger = worse quality but less VRAM
            #
            # starting with 1024x1024, going to 16:9
            #
            # 6 uses about 8GB, and is obviously blurry
            # (384x216) 5 uses about 9GB, clearly better than 6
            # (480x270) 4 uses about 9.4GB, looks pretty good
            # (640x360) 3 uses about 9.4GB(?!), looks good
            # (960x540) 2 uses about 9.4GB(!!),
            resize_factor = 2

            canvas = canvas.resize(
                (target_size[0] // resize_factor, target_size[1] // resize_factor),
                Image.Resampling.LANCZOS,
                reducing_gap=3.0,
            )

            # start with the mask also all replace_color
            mask = Image.new(
                "RGBA",
                (target_size[0] // resize_factor, target_size[1] // resize_factor),
                replace_color,
            )

            # keep_color box over the original image area in the mask
            # the rest will be replace_color, the padding is to take
            # care of edge artifacts by giving up the edge pixels on
            # the original.
            padding = 2
            mask.paste(
                keep_color,
                (
                    paste_position[0] // resize_factor + padding,
                    paste_position[1] // resize_factor + padding,
                    (
                        paste_position[0] // resize_factor
                        + imaginative_image.size[0] // resize_factor
                    )
                    - padding,
                    (
                        paste_position[1] // resize_factor
                        + imaginative_image.size[1] // resize_factor
                    )
                    - padding,
                ),
            )

            # blur the mask to soften the edges of the inpainting
            # otherwise we get lines.
            mask = mask.filter(ImageFilter.GaussianBlur(3))

            canvas.save(adjusted_image_pfn + ".canvas.png")
            mask.save(adjusted_image_pfn + ".mask.png")

            # blocks until the image exists
            outpainted_image_fn = adjusted_image_pfn + ".outpaint.png"

            if force and os.path.exists(outpainted_image_fn):
                os.unlink(outpainted_image_fn)

            if not os.path.exists(outpainted_image_fn):
                log.info(
                    "Requesting outpainting for image %s with target aspect ratio %s",
                    source_image_fn,
                    target_aspect_ratio,
                )
                llm.outpainting(
                    prompt="Extend the image while maintaining the lighting, colors and composition",
                    image_fn=adjusted_image_pfn + ".canvas.png",
                    mask_fn=adjusted_image_pfn + ".mask.png",
                    output_fn=outpainted_image_fn,
                )

            # opening this right away seems to fail with PNG corruption
            time.sleep(0.5)

            check = Image.open(outpainted_image_fn)
            check.verify()
            check.close()

            imaginative_image = Image.open(outpainted_image_fn)

            # log.info('Resizing outpainted image to %s', target_size)
            imaginative_image = imaginative_image.resize(
                target_size, Image.Resampling.LANCZOS, reducing_gap=3.0
            )

            # log.info('Saving outpainted image to %s', adjusted_image_pfn)
            imaginative_image.save(adjusted_image_pfn)

            # imaginative_image = white_canvas
            # now we have a centered image on a white canvas, we can outpaint
            # to fill in the white areas.
            # imaginative_image.save(adjusted_image_pfn)

    # square scale up
    if "scale" in modesplit:
        # is the aspect ratio the same?  Right now target_height=target_width
        # (square) but that won't always be the case (probably), so we're
        # comparing the three decimal place float of the aspect ratio.  The
        # intent is to refuse to warp the image by linearly scaling which would
        # ruin the aspect ratio.
        if round(imaginative_image.size[0] / imaginative_image.size[1], 2) == round(
            target_aspect_ratio, 2
        ):
            # same aspect ratio, we can just jam it in.
            if fullscreen:
                # fullscreen mode, so we want to fill the entire screen
                target_size = const.GEOMETRY[aspect]["SIZE"]

                if imaginative_image.size != target_size:
                    log.info("Resizing image to fullscreen %s", target_size)
                    imaginative_image = imaginative_image.resize(
                        target_size, Image.Resampling.LANCZOS, reducing_gap=3.0
                    )
                    log.info("Saving adjusted image to %s", adjusted_image_pfn)
                    imaginative_image.save(adjusted_image_pfn)
                    ii_pfn = adjusted_image_pfn

            elif imaginative_image.size != (
                const.IMG_TARGET_WIDTH,
                const.IMG_TARGET_HEIGHT,
            ):
                log.info(
                    "Resizing image from %sx%s to %sx%s",
                    imaginative_image.size[0],
                    imaginative_image.size[1],
                    const.IMG_TARGET_WIDTH,
                    const.IMG_TARGET_HEIGHT,
                )
                imaginative_image = imaginative_image.resize(
                    (const.IMG_TARGET_WIDTH, const.IMG_TARGET_HEIGHT),
                    Image.Resampling.LANCZOS,
                    reducing_gap=3.0,
                )

                # so this can fail.
                imaginative_image.save(adjusted_image_pfn + ".rotate.png")
                time.sleep(0.5)
                i = Image.open(adjusted_image_pfn + ".rotate.png")

                try:
                    i.verify()
                except Exception as e:
                    log.error(f"Image verification failed: {e}")
                    os.unlink(adjusted_image_pfn + ".rotate.png")

                shutil.move(adjusted_image_pfn + ".rotate.png", adjusted_image_pfn)

                ii_pfn = adjusted_image_pfn
        else:
            # we need to pan, crop or outfill.
            log.error(
                "Aspect ratio mismatch (Yours: %sx%s %s%s, Required Ratio: %s), cannot scale image.  A more advanced method is needed.",
                imaginative_image.size[0],
                imaginative_image.size[1],
                round(imaginative_image.size[0] / imaginative_image.size[1], 3),
                "(fullscreen)" if fullscreen else "",
                round(target_aspect_ratio, 3),
            )

    log.info(f"Image adjustment complete.  Returning {imaginative_image=}, {ii_pfn=}")

    return imaginative_image, ii_pfn


class Registry:
    def __init__(self):
        self._registry = {}

    def register(self, key, provider_class):
        self._registry[key] = provider_class

    def get(self, key):
        return self._registry.get(key)

    def iterate_transformations(self):
        for i in self._registry.items():
            yield i


registry = Registry()


class ImageTransformations:
    def options(self, chapter, image_xml, src, tab):
        """
        Returns raw HTML for the widgets suitable for configuring this transformation.
        It will be inside the form, so you get "src" and "image_index" as hidden form values automatically.
        """
        return """
        <wa-button
            hx-post="selector/apply_transformation"
            hx-target="#strip-centerpiece"
            hx-swap="outerHTML transition:true"
            hx-trigger="click"
            name="button">Apply Transformation
        </wa-button>"""

class Scale(ImageTransformations):
    cosmetic = "Scale"
    

class HorizontalFlip(ImageTransformations):
    cosmetic = "Horizontal Flip"
    

class Outpaint(ImageTransformations):
    cosmetic = "Outpaint"
    

class CropBorders(ImageTransformations):
    cosmetic = "Crop Borders"
    

class InpaintRegion(ImageTransformations):
    cosmetic = "Inpaint Region"

    def apply(self, chapter, image_xml, src, prompt, region):
        """
        We want to use 'region' to create a mask, then send the image and mask
        through the outpainting pipeline with the prompt to do inpainting on
        just that region.  Then we want to save the result and return the new
        image path so it can be reloaded in the viewer.
        """
        # region is a dict with x, y, width, height of the region to inpaint.
        original_image_fn = os.path.join(
            const.LIBRARY_DIR,
            chapter.get_paragraph_dir(image_xml.find_parent("paragraph").attrs["index"]),
            os.path.basename(src),
        )
        mask_image_fn = original_image_fn.replace(".png", "_mask.png")
        inpainted_image_fn = original_image_fn.replace(".png", "_inpaint.png")

        original_image = Image.open(original_image_fn)

        keep_color = (0, 0, 0, 0)
        replace_color = (255, 255, 255, 255)

        mask = Image.new(
            "RGBA",
            original_image.size,
            keep_color,
        )
        
        mask.paste(
            replace_color,
            (
                int(region["x"]), int(region["y"]), 
                int(region["x"]) + int(region["width"]), int(region["y"]) + int(region["height"])
            ),
        )
        mask = mask.filter(ImageFilter.GaussianBlur(3))
        mask.save(mask_image_fn)

        llm.outpainting(
            prompt=prompt,
            image_fn=original_image_fn,
            mask_fn=mask_image_fn,
            output_fn=inpainted_image_fn,
        )

        return inpainted_image_fn


    def options(self, chapter, image_xml, src, tab):
        return """
        <wa-button
            onClick="chooseRegion(this,event)"
            id="choose_region_button"
            variant="neutral"
            name="button">Choose Region
        </wa-button>

        <div>
            <label for="inpaint_prompt">Inpainting Prompt:</label>
            <input type="text" id="inpaint_prompt" name="inpaint_prompt" placeholder="Describe the region to inpaint"></input>
        </div>

        <wa-button
            hx-post="selector/apply_inpaint_region"
            hx-vals='js:{"region": getRegionDimensions()}'
            hx-target="#strip-centerpiece"
            hx-swap="outerHTML transition:true"
            hx-trigger="click"
            name="button">Apply Inpainting
        </wa-button>"""

registry.register("scale", Scale)
registry.register("horizontal_flip", HorizontalFlip)
registry.register("outpaint", Outpaint)
registry.register("crop_borders", CropBorders)
registry.register("inpaint_region", InpaintRegion)