import os
from artifact_editor.author.author import Author
from artifact_editor.chapter.chapter import Chapter
from artifact_editor.characters import characters
import logger

from artifact_editor.images import images

from . import htmx, scene

from flask import (
    Blueprint,
    request,
    make_response,
)


log = logger.log(__name__)

bp = Blueprint(
    'scene', 
    __name__,
    template_folder=os.path.join(
        os.path.dirname(__file__),
        "templates"
    ),
)


##
## Scene Panel Actions
##

# Setting
@bp.route("generate_setting", methods=["POST"])
def generate_setting(author, title, chapter_number, language, image_index=0):
    """Generate a setting for this image."""
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)

    image_xml = chapter.get_xml().findAll("image")[image_index]

    images.generate_metadata_setting(image_xml, force=True)
    chapter.save_xml()

    return htmx.setting(chapter, image_xml)


@bp.route("generate_pose", methods=["POST"])
def generate_pose(author, title, chapter_number, language, image_index=0):
    """Generate a pose for a character in this image."""
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)

    image_xml = chapter.get_xml().findAll("image")[image_index]

    character = request.form["character"]
    pose = scene.generate_character_pose(image_xml, character)
    if pose:
        image_xml.attrs["pose"] = pose
        chapter.save_xml()

    return htmx.character_pose_selector(chapter, image_xml, character, pose)


@bp.route("previous_setting", methods=["PUT"])
def previous_setting(author, title, chapter_number, language, image_index=0):
    """Borrow the setting from a previous image."""
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)

    all_images = chapter.get_xml().findAll("image")
    # the image we are updating
    image_xml = all_images[image_index]

    # the image we are borrowing from
    previous_index = int(request.args.get("previous_index", 0))
    previous_image = all_images[previous_index]

    image_xml.attrs["setting"] = previous_image.attrs.get("setting", "")
    chapter.save_xml()
    return htmx.setting(chapter, image_xml)


@bp.route("save_setting", methods=["PUT"])
def save_setting(author, title, chapter_number, language, image_index=0):
    log.info(f"{request.form}")
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)
    image_xml = chapter.get_xml().findAll("image")[image_index]

    image_xml.attrs["setting"] = request.form["setting"]
    chapter.save_xml()

    return htmx.setting(chapter, image_xml)


# Time Of Day
@bp.route("tod", methods=["POST"])
def generate_tod(author, title, chapter_number, language, image_index=0):
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)
    image_index = int(image_index)

    image_xml = chapter.get_image(image_index)

    tod = scene.generate_tod(image_xml)
    if tod:
        image_xml.attrs["tod"] = tod
        chapter.save_xml()

    return htmx.tod(chapter, image_xml)


@bp.route("tod", methods=["PUT"])
def save_tod(author, title, chapter_number, language, image_index=0):
    log.info(f"{request.form}")
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)
    image_index = int(image_index)

    image_xml = chapter.get_image(image_index)

    image_xml.attrs["tod"] = request.form["tod"]

    chapter.save_xml()
    return htmx.tod(chapter, image_xml)


# Mood
@bp.route("save_mood", methods=["PUT"])
def save_mood(author, title, chapter_number, language, image_index=0):
    """
    Mood as an attribute of the image isn't quite right
    """
    log.info(f"{request.form}")
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)

    image_xml = chapter.get_xml().findAll("image")[image_index]
    image_xml.attrs["mood"] = request.form["mood"]
    chapter.save_xml()

    return htmx.mood(chapter, image_index)


# Camera
@bp.route("camera", methods=["POST"])
def generate_camera(author, title, chapter_number, language, image_index=0):
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)
    image_index = int(image_index)

    image_xml = chapter.get_image(image_index)

    camera = scene.generate_camera(image_xml)
    if camera:
        image_xml.attrs["camera"] = camera
        chapter.save_xml()

    return htmx.camera_direction(chapter, image_xml)


@bp.route("camera", methods=["PUT"])
def save_camera(author, title, chapter_number, language, image_index=0):
    """
    The camera angle combobox has changed.  The new value is in the form data
    'camera' and must be one of the values in const.py CAMERA_CHOICES.  
    """
    log.info(f"{request.form}")
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)
    image_index = int(image_index)

    image_xml = chapter.get_image(image_index)

    image_xml.attrs["camera"] = request.form["camera"]
    chapter.save_xml()

    return htmx.camera_direction(chapter, image_index)


