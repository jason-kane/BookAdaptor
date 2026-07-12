
class Transition:
    """Base class for image transitions."""
    cosmetic = "Base"
    key = "base"

    def __init__(self):
        pass
        # self.transition_duration = transition_duration

    # def to_dict(self):
    #     return {
    #         "transition_type": self.transition_type,
    #         "transition_duration": self.transition_duration,
    #     }

    # @classmethod
    # def from_dict(cls, data):
    #     return cls(
    #         transition_type=data.get("transition_type", ""),
    #         transition_duration=data.get("transition_duration", 500),
    #     )
    
    def apply(self, old_image, new_image, frame_directory, config_dict):
        """Apply the transition effect between old_image and new_image.

        old_image and new_image are paths to png files.
        place the frames in the frame directory.  They need to be sortable by filename.
        
        config_dict is a dictionary of any configuration values.
        """
        raise NotImplementedError("Subclasses must implement this method.")
    
    def get_configuration_widgets(self):
        """
        Return an inject block of html of configuration widgets for this transition.
        
        the transition apply() will get a config_dict with {name: value} from these widgets,       
        """
        return []