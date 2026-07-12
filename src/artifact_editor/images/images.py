import glob
import json
import os
import random
import subprocess
import redis
import textwrap
import shutil
import time
from functools import lru_cache

from text_to_image.registry import registry as t2i_registry

from ascii_magic import AsciiArt
from PIL import Image, ImageFilter, UnidentifiedImageError

import const
import image_to_video
import logger

import transitions
import animations

from artifact_editor import (
    config,
    llm,
    video,
)
from artifact_editor.characters import characters
from artifact_editor.styles import styles
from artifact_editor.tools import (
    friendly_location,
    get_chapterdir,
    get_surrounding_paragraphs,
    get_tag,
    get_text_to_next,
    wait_for,
)

from .scene import const as scene_const
from flask import url_for

# /src/drawing.fifo
FIFO_FN = os.path.join(os.path.dirname(__file__), "..", "..", "drawing.fifo")
log = logger.log(__name__)

# how many characters are allowed in each meta setting
MAX_SETTING_LENGTH = 250
CLAIM_SETTING_LENGTH = 40

TIME_OF_DAY_CHOICES = scene_const.TIME_OF_DAY_CHOICES
CAMERA_CHOICES = scene_const.CAMERA_CHOICES
POSE_CHOICES = scene_const.POSE_CHOICES


def text_to_clip_prompt(image, paragraph, text_to_next, t5_prompt=""):
    prompt_fn = os.path.abspath(
        os.path.join(
            const.LIBRARY_DIR,
            paragraph.attrs["dir"],
            f"img_{image.attrs["fragdex"]}.prompt",
        )
    )

    clip_prompt = text_to_image_clip_prompt(
        text=text_to_next,
        paragraph_text=get_surrounding_paragraphs(paragraph),
        t5_prompt=t5_prompt,
        prompt_fn=prompt_fn,
    )

    # strip and dedupe
    clip_prompt = ",".join(
        list(set([keyword.strip() for keyword in clip_prompt.split(",")]))
    )

    image.attrs["clip_prompt"] = clip_prompt
    return clip_prompt


def assign_paragraph_dir_and_index(soup, chapterdir):
    """
    Assigns the 'dir' and 'index' attributes to all paragraphs in the book.
    This is used to ensure that paragraphs have a unique directory and index.
    """
    for index, paragraph in enumerate(soup.findAll("paragraph")):
        if "dir" not in paragraph.attrs:
            paragraph["dir"] = os.path.join(chapterdir, f"paragraphs/{index:06}")
            os.makedirs(
                os.path.join(const.LIBRARY_DIR, paragraph["dir"]), exist_ok=True
            )
            log.info(f"Assigning dir {paragraph['dir']} for paragraph {index}")
        if "index" not in paragraph.attrs:
            paragraph["index"] = str(index)
            log.info(f"Assigning index {index} for paragraph {paragraph['dir']}")


def imageForex(chapter, image_xml, image_index):
    absolute = {}

    all_images = chapter.get_xml().findAll("image")
    total_images = len(all_images)

    def gridtable(dim=8, count=0):
        height = 200 // dim
        done = False
        log.info("There are %s images in this book. dim: %s", total_images, dim)
        for sixtyfour_c in range(0, dim):
            for sixtyfour_r in range(0, dim):
                if done or count < 0:
                    absolute[sixtyfour_c, sixtyfour_r] = (
                        f'<div count="{count}" class="imgheight img-{height}"></div>'
                    )
                else:
                    if count < total_images:
                        image = all_images[count]
                    else:
                        image = None

                    if count == total_images:
                        done = True
                        absolute[sixtyfour_c, sixtyfour_r] = (
                            f'<div count="{count}" class="imgheight img-{height}"></div>'
                        )
                    elif image:
                        #"src" in image_xml.attrs and image_xml.attrs["src"]:
                        imageurl = url_for(
                            "library.book.chapter.images.show_image_by_index",
                            **chapter.kwargs,
                            height=height,
                            image_index=image.attrs.get("index", '0'),
                        )

                        absolute[sixtyfour_c, sixtyfour_r] = f"""<a 
                            hx-on::click="chooseImage('{chapter.url}', '{chapter.language}', {count}, 'forex')"
                            href="{url_for('library.book.chapter.images.base', author=chapter.author.name, title=chapter.title, chapter_number=chapter.number, language=chapter.language, image_index=count)}">
                            <div class="imgheight img-{height}" style="background-color:#777">
                                <img class="image_{image.attrs.get("index")}" src="{imageurl}"/></img>
                            </div>
                        </a>"""
                    # else:
                    #     absolute[sixtyfour_c, sixtyfour_r] = f"""<a 
                    #         hx-on::click="chooseImage('{chapter.url}', '{chapter.language}', {count}, 'forex')"
                    #         href="{url_for('library.book.chapter.images.base', author=chapter.author.name, title=chapter.title, chapter_number=chapter.number, language=chapter.language, image_index=count)}">
                    #         <img class="imgheight img-{height} image_{image.attrs.get("index")}" style="background-color:#777;" src="/static/images/x.png"/></img>
                    #     </a>"""

                count += 1

        left = '<div class="grid" style="height:200px; width:200px; float:left">'
        for sixtyfour_c in range(0, dim):
            left += "<div>"
            for sixtyfour_r in range(0, dim):
                left += absolute[sixtyfour_c, sixtyfour_r]
            left += "</div>"
        left += "</div>"

        return left

    count = image_index - (4 + 16 + 64)
    left = gridtable(8, count)

    count = image_index - (4 + 16)
    left += gridtable(4, count)
    
    count = image_index - 4
    left += gridtable(2, count)

    if "src" in image_xml.attrs:
        image_src = url_for(
            "library.book.chapter.images.show_image_by_index",
            **chapter.kwargs,
            height=200,
            image_index=image_xml.attrs["index"],
        ) + "?t=%s" % int(time.time())
        
    else:
        image_src = "/static/images/x.png"
        
    center = f"""<div class="imgheight" style="height: 200px; float:left">
        <img class="image_{image_xml.attrs.get("index")}" style="background-color:#777; height:200px;" src="{image_src}"/></img>
    </div>"""

    count = image_index + 1
    right = gridtable(2, count)

    log.warning('A')

    count = image_index + 5
    right += gridtable(4, count)

    log.warning('B')

    try:
        count = image_index + 17
        right += gridtable(8, count)
    except Exception as e:
        log.error(f"Error in gridtable: {e}")
        raise

    log.warning('!!! Returning forex... !!!')

    return left + center + right


