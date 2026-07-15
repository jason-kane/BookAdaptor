import html
import os

from flask import request, url_for
from torch import div

import logger
from artifact_editor.characters import characters
from artifact_editor.styles import htmx as styles_htmx
from artifact_editor.tools import generic_button

from . import const as scene_const

log = logger.log(__name__)


def image_scene_workshop(
    chapter,
    image_xml,
):
    # use the image style if there is one.
    style = image_xml.attrs.get("style")
    if not style:
        # otherwise, use the chapter style if there is one.
        style = chapter.get_chapter_style()
        if not style:
            # otherwise, use the book style if there is one.
            style = chapter.config.get("default_style")
            if not style:
                log.warning("No style found for image, chapter, or book.")
            else:
                log.info('Using book style: %s', style)
        else:
            log.info('Using chapter style: %s', style)
    else:
        log.info('Using image style: %s', style)

    image_index = image_xml.attrs["index"]
    style_ui = styles_htmx.style_selector(
        selected_style=style,
        url="set_image_style"
    )
    description_ui = description(chapter, image_xml)
    setting_ui = setting(chapter, image_xml)
    tod_ui = tod(chapter, image_xml)
    mood_ui = mood(chapter, image_xml)
    camera_ui = camera_direction(chapter, image_xml)
    lighting_ui = camera_lighting(chapter, image_xml)
    focus_character_ui = focus_character(chapter, image_xml)
    characters_section_ui = characters_section(
        chapter, image_xml
    )

    # @bp.route("/<author>/<path:title>/<chapter>/images/<int:image_index>/actions/generate_meta", methods=["POST"]) 
    generate_meta_button = generic_button(
        os.path.join(chapter.url, "images", str(image_index)),
        # ", "actions", "generate_meta"),
        category=None,
        tag="generate_meta",
        cosmetic="Generate Meta from Text",
        target="#strip-centerpiece",
    )

    return f"""
    <div class="wa-stack wa-gap-lg">
        {style_ui}
        {description_ui}
        {setting_ui}
        {tod_ui}
        {mood_ui}
        {camera_ui}
        {lighting_ui}
        {focus_character_ui}
        {characters_section_ui}
        {generate_meta_button}
    </div>
    """


def datastack(chapter, image_xml):
    """
    returns a a simplified read-only presentation of the provided scene metadata
    regarding the indicated image.

    This is so the user can compare the previous and next days metadta to the
    one they are working on.  Think disabled.  Should come naturally.
    """
    if image_xml is None:
        return "<div>No image data available.</div>" 
    
    setting = image_xml.attrs.get("setting", "")
    tod = image_xml.attrs.get("tod", "")
    camera = image_xml.attrs.get("camera", "")
    focus_character = image_xml.attrs.get("focus_character", "")
    scene_characters = image_xml.attrs.get("scene_characters", "")
    mood = image_xml.attrs.get("mood", "")
    lighting = image_xml.attrs.get("lighting", "")

    character_descriptions = []
    for character_name in scene_characters.split(","):
        character_name = character_name.strip()
        character_descriptions.append(
            f"<div><strong>{character_name}:</strong> {image_xml.attrs.get(f'{character_name}_description', '')}</div>"
        )

    out = f"""
    <div class="wa-stack wa-gap-sm">
        <div><strong>Setting:</strong> {html.escape(setting)}</div>
        <div><strong>Time of Day:</strong> {html.escape(tod)}</div>
        <div><strong>Mood:</strong> {html.escape(mood)}</div>
        <div><strong>Camera Angle:</strong> {html.escape(camera)}</div>
        <div><strong>Lighting:</strong> {html.escape(lighting)}</div>
        <div><strong>Focus Character:</strong> {html.escape(focus_character)}</div>
        <div><strong>Scene Characters:</strong> {html.escape(scene_characters)}</div>
        <hr/>
        {'\n'.join(character_descriptions)}
    </div>
    """
    return out


