import fcntl
import gc
import os
import random
import shutil
import subprocess
import time

import httpx
import torch
from ascii_magic import AsciiArt
from diffusers import (
    AutoPipelineForImage2Image,
    DiffusionPipeline,
    DPMSolverMultistepScheduler,
    FluxFillPipeline,
    StableDiffusion3Pipeline,
    StableDiffusionPipeline,
)
from openai import AzureOpenAI, BadRequestError
from PIL import (
    Image,
    ImageDraw,
)
from transformers import (
    BitsAndBytesConfig,
    T5EncoderModel,
    pipeline,
)

import const
import logger

log = logger.log(__name__)


device = "cuda" if torch.cuda.is_available() else "cpu"
POPPY_FPS = 25


def _image_to_image_morph(first_fn, last_fn, num_frames, morph_frame_dir, done_flag_fn):
    """
    Morph between two images using poppy
    """   
    log.info(f'Morphing {first_fn=} into {last_fn=}...')

    # run poppy to generate the image merge video
    log.info('Creating poppy morph...')
    cmd = [
        'poppy',
        '--rate', str(POPPY_FPS),
        '--frames', str(int(num_frames)),
        os.path.abspath(first_fn),
        os.path.abspath(last_fn)
    ]
    log.info(f"Running {cmd}...")
    subprocess.run(
        cmd,
        cwd=morph_frame_dir
    )

    # now run ffmpg to break the video into frames
    log.info(f'[{morph_frame_dir}] Disassembling poppy morph...')
    subprocess.run(
        [
            'ffmpeg', 
            '-i', 
            'output.mkv',
            'frame_%04d.png'
        ], 
        cwd=morph_frame_dir
    )
    
    mfd = os.path.basename(morph_frame_dir)
    
    shutil.move(
        os.path.join(morph_frame_dir, 'output.mkv'),
        os.path.join(morph_frame_dir, '..', f'{mfd}_poppy.mkv')
    )
    
    with open(done_flag_fn, 'w') as h:
        h.write('done')

    return morph_frame_dir


def _edit_image(input_image_fn, prompt, output_image_fn):
    """
    Edit an image using instruct-pix2pix
    I can't believe this worked mfore or less out-of-the-box.
    Nevermind, I believe it.  This kind of sucks.
    """
    import torch
    from diffusers import (
        EulerAncestralDiscreteScheduler,
        StableDiffusionInstructPix2PixPipeline,
    )

    model_id = "timbrooks/instruct-pix2pix"
    pipe = StableDiffusionInstructPix2PixPipeline.from_pretrained(model_id, torch_dtype=torch.float16, safety_checker=None)
    pipe.to("cuda")
    pipe.scheduler = EulerAncestralDiscreteScheduler.from_config(pipe.scheduler.config)

    # `image` is an RGB PIL.Image
    image = Image.open(input_image_fn).convert('RGB')
    images = pipe(prompt, image=image).images
    
    images[0].save(output_image_fn)


