# we're going to fade out the old image and fade in the new one
# half the given duration for each.

import logger
from transitions import Transition, registry

log = logger.log(__name__)


class Cut(Transition):
    """Cut transition between images."""
    cosmetic = "Cut"
    key = "cut"
    
    def get_configuration_widgets(self):
        """
        No configuration needed.
        """
        widget = ""       
        return widget

    def apply(self, old_image, new_image, frame_directory, config_dict):
        """Apply the transition effect between old_image and new_image.

        old_image and new_image are paths to png files.
        
        we place the frames in the frame directory.  They need to be sortable by
        filename.
        """
        # yeah, for a cut we don't actually have to do anything.  It is the default behavior.
        return 


registry.add_transition(Cut)
