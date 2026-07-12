import logger
import const

from flask import url_for

log = logger.log(__name__)


class Camera:
    """Base class for camera motion."""
    cosmetic_name= "Base"
    name = "base"
    description = "Base camera motion class, should not be used directly."

    def __init__(self):
        pass
    
    def apply(self, chapter, image_xml, frame_directory, frame_count):
        """Apply the transition effect described by the "animate_<key>" attributes of image_xml.

        places the frames in frame_directory.  They need to be sortable by filename.
        
        Our inputs:
            the src= portion of image_xml
            a directory to put images
            frame count

        Each image will be the input image, for that frame, to the animation phase.

        How many frames?  Hell of a question buddy; we will tell you, but you can also do your best and we can freeze at the end.

        Or that's the final image if there is no animation phase.
        """
        raise NotImplementedError("Subclasses must implement this method.")
    
    def get_configuration_widgets(self, chapter, image_xml, effect_config_list: list):
        """
        Return an inject block of html of configuration widgets for this effect.

        the effect apply() will get a config_dict with {name: value} from these widgets
        """
        # TODO
        # we have effect_config_dict to guide our efforts.  This will evolve as
        # feature requirements become clearer.
        paragraphdir = chapter.get_paragraphdir(image_xml.find_parent('paragraph').attrs.get('id', ''))
        aspect = chapter.get_aspect()
        
        out = f"{self.cosmetic_name} Configuration:<br/>"
        for effect_dict in effect_config_list:
            log.info(f"effect_dict_name: {effect_dict['name']}")
            log.info(f"effect_dict: {effect_dict}")

            if effect_dict["widget"] == "slider":
                if effect_dict.get('range', False):
                    log.info(f"Rendering range slider for {effect_dict['name']}")
                    out += f"""
                    <wa-slider
                        hx-post="camera/set/{self.name}/{effect_dict['name']}/minmax"
                        hx-vals='js:{{"{effect_dict["name"]}_min": this.minValue, "{effect_dict["name"]}_max": this.maxValue}}'
                        label="{effect_dict['label']}"
                        name="{effect_dict['name']}"
                        min-value="{effect_dict["min-value"]}"
                        max-value="{effect_dict["max-value"]}"
                        min="{effect_dict['minimum']}"
                        max="{effect_dict['maximum']}"
                        step="{effect_dict.get('step', 1)}"
                        hx-trigger="change delay:500ms"
                        indicator-offset="0"
                        with-markers
                        with-tooltip
                        range
                        >
                    </wa-slider>
                    """
                else:
                    out += f"""
                    <wa-slider
                        hx-post="camera/set/{self.name}/{effect_dict['name']}/value"
                        label="{effect_dict['label']}"
                        name="{effect_dict['name']}"
                        value="{effect_dict["value"]}"
                        min="{effect_dict['minimum']}"
                        max="{effect_dict['maximum']}"
                        step="{effect_dict.get('step', 1)}"
                        hx-trigger="change delay:500ms"
                        indicator-offset="0"
                        with-markers
                        with-tooltip
                        >
                    </wa-slider>
                    """                    

            elif effect_dict["widget"] == "pixel_chooser":
                image_frame = ""
                try:
                    x_coord, y_coord = [int(x) for x in effect_dict['value']]
                except TypeError:
                    if effect_dict.get('default_center', False):
                        if image_xml.attrs.get("fullscreen", "false").lower() == "true":
                            # portrait/widescreen?
                            x_coord = const.HSIZE // 2
                            y_coord = const.VSIZE // 2
                        else:
                            x_coord = const.IMG_TARGET_WIDTH // 2
                            y_coord = const.IMG_TARGET_HEIGHT // 2
                    else:
                        x_coord, y_coord = None, None
                
                if x_coord and y_coord:
                    # center of the rectangle
                   
                    if image_xml.attrs.get("fullscreen", "false").lower() == "true":
                        width = const.GEOMETRY[aspect]['HSIZE'] // 10
                        height = const.GEOMETRY[aspect]['VSIZE'] // 10
                    else:
                        width = 50
                        height = 50

                    top = y_coord - height // 2
                    left = x_coord - width // 2

                    image_frame = f"""<div
                        style="pointer-events: none; position: absolute; top: {top}px; left:{left}px; width: {width}px; height: {height}px; border: 2px solid yellow; background-color: transparent;"
                    > </div>"""
                else:
                    log.info(f"No {self.name}_x1 or {self.name}_y1 in image_xml.attrs")

                crosshair = """                
                <div style="pointer-events: none; position: absolute; top: 0; left: 50%; width: 1px; height: 200px; background-color: #ff000096;"></div>
                <div style="pointer-events: none; position: absolute; top: 50%; width: 100%; height: 1px; background-color: #ff000096;"></div>
                """

                # /{chapterurl}/images/{image_xml['index']}
                image_update_url = url_for(
                    'library.book.chapter.images.update',
                    **chapter.kwargs,
                    image_index=image_xml.attrs['index']
                )

                # /{paragraphdir}/images/{image_xml['src']}
                image_src_url = url_for(
                    'library.book.chapter.images.show_image_by_index',
                    **chapter.kwargs,
                    height=256,
                    image_index=image_xml.attrs['index']
                )

                # /{self.name}/{effect_dict['name']}/set_pixel
                out += f"""
                <div class="wa-stack">
                    <div class="wa-cluster">
                        <div style="position: relative; display: inline-block;">
                            <img 
                                hx-vals='js:{{respond_with: "camera", camera_{self.name}_x: get_pixel("x", event), camera_{self.name}_y: get_pixel("y", event)}}'
                                hx-put="{image_update_url}"
                                hx-target="#camera-panel"
                                hx-trigger="click"
                                src="{image_src_url}" 
                                style="max-width:200px;max-height:200px;"
                                class="interactive-image"></img>
                            {crosshair}
                            {image_frame}
                        </div>
                        <div class="label-on-left">
                            <label for="{self.name}_x">X:</label>
                            <input type="number" id="{self.name}_x" name="{self.name}_x" value="{x_coord}" />
                            <label for="{self.name}_y">Y:</label>
                            <input type="number" id="{self.name}_y" name="{self.name}_y" value="{y_coord}" />
                        </div>
                    </div>
                </div>
"""

        return out