def _interpolate_images(before, after, exp:int=4):
    """
    returns a list of 32+1 absolute image filenames
    not thread save.
    """
    # clear our plate
    for fn in os.listdir('Practical-RIFE/output'):
        log.info(f'Removing {fn}')
        os.unlink(os.path.join('Practical-RIFE/output', fn))

    cmd = [
        'python3',
        'inference_img.py',
        '--img', str(before), str(after),
        '--exp', '5',
    ]
    # now we have ^4 frames = 16, perfect for a half second of video.
    # try exp 5... gives the expected 32+1 frames.  I can live with that.
    # however, they are all the wrong size.  448x256 to be specific.
    # so.. we have squished, wide frames.
    # 
    # we can give up, but that isn't any fun.  so we will try and
    # simultaniously upscale and resize them.  To do that and have the result
    # be coherent we will maek them into a video first, then upscale that.

    print(f'Running {cmd}...')
    subprocess.run(cmd, cwd='Practical-RIFE')
    # give it a moment, this helps keep it from getting crashy because the gpu hasn't released the memory yet.

    # unsorted = glob.glob('Practical-RIFE/output/*.png')
    # frame_filenames = list(unsorted)
    # frame_filenames.sort(key=natural_keys)
    # with open('/tmp/video_index.txt', 'w') as h:
    #     for fn in frame_filenames:
    #         fn = os.path.abspath(fn)
    #         h.write(f"file '{fn}'\n")
    
    if os.path.exists('/tmp/video.mp4'):
        os.unlink('/tmp/video.mp4')

    # assemble our frames in to a video
    cmd = [
        "ffmpeg",
        "-y",
        "-i", 'Practical-RIFE/output/image_%06d.png',
        "-c:v", "libx264",
        "-vf", "fps=25,format=yuv420p",
        # "-pix_fmt", "yuv420p",
        '/tmp/video.mp4',
    ]    
    print(f'Running {cmd}...')
    subprocess.run(cmd)
    # so we should have a video now.  448x256 and 33 frames long, and the apect ratio isn't correct so it is squished.
    # but -- we haven't introduced any new artifacts.
    
    # fix the aspect ratio first.  pass it through ffmpeg again
    if os.path.exists('/tmp/video_1x.mp4'):
        os.unlink('/tmp/video_1x.mp4')

    # yeah, the downscale is upscaling, it might be better
    # to 4x, then downscale and defer correcting the aspect ratio.
    # video.downscale_video(
    #     '/tmp/video.mp4',
    #     '/tmp/video_1x.mp4',
    #     size=(1024, 1024)
    # )

    # is this two-step worth the time?
    # now we upscale
    # takes about 30 seconds at this input resolution
    video.upscale_4x_video(
        '/tmp/video.mp4', 
        '/tmp/video_4x.mp4'
    )

    # now we have a 1792x1024@25fps, still 33 frames long.
    # rescale to proper aspect ratio, this is a little destructive
    # but the upscale should make it less so.
    video.downscale_video(
        '/tmp/video_4x.mp4',
        '/tmp/video_final.mp4',
        size=(1024, 1024)
    )

    os.makedirs('/tmp/output', exist_ok=True)
    for fn in os.listdir('/tmp/output'):
        os.unlink(os.path.join('/tmp/output', fn))

    # break the mp4 apart into frames
    video.video_to_frames(
        '/tmp/video_final.mp4',
        '/tmp/output'
    )

    with open('/tmp/interpolate.flag', 'w') as h:
        h.write('done')


def disabled_safety_checker(images, clip_input):
    if len(images.shape)==4:
        num_images = images.shape[0]
        return images, [False]*num_images
    else:
        return images, False



def local_flux_schnell(
        clip_prompt,  # short prompt 
        t5_prompt=None,  # longer, more detailed prompt
    ):

    quantization_config = BitsAndBytesConfig(load_in_8bit=True)
    if t5_prompt is None:
        t5_prompt = clip_prompt

    model_id = "black-forest-labs/FLUX.1-schnell"    #needs 4 steps only - it is faster than the dev version as the name implies
    
    text_encoder = T5EncoderModel.from_pretrained(
        model_id,
        subfolder="text_encoder_2",
        quantization_config=quantization_config,
        torch_dtype=torch.bfloat16   #bfloat16 and normal float16 both work - former gives a warning but seems to work                                
    )

    pipe = DiffusionPipeline.from_pretrained(
        model_id, 
        torch_dtype=torch.bfloat16,   #bfloat16 and float16 both work, must match the T5               
        text_encoder_2=text_encoder,
        device_map="balanced", 
        max_memory={0:"11GiB", "cpu":"48GiB"},
    )
    #
    # pipe.enable_xformers_memory_efficient_attention()
    # not compatible with transformer_flux
    #
    pipe.safety_checker = disabled_safety_checker
    pipe.vae.enable_tiling()   #less memory usage at VAE time

    log.info(f'Using {clip_prompt=} and {t5_prompt=} to generate a new image...')
    image = pipe(
        clip_prompt,
        prompt_2=t5_prompt,
        num_images_per_prompt=1,
        guidance_scale=0.0,    #must be 0.0 for schnell version, dev version can be as per SD                                                         
        num_inference_steps=4,  #only need 4 for schnell version, dev version needs 50 or so                                                      
        max_sequence_length=256,  #relates to the T5 encoder - text_encoder_2 - max 256 for schnell                                                   
        generator=torch.Generator("cpu").manual_seed(int(random.randrange(4294967294)))
    ).images[0]
    
    del pipe
    del text_encoder
    gc.collect()

    torch.cuda.empty_cache()
    torch.cuda.ipc_collect()
    return image


