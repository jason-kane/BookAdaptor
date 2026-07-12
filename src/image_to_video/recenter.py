
import os
import json
import parselmouth

from . import logger
from neobreaker import tools, const
from image_to_video import ImageToVideo, registry

log = logger.log(__name__)


class Recenter(ImageToVideo):
    cosmetic_name = "Recenter"
    name = "recenter"
    description = "Recenter the image on the selected point."

    values = {}

    def __init__(self, image_file, **kwargs):
        super().__init__(
            image_file=image_file,
            **kwargs
        )
        
    def apply(self, in_image, values):
        """
        x1 and y1 are percent of image size, not pixels.
        """
        width, height = in_image.size
        
        # where in this width x height image are we centering?
        x1 = int(values["x1"] / 100 * width)
        y1 = int(values["y1"] / 100 * height)

        log.info(f'Recentering image around {x1},{y1}')

        # we need to know if we're fullscreen or not, otherwise we don't know
        # what aspect ratio to target.
        if values.get("fullscreen", False):
            target_aspect_ratio = const.FULLSCREEN_ASPECT_RATIO
        else:
            target_aspect_ratio = const.STANDARD_ASPECT_RATIO

        crop_width = min(x1, width - x1) * 2
        crop_height = min(y1, height - y1) * 2

        # which is our limiting factor, width or height?
        if (crop_width / crop_height) > target_aspect_ratio:
            # width is the limiting factor, we need to shrink it
            crop_width = int(crop_height * target_aspect_ratio)
        else:
            # height is the limiting factor, we need to shrink it
            crop_height = int(crop_width / target_aspect_ratio)

        # crop from in_image to a target_aspect_ratio rectangle centered on (x1, y1)
        cleft, cupper, cright, clower = (
            x1 - crop_width // 2,
            y1 - crop_height // 2,
            x1 + crop_width // 2,
            y1 + crop_height // 2
        )
        log.info(f'Cropping to {cleft},{cupper},{cright},{clower}')
        log.info(f'%s {cleft=} is >= 0' % (cleft >= 0))
        log.info(f'%s {cupper=} is >= 0' % (cupper >= 0))
        log.info(f'%s {cright=} is <= {width}' % (cright <= width))
        log.info(f'%s {clower=} is <= {height}' % (clower <= height))

        out_image = in_image.crop((
            cleft, cupper,
            cright, clower
        ))

        return out_image

    def get_configuration_data(self, image_xml):
        """
        Return a list of dictionaries.  Each dictionary describes a parameter
        supported by this widget.        
        """
        # definition driven config, you describe what you want here and it gets built.
        return [{
            "name": "xy",
            "widget": "pixel_chooser",
            "value": (image_xml.attrs.get(f"{self.name}_x1", None), image_xml.attrs.get(f"{self.name}_y1", None)),
        }, {
            "name": "frames",
            "label": "Frames",
            "widget": "slider",
            "value": image_xml.attrs.get(f"{self.name}_frames", 1),
            "minimum": 0,
            "maximum": 100
        }]


        # return [{
        #     "name": "x1",
        #     "label": "X1",
        #     "widget": "slider",
        #     "value": self.values["x1"],
        #     "minimum": 0,
        #     "maximum": 100
        # }, {
        #     "name": "y1",
        #     "label": "Y1",
        #     "widget": "slider",
        #     "value": self.values["y1"],
        #     "minimum": 0,
        #     "maximum": 100
        # },  {
        #     "name": "frames",
        #     "label": "Frames",
        #     "widget": "slider",
        #     "value": self.values["frames"],
        #     "minimum": 0,
        #     "maximum": 100
        # }]        

registry.add(Recenter)

