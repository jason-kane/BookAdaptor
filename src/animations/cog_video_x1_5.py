# we're going to fade out the old image and fade in the new one
# half the given duration for each.
import os
import json

import logger
import const
from artifact_editor import tools
from animations import Animation, registry

log = logger.log(__name__)


class CogVideoX1_5(Animation):
    """CogVideo X1.5 test of animation feature."""
    cosmetic = "CogVideo"
    key = "cog_video_x1_5"
    
    def get_configuration_widgets(self, chapter, image_xml, video_index):
        """
        These are individually responseive for submitting themselves on-change.
        """
        widget = """<wa-textarea
                label="Prompt"
                name="prompt"
                value="This is a test of the alpha animation feature."></wa-textarea>"""       
        return widget

    def apply(self, chapter, image_xml, frame_directory):
        """Apply the transition effect described by the "animate_<key>" attributes of image_xml.

        places the frames in frame_directory.  They need to be sortable by filename.
        """
        # which engine for alpha?  In any case, we block until it is done.
        # def animate_image(image_pfn, prompt, animate_frame_dir, image_tag):
        os.makedirs(frame_directory, exist_ok=True)
        done_flag_fn = os.path.join(frame_directory, 'done.flag')
        
        if os.path.exists(done_flag_fn):
            os.unlink(done_flag_fn)

        paragraph = image_xml.find_parent('paragraph')
        
        image_pfn = os.path.join(
            const.LIBRARY_DIR,
            paragraph.attrs.get('dir', ''),
            image_xml.attrs.get('src', None)
        )

        with open('drawing.fifo', 'a') as fifo:
            fifo.write(
                json.dumps([
                    'animate',
                    image_pfn,
                    image_xml.attrs.get('prompt', ''),
                    frame_directory,
                    f"{image_xml['index']:06}",  # per image unique string
                    done_flag_fn
                ]) + "\n\n"
            )
    
        tools.wait_for(done_flag_fn)
        os.unlink(done_flag_fn)

        return 


registry.add_module(CogVideoX1_5)