def local_flux(prompt):
    from diffusers import AutoencoderKL, FlowMatchEulerDiscreteScheduler, FluxPipeline
    from diffusers.models.transformers.transformer_flux import FluxTransformer2DModel
    from diffusers.pipelines.flux.pipeline_flux import FluxPipeline
    from optimum.quanto import freeze, qfloat8, qint4, quantize
    from transformers import (
        CLIPTextModel,
        CLIPTokenizer,
        T5EncoderModel,
        T5TokenizerFast,
    )

    bfl_repo = "black-forest-labs/FLUX.1-dev"
    #adapter_id = "alimama-creative/FLUX.1-Turbo-Alpha"
    # revision = "refs/pr/1"
    dtype = torch.bfloat16

    scheduler = FlowMatchEulerDiscreteScheduler.from_pretrained(bfl_repo, subfolder="scheduler")
    text_encoder = CLIPTextModel.from_pretrained("openai/clip-vit-large-patch14", torch_dtype=dtype)
    tokenizer = CLIPTokenizer.from_pretrained("openai/clip-vit-large-patch14", torch_dtype=dtype)
    text_encoder_2 = T5EncoderModel.from_pretrained(bfl_repo, subfolder="text_encoder_2", torch_dtype=dtype)
    tokenizer_2 = T5TokenizerFast.from_pretrained(bfl_repo, subfolder="tokenizer_2", torch_dtype=dtype)
    vae = AutoencoderKL.from_pretrained(bfl_repo, subfolder="vae", torch_dtype=dtype)
    transformer = FluxTransformer2DModel.from_pretrained(bfl_repo, subfolder="transformer", torch_dtype=dtype)

    #pipe.enable_model_cpu_offload() #save some VRAM by offloading the model to CPU. Remove this if you have enough GPU power
    # pipe.load_lora_weights(adapter_id)
    # pipe.fuse_lora()
    quantize(transformer, weights=qfloat8)
    # quantize(transformer, weights=qint4, exclude=[
    #     "proj_out", "x_embedder", "norm_out", "context_embedder"
    # ])
    freeze(transformer)

    quantize(text_encoder_2, weights=qfloat8)
    freeze(text_encoder_2)

    pipe = FluxPipeline(
        scheduler=scheduler,
        text_encoder=text_encoder,
        tokenizer=tokenizer,
        text_encoder_2=text_encoder_2,
        tokenizer_2=tokenizer_2,
        vae=vae,
        transformer=transformer,
    )
    # pipe.text_encoder_2 = text_encoder_2
    # pipe.transformer = transformer
    pipe.enable_model_cpu_offload()
    # pipe.enable_sequential_cpu_offload()
    pipe = pipe.to("cuda")
    
    generator = torch.Generator().manual_seed(12345)

    image = pipe(
        prompt,
        height=1024,
        width=1024,
        guidance_scale=3.5,
        num_inference_steps=8,
        max_sequence_length=512,
        generator=generator
    ).images[0]
    
    image.resize(const.IMG_TARGET_WIDTH, const.IMG_TARGET_HEIGHT)
    return image


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


