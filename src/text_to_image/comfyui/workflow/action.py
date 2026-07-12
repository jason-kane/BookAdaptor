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
class FluxSchnellProvider(TextToImageProvider):
    key = "flux.schnell"
    cosmetic = "Flux Schnell"

    def generate_image(self, clip_prompt: str, t5_prompt: str) -> bytes:
        """
        Response is a PIL Image.
        """
        return local_flux_schnell(clip_prompt, t5_prompt)

registry.add(FluxSchnellProvider)


def disabled_safety_checker(images, clip_input):
    if len(images.shape) == 4:
        num_images = images.shape[0]
        return images, [False] * num_images
    else:
        return images, False


def local_flux_schnell(
    clip_prompt,  # short prompt
    t5_prompt=None,  # longer, more detailed prompt
):
    quantization_config = BitsAndBytesConfig(load_in_8bit=True)
    if t5_prompt is None:
        t5_prompt = clip_prompt

    model_id = "black-forest-labs/FLUX.1-schnell"  # needs 4 steps only - it is faster than the dev version as the name implies

    text_encoder = T5EncoderModel.from_pretrained(
        model_id,
        subfolder="text_encoder_2",
        quantization_config=quantization_config,
        torch_dtype=torch.bfloat16,  # bfloat16 and normal float16 both work - former gives a warning but seems to work
    )

    pipe = DiffusionPipeline.from_pretrained(
        model_id,
        torch_dtype=torch.bfloat16,  # bfloat16 and float16 both work, must match the T5
        text_encoder_2=text_encoder,
        device_map="balanced",
        max_memory={0: "11GiB", "cpu": "48GiB"},
    )
    #
    # pipe.enable_xformers_memory_efficient_attention()
    # not compatible with transformer_flux
    #
    pipe.safety_checker = disabled_safety_checker
    pipe.vae.enable_tiling()  # less memory usage at VAE time

    log.info(f"Using {clip_prompt=} and {t5_prompt=} to generate a new image...")
    image = pipe(
        clip_prompt,
        prompt_2=t5_prompt,
        num_images_per_prompt=1,
        guidance_scale=0.0,  # must be 0.0 for schnell version, dev version can be as per SD
        num_inference_steps=4,  # only need 4 for schnell version, dev version needs 50 or so
        max_sequence_length=256,  # relates to the T5 encoder - text_encoder_2 - max 256 for schnell
        generator=torch.Generator("cpu").manual_seed(int(random.randrange(4294967294))),
    ).images[0]

    del pipe
    del text_encoder
    gc.collect()

    torch.cuda.empty_cache()
    torch.cuda.ipc_collect()
    return image
