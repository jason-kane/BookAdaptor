# Should only be sourced on the GPU server side.
import gc
import os
import json
from redis import Redis
import torch
from diffusers import (
    AutoencoderKL,
    FlowMatchEulerDiscreteScheduler,
    ZImagePipeline,
    ZImageTransformer2DModel,
)
from huggingface_hub import hf_hub_download, snapshot_download
import tqdm
from transformers import (
    Qwen2Tokenizer,
    Qwen3Model,
)

import const
import logger
from text_to_image.base import TextToImageProvider
from text_to_image.registry import registry

log = logger.log(__name__)

# actions are triggered by the GPU service, which is in turn triggered by the
# global drawing fifo.  We are serialized, we have the complete GPU available
# for our use.


# out here, because it won't live here.
def progress_bar_callback(pipeline, step, timestep, latents):
    """
    You're gonna need to have pipe.redis and pipe.redis_key set for this to work.
    """
    # if step == 0:
        # Initialize tqdm bar on first step
        # pipeline.progress_bar = tqdm.tqdm(total=pipeline.num_inference_steps)
    
    # Update progress bar
    # pipeline.progress_bar.update(1)
    
    # Optional: access metadata
    # print(f"Step: {step}, Timestep: {timestep}")
    pipeline.redis.rpush(pipeline.redis_key, f"Inference step {step} {timestep}")

    return latents # Must return latents


# little harness so we play nice.  Advertise ourselves to the GPU service.
class TongyiZImageTurboProvider(TextToImageProvider):
    key = "tongyi.zimageturbo"
    cosmetic = "Z Image Turbo"

    def generate_image(self, chapter_key: str, image_fn: str, prompt: str, flag_fn: str, seed: int = 1234, image_index: int = 0, lora_weights: list = [], sample: str = "") -> bytes:
        """
        Response is a PIL Image.
        """
        log.info('Involking Tongyi Z-Image-Turbo provider...')
        redis_key = f"websocket_{chapter_key}_{image_index}"
        seed = int(seed)
        return local_tongyi_zimageturbo(image_fn, prompt, flag_fn, seed=seed, redis_key=redis_key, lora_weights=lora_weights, sample=sample)

def local_tongyi_zimageturbo(
    image_fn: str,
    prompt: str,  # short prompt
    lock_fn: str,
    seed: int = 1234,
    redis_key: str = "",
    lora_weights: list = [],
    sample: str = "",
):
    redis = Redis()
    # how many steps there are going to be, for progress bar purposes.  This is a guess.
    redis.rpush(redis_key, 17)  
    redis.rpush(redis_key, f"Starting image generation with seed {seed}...")
    # switch to "mps" for apple devices
    # AutoPipelineForText2Image.from_pretrained(
    # quantization_config = BitsAndBytesConfig(load_in_8bit=True)
    model_id = "Tongyi-MAI/Z-Image-Turbo"
    # text_encoder = T5EncoderModel.from_pretrained(
    #     model_id,
    #     subfolder="text_encoder_2",
    #     quantization_config=quantization_config,
    #     torch_dtype=torch.bfloat16   #bfloat16 and normal float16 both work - former gives a warning but seems to work
    # )
    # lock_fn = os.path.join(const.LIBRARY_DIR, lock_fn)

    if os.path.exists(lock_fn):
        log.info("not really a lock, it just means we screwed up.  %s", lock_fn)
        # it's cool.
        os.unlink(lock_fn)

    redis.rpush(redis_key, "Creating pipe...")
    pipe = ZImagePipeline.from_pretrained(
        model_id,
        torch_dtype=torch.bfloat16,
        low_cpu_mem_usage=False,
        max_memory={0: "11GiB", "cpu": "48GiB"},
    )

    log.info(f"Loading LoRA weights: {lora_weights}")
    adapters = []
    weights = []
    for index, lora in enumerate(lora_weights):
        pipe.load_lora_weights(
            const.LORA_DIR, 
            weight_name=lora,
            adapter_name=f"lora_{index}"
        )
        adapters.append(f"lora_{index}")

        # this bit is unfortunate.
        with open(os.path.join(const.LORA_DIR, lora + ".json"), "r") as f:
            lora_metadata = json.load(f)
            weight = lora_metadata.get("weight", 0.8)
            weights.append(weight)

            # prefix the prompt with the _first_ trigger word, if it exists.
            if lora_metadata.get("trigger_words", []):
                trigger_word = lora_metadata["trigger_words"][0]
                prompt = trigger_word + " " + prompt

    if adapters:
        pipe.set_adapters(adapters, adapter_weights=weights)


    redis.rpush(redis_key, "Configuring pipe...")
    pipe.redis = redis
    pipe.redis_key = redis_key
    pipe.enable_model_cpu_offload()
    pipe.enable_sequential_cpu_offload()
    pipe.safety_checker = lambda images, **kwargs: (images, False)

    redis.rpush(redis_key, "Beginning Inference...")
    image = pipe(
        prompt,
        height=512 if sample else 1024,
        width=512 if sample else 1024,
        num_inference_steps=5,
        guidance_scale=0.0,
        generator=torch.Generator("cuda").manual_seed(seed),
    ).images[0]

    image_pfn = os.path.join(
        const.LIBRARY_DIR,
        image_fn
    )

    image.save(image_pfn)
    redis.rpush(redis_key, f"Saved {image_pfn}")
    log.info("Saved %s", image_pfn)

    with open(lock_fn, "w") as f:
        f.write("Done")

    log.info("Cleaning video memory...")
    del pipe
    gc.collect()

    torch.cuda.empty_cache()
    torch.cuda.ipc_collect()

    log.info("Generation complete.")
    redis.rpush(redis_key, "Complete")
    return image

