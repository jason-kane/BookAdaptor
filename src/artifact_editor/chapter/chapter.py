import base64
import glob
import json
import math
import os
import re
import shutil
import subprocess
from hashlib import md5
from typing import Any, Generator, Iterable
import uuid

import numpy as np
from bs4 import BeautifulSoup, ResultSet, Tag
from flask import g, url_for
from PIL import Image

import comfy
import const
import logger
from artifact_editor import (
    camera,
    config,
    tools,
)
from artifact_editor.author.author import Author
from artifact_editor.masterplan import masterplan
from artifact_editor.phrases.pronunciation.pronunciation import (
    get_global_pronunciations,
)
from artifact_editor.styles import styles
from artifact_editor.textoviva.textoviva import Textoviva
from artifact_editor.tools import as_float, as_int, as_str, tags_to_dict
from artifact_editor.types import aspect
from artifact_editor.typography import page_segment

from artifact_editor.characters import characters

log = logger.log(__name__)


def chapter_url_defaults_helper(endpoint: str, values: dict[str, str | int]):
    """
    Automatically add author, title, chapter_number, and language to the URL
    if they are not already present.
    """
    log.info("bp.url_defaults() called", endpoint=endpoint, values=values)

    if "author" not in values and hasattr(g, "author"):
        values["author"] = g.author.name
    if "title" not in values and hasattr(g, "title"):
        values["title"] = g.title
    if "chapter_number" not in values and hasattr(g, "chapter_number"):
        values["chapter_number"] = int(g.chapter_number)
    if "language" not in values and hasattr(g, "language"):
        values["language"] = g.language
    return endpoint, values


def chapter_url_value_preprocessor(endpoint: str, values: dict[str, str | int]):
    """
    Extract author, title, chapter_number, and language from the URL and store
    them in the Flask global context (g).
    """
    log.info("Pulling higher nesting values", endpoint=endpoint, values=values)
    if "author" in values:
        g.author = Author(str(values.pop("author")))
    if "title" in values:
        g.title = str(values.pop("title"))
    if "chapter_number" in values:
        g.chapter_number = int(values.pop("chapter_number"))
    if "language" in values:
        g.language = str(values.pop("language"))
    g.chapter = Chapter(
        author=g.author,
        title=g.title,
        number=g.chapter_number,
        language=g.language,
    )


def phrase_images(
    paragraph_dir: str, aspect: str, phrase_index: int
) -> Generator[Any, Any, Any]:
    """
    Generator that yields back all the text_layer image filenames [first...last]
    with this phrase_id highlighted.  Not doing any real work, this bit is fast.
    """
    at_least_one = False
    aspect_dir = os.path.join(const.LIBRARY_DIR, paragraph_dir.lstrip("/"), aspect)

    if os.path.exists(os.path.join(aspect_dir, f"text_layer_{phrase_index}.png")):
        yield os.path.join(aspect_dir, f"text_layer_{phrase_index}.png")
        return

    for image in sorted(
        glob.glob(os.path.join(aspect_dir, f"text_layer_{phrase_index}-*.png"))
    ):
        if "done" in image:
            # just skip it
            continue

        if os.path.exists(os.path.join(aspect_dir, image)):
            at_least_one = True
            yield image
        else:
            log.warning("Disappearing image: %s", image)

    if not at_least_one:
        log.warning(
            "No images found for %s"
            % os.path.join(aspect_dir, f"text_layer_{phrase_index}-*.png")
        )


class Phrase:
    def __init__(self, chapter: "Chapter", phrase_xml: Tag):
        log.info("NEW Phrase(chapter, %s)", phrase_xml)
        self.attrs = []
        for attr in phrase_xml.attrs:
            self.attrs.append(attr)
            setattr(self, attr, phrase_xml.attrs[attr])

        self.chapter = chapter
        self.phrase_xml = phrase_xml
        phrase_index = self.phrase_xml.attrs.get("index", "-1")
        # strong
        self.index = as_int(phrase_index)

        # wimpy
        self.phrase_index = self.index

    def simple_text(self) -> str:
        text = self.phrase_xml.text or ""
        # text = text.replace('\n', '\\n')
        return text

    def get_highlight_dimensions(self, aspect: str, force: bool) -> dict[str, int]:
        """
        Iterate chapter images accumulating height until we find the highlighted
        phrase.  Return a ct_layer dictionary with top/right/bottom/left pixel
        indices.
        """
        if not force:
            try:
                return {
                    "top": as_int(self.phrase_xml.attrs["top"]),
                    "bottom": as_int(self.phrase_xml.attrs["bottom"]),
                    "left": as_int(self.phrase_xml.attrs["left"]),
                    "right": as_int(self.phrase_xml.attrs["right"]),
                }
            except KeyError:
                log.info("No highlight dimensions cached, calculating from images...")

        paragraph = self.phrase_xml.find_parent("paragraph")
        if paragraph is None:
            raise ValueError("Phrase is not inside a paragraph.")
        paragraph_dir = self.chapter.get_paragraph_dir(as_int(paragraph.attrs["index"]))
        total_height = 0

        all_phrase_images = list(
            phrase_images(
                paragraph_dir,
                aspect,
                self.phrase_index,
            )
        )
        log.info(f"{all_phrase_images=}")

        if not all_phrase_images:
            fn = f"{paragraph_dir}/{aspect}/text_layer_{self.phrase_index}*.png"
            log.warning(f"No phrase images found matching glob: {fn}")

        for fn in all_phrase_images:
            log.info("Examining %s for highlight block...", fn)
            img = Image.open(fn)

            black_bg = Image.new("RGBA", img.size, "BLACK")
            img = Image.alpha_composite(black_bg, img)

            bbox = img.getchannel("R").getbbox()
            if bbox is None:
                log.info(f"Skipping {fn=}, it has no highlighted region.")
                # there is no highlighted text in this image.
                # move on to the next one.
                total_height += img.height
                continue
            else:
                # TODO:
                #   what if the highlight spans pages?
                #
                hleft, htop, hright, hbottom = bbox
                # this is good for top/bottom, but left/right need some fancy.
                # no, this is shit.

                topstrip = img.crop((hleft, htop, hright, htop + 1))
                topstrip_bb = topstrip.getchannel("R").getbbox()
                if topstrip_bb is None:
                    raise ValueError(f"Top strip has no highlighted region in {fn}")

                left, _, _, _ = topstrip_bb

                bottomstrip = img.crop((hleft, hbottom - 1, hright, hbottom))
                bottomstrip_bb = bottomstrip.getchannel("R").getbbox()
                if bottomstrip_bb is None:
                    raise ValueError(f"Bottom strip has no highlighted region in {fn}")
                _, _, right, _ = bottomstrip_bb

                self.phrase_xml.attrs["top"] = str(total_height + htop)
                self.phrase_xml.attrs["bottom"] = str(total_height + hbottom)
                self.phrase_xml.attrs["left"] = str(left)
                self.phrase_xml.attrs["right"] = str(right)
                break

        return {
            "top": as_int(self.phrase_xml.attrs["top"]),
            "bottom": as_int(self.phrase_xml.attrs["bottom"]),
            "left": as_int(self.phrase_xml.attrs["left"]),
            "right": as_int(self.phrase_xml.attrs["right"]),
        }


class Speaker:
    def __init__(self, **kwargs: str):
        # Sinner!
        self.args = []
        for arg in kwargs:
            self.args.append(arg)
            setattr(self, arg, kwargs[arg])

    def __repr__(self) -> str:
        return f"Speaker({', '.join([f'{arg}={getattr(self, arg)}' for arg in self.args])})"

# override if you want something other than .title()
# for the "pretty" version of the language name.
PRETTY_LANGUAGE: dict[str, str] = {}


def get_prompt_workflow_by_filename(
    workflow_filename: str,
    system_prompt: str | None,
    prompt: str,
    outputfile: str,
):

    common_string_nodes = {
        "PrimitiveString": "value", 
        "BetterString": "string"
    }

    with open(
        os.path.join(
            const.COMFY_DIRS["artifactserver"]["WORKFLOW_TEMPLATES_DIR"],
            os.path.basename(workflow_filename)
        ),
        "r"
    ) as f:
        workflow = json.load(f)

    # we've got a _lot_ of variables to fill in.
    out_nodes = []
    for node in workflow.get("nodes", []):
        if node.get("type") in common_string_nodes:
            if node.get("title") in ["PROMPT", "Prompt"]:
                node["widgets_values"] = [ prompt ]
                node["widgets_values_named"] = {
                    common_string_nodes[node.get("type")]: prompt
                }
            elif node.get("title") in ["Output Filename"]:
                node["widgets_values"] = [ outputfile ]
                node["widgets_values_named"] = {
                    common_string_nodes[node.get("type")]: outputfile
                }
            elif node.get("title") in ["OUTPUT_DIR"]:
                node["widgets_values"] = [
                    const.COMFY_DIRS["comfyui"]["OUTPUT_DIR"],
                ]
                node["widgets_values_named"] = {
                    common_string_nodes[node.get("type")]: const.COMFY_DIRS["comfyui"]["OUTPUT_DIR"]
                }

            elif node.get("title") in ["PROMPT_FN"]:
                if "index" in image_xml.attrs:
                    prompt_fn = f"{self.nice}_img_{image_xml.attrs['index']}_{workflow_name}.prompt"
                else:
                    prompt_fn = f"{self.nice}_{workflow_name}.prompt"

                node["widgets_values"] = [prompt_fn]
                node["widgets_values_named"] = {common_string_nodes[node.get("type")]: prompt_fn}

            elif node.get("title") in ["SCENE_DESCRIPTION_FN"]:
                if "index" in image_xml.attrs:
                    scene_description_fn = f"{self.nice}_img_{image_xml.attrs['index']}_{workflow_name}.txt"
                else:
                    scene_description_fn = f"{self.nice}_{workflow_name}.txt"
                node["widgets_values"] = [scene_description_fn]
                node["widgets_values_named"] = {common_string_nodes[node.get("type")]: scene_description_fn}

            elif node.get("title") in ["LAST_FRAME"]:
                if last_frame:
                    node["widgets_values"] = [last_frame]
                    node["widgets_values_named"] = {common_string_nodes[node.get("type")]: last_frame}
                else:
                    log.warning(
                        "No last frame found for node %s", node.get("title")
                    )

        elif node.get("type") == "PrimitiveInt":
            if node.get("title") == "HEIGHT":
                node["widgets_values"] = [height, "fixed"]
                node["widgets_values_named"] = {"value": height, "fixed": "fixed"}
            elif node.get("title") == "WIDTH":
                node["widgets_values"] = [width, "fixed"]
                node["widgets_values_named"] = {"value": width, "fixed": "fixed"}

        elif node.get("type") == "PrimitiveFloat":
            if node.get("title") in ["DURATION"]:
                node["widgets_values"] = [min(0.5, float(duration_in_seconds))]
                node["widgets_values_named"] = {
                    "value": min(0.5, float(duration_in_seconds))
                }

        elif node.get("type") == "PrimitiveStringMultiline":
            if node.get("title") in ["META"]:
                value = json.dumps(self.get_meta_dict(), indent=2).replace(
                    "\n", r"\n"
                )
                node["widgets_values"] = [value]
                node["widgets_values_named"] = {"value": value}

            elif node.get("title") in ["NEGATIVE_PROMPT"]:
                negative_prompt = (
                    "pc game, console game, video game, cartoon, childish, ugly"
                )
                node["widgets_values"] = [negative_prompt]
                node["widgets_values_named"] = {"value": negative_prompt}

            elif node.get("title") in ["SYSTEM_PROMPT"]:
                system_prompt = (
                    "You are a helpful assistant for generating images based on the text of a book.  You are given a snippet of text from the book, and you use that snippet to generate an image that captures the essence of that text.  You have access to the full text of the book, but you should focus on the snippet provided.  You can also use the scene description and meta information to inform your image generation.  Your goal is to create an image that is faithful to the source material and captures the mood and details of the scene.",
                )
                node["widgets_values"] = [system_prompt]
                node["widgets_values_named"] = {"value": system_prompt}

            elif node.get("title") in ["PROMPT"]:
                prompt = template_environment.get(
                    "PROMPT",
                    self.get_prompt(image_xml),
                )
                node["widgets_values"] = [prompt]
                node["widgets_values_named"] = {"value": prompt}

            elif node.get("title") in ["SCENE"]:
                value = json.dumps(self.get_scene(image_xml), indent=2).replace(
                    "\n", r"\n"
                )
                node["widgets_values"] = [value]
                node["widgets_values_named"] = {"value": value}

            elif node.get("title") in ["SOURCE"]:
                # the portion of the text between this image and the next image.
                next_phrase = image_xml.find_next("phrase")
                next_image = image_xml.find_next("image")

                phrase_before_next_image = None
                if next_image:
                    phrase_before_next_image = next_image.find_previous("phrase")

                if (
                    next_phrase is not None
                    and next_image is not None
                    and phrase_before_next_image is not None
                ):
                    our_source = self.get_paragraph_snippet(
                        image_xml,
                        context_before=3,
                        context_after=0,
                        focus_marker="FOCUS_PARAGRAPH",
                        focus_phrases=list(
                            range(
                                as_int(next_phrase.attrs["index"]),
                                1 + as_int(phrase_before_next_image.attrs["index"]),
                            )
                        ),
                    )
                else:
                    our_source = ""
                value = json.dumps(our_source, indent=2).replace("\n", r"\n")
                node["widgets_values"] = [value]
                node["widgets_values_named"] = {"value": value}

        elif node.get("type") == "CustomStyler":
            if node.get("title") in ["STYLE"]:
                style_tag = self.get_image_style(image_xml)
                style = styles.Style(style_tag)
                log.warning(
                    "** Using style %s for image %s",
                    style,
                    image_xml.attrs["index"],
                )
                node["widgets_values"][2] = style.name
                node["widgets_values_named"]["Custom"] = style.name
        
        out_nodes.append(node)

    workflow["nodes"] = out_nodes

    # one shot workflow      
    workflow_fn = os.path.join(
        const.COMFY_DIRS["artifactserver"]["WORKFLOWS_DIR"],
        "prompt_workflow.json"
    )
    with open(workflow_fn, "w") as f:
        json.dump(workflow, f, indent=2)

    return os.path.basename(workflow_fn)