def create_cite_image(
    artist: str = "",
    title: str = "",
    year: str = "",
    medium: str = "",
    width: str = "",
    height: str = "",
    location: str = "",
    output_image_pfn: str = "",
):
    log.info(
        f"create_cite_image({artist=}, {title=}, {year=}, {medium=}, {width=}, {height=}, {location=}, {output_image_pfn=})"
    )
    tex_template = """\\documentclass[varwidth=true]{standalone}
\\pagestyle{empty}

\\begin{document}
"""
    if artist:
        tex_template += f"\\textbf{{{artist}}} \\\\\n"
    else:
        tex_template += "\\textbf{Unknown Artist} \\\\\n"

    if title:
        if year:
            tex_template += f"\\textit{{{title}}}, {year} \\\\\n"
        else:
            tex_template += f"\\textit{{{title}}}, Unknown Year \\\\\n"
    else:
        if year:
            tex_template += f"\\textit{{Untitled}}, {year} \\\\\n"
        else:
            tex_template += "\\textit{Untitled}, Unknown Year \\\\\n"

    if medium:
        if width and height:
            tex_template += f"{medium}, {width} $\\times$ {height} \\\\\n"
        else:
            tex_template += f"{medium}, Unknown dimensions \\\\\n"
    else:
        if width and height:
            tex_template += f"Unknown medium, {width} $\\times$ {height} \\\\\n"
        else:
            tex_template += "Unknown medium, Unknown dimensions \\\\\n"

    if location:
        tex_template += f"{location}\n"
    else:
        tex_template += "Unknown Location\n"

    tex_template += "\\end{document}\n"

    ## okay, we have our LaTeX ready.  Now we need to turn it into an image
    output_directory = os.path.dirname(output_image_pfn)
    tex_fn = os.path.join(
        output_directory,
        os.path.splitext(os.path.basename(output_image_pfn))[0] + ".tex",
    )

    # create our .tex file
    with open(tex_fn, "w") as f:
        f.write(tex_template)

    # create the .dvi (this is pretty fast)
    subprocess.run(["latex", "--output-directory", output_directory, tex_fn])

    dvi_fn = os.path.join(
        output_directory,
        os.path.splitext(os.path.basename(output_image_pfn))[0] + ".dvi",
    )

    # convert the .dvi to .png
    subprocess.run(["dvipng", "-o", output_image_pfn, dvi_fn])
    return output_image_pfn


def all_images_by_image_index(paragraph_dir, image_index):
    log.info(
        "Looking for images in paragraph dir: %s with index: %s",
        paragraph_dir,
        image_index,
    )
    globpath = os.path.join(
        const.LIBRARY_DIR,
        paragraph_dir,
        f"img_{image_index}_*.png",
    )
    images = glob.glob(globpath)
    log.info("images matching glob", globpath=globpath, images=images)

    return images


