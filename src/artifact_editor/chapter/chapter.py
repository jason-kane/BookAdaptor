import os
import random
import re
import shutil
import base64
import comfy
from hashlib import md5
import glob
import json

# from artifact_editor.images import animation
from flask import url_for
import numpy as np
from bs4 import BeautifulSoup
from PIL import Image

import const
import logger
from artifact_editor import (
    camera,
    config,
    styles,
    tools,
    typography,
)
from artifact_editor.author.author import Author
from artifact_editor.masterplan import masterplan
from artifact_editor.tools import tags_to_dict
from artifact_editor.typography import page_segment
from artifact_editor.audio.pronunciation.pronunciation import get_global_pronunciations

log = logger.log(__name__)


def phrase_images(paragraph_dir: str, aspect: str, phrase_index: int) -> str:
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
        glob(os.path.join(aspect_dir, f"text_layer_{phrase_index}-*.png"))
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
    def __init__(self, chapter, phrase_xml, aspect):
        log.info("NEW Phrase(%s, %s)", phrase_xml, aspect)

        self.chapter = chapter
        self.phrase_xml = phrase_xml
        self.phrase_index = int(self.phrase_xml.attrs["index"])
        self.aspect = aspect

    def get_highlight_dimensions(self, force):
        """
        Iterate chapter images accumulating height until we find the highlighted
        phrase.  Return a ct_layer dictionary with top/right/bottom/left pixel
        indices.
        """
        if not force:
            try:
                return {
                    "top": int(self.phrase_xml.attrs["top"]),
                    "bottom": int(self.phrase_xml.attrs["bottom"]),
                    "left": int(self.phrase_xml.attrs["left"]),
                    "right": int(self.phrase_xml.attrs["right"]),
                }
            except KeyError:
                log.info("No highlight dimensions cached, calculating from images...")

        paragraph = self.phrase_xml.find_parent("paragraph")
        paragraph_dir = self.chapter.get_paragraph_dir(paragraph.attrs["index"])
        total_height = 0

        all_phrase_images = list(
            phrase_images(
                paragraph_dir,
                self.aspect,
                self.phrase_index,
            )
        )
        log.info(f"{all_phrase_images=}")

        if not all_phrase_images:
            fn = f"{paragraph_dir}/{self.aspect}/text_layer_{self.phrase_index}*.png"
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
                left, _, _, _ = topstrip.getchannel("R").getbbox()

                bottomstrip = img.crop((hleft, hbottom - 1, hright, hbottom))
                _, _, right, _ = bottomstrip.getchannel("R").getbbox()

                self.phrase_xml.attrs["top"] = total_height + htop
                self.phrase_xml.attrs["bottom"] = total_height + hbottom
                self.phrase_xml.attrs["left"] = left
                self.phrase_xml.attrs["right"] = right
                break

        return {
            "top": int(self.phrase_xml.attrs["top"]),
            "bottom": int(self.phrase_xml.attrs["bottom"]),
            "left": int(self.phrase_xml.attrs["left"]),
            "right": int(self.phrase_xml.attrs["right"]),
        }


# override if you want something other than .title()
# for the "pretty" version of the language name.
PRETTY_LANGUAGE = {}


