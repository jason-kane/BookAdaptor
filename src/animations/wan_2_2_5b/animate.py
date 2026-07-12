
import glob
import os
import json
import urllib.request
import uuid
from click import prompt
from llama_cpp import Llama

import torch
from diffusers import (
    UniPCMultistepScheduler,
    WanImageToVideoPipeline,
    WanPipeline
)
from diffusers.hooks.group_offloading import apply_group_offloading
from diffusers.utils import export_to_video
from PIL import Image

import const
import logger
from artifact_editor.video import video
from gpu import llm

log = logger.log(__name__)


def animate_image_wan_2_2_5b_crash(
        image_pfn, 
        prompt, 
        negative_prompt,
        animate_frame_dir, 
        extend,
        done_flag_fn,
        num_frames=None
):
    log.info('_animate_image_wan_2_2_5b(%s, %s, %s, %s, %s, %s, %s)' % (image_pfn, prompt, negative_prompt, animate_frame_dir, extend, done_flag_fn, num_frames))
    # Question #1:  what is the largest image wen 2.2 can animate with
    # a 12GB video card?

    # i can easily run Q8_0 which is basically fp16 on my 12gb vram card and 32gb ram,
    # distorch gguf loader to achieve this with offloading, which only costs 10-20% of speed for much greater quality.
    # been using Q_4_K_M and it’s been a hit or miss, tried Q8 with my 3070 and it was three times as long lol
    # Causvid Lora
    
    # we're goinna try and get 480p
    # this is the resolution at which we're animating
    # It can also do 720p but.. not on my 12GB card
    # so we downscale to 480p, animate, then upscale after
    # 480p  is this really "supported"?
    
    # must be multiples of 16
    #target_width = 864
    #target_height = 480

    # very interested in seeing if this works...
    # any perhaps faster than 8.4s/it; we are wasting pixels.
    # 512x512, 5.88s/it on 12GB card with offloading.
    # target_width = 512
    # target_height = 512

    # since the "real" output is 1080x1080, an even multiple will give us fewest
    # resize artifacts and crispest image.  but.. not a multiple of 16 which is
    # what wan requires.
    #target_width = 544
    #target_height = 544

    # what actually happens when we go for broke, good multiple of 16
    #target_width = 1024
    #target_height = 1024
    
    # out of memory, but only at the _end_.  Cute.
    # target_width = int(1024 * 0.8)
    # target_width = ((target_width + 15) // 16) * 16

    # target_height = int(1024 * 0.8)
    # target_height = ((target_height + 15) // 16) * 16

    # 720p this is the resolution in the wan 2.2 paper/docs?!? pretty weird
    # people.  pretty weird.  I do wonder if using 704x704 square instead of
    # 900-ish might have unexpected benefits in render times.

    target_width = 704
    target_height = 704
    
    # Calculate the nearest multiple of 16 (rounding up)
    height = ((target_height + 15) // 16) * 16
    assert height == target_height, f'Height {height} != target_height {target_height}'

    width = ((target_width + 15) // 16) * 16
    assert width == target_width, f'Width {width} != target_width {target_width}'

    dtype = torch.bfloat16

    model_id = "Wan-AI/Wan2.2-TI2V-5B-Diffusers"
      
    pipe = WanImageToVideoPipeline.from_pretrained(
        model_id, 
        torch_dtype=dtype
    )

    flow_shift = 5.0 # default for 720p

    if target_height <= 480:
        flow_shift = 3.0  # 5.0 for 720P, 3.0 for 480P

    # pipe.scheduler = UniPCMultistepScheduler.from_config(
    #     pipe.scheduler.config,
    #     flow_shift=flow_shift
    # )

    # Use block-level offloading for the text encoder
    apply_group_offloading(
        pipe.text_encoder, 
        onload_device=torch.device("cuda"), 
        offload_device=torch.device("cpu"), 
        offload_type="block_level",
        num_blocks_per_group=2
    )

    for component in [pipe.transformer, pipe.vae, pipe.text_encoder]:
        apply_group_offloading(
            component,
            offload_type="block_level",
            num_blocks_per_group=1,
            offload_device=torch.device("cpu"),
            onload_device=torch.device("cuda"),
        )
    # pipe.enable_model_cpu_offload()
    # pipe.enable_attention_slicing()
    # pipe.enable_sequential_cpu_offload()

    # pipe.to('cuda')

    # height = 704
    # width = 1280
    # num_frames = 121
    
    # holy shit, num_frames 21 worked. 61 worked 81 worked.  3.28 seconds.
    # but.. kind of goes broken.
    # 
    # ok.. workflow, animate, 
    # 
    # 'Extend' then choose the last frame, use that as the basis for a new
    # sequence. Each "sequence" has its own prompt and negative prompt.
    #
    # Maybe more critically, it lets you cut off the bad ends of your videos
    # as long as you get a few good frames, you can make progress.
    # 
    # rough, but continuous, non-repeating and arbitrary length.
    # 
    # very rough dialog included (equal time division per IPA character) but
    # displayed as text.
    #

    # two seconds of video, we can extend where we need more.
    # should these be surfaced as sliders on the animate dialog?
    num_frames = num_frames or const.FPS * 2
    num_inference_steps = 50
    guidance_scale = 5.0

    if extend:
        # the last frame image in animate_frame_dir
        all_frames = sorted(
            glob.glob(os.path.join(animate_frame_dir, "*.png"))
        )

        img = Image.open(
            os.path.join(
                animate_frame_dir,
                all_frames[-1]
            )
        )
    else:
        img = Image.open(image_pfn).convert("RGB")

    log.info('Initial Image size %s x %s' % (img.width, img.height))
    img = img.resize((target_width, target_height))
    
    log.info('Image Resize to %s x %s' % (img.width, img.height))

    log.info(f'Generating {num_frames} frames of {width}x{height} animation...')
    output = pipe(
        image=img,
        prompt=prompt,
        negative_prompt=negative_prompt,
        height=height,
        width=width,
        num_frames=num_frames,
        guidance_scale=guidance_scale,
        num_inference_steps=num_inference_steps,
        generator=None,
        output_type="pil"
    ).frames[0]

    # output is a list of images, but they are all the wrong size.
    # specifically; they are 864x480

    video_filename = (
        os.path.splitext(
            os.path.basename(image_pfn)
        )[0] + ".mp4"
    )

    if extend:
        log.info("Extending existing animation frames in %s", animate_frame_dir)
        starting_frame_number = int(
            os.path.basename(
                sorted(
                    glob.glob(os.path.join(animate_frame_dir, "*.png"))
                )[-1]
            ).lstrip('0').split('.')[0]) + 1

        log.info('Frame dimensions are %s x %s' % (output[0].width, output[0].height))

        log.info('Resizing frames to %s x %s' % (const.IMG_TARGET_WIDTH, const.IMG_TARGET_HEIGHT))
        for frame_number, frame in enumerate(output):
            # Yes, i'm also surprised this works.  The (now square) output looks fine, not stretched or distorted.
            frame = frame.resize((const.IMG_TARGET_WIDTH, const.IMG_TARGET_HEIGHT))

            frame.save(
                os.path.join(
                    animate_frame_dir,
                    f'{starting_frame_number + frame_number:06}.png'
                )
            )

        # and make a video from all the frames, old and new.
        video.assemble_mp4(
            fps=const.FPS,
            framedir=animate_frame_dir,
            wavfile=None,
            videofile=os.path.join(
                os.path.dirname(image_pfn), video_filename
            ),
            image_match='%06d.png'
        )
            
    else:
        os.makedirs(animate_frame_dir, exist_ok=True)
        for frame_number, frame in enumerate(output):
            # so.. do we resize each frame, upsize each frame, or upsize the video?
            # let's try resizing each frame.  They look kind of horizontally
            # stretched so maybe a dumb resize is a good idea?  Oh yeah, tried it in
            # gimp.  Looks great as a 480x480 resize, heck, looks good as a one-shot
            # 1024x1024 resize too.  that is so much easier, but it won't work for fullscreen.
            frame = frame.resize((const.IMG_TARGET_WIDTH, const.IMG_TARGET_HEIGHT))

            frame.save(
                os.path.join(
                    animate_frame_dir,
                    f'{frame_number:06}.png'
                )
            )        

        export_to_video(
            output, 
            os.path.join(
                os.path.dirname(image_pfn),
                video_filename
            ),
            fps=24
        )
    
    with open(done_flag_fn, 'w') as h:
        h.write('done\n')

    return


def animate_image_wan_2_2_5b(        
        image_pfn, 
        prompt, 
        negative_prompt,
        animate_frame_dir, 
        extend,
        done_flag_fn,
        num_frames=None
):
    # Brave New World.  We're going to trigger a comfyui workflow.
    with open("animate_image_wan_2_2_5b.json", "r") as h:
        prompt = h.read()
    
    # attach our inputs

    # Prepare the payload
    prompt_id = str(uuid.uuid4())
    p = {"prompt": prompt, "client_id": "animate_image_wan_2_2_5b", prompt_id: prompt_id}
    data = json.dumps(p).encode('utf-8')

    # Send to ComfyUI
    req = urllib.request.Request("http://127.0.0.1:8188/prompt", data=data)
    response = json.loads(urllib.request.urlopen(req).read())
    print(f"Prompt queued! ID: {response['prompt_id']}")
    
    finished = False
    #while not finished:
        # Check the status of the prompt
    #    status_req = urllib.request.Request(f"http://

    return

    llm = Llama.from_pretrained(
        repo_id="wangkanai/wan22-fp8-i2v-gguf",
        filename="wan22-i2v-a14b-high-q4-k-s.gguf",
        n_gpu_layers=20,
        local_files_only=True
    )

    model_filename = "~/.cache/huggingface/hub/models--wangkanai--wan22-fp8-i2v-gguf" 
    if not os.path.exists(model_filename):
        os.makedirs(os.path.dirname(model_filename), exist_ok=True)
        import requests
        url = "https://huggingface.co/wangkanai/wan22-fp8-i2v-gguf/resolve/main/diffusion_models/wan/wan22-i2v-a14b-high-q4-k-s.gguf?download=true"
        log.info(f"Downloading model from {url} to {model_filename}...")
        response = requests.get(url, stream=True)
        with open(model_filename, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        log.info("Model download completed.")

    # https://huggingface.co/wangkanai/wan22-fp8-i2v-gguf/resolve/main/diffusion_models/wan/wan22-i2v-a14b-high-q4-k-s.gguf?download=true
    #"wangkanai/wan22-fp8-i2v-gguf"

    num_frames = num_frames or const.FPS * 2
    num_inference_steps = 50
    guidance_scale = 5.0

    # import torch
    # from diffusers import DiffusionPipeline
    # from diffusers.utils import load_image, export_to_video

    # pipe = DiffusionPipeline.from_pretrained("wangkanai/wan22-fp8-i2v-gguf", dtype=torch.bfloat16, device_map="cuda")
    # pipe.to("cuda")

    # prompt = "A man with short gray hair plays a red electric guitar."
    # image = load_image(
    #     "https://huggingface.co/datasets/huggingface/documentation-images/resolve/main/diffusers/guitar-man.png"
    # )

    # output = pipe(image=image, prompt=prompt).frames[0]
    # export_to_video(output, "output.mp4")


    model_id = "wangkanai/wan22-fp8-i2v-gguf"
    # pipe = WanImageToVideoPipeline.from_pretrained(
    pipe = WanPipeline.from_single_file(
    # pipe = WanImageToVideoPipeline.from_single_file(
        model_filename,  #  filename,
        # local_files_only=True,
        torch_dtype=torch.float16,
        low_cpu_mem_usage=True
    )
    pipe.to('cuda')

    pipe.enable_model_cpu_offload()
    pipe.enable_vae_slicing()
    pipe.enable_vae_tiling()

    #pipe.enable_model_cpu_offload() # Crucial: Offloads model to CPU
    #pipe.enable_vae_tiling()       # Crucial: Prevents VRAM overflow

    target_width = 704
    target_height = 704
    
    # Calculate the nearest multiple of 16 (rounding up)
    height = ((target_height + 15) // 16) * 16
    assert height == target_height, f'Height {height} != target_height {target_height}'

    width = ((target_width + 15) // 16) * 16
    assert width == target_width, f'Width {width} != target_width {target_width}'

    input_image = Image.open(image_pfn).convert("RGB")
    if input_image.size != (target_width, target_height):
        input_image = input_image.resize((target_width, target_height))

    pil_frames = pipe(
        prompt=prompt,
        negative_prompt=negative_prompt,
        height=height,
        width=width,
        image=input_image,
        num_frames=num_frames,      # Lower frame count to conserve VRAM
        guidance_scale=guidance_scale,
        num_inference_steps=num_inference_steps,
        output_type="pil"
    ).frames[0]

    video_filename = (
        os.path.splitext(
            os.path.basename(image_pfn)
        )[0] + ".mp4"
    )

    os.makedirs(animate_frame_dir, exist_ok=True)
    for frame_number, frame in enumerate(pil_frames):
        # TODO: this resize is a problem.
        frame = frame.resize((const.IMG_TARGET_WIDTH, const.IMG_TARGET_HEIGHT))

        frame.save(
            os.path.join(
                animate_frame_dir,
                f'{frame_number:06}.png'
            )
        )

    export_to_video(
        pil_frames, 
        os.path.join(
            os.path.dirname(image_pfn),
            video_filename
        ),
        fps=24
    )

    with open(done_flag_fn, 'w') as h:
        h.write('done\n')



def wan_animation_prompt_enhance(prompt: str, result_filename: str) -> str:
    ai = llm.Qwen2_1_5B_Instruct()

    system_prompt = \
        '''You are an expert in rewriting video description prompts. Your task is to rewrite the provided video description prompts based on the images given by users, emphasizing potential dynamic content. Specific requirements are as follows:
        The user's input language may include diverse descriptions, such as markdown format, instruction format, or be too long or too short. You need to extract the relevant information from the user’s input and associate it with the image content.
        Your rewritten video description should retain the dynamic parts of the provided prompts, focusing on the main subject's actions. Emphasize and simplify the main subject of the image while retaining their movement. If the user only provides an action (e.g., "dancing"), supplement it reasonably based on the image content (e.g., "a girl is dancing").
        If the user’s input prompt is too long, refine it to capture the essential action process. If the input is too short, add reasonable motion-related details based on the image content.
        Retain and emphasize descriptions of camera movements, such as "the camera pans up," "the camera moves from left to right," or "the camera moves from right to left." For example: "The camera captures two men fighting. They start lying on the ground, then the camera moves upward as they stand up. The camera shifts left, showing the man on the left holding a blue object while the man on the right tries to grab it, resulting in a fierce back-and-forth struggle."
        Focus on dynamic content in the video description and avoid adding static scene descriptions. If the user’s input already describes elements visible in the image, remove those static descriptions.
        Limit the rewritten prompt to 100 words or less. Regardless of the input language, your output must be in English.

        Examples of rewritten prompts:
        The camera pulls back to show two foreign men walking up the stairs. The man on the left supports the man on the right with his right hand.
        A black squirrel focuses on eating, occasionally looking around.
        A man talks, his expression shifting from smiling to closing his eyes, reopening them, and finally smiling with closed eyes. His gestures are lively, making various hand motions while speaking.
        A close-up of someone measuring with a ruler and pen, drawing a straight line on paper with a black marker in their right hand.
        A model car moves on a wooden board, traveling from right to left across grass and wooden structures.
        The camera moves left, then pushes forward to capture a person sitting on a breakwater.
        A man speaks, his expressions and gestures changing with the conversation, while the overall scene remains constant.
        The camera moves left, then pushes forward to capture a person sitting on a breakwater.
        A woman wearing a pearl necklace looks to the right and speaks.
        Output only the rewritten text without additional responses.'''

    response = ai.str_prompt(
        prompt=prompt,
        system_prompt=system_prompt,
        max_new_tokens=512
    )

    with open(result_filename, "w") as h:
        h.write(response)

    