class Chapter:
    def __init__(
        self, author: Author, title: str, number: int, language: str = "english"
    ):
        log.info("Initializing Chapter")
        # unique key used for redis, doorway for reconstructing this chapter from a redis task.
        self.key = str(json.dumps([author.name, title, number, language]))
        self.author = author
        self.title = title

        self.number = int(number)
        self.language = language
        if language:
            self.pretty_language = PRETTY_LANGUAGE.get(language, language.title())

        self.soup = None
        self.nice = "invalid"
        self.chapter_title = ""
        self.mood = "dark,occult,mombi the witch is scary"
        self.theme = "Secrets, Hidden Childhood, Supernatural Threat, Survival"

        if self.author:
            ## backwards compatability
            self.bookdir = os.path.join(self.author.name, self.title)
            self.languagedir = os.path.join(self.chapterdir, self.language)

            self.config = self.get_config()

            self.chapter_title: str = self.config.get(
                "chapter_title", f"Chapter {number}"
            )

            self.mood = self.config.get("mood", self.mood)
            self.theme = self.config.get("theme", self.theme)

            # todo: roman number support?

            # feeds self.card(), which is used on the table of contents page not the
            # actual book contents.
            self.cosmetic = self.chapter_title

            self.nice = self.make_it_nice()
            self._max_image_index = None

            self.aspect = self.get_aspect()  # enforce aspect is set in config

            # unique key used for redis
            # self.key = (
            #     self.config.get("title")
            #     + "_"
            #     + self.config.get("chapter_title")
            #     + "_"
            #     + self.aspect
            # )
            # text structure used for this text.
            self.structure = self.config.get("TEXT_STRUCTURE", "novel")
            self.stub = False
        else:
            self.stub = True
            self.author = Author("invalid")

        self.translator = None
        self.subtitle = ""

        self.textoviva = Textoviva(self)
        log.info("Chapter initialized complete with key", key=self.key)

    def mkdirs(self):
        # make sure the basic directories exist.
        os.makedirs(self.chapterdir, exist_ok=True)
        os.makedirs(self.languagedir, exist_ok=True)

    def make_it_nice(self):
        # make a nice string for filenames and such.
        nice: list[str] = []
        # first four characters of the authors last name
        if self.author.name:
            last_name: str = self.author.name.split()[-1]
            if last_name:
                nice.append(last_name[:4])

        # first four characters of the title skipping "the" if it's there.
        title = self.title
        if title:
            if title.lower().startswith("the "):
                title = title[4:]
            nice.append(title.split()[0][:4])

        # the chapter number, zero-padded to 3 digits
        nice.append(f"{self.number:0>3}")

        # 4 character hash of full author name, title, chapter number and language
        # 4 characters should be unique enough.
        unique = md5(
            (self.author.name + self.title + str(self.number) + self.language).encode()
        ).digest()
        # base64 gives us a lot more bits of uniqueness than hex would.
        unique = base64.b64encode(unique).decode()[:4]
        nice.append(unique)

        return "-".join(nice).replace(" ", "_").lower()

    def get_pronunciation(self):
        """
        Get the global_pronunciations.json for this chapter.
        """
        return get_global_pronunciations(chapter=self)

    @classmethod
    def from_chapterdir(cls, chapterdir: str):
        log.warning("Obsolete do not use from_chapterdir")
        # /Aesop/Fables/chapter/0001
        parts = chapterdir.strip("/").split("/")
        if len(parts) < 4:
            raise ValueError(f"Invalid chapterdir: {chapterdir}")
        author, title, _, number = parts[-4:]
        number = int(number)
        return cls(Author(author), title, number, "english")

    def get_paragraphdir(self, paragraph_index: int) -> str:
        return os.path.join(
            self.chapterdir,
            "paragraphs",
            f"{paragraph_index:0>6}",
        )

    def find_all_tag(self, tag_name: str) -> Iterable[Tag]:
        self.load_xml()
        if self.soup:
            all_tags: ResultSet[Tag] = self.soup.find_all(tag_name)
            for tag in all_tags:
                yield tag

    def phrases(self) -> Iterable[Phrase]:
        return [
            Phrase(
                chapter=self,
                phrase_xml=phrase_xml
            ) for phrase_xml in self.find_all_tag("phrase")
        ]


    def images(self) -> Iterable[Tag]:
        return self.find_all_tag("image")

    def speakers(self) -> list[Speaker]:
        """
        Get the list of speaker uniques for this chapter.
        """
        all_characters = characters.get_all_characters(self)

        speakers = []
        for character in all_characters:
            speakers.append(Speaker(**all_characters[character]))
        return speakers

    def paragraphs(self) -> Iterable[Tag]:
        return self.find_all_tag("paragraph")

    def get_paragraph(self, paragraph_index: int) -> Tag | None:
        self.load_xml()
        if self.soup is None:
            return None

        return self.soup.find("paragraph", attrs={"index": str(paragraph_index)})

    def get_phrase_rainbow(
        self, phrase_xml: Tag
    ) -> tuple[np.ndarray, int, int, int, int]:
        """
        Find the portion of rainbow for the given phrase.
        """
        r, g, b = self.index_to_highlight_color(as_int(phrase_xml.attrs["index"]))
        text_layer_rainbow_fn = os.path.join(
            const.LIBRARY_DIR,
            self.chapterdir,
            self.language,
            "chapter.rainbow-1.png",
        )

        with open(text_layer_rainbow_fn, "rb") as h:
            img = Image.open(h)
            img = img.convert("RGBA")

        data = np.array(img)
        highlight_mask = (
            (data[:, :, 0] == r) & (data[:, :, 1] == g) & (data[:, :, 2] == b)
        )
        coordinates = np.argwhere(highlight_mask)

        y_min, x_min = coordinates.min(axis=0)
        y_max, x_max = coordinates.max(axis=0)
        # bottom, left, top, right
        return highlight_mask, y_min, x_min, y_max, x_max

    def get_bottom_height(self, force: bool = False) -> int:
        all_phrases = list(self.phrases())
        phrase_xml = all_phrases[-1]

        # open the rainbow text, filter for the color of the last phrase,
        # determine those dimensions.
        _, _, _, y_max, _ = self.get_phrase_rainbow(phrase_xml)

        return y_max

    def get_text_height(self, force: bool = False) -> int:
        all_phrases = list(self.phrases())
        last_phrase = Phrase(self, all_phrases[-1], self.aspect)
        total_height = 0

        # find the top of the first phrase _with_ text.
        for phrase in all_phrases:
            paragraph = phrase.find_parent("paragraph")
            if paragraph is None:
                continue
            paragraph_tags = tags_to_dict(as_str(paragraph.attrs.get("tags", "")))
            if not paragraph_tags.get("has-text", True):
                log.info(
                    "Skipping paragraph with no text highlight...",
                    chapter=self.key,
                    force=force,
                )
                continue

            first_phrase = Phrase(self, phrase, self.aspect)
            break
        else:
            first_phrase = None

        if not first_phrase:
            raise ValueError("No phrase with text found.")
        first_phrase_dimensions = first_phrase.get_highlight_dimensions(force=force)
        last_phrase_dimensions = last_phrase.get_highlight_dimensions(force=force)

        bottom = last_phrase_dimensions["bottom"]
        top = first_phrase_dimensions["top"]

        total_height = bottom - top
        log.debug(
            f"Chapter {self.key} text height is {bottom} - {top} = {total_height}"
        )

        self.save_xml()  # save any cached dimensions
        return total_height

    def get_highlighted_text_snippet_fn(self, phrase_xml: Tag) -> str:
        paragraph_xml = phrase_xml.find_parent("paragraph")
        if not paragraph_xml:
            raise ValueError("Phrase is not inside a paragraph.")

        paragraph_index = as_int(paragraph_xml.get("index", "-1"))
        return os.path.join(
            const.LIBRARY_DIR,
            self.get_paragraph_dir(paragraph_index),
            f"phrase_{as_int(phrase_xml.attrs['index'])}.png",
        )

    def index_to_highlight_color(self, index: int) -> tuple[int, int, int]:
        index += 10  # stay away from the black end
        rainbow_int = index % 16777216  # limit to 24 bits

        rainbow_bytes = rainbow_int.to_bytes(3, "big")
        r, g, b = rainbow_bytes
        return r, g, b

    def get_highlighted_text_image(
        self, phrase_xml: Tag, scroll_lock: int, force: bool = False
    ):
        """
        Not the snippet, the real-deal.  That means we need to obey scroll_lock.
        That is exactly how many pixels the text should be scrolled at this
        moment in the text.  We are told the answer, we just need to do it.
        """
        return page_segment.from_offset(
            chapter=self, phrase_xml=phrase_xml, top_index=scroll_lock, force=force
        )

    def get_highlighted_text_snippet(self, phrase_xml: Tag, force: bool = False):
        """ """
        fn = self.get_highlighted_text_snippet_fn(phrase_xml)

        if not force and os.path.exists(fn):
            log.info(
                "Highlighted text snippet already exists for phrase, returning existing file.",
                phrase_index=as_int(phrase_xml.attrs["index"]),
            )
            return url_for(
                "library.book.chapter.phrases.text_snippet",
                phrase_index=as_int(phrase_xml.attrs["index"]),
            )

        highlight_mask, y_min, _, y_max, _ = self.get_phrase_rainbow(phrase_xml)

        with open(
            os.path.join(
                const.LIBRARY_DIR,
                self.chapterdir,
                self.language,
                "chapter-1.png",
            ),
            "rb",
        ) as h:
            img = Image.open(h)
            img = img.convert("RGBA")

        # white canvas
        white_bg = Image.new("RGBA", img.size, "WHITE")

        # every pixel in highlight_mask that is (r,g,b) should be a yellow pixel in img.
        img_data = np.array(white_bg)
        img_data[highlight_mask] = [255, 255, 0, 255]
        white_bg = Image.fromarray(img_data)

        # paste text on white background
        img = Image.alpha_composite(white_bg, img)

        # crop, highlighted region with padding.
        img = img.crop((0, y_min - 75, img.size[0], y_max + 75))

        # the possibility exists.
        os.makedirs(os.path.dirname(fn), exist_ok=True)
        img.save(fn)

        return url_for(
            "library.book.chapter.phrases.text_snippet",
            phrase_index=phrase_xml.attrs["index"],
        )

    def get_image_citation_filename(self, index: int):
        """
        Citation images live next door to the image described.
        """
        image_xml = self.get_image(index)
        if image_xml is None:
            raise ValueError(f"No image found with index {index}")

        base_image = as_str(image_xml.attrs.get("src"))
        paragraph = image_xml.find_parent("paragraph")
        if paragraph is None:
            raise ValueError(f"No paragraph found for image with index {index}")

        if base_image:
            citation_image_fn = (
                os.path.splitext(os.path.basename(base_image))[0] + "_cite.png"
            )

            return os.path.join(
                self.get_paragraphdir(as_int(paragraph.attrs["index"])),
                citation_image_fn,
            )

        return None

    def get_image_prompt_filename(self, image_xml: Tag):
        """
        Prompt images live next door to the image described.
        No guarantee the file or directory exist.
        """
        base_image = as_str(image_xml.attrs.get("src"))
        paragraph = image_xml.find_parent("paragraph")
        if paragraph is None:
            raise ValueError(
                f"No paragraph found for image with index {image_xml.attrs['index']}"
            )

        if base_image:
            prompt_image_fn = (
                os.path.splitext(os.path.basename(base_image))[0] + ".prompt"
            )

            return os.path.join(
                self.get_paragraphdir(as_int(paragraph.attrs["index"])),
                prompt_image_fn,
            )
        else:
            # we don't have a src, so the question
            # becomes.. what might it be?
            return os.path.join(
                self.get_paragraphdir(as_int(paragraph.attrs["index"])),
                f"image_{as_int(image_xml.attrs['index']):06d}.prompt",
            )

    def get_image(self, index: int) -> Tag | None:
        """
        Get a specific image by index
        """
        self.load_xml()
        if self.soup is None:
            raise ValueError("XML not loaded properly.")

        image = self.soup.find("image", attrs={"index": str(index)})
        if image is None:
            log.warning(
                f"No image found with attr index={index} in {self.kwargs}",
            )
            return None

        return image

    def get_image_frames(self, image_xml: Tag):
        """
        How many frames of duration will we display this image?
        """
        return as_int(image_xml.attrs.get("frames", 1))

    def get_image_filename(self, image_xml: Tag) -> str | None:
        """
        Get the filename for a given image, if it exists.
        """
        try:
            base_image = as_str(image_xml.attrs.get("src"))
        except ValueError:
            base_image = None
            
        paragraph = image_xml.find_parent("paragraph")

        if paragraph is None:
            raise ValueError("Parent paragraph not found (%s)" % image_xml)

        if base_image:
            return os.path.join(
                const.LIBRARY_DIR,
                self.get_paragraphdir(as_int(paragraph.attrs["index"])),
                base_image,
            )
        else:
            return None

    def get_chapter_style(self) -> str | None:
        """
        Get the style for this chapter, if it exists.
        """
        self.load_xml()
        if self.soup is None:
            raise ValueError("XML not loaded properly.")

        chapter = self.soup.find("chapter")
        if chapter is None:
            raise ValueError("Chapter not found in XML.")

        return as_str(chapter.attrs.get("style", None))

    def set_chapter_style(self, style: str | None):
        """
        Set the style for this chapter.

        style is ultimately from get_chapter_style()
        """
        self.load_xml()
        if self.soup is None:
            raise ValueError("XML not loaded properly.")
        chapter = self.soup.find("chapter")
        if chapter is None:
            raise ValueError("Chapter not found in XML.")

        if style:
            chapter.attrs["style"] = style
        else:
            del chapter.attrs["style"]

        self.save_xml()

    def get_image_style(self, image_xml: Tag) -> str:

        try:
            style = as_str(image_xml.attrs.get("style", ''))
        except ValueError:
            style = None

        if not style:
            # otherwise, use the chapter style if there is one.
            try:
                style = as_str(self.get_chapter_style())
            except ValueError:
                style = None

            if not style:
                # otherwise, use the book style if there is one.
                try:
                    style = as_str(self.config.get("default_style"))
                except ValueError:
                    style = None

                if not style:
                    log.warning("No style found for image, chapter, or book.")
                else:
                    log.info("Using book style: %s", style)
            else:
                log.info("Using chapter style: %s", style)
        else:
            log.info("Using image style: %s", style)

        return style

    def get_prompt(self, image_xml: Tag) -> str:
        """
        Get the prompt for a given image, if it exists.
        """
        return as_str(image_xml.attrs.get("prompt", ""))

    def get_last_frame(
        self,
        image_xml: Tag,
        frame_index: int | None = None,
        recursed: bool = False,
        video_index: int | None = None,
    ) -> str | None:
        """
        If this image is part of a video, get the filename of the last frame.
        """
        log.info(
            "get_last_frame()",
            image_xml_index=image_xml.attrs["index"],
            frame_index=frame_index,
        )

        paragraph_xml = image_xml.find_parent("paragraph")
        if paragraph_xml is None:
            log.error("Image does not have a parent paragraph.")
            return None

        if video_index is None:
            # all animation dirs for this image, regardless of video_index, we're going to start looking
            # for last frame in the highest sorted dirname.
            all_animation_dirs = glob.glob(
                os.path.join(
                    const.LIBRARY_DIR,
                    self.get_paragraphdir(as_int(paragraph_xml.attrs["index"])),
                    "animation",
                    f"image_{as_int(image_xml.attrs['index']):06d}_*",
                )
            )
        else:
            # we are looking for a specific video_index, so we only want the animation dir for that video_index.
            # rather unnecessary glob for easy consistency.
            all_animation_dirs = glob.glob(
                os.path.join(
                    const.LIBRARY_DIR,
                    self.get_paragraphdir(as_int(paragraph_xml.attrs["index"])),
                    "animation",
                    f"image_{as_int(image_xml.attrs['index']):06d}_{video_index:02d}",
                )
            )

        if frame_index:
            # Absolute frame_index across all video_index values.
            index_to_fn: list[str] = []
            for animation_dir in sorted(all_animation_dirs):
                for frame in sorted(os.listdir(animation_dir)):
                    index_to_fn.append(os.path.join(animation_dir, frame))

            frame_index = min(frame_index, len(index_to_fn) - 1)

            if frame_index >= 0:
                log.info(
                    "Index of %s frame constructed, selecting frame %s",
                    len(index_to_fn),
                    frame_index,
                )
                return index_to_fn[frame_index]

        # the last frame, start with highest numbered animation dir
        for animation_dir in sorted(all_animation_dirs, reverse=True):
            frame = sorted(os.listdir(animation_dir), reverse=True)
            try:
                return os.path.join(animation_dir, frame[0])
            except IndexError:
                continue

        if not recursed:
            # re-generate the frames from the video file(s) (if it/they exist(s))
            self.video_to_frames(image_xml)
            return self.get_last_frame(
                image_xml, frame_index=frame_index, recursed=True
            )

        # fallback to the image.src
        return self.get_image_filename(image_xml)

    def video_to_frames(self, image_xml: Tag):
        """
        Convert a video into frames, and return the list of frame filenames.
        Dupe of tools.extract_frames() (oops)
        """
        video_fn = None
        for video_index in range(as_int(image_xml.attrs.get("animation_count", 1))):
            image_fn = self.get_image_filename(image_xml)

            if image_fn is None:
                log.error(
                    "Image filename is None, cannot extract frames from video.",
                    image_xml_index=image_xml.attrs["index"],
                )
                return

            if video_index == 0:
                video_fn = image_fn.replace(".png", ".mp4")
            else:
                video_fn = image_fn.replace(".png", f"_{video_index:02d}.mp4")

            paragraph = image_xml.find_parent("paragraph")
            if paragraph is None:
                raise ValueError("Image not inside a paragraph in XML.")

            output_dir = os.path.join(
                const.LIBRARY_DIR,
                self.get_paragraphdir(as_int(paragraph.attrs["index"])),
                "animation",
                f"image_{as_int(image_xml.attrs['index']):06d}_{video_index:02d}",
            )

            os.makedirs(output_dir, exist_ok=True)

            # use ffmpeg to extract frames
            command = (
                f"ffmpeg -i {video_fn} -vf fps={const.FPS} {output_dir}/frame_%04d.png"
            )
            log.info("Extracting frames from video", command=command)
            subprocess.run(command, shell=True, check=True)

        if not video_fn or not os.path.exists(video_fn):
            log.error("Video file does not exist", video_fn=video_fn)

    def get_phrase_workflow(
        self,
        phrase_xml: Tag | None,
        workflow_name: str,
        template_environment: dict[str, str | int | float] | None,
    ) -> str:
        """
        Get the workflow for a given phrase XML element and workflow name.
        If a template environment is provided, it will be used to provide variables
        to the workflow template during its generation.

        Returns the filename of the generated workflow JSON file.
        """
        if template_environment is None:
            template_environment = {}

        try:
            glob_path = os.path.join(
                const.COMFY_DIRS["artifactserver"]["WORKFLOW_TEMPLATES_DIR"],
                f"phrase.{workflow_name}.*.json",
            )
            log.info("Looking for template", glob_path=glob_path)
            template_fn = glob.glob(glob_path)[0]
        except IndexError:
            log.error(
                "No workflow template found for workflow_name",
                workflow_name=f"phrase.{workflow_name}.*.json",
                templates_dir=const.COMFY_DIRS["artifactserver"][
                    "WORKFLOW_TEMPLATES_DIR"
                ],
            )
            raise FileNotFoundError(
                f"No workflow template found for workflow_name: phrase.{workflow_name}.*.json"
            )

        return self.get_phrase_workflow_by_filename(
            phrase_xml=phrase_xml,
            workflow_filename=template_fn,
            template_environment=template_environment,
        )

    def get_phrase_workflow_by_filename(
        self,
        phrase_xml: Tag | None,
        workflow_filename: str,
        template_environment: dict[str, str | int | float] | None,
    ):
        """
        Returns the filename of a workflow JSON file
        created inside your workflow directory.
        """

        if template_environment is None:
            template_environment = {}

        with open(workflow_filename, "r") as f:
            workflow: dict[str, Any] = json.load(f)

        # Set helpful variables we want to have available to the workflow
        out_nodes = []
        for node in workflow.get("nodes", []):
            # apply any transformations based on template_environment
            # voice index valid 0-10 for blending kokoro voices

            # template_environment[f"SPEAKER_{voice_index:02d}_NAME"] = voice_dict["id"]
            # template_environment[f"SPEAKER_{voice_index:02d}_WEIGHT"] = int(voice_dict["strength"]) / 100
            # template_environment["TEXT"] = spoken_text.strip().replace("\n", " ")
            # template_environment["FILENAME_PREFIX"] = os.path.join("audio", os.path.basename(outputfile))
            # template_environment["PRONUNCIATION_DIR"] = os.path.join(
            # template_environment["PRONUNCIATION_FILENAME"] = os.path.basename(outputfile).replace(".wav", ".pronunciation")

            # Actual
            # "TEXT": "THE MOUSE, THE CAT, AND THE COCK",
            # "FILENAME_PREFIX": "audio/ph_0_THE_MOUSE__THE_CAT__AND_T_af306568_d3a9.wav",
            # "PRONUNCIATION_DIR": "/root/ComfyUI/output/audio",
            # "PRONUNCIATION_FILENAME": "ph_0_THE_MOUSE__THE_CAT__AND_T_af306568_d3a9.pronunciation"

            # Actual output (thats right):
            # /output/audio/ph_0_THE_MOUSE__THE_CAT__AND_T_af306568_d3a9.wav.wav_00001.flac  

            if node["type"] == "KokoroGenerator":
                text_value = template_environment.get(
                    "TEXT", phrase_xml.get_text() if phrase_xml is not None else ""
                )
                language_value = template_environment.get(
                    "LANGUAGE",
                    node.get("widgets_values_named", {}).get(
                        "lang", "English (American)"
                    ),
                )
                widget_values = list(node.get("widgets_values", []))
                while len(widget_values) < 3:
                    widget_values.append(None)
                widget_values[0] = text_value
                widget_values[2] = language_value
                node["widgets_values"] = widget_values
                node.setdefault("widgets_values_named", {}).update(
                    {"text": text_value, "lang": language_value}
                )

            elif node["type"] == "KokoroWeightedSpeaker":
                if node["title"] == "Required Voice Selection":
                    # the one required voice
                    node["widgets_values"] = [
                        template_environment.get("SPEAKER_00_NAME", ""),
                        template_environment.get("SPEAKER_00_WEIGHT", ""),
                    ]
                else:
                    # any other voice layers with the weights
                    voice_layer_index = int(node["title"].split()[-1])
                    node["widgets_values"] = [
                        template_environment.get(
                            f"SPEAKER_{voice_layer_index:02d}_NAME", ""
                        ),
                        template_environment.get(
                            f"SPEAKER_{voice_layer_index:02d}_WEIGHT", ""
                        ),
                    ]

            elif node["type"] == "SaveAudio" and template_environment:
                outputfile = (
                    as_str(template_environment.get("FILENAME_PREFIX", "sample"))
                    + ".wav"
                )
                node["widgets_values"] = [outputfile]
                node["widgets_values_named"]["filename"] = outputfile

            elif node["type"] == "saveText":
                node["widgets_values"] = [
                    template_environment["FILENAME_PREFIX"],
                    "txt",
                ]
                node["widgets_values_named"] = {
                    "filename_prefix": template_environment["FILENAME_PREFIX"],
                    "file_extension": "txt",
                }
            elif node["type"] == "easy saveText":
                node["widgets_values"] = [
                    template_environment["PRONUNCIATION_DIR"],
                    template_environment["PRONUNCIATION_FILENAME"],
                    "txt",
                    True,
                ]
                node["widgets_values_named"] = {
                    "output_file_path": template_environment["PRONUNCIATION_DIR"],
                    "file_name": template_environment["PRONUNCIATION_FILENAME"],
                    "file_extension": "txt",
                    "overwrite": True,
                }

            out_nodes.append(node)

        workflow["nodes"] = out_nodes
        workflow_name = os.path.splitext(os.path.basename(workflow_filename))[0]
        phrase_identifier = None
        if phrase_xml is not None:
            phrase_identifier = phrase_xml.get("index") or phrase_xml.get("id")
        if not phrase_identifier:
            output_prefix = template_environment.get("FILENAME_PREFIX")
            phrase_identifier = (
                os.path.splitext(os.path.basename(as_str(output_prefix)))[0]
                if output_prefix
                else "unlinked"
            )
        phrase_identifier = re.sub(r"[^A-Za-z0-9_-]+", "_", as_str(phrase_identifier))
        workflow_fn = os.path.join(
            const.COMFY_DIRS["artifactserver"]["WORKFLOWS_DIR"],
            f"{self.nice}_phrase_{phrase_identifier}_{workflow_name}.json",
        )

        with open(workflow_fn, "w") as f:
            json.dump(workflow, f, indent=2)

        return os.path.basename(workflow_fn)

    def get_workflow(
        self,
        image_xml: Tag,
        workflow_name: str,  # flux_phrasechain
        video_index: int | None = None,
        template_environment: dict[str, str] | None = None,
        force_name: str | None = None,
    ):
        """
        We want the indicated workflow, with variables based on
        the provided image_xml and surrounding material.

        template_environment is an override dict.
        """
        # we load the workflow template,
        # fill in the variables,
        # save a copy in the workflow directory with a suitable filename
        # return the name of that file.

        # It is easy to go from there to a one-click workflow load.

        # ui.ti2i.flux_phrasechain-Flux Klein Image Edit.json
        #               ^-- workflow_name, global unique.
        #
        if template_environment is None:
            template_environment = {}

        try:
            template_fn = glob.glob(
                os.path.join(
                    const.COMFY_DIRS["artifactserver"]["WORKFLOW_TEMPLATES_DIR"],
                    f"ui.*.{workflow_name}*.json",
                )
            )[0]
        except IndexError:
            log.error(
                "No workflow template found for workflow_name",
                workflow_name=f"ui.*.{workflow_name}*.json",
                templates_dir=const.COMFY_DIRS["artifactserver"][
                    "WORKFLOW_TEMPLATES_DIR"
                ],
            )
            return None

        return self.get_workflow_by_filename(
            image_xml=image_xml,
            workflow_filename=template_fn,
            video_index=video_index,
            template_environment=template_environment,
            force_name=force_name,
        )

    def get_workflow_by_filename(
        self,
        image_xml: Tag,
        workflow_filename: str,
        video_index: int | None = None,
        template_environment: dict[str, str] | None = None,
        force_name: str | None = None,
    ) -> str:
        """
        Returns the filename of a workflow JSON file
        created inside your workflow directory.

        image_xml: BeautifulSoup Tag for an <image> block

        workflow_filename: filename of a /template JSON

        video_index: if this is a video, it may be part of a sequence of videos.  If that is the case, which number in the
        sequence is it? (matters for file naming reasons)

        template_environment: dict of variable overrides for the workflow template.

        force_name: if provided, this will be used as the output filename for the workflow JSON, instead of the generated name.
        """
        with open(workflow_filename, "r") as f:
            workflow = json.load(f)

        try:
            workflow_name = os.path.splitext(os.path.basename(workflow_filename))[
                0
            ].split(".")[2]
        except IndexError:
            workflow_name = os.path.splitext(os.path.basename(workflow_filename))[0]

        if template_environment is None:
            template_environment = {}

        height = int(template_environment.get("HEIGHT", 1024))
        width = int(template_environment.get("WIDTH", 1024))

        try:
            duration_in_frames = self.get_image_frames(image_xml)
        except AttributeError:
            duration_in_frames = 1

        duration_in_seconds = duration_in_frames / const.FPS

        source_image = self.get_image_filename(image_xml)
        if video_index not in [None, ""]:
            source_image = self.get_last_frame(image_xml, video_index=video_index)

        # our last frame generally the _next_ image.
        last_frame = None
        try:
            next_image_xml = self.get_image(as_int(image_xml.attrs["index"]) + 1)
            if next_image_xml:
                next_image_filename = self.get_image_filename(next_image_xml)
                if next_image_filename:
                    last_frame = os.path.basename(next_image_filename)
        except TypeError:
            last_frame = None
        except KeyError:
            last_frame = None

        if not last_frame:
            last_frame = source_image

        # reasonable default
        prior_frame = None
        try:
            prior_image_xml = self.get_image(as_int(image_xml.attrs["index"]) - 1)
            if prior_image_xml:
                prior_image_filename = self.get_image_filename(prior_image_xml)
                if prior_image_filename:
                    prior_frame = os.path.basename(prior_image_filename)
        except TypeError:
            prior_frame = None
        except KeyError:
            prior_frame = None

        # we've got a _lot_ of variables to fill in.
        out_nodes = []
        for node in workflow.get("nodes", []):
            if node.get("type") == "LoadImage":
                if node.get("title") in ["INPUT_IMAGE", "FIRST_FRAME"]:
                    if source_image:
                        target = os.path.join(
                            const.COMFY_DIRS["artifactserver"]["INPUT_DIR"],
                            os.path.basename(source_image),
                        )

                        log.info("Copying %s to %s", source_image, target)

                        shutil.copyfile(source_image, target)
                        node["widgets_values"] = [os.path.basename(target), "image"]
                        node["widgets_values_named"] = {
                            "image": os.path.basename(target),
                            "upload": "image",
                        }

                if node.get("title") in ["LAST_FRAME"]:
                    if last_frame:
                        target = os.path.join(
                            const.COMFY_DIRS["artifactserver"]["INPUT_DIR"],
                            os.path.basename(last_frame),
                        )

                        log.info("Copying %s to %s", last_frame, target)

                        shutil.copyfile(last_frame, target)
                        node["widgets_values"] = [os.path.basename(target), "image"]
                        node["widgets_values_named"] = {
                            "image": os.path.basename(target),
                            "upload": "image",
                        }

            elif node.get("type") == "PrimitiveString":
                if node.get("title") in ["FILE_NAME", "FILENAME_PREFIX"]:
                    if "FILENAME_PREFIX" in template_environment:
                        filename_prefix = template_environment["FILENAME_PREFIX"]
                    else:
                        filename_prefix = f"{self.nice}_img_{image_xml.attrs['index']}_{workflow_name}"
                        if video_index not in [None, ""]:
                            filename_prefix += f"_{video_index:02d}"

                    node["widgets_values"] = [filename_prefix]
                    node["widgets_values_named"] = {"value": filename_prefix}

                elif node.get("title") in ["PREVIOUS_IMAGE", "PRIOR_FRAME"]:
                    node["widgets_values"] = [prior_frame]
                    node["widgets_values_named"] = {"value": prior_frame}

                elif node.get("title") in ["INPUT_IMAGE_PATH"]:
                    node["widgets_values"] = [const.COMFY_DIRS["comfyui"]["INPUT_DIR"]]
                    node["widgets_values_named"] = {
                        "value": const.COMFY_DIRS["comfyui"]["INPUT_DIR"]
                    }

                elif node.get("title") in ["OUTPUT_DIR"]:
                    node["widgets_values"] = [
                        const.COMFY_DIRS["comfyui"]["OUTPUT_DIR"],
                    ]
                    node["widgets_values_named"] = {
                        "value": const.COMFY_DIRS["comfyui"]["OUTPUT_DIR"]
                    }

                elif node.get("title") in ["PROMPT_FN"]:
                    if "index" in image_xml.attrs:
                        prompt_fn = f"{self.nice}_img_{image_xml.attrs['index']}_{workflow_name}.prompt"
                    else:
                        prompt_fn = f"{self.nice}_{workflow_name}.prompt"

                    node["widgets_values"] = [prompt_fn]
                    node["widgets_values_named"] = {"value": prompt_fn}

                elif node.get("title") in ["SCENE_DESCRIPTION_FN"]:
                    if "index" in image_xml.attrs:
                        scene_description_fn = f"{self.nice}_img_{image_xml.attrs['index']}_{workflow_name}.txt"
                    else:
                        scene_description_fn = f"{self.nice}_{workflow_name}.txt"
                    node["widgets_values"] = [scene_description_fn]
                    node["widgets_values_named"] = {"value": scene_description_fn}

                elif node.get("title") in ["LAST_FRAME"]:
                    if last_frame:
                        node["widgets_values"] = [last_frame]
                        node["widgets_values_named"] = {"value": last_frame}
                    else:
                        log.warning(
                            "No last frame found for node %s", node.get("title")
                        )

            elif node.get("type") == "PrimitiveInt":
                if node.get("title") == "HEIGHT":
                    node["widgets_values"] = [height, "fixed"]
                    node["widgets_values_named"] = {"value": height, "fixed": "fixed"}
                elif node.get("title") == "WIDTH":
                    node["widgets_values"] = [width, "fixed"]
                    node["widgets_values_named"] = {"value": width, "fixed": "fixed"}

            elif node.get("type") == "PrimitiveFloat":
                if node.get("title") in ["DURATION"]:
                    node["widgets_values"] = [min(0.5, float(duration_in_seconds))]
                    node["widgets_values_named"] = {
                        "value": min(0.5, float(duration_in_seconds))
                    }

            elif node.get("type") == "PrimitiveStringMultiline":
                if node.get("title") in ["META"]:
                    value = json.dumps(self.get_meta_dict(), indent=2).replace(
                        "\n", r"\n"
                    )
                    node["widgets_values"] = [value]
                    node["widgets_values_named"] = {"value": value}

                elif node.get("title") in ["NEGATIVE_PROMPT"]:
                    negative_prompt = (
                        "pc game, console game, video game, cartoon, childish, ugly"
                    )
                    node["widgets_values"] = [negative_prompt]
                    node["widgets_values_named"] = {"value": negative_prompt}

                elif node.get("title") in ["SYSTEM_PROMPT"]:
                    system_prompt = (
                        "You are a helpful assistant for generating images based on the text of a book.  You are given a snippet of text from the book, and you use that snippet to generate an image that captures the essence of that text.  You have access to the full text of the book, but you should focus on the snippet provided.  You can also use the scene description and meta information to inform your image generation.  Your goal is to create an image that is faithful to the source material and captures the mood and details of the scene.",
                    )
                    node["widgets_values"] = [system_prompt]
                    node["widgets_values_named"] = {"value": system_prompt}

                elif node.get("title") in ["PROMPT"]:
                    prompt = template_environment.get(
                        "PROMPT",
                        self.get_prompt(image_xml),
                    )
                    node["widgets_values"] = [prompt]
                    node["widgets_values_named"] = {"value": prompt}

                elif node.get("title") in ["SCENE"]:
                    value = json.dumps(self.get_scene(image_xml), indent=2).replace(
                        "\n", r"\n"
                    )
                    node["widgets_values"] = [value]
                    node["widgets_values_named"] = {"value": value}

                elif node.get("title") in ["SOURCE"]:
                    # the portion of the text between this image and the next image.
                    next_phrase = image_xml.find_next("phrase")
                    next_image = image_xml.find_next("image")

                    phrase_before_next_image = None
                    if next_image:
                        phrase_before_next_image = next_image.find_previous("phrase")

                    if (
                        next_phrase is not None
                        and next_image is not None
                        and phrase_before_next_image is not None
                    ):
                        our_source = self.get_paragraph_snippet(
                            image_xml,
                            context_before=3,
                            context_after=0,
                            focus_marker="FOCUS_PARAGRAPH",
                            focus_phrases=list(
                                range(
                                    as_int(next_phrase.attrs["index"]),
                                    1 + as_int(phrase_before_next_image.attrs["index"]),
                                )
                            ),
                        )
                    else:
                        our_source = ""
                    value = json.dumps(our_source, indent=2).replace("\n", r"\n")
                    node["widgets_values"] = [value]
                    node["widgets_values_named"] = {"value": value}

            elif node.get("type") == "CustomStyler":
                if node.get("title") in ["STYLE"]:
                    style_tag = self.get_image_style(image_xml)
                    style = styles.Style(style_tag)
                    log.warning(
                        "** Using style %s for image %s",
                        style,
                        image_xml.attrs["index"],
                    )
                    node["widgets_values"][2] = style.name
                    node["widgets_values_named"]["Custom"] = style.name
            else:
                log.info("Skipping node type: %s", node.get("type"))

            out_nodes.append(node)

        workflow["nodes"] = out_nodes

        if force_name:
            workflow_fn = os.path.join(
                const.COMFY_DIRS["artifactserver"]["WORKFLOWS_DIR"],
                force_name,
            )
        else:
            # KeyErrors?  Use force_name.
            workflow_fn = os.path.join(
                const.COMFY_DIRS["artifactserver"]["WORKFLOWS_DIR"],
                f"{self.nice}_workflow_{image_xml['index']}_{workflow_name}.json",
            )

        with open(workflow_fn, "w") as f:
            json.dump(workflow, f, indent=2)

        return os.path.basename(workflow_fn)

    get_image_workflow = get_workflow
    get_image_workflow_by_filename = get_workflow_by_filename


    def get_prompt_workflow_by_filename(
        self,
        workflow_filename: str,
        system_prompt: str,
        prompt: str,
        outputfile: str,
    ) -> str:
        return get_prompt_workflow_by_filename(
            workflow_filename=workflow_filename,
            system_prompt=system_prompt,
            prompt=prompt,
            outputfile=outputfile
        )

    def get_comfy_workflow(
        self,
        image_xml: Tag,
        interface: str = "ui",
        mode: str = "*",
        workflow_name: str = "",
        video_index: int | None = None,
        template_environment: dict[str, str | int | float] | None = None,
    ):
        """
        The problem is we don't know if this is an animation or an image gen (or
        text gen);
        """
        log.warning(
            "Obsolete get_comfy_workflow() called.  Rewrite for get_workflow() and comfy.run_workflow_fn()"
        )
        if template_environment is None:
            template_environment = {}

        if interface != "ui":
            log.warning(
                "get_comfy_workflow() !OBSOLETE! called with interface=%s, which is not 'ui'.  This may not work as expected.",
                interface,
            )

        log.info(
            "get_comfy_workflow(image_xml=%s, interface=%s, workflow_name=%s, video_index=%s)",
            image_xml.attrs["index"],
            interface,
            workflow_name,
            video_index,
        )

        video_tag = ""
        if video_index not in [None, ""]:
            video_tag = f"_{video_index:02d}"

        height = 1024
        width = 1024

        # is an image an input to this workflow?  If so we need to copy it to the comfyUI input directory.

        previous_image_xml = self.get_image(as_int(image_xml.attrs["index"]) - 1)
        prior_frame = None

        # reasonable default
        if previous_image_xml:
            prior_image_filename = self.get_image_filename(previous_image_xml)
            if prior_image_filename:
                prior_frame = os.path.basename(prior_image_filename)

        # copy the current version of "this" image as an input image
        source_image = self.get_image_filename(image_xml) or "image.png"

        if video_index == 0:
            # we are a "base" video ie: there is an <image> for us
            # okay.. so we're not a subsequent video in a stream, but we might be the second
            # animation in a paragraph.  In that case our source image is the last frame of the previous animation.
            if previous_image_xml is not None:
                # ok, so not to get too personal, but exactly which frame do you want?
                # we want the frame that corresponds with the duration of the audio track.
                # for this kind of work, we can use the masterplan.
                mplan = self.get_masterplan()
                try:
                    image_plan = mplan.images[as_int(previous_image_xml.attrs["index"])]
                except IndexError:
                    # try again with a new masterplan
                    # TODO: why is this such shit when the video->"regenerate master plan"
                    # appears to be doing it correctly?
                    mplan = self.get_masterplan(force=True)
                    image_plan = mplan.images[as_int(previous_image_xml.attrs["index"])]

                frame_index = image_plan.frames
                prior_frame = self.get_last_frame(
                    previous_image_xml, frame_index=frame_index
                )
            else:
                prior_frame = ""
        elif video_index not in [None, ""]:
            # we are one of the subsequent videos in a sequence of videos, our
            # source image is the last frame of the _previous_ video.
            # (the critical -1 is buried in here)

            # get_comfy_workflow(image_xml=6, interface=ui, workflow_template=LTX23, video_index=1)
            image_index = as_int(image_xml.attrs["index"])

            source_dir = os.path.join(
                const.LIBRARY_DIR,
                self.get_paragraphdir(
                    as_int(image_xml.find_parent("paragraph").attrs["index"])
                ),
                "animation",
                f"image_{image_index:06d}_{video_index - 1:02d}",
            )

            source_image = os.path.join(source_dir, max(os.listdir(source_dir)))
            log.info("Source image for video_index %s is %s", video_index, source_image)

        next_image_xml = self.get_image(as_int(image_xml.attrs["index"]) + 1)
        next_image_src = ""

        if next_image_xml is not None:
            paragraph_xml = next_image_xml.find_parent("paragraph")
            if paragraph_xml is None:
                log.warning("Next image does not have a parent paragraph.")
            else:
                next_image_src = os.path.join(
                    const.LIBRARY_DIR,
                    self.get_paragraphdir(as_int(paragraph_xml.attrs["index"])),
                    as_str(next_image_xml.attrs["src"]),
                )

        if prior_frame is None:
            # we need to survive an os.path.join, even if the result is discarded.
            prior_frame = ""

        files_to_copy = [
            (
                source_image,
                os.path.join(
                    const.COMFY_DIRS["artifactserver"]["INPUT_DIR"],
                    os.path.basename(source_image),
                ),
            ),
            (
                prior_frame,
                os.path.join(
                    const.COMFY_DIRS["artifactserver"]["INPUT_DIR"],
                    os.path.basename(prior_frame),
                ),
            ),
            (
                next_image_src,
                os.path.join(
                    const.COMFY_DIRS["artifactserver"]["INPUT_DIR"],
                    os.path.basename(next_image_src),
                ),
            ),
        ]
        log.info("preparing files_to_copy into comfy", files_to_copy=files_to_copy)

        for source, destination in files_to_copy:
            if not source:
                continue

            log.info(
                "Copying source image to comfyUI input directory",
                source_image=source,
                dest_image=destination,
            )

            if os.path.exists(source) and os.path.isfile(source):
                shutil.copy(source, destination)
            else:
                log.info("Input Image file does not exist", image_fn=source)

        log.info("Using workflow template", workflow_name=workflow_name)

        workflow = comfy.load_workflow_template(interface, mode, workflow_name)

        ####
        # Useful variables for workflow construction.  These get substituted
        # into the workflow template to create the actual workflow.
        ####
        log.info("Gathering Text and Image metadata for workflow construction...")

        animation_prompt = ""
        if video_index not in [None, ""]:
            animation_prompt = as_str(
                image_xml.attrs.get(f"animation_prompt{video_tag}", "")
            )
            log.info(
                "Using animation prompt: %s (from %s)",
                animation_prompt,
                f"animation_prompt{video_tag}",
            )

        negative_prompt = "pc game, console game, video game, cartoon, childish, ugly"
        scene = self.get_scene(image_xml)

        duration_in_frames = self.get_image_frames(image_xml)
        duration_in_seconds = duration_in_frames / const.FPS

        # so we can wildcard to pull output images into the right place.
        filename_prefix = f"{self.nice}_img_{image_xml.attrs['index']}_{workflow_name}"

        if video_index not in [None, ""]:
            filename_prefix += video_tag

        # we want two paragraphs before and two after the paragraph containing this image.
        # we also want to prefix this paragraph with "#FOCUS_PARAGRAPH" so make it easier for the
        # LLM to manipulate it.
        # next_image_xml = image_xml
        # if "ti2i" in modes:
        next_image_xml = self.get_image(as_int(image_xml.attrs["index"]) + 1)

        next_phrase = image_xml.find_next("phrase")

        if next_phrase is not None:
            first_phrase_index = next_phrase.attrs["index"]
        else:
            first_phrase_index = None

        last_phrase_index = None
        if next_image_xml is not None:
            last_phrase = next_image_xml.find_previous("phrase")
            if last_phrase is not None:
                last_phrase_index = last_phrase.attrs["index"]

        log.info(
            "Collecting snippet focused on phrases %s - %s",
            first_phrase_index,
            last_phrase_index,
        )

        our_source = ""
        if first_phrase_index is not None and last_phrase_index is not None:
            our_source = self.get_paragraph_snippet(
                image_xml,
                context_before=3,
                context_after=0,
                focus_marker="FOCUS_PARAGRAPH",
                focus_phrases=list(
                    range(as_int(first_phrase_index), as_int(last_phrase_index) + 1)
                ),
            )

        meta = self.get_meta_dict()
        prompt_fn = f"{self.nice}_img_{image_xml.attrs['index']}_{workflow_name}.prompt"
        # previous_image = image_xml.find_previous_sibling("image")
        scene_description_fn = (
            f"{self.nice}_img_{image_xml.attrs['index']}_{workflow_name}.txt"
        )

        # for first frame/last frame interpolation
        # if prior_frame:
        #     first_frame = os.path.basename(prior_frame)
        # else:
        first_frame = os.path.basename(source_image)

        if next_image_src:
            last_frame = os.path.basename(next_image_src)
        else:
            last_frame = os.path.basename(source_image)

        # for animation workflows the input image is the source_image, which is to say the
        # output of the text-to-image stage.

        # For non-animation workflows, the input image when relevant is the
        # image frame immediately prior to the image we want to draw.
        input_image = os.path.basename(source_image)

        style_name = self.get_image_style(image_xml)
        style = styles.Style(style_tag=style_name)
        log.info("style: %s", style)
        #  Using image style: gothic_manuscript_illumination name=artifact_editor.chapter.chapter

        # the funky "blah:int" syntax is what lets us
        # have a valid json template with {"cow": "{{blah:int}}"} in it
        # and get {"cow": 5} instead of {"cow": "5"} in the final workflow.
        base_template_environment: dict[str, str | int | float] = {
            "WIDTH": str(width),
            "HEIGHT": str(height),
            "DURATION": str(duration_in_seconds),  # float
            "FILENAME_PREFIX": filename_prefix,
            "FILE_NAME": filename_prefix,
            "FIRST_FRAME": first_frame,
            "INPUT_IMAGE_PATH": const.COMFY_DIRS["comfyui"]["INPUT_DIR"],
            "INPUT_IMAGE": input_image,
            "LAST_FRAME": last_frame,
            "META": json.dumps(meta, indent=2),
            "NEGATIVE_PROMPT": negative_prompt,
            "OUTPUT_DIR": const.COMFY_DIRS["comfyui"]["OUTPUT_DIR"],
            "PREVIOUS_IMAGE": os.path.basename(prior_frame),
            "PRIOR_FRAME": os.path.basename(prior_frame)
            if prior_frame is not None
            else "",
            "PRIOR_RESULT": "",
            "PROMPT_FN": prompt_fn,
            "PROMPT": self.get_prompt(image_xml),
            "SCENE_DESCRIPTION_FN": scene_description_fn,
            "SCENE": json.dumps(scene, indent=2),
            "SOURCE": our_source,
            "STYLE": style.name,
            "SYSTEM_PROMPT": "You are a helpful assistant for generating images based on the text of a book.  You are given a snippet of text from the book, and you use that snippet to generate an image that captures the essence of that text.  You have access to the full text of the book, but you should focus on the snippet provided.  You can also use the scene description and meta information to inform your image generation.  Your goal is to create an image that is faithful to the source material and captures the mood and details of the scene.",
        }

        if video_index not in [None, ""]:  # we need 0 to be valid.
            # this is a video.
            base_template_environment["ANIMATION_PROMPT"] = animation_prompt

            if video_index and video_index > 0:
                # our first frame is the last frame of the previous video, not
                # the source image.
                base_template_environment["FIRST_FRAME"] = os.path.join(
                    os.path.basename(source_image)
                )
                base_template_environment["INPUT_IMAGE"] = os.path.join(
                    os.path.basename(source_image)
                )
                # how many frames have we already generated? we can somewhat
                # assume each prior video is 4 seconds long because of the min()
                # calls below.
                prior_video_frames = video_index * 4 * const.FPS
                base_template_environment["TOTAL_FRAMES"] = str(
                    min(
                        duration_in_frames - prior_video_frames,
                        4
                        * const.FPS,  # 4 seconds max, for more you have to chain videos together.
                    )
                )
            else:
                # first video for this image
                base_template_environment["TOTAL_FRAMES"] = str(
                    min(
                        self.get_image_frames(image_xml),
                        4
                        * const.FPS,  # 4 seconds max, for more you have to chain videos together.
                    )
                )

        # overwrite template environment settings from template_envionment arg
        base_template_environment.update(template_environment)

        # workflow = comfy.apply_template_environment(
        #     workflow=workflow,
        #     template_environment=base_template_environment
        # )
        return workflow

    def get_paragraph_snippet(
        self,
        image_xml: Tag,
        context_before: int = 2,
        context_after: int = 1,
        focus_marker: str = "FOCUS PARAGRAPH",
        focus_phrases: list[int] | None = None,
    ) -> str:
        # point 'previous' at the paragraph <context> paragraphs before the one
        # containing this image.
        if focus_phrases is None:
            focus_phrases = []
        else:
            focus_phrases = list(focus_phrases)

        parent_paragraph = image_xml.find_parent("paragraph")
        out = []

        # back 'previous_paragraph' up to the Nth paragraph before the current one
        previous_paragraph = parent_paragraph
        for _ in range(context_before):
            if previous_paragraph is not None:
                previous_paragraph = previous_paragraph.find_previous_sibling(
                    "paragraph"
                )

        for _ in range(context_before):
            if previous_paragraph is not None:
                previous_paragraph = previous_paragraph.find_next_sibling("paragraph")
                if previous_paragraph is not None:
                    out.append(
                        as_str(previous_paragraph.get_text()).replace("\n", "") + "\n\n"
                    )

        out.append("#" + focus_marker)
        # should be the same as previous_paragraph
        parent_paragraph = image_xml.find_parent("paragraph")
        if parent_paragraph is None:
            raise ValueError("Image does not have a parent paragraph")

        for phrase in parent_paragraph.find_all("phrase"):
            if as_int(phrase.attrs["index"]) in focus_phrases:
                out.append("<PHRASE>" + phrase.get_text() + "</PHRASE>\n")
            else:
                # log.info("Nope", phrase_index=int(phrase.attrs['index']), focus_phrases=focus_phrases)
                out.append(phrase.get_text().replace("\n", ""))

        # out.append(previous.get_text().replace("\n", "") + "\n\n")
        out.append("\n\n")

        # keep rolling with previous paragraph so even if we bork it up
        # we keep continuity.
        for _ in range(context_after):
            if previous_paragraph is not None:
                previous_paragraph = previous_paragraph.find_next_sibling("paragraph")

            if previous_paragraph:
                out.append(
                    as_str(previous_paragraph.get_text()).replace("\n", "") + "\n\n"
                )

        out = "\n".join(out)
        while "  " in out:
            out = out.replace("  ", " ")

        # combine any adjacent <PHRASE> blocks
        out = re.sub("</PHRASE>[\n ]*<PHRASE>", "\n", out)

        log.info(
            "Generated paragraph snippet for image",
            focus_phrases=focus_phrases,
            context_before=context_before,
            context_after=context_after,
            snippet=out,
        )
        return out

    def is_first_image(self, image_xml: Tag):
        """
        is this the first image in the paragraph?
        """
        return image_xml.find_previous_sibling("image") is None

    def is_last_image(self, image_xml: Tag):
        """
        is this the last image in the paragraph?
        """
        return image_xml.find_next_sibling("image") is None

    def get_scene(self, image_xml: Tag) -> dict[str, Any]:
        ch = {}
        characters = as_str(image_xml.attrs.get("scene_characters", "")).split(",")
        for character_name in characters:
            if character_name:
                ch[character_name] = {
                    "pose": as_str(image_xml.attrs.get(f"{character_name}_pose", "")),
                    "action": as_str(
                        image_xml.attrs.get(f"{character_name}_action", "")
                    ),
                    "location": as_str(
                        image_xml.attrs.get(f"{character_name}_location", "")
                    ),
                    "description": as_str(
                        image_xml.attrs.get(f"{character_name}_description", "")
                    ),
                }

        out = {
            "setting": as_str(image_xml.attrs.get("setting", "")),
            "tod": as_str(image_xml.attrs.get("tod", "")),
            "camera": as_str(image_xml.attrs.get("camera", "")),
            "lighting": {
                "direction": as_str(image_xml.attrs.get("lighting_direction", "")),
                "quality": as_str(image_xml.attrs.get("lighting_quality", "")),
                "source": as_str(image_xml.attrs.get("lighting_source", "")),
            },
            # easy way to put your thumb on the scale.
            # [PARAGRAPH META] is the LAW!!
            "description": as_str(image_xml.attrs.get("description", "")),
        }

        focus_character = as_str(image_xml.attrs.get("focus_character", ""))
        if focus_character and focus_character in ch:
            out["character_focus"] = focus_character

        if ch:
            out["characters"] = ch

        # deep-remove every key with an empty value,
        # we want to descent into dicts inside 'out' and remove empty values there too.
        def deep_clean(d: Any) -> Any:
            if isinstance(d, dict):
                return {k: deep_clean(v) for k, v in d.items() if v}
            return d

        out = deep_clean(out)

        return out

    def get_meta_dict(self):
        return {
            "title": self.title,
            "chapter_title": self.chapter_title,
            "author": self.author.name,
            "chapter": self.number,
            "mood": self.mood,
            "theme": self.theme,
        }

    def get_phrase(self, phrase_index: int) -> Tag | None:
        """
        Get a specific phrase by index
        """
        self.load_xml()  # cached load
        return self.soup.find("phrase", index=str(phrase_index))

    def get_sound(self, sound_index: int) -> Tag | None:
        """
        Get a specific sound by index
        """
        self.load_xml()  # cached load
        return self.soup.find("sound", index=str(sound_index))

    def get_aspect(self) -> aspect:
        """
        What is the aspect ratio for this chapter?
        valid outputs are "widescreen" and "portrait"

        If the config is invalid, we default to "widescreen".
        """
        aspect = self.config.get("aspect_ratio", "widescreen")
        if aspect not in ["widescreen", "portrait"]:
            # one more chance..
            try:
                aspect = {
                    "landscape": "widescreen",
                }[aspect]
            except KeyError:
                log.error(
                    "Invalid aspect ratio in config, defaulting to widescreen",
                    aspect=aspect,
                )
                aspect = "widescreen"

        return aspect

    def text_cleanup(self, raw):
        """
        Do some basic cleanup on the raw text before we save it.
        """
        # en-dash; latex wants two hyphens
        # U+2013
        cleaned = raw.replace("–", "--")

        # em-dash; latex wants three hyphens
        # U+2014
        cleaned = cleaned.replace("—", "---")

        # reduce _any_ sequence of 3 or more dashes to 3 dashes, because we use
        # --- as a special marker for em-dashes.
        cleaned = re.sub(r"-{4,}", "---", cleaned)

        return cleaned.strip()

    def has_text(self):
        txt_fn = self.get_txt_fn()
        return os.path.exists(txt_fn) and os.path.getsize(txt_fn) > 0

    def load_txt(self):
        txt_fn = self.get_txt_fn()
        # os.makedirs(os.path.dirname(txt_fn), exist_ok=True)

        if not os.path.exists(txt_fn):
            alternate_1 = os.path.join(
                const.LIBRARY_DIR, self.chapterdir, "chapter.txt"
            )
            if os.path.exists(alternate_1):
                shutil.copy(alternate_1, txt_fn)
            else:
                alternate_2 = os.path.join(
                    const.LIBRARY_DIR, self.chapterdir, "book.txt"
                )
                if os.path.exists(alternate_2):
                    shutil.copy(alternate_2, txt_fn)

        if os.path.exists(txt_fn):
            with open(txt_fn, "r") as f:
                return f.read()

        log.error("Chapter text file not found", txt_fn=txt_fn)
        return ""

    def save_txt(self, raw):
        txt_fn = self.get_txt_fn()
        with open(txt_fn, "w") as f:
            f.write(raw)

    def get_delta_fn(self, tag=""):
        return os.path.join(
            const.LIBRARY_DIR,
            self.chapterdir,
            self.language,
            f"chapter{'.' + tag if tag else ''}.delta.json",
        )

    def save_delta(
        self, delta: dict[str, list[dict[str, str | dict[str, bool]]]], tag=""
    ):
        delta_fn = self.get_delta_fn(tag)
        os.makedirs(os.path.dirname(delta_fn), exist_ok=True)
        log.info("Saving delta to file", delta_fn=delta_fn)
        with open(delta_fn, "w") as f:
            json.dump(delta, f, indent=2)

    def load_delta(self) -> dict[str, list[dict[str, str | dict[str, bool]]]]:
        """
        Example:
        delta = {
            'ops': [
                {
                    "attributes": {
                        "bold": true
                    },
                    "insert": "Real Computers"
                },
                ...
            ]
        }
        """
        delta_fn = self.get_delta_fn()
        if os.path.exists(delta_fn):
            with open(delta_fn, "r") as f:
                return json.load(f)
        else:
            log.warning("Delta file not found", delta_fn=delta_fn)
            return {}

    def get_title_delta_fn(self):
        return os.path.join(
            const.LIBRARY_DIR,
            self.chapterdir,
            self.language,
            "chapter.title.delta.json",
        )

    def save_title_delta(
        self, delta: dict[str, list[dict[str, str | dict[str, bool]]]]
    ):
        delta_fn = self.get_title_delta_fn()
        os.makedirs(os.path.dirname(delta_fn), exist_ok=True)
        log.info("Saving title delta to file", delta_fn=delta_fn)
        with open(delta_fn, "w") as f:
            json.dump(delta, f, indent=2)

    def load_title_delta(self) -> dict[str, list[dict[str, str | dict[str, bool]]]]:
        delta_fn = self.get_title_delta_fn()
        if os.path.exists(delta_fn):
            with open(delta_fn, "r") as f:
                return json.load(f)
        else:
            log.warning("Title delta file not found", delta_fn=delta_fn)
            return {}

    def delete(self):
        """
        Delete all files associated with this chapter.
        """
        full_path = os.path.join(const.LIBRARY_DIR, self.chapterdir)

        if os.path.exists(full_path):
            log.info("Deleting chapter directory: %s", full_path)
            shutil.rmtree(full_path)
        else:
            log.warning("Chapter directory not found for deletion: %s", full_path)

    # syntax sugar
    @property
    def cover_url(self):
        return url_for(
            "library.chapter_cover",
            author=self.author.name,
            title=self.title,
            chapter_number=self.number,
        )

    @property
    def args(self):
        return [self.author.name, self.title, self.number, self.language]

    @property
    def kwargs(self):
        return {
            "author": self.author.name,
            "title": self.title,
            "chapter_number": self.number,
            "language": self.language,
        }

    def previous(self):
        number = self.number
        while number > 1:
            number -= 1
            c = Chapter(
                self.author,
                self.title,
                number,
                self.language,
            )

            if c.has_text():
                return c
        return None

    @property
    def url(self):
        log.warning("Obsolete chapter.url reference")
        if not hasattr(self, "_url"):
            self._url = tools.get_chapterurl(self.author.name, self.title, self.number)
        return self._url

    @property
    def chapterdir(self):
        if not hasattr(self, "_chapterdir"):
            self._chapterdir = tools.get_chapterdir(
                self.author.name, self.title, self.number
            )
        return self._chapterdir

    def get_chapterdir(self):
        return self.chapterdir

    def get_paragraph_dir(self, paragraph_index: int) -> str:
        return os.path.join(self.chapterdir, "paragraphs", f"{paragraph_index:0>6}")

    def get_xml(self):
        self.load_xml()
        return self.soup

    def load_xml(self, force=False):
        """
        Read the xml of the book into self.soup

        returns true if we got contents
        """
        if self.stub:
            self.soup = BeautifulSoup("<chapter></chapter>", "xml")
            return self.soup

        if self.soup is not None:
            log.info("Chapter XML already loaded")
            if not force:
                log.info("Cache hit")
                return self.soup
            else:
                log.info("** Forced reload of chapter XML **")

        xml_fn = self.get_xml_fn()

        log.info("Cache not loaded, loading fresh XML for chapter", xml_fn=xml_fn)

        if os.path.exists(xml_fn):
            with open(xml_fn, "r") as h:
                self.soup = BeautifulSoup(h.read(), "xml")

            chapter_xml = self.soup.find("chapter")
            if chapter_xml is None:
                chapter_xml = self.soup.new_tag("chapter")

            old_version = as_float(chapter_xml.attrs.get("version", "0.0"))
            self.soup = xml_upgrade(self, self.soup)

            new_version = as_float(chapter_xml.attrs.get("version", "0.0"))
            if f"{old_version}" != f"{new_version}":
                self.save_xml()
                log.info(
                    "Upgraded XML from version %s to version %s",
                    old_version,
                    new_version,
                )
        else:
            self.soup = BeautifulSoup("<chapter></chapter>", "xml")

        return self.soup

    def get_xml_fn(self):
        best = os.path.join(
            const.LIBRARY_DIR, self.chapterdir, self.language, "chapter.xml"
        )
        if os.path.exists(best):
            return best

        common = os.path.join(const.LIBRARY_DIR, self.chapterdir, "book.xml")
        if os.path.exists(common):
            shutil.copy(common, best)
            return common

        return best

    def save_xml(self):
        """
        Save the xml version of this chapter
        """
        book_fn = self.get_xml_fn()
        # /home/jkane/books/active/Aesop/Fables/chapter/0025/book.xml
        # /home/jkane/books/active/Aesop/Fables/chapter/0025/book.xml
        log.info("Saving book as XML", book_fn=book_fn)
        if self.soup is None:
            log.error("book not loaded!", book_fn=book_fn)
            self.load_xml()

        pretty = self.soup.prettify()

        as_split = pretty.splitlines()
        if len(as_split) > 10:
            beginning = as_split[:5]
            ending = as_split[-5:]
            # log.debug("pre-samples", beginning=beginning, ending=ending)

        log.info("Generated XML in bytes", len_pretty=len(pretty), book_fn=book_fn)
        with open(book_fn, "w") as h:
            h.write(pretty)

        log.info("Finished saving book as XML")

    def get_txt_fn(self):
        best = os.path.join(
            const.LIBRARY_DIR,
            self.chapterdir,
            self.language,
            "chapter.txt",
        )
        if os.path.exists(best):
            return best

        common = os.path.join(
            const.LIBRARY_DIR,
            self.chapterdir,
            "book.txt",
        )

        os.makedirs(os.path.dirname(best), exist_ok=True)
        if os.path.exists(common):
            shutil.copy(common, best)

        return best

    def card(self):
        return {
            "index": self.number,
            "slug": f"{self.number:06}",
            "chapter_title": self.cosmetic,
        }

    def save_config(self):
        config.save_config(
            self.chapterdir,
            self.config,
        )

    def get_config(self, force: bool = False) -> dict[str, str]:
        if hasattr(self, "config") and self.config and not force:
            return self.config

        # Start with and config for the parent book
        if os.path.exists(os.path.join(const.LIBRARY_DIR, self.bookdir)):
            log.info("Loading config", bookdir=self.bookdir)
            unified_config = config.get_config(self.bookdir).copy()
        else:
            log.info("Book config not found", bookdir=self.bookdir)
            unified_config = {}
        # log.info(f"Got parent configdict: {unified_config}")

        # is there a chapter config?
        if os.path.exists(os.path.join(const.LIBRARY_DIR, self.chapterdir)):
            log.info("Augmenting config", chapterdir=self.chapterdir)
            try:
                chapter_configdict = config.get_config(self.chapterdir)
            except json.JSONDecodeError:
                log.warning(
                    "Chapter config.json is malformed, ignoring",
                    chapterdir=self.chapterdir,
                )
                chapter_configdict = {}
        else:
            log.info("Chapter config not found", chapterdir=self.chapterdir)
            chapter_configdict = {}

        log.debug("Got chapter configdict", chapter_configdict=chapter_configdict)

        # ultimate default with the path values
        unified_config.update(chapter_configdict)

        # feed some defaults
        if "author" not in unified_config:
            unified_config["author"] = self.author.name

        if "title" not in unified_config:
            unified_config["title"] = self.title

        if "chapter_title" not in unified_config:
            unified_config["chapter_title"] = f"Chapter {int(self.number)}"

        if "dialog" not in unified_config:
            unified_config["dialog"] = "dialog"
        self.config = unified_config

        return unified_config

    def get_masterplan(self, force: bool = False) -> masterplan.Plan:
        """
        Generate a plan for the chapter
        """
        mp = masterplan.Masterplan(self)
        return mp.get(force=force)

    def build_frame_to_camera(self, aspect="widescreen", force=False):
        """
        output is a camera.json

        given a frame index, return the scroll lock.
        What _matters_ is leaving things nice for the next person
        Calculate each inter-paragraph gap directly instead of assuming a constant.
        """
        log.info("=== Building frame to camera (%s) ===", aspect)
        mplan = self.get_masterplan()
        saved = camera.load_camera(self)
        if saved and not force:
            log.info("Camera loaded from disk, skipping build_frame_to_camera")
            return

        last_frame = mplan.words[-1].end_frame
        # slots for every frame, we want the scroll_lock value for each frame.
        _frame_to_camera: list[float|None] = [None] * (last_frame + 1)

        current_frame = 0

        self.scrolling_rate = None
        # set scrolling rate to the average required to place the bottom of the last
        # line in the center of the screen when the audio finishes.

        # adjusted because we start half way down the screen
        # ^ ballpark, we hate ballpark.
        # height = chapter.get_text_height(force=force)

        G = const.GEOMETRY[aspect]
        # the text begins halfway down the screen.
        height = self.get_bottom_height(force=force) - (G["TEXT_HEIGHT"] // 2)

        scrolled_frames = 0
        for current_frame in range(last_frame + 1):
            word = self.frame_to_word(mplan, current_frame)
            # log.info(f'[{current_frame}] {word=}')
            if word.fullscreen:
                continue
            scrolled_frames += 1

        self.scrolling_rate = height / float(scrolled_frames)
        log.info(
            "In order to traverse %s pixels of text height in %s frames",
            height,
            scrolled_frames,
        )
        log.info(
            "Setting initial scrolling rate to %s pixels per frame",
            self.scrolling_rate,
        )

        scroll_lock: float = 0.0
        for current_frame in range(last_frame + 1):
            word = self.frame_to_word(mplan, current_frame)

            # skip over any fullscreen frames, they will fall-through to None
            # and we don't want to advance scroll lock.
            if word.fullscreen:
                continue

            _frame_to_camera[current_frame] = scroll_lock
            scroll_lock += self.scrolling_rate

        log.info("Frame to camera built with %s frames", len(_frame_to_camera))
        camera.set_frame_to_camera(_frame_to_camera)
        camera.save_camera(self)
        return

    def time_to_frame(self, duration_in_seconds):
        """
        Given a time in seconds, return the frame index.
        """
        return math.floor(const.FPS * duration_in_seconds)

    def frame_to_image(self, mplan, frame_index):
        """
        Given a frame index, return the image dict for that frame.
        """
        return mplan.frame_to_image(mplan, frame_index)

    def frame_to_word(self, mplan: masterplan.Plan, frame_index: int):
        """
        Based on the 'words' section of masterplan, determine which 'word'
        object governs the given frame_index.
        """
        # quick access to get the last word.
        if frame_index == -1:
            return mplan.words[-1]

        for word in mplan.words:
            if (word.start_frame <= frame_index) and (word.end_frame >= frame_index):
                return word

        log.error("No word found for frame index %s", frame_index)
        return None

    def get_all_audio_tracks(self) -> list[str]:
        mplan = self.get_masterplan()
        audio_tracks = []
        for word in mplan.words:
            audio_tracks.append(
                os.path.join(
                    const.LIBRARY_DIR,
                    word.paragraph_dir,
                    word.src.lstrip("/"),
                )
            )

        return audio_tracks

    def safe_title(self):
        log.info(self.config)
        full_title = self.config["title"]
        safe_title = re.sub(r"[/\\?%*:|\"<>\x7F\x00-\x1F]", "-", full_title)
        return safe_title.replace(" ", "_")

    def set_chapter_title(self, new_title):
        self.config["chapter_title"] = new_title
        self.chapter_title = new_title
        self.save_config()

    def get_video_filename(self):
        video_pfn = os.path.join(
            const.LIBRARY_DIR,
            self.chapterdir.lstrip("/"),
            self.safe_title() + ".mp4",
        )
        return video_pfn

    def max_image_index(self) -> int:
        """
        what does max_image_index mean?  The maximum index of the images in the book?
        """
        if self._max_image_index:
            return self._max_image_index

        all_images = self.get_xml().findAll("image")
        last_index = all_images[-1].attrs.get("index", None)

        if last_index is None:
            last_index = len(all_images) - 1

        self._max_image_index = last_index
        return int(last_index)


def xml_upgrade(chapter: Chapter, soup: Tag) -> Tag:
    """
    Upgrade the XML to the latest version.
    """
    log.info("xml_upgrade called")
    chapter_xml = soup.find("chapter")
    if chapter_xml is None:
        # upgrade old 'book' to 'chapter'
        chapter_xml = soup.find("book")
        chapter_xml.name = "chapter"

    version = float(chapter_xml.attrs.get("version", "0.0"))

    if version < 1.0:
        log.warning("Upgrading XML from version %s to 1.0", version)

        # enforce some hard types
        for index, image in enumerate(soup.findAll("image")):
            if "frames" in image.attrs:
                image.attrs["frames"] = int(float(image.attrs["frames"]))
            image.attrs["index"] = int(index)

        for index, phrase in enumerate(soup.findAll("phrase")):
            if "frames" in phrase.attrs:
                phrase.attrs["frames"] = int(float(phrase.attrs["frames"]))

            phrase.attrs["index"] = int(index)

        for index, sound_xml in enumerate(soup.findAll("sound")):
            if "frames" in sound_xml.attrs:
                sound_xml.attrs["frames"] = int(float(sound_xml.attrs["frames"]))

            sound_xml.attrs["index"] = int(index)

        # This is going to get messy.  The paragraph index reflects the directory
        # that files for the paragraph are stored in so they can't change after creation.
        paragraph_indexes = set()
        missing = False
        for index, paragraph in enumerate(soup.findAll("paragraph")):
            if "index" not in paragraph.attrs:
                missing = True

        if missing:
            for index, paragraph in enumerate(soup.findAll("paragraph")):
                if "index" not in paragraph.attrs:
                    if index not in paragraph_indexes:
                        # the easy case
                        paragraph_indexes.add(index)
                        paragraph.attrs["index"] = int(index)
                    else:
                        # the ugly case.
                        # TODO:
                        # re-index every paragraph after this one and rename all the directories.
                        # mostly because this should be stupid-rare.
                        log.warning(
                            "Duplicate paragraph index found",
                            index=index,
                            assigning_unique_index=True,
                        )
                        log.error("THIS WILL PROBABLY BREAK YOUR SHIT")
                        unique = str(index) + "_a"
                        paragraph_indexes.add(unique)
                        paragraph.attrs["index"] = unique

        # remove 'image_index'
        for image in soup.findAll("image"):
            if "image_index" in image.attrs:
                del image.attrs["image_index"]

        chapter_xml.attrs["version"] = "1.0"

    if version < 1.1:
        log.warning("Upgrading XML from version %s to 1.1", version)
        for image_xml in soup.findAll("image"):
            if "animation_method" in image_xml.attrs:
                log.info("Fixing up animation_method to be versioned")
                # update "bare" animation metadata to be clearly versioned for multiple-video sequences.
                # these are numbered from 0 and typically are :02d
                image_xml.attrs["animation_method_00"] = image_xml.attrs[
                    "animation_method"
                ]
                del image_xml.attrs["animation_method"]

            if "animation_prompt" in image_xml.attrs:
                log.info("Fixing up animation_prompt to be versioned")
                image_xml.attrs["animation_prompt_00"] = image_xml.attrs[
                    "animation_prompt"
                ]
                del image_xml.attrs["animation_prompt"]

            if "workflow_i2v_template" in image_xml.attrs:
                log.info("Fixing up workflow_i2v_template to be versioned")
                image_xml.attrs["workflow_animation_template_00"] = image_xml.attrs[
                    "workflow_i2v_template"
                ]
                del image_xml.attrs["workflow_i2v_template"]

            # and some obsolete fields
            if "meta_prompt" in image_xml.attrs:
                log.info("Removing obsolete meta_prompt field")
                del image_xml.attrs["meta_prompt"]

            if "workflow_name" in image_xml.attrs:
                log.info("Removing obsolete workflow_name field")
                del image_xml.attrs["workflow_name"]

            if "workflow_template" in image_xml.attrs:
                log.info("Renaming workflow_template to workflow_image_template")
                image_xml.attrs["workflow_image_template"] = image_xml.attrs[
                    "workflow_template"
                ]
                del image_xml.attrs["workflow_template"]

            if "workflow_t2i_template" in image_xml.attrs:
                log.info("Renaming workflow_t2i_template to workflow_image_template")
                image_xml.attrs["workflow_image_template"] = image_xml.attrs[
                    "workflow_t2i_template"
                ]
                del image_xml.attrs["workflow_t2i_template"]

            if "workflow_animation_template" in image_xml.attrs:
                log.info(
                    "Renaming workflow_animation_template to workflow_animation_template_00"
                )
                image_xml.attrs["workflow_animation_template_00"] = image_xml.attrs[
                    "workflow_animation_template"
                ]
                del image_xml.attrs["workflow_animation_template"]

            # obsolete
            if "styled_prompt" in image_xml.attrs:
                log.info("Removing obsolete styled_prompt field")
                del image_xml.attrs["styled_prompt"]

            if "t5_prompt" in image_xml.attrs:
                log.info("Renaming t5_prompt to prompt")
                if "prompt" not in image_xml.attrs:
                    image_xml.attrs["prompt"] = image_xml.attrs["t5_prompt"]

                del image_xml.attrs["t5_prompt"]

            if "tab" in image_xml.attrs:
                log.info("Removing obsolete tab field")
                del image_xml.attrs["tab"]

            if "¬" in image_xml.attrs.get("prompt", ""):
                log.info("Cleaning up prompt field to remove styled prompt metadata")
                # we have a styled prompt, we do not want that.  These are often nested a few layers, so we'll unwrap it.
                while "¬" in image_xml.attrs.get("prompt", ""):
                    first_comma = image_xml.attrs["prompt"].find(",")
                    last_weird = image_xml.attrs["prompt"].rfind("¬")

                    if (
                        first_comma != -1
                        and last_weird != -1
                        and last_weird > first_comma
                    ):
                        # we have a comma and a weird char, and the weird char
                        image_xml.attrs["prompt"] = image_xml.attrs["prompt"][
                            first_comma + 1 : last_weird
                        ]
                    else:
                        break

            if "styled" in image_xml.attrs:
                log.info("Removing obsolete styled field")
                del image_xml.attrs["styled"]

            if "workflow_animation_template_1" in image_xml.attrs:
                log.info(
                    "Renaming workflow_animation_template_1 to workflow_animation_template_01"
                )
                image_xml.attrs["workflow_animation_template_01"] = image_xml.attrs[
                    "workflow_animation_template_1"
                ]
                del image_xml.attrs["workflow_animation_template_1"]

            if "animation_method_1" in image_xml.attrs:
                log.info("Renaming animation_method_1 to animation_method_01")
                image_xml.attrs["animation_method_01"] = image_xml.attrs[
                    "animation_method_1"
                ]
                del image_xml.attrs["animation_method_1"]

            if "animation_mode_1" in image_xml.attrs:
                log.info("Renaming animation_mode_1 to animation_mode_01")
                image_xml.attrs["animation_mode_01"] = image_xml.attrs[
                    "animation_mode_1"
                ]
                del image_xml.attrs["animation_mode_1"]

            if "Narrator_action" in image_xml.attrs:
                log.info("Removing obsolete Narrator_action field")
                del image_xml.attrs["Narrator_action"]

            if "Narrator_description" in image_xml.attrs:
                log.info("Removing obsolete Narrator_description field")
                del image_xml.attrs["Narrator_description"]

            if "Narrator_location" in image_xml.attrs:
                log.info("Removing obsolete Narrator_location field")
                del image_xml.attrs["Narrator_location"]

            if "None_location" in image_xml.attrs:
                log.info("Removing obsolete None_location field")
                del image_xml.attrs["None_location"]

            for key in ["animation_method_00", "animation_method_01"]:
                if key in image_xml.attrs:
                    if image_xml.attrs[key] == "comfy_ui":
                        log.info(
                            "Updating animation_method to use a specific comfy_ui workflow"
                        )
                        if image_xml.find_next_sibling("image"):
                            # there is an image after this one, with this paragraph.
                            image_xml.attrs[key] = "comfy_ui_flf2v"
                        else:
                            # this is the final (or only) image in this paragraph.
                            image_xml.attrs[key] = "comfy_ui_i2v"

            if "clip_prompt" in image_xml.attrs:
                log.info("Removing obsolete clip_prompt field")
                del image_xml.attrs["clip_prompt"]

        for paragraph_xml in chapter_xml.findAll("paragraph"):
            paragraph_dir = chapter.get_paragraph_dir(paragraph_xml.attrs["index"])
            animation_dir = os.path.join(const.LIBRARY_DIR, paragraph_dir, "animation")

            if os.path.exists(animation_dir):
                for dirname in os.listdir(animation_dir):
                    if dirname.endswith(","):
                        # remove animation directories ending with a comma (was a bug)
                        dirpath = os.path.join(animation_dir, dirname)
                        if os.path.isdir(dirpath):
                            shutil.rmtree(dirpath)

                    split_under = dirname.split("_")
                    image_index = split_under[-1]
                    if image_index[0] != "0":
                        # different bug, the first digit would only legit be
                        # non-zero if there are more than 100000, we can just delete it.
                        dirpath = os.path.join(animation_dir, dirname)
                        if os.path.isdir(dirpath):
                            shutil.rmtree(dirpath)
                            # os.rmdir(dirpath)

                    if len(split_under) == 3:
                        # good... ish.
                        _, image_index, video_index = split_under

                        if len(image_index) != 6:
                            # we have a problem.
                            if len(video_index) == 2:
                                # image_00_03 sort of directory name (oops), the trouble
                                # is we don't really know which is supposed to be the image_index and which is the video_index.
                                # so we guess.
                                if image_index == "00" and video_index != "00":
                                    # odds are the video index is the one that is _supposed_ to be 00
                                    video_index, image_index = image_index, video_index

                            image_index = int(image_index)
                            video_index = int(video_index)

                            new_dirname = f"image_{image_index:06d}_{video_index:02d}"

                            new_dirpath = os.path.join(animation_dir, new_dirname)
                            old_dirpath = os.path.join(animation_dir, dirname)

                            if os.path.exists(new_dirpath):
                                log.warning(
                                    "New animation directory already exists, skipping rename and deleting old directory.",
                                    old_dirpath=old_dirpath,
                                    new_dirpath=new_dirpath,
                                )
                                shutil.rmtree(old_dirpath)
                            else:
                                log.info(
                                    "Renaming animation directory",
                                    old_dirpath=old_dirpath,
                                    new_dirpath=new_dirpath,
                                )
                                os.rename(old_dirpath, new_dirpath)

                    elif len(split_under) == 2:
                        video_index = 0
                        image_index = int(split_under[-1])
                        new_dirname = f"image_{image_index:06d}_{video_index:02d}"

                        new_dirpath = os.path.join(animation_dir, new_dirname)
                        old_dirpath = os.path.join(animation_dir, dirname)
                        if os.path.exists(new_dirpath):
                            log.warning(
                                "New animation directory already exists, skipping rename",
                                new_dirpath=new_dirpath,
                            )
                        else:
                            log.info(
                                "Renaming animation directory",
                                old_dirpath=old_dirpath,
                                new_dirpath=new_dirpath,
                            )
                            os.rename(old_dirpath, new_dirpath)

        chapter_xml.attrs["version"] = "1.1"
    else:
        log.info("XML version is up to date (%s), no upgrade needed", version)

    return soup