def description(chapter, image_xml):
    desc = html.escape(image_xml.attrs.get("description", ""))
    
    update_description_url = url_for(
        "library.book.chapter.images.scene.update_description",
        **chapter.kwargs,
        image_index=image_xml.attrs["index"],
    )

    return f"""<wa-textarea 
        label="Description"
        name="description"
        id="{image_xml.attrs['index']}_description"
        value="{desc}"
        hx-post="{update_description_url}"
        hx-trigger="change delay:500ms"
        hx-swap="none"
        rows="{len(desc) // 60}"
        size="large"
        style="width: 100%"
    ></wa-textarea>
    """


def setting(chapter, image_xml):
    setting = html.escape(image_xml.attrs.get("setting", ""))

    recent_settings = ""
    count = 0
    total_previous_settings = 6
    previous_setting = setting

    border_plain = "3px solid" + "#494949"
    border_hover = "3px solid" + "#C9C25B"

    previous_image = image_xml.find_previous("image")
    log.info("Iterating previous images for alternative settings")
    while count < total_previous_settings and previous_image is not None:
        log.info("Found previous image", previous_image=previous_image)
        if previous_image.attrs.get("setting", "") != previous_setting:
            count += 1       

            if "src" in previous_image.attrs:
                img_src = url_for(
                    'library.book.chapter.images.show_image_by_index',
                    author=chapter.author.name,
                    title=chapter.title,
                    chapter_number=chapter.number,
                    language=chapter.language,
                    height=127,
                    image_index=previous_image.attrs["index"]
                )
            else:
                img_src = "/static/images/x.png"
    
            recent_settings += f"""<img 
                style="border: {border_plain}; margin: 2px; cursor: pointer;"
                onmouseover="this.style.border='{border_hover}';"
                onmouseout="this.style.border='{border_plain}';"
                hx-put="scene/previous_setting?previous_index={previous_image["index"]}"
                hx-target="#setting-textarea"
                hx-swap="outerHTML transition:true"
                height='127px' width='127px' src='{img_src}'>
            </img>"""

        previous_image = previous_image.find_previous("image")

    return f"""
        <div class="wa-flank" style="position: relative;" id="setting-textarea">
            <div class="wa-stack">
                <wa-textarea 
                    label="Setting" 
                    name="setting" 
                    hx-put="scene/save_setting"
                    hx-target="#setting-textarea"
                    hx-swap="outerHTML transition:true"
                    hx-trigger="change"
                    cols=70
                    rows=3 
                    value="{setting}"></wa-textarea>

                <wa-button
                    style="align-self: flex-end; margin-left: 1em;"
                    hx-post="scene/generate_setting"
                    hx-target="#setting-textarea"
                    hx-swap="outerHTML transition:true">
                    Generate Setting
                </wa-button>
            </div>
            
            <div class="wa-stack">
                <div class="wa-grid wa-align-items-start" style="--min-column-size: 130px;">
                    <div style="position: absolute;top: 0px;color: var(--wa-form-control-label-color);font-weight: var(--wa-form-control-label-font-weight);line-height: var(--wa-form-control-label-line-height);">Previous settings:</div>
                    {recent_settings}
                </div>
            </div>
        </div>"""


def tod(chapter, image_xml):
    tod = image_xml.attrs.get("tod", "")

    out = f"""<div class="wa-cluster" style="width: 100%" id="tod">
        <wa-select
            value="{tod}" 
            hx-put="scene/tod"
            hx-target="#tod"
            hx-swap="outerHTML transition:true"
            hx-trigger="change"
            name="tod"
            id="tod-select" 
            label="Time of day">"""
    
    for tod_choice in scene_const.TIME_OF_DAY_CHOICES:
        out += f'<wa-option value="{tod_choice[0]}">{tod_choice[1]}</wa-option>'
        
    out += "</wa-select>"

    out += """<wa-button
        pill
        style="align-self: flex-end; margin-left: 1em;"
        hx-post="scene/tod"
        hx-target="#tod"
        hx-swap="outerHTML transition:true">
        Generate
    </wa-button>"""
    
    out += "</div>"
    return out


