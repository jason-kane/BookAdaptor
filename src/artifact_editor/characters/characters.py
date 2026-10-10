import inspect
import json
import os
import random
import tempfile
import textwrap
import time
from collections import defaultdict

from bs4 import BeautifulSoup

import const
import logger
from artifact_editor import (
    llm,
    tools,
)
from artifact_editor.images import images

log = logger.log(__name__)

# from the _old_ TTS engine..
ALLOWED_AGES = ["child", "young", "middle_aged", "old"]
ALLOWED_ACCENTS = ["british", "american", "african", "australian", "indian"]
ALLOWED_GENDERS = ["male", "female"]


def _get_characters_fn(chapter, is_global:bool=False):
    if is_global:
        return os.path.join(const.GLOBAL_CHARACTERS_DIR, "characters.json")
    else:
        return os.path.join(const.LIBRARY_DIR, chapter.chapterdir, "characters.json")


def save_characters(chapter, characters, is_global:bool=False):
    characters_fn = _get_characters_fn(chapter, is_global=is_global)
    # it's cool bro.
    os.makedirs(
        os.path.dirname(characters_fn),
        exist_ok=True
    )
    with open(characters_fn, "w") as f:
        json.dump(characters, f, indent=2)


def save_character(chapter, character_tag, character_dict):
    """
    Saves a character to the chapter's characters.json file.
    If the character already exists, it will be updated.
    """
    is_global = character_dict.get('is_global', False)

    characters = get_all_characters(chapter, is_global=is_global)
    log.info(f'Saving character {character_tag=} as {character_dict=}')
    characters[character_tag] = character_dict

    if is_global:
        characters = get_all_characters(chapter, is_global=False)
        characters[character_tag] = character_dict
        save_characters(chapter, characters, is_global=False)        

    save_characters(chapter, characters, is_global=is_global)


def get_all_character_names(chapter) -> list:
    characters = get_all_characters(chapter)
    return list(c['name'] for c in characters.values() if 'name' in c)


def add_character(chapter, character_name, chardict=None):
    characters = get_all_characters(chapter)
    if chardict is None:
        chardict = const.NARRATOR
    characters[character_name] = chardict
    save_characters(chapter, characters)
    return chardict


def get_global_character(chapter, character_name) -> dict:
    all_characters = get_all_characters(chapter, is_global=True)
    for character_tag in all_characters:

        if all_characters[character_tag]['name'] == character_name:
            return all_characters[character_tag]


def get_character(chapter, character_name) -> dict:
    """
    We might be global, don't make assumptions.
    """
    log.info(f'get_character(chapter, {character_name=}) called by: %s', inspect.stack()[1].function)
    if character_name == "":
        log.error("Character name is empty")
        return None

    if chapter is None:
        log.error("Chapter is None")
        return None

    character = None

    characters = get_all_characters(chapter)
    character_tag = name_to_tag(character_name)

    character = characters.get(character_tag)

    # if this is a global character, refresh it from the source.
    if character and character.get('is_global', False):
        global_characters = get_all_characters(chapter, is_global=True)
        character = global_characters.get(character_tag)

    if character is None:
        for c in characters:
            if character_name in characters[c].get('alias', []):
                character = characters[c]
                break

            if character_tag in characters[c].get('alias', []):
                character = characters[c]
                break            

    if character is None:
        log.error(f"Unknown character: {character_name=} {chapter=}")
        c = add_character(chapter, character_name)
        return c
    
    if 'number_of_appearances' not in character:
        # no no, well, not quite.  we don't want to reset this
        # we want to recalculate it.  This thing takes care of itself.
        characters = number_of_appearances(chapter, characters)
        character = characters.get(character_tag)

    return character


