# Should only be sourced on the UI server side.
import html
import json
import os

import redis
from flask import url_for
from PIL import Image

import artifact_editor.images.htmx as images_htmx
import artifact_editor.styles.htmx as styles_htmx
import const
import logger
from artifact_editor import tools
from artifact_editor.images import images
from artifact_editor.styles import styles
from text_to_image.base import TextToImageProviderUI
from text_to_image.registry import registry

log = logger.log(__name__)


# little harness so we play nice.
class TongyiZImageTurboProviderUI(TextToImageProviderUI):
    key = "tongyi.zimageturbo"
    cosmetic = "Z Image Turbo"

    def generate_ui(self, image_xml, with_class="") -> bytes:
        """
        Response is a string of HTML components.
        """
        # select widget for LoRas
        lora_select = styles_htmx.lora_selector()

        prompt_url = url_for(
            "library.book.chapter.images.update",
            author=self.chapter.author.name,
            title=self.chapter.title,
            chapter_number=self.chapter.number,
            language=self.chapter.language,
            image_index=image_xml.attrs["index"],
        )

        prompt = f"""<div style="width: 49%"><wa-textarea 
                class="smooth {with_class}"
                hx-put="{prompt_url}"
                hx-vals='js:{{respond_with: "prompt"}}'
                hx-swap="outerHTML transition:true"
                hx-trigger="change delay:500ms"
                hx-include="[name='prompt']"
                hx-target="#prompt"
                label="Prompt"
                name="prompt"
                id="prompt"
                cols=70
                rows=7
                value="{html.escape(image_xml.attrs.get('prompt', ''), quote=True)}"></wa-textarea>
            </div>"""

        style = image_xml.attrs.get("style", "")
        if style == "":
            style = self.chapter.config.get("default_style", "")
            image_xml.attrs["style"] = style

        apply_style_url = url_for(
            "library.book.chapter.images.apply_style",
            **self.chapter.kwargs,
            image_index=image_xml.attrs["index"],
        )

        style_widget = styles_htmx.add_style_widget(
            selected_style=image_xml.attrs.get("style", ""),
            url=apply_style_url,
            target="#prompt",
        )

        seed_widget = f"""<wa-slider
                class="smooth {with_class}"
                hx-put=""
                hx-vals='js:{{respond_with: "seed"}}'
                hx-swap="outerHTML transition:true"
                hx-trigger="change delay:500ms"
                hx-target="#seed"
                label="Seed"
                name="seed"
                id="seed"
                min=0
                max=4294967295
                step=1
                value="{html.escape(str(image_xml.attrs.get('seed', 1234)), quote=True)}"></wa-slider>
            </wa-slider>"""

        out = f"""
            <form>
                {lora_select}
                {prompt}
                {style_widget}
                {seed_widget}
            </form>
        """
        return out

    def generate_image(
            self, 
            chapter_key, 
            image_xml, 
            force=False,
            sample=False
        ) -> bytes:
        """
        You're going to want to .save_xml() after calling this to persist the change
        """
        if sample:
            sample = "True"
        else:
            sample = ""
        # prefer the styled prompt if there is one.
        # if "styled_prompt" in image_xml.attrs:
        #     log.info("Using existing styled_prompt from image_xml")
        #     prompt = image_xml.attrs["styled_prompt"]
        # else:
        #     log.info("No styled_prompt found in image_xml, using raw prompt")
        prompt = image_xml.attrs.get("prompt", "")
        loras = json.loads(image_xml.attrs.get("loras", "[]"))
        
        paragraph = image_xml.find_parent("paragraph")
        paragraph_dir=self.chapter.get_paragraph_dir(paragraph.attrs["index"])

        seed = str(image_xml.attrs.get("seed", 1234))

        styled = image_xml.attrs.get("styled", False)
        if styled:
            prompt = image_xml.attrs.get("styled_prompt", prompt)

        image_fn = images.get_image_fn(
            prompt=prompt,  # for hash purposes
            loras=loras,
            paragraph_dir=paragraph_dir,
            image_index=image_xml.attrs["index"],
            randomized=False
        )

        flag_fn = os.path.join(
            const.LIBRARY_DIR,
            paragraph_dir,
            os.path.basename(image_fn) + ".flag",
        )

        if os.path.exists(flag_fn):
            os.unlink(flag_fn)

        # why?  control shift 'f': redis.Redis(host="redis").rpush(
        # or even "text_to_image", it's easy to see globally every-where it goes into redis.
        # fmt: off
        redis.Redis(host="redis").rpush("gpu_tasks",json.dumps(["text_to_image", self.key, self.chapter.key, image_fn, prompt, flag_fn, seed, image_xml.attrs["index"], json.dumps(loras), sample]))
        # fmt: on
        log.info(f'Submitted: redis.Redis(host="redis").rpush("gpu_tasks",json.dumps(["text_to_image", {self.key=}, {self.chapter.key=}, {image_fn=}, {prompt=}, {flag_fn=}, {seed=}, {sample=}]))')
        tools.wait_for(flag_fn)

        # /home/jkane/books/active
        # L. Frank Baum/The Marvelous Land of Oz/chapter/0001/paragraphs/000015/
        # img_37_Line_Art_Drawing_mode__In_d9c41857_d124.png
        full_image_fn = os.path.join(
            const.LIBRARY_DIR,
            paragraph_dir,
            os.path.basename(image_fn),
        )

        image = Image.open(full_image_fn)

        if not sample:
            target_size = (const.IMG_TARGET_WIDTH, const.IMG_TARGET_HEIGHT)
            if image.size != target_size:
                log.info(f"Resizing image from {image.size} to {target_size}")
                image = image.resize(target_size)
                log.info("Saving image as %s", full_image_fn)
                image.save(full_image_fn)

        src = os.path.basename(image_fn)
        image_xml.attrs["src"] = src
        return src