def local_diffusion_3(prompt):
    """
    """
    # from transformers import BitsAndBytesConfig, T5EncoderModel

    # this is as good as I have now that will run reliably on 12GB
    repo_id = "stabilityai/stable-diffusion-3-medium-diffusers"
    # text_encoder = T5EncoderModel.from_pretrained(
    #     repo_id,
    #     subfolder="text_encoder_3",
    #     # quantization_config=quantization_config
    # )
    
    pipe = StableDiffusion3Pipeline.from_pretrained(
	    repo_id,
        # text_encoder_3=text_encoder,
        text_encoder_3=None,        # text_encoder_3=text_encoder,
        torch_dtype=torch.float16,
        variant="fp16"
    )
    pipe.safety_checker = disabled_safety_checker
    pipe.enable_model_cpu_offload()
    
    # pipe.scheduler = DPMSolverMultistepScheduler.from_config(pipe.scheduler.config)
    # pipe.enable_sequential_cpu_offload()
    # pipe = pipe.to("cuda")

    seed = random.randrange(0, 200)

    # Generate an image
    image = pipe(
        prompt,
        negative_prompt="words, letters, facing away, disconnected limbs",
        width=768,
        height=768,
        # max_embeddings_multiples=3,
        num_inference_steps=27,
        generator=torch.Generator().manual_seed(seed),
        max_sequence_length=512,
    ).images[0]

    my_art = AsciiArt.from_pillow_image(image)
    my_art.to_terminal()

    torch.cuda.empty_cache()
    del pipe

    # Save the image
    return image


def get_dalle_client():
    return AzureOpenAI(
        api_version="2024-02-01",
        api_key=const.EMILYD_API_KEY,
        azure_endpoint=const.EMILYD_ENDPOINT
    )



def _get_image(text, image_path, detailed_prompt=None, negative_prompt=None):
    success = False
    lasterr = ValueError

    prompt = text
    text = text.replace('!', '')  # dalle doesn't seem to like exclaimations
    wordcount = len(text.split())
    if wordcount == 1:
        # do a word-party image when we get a single word 'phrase'
        log.info('Word party!')
        prompt = f"In an amusing but wild and unhinged style present the letter sequence '{text}'"
    else:
        log.info(f"len(text.split()) = {wordcount}")
    
    log.info(f'Using engine: {const.TEXT_TO_IMAGE}')

    if const.TEXT_TO_IMAGE == "flux":
        # no negative prompt support
        image = local_flux(prompt)
        image.save(image_path, )
        return image

    elif const.TEXT_TO_IMAGE == "flux-schnell":
        # no negative prompt support
        image = local_flux_schnell(
            prompt,
            t5_prompt=detailed_prompt,
        )
        os.makedirs(os.path.dirname(image_path), exist_ok=True)
        image.save(image_path)
        return image

    elif const.TEXT_TO_IMAGE == "stable-diffusion":
        # can't do this in parallel.  this is gonna be slow. using a lock.

        with open('.lock', "w+") as f:
            # Acquire exclusive lock
            success = False
            while not success:
                try:
                    fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)

                    try:
                        image = local_diffusion_3(prompt)
                        resized = image.resize((const.IMG_TARGET_WIDTH, const.IMG_TARGET_HEIGHT))
                        resized.save(image_path)
                        success = True
                    except Exception as err:
                        log.error(err)
                        time.sleep(random.randrange(10, 40))
                except IOError as e:
                    log.info('Unable to lock: %s', e)
                    time.sleep(random.randrange(2, 20))
                finally:
                    # Release the lock
                    fcntl.flock(f, fcntl.LOCK_UN)

        return image

    elif const.TEXT_TO_IMAGE == "dall-e":
        # will still fallback to local stable-diffusion
        dalle_client = get_dalle_client()

        try:
            log.info(f'$$$ Drawing: {prompt}')
            result = dalle_client.images.generate(
                model="dalle3", # the name of your DALL-E 3 deployment
                prompt=prompt,
                n=1
            )
            success = True
            
        except BadRequestError as err:
            if err.status_code == 400:
                e = err.response.json()

                cf = e['error'].get('inner_error', {}).get('content_filter_results', {})
                for content_filter in cf:
                    log.error(f'{content_filter}: {cf[content_filter]["filtered"]}')

                log.error("BadRequestError: %s" % e)
                image = local_diffusion_3(prompt)
                resized = image.resize((const.IMG_TARGET_WIDTH, const.IMG_TARGET_HEIGHT))
                resized.save(image_path)
                return resized

    if not success:
        log.warning(f'{text=}')
        raise lasterr

    image_url = result.data[0].url  # extract image URL from response
    generated_image = httpx.get(image_url).content  # download the image
    with open(image_path, "wb") as image_file:
        log.info(f'Saving as {image_path}')
        image_file.write(generated_image)

    image = Image.open(image_path)

    # because it is fun
    my_art = AsciiArt.from_pillow_image(image)
    my_art.to_terminal()

    image.load()
    return image