def number_of_appearances(chapter, characters: dict | None=None):
    """
    Re-calculates the number of appearances of ALL character in the chapter.
    because all characters is only slightly slower than once character.
    
    This is pretty fast so I'm guessing I'll be lazy and use it instead
    of doing the accounting.
    """
    log.info('Refreshing character appearance counts')

    # audio, only characters that speak
    audio_appearances = defaultdict(int)
    visual_appearances = defaultdict(int)
       
    if characters is None:
        characters = get_all_characters(chapter)

    
    for phrase in chapter.get_xml().find_all('phrase'):
        audio_appearances[phrase.get('speaker', 'Narrator')] += 1

    for tag, character in characters.items():
        character['audio_appearances'] = audio_appearances.get(tag, 0)


    for image_xml in chapter.get_xml().find_all('image'):
        for c in image_xml.get('scene_characters', '').split(", "):
            visual_appearances[c] += 1

    for tag, character in characters.items():
        character['visual_appearances'] = visual_appearances.get(tag, 0)

    save_characters(chapter, characters)
    return characters


def get_all_characters(chapter, is_global: bool = False) -> dict:
    """
    """
    characters = {}
    characters_fn = _get_characters_fn(chapter, is_global=is_global)
    log.info('Including', characters_fn=characters_fn)
    if os.path.exists(characters_fn):
        with open(characters_fn, "r") as f:
            characters = json.load(f)

    else:
        characters = {
           'Narrator': const.NARRATOR
        }
        with open(characters_fn, "w") as f:
            json.dump(characters, f, indent=2)

    # TODO:
    # Fix this.  I liked it.
    #
    if not all(
        (
            'audio_appearances' in characters[tag] and 
            'visual_appearances' in characters[tag]
        ) for tag in characters):
            # if any characters are missing audio or visual appearances, refresh 
            # the values for everyone.
            characters = number_of_appearances(chapter, characters)

    # minor spot corrections/additions
    valid_characters = {}
    for tag in characters:
        if 'tag' not in characters[tag]:
            characters[tag]['tag'] = tag

        if 'name' not in characters[tag]:
            characters[tag]['name'] = tag

        # empty name, replace with best effort de-tagging
        if characters[tag]['name'] == "":
            characters[tag]['name'] = tag.replace("_", " ").title()

        if tag:
            valid_characters[tag] = characters[tag]

    log.info('Returning', valid_characters=valid_characters)
    if "Prepping_To_Attack_Setting_The_Tone_For_The_Story" in valid_characters:
        log.error("This is a bad character name.  It should not be here.")
        raise ValueError("This is a bad character name.  It should not be here.")

    return valid_characters


def name_to_tag(name: str) -> str:
    if not name:
        return ""
    
    for strip_this in [".", ","]:
        if strip_this in name:
            name = name.replace(strip_this, "")
    
    for under_this in [" ", "'", '"', "!", "?", ":", ";", "-"]:
        if under_this in name:
            name = name.replace(under_this, "_")

    return name.title()


def draw_headshot(chapter, character_name, force=False):
    characters = get_all_characters(chapter)
    character_name = name_to_tag(character_name)
    
    if character_name in characters:
        character = characters[character_name]
        
        os.makedirs(
            os.path.join(
                const.LIBRARY_DIR,
                chapter.chapterdir,
                "characters"
            ),
            exist_ok=True
        )

        image_fn = os.path.join(
            const.LIBRARY_DIR,
            chapter.chapterdir,
            "characters",
            f"{character_name}.png"
        )

        if force and os.path.exists(image_fn):
            os.unlink(image_fn)

        # get_flux_image is badly named, we're telling it to create the named file.
        my_image, was_created = images.get_flux_image(
            image_fn=image_fn,
            clip_prompt="",
            t5_prompt=character.get('description', ''),
            force=force
        )
                    
        characters[character_name]["headshot"] = os.path.join(
            chapter.chapterdir, 
            "characters",
            f"{character_name}",
            "headshot.png"
        )

        save_characters(chapter, characters)
    else:
        return None


def tag_to_character(chapter, character_tag):
    characters = get_all_characters(chapter)
    return characters.get(character_tag)