registry.add(TongyiZImageTurboProvider)


class TSQNZImageTurboProvider(TextToImageProvider):
    key = "tsqn.zimageturbo"
    cosmetic = "(tsqn) Z Image Turbo"

    def generate_image(self, chapter_key: str, image_fn: str, prompt: str, flag_fn: str, seed: int = 1234, image_index: int = 0, lora_weights: list = [], sample: str = "") -> bytes:
        """
        Response is a PIL Image.
        """
        log.info('Invoking TSQN Z-Image-Turbo provider...')
        
        # b'websocket_["L. Frank Baum", "The Marvelous Land of Oz", "1", "english"]_37'
        redis_key = f"websocket_{chapter_key}_{image_index}"
        seed = int(seed)
        return local_tsqn_zimageturbo(image_fn, prompt, flag_fn, seed=seed, redis_key=redis_key, lora_weights=lora_weights, sample=sample)

def download_tsqn_zimageturbo_model(force=False):
    REPO_ID = "tsqn/Z-Image-Turbo_fp32-fp16-bf16_full_and_ema-only"
    local_dir = os.path.join(
        const.MODEL_CACHE_DIR,
        "models--tsqn--Z-Image-Turbo_fp32-fp16-bf16_full_and_ema-only"
    )

    TRANSFORMER_DIR = os.path.join(local_dir, "transformer")
    os.makedirs(TRANSFORMER_DIR, exist_ok=True)

    TEXT_ENCODER_DIR = os.path.join(local_dir, "text_encoder")
    os.makedirs(TEXT_ENCODER_DIR, exist_ok=True)

    VAE_DIR = os.path.join(local_dir, "vae")
    os.makedirs(VAE_DIR, exist_ok=True)

    if not os.path.exists(
        os.path.join(TRANSFORMER_DIR, "diffusion_pytorch_model.safetensors")
    ) or force:
        snapshot_download(
            repo_id=REPO_ID, 
            ignore_patterns="*.safetensors", 
            local_dir=local_dir,
        )
        hf_hub_download(
            repo_id=REPO_ID,
            filename="diffusion_pytorch_model-ema-only-fp32.safetensors",
            local_dir=local_dir,
        )
        os.rename(
            os.path.join(local_dir, "diffusion_pytorch_model-ema-only-fp32.safetensors"), 
            os.path.join(TRANSFORMER_DIR, "diffusion_pytorch_model.safetensors")
        )

    if not os.path.exists(
        os.path.join(TEXT_ENCODER_DIR, "model.safetensors")
    ) or force:
        hf_hub_download(
            repo_id=REPO_ID,
            subfolder="text_encoder",
            filename="qwen_3_4b_bf16.safetensors",
            local_dir=local_dir,
        )
        os.rename(
            os.path.join(TEXT_ENCODER_DIR, "qwen_3_4b_bf16.safetensors"),
            os.path.join(TEXT_ENCODER_DIR, "model.safetensors")
        )

    if not os.path.exists(
        os.path.join(VAE_DIR, "diffusion_pytorch_model.safetensors")
    ) or force:
        hf_hub_download(
            repo_id=REPO_ID,
            subfolder="vae",
            filename="ae_bf16.safetensors",
            local_dir=local_dir,
        )
        os.rename(
            os.path.join(VAE_DIR, "ae_bf16.safetensors"),
            os.path.join(VAE_DIR, "diffusion_pytorch_model.safetensors")
        )