def _get_flux_image(image_path, clip_prompt=None, t5_prompt=None):
    image = local_flux_schnell(
        clip_prompt=clip_prompt,
        t5_prompt=t5_prompt
    )
    os.makedirs(os.path.dirname(image_path), exist_ok=True)
    
    log.info('Saving image as %s', image_path)
    image.save(image_path)
    return image


def _prompt_enhance(old_prompt, output_prompt_fn):
    """
    given a prompt, improve it.
    """
    from transformers import GPT2LMHeadModel, GPT2Tokenizer
    
    # Model checkpoint
    model_checkpoint = "Gustavosta/MagicPrompt-Stable-Diffusion"

    # Tokenizer
    tokenizer = GPT2Tokenizer.from_pretrained(model_checkpoint)

    # Model
    model = GPT2LMHeadModel.from_pretrained(
        model_checkpoint,
        pad_token_id=tokenizer.eos_token_id
    )

    enhancer = pipeline(
        "text-generation",
        model=model,
        tokenizer=tokenizer,
        max_new_tokens=400,
        device=device,
    )

    max_target_length = 200
    answer = enhancer(old_prompt, max_length=max_target_length)
    
    final_answer = answer[0]["generated_text"]
    with open(output_prompt_fn, 'w') as h:
        h.write(final_answer)
    
    print(final_answer)
    return final_answer




