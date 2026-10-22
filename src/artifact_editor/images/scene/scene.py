import json

from bs4.element import Tag

import logger
from artifact_editor import llm
from artifact_editor.tools import get_image_narration_context, get_text_to_next

log = logger.log(__name__)

from artifact_editor.characters import characters

from . import const as scene_const


def generate_character_pose(image_xml: Tag, character):
    # all the text around us
    context = get_image_narration_context(image_xml)

    # the specific text we want to focus on for this character pose determination
    text = get_text_to_next(
        image_xml=image_xml, 
        next_image_xml=image_xml.find_next("image")
    )

    # our prompt
    pose_choices = [c[1] for c in scene_const.POSE_CHOICES]
    prompt = (
        f"I want to determine the pose of a character in a portion of a story.  "
        f"The character is: {character}.  "
        f"The text is: {text}.  "
        f"The surrounding text is: {context}.  "
        "What is the character's pose?  The answer must be one of the following: "
        f"[UNKNOWN, {', '.join(pose_choices)}]\n\n"
        "Single response only.  Do not explain."
    )
    
    pose = llm.str_prompt(prompt)
    if pose in pose_choices:
        for c in scene_const.POSE_CHOICES:
            if c[1] == pose:
                pose = c[0]

    elif pose != "UNKNOWN":
        log.warning(
            f"Chosen pose '{pose}' is not in {scene_const.POSE_CHOICES} for character '{character}' in image {image_xml}"
        )

    return pose


def generate_tod(image_xml: Tag):
    # all the text around us
    context = get_image_narration_context(image_xml)

    # the specific text we want to focus on for this TOD determination
    text = get_text_to_next(
        image_xml=image_xml, 
        next_image_xml=image_xml.find_next("image")
    )

    tod_choices = [c[0] for c in scene_const.TIME_OF_DAY_CHOICES]

    prompt = (
        f"I want to determine the time of day for a portion of a story."
        f"  The text is: {text}.  "
        f"The surrounding text is: {context}.  "
        "What is the time of day?  The answer must be one of the following: "
        f"[{', '.join(tod_choices)}]"
        "Single word response only."
    )
    
    tod = llm.str_prompt(prompt).lower().strip()
    
    if tod not in tod_choices:
        log.warning(
            f"Time of day '{tod}' is not in {scene_const.TIME_OF_DAY_CHOICES} for image {image_xml}"
        )
        tod = f"Odd {tod}"

    image_xml.attrs["tod"] = tod
    return tod


def generate_camera(image_xml: Tag):
    # all the text around us
    context = get_image_narration_context(image_xml)

    # the specific text we want to focus on for this camera angle determination
    text = get_text_to_next(
        image_xml=image_xml, 
        next_image_xml=image_xml.find_next("image")
    )

    # our prompt
    camera_choices = [c[1] for c in scene_const.CAMERA_CHOICES]
    prompt = (
        f"I want to choose a suitable camera angle for a portion of a story."
        f"  The text is: {text}.  "
        f"The surrounding text is: {context}.  "
        "What is the camera angle?  The answer must be one of the following: "
        f"[UNKNOWN, {', '.join(camera_choices)}]\n\n"
        "Single response only.  Do not explain."
    )
    
    camera = llm.str_prompt(prompt)
    if camera in camera_choices:
        for c in scene_const.CAMERA_CHOICES:
            if c[1] == camera:
                camera = c[0]

    elif camera != "UNKNOWN":
        log.warning(
            f"Chosen camera angle '{camera}' is not in {scene_const.CAMERA_CHOICES} for image {image_xml}"
        )

    log.info('Assigning camera angle: "%s"', camera)
    image_xml.attrs["camera"] = camera
    return camera


