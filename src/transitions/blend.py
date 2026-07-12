# we're going to fade out the old image and fade in the new one
# half the given duration for each.
import os
import math
from PIL import Image

import logger
import const
from transitions import Transition, registry

log = logger.log(__name__)


class Blend(Transition):
    """Blend transition between images."""
    cosmetic = "Blend"
    key = "blend"
    
    def get_configuration_widgets(self):
        log.info('Triggerend get_configuration_widgets for Blend')

        widget = """<wa-slider
                label="Transition Duration (ms)"
                name="duration"
                value="500"
                min="100"
                max="2000"></wa-slider>"""
        
        log.info(f"widget: {widget}")
        return widget

    def apply(self, old_image, new_image, frame_directory, config_dict):
        """Apply the transition effect between old_image and new_image.

        old_image and new_image are paths to png files.
        
        we place the frames in the frame directory.  They need to be sortable by
        filename.
        
        config_dict is a dictionary of any configuration values.  these values
        must be request in get_configuration-widgets.
        """
        log.info(f'Apply blend transition to {old_image} -> {new_image}')
        duration_ms = config_dict.get('duration', 500)
        num_frames = math.ceil(const.FPS * (duration_ms / 1000.0))

        # if os.path.exists(old_image + ".adj.png"):
        #     old_image = old_image + ".adj.png"
        
        # if os.path.exists(new_image + ".adj.png"):
        #     new_image = new_image + ".adj.png"

        old_image_obj = Image.open(old_image)
        new_image_obj = Image.open(new_image)

        if old_image_obj.size != new_image_obj.size:
            log.error(f"Image sizes do not match for blending: {old_image_obj.size} vs {new_image_obj.size}")
            if old_image_obj.size != (1080, 1080):
                log.info(f"Resizing old image from {old_image_obj.size} to (1080, 1080)")
                old_image_obj = old_image_obj.resize((1080, 1080))
            elif new_image_obj.size != (1080, 1080):
                log.info(f"Resizing new image from {new_image_obj.size} to (1080, 1080)")
                new_image_obj = new_image_obj.resize((1080, 1080))

        for frame in range(num_frames):
            alpha = frame / num_frames
            try:
                blended = Image.blend(
                    old_image_obj,
                    new_image_obj,
                    alpha
                )
            except ValueError as ve:
                log.error(f"Error blending images at frame {frame} with alpha {alpha}: {ve}")
                log.error(f'old: {old_image_obj} ({old_image_obj.size})')
                log.error(f'new: {new_image_obj} ({new_image_obj.size})')
                raise ve
            except OSError as ose:
                log.error(f"OSError blending images at frame {frame} with alpha {alpha}: {ose}")
                log.error(f'old: {old_image_obj} ({old_image_obj.size})')
                log.error(f'new: {new_image_obj} ({new_image_obj.size})')
                raise ose
            
            blended.save(
                os.path.join(
                    frame_directory, 
                    f"frame_{frame:04d}.png"
                )
            )
            log.info(f"Saved blended frame [{alpha}]: frame_{frame:04d}.png")

        return 


registry.add_transition(Blend)