def zoom_in_to_frame(prompt, dir, first_frame=0, last_frame=10, step=400):
    """
    1. First we generate the requested prompt image
    2. then we resize it to step % with a transparent background
    3. then we inpaint the transparent edges
    4. we want zoom _in_ so we wind it backward
    """
    from diffusers import (
        AutoPipelineForInpainting,
        AutoPipelineForText2Image,
        DPMSolverMultistepScheduler,
        StableDiffusionPipeline,
    )

    frame_index = last_frame
    image_pfn = os.path.join(
        dir,
        f"image_{frame_index:06d}.png"
    )

    image = local_diffusion_15(prompt)
    image.save(image_pfn)
    frame_index -= 1

    pipeline = AutoPipelineForInpainting.from_pretrained(
        "runwayml/stable-diffusion-inpainting", 
        torch_dtype=torch.float16, 
        # variant="fp16"
    ).to("cuda")

    # pipeline.enable_model_cpu_offload()
    # generator = torch.Generator("cuda").manual_seed(92)

    # mask_image = Image.new(mode="RGBA", size=(512, 512))

    new_width = 512 - step
    new_height = 512 - step
    print(f'Initializing ({step=}): {new_width=}, {new_height=}')
    
    # White pixels in the mask are repainted while black pixels are preserved
    mask_image = Image.new("L", (512, 512), 255)
    d = ImageDraw.Draw(mask_image)
    horizontal_padding = int(step / 2)
    vertical_padding = int(step / 2)
    ul_location = (
        (
            horizontal_padding,
            vertical_padding
        ), (
            512 - horizontal_padding,
            512 - vertical_padding,
        )
    )
    print(ul_location)
    d.rectangle(ul_location, fill=0)
    mask_image.save(os.path.join(dir, 'mask.png'))

    prompt = f"a broader view of {prompt}"

    while frame_index > first_frame:
        image_pfn = os.path.join(
            dir,
            f"image_{frame_index:06d}.png"
        )

        image_resize_pfn = os.path.join(
            dir,
            f"image_resize_{frame_index:06d}.png"
        )
        
        image_base_pfn = os.path.join(
            dir,
            f"image_base_{frame_index:06d}.png"
        )

        print(f'Resizing to {new_width}, {new_height}')
        resized = image.resize(
            (new_width, new_height)
        ).convert('RGB')
        resized.save(image_resize_pfn)

        base_image = Image.new("RGB", (512, 512), 'white')

        base_image.paste(
            resized, (
                horizontal_padding,
                vertical_padding
            )
        )
        base_image.save(image_base_pfn)

        print(f"{base_image.size=}")
        print(f"{resized.size=}")
        print(f"{image.size=}")
        print(f"{mask_image.size=}")
        # negative_prompt = "bad anatomy, deformed, ugly, disfigured"

        print('Drawing image with inpainting...')
        image = pipeline(
            prompt=prompt,
            #negative_prompt=negative_prompt,
            image=base_image,
            mask_image=mask_image,
            #guidance_scale=5.0, # defaults to 7.5
            # generator=generator
        ).images[0]
        print('Inpainted image complete')

        image_pfn = os.path.join(
            dir,
            f"image_{frame_index:06d}.png"
        )
        image.save(image_pfn)
        frame_index -= 1


def local_diffusion_15(prompt):
    """
    About the same speed as Meissonic (5 minutes +/-) Qualify.. well, not very
    impressive. dall-e is significantly faster and better.

    TODO: Locking will NOT work.  We can only call diffuser from one process, so we
    need a worker and queue architecture.  In theory this won't be any more of
    a bottleneck than what I was already trying to do with locks.
    """

    pipe = StableDiffusionPipeline.from_pretrained(
	    "runwayml/stable-diffusion-v1-5", 
        # custom_pipeline="lpw_stable_diffusion",
        torch_dtype=torch.float16,
        variant="fp16",
        safety_checker=None
    )
    pipe = pipe.to("cuda")

    generator = torch.Generator("cuda").manual_seed(31)
    neg_prompt = "lowres, bad_anatomy, error_body, error_hair, error_arm, error_hands, bad_hands, error_fingers, bad_fingers, missing_fingers, error_legs, bad_legs, multiple_legs, missing_legs, error_lighting, error_shadow, error_reflection, text, error, extra_digit, fewer_digits, cropped, worst_quality, low_quality, normal_quality, jpeg_artifacts, signature, watermark, username, blurry"

    # Generate an image
    image = pipe(  # ).text2img(
        prompt,
        negative_prompt=neg_prompt,
        width=1024,
        height=1024,
        # max_embeddings_multiples=3,
        num_inference_steps=80
    ).images[0]

    # image = pipeline(
    #    prompt,
    #    height=1024,
    #    width=1024,
    #    generator=generator
    #).images[0]

    my_art = AsciiArt.from_pillow_image(image)
    my_art.to_terminal()

    torch.cuda.empty_cache()
    del pipe
    del generator

    # Save the image
    return image