def local_tsqn_zimageturbo(
    image_fn: str,
    prompt: str,  # short prompt
    lock_fn: str,
    seed: int = 1234,
    redis_key: str = None,
    lora_weights: list = [],
    sample: str = "",
):
    image_pfn = os.path.join(
        const.LIBRARY_DIR, 
        image_fn
    )

    if not image_pfn.endswith(".png"):
        image_pfn += ".png"    
    
    if os.path.exists(image_pfn):
        log.info(f"Image already exists at {image_pfn}, skipping generation.")
        with open(lock_fn, "w") as f:
            f.write("Done")
        return
    
    redis = Redis()
    # https://redis.io/docs/latest/commands/rpush/
    redis.rpush(redis_key, 17)  # how many steps there are going to be, for progress bar purposes.  
    redis.rpush(redis_key, f"Starting image generation with seed {seed}...")
    
    model_path = os.path.join(
        const.MODEL_CACHE_DIR, 
        "models--tsqn--Z-Image-Turbo_fp32-fp16-bf16_full_and_ema-only"
    )

    log.info('model path: %s', model_path)
    redis.rpush(redis_key, f"Verifying model path: {model_path}")
    if not os.path.exists(model_path):
        log.info("Downloading TSQN Z-Image-Turbo model...")
        download_tsqn_zimageturbo_model()
        log.info("Download complete.")

    lock_fn = os.path.join(const.LIBRARY_DIR, lock_fn)

    if os.path.exists(lock_fn):
        log.info("not really a lock, it just means we screwed up.  %s", lock_fn)
        # it's cool.
        os.unlink(lock_fn)

    redis.rpush(redis_key, "Creating pipe")
    pipe = ZImagePipeline.from_pretrained(
        model_path,
        torch_dtype=torch.bfloat16,
        vae=AutoencoderKL.from_pretrained(model_path, subfolder="vae", torch_dtype=torch.bfloat16),
        text_encoder=Qwen3Model.from_pretrained(model_path, subfolder="text_encoder", torch_dtype=torch.bfloat16),
        tokenizer=Qwen2Tokenizer.from_pretrained(model_path, subfolder="tokenizer"),
        transformer=ZImageTransformer2DModel.from_pretrained(model_path, subfolder="transformer", torch_dtype=torch.float32),
        low_cpu_mem_usage=False,
        max_memory={0: "11GiB", "cpu": "48GiB"},
    )

    # Watercolor_V7_E10.safetensors
    # "VarcoterolV7 art style"
    log.info(f"Loading LoRA weights: {lora_weights}")
    adapters = []
    weights = []
    for index, lora in enumerate(lora_weights):
        pipe.load_lora_weights(
            const.LORA_DIR, 
            weight_name=lora,
            adapter_name=f"lora_{index}"
        )
        adapters.append(f"lora_{index}")

        # this bit is unfortunate.
        with open(os.path.join(const.LORA_DIR, lora + ".json"), "r") as f:
            lora_metadata = json.load(f)
            weight = lora_metadata.get("weight", 0.8)
            weights.append(weight)

            # prefix the prompt with the _first_ trigger word, if it exists.
            if lora_metadata.get("trigger_words", []):
                trigger_word = lora_metadata["trigger_words"][0]
                log.info(f"Prepending trigger word '{trigger_word}' to prompt for LoRA {lora}")
                prompt = trigger_word + " " + prompt
            else:
                log.info(f"No trigger words found for LoRA {lora}")

    if adapters:
        pipe.set_adapters(adapters, adapter_weights=weights)

    pipe.redis = redis
    pipe.redis_key = redis_key
    # [Optional] Attention Backend
    # Diffusers uses SDPA by default. Switch to Flash Attention for better efficiency if supported:
    # pipe.transformer.set_attention_backend("flash")    # Enable Flash-Attention-2
    # pipe.transformer.set_attention_backend("_flash_3") # Enable Flash-Attention-3

    # [Optional] Model Compilation
    # Compiling the DiT model accelerates inference, but the first run will take longer to compile.
    # pipe.transformer.compile()

    redis.rpush(redis_key, "Creating Scheduler")
    pipe.scheduler = FlowMatchEulerDiscreteScheduler.from_config(
        pipe.scheduler.config
    )

    # pipe.enable_model_cpu_offload()
    pipe.enable_sequential_cpu_offload()
    pipe.safety_checker = lambda images, **kwargs: (images, False)

    redis.rpush(redis_key, "Beginning Inference...")
    log.info('Text to image: %s', prompt)
    image = pipe(
        prompt,
        height=512 if sample else 1024,
        width=512 if sample else 1024,
        num_inference_steps=9,
        guidance_scale=0.0,
        generator=torch.Generator("cuda").manual_seed(seed),
        callback_on_step_end=progress_bar_callback,
        callback_on_step_end_tensor_inputs=["latents"] # Required to pass latents
    ).images[0]
    
    os.makedirs(os.path.dirname(image_pfn), exist_ok=True)
    image.save(image_pfn)
    redis.rpush(redis_key, f"Saved {image_pfn}")
    log.info("Saved %s", image_pfn)

    with open(lock_fn, "w") as f:
        f.write("Done")

    log.info("Cleaning video memory...")
    del pipe
    gc.collect()

    torch.cuda.empty_cache()
    torch.cuda.ipc_collect()

    log.info("Generation complete.")
    redis.rpush(redis_key, "Complete")
    return image

