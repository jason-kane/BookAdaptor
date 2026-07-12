# Should only be sourced on the GPU server side.

import gc
import random

import torch
from diffusers import (
    DiffusionPipeline,
)
from transformers import (
    BitsAndBytesConfig,
    T5EncoderModel,
)

from text_to_image.registry import registry
from text_to_image.base import TextToImageProvider
from . import config
import logger


log = logger.log(__name__)

# actions are triggered by the GPU service, which is in turn triggered by the
# global drawing fifo.  We are serialized, we have the complete GPU available
# for our use.

# little harness so we play nice.  Advertise ourselves to the GPU service.
class StableDiffusionProvider(TextToImageProvider):
    key = "sd.1.14"
    cosmetic = "Stable Diffusion 1.4"

    def generate_image(self, clip_prompt: str) -> bytes:
        """
        Response is a PIL Image.
        """
        return local_diffusion_14(clip_prompt)

registry.add(StableDiffusionProvider)


def local_diffusion_14(prompt):
    """
    About the same speed as Meissonic (5 minutes +/-)
    Qualify.. well, not very impressive. dall-e is significantly faster and better.
    """
    from diffusers import DPMSolverMultistepScheduler, StableDiffusionPipeline

    #model_id = "stabilityai/stable-diffusion-2-1"
    model_id = "CompVis/stable-diffusion-v1-4"
    pipe = StableDiffusionPipeline.from_pretrained(
        model_id,
        torch_dtype=torch.float16,
        safety_checker=None
    )
    # requires_safety_checker = False
    #pipe.scheduler = DPMSolverMultistepScheduler.from_config(pipe.scheduler.config)
    pipe = pipe.to("cuda") 

    # Generate an image
    image = pipe(
        prompt,
        height=512,
        width=512,
        num_interence_steps=50
    ).images[0]

    my_art = AsciiArt.from_pillow_image(image)
    my_art.to_terminal()
    log.info(prompt)

    # Save the image
    return image