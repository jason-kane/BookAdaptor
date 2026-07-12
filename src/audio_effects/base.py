
class AudioEffect:
    """Base class for audio effects."""
    cosmetic = "Base"
    key = "base"

    def __init__(self):
        pass
    
    def apply(self, effect_config_dict, input_wav_filename, output_wav_filename):
        """
        Apply the audio effect to input_wav_filename according to
        the configuration parameters provided in effect_config_dict.

        The result is placed in output_wav_filename.  If it already exists it will be overwritten.
        """
        raise NotImplementedError("Subclasses must implement this method.")
    
    def get_configuration_keys(self):
        """
        Return a list of configuration keys that this effect uses.
        These keys will be passed to apply() in effect_config_dict.
        """
        return []

    def get_configuration_widgets(self, chapterurl, character_name, effect_config_dict=None):
        """
        Return an inject block of html of configuration widgets for this effect.

        the effect apply() will get a config_dict with {name: value} from these widgets,
        """
        return ""