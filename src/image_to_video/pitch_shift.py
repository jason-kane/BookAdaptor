# we're going to fade out the old image and fade in the new one
# half the given duration for each.
import os
import json
import parselmouth

from . import logger
from neobreaker import tools, const
from audio_effects import AudioEffect, registry

log = logger.log(__name__)


class PitchShift(AudioEffect):
    """
    Pitch shift a wav file by the given number of octaves.
    """
    cosmetic = "Pitch Shift"
    key = "pitch_shift"

    def get_configuration_keys(self):
        """
        Return a list of configuration keys that this effect uses.
        These keys will be passed to apply() in effect_config_dict.
        """
        return ["octaves"]

    def get_configuration_widgets(self, chapterurl, character_name, effect_config_dict=None):
        """
        These are individually responseive for submitting themselves on-change.
        """
        octaves = effect_config_dict.get("octaves", 0.0) if effect_config_dict else 0.0
        widget = f"""<div class="wa-stack">
            <wa-slider
                label="Pitch Shift (multiply by)"
                name="octaves"
                min="0"
                max="4"
                step="0.1"
                value="{octaves}"
                hx-put="/{chapterurl}/characters/{character_name}/effect/{self.key}"
                hx-vals='{{"character_name": "{character_name}"}}'
                hx-target="#{character_name}_effects"
                hx-include="this"
                hx-swap="outerHTML"
                with-tooltip
                with-markers
                >{octaves}</wa-slider>
            </div>"""
        return widget

    def apply(self, effect_config_dict, input_wav_filename, output_wav_filename):
        """Apply the pitch shift effect described by the configuration parameters.

        The result is placed in output_wav_filename.  If it already exists it will be overwritten.
        """
        factor = float(
            effect_config_dict.get("octaves", 1.0)
        )

        sound = parselmouth.Sound(input_wav_filename)
        
        manipulation = parselmouth.praat.call(
            sound, "To Manipulation", 0.01, 75, 600
        )

        pitch_tier = parselmouth.praat.call(manipulation, "Extract pitch tier")

        parselmouth.praat.call(
            pitch_tier, "Multiply frequencies", sound.xmin, sound.xmax, factor
        )

        parselmouth.praat.call([pitch_tier, manipulation], "Replace pitch tier")
        modified_sound = parselmouth.praat.call(manipulation, "Get resynthesis (overlap-add)")
        modified_sound.save(output_wav_filename, "WAV")

        log.info(f"PitchShift: {input_wav_filename} -> {output_wav_filename} by {factor} octaves")
        return


registry.add_effect(PitchShift)