def mood(chapter, image_xml):
    # we're going to give a grid of emojis to snapshot the feeling of the scene.
    # The plan is to enable adding these emojis at any point in the timeline
    # (ie: inside <prompt/>).  We can use these to trigger things like an emotional change in the background music.  Once I figure the tech, a full library of sound effects.
    #
    # <mood/>
    # <effect/>
    #
    # placed at the word in the text when it should occur.
    # we whisper the wav, with the exact text to get sub-second timing for each word.  This is how we know exactly when to trigger the requestedd mood/effect changes, and we can re-calculate it whenever necessary, but it should only be necessary when the text actually changes, so it is legit.

    # ui... make it easy to use a resource like freesound.org
    # to find the perfect sound effect and download the sound.
    # I don't want to rape the site, that's just so rude.
    # I'm fine with the "choose sound" having a prominent link to freesound.org.  Let the user take it from there, that is legit.
    # 
    # I don't want to point anything at the download directory.  that feels bad-juju, but we the file dialog will put that one click away.
    # whatever is added, is copied in.

    # Timeline View, upgraded to "Timeline" stage between audio and images.
    # Audio player.
    # Set Mood button opens a grip of emojis.  Pick the emoji and place it in the text.  It is now visible (everywhere) and with give provide full name on hover.

    # anything that wants to be sensitive to mood can.
    # fast, efficient function call to get the mood for any frame.

    # Set Effect - Choose from <global> or an interactive hot-filter sort of searchable list of tags.  Users can add/remove tags as they see fit.  It must be easy to add an existing tag.  Only enforced bits are <global> and <recent>.

    # <global>
    # every sound effect, in some sort of nice card.  playable.
    # paginated.
    #
    # <recent>  this is the default view
    # same, but the most recently used effects, for easy repeat access.
    #
    # <tag>
    # all effects with that tag.  searchable via hot inclusion filter.
    # eventually:  Add button.
    #
    # Which gives the user something they can drag around inside the phrase.
    
    # Audio Effects CAN block.  Up to you.  Checkbox.  Block in this case meaning it is serialized with the voice track.  So you won't have to worry about effects making voices hard to understand because they won't overlap, you get talk, talk, talk, effect, talk, ... But .. overlap sounds more natural.
    return f"""<div>
    <p>Mood setting coming soon.  This will be a grid of emojis that you can click to add to the prompt, and then use in your music/sfx generation to create a more emotionally responsive soundtrack.</p>
    </div>"""


def camera_lighting(chapter, image_xml):
    out = '<div class="wa-cluster">'

    for aspect in ("direction", "source", "quality"):
        aspect_value = image_xml.attrs.get(f"lighting_{aspect}", "")
        out += f"""<wa-select
            value="{aspect_value}" 
            hx-put="scene/lighting_{aspect}"
            hx-swap="none"
            hx-trigger="change"
            name="lighting_{aspect}" 
            id="lighting_{aspect}-select" 
            label="Lighting {aspect.capitalize()}">"""
        
        choices = getattr(scene_const, f"LIGHTING_{aspect.upper()}_CHOICES", [])

        for tag, cosmetic in choices:
            out += f'<wa-option value="{tag}">{cosmetic}</wa-option>'
        
        out += "</wa-select>"

    out += """<wa-button
        pill
        style="align-self: flex-end; margin-left: 1em;"
        hx-post="scene/lighting"
        hx-target="#camera"
        hx-swap="outerHTML transition:true">
        Generate
    </wa-button>"""
    out += "</div>"
    return out


def camera_direction(chapter, image_xml):
    camera = image_xml.attrs.get("camera", "")

    camera_options = ""
    for camera_choice in scene_const.CAMERA_CHOICES:
        camera_options += (
            f'<wa-option value="{camera_choice[0]}">{camera_choice[1]}</wa-option>'
        )

    out = '<div class="wa-cluster" style="width: 100%" id="camera">'
    out += f"""<div>
        <wa-select
            value="{camera}" 
            hx-put="scene/camera"
            hx-target="#camera-select"
            hx-swap="outerHTML transition:true"
            hx-trigger="change"
            name="camera" 
            id="camera-select" 
            label="Camera Angle">{camera_options}</wa-select>
    </div>"""

    out += """<wa-button
        pill
        style="align-self: flex-end; margin-left: 1em;"
        hx-post="scene/camera"
        hx-target="#camera"
        hx-swap="outerHTML transition:true">
        Generate
    </wa-button>"""
    out += "</div>"
    return out


