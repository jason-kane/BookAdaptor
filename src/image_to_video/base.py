
import logging
log = logging.getLogger(__name__)


class ImageToVideo:
    name = "base"
    cosmetic_name = "Base ImageToVideo Object"
    description = "InfoTip Description"

    def __init__(self, image_file, paragraphdir=None):
        self.image_file = image_file
        self.paragraphdir = paragraphdir

    def apply(self, effect_config_dict, output_directory):
        """
        Apply the video effect with:
            self.image_file as the filename of the initial input.  You can
            Image.open() it directly.

        effect_config_dict: a dictionary of configuration parameters for this
        effect.

        a previous call to self.get_configuration_widgets() provided the
        pre-populated widgets that will POST with names matching all available
        expected keys here in effect_config_dict.

        The result(s) are placed in output_directory.
        """
        raise NotImplementedError("Subclasses must implement this method.")
    
    def get_configuration_data(self, image_xml):
        """
        Return a list of dictionaries.  Each dictionary describes a parameter
        supported by this widget.        
        """
        # definition driven config, you describe what you want here and it gets built.
        return []

    def get_configuration_widgets(self, image_xml, effect_config_dict=None):
        """
        Return an inject block of html of configuration widgets for this effect.

        the effect apply() will get a config_dict with {name: value} from these widgets,
        """
        # we have effect_config_dict to guide our efforts.  This will evolve as
        # feature requirements become clearer.
        out = f"{self.cosmetic_name} Configuration:<br/>"
        for effect_dict in effect_config_dict.get("widgets", []):
            log.info(f"effect_dict_name: {effect_dict['name']}")
            log.info(f"effect_dict: {effect_dict}")
            log.info(f"effect_config_dict: {effect_config_dict}")

            if effect_dict["widget"] == "slider":
                out += f"""
                <wa-slider
                    label="{effect_dict['label']}"
                    name="{effect_dict['name']}"
                    value="{effect_dict['value']}"
                    min="{effect_dict['minimum']}"
                    max="{effect_dict['maximum']}"
                    indicator-offset="0"
                    with-markers
                    with-tooltip
                    >
                </wa-slider>
                """

            elif effect_dict["widget"] == "pixel_chooser":
                crosshair = ""
                if image_xml.attrs.get(f"{self.name}_x1") and image_xml.attrs.get(f"{self.name}_y1"):
                    x_coord = int(image_xml.attrs.get(f"{self.name}_x1"))
                    y_coord = int(image_xml.attrs.get(f"{self.name}_y1"))
                    crosshair = f"""
                    <div class="interactive-image crosshair"
                        style="--x-coord: { x_coord }px; --y-coord: { y_coord }px;"
                    ></div>"""

                out = f"""
                <div class="wa-stack">
                    <div class="wa-cluster">
                        {crosshair}
                        <img 
                            hx-vals='js:{{pos:get_pixel(event)}}'
                            hx-post="/{self.paragraphdir}/images/{image_xml['index']}/image_to_video/{self.name}/set_pixel"
                            src="/{self.paragraphdir}/images/{image_xml['src']}" 
                            style="max-width:200px;max-height:200px;"
                            class="interactive-image"></img>
                    </div>
                </div>
"""

        return out

    def get_button(self, image_xml):
        """
        This is what triggers our modal.
        """
        return f"""<wa-button
            hx-post="/{self.paragraphdir}/images/{image_xml['index']}/image_to_video/{self.name}"
            hx-vals='{{"image_file": "{self.image_file}", "option": "{self.name}"}}'
            hx-swap="innerHTML"
            hx-trigger="click"
            hx-target="#model_container"
            name="{self.name}"
            variant="secondary"
            size="medium"
            tooltip="{self.description}">
            {self.cosmetic_name}
        </wa-button>
        """

    def get_modal(self, image_xml):
        """
        This is the configuration modal that pops up when the button is clicked.
        """
        out = f"""
        <wa-dialog 
            id="{self.name}" 
            label="Dialog" 
            light-dismiss
            class="dialog-footer dialog-scrolling dialog-light-dismiss">
        """

        config_data = self.get_configuration_data(image_xml)
        log.info(f"config_data: {config_data}")
        
        out += self.get_configuration_widgets(
            image_xml=image_xml,
            effect_config_dict={
                "widgets": config_data
            }
        )
        # get_configuration_widgets(self, chapterurl, character_name, effect_config_dict=None)

        out += f"""
        <wa-button slot="footer" variant="brand" data-dialog="close">Close</wa-button>
    </wa-dialog>
    <script>
        var dialog = document.querySelector('#{self.name}');
        dialog.open = true;
    </script>
        """
        return out