@bp.route("lighting_<aspect>", methods=["PUT"])
def set_lighting(author, title, chapter_number, language, image_index, aspect):
    """
    One of the lighting aspect comboboxes has changed.
    The Form Data we expect is:
    
    lighting_{aspect}: the new value for this aspect of lighting

    'value' and must be one of the values in the corresponding choices in const.py.  
    """
    log.info(f"{request.form}")
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)
    image_index = int(image_index)

    image_xml = chapter.get_image(image_index)

    key = f"lighting_{aspect}"

    value = request.form[key]
    image_xml.attrs[key] = value
    chapter.save_xml()

    # this is an hx-swap='none', there isn't anything the UI cares about other than success.
    return "", 200

@bp.route("lighting", methods=["POST"])
def generate_lighting(author, title, chapter_number, language, image_index):
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)
    image_index = int(image_index)

    image_xml = chapter.get_image(image_index)

    lighting_aspects = scene.generate_lighting(chapter, image_xml)

    for aspect, value in lighting_aspects.items():
        aspect = aspect.lower().replace(" ", "_")
        log.info('Setting lighting %s to %s', aspect, value)
        image_xml.attrs[f"lighting_{aspect}"] = value

    chapter.save_xml()
    return "", 200

# Scene Characters
@bp.route("characters", methods=["PUT"])
def save_scene_characters(author, title, chapter_number, language, image_index=0):
    """
    The scene characters input has changed.  The new value is in the form data
    'scene_characters' and is a comma-separated list of character tags.
    """
    log.info(f"{request.form}")
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)
    image_index = int(image_index)

    image_xml = chapter.get_image(image_index)

    # if the key is "scene_characters", we want to join the values with a comma
    # otherwise, we just want the first value
    scene_characters = request.form.getlist("scene_characters")
    
    log.info(f"Processing scene_characters={scene_characters}")
    image_xml.attrs["scene_characters"] = ",".join(scene_characters)

    chapter.save_xml()

    return htmx.scene_characters(chapter, image_xml)


@bp.route("characters", methods=["POST"])
def generate_scene_characters(author, title, chapter_number, language, image_index=0):
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)
    image_index = int(image_index)
    image_xml = chapter.get_image(image_index)
    
    scene_characters = scene.generate_scene_characters(chapter, image_xml)
    log.info(f"Scene characters: {scene_characters}")

    if scene_characters:
        image_xml.attrs["scene_characters"] = ",".join(scene_characters)
        chapter.save_xml()

    return htmx.scene_characters(chapter, image_xml)

#
# Character Description Actions
#
# POST /Aesop/Fables/0021/images/15/actions/generate_character_description
@bp.route("generate_character_description", methods=["POST"])
def generate_character_description(
    author, title, chapter_number, language, image_index=0
):
    """
    We want the character description for this particular image which
    may be different from the full chapter character description.
    """
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)
    image_index = int(image_index)
    image_xml = chapter.get_xml().findAll("image")[image_index]
    

    character_tag = request.form["character"]

    character_dict = characters.get_character(chapter, character_tag)
    if not character_dict:
        return f"Character {character_tag} not found.", 400

    description = characters.generate_metadata_character_description(
        character_dict, image_xml, force=True
    )

    chapter.load_xml(force=True)
    image_xml = chapter.get_image(image_index)
    image_xml.attrs[f"{character_tag}_description"] = description
    chapter.save_xml()

    character = characters.get_character(chapter, character_tag)

    return htmx.character_description(
        chapter, character, image_xml
    ), 200


@bp.route("set_character_description", methods=["POST"])
def set_character_description(author, title, chapter_number, language, image_index):
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)
    image_index = int(image_index)

    log.info(f"{request.form}")

    character_tag = request.form.get("character")
    description = request.form.get("description", "")

    if description == "":
        log.info(f"No description provided, using default for {character_tag}")
        description = characters.get_character(chapter, character_tag).get(
            "description", ""
        )

    log.info(f"Setting description for {character_tag} to {description}")

    image_xml = chapter.get_image(image_index)
    image_xml.attrs[f"{character_tag}_description"] = description
    chapter.save_xml()

    character = characters.get_character(chapter, character_tag)

    return htmx.character_description(chapter, character, image_xml), 200