def focus_character(chapter, image_xml):
    characters = image_xml.attrs.get("scene_characters", "").split(",")
    character_options = ""
    for character in characters:
        character_options += f'<wa-option value="{character}">{character}</wa-option>'

    set_focus_url = url_for(
        "library.book.chapter.images.scene.set_focus_character",
        **chapter.kwargs,
        image_index=image_xml.attrs["index"],
    )

    if character:
        return f"""<div>
            <wa-select
                hx-post="{set_focus_url}"
                hx-swap="outerHTML transition:true"
                hx-trigger="change"
                value="{image_xml.attrs.get('focus_character', '')}" 
                name="focus_character"
                label="Character focus"
                hint="Select the character that is speaking or should be the focus of the image."
                clearable
            >
            {character_options}
            </wa-select>
        </div>"""
    else:
        return ""


def characters_section(chapter, image_xml):
    scene_characters_ui = scene_characters(
        chapter, image_xml
    )

    character_breakdown_ui = character_breakdown(
        chapter, image_xml
    )

    return f"""
    <div id="characters_section">
        {scene_characters_ui}
        {character_breakdown_ui}
    </div>
    """


def scene_characters(chapter, image_xml):
    character_options = ""
    all_characters = characters.get_all_characters(chapter)

    selected = image_xml.attrs.get("scene_characters", "").split(",")

    for character_tag in all_characters:
        if character_tag:
            character = all_characters[character_tag]
            if character["tag"] in selected:
                character_options += f"<wa-option value=\"{character['tag']}\" selected>{character['name']}</wa-option>\n"
            else:
                character_options += f"<wa-option value=\"{character['tag']}\">{character['name']}</wa-option>\n"

    # value="{' '.join(selected)}"
    return f"""<div class="wa-cluster" id="scene_characters">
        <wa-select             
            name="scene_characters" 
            label="Scene Characters"
            hx-put="scene/characters"
            hx-swap="outerHTML transition:true"
            hx-target="#characters_section"
            hx-trigger="change delay:500ms"
            hint="Choose character(s) to include in this scene."
            with-clear
            multiple
        >
            {character_options}
        </wa-select>

        <wa-button
            pill
            style="align-self: flex-end; margin-left: 1em;"
            hx-post="scene/characters"
            hx-target="#scene_characters"
            hx-swap="outerHTML transition:true"
        >Generate</wa-button>
    </div>"""


def character_breakdown_old(chapter, image_xml):
    out = '<div class="wa-stack wa-gap-xl">'

    for character_name in image_xml.attrs.get("scene_characters", "").split(","):
        if character_name:
            character = characters.get_character(chapter, character_name)

            location = character_location_response(
                chapter,
                character,
                image_xml,
            )

            pose = character_pose_selector(
                chapter,
                character,
                image_xml,
            )

            action = character_action(
                chapter, 
                character,
                image_xml,
            )

            description_value = image_xml.attrs.get(character["tag"] + "_description", "")

            if description_value in [None, ""]:
                # the most recent image with a description of this character
                prior_image = image_xml.find_previous(
                    "image", {f"{character['tag']}_description": True}
                )
                if prior_image:
                    description_value = prior_image.attrs.get(
                        f"{character['tag']}_description", ""
                    )

                image_xml.attrs[f"{character['tag']}_description"] = description_value

            if description_value in [None, ""]:
                # the most recent description of this character
                description_value = characters.get_character(
                    chapter, character["tag"]
                ).get("description", "")

            if description_value is None:
                description_value = ""

            log.info(f"Character {character['tag']} description: {description_value}")

            description = character_description(
                chapter=chapter,
                character=character,
                image_xml=image_xml,                
            )

            # http://localhost:5000/L.%20Frank%20Baum/The%20Marvelous%20Land%20of%20Oz/0001/characters/Tip/headshot.png
            # <wa-avatar
            #             shape="rounded"
            #             image="/{chapterurl}/characters/{character_name}/headshot.png"
            #             label="{character_name} avatar"></wa-avatar>
            headshot_url = url_for(
                "library.book.chapter.characters.headshot",
                **chapter.kwargs,
                character_tag=character["tag"]
            )

            out += f"""
            <div class="wa-stack wa-align-items-start" style="position: relative;">
                <h2>{character_name}</h2>

                <div class="wa-cluster">
                    <img style="border-radius: 8px; float: left; position: absolute; z-index: -1;top: 0px;opacity: 0.25;height:100%;" 
                        src="{headshot_url}"
                        alt="{character_name} headshot"/>
                    
                    <div class="wa-stack wa-gap-3xs">
                        Location 
                        {location}
                        {pose}
                        {action}
                        {description}
                    </div>
                </div>
            </div>
            """

    out += "</div>"
    return out