print("Adding TongyiZImageTurboProviderUI...")
registry.add(TongyiZImageTurboProviderUI)


# little harness so we play nice.
class TSQNZImageTurboProviderUI(TongyiZImageTurboProviderUI):
    key = "tsqn.zimageturbo"
    cosmetic = "(tsqn) Z Image Turbo"

    def generate_ui(self, image_xml, with_class="") -> bytes:
        """
        Response is a string of HTML components.
        """
        # paragraph = image_xml.find_parent("paragraph")
        style = image_xml.attrs.get(
            "style", self.chapter.config.get("default_style", "")
        )
        image_xml.attrs["style"] = style

        apply_style_url = url_for(
            "library.book.chapter.images.apply_style",
            **self.chapter.kwargs,
            image_index=image_xml.attrs["index"],
        )

        style_widget = styles_htmx.add_style_widget(
            selected_style=image_xml.attrs["style"],
            url=apply_style_url,
            target="#prompt",
        )

        condense_image_prompt_button = images_htmx.condense_image_prompt_button(
            self.chapter, image_xml
        )
        create_fanciful_prompt_button = images_htmx.create_fanciful_prompt_button(
            self.chapter, image_xml
        )
        create_prompt_button = images_htmx.create_prompt_button(self.chapter, image_xml)
        scene_to_prompt = images_htmx.scene_to_prompt_button(self.chapter, image_xml)
        draw_button = images_htmx.draw_prompt_button(self.chapter, image_xml)
        draw_styled_prompt_button = images_htmx.draw_styled_prompt_button(
            self.chapter, image_xml
        )

        # hx-vals='js:{{respond_with: "seed"}}'
        seed_widget = f"""<wa-slider
            class="smooth"
            hx-put=""   
            hx-swap="none"
            hx-trigger="change delay:500ms"
            hx-target="#seed"
            label="Seed"
            name="seed"
            id="seed"
            min=0
            max=4294967295
            step=1
            value="{html.escape(str(image_xml.attrs.get('seed', 1234)), quote=True)}">
        </wa-slider>"""

        # ladies first.
        buttons = '<div class="wa-cluster" style="width: 100%;">'
        buttons += "".join(self.buttons)
        buttons += condense_image_prompt_button
        buttons += create_fanciful_prompt_button
        buttons += create_prompt_button
        buttons += scene_to_prompt
        buttons += draw_button
        buttons += draw_styled_prompt_button
        buttons += "</div>"

        prompt = image_xml.attrs.get("prompt", "")

        # the currently selected style, applied to the current prompt.
        if style:
            style_dict = styles.get_style(
                category_name="Custom",
                style_tag=style
            )
        else:
            style_dict = {}
    
        if style_dict:
            prompt_filter = style_dict.get("prompt", "{prompt}")
            negative_prompt = style_dict.get("negative_prompt", "")
            styled_prompt = prompt_filter.format(prompt=prompt)

        # outerHTML transition:true
        # response includes oob-swap directives.
        prompt_url = url_for(
            "library.book.chapter.images.update",
            author=self.chapter.author.name,
            title=self.chapter.title,
            chapter_number=self.chapter.number,
            language=self.chapter.language,
            image_index=image_xml.attrs["index"],
        )
        prompt_textarea = f"""
            <div>
                <wa-textarea 
                    class="smooth {with_class}"
                    size="large"
                    resize="auto"
                    hx-put="{prompt_url}"
                    hx-vals='js:{{respond_with: "prompt"}}'
                    hx-swap="none"
                    hx-trigger="change delay:500ms"
                    hx-target="#prompt"
                    label="Prompt"
                    name="prompt"
                    id="prompt"
                    cols=70
                    rows=7
                    value="{html.escape(image_xml.attrs.get('prompt', ''), quote=True)}">
                </wa-textarea>
                
            </div>
        """
        # <div id="styled-prompt">{styled_prompt}</div>
        # Styled token count: {len(styled_prompt) // 4}
        token_summary = f"""<div style="width: 49%; padding-left: 1em;" id="token-summary">
            <h4>ZImage Turbo Token limit is <b>512</b>.  Longer prompts will be truncated.</h4>

            <i>(approximate)</i><br/>
            Token count: {len(prompt) // 4}<br/>            
        </div>"""

        # select widget for LoRas
        lora_select = f"""
            <wa-select
                class="smooth {with_class}"
                hx-put=""
                hx-swap="none"
                hx-trigger="change delay:500ms"
                hx-target="#lora"
                label="LoRA"
                name="lora"
                id="lora"
            >
                <wa-option value="">None</wa-option>
                <wa-option value="Watercolor_V7_E10.safetensors">Watercolor</wa-option>
            </wa-select>
        """

        out = f"""
        <form>
            <div class="wa-stack" style="width: 100%">
                {lora_select}
                {prompt_textarea}
                {token_summary}
                {style_widget}
                {seed_widget}
                {buttons}
            </div>
        </form>
        """
        return out


registry.add(TSQNZImageTurboProviderUI)