# POST /Aesop/Fables/0020/images/21/actions/copy_previous_character_description
@bp.route("copy_previous_character_description", methods=["POST"])
def copy_previous_character_description(
    author, title, chapter_number, language, image_index=0
):
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)
    image_index = int(image_index)

    image_xml = chapter.get_image(image_index)

    character_name = request.form.get("character", "").strip()

    if image_index > 0:
        prev_image = chapter.get_image(image_index - 1)

        previous_description = prev_image.attrs.get(f"{character_name}_description", "")
        if previous_description:
            log.info(f"Copying description for {character_name} from previous image")
            image_xml.attrs[f"{character_name}_description"] = previous_description
            log.info(f"Previous description: {previous_description}")
            chapter.save_xml()
        else:
            log.info(f"No description for {character_name} in previous image")

    return htmx.character_description(
        chapter,
        character=characters.get_character(chapter, character_name),
        image_xml=image_xml
    ), 200

#
# Character Pose Actions
#
@bp.route("set_character_pose", methods=["PUT"])
def set_character_pose(author, title, chapter_number, language, image_index):
    """
    expects 'character' and 'pose' in the form data.  
    
    The character is the tag of the character, e.g. 'alice'.  
    The pose is one of the values in const.py POSE_CHOICES.
    """
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)

    character = request.form.get("character")
    pose = request.form.get("pose")
    log.info(f"Setting pose for {character} to {pose}")

    image = chapter.get_xml().findAll("image")[image_index]
    image.attrs[f"{character}_pose"] = pose
    chapter.save_xml()

    return htmx.character_pose_selector(chapter, image_index, character, pose)


@bp.route("generate_character_pose", methods=["POST"])
def generate_character_pose(author, title, chapter_number, language, image_index):
    """
    expects 'character' and 'pose' in the form data.  
    
    The character is the tag of the character, e.g. 'alice'.  
    The pose is one of the values in const.py POSE_CHOICES.
    """
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)
    image_index = int(image_index)
   
    character_tag = request.form.get("character")

    image_xml = chapter.get_image(image_index)
    image_xml.attrs[f"{character_tag}_pose"] = scene.generate_character_pose(image_xml, character_tag)
    chapter.save_xml()

    character = characters.get_character(chapter, character_tag)

    return htmx.character_pose_selector(
        chapter, character, image_xml
    )


#           /L.%20Frank%20Baum/The%20Marvelous%20Land%20of%20Oz/0001/image/3/action/Tip/set_location
# hx-post="                    /{chapterurl}/image/{image_index}/action/{character}/set_location"
@bp.route("set_location", methods=["POST"])
def set_character_location(author, title, chapter_number, language, image_index):
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)
    image_index = int(image_index)

    character = request.form.get("character")
    location = request.form.get("location")    
    
    image_xml = chapter.get_image(image_index)

    # if the key is "scene_characters", we want to join the values with a comma
    # otherwise, we just want the first value
    location = request.form.get("location", "")

    log.info(f"Processing {character}={location}")
    image_xml.attrs[f"{character}_location"] = location

    chapter.save_xml()

    return htmx.character_location_response(
        chapter=chapter,
        character=character,
        image_xml=image_xml,
    ), 200

# update_description
@bp.route("update_description", methods=["POST"])
def update_description(author, title, chapter_number, language, image_index):
    """
    The description is a strong hint to the image generation workflow.
    """
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)
    image_index = int(image_index)

    image_xml = chapter.get_image(image_index)

    description = request.form.get("description", "")
    image_xml.attrs["description"] = description
    chapter.save_xml()

    return htmx.description(chapter, image_xml), 200

# /{chapterurl}/images/{image_index}/actions/generate_character_action
@bp.route("generate_character_action", methods=["POST"])
def generate_character_action(author, title, chapter_number, language, image_index=0):
    log.info(f"{request.form}")
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)
    image_index = int(image_index)

    image_xml = chapter.get_image(image_index)

    character_tag = request.form["character"]

    character_dict = characters.get_character(chapter, character_tag)
    if not character_dict:
        return f"Character {character_tag} not found.", 400

    action_dict = images.generate_metadata_character_action(
        character_dict,
        image_xml,
        force=True,
    )

    motivation = ""
    if isinstance(action_dict, dict):
        if "action" in action_dict:
            action = action_dict["action"]

        if "motivation" in action_dict:
            motivation = action_dict["motivation"]
        
    chapter.load_xml(force=True)
    image_xml = chapter.get_image(image_index)

    if action:
        log.info('Saving action', character_tag=character_tag, action=action)
        image_xml.attrs[f"{character_tag}_action"] = action
    else:
        log.info('No action generated, skipping save for action', character_tag=character_tag)

    if motivation:
        log.info('Saving motivation', character_tag=character_tag, motivation=motivation)
        image_xml.attrs[f"{character_tag}_motivation"] = motivation
    else:
        log.info('No motivation generated, skipping save for motivation', character_tag=character_tag)

    chapter.save_xml()

    return htmx.character_action(chapter, character_dict, image_xml), 200