def get_camera_options(chapter, image_file, image_xml, full_screen=False):
    # future full-screen
    camera_actions = []
    paragraph = image_xml.find_parent("paragraph")
    if os.path.exists(image_file):
        paragraphdir = chapter.get_paragraph_dir(paragraph.attrs["index"])
        try:
            img = Image.open(image_file)
            width, height = img.size

            aspect_ratio = width / height
            if aspect_ratio > 1.1:
                # width is greater than height, landscape mode.
                # User gets to choose how we deal with it.

                # these are plugin modules.  Put a file with the right kind of
                # name in the directory and it will be discovered and included.
                image_to_video_widgets = image_to_video.registry.all()
                for widget_key in image_to_video_widgets:
                    widget_class = image_to_video.registry.get(widget_key)

                    widget = widget_class(
                        image_file=image_file, paragraphdir=paragraphdir
                    )

                    camera_actions.append(widget.get_button(image_xml))

            elif aspect_ratio < 0.9:
                # height is greater than width, portrait mode.
                image_to_video_widgets = image_to_video.registry.all()
                for widget_key in image_to_video_widgets:
                    widget_class = image_to_video.registry.get(widget_key)

                    widget = widget_class(
                        image_file=image_file, paragraphdir=paragraphdir
                    )

                    camera_actions.append(widget.get_button(image_xml))

            else:
                # we are at least roughly square.  We will pad the weaker
                # dimension to make it exactly square.  Any scaling will happen
                # elsewhere.
                if width != height:
                    # pad to square
                    dim = max(width, height)

                    # black is better.
                    new_img = Image.new("RGB", (dim, dim), (0, 0, 0))

                    # put our image in the center
                    new_img.paste(img, ((dim - width) // 2, (dim - height) // 2))
                    new_img.save(image_file)
                    new_img.close()

            img.close()
        except Exception as e:
            log.error(f"Error opening image {image_file}: {e}")
            raise
    else:
        log.warning(f"Image file {image_file} does not exist.")

    return "\n".join(camera_actions)


def generate_metadata_style(image_xml):
    """
    Use whatever sorts fo techniques we want to choose the most appropriate
    style for this image.

    Since consistency is the highest priority, we will try to use the style
    from the previous image if it exists.
    """
    # do _any_ previous images have a style?  Use the nearest
    # previous image that has a style.  We ultimately fallthrough to no style.
    previous_image = image_xml.find_previous_sibling("image")
    while previous_image and not hasattr(previous_image, "attrs"):
        # skip any text at the paragraph level
        previous_image = previous_image.find_previous_sibling("image")

    if previous_image and "style" in previous_image.attrs:
        # if it does, use that.
        image_xml.attrs["style"] = previous_image.attrs["style"]
        log.info(
            f"Using style '{image_xml.attrs['style']}' from previous image for image {image_xml}"
        )


def condense_setting_prompt(prompt):
    condense_prompt = (
        f"Please condense the following description of a setting to {CLAIM_SETTING_LENGTH} tokens or less:\n\n"
        + prompt
    )
    condensed_setting = llm.str_prompt(condense_prompt)
    return condensed_setting


def generate_metadata_setting(image_xml, force=False):
    text = get_text_to_next(
        image_xml=image_xml, next_image_xml=image_xml.find_next("image")
    )

    surrounding_text = get_surrounding_paragraphs(
        image_xml.find_parent("paragraph"), context_min=600
    )

    previous_image = image_xml.find_previous_sibling("image")
    if previous_image and "setting" in previous_image.attrs:
        previous_setting = previous_image.attrs["setting"]
        prompt = (
            f"In {MAX_SETTING_LENGTH} tokens or less I want to describe the specific setting for one portion of a story."
            f"  The setting used for the previous image was, but we want more accurate details:\n\n{previous_setting}.\n"
            f"  The text we are basing our decision on is:\n\n{text}.\n\n"
            f"For context surrounding text is:\n\n{surrounding_text}.\n\n"
            f"What is the setting?  Try and use exactly {MAX_SETTING_LENGTH} tokens. "
            "Do not include the time of day. "
            "Only include the answer, no explaining or reasoning."
        )
    else:
        # setting
        prompt = (
            f"In {CLAIM_SETTING_LENGTH} characters or less I want to determine the setting for a portion of a story."
            f"  The text is: {text}.  "
            f"The surrounding text is: {surrounding_text}.  "
            f"What is the setting?  Do not exceed {CLAIM_SETTING_LENGTH} characters. "
            "Do not include the time of day. "
            "Only include the answer, no explaining or reasoning.  Be brief and concise."
        )

    setting = llm.str_prompt(
        prompt,
        system_prompt="You are a helpful assistant that generates concise and accurate settings for story illustrations.  You prioritize consistency across images and will reuse previous settings when appropriate.",
        force=force,
    )
    if len(setting) < MAX_SETTING_LENGTH:
        image_xml.attrs["setting"] = setting.strip()
    else:
        condensed_setting = condense_setting_prompt(setting)

        if len(condensed_setting) < MAX_SETTING_LENGTH:
            image_xml.attrs["setting"] = condensed_setting.strip()
        else:
            log.warning(
                f"Setting too long ({len(condensed_setting)} > {MAX_SETTING_LENGTH}) for image {image_xml}"
            )
            double_condensed = condense_setting_prompt(condensed_setting)
            if len(double_condensed) < MAX_SETTING_LENGTH:
                image_xml.attrs["setting"] = double_condensed.strip()
            else:
                log.warning(
                    f"Setting is STILL too long ({len(double_condensed)} > {MAX_SETTING_LENGTH}) for image {image_xml}"
                )

                log.warning("Initial setting[%s]: %s", len(setting), setting)
                log.warning(
                    "Condensed setting[%s]: %s",
                    len(condensed_setting),
                    condensed_setting,
                )
                log.warning(
                    "Double condensed setting[%s]: %s",
                    len(double_condensed),
                    double_condensed,
                )

                log.error(
                    f"Unable to condense settings to below {MAX_SETTING_LENGTH} tokens. "
                )


def generate_metadata_tod(image_xml):
    surrounding_text = get_surrounding_paragraphs(
        image_xml.find_parent("paragraph"), context_min=600
    )

    text = get_text_to_next(
        image_xml=image_xml, next_image_xml=image_xml.find_next("image")
    )

    tod_choices = [c[1] for c in TIME_OF_DAY_CHOICES]
    prompt = (
        f"I want to determine the time of day for a portion of a story."
        f"  The text is: {text}.  "
        f"The surrounding text is: {surrounding_text}.  "
        "What is the time of day?  The answer must be one of the following: "
        f"UNKNOWN, {', '.join(tod_choices)}.  "
        "Single word response only."
    )

    tod = llm.str_prompt(prompt)
    if tod in tod_choices:
        for c in TIME_OF_DAY_CHOICES:
            if c[1] == tod:
                tod = c[0]
        image_xml.attrs["tod"] = tod
    elif tod != "UNKNOWN":
        log.warning(
            f"Time of day '{tod}' is not in {TIME_OF_DAY_CHOICES} for image {image_xml}"
        )


def generate_metadata_camera(image_xml):
    surrounding_text = get_surrounding_paragraphs(
        image_xml.find_parent("paragraph"), context_min=600
    )

    text = get_text_to_next(
        image_xml=image_xml, next_image_xml=image_xml.find_next("image")
    )

    choices = []
    for cam in CAMERA_CHOICES:
        choices.append(f"{cam[0]}:  {cam[1]}")

    prompt = (
        f"I want to choose a good camera angle for a portion of a story."
        f"  The text is: {text}.  "
        f"The surrounding text is: {surrounding_text}.  "
        "What is the camera angle?  The answer must be one of the following:\n"
        f"{'\n'.join(choices)}\n"
        "Single response only."
    )

    camera = llm.str_prompt(prompt)
    valid_camera = [c[0] for c in CAMERA_CHOICES]
    clean_camera = camera.strip().lower().replace("-", "_").replace(" ", "_")
    if clean_camera in valid_camera:
        image_xml.attrs["camera"] = clean_camera
    else:
        log.warning(
            f"Camera angle '{clean_camera}' is not in {CAMERA_CHOICES} for image {image_xml}"
        )


def build_replacement_prompt(all_characters, bookdir, image_xml, build_clip=False):
    # start with meta_prompt
    prompt = image_xml.attrs.get(
        "meta_prompt", "[SETTING] [TOD] [CAMERA] [FOCUS_CHARACTER] [CHARACTERS]"
    )

    for sub in ["SETTING", "CAMERA", "TOD"]:
        if f"[{sub}]" in prompt:
            prompt = prompt.replace(f"[{sub}]", image_xml.attrs.get(sub.lower(), ""))

    if "[FOCUS_CHARACTER]" in prompt and image_xml.attrs.get("focus_character", ""):
        prompt = prompt.replace(
            "[FOCUS_CHARACTER]",
            "The focus is %s." % image_xml.attrs.get("focus_character", ""),
        )
    elif "[FOCUS_CHARACTER]" in prompt:
        # strip that out focker, we don't want it.  strip it!
        prompt = prompt.replace("[FOCUS_CHARACTER]", "")

    if "[COUNT]" in prompt:
        prompt = prompt.replace(
            "[COUNT]", str(len(image_xml.attrs.get("scene_characters", "").split(",")))
        )

    if "[CHARACTERS]" in prompt:
        character_list = []
        character_names = image_xml.attrs.get("scene_characters", "").split(",")
        random.shuffle(character_names)

        for character_name in character_names:
            if not character_name.strip():
                continue

            character_tag = characters.name_to_tag(character_name)
            log.info("Adding character %s to prompt", character_name)

            pose = image_xml.attrs.get(f"{character_tag}_pose", "")
            location = friendly_location(
                image_xml.attrs.get(f"{character_tag}_location", "")
            )
            action = image_xml.attrs.get(f"{character_tag}_action", "")

            cd = all_characters.get(character_tag)

            if image_xml.attrs.get(f"{character_tag}_description", ""):
                # use the character description provided for this image
                description = image_xml.attrs.get(f"{character_tag}_description", "")
            else:
                # use the default character description
                log.info(f"Using default description for {character_name}")
                cd = all_characters.get(character_tag)
                if cd:
                    description = f"{character_name} is {cd.get('description', '')}"

            if description:
                # save it for next time
                image_xml.attrs[f"{character_tag}_description"] = description

                if pose:
                    pose = " " + pose

                if location:
                    location = " " + location

                if action:
                    action = " " + action

                if pose and (location or action):
                    pose = " is " + pose

                character_list.append(
                    f"{character_name} a {cd.get('age', '')} {cd.get('gender', '')}{pose}{location}. {action}. {character_name}: {description}"
                )
            else:
                character_list.append(f"{character_name} is {pose} {location} {action}")

        prompt = prompt.replace("[CHARACTERS]", ", ".join(character_list))

    # prompt_filter = ""
    # negative_prompt = ""
    # if "style" in image_xml.attrs:
    #     style = image_xml.attrs["style"]
    #     prompt_filter, negative_prompt = styles.get_style(style)
    # else:
    #     book_style = config.get_config(chapterdir=bookdir)
    #     if "default_style" in book_style:
    #         style = book_style["default_style"]
    #         prompt_filter, negative_prompt = styles.get_style(style)

    # if prompt_filter:
    #     prompt = prompt_filter.format(prompt=prompt)

    # image_xml.attrs["t5_prompt"] = prompt
    image_xml.attrs["prompt"] = prompt
    # if negative_prompt.strip():
    #     image_xml.attrs["negative_prompt"] = negative_prompt.strip()

    if build_clip:
        # build the clip prompt based on the t5 prompt and the surrounding text
        image_xml.attrs["clip_prompt"] = text_to_image_clip_prompt(
            text=image_xml.attrs["t5_prompt"],
            paragraph_text=get_surrounding_paragraphs(
                image_xml.find_parent("paragraph")
            ),
            t5_prompt=image_xml.attrs["t5_prompt"],
            prompt_fn=os.path.abspath(
                os.path.join(
                    const.LIBRARY_DIR,
                    mybook.soup.find("paragraph").attrs["dir"].lstrip("/"),
                    f"img_{image_xml.attrs['fragdex']}.prompt",
                )
            ),
        )

        # pass the clip BACK through the style filter, otherwise it gets
        # too diluted.
        if prompt_filter:
            image_xml.attrs["clip_prompt"] = prompt_filter.format(
                prompt=image_xml.attrs["clip_prompt"]
            )


def generate_metadata_scene_characters(mybook, image_xml, chapterdir, force=False):
    """
    Which characters are potentially visible in this scene?
    """
    surrounding_text = get_surrounding_paragraphs(
        image_xml.find_parent("paragraph"), context_min=600
    )

    text = get_text_to_next(
        image_xml=image_xml, next_image_xml=image_xml.find_next("image")
    )

    all_character_names = characters.get_all_character_names(mybook, chapterdir)

    if "scene_characters" in image_xml.attrs:
        # Try a refining prompt.
        prior_scene_characters = image_xml.attrs["scene_characters"].split(",")
        prompt = (
            f"In {MAX_SETTING_LENGTH} tokens or less I want to determine which characters should be visible in a drawing for a portion of a story."
            f"  I think the answer might be:\n\n[{', '.join(prior_scene_characters)}]\n"
            f"  The text we are basing our decision on is:\n\n{text}.\n\n"
            f"For context surrounding text is:\n\n{surrounding_text}.\n\n"
            f"Available Characters are: {json.dumps(all_character_names)}\n"
            "Make sure this list of characters is correct.  Send the correct complete list of names in JSON format."
            "Do not include anything else."
        )

    else:
        prompt = (
            f"I want to determine which characters should be visible in a drawing for a portion of a story."
            f"  The text is:\n\n{text}\n\n"
            f"For context the text around the portion we are interested in is:\n\n{surrounding_text}\n\n"
            f"Available Characters are: {json.dumps(all_character_names)}\n"
            "List the characters in this scene as a JSON formatted list of names."
            "Do not include anything else."
        )

    # This is going to be tough.  We have character names (sorta) for the characters
    # that speak, but we don't know anything about characters that do not talk.
    #
    # I think we should build characters for them, we can leave out the voice
    # profile but we want physical descriptions to try and get some consistency.
    scene_characters = llm.json_prompt(prompt, force=force)
    clean_scene_characters = set()
    if not isinstance(scene_characters, list):
        if isinstance(scene_characters, dict) and "characters" in scene_characters:
            scene_characters = scene_characters["characters"]
        else:
            log.warning(
                f"Scene characters is not a list for image {image_xml}, got: {scene_characters}"
            )
            scene_characters = []

    clean_scene = set()
    for c in scene_characters:
        if isinstance(c, list):
            if len(c) == 1:
                clean_scene.add(str(c[0]).strip())
                continue

        if isinstance(c, dict) and "name" in c:
            clean_scene.add(c["name"].strip())
            continue

        clean_scene.add(str(c).strip())

    for c_name in clean_scene:
        if c_name.title() in [
            "Narrator",
        ]:
            continue

        if c_name.replace(" ", "_") in all_character_names:
            key = characters.name_to_tag(c_name.replace(" ", "_"))
            clean_scene_characters.add(key)

        elif c_name and c_name not in all_character_names:
            log.warning(
                'Unknown character: "%s" in scene characters for image %s',
                c_name,
                image_xml,
            )

            for character_name in all_character_names:
                if c_name.lower() in character_name.lower():
                    log.info(
                        f"Using partial match for scene character: {character_name}"
                    )
                    key = characters.name_to_tag(character_name)
                    clean_scene_characters.add(key)
                    break

        elif c_name:
            key = characters.name_to_tag(c_name)
            clean_scene_characters.add(key)

    as_list = list(clean_scene_characters)

    if as_list:
        image_xml.attrs["scene_characters"] = ",".join(as_list)


def generate_metadata_focus_character(mybook, image_xml, chapterdir):
    """
    Which character is the focus of this image?
    """
    surrounding_text = get_surrounding_paragraphs(
        image_xml.find_parent("paragraph"), context_min=600
    )

    all_character_names = characters.get_all_character_names(mybook, chapterdir)

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


def generate_metadata_character_pose(character, image_xml, chapter):
    """
    Choose a pose, location, and action for the character in this image.
    """
    surrounding_text = get_surrounding_paragraphs(
        image_xml.find_parent("paragraph"), context_min=600
    )

    text = get_text_to_next(
        image_xml=image_xml, next_image_xml=image_xml.find_next("image")
    )

    log.info(f"Character: {character}")

    prompt = (
        f"I want to determine how a character is positioned in a drawing for a portion of a story."
        f"  The text is: {text}.  "
        f"The surrounding text is: {surrounding_text}.  "
        f"The character is named {character['name']} and is described as: {character.get('description', '')}.  "
        "What is the best pose for this character?  The answer must be one of the following:\n"
        + "\n".join([f"{p[0]}: {p[1]}" for p in scene_const.POSE_OPTIONS])
        + "\nSingle JSON string response only."
    )

    pose = llm.str_prompt(prompt)
    valid_pose = [p[0] for p in scene_const.POSE_OPTIONS]
    clean_pose = pose.strip().lower().replace("-", "_").replace(" ", "_")
    if clean_pose in valid_pose:
        image_xml.attrs[f"{character['tag']}_pose"] = clean_pose
    else:
        log.warning(
            f"Pose '{clean_pose}' is not in {scene_const.POSE_OPTIONS} for character {character['name']} in image {image_xml}"
        )


def generate_metadata_character_location(character, image_xml, chapter):
    # location
    surrounding_text = get_surrounding_paragraphs(
        image_xml.find_parent("paragraph"), context_min=600
    )

    text = get_text_to_next(
        image_xml=image_xml, next_image_xml=image_xml.find_next("image")
    )

    prompt = (
        f"I want to determine where a character should be located in a drawing for a portion of a story."
        f"  The text is: {text}.  "
        f"The surrounding text is: {surrounding_text}.  "
        f"The character is named {character['name']} and is described as: {character.get('description', '')}.  "
        "Where should this character be located?  The answer must be one of the following:\n"
        "UR, UC, UL, SR, CS, SL, DR, DC, DL\n"
        "Single response only."
    )

    location = llm.str_prompt(prompt)
    valid_location = ["UR", "UC", "UL", "SR", "CS", "SL", "DR", "DC", "DL"]
    clean_location = location.strip().upper()
    if clean_location in valid_location:
        image_xml.attrs[f"{character['tag']}_location"] = clean_location


def _compress_action(
    character_dict, action
):  # try and make the action shorter if possible
    if len(action) > 200:
        prompt = (
            f"Condense the following physical description of what {character_dict['name']} is doing.  It must be reduced to 200 characters or less:\n\n"
            f"{action}\n\n"
        )
        condensed_action = llm.str_prompt(prompt)
        return condensed_action
    return action


def generate_metadata_character_action(character_dict, image_xml, force=False):
    """
    We want to know what this character is _doing_ at the moment in the story
    where image_xml is located.  It's going to inform the prompt used to
    generate the image and any subsequent animation.

    The hard part is figuring out how much of the story context we need for the
    AI to provide a sensible answer.

    Ooh, the smart thing, the clever thing, is to generate a _motivation_ for 
    the character, in addition to the actual action(s)
    """
    character_detail = llm.json_prompt(
        f"""
    Generate a concise description of what {character_dict['name']} is doing in
    the story at this moment.  Include any motivations if possible. Use the text
    of the story to inform your answer, but be concise. 
    
    The text is: {get_text_to_next(image_xml=image_xml,
    next_image_xml=image_xml.find_next('image'))}.  
    
    The surrounding context is:
    
    #### START
    {get_surrounding_paragraphs(image_xml.find_parent('paragraph'),
    context_min=600)}.  
    #### END
    
    Respond with a JSON object with two keys: 'action' and 'motivation'.  
    
    The only character we are interested in is {character_dict['name']}. 
    
    The value for 'action' should be a short description of what the character
    is doing, and the value for 'motivation' should be a short description of
    why they are doing it, if that can be inferred from the text.
    """.strip()
    )
    log.info(
        "Generated Character detail",
        name=character_dict['name'],
        detail=character_detail,
    )
    if not isinstance(character_detail, dict):
        log.warning(
            "Character detail is not a dict", 
            name=character_dict['name'],
            character_detail=character_detail,
        )
    else:
        log.info(
            "Character detail is a dict",
            name=character_dict['name'],
            keys=character_detail.keys(),
        )

    return character_detail

def generate_metadata_character_action_old(character_dict, image_xml, force=False):
    """
    We have a character, we have an <image> tag in its context within the chapter xml.
    And force, to bypass caching.
    """
    character_tag = character_dict["tag"]
    # action
    surrounding_text = get_surrounding_paragraphs(
        image_xml.find_parent("paragraph"), context_before=600, context_after=0
    )

    text = get_text_to_next(
        image_xml=image_xml, next_image_xml=image_xml.find_next("image")
    )
    log.info('Text for action: "%s"', text)

    # do we have a customized description for this character?
    character_description = image_xml.attrs.get(
        f"{character_dict['tag']}_description", ""
    )
    if character_description == "":
        # use the characters default description instead
        character_description = character_dict.get("description", "")

    # "{character_dict['name']} is described as:
    # {character_description}.

    # dedent to reduce token waste
    prompt = textwrap.dedent(f"""
    I want to determine what "{character_dict["name"]}" is doing.  Not what they should do, 
    what they are actually doing in this small portion of the story.  I am 
    providing both the small piece of the story and the surrounding context 
    to help you provide a carefully considered high quality response.  
    Focus on this small text it is the most critically important. Only hint 
    at anything that comes after the small text.

    The small text is:
    
    {text}
    
    The surrounding text is: 
    
    {surrounding_text}.  

    Describe what {character_dict['name']} is doing and their emotional state.  Respond with a short sentence describing the action.
    """)

    action = llm.str_prompt(prompt, force=force)
    log.info('Action for character %s: "%s"', character_tag, action)
    if action and len(action) < 200:
        image_xml.attrs[f"{character_tag}_action"] = action.strip()
        return action.strip()
    else:
        log.warning(
            f"Action '{action}' is too long for "
            f"character {character_tag} in image {image_xml}"
        )

        # we will give it a try.
        compressed_action = _compress_action(character_dict, action)
        if len(compressed_action) < 200:
            log.info(
                f"Success! Compressed action for character {character_tag} is: {compressed_action}"
            )
            image_xml.attrs[f"{character_tag}_action"] = compressed_action.strip()
            return compressed_action.strip()
        else:
            log.warning(
                f"Compressed action is STILL too long "
                f"({len(compressed_action)} > 200) for "
                f"character {character_tag} in image "
                f"{image_xml}"
            )


def generate_metadata_from_text(chapter, image_xml, force_all=False):
    # style
    if "style" not in image_xml.attrs or force_all:
        generate_metadata_style(image_xml)

    # determine if we need to do textual analysis.
    textual_analysis = False
    for tag in ["setting", "tod", "camera", "focus_character", "scene_characters"]:
        if tag not in image_xml.attrs:
            textual_analysis = True

    all_characters = characters.get_all_characters(
        chapter,
    )

    if textual_analysis or force_all:
        # we need to do textual analysis.
        # use the text_to_next to determine the setting, tod, camera, # focus_character, and scene_characters.
        if "setting" not in image_xml.attrs or force_all:
            generate_metadata_setting(image_xml)

        if "tod" not in image_xml.attrs or force_all:
            # time of day
            generate_metadata_tod(image_xml)

        if "camera" not in image_xml.attrs or force_all:
            # camera
            generate_metadata_camera(image_xml)

        if "scene_characters" not in image_xml.attrs or force_all:
            # scene characters
            generate_metadata_scene_characters(chapter, image_xml)

        if ("scene_characters" in image_xml.attrs) or force_all:
            # make sure the scene characters are valid.
            valid = set()
            for character_name in image_xml.attrs.get("scene_characters", "").split(
                ","
            ):
                character_name = character_name.strip()

                if character_name and character_name not in all_characters.keys():
                    log.warning(
                        'Unknown character: "%s" in scene characters for image %s',
                        character_name,
                        image_xml,
                    )
                elif character_name:
                    valid.add(character_name)

            image_xml.attrs["scene_characters"] = ",".join(sorted(valid))
            log.info(
                "Updated scene characters for image %s: %s",
                image_xml,
                image_xml.attrs["scene_characters"],
            )

        if (
            "scene_characters" in image_xml.attrs
            and "focus_character" not in image_xml.attrs
        ) or force_all:
            # focus character
            generate_metadata_focus_character(chapter, image_xml)

    # characters may have been created, reload them
    log.info("Reloading all characters for chapter %s", chapter)
    all_characters = characters.get_all_characters(chapter)
    for c in list(all_characters.keys()):
        # index by both name and tag
        all_characters[all_characters[c]["name"]] = c

    # remove any empty character names from scene_characters
    if "scene_characters" in image_xml.attrs:
        clean_scene_characters = set()
        for character_name in image_xml.attrs.get("scene_characters", "").split(","):
            character_name = character_name.strip()
            if character_name:
                clean_scene_characters.add(character_name)

        image_xml.attrs["scene_characters"] = ",".join(sorted(clean_scene_characters))

        for character_name in image_xml.attrs["scene_characters"].split(","):
            character_name = character_name.strip()

            character_dict = characters.get_character(chapter, character_name)
            if character_dict:
                log.info(f"Found character {character_name} in {chapter}")
                # choose an appropriate pose, location, and action
                generate_metadata_character_pose(character_dict, image_xml, chapter)
                generate_metadata_character_location(character_dict, image_xml, chapter)
                generate_metadata_character_action(character_dict, image_xml)

            else:
                log.warning(f"Character {character_name} not found")

            # what the hell is this for?
            if character_name == "disabled":
                # make sure the character exists.
                if character_name not in all_characters:
                    log.info(f"Creating character {character_name} in {chapter}")
                    character_tag = characters.name_to_tag(character_name)

                    chardict = {
                        "name": character_name,
                        "tag": character_tag,
                        "description": "",
                        "gender": "",
                        "age": "",
                    }

                    characters.add_character(
                        chapter=chapter,
                        character_name=character_name,
                        chardict=chardict,
                    )

                    # avoid dupes all_characters[character_name] = chardict
                    all_characters[character_tag] = chardict
    else:
        log.info("No scene characters for image %s", image_xml)

    # per character: # pose # location # action

    # save per-image # mybook.save_xml()


def text_to_image_clip_prompt(text, paragraph_text, t5_prompt, prompt_fn):
    if os.path.exists(prompt_fn):
        os.unlink(prompt_fn)

    with open("drawing.fifo", "a") as fifo:
        fifo.write(
            json.dumps(
                [
                    "text_to_image_clip_prompt",
                    text,
                    paragraph_text,
                    t5_prompt,
                    prompt_fn,
                ]
            )
            + "\n\n"
        )

    log.info(f"Waiting for {prompt_fn} to generate...")
    wait_for(prompt_fn)

    if os.path.exists(prompt_fn):
        log.info("Prompt file exists!")
        with open(prompt_fn, "r") as h:
            prompt = h.read()
    return prompt


def get_image_fn(prompt, loras, paragraph_dir, image_index, randomized=True):
    tag = get_tag(prompt, loras, randomized=randomized)
    image_fn = os.path.join(paragraph_dir, f"img_{image_index}_{tag}.png")
    return image_fn


def get_flux_image(image_fn="", clip_prompt="", t5_prompt="", force=False):
    """
    FLUX based image generation with seperate CLIP and T5 prompts.
    """
    log.info(f"get_flux_image({image_fn=}, {clip_prompt=}, {t5_prompt=})")

    if not force and os.path.exists(image_fn):
        log.debug(f"Re-using existing image {image_fn}")
        image = load_image(image_fn)
        return image, False
    else:
        log.debug(f"Image {image_fn} does not exist.")

    log.info(f"({force}) Creating new image {image_fn}")

    # open the "draw a new AI image" fifo.  So this is just prompt-drawing
    # as a simple service.  dump a properly formatted string (json list of
    # the prompt text and a string for the path and filename of the image
    # you want to write) to the FIFO.  The service side reads one entry at a
    # time, renders it, writes it to disk then starts the next entry.  Perfectly
    # suited to a local rough render with modest GPU support.
    #

    if os.path.exists(image_fn + ".tmp.png"):
        os.unlink(image_fn + ".tmp.png")

    # fmt: off
    redis.Redis(host="redis").rpush("gpu_tasks", json.dumps(["get_flux_image", image_fn + ".tmp.png", clip_prompt, t5_prompt]))
    # fmt: on

    log.debug(f"Waiting for {image_fn}.tmp.png to generate...")
    wait_for(image_fn + ".tmp.png")
    log.debug("Image Exists!")
    # give the image a moment to finish writing, when the sequence is
    # too tight we get truncated images.
    time.sleep(0.5)

    image = load_image(image_fn + ".tmp.png")

    # image.save(image_fn)
    # os.unlink(image_fn + ".tmp.png")
    shutil.move(image_fn + ".tmp.png", image_fn)

    my_art = AsciiArt.from_pillow_image(image)
    my_art.to_terminal()

    return image, True


def assign_fragdex_and_index(chapter):
    """
    Assigns the 'fragdex' attribute to all images in the book.
    This is used to ensure that images have a unique fragment index.
    """
    soup = chapter.get_xml()
    for paragraph in soup.findAll("paragraph"):
        used_fragdex = set()
        for image_xml in paragraph.findAll("image"):
            used_fragdex.add(int(image_xml.get("fragdex", 0)))

        fragdex = 0
        while fragdex in used_fragdex:
            fragdex += 1

        for image_xml in paragraph.findAll("image"):
            if "fragdex" not in image_xml:
                image_xml["fragdex"] = str(fragdex)
                used_fragdex.add(fragdex)
                log.info(
                    f"Assigning fragdex {fragdex} for image {image_xml.attrs.get('src', '')} in paragraph {paragraph.get('dir', '')}"
                )

            elif image_xml["fragdex"] != str(fragdex):
                # this isn't a problem; we just don't want to assign a fragdex that is already in use.
                # it's _helpful_ for fragdex to be incrementing, but not really required.
                fragdex = int(image_xml["fragdex"])

            while fragdex in used_fragdex:
                fragdex += 1

    for image_index, image_xml in enumerate(soup.findAll("image")):
        image_xml["index"] = int(image_index)

    chapter.save_xml()

    # used_index = set()
    # for image_xml in soup.findAll('image'):
    #     if 'index' in image_xml.attrs:
    #         used_index.add(int(image_xml['index']))

    # index = 0
    # for image in soup.findAll('image'):
    #     if 'index' not in image.attrs:
    #         image['index'] = str(index)
    #         used_index.add(index)
    #         log.info(f"Assigning index {index} for image {image.attrs.get('src', '')}")

    #     elif image['index'] != str(index):
    #         log.warning(f"Image index misalignment. {image['index']} != {index} for image {image.attrs.get('src', '')}")

    #     while index in used_index:
    #         index += 1


index_to_image = {}
index_to_paragraph = {}
index_to_text = {}


def get_xml_for_image(bookdir, mybook, image_index):
    # mybook = get_book(bookdir)
    # if mybook.soup is None:
    #     log.error('Book.xml failed to load')
    #     return None, None, None, None

    index = 0
    accumulated_text = ""

    if not index_to_image:
        log.info("Generating index_to_image{}")
        paragraph_index = 0
        for paragraph in mybook.soup.find("book").children:
            # anything at the paragraph level must have text contents
            # or it won't consume any time, so it isn't meaningful.
            if not paragraph.get_text().strip():
                continue

            log.info(f"Processing paragraph {paragraph_index}: {paragraph}")
            image = None
            if "dir" not in paragraph.attrs:
                paragraph.attrs["dir"] = os.path.join(
                    bookdir, "paragraphs", f"{paragraph_index:0622}"
                )

            paragraph_dir = paragraph.attrs["dir"].lstrip("/")
            os.makedirs(os.path.join(const.LIBRARY_DIR, paragraph_dir), exist_ok=True)

            # if there is some other object in here we will ignore it.
            if paragraph.name == "paragraph":
                fragdex = 0
                for fragment in paragraph.contents:
                    if fragment is not None:
                        fragdex += 1

                        if fragment.name == "image":
                            # an images indicate the end of one image region and the
                            # beginnng of the next image region.
                            fragment.attrs["fragdex"] = fragdex

                            image = fragment
                            image.attrs["index"] = index

                            index_to_image[index] = image
                            index_to_paragraph[index] = paragraph

                            index_to_text[index - 1] = accumulated_text
                            accumulated_text = ""

                            index += 1
                        elif fragment.name == "phrase":
                            accumulated_text += fragment.get_text()

            paragraph_index += 1

        index_to_text[index - 1] = accumulated_text
        mybook.save_xml()

    try:
        image = index_to_image[image_index]
    except KeyError:
        log.error(
            f"Could not find image for image_index {image_index} in book {bookdir}"
        )
        return None, None, None, None

    try:
        paragraph = index_to_paragraph[image_index]
    except KeyError:
        log.error(
            f"Could not find paragraph for image_index {image_index} in book {bookdir}"
        )
        return None, None, None, None

    try:
        text_to_next = index_to_text[image_index]
    except KeyError:
        log.error(
            f"Could not find text for image_index {image_index} in book {bookdir}"
        )
        return None, None, None, None

    return mybook, image, paragraph, text_to_next


@lru_cache(maxsize=8)
def load_image(filename):
    log.debug(f"[Cache Miss] Loading image {filename}")
    if not os.path.exists(filename):
        log.error(f"Image file {filename} does not exist")
        raise UnidentifiedImageError("File does not exist")

    try:
        image = Image.open(filename)
        image.verify()  # Verify that it is, in fact an image
    except Exception as e:
        log.error(f"Error opening image {filename}: {e}")
        raise UnidentifiedImageError("Error opening image")

    try:
        image = Image.open(filename)
        image.load()
    except Exception as e:
        log.warning(f"Error loading image {filename}: {e}, retrying...")
        time.sleep(0.5)

        try:
            image = Image.open(filename)
            image.load()
        except Exception as e:
            log.error(f"Error loading image {filename}: {e}")
            raise UnidentifiedImageError("Error loading image")

    return image


# def get_image_by_index(image_index, frame_index=None):
#     log.info('Retrieving image by index: %s', image_index)

#     image_xml = all_images[image_index]

#     if "/" in image_xml.attrs.get("src", ""):
#         image_xml.attrs['src'] = image_xml.attrs['src'].split('/')[-1]

#     if frame_index:
#         image_fn = os.path.join(
#             const.LIBRARY_DIR,
#             image_xml.find_parent("paragraph").attrs['dir'],
#             "image_frames",
#             f"image_{image_index:06}",
#             f"frame_{frame_index:06}"
#         )
#     else:
#         image_fn = os.path.join(
#             const.LIBRARY_DIR,
#             image_xml.find_parent("paragraph").attrs['dir'],
#             image_xml.attrs.get("src", "")
#         )

#     # this is a problem, but an easy one to solve.
#     # we just don't want every process to do it at the same time.

#     if not os.path.exists(image_fn + '.adj.png'):
#         with open("/tmp/neobreaker-image-adjust.lock", "a") as f:
#             fcntl.flock(f, fcntl.LOCK_EX)
#             # check again, maybe someone else did it while we waited for the
#             # lock.
#             if not os.path.exists(image_fn + '.adj.png'):
#                 log.info(f'Created adjusted image for {image_fn}')
#                 image_dict = {}
#                 if image_xml.attrs.get('recenter_x1', None):
#                     image_dict['recenter'] = {
#                     'x1': image_xml.attrs.get('recenter_x1', None),
#                     'y1': image_xml.attrs.get('recenter_y1', None)
#                 }

#                 image_dict['fullscreen'] = image_xml.attrs.get('fullscreen', False)

#                 log.info('transition path')
#                 imaginative_image, image_fn = apply_image_adjustments(
#                     image_pfn=image_fn,
#                     image_dict=image_dict
#                 )
#             else:
#                 # it does exist now. great!
#                 image_fn = image_fn + '.adj.png'
#             fcntl.flock(f, fcntl.LOCK_UN)
#     else:
#         image_fn = image_fn + '.adj.png'

#     return image_xml, image_fn


def create_transition(image_index, chapter, force=False):
    """
    Use the metadata associated with this image in the book XML to create a
    transition between the previous image and the selected image.
    """
    image_index = int(image_index)

    all_images = chapter.get_xml().findAll("image")

    # image_xml, image_fn = get_image_by_index(image_index, frame_index=0)
    image_xml = all_images[image_index]

    frame_directory = os.path.join(
        const.LIBRARY_DIR,
        chapter.chapterdir,
        "transitions",
        f"transition_{image_index:06}",
    )

    filename = os.path.join(
        const.LIBRARY_DIR,
        chapter.chapterdir,
        "transitions",
        f"transition_{image_index:06}.mp4",
    )

    if os.path.exists(filename) and not force:
        log.info(
            f"Transition frame directory {frame_directory} already exists, reusing it."
        )
        return filename

    transition_type = image_xml.attrs.get("transition_type", "")

    if transition_type:
        transition_obj = transitions.registry.get(transition_type)

        transition = transition_obj()
        log.info(f"Got transition {transition} for type {transition_type}")

        # where exactly should we keep these frames so the video assemly
        # process can smoothly integrate them?
        # ok, so I'm bundeling them here because it will be fun to browse.

        os.makedirs(frame_directory, exist_ok=True)

        config_dict = {}
        previous_image = all_images[image_index - 1] if image_index > 0 else None

        image_fn = os.path.join(
            const.LIBRARY_DIR,
            image_xml.find_parent("paragraph").attrs["dir"].lstrip("/"),
            os.path.basename(image_xml.attrs["src"]),
        )

        if previous_image:
            previous_paragraph = previous_image.find_parent("paragraph")
            previous_image_fn = os.path.join(
                const.LIBRARY_DIR,
                previous_paragraph.attrs["dir"].lstrip("/"),
                os.path.basename(previous_image.attrs["src"]),
            )
            log.info(
                f'{previous_image_fn=} {const.LIBRARY_DIR=} {previous_paragraph.attrs['dir']=} {previous_image.attrs['src']=}'
            )
        else:
            previous_image_fn = None

        try:
            transition.apply(
                old_image=previous_image_fn,
                new_image=image_fn,
                frame_directory=frame_directory,
                config_dict=config_dict,
            )
        except Exception as e:
            log.error(
                f"Error applying transition {transition_type} between {previous_image_fn} and {image_fn}: {e}"
            )

            for image_name in [previous_image_fn, image_fn]:
                if not os.path.exists(image_name):
                    log.error(f"Image file {image_name} does not exist.")
                    return

                try:
                    i = Image.open(image_name)
                    i.verify()
                except Exception as ie:
                    log.error(f"Error verifying image {image_name}: {ie}")
                    os.unlink(image_name)

            raise e

        video.video.assemble_mp4(
            fps=const.FPS,
            framedir=frame_directory,
            wavfile=None,
            videofile=filename,
            image_match="frame_%04d.png",
        )

    transition_done = os.path.join(frame_directory, "done.flag")
    if os.path.exists(frame_directory):
        with open(transition_done, "w") as f:
            f.write(
                f"Transition {transition_type} applied, video assembled to {filename}\n"
            )

    return filename


def create_animation(
    chapter, image_xml, video_index=0, extend=False, force=False, default_method=None
):
    """
    Use the metadata associated with this image in the book XML to create a
    animation based on the image and the image_xml metadata
    """
    # dirtag = "animation"

    # image_index = int(image_index)
    # mybook = booklib.get_book(chapterdir)

    # all_images = mybook.soup.findAll('image')

    # def get_image_by_index(image_index):
    #     """
    #     We're applying some corrections since we're in
    #     here to ease the pain of some past sins.
    #     """
    #     log.info('Retrieving image by index: %s', image_index)

    #     image_xml = all_images[image_index]

    #     if "/" in image_xml.attrs.get("src", ""):
    #         image_xml.attrs['src'] = image_xml.attrs['src'].split('/')[-1]

    #     image_fn = os.path.join(
    #         const.LIBRARY_DIR,
    #         image_xml.find_parent("paragraph").attrs['dir'],
    #         image_xml.attrs.get("src", "")
    #     )

    #     # this is a problem, but an easy one to solve.
    #     if not os.path.exists(image_fn + '.adj.png'):
    #         log.info(f'Created adjusted image for {image_fn}')

    #         image_dict = {}
    #         if image_xml.attrs.get('recenter_x1', None):
    #             image_dict['recenter'] = {
    #                 'x1': image_xml.attrs.get('recenter_x1', None),
    #                 'y1': image_xml.attrs.get('recenter_y1', None)
    #             }
    #         log.info('animation path')
    #         imaginative_image, image_fn = apply_image_adjustments(
    #             image_pfn=image_fn + '.adj.png',
    #             image_dict=image_dict
    #         )
    #     else:
    #         image_fn = image_fn + '.adj.png'

    #     return image_xml, image_fn

    # image_xml, image_fn = get_image_by_index(image_index)
    # image_xml = all_images[image_index]

    video_tag = f"_{video_index:02d}"

    # this should resolve to a specific animation
    my_method = image_xml.attrs.get(f"animation_method{video_tag}", default_method)
    image_index = int(image_xml.attrs["index"])

    # if my_method == "comfy_ui":
    #     # use the comfy UI pipeline for this animation.
    # my_method = image_xml.attrs[f"workflow_animation_template{video_tag}"] = "LTX23"

    # if not my_method:
    #     log.error(f"Image XML: {image_xml}")
    #     raise ValueError(
    #         f"No animation_method found in image XML for index {image_index}."
    #     )

    paragraph = image_xml.find_parent("paragraph")
    paragraph_dir = chapter.get_paragraph_dir(paragraph.attrs["index"])

    frame_directory = os.path.join(
        const.LIBRARY_DIR, paragraph_dir, "animation", f"image_{image_index:06}"
    )

    filename = image_xml.attrs["src"].replace(".png", ".mp4")

    if not extend and not force and os.path.exists(filename):
        log.info(f"Animation video {filename} already exists, reusing it.")
        return filename

    os.makedirs(frame_directory, exist_ok=True)
    active_obj = animations.registry.get(my_method)

    animation = active_obj()
    log.info(f"Got animation {animation} for type {my_method}")

    # animations/wan_2_2_5b/animate.py
    animation.apply(
        chapter=chapter,
        image_xml=image_xml,
        frame_directory=frame_directory,
        extend=extend,
        prompt_enhance=None,
        # image_xml.attrs.get("animation_prompt_enhance", "false").lower() == "false",
    )
    # elif dirtag == "transition":
    #     active_obj = transitions.registry.get(my_method)

    #     transition = active_obj()
    #     log.info(f"Got transition {transition} for type {my_method}")

    #     config_dict = {}

    #     old_image_xml, old_image_fn = get_image_by_index(image_index - 1)
    #     new_image_xml, new_image_fn = get_image_by_index(image_index)

    #     transition.apply(
    #         old_image=old_image_fn,
    #         new_image=new_image_fn,
    #         frame_directory=frame_directory,
    #         config_dict=config_dict
    #     )

    video.video.assemble_mp4(
        fps=const.FPS,
        framedir=frame_directory,
        wavfile=None,
        videofile=filename,
        image_match="%06d.png",
    )

    transition_done = os.path.join(frame_directory, "done.flag")
    if os.path.exists(frame_directory):
        with open(transition_done, "w") as f:
            f.write(f"Transition {my_method} applied, video assembled to {filename}\n")

    return filename


def zmi_regenerate_image(
    mybook,
    author,
    title,
    chapter,
    image_xml,
    chapterdir,
    bookdir,
):
    """
    FIXME
    z-index-specific pipeline
    """
    generate_metadata_from_text(
        chapterdir,
        image_xml,
        force_all=True,
    )
    mybook.save_xml()

    # start with all global characters
    all_characters = characters.get_all_characters(chapter, is_global=True)

    # update with all chapter-specific characters
    all_characters.update(characters.get_all_characters(chapter, is_global=False))

    build_replacement_prompt(all_characters, bookdir, image_xml)
    mybook.save_xml()

    prompt = image_xml.attrs.get("prompt", "")
    paragraph = image_xml.find_parent("paragraph")

    image_fn = get_image_fn(
        prompt=f"{prompt}",
        loras=[],
        paragraph_dir=paragraph.attrs["dir"],
        image_index=image_xml.attrs["index"],
    )
    image_xml.attrs["src"] = image_fn

    # draw the image
    image_module = t2i_registry.get("tsqn.zimageturbo")
    image_module().generate_image(image_xml)
    mybook.save_xml()

    # get_flux_image(
    #     image_fn=os.path.join(
    #         const.LIBRARY_DIR,
    #         image_fn
    #     ),
    #     clip_prompt=clip_prompt,
    #     t5_prompt=t5_prompt,
    #     force=False
    # )


def tmi_regenerate_image(
    mybook,
    author,
    title,
    chapter,
    image_xml,
    chapterdir,
    bookdir,
):
    """
    FIXME
    flux-specific pipeline
    """
    generate_metadata_from_text(
        chapterdir,
        image_xml,
        force_all=True,
    )
    mybook.save_xml()

    # start with all global characters
    all_characters = characters.get_all_characters(chapter, is_global=True)

    # update with all chapter-specific characters
    all_characters.update(characters.get_all_characters(chapter, is_global=False))

    build_replacement_prompt(all_characters, bookdir, image_xml)
    mybook.save_xml()

    clip_prompt = image_xml.attrs.get("clip_prompt", "")
    t5_prompt = image_xml.attrs.get("t5_prompt", "")

    paragraph = image_xml.find_parent("paragraph")

    image_fn = get_image_fn(
        prompt=f"{clip_prompt}_{t5_prompt}",
        loras=[],
        paragraph_dir=paragraph.attrs["dir"],
        image_index=image_xml.attrs["index"],
    )
    image_xml.attrs["src"] = image_fn

    get_flux_image(
        image_fn=os.path.join(const.LIBRARY_DIR, image_fn),
        clip_prompt=clip_prompt,
        t5_prompt=t5_prompt,
        force=False,
    )

    mybook.save_xml()


def text_to_image_prompt(text, paragraph_text, meta_prompt="", prompt_fn=None):
    """
    Generate a prompt for the image based on the text and surrounding context.
    """
    # call the LLM via the gpu fifo
    prompt = llm.generate_image_prompt(
        text=text,
        paragraph_text=paragraph_text,
        meta_prompt=meta_prompt,
        prompt_fn=prompt_fn,
    )

    return prompt


def text_to_prompt(image, paragraph, text_to_next, enhanced=False):
    image_prompt_type = random.choice(
        [
            "basic",
            "augmented",
        ]
    )

    if image_prompt_type == "basic":
        prompt = (
            "Draw a highly detailed intricate whimsical painting with no words or letters illustrating the meaning of this text: %s"
            % text_to_next
        )

    elif image_prompt_type == "augmented":
        # who better to make a prompt for AI than another AI?
        prompt_fn = os.path.abspath(
            os.path.join(
                const.LIBRARY_DIR,
                paragraph.attrs["dir"],
                f"img_{image.attrs["fragdex"]}.prompt",
            )
        )

        prompt = text_to_image_prompt(
            text=text_to_next,
            paragraph_text=get_surrounding_paragraphs(paragraph),
            meta_prompt="",
            prompt_fn=prompt_fn,
        )

        if enhanced:
            prompt = images.prompt_enhance(
                prompt=prompt,
                prompt_fn=os.path.abspath(
                    os.path.join(
                        const.LIBRARY_DIR,
                        paragraph.attrs["dir"].lstrip("/"),
                        f"img_{image.attrs["fragdex"]}_enhanced.prompt",
                    )
                ),
            )

    image.attrs["t5_prompt"] = prompt
    return prompt
