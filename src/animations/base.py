
class Animation:
    """Base class for image animations."""
    cosmetic = "Base"
    key = "base"

    def __init__(self):
        pass
    
    def apply(self, chapter, image_xml, frame_directory):
        """Apply the transition effect described by the "animate_<key>" attributes of image_xml.

        places the frames in frame_directory.  They need to be sortable by filename.
        """
        raise NotImplementedError("Subclasses must implement this method.")
    
    def get_configuration_widgets(self, chapter, image_xml, video_index):
        """
        Return an inject block of html of configuration widgets for this animation.

        the animation apply() will get a config_dict with {name: value} from these widgets,
        """
        return ""