registry.add(TSQNZImageTurboProvider)


# class TSQNZImageTurboProvider(TextToImageProvider):
#     key = "tsqn.zimageturbo"
#     cosmetic = "(tsqn) Z Image Turbo"

#     def generate_image(self, progress_key: str, image_fn: str, prompt: str, flag_fn: str, seed: int = 1234) -> bytes:
#         """
#         Response is a PIL Image.
#         """
#         log.info('Involking TSQN Z-Image-Turbo provider...')
#         seed = int(seed)
#         return local_tsqn_zimageturbo(image_fn, prompt, flag_fn, seed=seed, redis_key=progress_key)

# def download_tsqn_zimageturbo_model(force=False):
#     REPO_ID = "tsqn/Z-Image-Turbo_fp32-fp16-bf16_full_and_ema-only"
#     local_dir = os.path.join(
#         const.MODEL_CACHE_DIR,
#         "models--tsqn--Z-Image-Turbo_fp32-fp16-bf16_full_and_ema-only"
#     )

#     TRANSFORMER_DIR = os.path.join(local_dir, "transformer")
#     os.makedirs(TRANSFORMER_DIR, exist_ok=True)

#     TEXT_ENCODER_DIR = os.path.join(local_dir, "text_encoder")
#     os.makedirs(TEXT_ENCODER_DIR, exist_ok=True)

#     VAE_DIR = os.path.join(local_dir, "vae")
#     os.makedirs(VAE_DIR, exist_ok=True)

#     if not os.path.exists(
#         os.path.join(TRANSFORMER_DIR, "diffusion_pytorch_model.safetensors")
#     ) or force:
#         snapshot_download(
#             repo_id=REPO_ID, 
#             ignore_patterns="*.safetensors", 
#             local_dir=local_dir,
#         )
#         hf_hub_download(
#             repo_id=REPO_ID,
#             filename="diffusion_pytorch_model-ema-only-fp32.safetensors",
#             local_dir=local_dir,
#         )
#         os.rename(
#             os.path.join(local_dir, "diffusion_pytorch_model-ema-only-fp32.safetensors"), 
#             os.path.join(TRANSFORMER_DIR, "diffusion_pytorch_model.safetensors")
#         )

#     if not os.path.exists(
#         os.path.join(TEXT_ENCODER_DIR, "model.safetensors")
#     ) or force:
#         hf_hub_download(
#             repo_id=REPO_ID,
#             subfolder="text_encoder",
#             filename="qwen_3_4b_bf16.safetensors",
#             local_dir=local_dir,
#         )
#         os.rename(
#             os.path.join(TEXT_ENCODER_DIR, "qwen_3_4b_bf16.safetensors"),
#             os.path.join(TEXT_ENCODER_DIR, "model.safetensors")
#         )