def generate_scene_characters(chapter, image_xml: Tag):
    """
    Which characters are in this scene?  Even characters that don't speak.Return a list of character names.
    """
    # all the text around us
    context = get_image_narration_context(image_xml)

    # the specific text we want to focus on for this character determination
    text = get_text_to_next(
        image_xml=image_xml, 
        next_image_xml=image_xml.find_next("image")
    )
    
    all_characters = characters.get_all_characters(chapter)

    prompt = (
        f"I want to determine which characters are present in a portion of a story.  "
        f"The text is: {text}.  "
        f"The surrounding text is: {context}.  "
        "Which characters are present in this scene?  Even characters that don't speak.  "
        f"Only characters from this list are valid: [{', '.join(all_characters)}].  "
        "Single JSON format list of characters only.  Do not explain."
    )
    
    scene_characters = list(llm.json_prompt(prompt))
    if 'Narrator' in scene_characters:
        # we don't allow the narrator to be _in_ the scene.  If you want that, you need
        # to make a "real" character for the narrator.
        scene_characters.remove('Narrator')

     # TODO: validate scene_characters response 
     
     # Every character should be a valid character in the story.  If not, log a
     # warning and remove them from the list.

    image_xml.attrs["scene_characters"] = ",".join(scene_characters)
    return scene_characters



def generate_focus_character(chapter, image_xml: Tag):
    """
    Which character is the focus of this image?
    """
    surrounding_text = get_image_narration_context(image_xml)

    all_character_names = characters.get_all_character_names(chapter)

    text = get_text_to_next(
        image_xml=image_xml, next_image_xml=image_xml.find_next("image")
    )

    prompt = (
        f"I want to determine which character is the focus of one small portion of a story."
        f"  The text is: {text}.  "
        f"The surrounding text is: {surrounding_text}.  "
        "Who is the focus character?  Only response with the characters name. "
        "The answer must be one of the following: "
        + json.dumps(["UNKNOWN"] + all_character_names)
    )

    #
    focus_character = llm.str_prompt(prompt)
    focus_character = (
        focus_character.replace("[", "").replace("]", "").strip('"').strip().title()
    )

    log.info('Focus character: "%s"', focus_character)
    if focus_character in all_character_names:
        image_xml.attrs["focus_character"] = characters.name_to_tag(focus_character)
    elif focus_character.replace(" ", "_") in all_character_names:
        image_xml.attrs["focus_character"] = characters.name_to_tag(
            focus_character.replace(" ", "_")
        )
    elif focus_character != "UNKNOWN":
        for character_name in all_character_names:
            if focus_character.lower() in character_name.lower():
                log.info(f"Using partial match for focus character: {character_name}")
                image_xml.attrs["focus_character"] = characters.name_to_tag(
                    character_name
                )
                break

        log.warning(
            f"Detected Focus character '{focus_character}' is not in {all_character_names} for image {image_xml}"
        )
        # if "focus_character" in image_xml.attrs:
        #     del image_xml.attrs["focus_character"]
    
    return image_xml.attrs.get("focus_character", "")




def generate_lighting(chapter, image_xml: Tag):
    """
    What is the lighting in this scene?  Return one of [UNKNOWN, DARK, DIM, NORMAL, BRIGHT]
    """
    # all the text around us
    context = get_image_narration_context(image_xml)

    # the specific text we want to focus on for this character determination
    text = get_text_to_next(
        image_xml=image_xml, 
        next_image_xml=image_xml.find_next("image")
    )
    
    prompt = (
        f"I want to determine the lighting for a portion of a story.  "
        f"The text is: {text}.  "
        f"The surrounding text is: {context}.  "
        "What is the most appropriate lighting in this scene?  "
        "We are breaking lighting down into 3 categories, with multiple options in each category.  " 
        "\nChoose one from each category.  Respond with a JSON dictionary of your choices."
        "\nThe categories and options are:\n"
        f"DIRECTION: {scene_const.LIGHTING_DIRECTION_CHOICES}\n"
        f"SOURCE: {scene_const.LIGHTING_SOURCE_CHOICES}\n"
        f"QUALITY: {scene_const.LIGHTING_QUALITY_CHOICES}\n"
    )
    
    lighting = llm.json_prompt(prompt)
    log.info(f"Raw lighting response: {lighting}")

    for aspect, value in lighting.items():
        aspect = aspect.lower().replace(" ", "_")
        log.info('Setting lighting %s to %s', aspect, value)
        image_xml.attrs[f"lighting_{aspect}"] = value

    return lighting