def character_breakdown(chapter, image_xml):
    character_map = {}
    for character_name in image_xml.attrs.get("scene_characters", "").split(","):
        if character_name:
            character = characters.get_character(chapter, character_name)

            location = character_location_response(
                chapter,
                character,
                image_xml,
            )

            pose = character_pose_selector(
                chapter,
                character,
                image_xml,
            )

            action = character_action(
                chapter, 
                character,
                image_xml,
            )

            description_value = image_xml.attrs.get(character["tag"] + "_description", "")

            if description_value in [None, ""]:
                # the most recent image with a description of this character
                prior_image = image_xml.find_previous(
                    "image", {f"{character['tag']}_description": True}
                )
                if prior_image:
                    description_value = prior_image.attrs.get(
                        f"{character['tag']}_description", ""
                    )

                image_xml.attrs[f"{character['tag']}_description"] = description_value

            if description_value in [None, ""]:
                # the most recent description of this character
                description_value = characters.get_character(
                    chapter, character["tag"]
                ).get("description", "")

            if description_value is None:
                description_value = ""

            log.info(f"Character {character['tag']} description: {description_value}")

            description = character_description(
                chapter=chapter,
                character=character,
                image_xml=image_xml,                
            )

            # http://localhost:5000/L.%20Frank%20Baum/The%20Marvelous%20Land%20of%20Oz/0001/characters/Tip/headshot.png
            # <wa-avatar
            #             shape="rounded"
            #             image="/{chapterurl}/characters/{character_name}/headshot.png"
            #             label="{character_name} avatar"></wa-avatar>
            headshot_url = url_for(
                "library.book.chapter.characters.headshot",
                **chapter.kwargs,
                character_tag=character["tag"]
            )

            character_map[character["tag"]] = {
                "name": character_name,
                "headshot_url": headshot_url,
                "location": location,
                "pose": pose,
                "action": action,
                "description": description
            }

    out = '<wa-tab-group>'
    for character_tag in character_map:
        out += f"""<wa-tab panel="character_{character_tag}_panel">{character_map[character_tag]["name"]}</wa-tab>"""

    for character_tag in character_map:
        character_name = character_map[character_tag]["name"]
        headshot_url = character_map[character_tag]["headshot_url"]
        location = character_map[character_tag]["location"]
        pose = character_map[character_tag]["pose"]
        action = character_map[character_tag]["action"]
        description = character_map[character_tag]["description"]

        out += f"""
        <wa-tab-panel name="character_{character_tag}_panel">
            <div class="wa-stack wa-align-items-start" style="position: relative;">
                <h2>{character_name}</h2>

                <div class="wa-cluster">
                    <img style="border-radius: 8px; float: left; position: absolute; z-index: -1;top: 0px;opacity: 0.25;height:100%;" 
                        src="{headshot_url}"
                        alt="{character_name} headshot"/>
                    
                    <div class="wa-stack wa-gap-3xs">
                        Location 
                        {location}
                        {pose}
                        {action}
                        {description}
                    </div>
                </div>
            </div>
        </wa-tab-panel>
        """

    out += "</wa-tab-group>"
    return out