#     if not os.path.exists(
#         os.path.join(VAE_DIR, "diffusion_pytorch_model.safetensors")
#     ) or force:
#         hf_hub_download(
#             repo_id=REPO_ID,
#             subfolder="vae",
#             filename="ae_bf16.safetensors",
#             local_dir=local_dir,
#         )
#         os.rename(
#             os.path.join(VAE_DIR, "ae_bf16.safetensors"),
#             os.path.join(VAE_DIR, "diffusion_pytorch_model.safetensors")
#         )

# def local_tsqn_zimageturbo(
#     image_fn: str,
#     prompt: str,  # short prompt
#     lock_fn: str,
#     seed: int = 1234,
#     redis_key: str = None,
# ):
#     model_path = os.path.join(
#         const.MODEL_CACHE_DIR, 
#         "models--tsqn--Z-Image-Turbo_fp32-fp16-bf16_full_and_ema-only"
#         # "tsqn_Z-Image-Turbo_fp32-fp16-bf16_full_and_ema-only"
#     )

#     log.info('model path: %s', model_path)
#     if not os.path.exists(model_path):
#         log.info("Downloading TSQN Z-Image-Turbo model...")
#         download_tsqn_zimageturbo_model()
#         log.info("Download complete.")

#     lock_fn = os.path.join(const.LIBRARY_DIR, lock_fn)

#     if os.path.exists(lock_fn):
#         log.info("not really a lock, it just means we screwed up.  %s", lock_fn)
#         # it's cool.
#         os.unlink(lock_fn)

#     pipe = ZImagePipeline.from_pretrained(
#         model_path,
#         torch_dtype=torch.bfloat16,
#         vae=AutoencoderKL.from_pretrained(model_path, subfolder="vae", torch_dtype=torch.bfloat16),
#         text_encoder=Qwen3Model.from_pretrained(model_path, subfolder="text_encoder", torch_dtype=torch.bfloat16),
#         tokenizer=Qwen2Tokenizer.from_pretrained(model_path, subfolder="tokenizer"),
#         transformer=ZImageTransformer2DModel.from_pretrained(model_path, subfolder="transformer", torch_dtype=torch.float32),
#         low_cpu_mem_usage=False,
#         max_memory={0: "11GiB", "cpu": "48GiB"},
#     )

#     # [Optional] Attention Backend
#     # Diffusers uses SDPA by default. Switch to Flash Attention for better efficiency if supported:
#     # pipe.transformer.set_attention_backend("flash")    # Enable Flash-Attention-2
#     # pipe.transformer.set_attention_backend("_flash_3") # Enable Flash-Attention-3

#     # [Optional] Model Compilation
#     # Compiling the DiT model accelerates inference, but the first run will take longer to compile.
#     # pipe.transformer.compile()

#     pipe.scheduler = FlowMatchEulerDiscreteScheduler.from_config(
#         pipe.scheduler.config
#     )

#     # pipe.enable_model_cpu_offload()
#     pipe.enable_sequential_cpu_offload()
#     pipe.safety_checker = lambda images, **kwargs: (images, False)
#     pipe.num_inference_steps = 9
    
#     image = pipe(
#         prompt,
#         height=1024,
#         width=1024,
#         num_inference_steps=9,
#         guidance_scale=0.0,
#         generator=torch.Generator("cuda").manual_seed(seed),
#         callback_on_step_end=progress_bar_callback,
#         callback_on_step_end_tensor_inputs=["latents"] # Required to pass latents
#     ).images[0]

#     image_pfn = os.path.join(
#         const.LIBRARY_DIR, 
#         image_fn
#     )
    
#     os.makedirs(os.path.dirname(image_pfn), exist_ok=True)
#     image.save(image_pfn)

#     log.info("Saved %s", image_pfn)

#     with open(lock_fn, "w") as f:
#         f.write("Done")

#     log.info("Cleaning video memory...")
#     del pipe
#     gc.collect()

#     torch.cuda.empty_cache()
#     torch.cuda.ipc_collect()

#     log.info("Generation complete.")
#     return image

# registry.add(TSQNZImageTurboProvider)