class Chapter:
    @classmethod
    def door(cls, key):
        log.info("Opening chapter.door(%s)", key)
        # just serializing the constructor args to a string so we can
        # pass it through a redis queue.
        try:
            author, title, number, language = json.loads(key)
        except Exception as e:
            log.error(f"Invalid chapter key: {key}")
            raise

        author = Author(author)
        log.info(f"cls({author}, {title}, {number}, {language})")
        return cls(author, title, number, language)

    def __init__(
        self, author: Author, title: str, number: int, language: str = "english"
    ):
        # log = log.bind(
        #     author=author.name,
        #     title=title,
        #     number=number,
        #     language=language
        # )

        log.info("Initializing Chapter")
        # unique key used for redis, doorway for reconstructing this chapter from a redis task.
        if author is None:
            self.key = "invalid"
        else:
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
            self.chapterdir = tools.get_chapterdir(author.name, title, number)
            self.url = tools.get_chapterurl(author.name, title, number)
            self.languagedir = os.path.join(self.chapterdir, self.language)

            self.config = self.get_config()

            self.chapter_title = self.config.get("chapter_title", f"Chapter {number}")
            
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
            self.STUB = False
        else:
            self.STUB = True
            self.author = Author("invalid")
            self.chapterdir = ""

        self.translator = None
        self.subtitle = ""
        log.info("Chapter initialized complete with key", key=self.key)

    def mkdirs(self):
        # make sure the basic directories exist.
        os.makedirs(self.chapterdir, exist_ok=True)
        os.makedirs(self.languagedir, exist_ok=True)

    def make_it_nice(self):
        # make a nice string for filenames and such.
        nice = []
        # first four characters of the authors last name
        nice.append(self.author.name.split()[-1][:4])

        # first four characters of the title skipping "the" if it's there.
        title = self.title
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
    def from_chapterdir(cls, chapterdir):
        # /Aesop/Fables/chapter/0001
        parts = chapterdir.strip("/").split("/")
        if len(parts) < 4:
            raise ValueError(f"Invalid chapterdir: {chapterdir}")
        author, title, _, number = parts[-4:]
        return cls(Author(author), title, number, "english")

    def get_paragraphdir(self, paragraph_index):
        return os.path.join(
            self.chapterdir,
            "paragraphs",
            f"{paragraph_index:0>6}",
        )

    def phrases(self):
        self.load_xml()
        return self.soup.find_all("phrase")

    def paragraphs(self):
        self.load_xml()
        return self.soup.find_all("paragraph")

    def get_paragraph(self, paragraph_index: int):
        self.load_xml()
        return self.soup.find("paragraph", attrs={"index": str(paragraph_index)})

    def get_bottom_height(self, force=False):
        all_phrases = self.phrases()
        last_phrase = Phrase(self, all_phrases[-1], self.aspect)
        last_phrase_dimensions = last_phrase.get_highlight_dimensions(force=force)
        return last_phrase_dimensions["bottom"]

    def get_text_height(self, force=False):
        all_phrases = self.phrases()

        # first_phrase = Phrase(all_phrases[0])
        last_phrase = Phrase(self, all_phrases[-1], self.aspect)

        total_height = 0

        # find the top of the first phrase _with_ text.
        for phrase in all_phrases:
            paragraph = phrase.find_parent("paragraph")
            paragraph_tags = tags_to_dict(paragraph.attrs.get("tags", ""))
            if not paragraph_tags.get("has-text", True):
                log.info(
                    "Skipping paragraph with no text highlight...",
                    chapter=self.key,
                    force=force,
                )
                continue

            first_phrase = Phrase(self, phrase, self.aspect)
            break

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

    def get_highlighted_text_snippet_fn(self, phrase_xml):
        paragraph_xml = phrase_xml.find_parent("paragraph")
        paragraph_index = paragraph_xml["index"]
        return os.path.join(
            const.LIBRARY_DIR,
            self.get_paragraph_dir(paragraph_index),
            f"phrase_{phrase_xml.attrs['index']}.png",
        )

    def index_to_highlight_color(self, index):
        index += 10  # stay away from the black end
        rainbow_int = index % 16777216  # limit to 24 bits

        rainbow_bytes = rainbow_int.to_bytes(3, "big")
        r, g, b = rainbow_bytes
        return r, g, b

    def get_highlighted_text_image(self, phrase_xml, scroll_lock, force=False):
        """
        Not the snippet, the real-deal.  That means we need to obey scroll_lock.
        That is exactly how many pixels the text should be scrolled at this
        moment in the text.  We are told the answer, we just need to do it.
        """
        return page_segment.from_offset(
            chapter=self, phrase_xml=phrase_xml, top_index=scroll_lock, force=force
        )

    def get_highlighted_text_snippet(self, phrase_xml, force=False):
        """ """
        # log = logger.bind(
        #     chapter=self.key,
        #     phrase_xml=phrase_xml.attrs['index'],
        #     force=force
        # )

        fn = self.get_highlighted_text_snippet_fn(phrase_xml)

        if not force and os.path.exists(fn):
            log.info(
                "Highlighted text snippet already exists for phrase, returning existing file.",
                phrase_index=phrase_xml.attrs["index"],
            )
            return url_for(
                "library.book.chapter.audio.text_snippet",
                **self.kwargs,
                phrase_index=phrase_xml.attrs["index"],
            )

        r, g, b = self.index_to_highlight_color(int(phrase_xml.attrs["index"]))

        # TODO: the problem is, this isn't going to work if the highlight spans multiple images.  Which it often does.
        # open up text_layer_rainbow.png.
        text_layer_rainbow_fn = os.path.join(
            const.LIBRARY_DIR, self.chapterdir, "text_layer_rainbow.png"
        )
        if not os.path.exists(text_layer_rainbow_fn):
            log.error(
                "Rainbow text layer not found, cannot generate highlighted text snippet.",
                file=text_layer_rainbow_fn,
            )
            return None

        with open(text_layer_rainbow_fn, "rb") as h:
            img = Image.open(h)
            img = img.convert("RGBA")

        data = np.array(img)
        highlight_mask = (
            (data[:, :, 0] == r) & (data[:, :, 1] == g) & (data[:, :, 2] == b)
        )

        # get the bounding box of the highlight mask
        coordinates = np.argwhere(highlight_mask)
        if coordinates.size == 0:
            log.warning(
                "No highlight region found for phrase with color ({r}, {g}, {b})",
                phrase_index=phrase_xml.attrs["index"],
                r=r,
                g=g,
                b=b,
            )
            return None

        y_min, x_min = coordinates.min(axis=0)
        y_max, x_max = coordinates.max(axis=0)

        with open(
            os.path.join(const.LIBRARY_DIR, self.chapterdir, "text_layer_plain.png"),
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
            "library.book.chapter.audio.text_snippet",
            **self.kwargs,
            phrase_index=phrase_xml.attrs["index"],
        )

    def get_image_citation_filename(self, index):
        """
        Citation images live next door to the image described.
        """
        image_xml = self.get_image(index)
        base_image = image_xml.attrs.get("src")
        paragraph = image_xml.find_parent("paragraph")

        if base_image:
            citation_image_fn = (
                os.path.splitext(os.path.basename(base_image))[0] + "_cite.png"
            )

            return os.path.join(
                self.get_paragraphdir(paragraph.attrs["index"]), citation_image_fn
            )

        return None

    def get_image_prompt_filename(self, image_xml):
        """
        Prompt images live next door to the image described.
        No guarantee the file or directory exist.
        """
        base_image = image_xml.attrs.get("src")
        paragraph = image_xml.find_parent("paragraph")

        if base_image:
            prompt_image_fn = (
                os.path.splitext(os.path.basename(base_image))[0] + ".prompt"
            )

            return os.path.join(
                self.get_paragraphdir(paragraph.attrs["index"]), prompt_image_fn
            )
        else:
            # we don't have a src, so the question
            # becomes.. what might it be?
            return os.path.join(
                self.get_paragraphdir(paragraph.attrs["index"]),
                f"image_{int(image_xml.attrs['index']):06d}.prompt",
            )

    def get_image(self, index):
        """
        Get a specific image by index
        """
        index = int(index)
        self.load_xml()
        all_images = self.soup.findAll("image")
        if index < 0 or index >= len(all_images):
            return None
        return all_images[index]

    def get_image_frames(self, image_xml):
        """
        How many frames of duration will we display this image?
        """
        return int(image_xml.attrs.get("frames", 1))

    def get_image_filename(self, image_xml):
        """
        Get the filename for a given image, if it exists.
        """
        base_image = image_xml.attrs.get("src")
        try:
            paragraph = image_xml.find_parent("paragraph")
        except AttributeError as err:
            log.error(err, image_xml=image_xml)
            return None

        if base_image:
            return os.path.join(
                const.LIBRARY_DIR,
                self.get_paragraphdir(paragraph.attrs["index"]),
                base_image,
            )
        else:
            return None

    def get_chapter_style(self):
        """
        Get the style for this chapter, if it exists.
        """
        self.load_xml()
        return self.soup.find("book").attrs.get("style", None)

    def set_chapter_style(self, style):
        """
        Set the style for this chapter.
        """
        self.load_xml()
        self.soup.find("book").attrs["style"] = style
        self.save_xml()

    def get_prompt(self, image_xml):
        """
        Get the prompt for a given image, if it exists.
        """
        return image_xml.attrs.get("prompt")
        
        # styling here is dumb.
        change = False
        prompt = image_xml.attrs.get("prompt")
        style = image_xml.attrs.get("style", self.config.get("default_style"))

        if not style:
            style = self.get_chapter_style()
            
            if not style:
                # is there a book style?
                style = self.config.get("default_style")

            if style:
                image_xml.attrs["style"] = style
                change = True

        if not style:
            log.error(
                "No style specified for image and no default style in chapter or book config, cannot get styled prompt."
            )

        if change:
            self.save_xml()

        return prompt

    def get_last_frame(self, image_xml, frame_index=None, recursed=False, video_index=None) -> str | None:
        """
        If this image is part of a video, get the filename of the last frame.
        """
        log.info("get_last_frame()", image_xml_index=image_xml.attrs["index"], frame_index=frame_index)
        
        if video_index is None:
            # all animation dirs for this image, regardless of video_index, we're going to start looking
            # for last frame in the highest sorted dirname.
            all_animation_dirs = glob.glob(
                os.path.join(
                    const.LIBRARY_DIR,
                    self.get_paragraphdir(image_xml.find_parent("paragraph").attrs["index"]),
                    "animation",
                    f"image_{int(image_xml.attrs['index']):06d}_*"
                )
            )
        else:
            # we are looking for a specific video_index, so we only want the animation dir for that video_index.
            # rather unnecessary glob for easy consistency.
            all_animation_dirs = glob.glob(
                os.path.join(
                    const.LIBRARY_DIR,
                    self.get_paragraphdir(image_xml.find_parent("paragraph").attrs["index"]),
                    "animation",
                    f"image_{int(image_xml.attrs['index']):06d}_{video_index:02d}"
                )
            )

        if frame_index:
            # Absolute frame_index across all video_index values.
            index_to_fn = []
            for animation_dir in sorted(all_animation_dirs):
                for frame in sorted(os.listdir(animation_dir)):
                    index_to_fn.append(os.path.join(animation_dir, frame))
        
            frame_index = min(frame_index, len(index_to_fn) - 1)
            
            if frame_index >= 0:
                log.info('Index of %s frame constructed, selecting frame %s', len(index_to_fn), frame_index)
                return index_to_fn[frame_index]
        
        # the last frame, start with highest numbered animation dir
        for animation_dir in sorted(all_animation_dirs, reverse=True):
            frame = sorted(os.listdir(animation_dir), reverse=True)
            return os.path.join(animation_dir, frame[0])

        if not recursed:
            # re-generate the frames from the video file(s) (if it/they exist(s))
            self.video_to_frames(image_xml)
            return self.get_last_frame(image_xml, frame_index=frame_index, recursed=True)
    
        # fallback to the image.src
        return self.get_image_filename(image_xml)

    def video_to_frames(self, image_xml):
        """
        Convert a video into frames, and return the list of frame filenames.
        Dupe of tools.extract_frames() (oops)
        """
        video_fn = None
        for video_index in range(image_xml.attrs.get('animation_count', 1)):
            if video_index == 0:
                image_fn = self.get_image_filename(image_xml)
                video_fn = image_fn.replace(".png", ".mp4")
            else:
                image_fn = self.get_image_filename(image_xml)
                video_fn = image_fn.replace(".png", f"_{video_index:02d}.mp4")

            output_dir = os.path.join(
                const.LIBRARY_DIR,
                self.get_paragraphdir(image_xml.find_parent("paragraph").attrs["index"]),
                "animation",
                f"image_{int(image_xml.attrs['index']):06d}_{video_index:02d}",
            )

            os.makedirs(output_dir, exist_ok=True)

            # use ffmpeg to extract frames
            command = f"ffmpeg -i {video_fn} -vf fps={const.FPS} {output_dir}/frame_%04d.png"
            log.info("Extracting frames from video", command=command)
            os.system(command)

        if not video_fn or not os.path.exists(video_fn):
            log.error("Video file does not exist", video_fn=video_fn)

    def get_comfy_workflow(
        self,
        image_xml,
        interface="ui",
        mode="*",
        workflow_template=None,
        video_index=None,
        template_environment=None,
    ):
        """
        The problem is we don't know if this is an animation or an image gen (or
        text gen); 
        """
        if template_environment is None:
            template_environment = {}

        log.info(
            "get_comfy_workflow(image_xml=%s, interface=%s, workflow_template=%s, video_index=%s)",
            image_xml.attrs["index"],
            interface,
            workflow_template,
            video_index,
        )

        if video_index not in [None, '']:
            video_tag = f"_{video_index:02d}"

        # animation = False
        # if mode.endswith("2v"):
        #     animation = True

        height = 1024
        width = 1024

        # is an image an input to this workflow?  If so we need to copy it to the comfyUI input directory.
        previous_image_xml = self.get_image(int(image_xml.attrs["index"]) - 1)
        # reasonable default
        prior_frame = ""
        previous_image_src = ""
        if previous_image_xml:
            previous_image_src = os.path.join(
                const.LIBRARY_DIR,
                self.get_paragraphdir(previous_image_xml.find_parent("paragraph").attrs["index"]),
                previous_image_xml.attrs.get("src", "error.png")
            )
            prior_frame = previous_image_src

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
                    image_plan = mplan["images"][int(previous_image_xml.attrs["index"])]
                except IndexError:
                    # try again with a new masterplan
                    # TODO: why is this such shit when the video->"regenerate master plan"
                    # appears to be doing it correctly?
                    mplan = self.get_masterplan(force=True)
                    image_plan = mplan["images"][int(previous_image_xml.attrs["index"])]

                frame_index = image_plan["frames"]
                prior_frame = self.get_last_frame(previous_image_xml, frame_index=frame_index)
            else:
                prior_frame = ""
        elif video_index not in [None, '']:
            # we are one of the subsequent videos in a sequence of videos, our
            # source image is the last frame of the _previous_ video.
            # (the critical -1 is buried in here)

            # get_comfy_workflow(image_xml=6, interface=ui, workflow_template=LTX23, video_index=1)
            image_index = int(image_xml.attrs["index"])

            source_dir = os.path.join(
                const.LIBRARY_DIR,
                self.get_paragraphdir(
                    image_xml.find_parent("paragraph").attrs["index"]
                ),
                "animation",
                f"image_{image_index:06d}_{video_index - 1:02d}",
            )

            source_image = os.path.join(source_dir, max(os.listdir(source_dir)))
            log.info("Source image for video_index %s is %s", video_index, source_image)

        next_image_xml = self.get_image(int(image_xml.attrs["index"]) + 1)
        next_image_src = ""
        
        if next_image_xml is not None:
            next_image_src = os.path.join(
                const.LIBRARY_DIR,
                self.get_paragraphdir(next_image_xml.find_parent("paragraph").attrs["index"]),
                next_image_xml.attrs.get("src", "") if next_image_xml is not None else ""
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
                )
            ), (
                prior_frame,
                os.path.join(
                    const.COMFY_DIRS["artifactserver"]["INPUT_DIR"],
                    os.path.basename(prior_frame),
                )
            ), (
                next_image_src,
                os.path.join(
                    const.COMFY_DIRS["artifactserver"]["INPUT_DIR"],
                    os.path.basename(next_image_src),
                )
            ), (
                previous_image_src,
                os.path.join(
                    const.COMFY_DIRS["artifactserver"]["INPUT_DIR"],
                    os.path.basename(previous_image_src),
                )                
            )
        ]
        log.info('preparing files_to_copy into comfy', files_to_copy=files_to_copy)
       
        for (source, destination) in files_to_copy:
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

        log.info("Using workflow template", workflow_template=workflow_template)

        workflow = comfy.load_workflow_template(
            interface, mode, workflow_template
        )

        if interface == "ui" and "nodes" not in workflow:
            raise ValueError(
                f'Invalid UI Workflow Template {workflow_template} -- For a UI template you must use plain "Export" in ComfyUI'
            )

        if interface == "api" and "nodes" in workflow:
            raise ValueError(
                f'Invalid API Workflow Template {workflow_template} -- For an API template you must use "Export API" in ComfyUI'
            )

        ####
        # Useful variables for workflow construction.  These get substituted
        # into the workflow template to create the actual workflow.
        ####
        log.info("Gathering Text and Image metadata for workflow construction...")

        if video_index not in [None, '']:
            animation_prompt = image_xml.attrs.get(f"animation_prompt{video_tag}", "")
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
        filename_prefix = f"{self.nice}_img_{image_xml.attrs['index']}_{workflow_template}"

        if video_index not in [None, '']:
            filename_prefix += video_tag

        # we want two paragraphs before and two after the paragraph containing this image.
        # we also want to prefix this paragraph with "#FOCUS_PARAGRAPH" so make it easier for the
        # LLM to manipulate it.
        # next_image_xml = image_xml
        # if "ti2i" in modes:
        next_image_xml = self.get_image(int(image_xml.attrs["index"]) + 1)

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
                    range(int(first_phrase_index), int(last_phrase_index) + 1)
                ),
            )

        meta = self.get_meta_dict()
        prompt_fn = (
            f"{self.nice}_img_{image_xml.attrs['index']}_{workflow_template}.prompt"
        )
        #previous_image = image_xml.find_previous_sibling("image")
        scene_description_fn = (
            f"{self.nice}_img_{image_xml.attrs['index']}_{workflow_template}.txt"
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

        # the funky "blah:int" syntax is what lets us
        # have a valid json template with {"cow": "{{blah:int}}"} in it
        # and get {"cow": 5} instead of {"cow": "5"} in the final workflow.
        base_template_environment = {
            "WIDTH": str(width),
            "HEIGHT": str(height),
            "DURATION": str(duration_in_seconds), # float
            "FILENAME_PREFIX": filename_prefix,
            "FILE_NAME": filename_prefix,
            "FIRST_FRAME": first_frame,
            "INPUT_IMAGE_PATH": const.COMFY_DIRS["comfyui"]["INPUT_DIR"],
            "INPUT_IMAGE": input_image,
            "LAST_FRAME": last_frame,
            "META": json.dumps(meta, indent=2),
            "NEGATIVE_PROMPT": negative_prompt,
            "OUTPUT_DIR": const.COMFY_DIRS["comfyui"]["OUTPUT_DIR"],
            "PREVIOUS_IMAGE": previous_image_src,
            "PRIOR_FRAME": os.path.basename(prior_frame) if prior_frame is not None else "",
            "PRIOR_RESULT": "",
            "PROMPT_FN": prompt_fn,
            "PROMPT": self.get_prompt(image_xml),
            "SCENE_DESCRIPTION_FN": scene_description_fn,
            "SCENE": json.dumps(scene, indent=2),
            "SOURCE": our_source,
            "STYLE": "Denslow",
            "SYSTEM_PROMPT": "You are a helpful assistant for generating images based on the text of a book.  You are given a snippet of text from the book, and you use that snippet to generate an image that captures the essence of that text.  You have access to the full text of the book, but you should focus on the snippet provided.  You can also use the scene description and meta information to inform your image generation.  Your goal is to create an image that is faithful to the source material and captures the mood and details of the scene.",
        }

        if video_index not in [None, '']:  # we need 0 to be valid.
            # this is a video.
            base_template_environment["ANIMATION_PROMPT"] = animation_prompt
            
            if video_index > 0:
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
                        4 * const.FPS,  # 4 seconds max, for more you have to chain videos together.
                    )
                )
            else:
                # first video for this image
                base_template_environment["TOTAL_FRAMES"] = str(
                    min(
                        self.get_image_frames(image_xml),
                        4 * const.FPS,  # 4 seconds max, for more you have to chain videos together.
                    )
                )


        # overwrite template environment settings from template_envionment arg
        base_template_environment.update(template_environment)

        workflow = comfy.apply_template_environment(
            workflow=workflow,
            template_environment=base_template_environment
        )
        return workflow

    def get_paragraph_snippet(
        self,
        image_xml,
        context_before=2,
        context_after=1,
        focus_marker="FOCUS PARAGRAPH",
        focus_phrases=None,
    ):
        # log = logger.bind(
        #     chapter=self.key,
        #     image_index=image_xml.attrs['index'],
        #     context_before=context_before,
        #     context_after=context_after,
        #     focus_marker=focus_marker,
        #     focus_phrases=focus_phrases
        # )
        # context prior paragraphs

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
        for x in range(context_before):
            if previous_paragraph is not None:
                previous_paragraph = previous_paragraph.find_previous_sibling("paragraph")

        for x in range(context_before):
            if previous_paragraph is not None:
                previous_paragraph = previous_paragraph.find_next_sibling("paragraph")
                out.append(previous_paragraph.get_text().replace("\n", "") + "\n\n")

        out.append("#" + focus_marker)
        # should be the same as previous_paragraph
        parent_paragraph = image_xml.find_parent("paragraph")

        for phrase in parent_paragraph.find_all("phrase"):
            if int(phrase.attrs["index"]) in focus_phrases:
                out.append("<PHRASE>" + phrase.get_text() + "</PHRASE>\n")
            else:
                # log.info("Nope", phrase_index=int(phrase.attrs['index']), focus_phrases=focus_phrases)
                out.append(phrase.get_text().replace("\n", ""))

        # out.append(previous.get_text().replace("\n", "") + "\n\n")
        out.append("\n\n")

        # keep rolling with previous paragraph so even if we bork it up
        # we keep continuity.
        for x in range(context_after):
            if previous_paragraph is not None:
                previous_paragraph = previous_paragraph.find_next_sibling("paragraph")

            if previous_paragraph:
                out.append(previous_paragraph.get_text().replace("\n", "") + "\n\n")

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

    def is_first_image(self, image_xml):
        """
        is this the first image in the paragraph?
        """
        return image_xml.find_previous_sibling("image") is None

    def is_last_image(self, image_xml):
        """
        is this the last image in the paragraph?
        """
        return image_xml.find_next_sibling("image") is None

    def get_scene(self, image_xml) -> dict:
        ch = {}
        characters = image_xml.attrs.get("scene_characters", "").split(",")
        for character_name in characters:
            if character_name:
                ch[character_name] = {
                    "pose": image_xml.attrs.get(f"{character_name}_pose", ""),
                    "action": image_xml.attrs.get(f"{character_name}_action", ""),
                    "location": image_xml.attrs.get(f"{character_name}_location", ""),
                    "description": image_xml.attrs.get(
                        f"{character_name}_description", ""
                    ),
                }

        out = {
            "setting": image_xml.attrs.get("setting", ""),
            "tod": image_xml.attrs.get("tod", ""),
            "camera": image_xml.attrs.get("camera", ""),
            "lighting": {
                "direction": image_xml.attrs.get("lighting_direction", ""),
                "quality": image_xml.attrs.get("lighting_quality", ""),
                "source": image_xml.attrs.get("lighting_source", ""),
            },
            # easy way to put your thumb on the scale.
            # [PARAGRAPH META] is the LAW!!
            "description": image_xml.attrs.get("description", ""),
        }

        focus_character = image_xml.attrs.get("focus_character", "")
        if focus_character and focus_character in ch:
            out["character_focus"] = focus_character

        if ch:
            out["characters"] = ch

        # deep-remove every key with an empty value,
        # we want to descent into dicts inside 'out' and remove empty values there too.
        def deep_clean(d):
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

    def get_phrase(self, phrase_index: int):
        """
        Get a specific phrase by index
        """
        self.load_xml()  # cached load
        phrase_xml = self.soup.find("phrase", index=str(phrase_index))

        if phrase_xml is None:
            log.error("Phrase with index %s not found", phrase_index)
            return None

        return phrase_xml

        if isinstance(phrase_index, str):
            if phrase_index.isdigit():
                phrase_index = int(phrase_index)
            else:
                log.error("Invalid phrase index", phrase_index=phrase_index)
                return None

        if phrase_index < 0 or phrase_index >= len(all_phrases):
            return None
        return all_phrases[phrase_index]

    def get_sound(self, sound_index: int):
        """
        Get a specific sound by index
        """
        self.load_xml()  # cached load
        all_sounds = self.soup.findAll("sound")

        if isinstance(sound_index, str):
            if sound_index.isdigit():
                sound_index = int(sound_index)
            else:
                log.error("Invalid sound index", sound_index=sound_index)
                return None

        if sound_index < 0 or sound_index >= len(all_sounds):
            return None
        return all_sounds[sound_index]

    def get_aspect(self):
        aspect = self.config.get("aspect_ratio", "widescreen")
        if aspect not in ["widescreen", "portrait"]:
            # one more chance..
            try:
                aspect = {
                    'landscape': 'widescreen',
                }[aspect]
            except KeyError:
                log.error("Invalid aspect ratio in config, defaulting to widescreen", aspect=aspect)
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

    def get_chapterdir(self):
        return self.chapterdir

    def get_paragraph_dir(self, paragraph_index):
        return os.path.join(self.chapterdir, "paragraphs", f"{paragraph_index:0>6}")

    def get_xml(self):
        self.load_xml()
        return self.soup

    def load_xml(self, force=False):
        """
        Read the xml of the book into self.soup

        returns true if we got contents
        """
        if self.STUB:
            self.soup = BeautifulSoup("<book></book>", "xml")
            return self.soup

        if self.soup is not None:
            log.info('Chapter XML already loaded')
            if not force:
                log.info('Cache hit')
                return self.soup
            else:
                log.info('** Forced reload of chapter XML **')
        
        log.info("Cache not loaded, loading fresh XML for chapter")

        xml_fn = self.get_xml_fn()

        if os.path.exists(xml_fn):
            with open(xml_fn, "r") as h:
                self.soup = BeautifulSoup(h.read(), "xml")

            book = self.soup.find("book")
            if book is None:
                raise ValueError("Invalid XML: No <book> tag found in XML file")

            old_version = float(book.attrs.get("version", "0.0"))
            self.soup = xml_upgrade(self, self.soup)

            new_version = float(book.attrs.get("version", "0.0"))
            if f"{old_version}" != f"{new_version}":   
                self.save_xml()
                log.info("Upgraded XML from version %s to version %s", old_version, new_version)
        else:
            self.soup = BeautifulSoup("<book></book>", "xml")

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
            log.error('book not loaded!', book_fn=book_fn)
            self.load_xml()

        pretty = self.soup.prettify()

        as_split = pretty.splitlines()
        if len(as_split) > 10:
            beginning = as_split[:5]
            ending = as_split[-5:]
            log.info("pre-samples", beginning=beginning, ending=ending)

        log.info("Generated XML in bytes", len_pretty=len(pretty), book_fn=book_fn)
        with open(book_fn, "w") as h:
            h.write(pretty)

        log.info("Finished saving book as XML")
        with open(book_fn, "r") as h:
            as_split = h.read().splitlines()
            if len(as_split) > 10:
                beginning = as_split[:5]
                ending = as_split[-5:]
                log.info("samples", beginning=beginning, ending=ending)
            else:
                log.info("samples", lines=as_split)

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

    def get_config(self, force=False):
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

    def get_masterplan(self, force=False):
        """
        Generate a masterplan for the chapter
        """
        return masterplan.get_masterplan(self, force=force)

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

        last_frame = mplan["words"][-1]["end_frame"]
        # slots for every frame, we want the scroll_lock value for each frame.
        _frame_to_camera = [None] * (last_frame + 1)

        current_frame = 0

        self.scrolling_rate = None
        # set scrolling rate to the average required to place the bottom of the last
        # line in the center of the screen when the audio finishes.

        # chapter = typography.page_segment.Chapter(
        #     chapterdir=self.chapterdir, aspect=aspect
        # )

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
            if word["fullscreen"]:
                continue
            scrolled_frames += 1

        self.scrolling_rate = height / float(scrolled_frames)
        log.info(
            f"In order to traverse {height} pixels of text height in {scrolled_frames} frames"
        )
        log.info(
            f"Setting initial scrolling rate to {self.scrolling_rate} pixels per frame"
        )

        scroll_lock = 0
        for current_frame in range(last_frame + 1):
            word = self.frame_to_word(mplan, current_frame)

            # skip over any fullscreen frames, they will fall-through to None
            # and we don't want to advance scroll lock.
            if word["fullscreen"]:
                continue

            _frame_to_camera[current_frame] = scroll_lock
            scroll_lock += self.scrolling_rate

        log.info("Frame to camera built with %s frames", len(_frame_to_camera))
        camera.set_frame_to_camera(_frame_to_camera)
        camera.save_camera(self)
        return

    def frame_to_image(self, mplan, frame_index):
        """
        Given a frame index, return the image dict for that frame.
        """
        return masterplan.frame_to_image(mplan, frame_index)

    def frame_to_word(self, mplan, frame_index):
        """
        Based on the 'words' section of masterplan, determine which 'word'
        object governs the given frame_index.
        """
        # quick access to get the last word.
        if frame_index == -1:
            return mplan["words"][-1]

        for word in mplan["words"]:
            if (word["start_frame"] <= frame_index) and (
                word["end_frame"] >= frame_index
            ):
                return word

        log.error("No word found for frame index %s", frame_index)
        return None

    def get_all_audio_tracks(self):
        mplan = self.get_masterplan()
        audio_tracks = []
        for word in mplan["words"]:
            audio_tracks.append(
                os.path.join(
                    const.LIBRARY_DIR, word["paragraph_dir"], word["src"].lstrip("/")
                )
            )

        return audio_tracks

    def safe_title(self):
        log.info(self.config)
        full_title = self.config["title"]
        safe_title = re.sub(r"[/\\?%*:|\"<>\x7F\x00-\x1F]", "-", full_title)
        return safe_title.replace(" ", "_")

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


