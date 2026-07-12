# we're going to fade out the old image and fade in the new one
# half the given duration for each.
import os
import json
import tempfile
import redis
from flask import url_for
import logger
import const
from artifact_editor import tools
from animations import Animation, registry

log = logger.log(__name__)


class Wan_2_2_5B(Animation):
    """Wan 2.2.5B test of animation feature."""
    cosmetic = "Wan 2.2 5B"
    key = "wan_2_2_5b"

    def prompt_enhance(self, prompt: str) -> str:
        """Enhance the prompt for better animation results."""

        with tempfile.NamedTemporaryFile(mode='w+', suffix='.txt', delete=True) as result_h:
            result_fn = result_h.name
            result_h.close()

            # ask qwen about it.
            with open('drawing.fifo', 'a') as fifo:
                fifo.write(
                    json.dumps([
                        'wan_animation_prompt_enhance',
                        prompt,
                        result_fn
                    ]) + "\n\n"
                )
        
            tools.wait_for(result_fn)
            with open(result_fn, 'r') as h:
                prompt = h.read().strip()

        return prompt

    def get_configuration_widgets(self, chapter, image_xml, video_index):
        """
        These are individually responseive for submitting themselves on-change.
        TODO: 
            Add htmx attributes to make them submit on-change.
            Add view handlers to receive the data and update the image xml
        """
        #paragraph_dir = image_xml.find_parent('paragraph').attrs.get('dir', '')
        # Aesop/Fables/chapter/0024/paragraphs/000000
        #author, title, _, chapter, _, paragraph = paragraph_dir.split('/')
        #chapter_url = f"{author}/{title}/{chapter}"
        
        animate_image_url = url_for(
            'library.book.chapter.images.update',
            **chapter.kwargs,
            image_index=image_xml.attrs.get('index', 0)
        )

        widget = f"""
        <wa-textarea
            label="Prompt"
            name="animation_prompt"
            placeholder="Describe the action you want from the image."
            hx-put="{animate_image_url}"
            value="{image_xml.attrs.get('animation_prompt', '')}"></wa-textarea>

        <wa-textarea
            label="Negative Prompt"
            name="animation_negative_prompt"
            placeholder="Describe what you do NOT want in the image."
            hx-put="{animate_image_url}"
            value="{image_xml.attrs.get('animation_negative_prompt', '')}"></wa-textarea>            
        """
        return widget

    def apply(self, chapter, image_xml, frame_directory, extend=False, prompt_enhance=True):
        """Apply the transition effect described by the "animate_<key>" attributes of image_xml.

        places the frames in frame_directory.  They need to be sortable by filename.
        """
        # which engine for alpha?  In any case, we block until it is done.
        # def animate_image(image_pfn, prompt, animate_frame_dir, image_tag):
        log.info('Creating animation frames in %s', frame_directory)
        os.makedirs(frame_directory, exist_ok=True)
        done_flag_fn = os.path.join(frame_directory, 'done.flag')
        
        if os.path.exists(done_flag_fn):
            os.unlink(done_flag_fn)

        paragraph = image_xml.find_parent('paragraph')

        image_pfn = os.path.join(
            const.LIBRARY_DIR,
            chapter.get_paragraph_dir(paragraph.attrs['index']),
            image_xml.attrs.get('src', None)
        )
        prompt = image_xml.attrs.get('animation_prompt', '')
        
        if not prompt:
            log.error(f'{image_xml.attrs=}')
            raise ValueError('Prompt is required.')

        if prompt_enhance and prompt:
            log.info(' Initial prompt: %s', prompt  )
            prompt = self.prompt_enhance(prompt)
            log.info('Enhanced prompt: %s', prompt)

        # fmt: off
        redis.Redis(host="redis").rpush('gpu_tasks', json.dumps(['animate_wan_2_2_5b', image_pfn, prompt, image_xml.attrs.get('negative_prompt', ''), frame_directory, extend, done_flag_fn, int(image_xml.attrs.get('frames', 0))]))
        # fmt: on
        
        # with open('drawing.fifo', 'a') as fifo:
        #     fifo.write(
        #         json.dumps([
        #             'animate_wan_2_2_5b',
        #             image_pfn,
        #             prompt,
        #             image_xml.attrs.get('negative_prompt', ''),
        #             frame_directory,
        #             extend,
        #             done_flag_fn,
        #             image_xml.attrs.get('frames', 0)
        #         ]) + "\n\n"
        #     )
    
        tools.wait_for(done_flag_fn)
        os.unlink(done_flag_fn)

        return 


registry.add_module(Wan_2_2_5B)