def character_location_response(chapter, character, image_xml):
    buildup = f"""<div id="character_{character['tag']}_location" class="wa-stack">"""

    current_location = image_xml.attrs.get(f"{character['tag']}_location", "")

    def stage_button(loop_location):
        set_location_url = url_for(
            "library.book.chapter.images.scene.set_character_location",
            **chapter.kwargs,
            image_index=image_xml.attrs["index"],
            character=character["tag"],
        )

        return f"""
        <div>
            <wa-button 
            hx-vals='{{"location": "{loop_location}"}}'
            hx-post="{set_location_url}"
            hx-target="#character_{character['tag']}_location"
            hx-swap="outerHTML transition:true"           
            {"variant=\"success\"" if loop_location == current_location else ""}
            class="loc_{loop_location.lower()}">{loop_location}</wa-button>
        </div>        
    """

    buildup += '<div class="wa-cluster">'
    for loop_location in ["UR", "UC", "UL"]:
        buildup += stage_button(loop_location)
    buildup += "</div>"

    buildup += '<div class="wa-cluster">'
    for loop_location in ["SR", "CS", "SL"]:
        buildup += stage_button(loop_location)
    buildup += "</div>"

    buildup += '<div class="wa-cluster">'
    for loop_location in ["DR", "DC", "DL"]:
        buildup += stage_button(loop_location)
    buildup += "</div>"

    buildup += "</div>"  # the outer stack
    return buildup


def character_pose_selector(chapter, character, image_xml):
    set_pose_url = url_for(
        'library.book.chapter.images.scene.set_character_pose',
        **chapter.kwargs,
        image_index=image_xml.attrs['index']
    )

    generate_pose_url = url_for(
        'library.book.chapter.images.scene.generate_character_pose',
        **chapter.kwargs,
        image_index=image_xml.attrs['index']
    )

    pose = image_xml.attrs.get(f"{character['tag']}_pose", "")

    out = f"""
    <div class="wa-cluster" id="character_{character['tag']}_pose">
        <wa-select 
            label="Pose" 
            value="{pose}"
            hx-vals='{{"character": "{character['tag']}"}}'
            hx-put="{set_pose_url}"
            hx-target="#character_{character['tag']}_pose"
            hx-swap="outerHTML transition:true"
            hx-trigger="change"
            name="pose">"""
    for key, cosmetic in scene_const.POSE_CHOICES:
        out += f"""<wa-option value="{key}">{cosmetic}</wa-option>"""
    out += "    </wa-select>"

    out += f"""<wa-button
        pill
        style="align-self: flex-end; margin-left: 1em;"
        hx-post="{generate_pose_url}"
        hx-vals='{{"character": "{character['tag']}"}}'
        hx-target="#character_{character['tag']}_pose"
        hx-swap="outerHTML transition:true"
        hx-trigger="click"
        name="generate_{character['tag']}_pose"
        variant="secondary"
        hx-on::before-request="beforeRequest(this,event)"
        hx-on::after-request="afterRequest(this,event)"
        Generate Pose
    </wa-button>"""

    out += "</div>"
    return out


def character_description(chapter, character, image_xml):
    log.info('Generating character description UI for character %s', character)
    character_tag = character["tag"]

    html_escaped_description = image_xml.attrs.get(
        character_tag + "_description", ""
    ).replace('"', "&quot;")

    set_character_description_url = url_for(
        "library.book.chapter.images.scene.set_character_description",
        **chapter.kwargs,
        image_index=image_xml.attrs["index"],
    )

    generate_character_description_url = url_for(
        "library.book.chapter.images.scene.generate_character_description",
        **chapter.kwargs,
        image_index=image_xml.attrs["index"],
    )

    copy_previous_character_description_url = url_for(
        "library.book.chapter.images.scene.copy_previous_character_description",
        **chapter.kwargs,
        image_index=image_xml.attrs["index"],
    )

    return f"""<div class="wa-cluster" id="{character_tag}_description">
    <wa-textarea
        label="Description"
        name="description"
        hx-vals='{{"character": "{character_tag}"}}'
        hx-post="{set_character_description_url}"
        hx-target="#{character_tag}_description"
        hx-swap="outerHTML transition:true"
        hx-trigger="change"
        value="{html_escaped_description}"
        hint="Describe the character's appearance, clothing, and any notable features."
        rows="4"
    ></wa-textarea>

    <div class="wa-stack">
        <wa-button
            hx-post="{generate_character_description_url}"
            hx-vals='{{"character": "{character_tag}"}}'
            hx-swap="outerHTML transition:true"
            hx-trigger="click"
            hx-target="#{character_tag}_description"
            name="generate_{character_tag}_description"
            variant="secondary"
            hx-on::before-request="beforeRequest(this,event)"
            hx-on::after-request="afterRequest(this,event)"
            Generate Description
        </wa-button>

        <wa-button
            hx-post="{copy_previous_character_description_url}"
            hx-vals='{{"character": "{character_tag}"}}'
            hx-swap="outerHTML transition:true"
            hx-trigger="click"
            hx-target="#{character_tag}_description"
            name="copy_previous_{character_tag}_description"
            variant="secondary"
            hx-on::before-request="beforeRequest(this,event)"
            hx-on::after-request="afterRequest(this,event)">
            Copy Previous
        </wa-button>
    </div>
</div>"""