def xml_upgrade(chapter, soup):
    """
    Upgrade the XML to the latest version.
    """
    log.info('xml_upgrade called') 
    book = soup.find("book")
    version = float(book.attrs.get("version", "0.0"))

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

        book.attrs["version"] = "1.0"

    if version < 1.1:
        log.warning("Upgrading XML from version %s to 1.1", version)
        for image_xml in soup.findAll("image"):
            if "animation_method" in image_xml.attrs:
                log.info('Fixing up animation_method to be versioned')
                # update "bare" animation metadata to be clearly versioned for multiple-video sequences.
                # these are numbered from 0 and typically are :02d
                image_xml.attrs["animation_method_00"] = image_xml.attrs["animation_method"]
                del image_xml.attrs["animation_method"]
            
            if "animation_prompt" in image_xml.attrs:
                log.info('Fixing up animation_prompt to be versioned')
                image_xml.attrs["animation_prompt_00"] = image_xml.attrs["animation_prompt"]
                del image_xml.attrs["animation_prompt"]

            if "workflow_i2v_template" in image_xml.attrs:
                log.info('Fixing up workflow_i2v_template to be versioned')
                image_xml.attrs["workflow_animation_template_00"] = image_xml.attrs["workflow_i2v_template"]
                del image_xml.attrs["workflow_i2v_template"]

            # and some obsolete fields
            if "meta_prompt" in image_xml.attrs:
                log.info('Removing obsolete meta_prompt field')
                del image_xml.attrs["meta_prompt"]

            if "workflow_name" in image_xml.attrs:
                log.info('Removing obsolete workflow_name field')
                del image_xml.attrs["workflow_name"]

            if "workflow_template" in image_xml.attrs:
                log.info('Renaming workflow_template to workflow_image_template')
                image_xml.attrs["workflow_image_template"] = image_xml.attrs["workflow_template"]
                del image_xml.attrs["workflow_template"]

            if "workflow_t2i_template" in image_xml.attrs:
                log.info('Renaming workflow_t2i_template to workflow_image_template')
                image_xml.attrs["workflow_image_template"] = image_xml.attrs["workflow_t2i_template"]
                del image_xml.attrs["workflow_t2i_template"]

            if "workflow_animation_template" in image_xml.attrs:
                log.info('Renaming workflow_animation_template to workflow_animation_template_00')
                image_xml.attrs["workflow_animation_template_00"] = image_xml.attrs["workflow_animation_template"]
                del image_xml.attrs["workflow_animation_template"]

            # obsolete
            if "styled_prompt" in image_xml.attrs:
                log.info('Removing obsolete styled_prompt field')
                del image_xml.attrs["styled_prompt"]

            if "t5_prompt" in image_xml.attrs:
                log.info('Renaming t5_prompt to prompt')
                if "prompt" not in image_xml.attrs:
                    image_xml.attrs["prompt"] = image_xml.attrs["t5_prompt"]

                del image_xml.attrs["t5_prompt"]

            if "tab" in image_xml.attrs:
                log.info('Removing obsolete tab field')
                del image_xml.attrs["tab"]

            if "¬" in image_xml.attrs.get("prompt", ""):
                log.info('Cleaning up prompt field to remove styled prompt metadata')
                # we have a styled prompt, we do not want that.  These are often nested a few layers, so we'll unwrap it.
                while "¬" in image_xml.attrs.get("prompt", ""):
                    first_comma = image_xml.attrs["prompt"].find(",")
                    last_weird = image_xml.attrs["prompt"].rfind("¬")
                    
                    if first_comma != -1 and last_weird != -1 and last_weird > first_comma:
                        # we have a comma and a weird char, and the weird char
                        image_xml.attrs["prompt"] = image_xml.attrs["prompt"][first_comma+1:last_weird]
                    else:
                        break
            
            if "styled" in image_xml.attrs:
                log.info('Removing obsolete styled field')
                del image_xml.attrs["styled"]

            if "workflow_animation_template_1" in image_xml.attrs:
                log.info('Renaming workflow_animation_template_1 to workflow_animation_template_01')
                image_xml.attrs["workflow_animation_template_01"] = image_xml.attrs["workflow_animation_template_1"]
                del image_xml.attrs["workflow_animation_template_1"]

            if "animation_method_1" in image_xml.attrs:
                log.info('Renaming animation_method_1 to animation_method_01')
                image_xml.attrs["animation_method_01"] = image_xml.attrs["animation_method_1"]
                del image_xml.attrs["animation_method_1"]

            if "animation_mode_1" in image_xml.attrs:
                log.info('Renaming animation_mode_1 to animation_mode_01')
                image_xml.attrs["animation_mode_01"] = image_xml.attrs["animation_mode_1"]
                del image_xml.attrs["animation_mode_1"]

            if "Narrator_action" in image_xml.attrs:
                log.info('Removing obsolete Narrator_action field')
                del image_xml.attrs["Narrator_action"]

            if "Narrator_description" in image_xml.attrs:
                log.info('Removing obsolete Narrator_description field')
                del image_xml.attrs["Narrator_description"]
            
            if "Narrator_location" in image_xml.attrs:
                log.info('Removing obsolete Narrator_location field')
                del image_xml.attrs["Narrator_location"]

            if "None_location" in image_xml.attrs:
                log.info('Removing obsolete None_location field')
                del image_xml.attrs["None_location"]

            for key in ["animation_method_00", "animation_method_01"]:
                if key in image_xml.attrs:
                    if image_xml.attrs[key] == "comfy_ui":
                        log.info('Updating animation_method to use a specific comfy_ui workflow')
                        if image_xml.find_next_sibling("image"):
                            # there is an image after this one, with this paragraph.
                            image_xml.attrs[key] = "comfy_ui_flf2v"
                        else:
                            # this is the final (or only) image in this paragraph.
                            image_xml.attrs[key] = "comfy_ui_i2v"

            if "clip_prompt" in image_xml.attrs:
                log.info('Removing obsolete clip_prompt field')
                del image_xml.attrs["clip_prompt"]

        
        for paragraph_xml in book.findAll("paragraph"):
            paragraph_dir = chapter.get_paragraph_dir(paragraph_xml.attrs["index"])
            animation_dir = os.path.join(
                const.LIBRARY_DIR,
                paragraph_dir,
                "animation"
            )

            if os.path.exists(animation_dir):
                for dirname in os.listdir(animation_dir):
                    if dirname.endswith(","):
                        # remove animation directories ending with a comma (was a bug)
                        dirpath = os.path.join(animation_dir, dirname)
                        if os.path.isdir(dirpath):
                            shutil.rmtree(dirpath)

                    split_under = dirname.split('_')
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
                                log.warning('New animation directory already exists, skipping rename and deleting old directory.', old_dirpath=old_dirpath, new_dirpath=new_dirpath)
                                shutil.rmtree(old_dirpath)
                            else:
                                log.info('Renaming animation directory', old_dirpath=old_dirpath, new_dirpath=new_dirpath)
                                os.rename(old_dirpath, new_dirpath)                                    

                    elif len(split_under) == 2:
                        video_index = 0
                        image_index = int(split_under[-1])
                        new_dirname = f"image_{image_index:06d}_{video_index:02d}"
                        
                        new_dirpath = os.path.join(animation_dir, new_dirname)
                        old_dirpath = os.path.join(animation_dir, dirname)
                        if os.path.exists(new_dirpath):
                            log.warning('New animation directory already exists, skipping rename', new_dirpath=new_dirpath)
                        else:
                            log.info('Renaming animation directory', old_dirpath=old_dirpath, new_dirpath=new_dirpath)
                            os.rename(old_dirpath, new_dirpath)

        book.attrs["version"] = "1.1"
    else:
        log.info("XML version is up to date (%s), no upgrade needed", version)
    
    return soup