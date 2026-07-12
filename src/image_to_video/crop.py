
import os
import json
import parselmouth

from . import logger
from neobreaker import tools, const
from image_to_video import ImageToVideo, registry

log = logger.log(__name__)


class Crop(ImageToVideo):
    cosmetic_name = "Crop"
    name = "crop"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
    def apply(self, image):
        return image.crop((self.x1, self.y1, self.x2, self.y2))
    
    def get_configuration_data(self, image_xml):
        """
        Return a list of configuration keys that this effect uses.
        These keys will be passed to apply() in effect_config_dict.
        """
        
        # definition driven config, you describe what you want here and it gets built.

        return []

registry.add(Crop)