def character_action(chapter, character, image_xml):
    character_tag = character["tag"]

    html_escaped_action = image_xml.attrs.get(character_tag + "_action", "").replace(
        '"', "&quot;"
    )
    html_escaped_motivation = image_xml.attrs.get(character_tag + "_motivation", "").replace(
        '"', "&quot;"
    )

    set_character_actions_url = url_for(
        "library.book.chapter.images.scene.set_character_action",
        **chapter.kwargs,
        image_index=image_xml.attrs["index"],
    )

    set_character_motivation_url = url_for(
        "library.book.chapter.images.scene.set_character_motivation",
        **chapter.kwargs,
        image_index=image_xml.attrs["index"],
    )

    generate_character_actions_url = url_for(
        "library.book.chapter.images.scene.generate_character_action",
        **chapter.kwargs,
        image_index=image_xml.attrs["index"],
    )

    copy_previous_character_action_url = url_for(
        "library.book.chapter.images.copy_previous_character_action",
        **chapter.kwargs,
        image_index=image_xml.attrs["index"],
    )

    return f"""<div class="wa-cluster" id="{character_tag}_action">
        <div class="wa-stack">
            <wa-textarea 
                name="action" 
                label="Action" 
                hx-post="{set_character_actions_url}" 
                hx-vals='{{"character": "{character_tag}"}}'
                hx-swap="outerHTML transition:true"
                hx-trigger="change"
                hx-target="#{character_tag}_action"
                hint="What is this character physically doing at this moment in the story?" 
                value="{html_escaped_action}">
            </wa-textarea>
            
            <wa-textarea 
                name="motivation" 
                label="Motivation" 
                hx-post="{set_character_motivation_url}" 
                hx-vals='{{"character": "{character_tag}"}}'
                hx-swap="outerHTML transition:true"
                hx-trigger="change"
                hx-target="#{character_tag}_motivation"
                hint="Why is this character making this decision at this moment?" 
                value="{html_escaped_motivation}">
            </wa-textarea>
        </div>

        <div class="wa-stack">
            <wa-button
                hx-post="{generate_character_actions_url}"
                hx-vals='{{"character": "{character_tag}"}}'
                hx-swap="outerHTML transition:true"
                hx-trigger="click"
                hx-target="#{character_tag}_action"
                name="generate_{character_tag}_action"
                variant="secondary"
                hx-on::before-request="beforeRequest(this,event)"
                hx-on::after-request="afterRequest(this,event)">
                Generate Action
            </wa-button>

            <wa-button
                hx-post="{copy_previous_character_action_url}"
                hx-vals='{{"character": "{character_tag}"}}'
                hx-swap="outerHTML transition:true"
                hx-trigger="click"
                hx-target="#{character_tag}_action"
                name="copy_previous_{character_tag}_action"
                variant="secondary"
                hx-on::before-request="beforeRequest(this,event)"
                hx-on::after-request="afterRequest(this,event)">
                Copy Previous
            </wa-button>            
        </div>
    </div>"""