def generate_sample_audio(
    chapter,
    character_name,
    sample_sentence,
    effects=None,
):
    """
    Generates a sample audio file for a character.
    """
    character = get_character(chapter, character_name)
    if character is None or not character.get("voices"):
        log.error(f"No voices found for character {character_name}.")
        return None

    workdir = os.path.join(const.LIBRARY_DIR, chapter.chapterdir)
    tempdir = os.path.join(workdir, "audio_temp_cache")
    os.makedirs(tempdir, exist_ok=True)

    with tempfile.NamedTemporaryFile(suffix=".wav", dir=tempdir, delete=False) as sample_file:
        wavfile = sample_file.name

    phrase_soup = BeautifulSoup("", "xml")
    phrase_xml = phrase_soup.new_tag("phrase")
    phrase_xml["speaker"] = character_name
    phrase_xml.string = sample_sentence

    # Use the same Comfy workflow path as chapter phrase audio generation.
    llm.text_2_audio(
        chapter,
        phrase_xml,
        character_name,
        wavfile,
        force=True,
    )

    return os.path.basename(wavfile)


def generate_metadata_character_description(
    character_dict,
    image_xml,
    force=False
):
    """
    Generates a character description based on the character dictionary
    and the image XML.
    """
    description = image_xml.get(f"{character_dict['tag']}_description", "")
    if description and not force:
        return description

    text = tools.get_text_to_next(
        image_xml=image_xml, 
        next_image_xml=image_xml.find_next_sibling("image")
    )

    prompt = f"""
    I will provide you with a portion of a book.  Your job is to extract a
    physical description of the character named "{character_dict['name']}". 
    
    This character is normally described as:

    {character_dict.get('description', '')}

    We want to customize that description to reflect one specific portion of the book.

    The portion of the book we want to focus on is:

    {text}

    Do not include anything but the detailed description of {character_dict['name']}.
    """

    response = llm.str_prompt(prompt=prompt, force=True)
    image_xml.attrs[f"{character_dict['tag']}_description"] = response
    return response


def generate_description(chapter, character_tag):
    """
    Generate a description for the character.
    """
    character_dict = get_character(
        chapter,
        character_tag
    )
    description = []

    # existing_description = character_dict.get("description", "")
    # if existing_description:
    #     description.append(existing_description)

    # prompt = """
    # I will provide you with a portion of a book.  Your job is to extract a
    # physical description of the character named "{character_name}". If this
    # portion of the book does not contain any description of the character,
    # respond with "No description found".  Focus on physical attributes you might
    # need to draw a good portrait like ethnicity, age, hair color, eye color,
    # height, gender and clothing.

    # Do not include anything but the detailed description of {character_name} 
    # or "no description found".
    # """.format(character_name=character_dict.get('name', character_tag))

    # one paragraph at a time?
    for p in chapter.get_xml().find_all("paragraph"):
        # prompt += "\n\n" + p.get_text()

        response = llm.str_prompt(
            prompt=f"""Character name: {character_dict.get('name', character_tag)}
            
            {p.get_text()}""",
            system_prompt="You are a helpful assistant that extracts physical "
                "descriptions of characters from book excerpts. Focus on details "
                "like ethnicity, age, hair color, eye color, height, gender, and "
                "clothing. If no description of the character is found in the provided "
                "excerpt, respond with 'No description found'. Always respond with "
                "either a detailed physical description or 'No description found'. ",
            force=True
        )

        if (
            "no description found" not in response.lower()
            and 
            "I cannot provide" not in response.lower()
        ):
            description.append(response)

        facts = json.dumps({
            'gender': character_dict.get('gender', ''),
            'age': character_dict.get('age', ''),
            'accent': character_dict.get('accent', ''),
        }, indent=2)

        # second pass to get more ephemeral details.
        name = character_dict.get('name', character_tag)
        response = llm.str_prompt(
            prompt=f"""Provided facts: {facts}
            
            {p.get_text()}""",
            system_prompt="You are a helpful assistant that extracts "
                "descriptions of characters from book excerpts. We want to know "
                f"if this excerpt tells us anything about who {name} is and what "
                "they are like. Focus on details like personality, demeanor, "
                "and character traits like maturity. Do not include any reasoning "
                "or commentary, only the description.",
            force=True
        )

        if (
            "no description found" not in response.lower()
            and 
            "I cannot provide" not in response.lower()
        ):
            description.append(response)

    description = condense_description(description)

    # one last pass to bring it home.
    name = character_dict.get('name', character_tag)
    description = llm.str_prompt(
        prompt=description,
        system_prompt=(
            "You are an expert at predicting the appearance of characters " 
            "based on minimal and often conflicting and ambiguous information. " 
            "The description should be highly detailed.  Do not include any "
            "reasoning or commentary, only the description itself."
        ),
        force=True
    )

    # and condense it again, because damn.
    description = condense_description(description)

    character_dict["description"] = description
    save_character(
        chapter,
        character_tag,
        character_dict
    )

    # condense it again. (wtf?)
    # description = condense_description(description)
    return description