@bp.route("set_character_action", methods=["POST"])
def set_character_action(author, title, chapter_number, language, image_index=0):
    log.info(f"{request.form}")
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)
    image_index = int(image_index)

    image_xml = chapter.get_image(image_index)
    character_tag = request.form["character"]
    action = request.form["action"]

    image_xml.attrs[f"{character_tag}_action"] = action
    chapter.save_xml()

    character = characters.get_character(chapter, character_tag)

    return htmx.character_action(chapter, character, image_xml), 200

@bp.route("set_character_motivation", methods=["POST"])
def set_character_motivation(author, title, chapter_number, language, image_index=0):
    log.info(f"{request.form}")
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)
    image_index = int(image_index)

    image_xml = chapter.get_image(image_index)
    character_tag = request.form["character"]
    motivation = request.form["motivation"]

    image_xml.attrs[f"{character_tag}_motivation"] = motivation
    chapter.save_xml()

    character = characters.get_character(chapter, character_tag)

    return htmx.character_action(chapter, character, image_xml), 200

@bp.route("set_focus_character", methods=["POST"])
def set_focus_character(author, title, chapter_number, language, image_index=0):
    log.info(f"{request.form}")
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)
    image_index = int(image_index)

    image_xml = chapter.get_image(image_index)

    image_xml.attrs["focus_character"] = request.form.get("focus_character", "")
    chapter.save_xml()

    return htmx.focus_character(
        chapter, image_xml
    ), 200


@bp.route("copy_from_previous", methods=["POST"])
def copy_from_previous(author, title, chapter_number, language, image_index=0):
    log.info(f"{request.form}")
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)
    image_index = int(image_index)

    image_xml = chapter.get_image(image_index)
    paragraph = image_xml.find_parent("paragraph")
    
    if image_index > 0:
        prev_image = chapter.get_image(image_index - 1)
        for key in prev_image.attrs.keys():
            log.info("Considering %s=%s", key, prev_image.attrs[key])

            if key in [
                "style",
                "tod",
                "camera",
                "setting",
                "scene_characters",
                "meta_prompt",
            ]:
                log.info(f"Updating XML ({key})...")
                image_xml.attrs[key] = prev_image.attrs[key]

        # second pass for character-specific attributes so we can
        # sanitize against scene_characters.
        valid_characters = image_xml.attrs.get("scene_characters", "").split(",")
        for key in prev_image.attrs.keys():
            if (
                key.endswith("_pose")
                or key.endswith("_location")
                or key.endswith("_action")
            ):
                log.info(f"Updating character XML ({key})...")
                character_tag, _ = key.rsplit("_", 1)

                if (
                    "scene_characters" in image_xml.attrs
                    and character_tag in valid_characters
                ):
                    image_xml.attrs[key] = prev_image.attrs[key]
                else:
                    log.info(
                        f"Skipping {key} because {character_tag} is not in scene_characters ({image_xml.attrs.get('scene_characters', '')})"
                    )

            if key == "focus_character" and prev_image.attrs[key] in valid_characters:
                log.info(f"Updating focus_character XML ({key})...")
                image_xml.attrs[key] = prev_image.attrs[key]

        # third pass to clean up any now-invalid character attributes for characters not in scene_characters

        for key in list(image_xml.attrs.keys()):
            if (
                key.endswith("_pose")
                or key.endswith("_location")
                or key.endswith("_action")
            ):
                character_tag, _ = key.rsplit("_", 1)
                if character_tag not in valid_characters:
                    log.info(
                        f"Removing {key} because {character_tag} is not in scene_characters ({image_xml.attrs.get('scene_characters', '')})"
                    )
                    del image_xml.attrs[key]

    else:
        log.info(f"No previous image to copy from (image_index == {image_index}).")

    chapter.save_xml()

    response = make_response(
        htmx.image_strip_centerpiece(chapter, image_xml, default="image")
    )
    response.headers["HX-Refresh"] = "true"

    return response