def local_diffusion_2(prompt):
    """
    """
    repo_id = "stabilityai/stable-diffusion-2"
    pipe = DiffusionPipeline.from_pretrained(
	    repo_id, 
        torch_dtype=torch.float16,
        variant="fp16",
        safety_checker=None
    )
    
    pipe.scheduler = DPMSolverMultistepScheduler.from_config(pipe.scheduler.config)
    pipe = pipe.to("cuda")

    # Generate an image
    image = pipe(
        prompt,
        width=768,
        height=768,
        # max_embeddings_multiples=3,
        num_inference_steps=25
    ).images[0]

    my_art = AsciiArt.from_pillow_image(image)
    my_art.to_terminal()

    torch.cuda.empty_cache()
    del pipe

    # Save the image
    return image


def embettering(input_image_fn, prompt, output_image_fn):
    """
    Takes a 1024x1024 image and prompt, returns a "better" image as a PILLOW
    Image() object.  Running more than one of these at a time is a bad idea.
    """
    log.info(f'Embettering {input_image_fn=}')
    input_image = Image.open(input_image_fn)

    # pipe = StableDiffusionXLImg2ImgPipeline.from_pretrained(
    #     "stabilityai/stable-diffusion-xl-refiner-1.0", 
    #     torch_dtype=torch.float16, 
    #     variant="fp16", 
    #     use_safetensors=True
    # )
    
    retries = 3
    image = None
    torch.backends.cudnn.allow_tf32 = False

    while image is None and retries > 0:
        # this sprints out of vram
        pipe = AutoPipelineForImage2Image.from_pretrained(
            "stabilityai/stable-diffusion-xl-refiner-1.0", 
            torch_dtype=torch.float16, 
            variant="fp16", 
            use_safetensors=True
        )
        # pipe = StableDiffusionXLImg2ImgPipeline.from_pretrained(
        #     "stabilityai/stable-diffusion-xl-refiner-1.0", 
        #     torch_dtype=torch.float16, 
        #     variant="fp16", 
        #     use_safetensors=True
        # ).to('cuda')

        pipe.enable_model_cpu_offload()
        # pipe.enable_sequential_cpu_offload() 
        pipe.enable_vae_tiling()

        image = pipe(
            prompt, 
            image=input_image, 
            strength=0.125  # higher = more "freedom" to be different than the input image
        ).images[0]
    
    if image is None:
        raise Exception('Failed to embetter')

    # because it is fun
    my_art = AsciiArt.from_pillow_image(image)
    my_art.to_terminal()

    image.save(output_image_fn)


def outpainting(prompt, image_fn, mask_fn, output_fn):
    repo_id = "black-forest-labs/FLUX.1-Fill-dev"

    quantization_config = BitsAndBytesConfig(load_in_8bit=True)
    text_encoder = T5EncoderModel.from_pretrained(
        repo_id,
        subfolder="text_encoder_2",
        quantization_config=quantization_config,
        torch_dtype=torch.bfloat16   #bfloat16 and normal float16 both work - former gives a warning but seems to work                                
    )

    pipe = FluxFillPipeline.from_pretrained(
        repo_id, 
        torch_dtype=torch.bfloat16,
        text_encoder_2=text_encoder,
        device_map="balanced", 
        max_memory={0:"11GiB", "cpu":"48GiB"},
    )
    pipe.vae.enable_tiling()

    image = pipe(
        prompt=prompt,
        image=Image.open(image_fn).convert("RGB"),
        mask_image=Image.open(mask_fn).convert("RGBA"),
        guidance_scale=3.5,
        num_inference_steps=50,
        max_sequence_length=256,
        generator=torch.Generator("cpu").manual_seed(int(random.randrange(4294967294)))
    ).images[0]

    log.info(f'Saving outpainted image to {output_fn}')
    image.save(output_fn)

    # flux doesn't like to give back memory
    del pipe
    del text_encoder
    gc.collect()

    torch.cuda.empty_cache()
    torch.cuda.ipc_collect()
    
    return output_fn