def condense_description(existing_description):
    
    if isinstance(existing_description, list):
        # now refine
        if existing_description:
            # and if you can't, then do it anyway.
            # filter out negative responses
            existing_description = "\n\n".join(
                [
                    d for d in existing_description
                    if \
                        "no description found" not in d.lower() 
                    and \
                        "i'm sorry" not in d.lower()
                ]
            )

        
        # I will provide you with several excerpts from a book that may describe a
        # character named "{character_dict.get('name', character_tag)}".  Some excerpts may not have any
        # description of the character, while others may have conflicting
        # information. Your job is to combine these excerpts into a single,
        # coherent physical description of the character. If the excerpts
        # contradict each other, use your best judgement to create a consistent
        # description of the appearance of this character. Include details such as
        # ethnicity, eye color, hair, height, age, gender and clothing.

        # If you are unable to condense the descriptions then return them as one
        # paragraph with duplicates removed.
        
        prompt = textwrap.dedent(f"""                                 
        Here are the excerpts:
        
        {existing_description}
        
        Combined description:
        """)

        description = llm.str_prompt(
            prompt=prompt, 
            system_prompt="You are a helpful assistant that combines multiple excerpts"
            " into a single, coherent physical description of a character. Focus on "
            "details like ethnicity, age, hair color, eye color, height, gender, and "
            "clothing. If the excerpts contradict each other, use your best judgement "
            "to create a consistent description. Always respond with either a detailed "
            "physical description or 'No description found', and nothing else.",
            force=True
        )
    else:
        description = "No description found."

    return description



def fanciful_description(chapter, character_tag):
    """
    Generate a fanciful description for the character.
    """
    character_dict = get_character(
        chapter,
        character_tag
    )
    description = []

    existing_description = character_dict.get("description", "")
    if existing_description:
        description.append(existing_description)

    for p in chapter.get_xml().find_all("paragraph"):
        response = llm.str_prompt(
            prompt=f"""Character name: {character_dict.get('name', character_tag)}
            
            {p.get_text()}""",
            system_prompt="You create detailed descriptions of story characters."
                "Focus on details like ethnicity, age, hair color, eye color, "
                "height, gender, and clothing.  You may embellish the description based"
                " on typical character traits to make it more vivid and engaging.",
            force=True
        )

        description.append(response)

    # mix and combine the descriptions into a single, coherent description.
    description = condense_description(description)

    character_dict["description"] = description
    save_character(
        chapter,
        character_tag,
        character_dict
    )

    # condense it again.
    # description = condense_description(description)
    return description



def condense_description(existing_description):
    
    prompt = f"""   
    {existing_description}
    """

    description = llm.str_prompt(
        prompt=prompt,
        system_prompt="You are a helpful assistant that condenses physical descriptions of characters into concise, detailed descriptions.",
        force=True
    )
    return description
    
