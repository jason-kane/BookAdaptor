import json
import os

from flask import request
from PIL import Image

import const
import logger
from artifact_editor import images, tools
# from artifact_editor.author.author import Author
#
from text_to_image.registry import registry

log = logger.log(__name__)
FIFO_FN = os.path.join(os.path.dirname(__file__), "..", "..", "drawing.fifo")


class TextToImageProvider:
    key = "base"
    cosmetic = "Base Text to Image"

    def generate_image(self, *args, **kwargs) -> bytes:
        """response is a PIL Image"""
        raise NotImplementedError("This method should be overridden by subclasses.")


class TextToImageProviderUI:
    key = "base"
    cosmetic = "Base Text to Image"
    buttons = []

    def __init__(self, chapter):
        #from artifact_editor.author.author import Author
        #from artifact_editor.chapter.chapter import Chapter
        #chapter_kwargs = tools.requestToChapterKwargs(request.url)
        #chapter_kwargs['author'] = Author(chapter_kwargs['author'])
        # self.chapter_key = chapter_key
        
        # args = tools.chapterkey_to_chapterargs(chapter_key)
        # author = Author(args.pop(0))
        # self.chapter = Chapter(author, *args)
        self.chapter = chapter

    def generate_ui(self) -> str:
        """
        Response is raw HTML components for the configuration UI.
        """
        return ""

    def generate_image(self, image_xml, force=False) -> bytes:
        """
        You're going to want to .save_xml() after calling this to persist the change
        """
        # prefer the styled prompt if there is one.
        if "styled_prompt" in image_xml.attrs:
            log.info("Using existing styled_prompt from image_xml")
            prompt = image_xml.attrs["styled_prompt"]
        else:
            log.info("No styled_prompt found in image_xml, using raw prompt")
            prompt = image_xml.attrs.get("prompt", "")

        paragraph = image_xml.getparent("paragraph")
        seed = str(image_xml.attrs.get("seed", 1234))
        
        image_fn = images.get_image_fn(
            prompt=prompt,  # for hash purposes
            loras=[],
            paragraph_dir=paragraph.attrs["dir"],
            image_index=image_xml.attrs["index"],
        )
        if force and os.path.exists(image_fn):
            log.info("Image already exists and force is set, deleting", image_fn=image_fn)
            os.unlink(image_fn)

        flag_fn = os.path.join(
            const.LIBRARY_DIR,
            paragraph.attrs["dir"],
            os.path.basename(image_fn) + ".flag"
        )
        
        if os.path.exists(flag_fn):
            os.unlink(flag_fn)
    
        with open(FIFO_FN, 'a') as fifo:
            fifo.write(
                json.dumps([
                    "text_to_image",
                    self.key,
                    self.chapter.key,
                    image_fn,
                    prompt,
                    flag_fn,
                    seed
                ]) + "\n\n"
            )

        tools.wait_for(flag_fn)

        full_image_fn = os.path.join(
            const.LIBRARY_DIR, 
            paragraph.attrs["dir"], 
            os.path.basename(image_fn)
        )
        
        image = Image.open(full_image_fn)
            
        target_size = (const.IMG_TARGET_WIDTH, const.IMG_TARGET_HEIGHT)
        if image.size != target_size:
            log.info("Resizing image from {image.size} to {target_size}", image_size=image.size, target_size=target_size)
            image = image.resize(target_size)
            image.save(full_image_fn)

        src = os.path.basename(image_fn)
        image_xml.attrs["src"] = src


    def add_button(self, button):
        self.buttons.append(button)




# registry.add(TextToImageProvider)

