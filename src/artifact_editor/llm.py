import glob
import json
import os
from pathlib import Path
import re
import tempfile
import subprocess
import shutil
import fnv_hash_fast
import redis
from bs4 import Tag

from artifact_editor.chapter.chapter import get_prompt_workflow_by_filename
from artifact_editor.phrases.phrases import clean_latex
import comfy
import const
import logger
from artifact_editor import tools
from artifact_editor.characters import characters
from artifact_editor.characters.voices import KOKORO_VOICES

import typing as t

if t.TYPE_CHECKING:
    from artifact_editor.chapter.chapter import Chapter

log = logger.log(__name__)



FIFO_FN = os.path.join(os.path.dirname(__file__), "..", "drawing.fifo")


def _kokoro_language(character_dict: dict) -> str:
    """Return the Kokoro language label for the character's active voice."""
    voice_language = {
        "english": "English",
        "french": "French",
        "japanese": "Japanese",
        "indian": "Hindi",
        "chinese": "Mandarin Chinese",
        "spanish": "Spanish",
        "portuguese": "Brazilian Portuguese",
        "italian": "Italian",
    }
    voices = character_dict.get("voices", [])
    active_voices = [
        voice for voice in voices if float(voice.get("strength", 0)) > 0
    ]
    voice = active_voices[0] if active_voices else (voices[0] if voices else {})
    voice_info = KOKORO_VOICES.get(voice.get("id"), {})
    language = voice.get("language", voice_info.get("language", "english"))
    accent = voice.get("accent", voice_info.get("accent"))

    if language == "english":
        return "English (British)" if accent == "british" else "English (American)"
    return voice_language.get(language, "All")

LLM_CACHE_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "LLM_CACHE")
os.makedirs(LLM_CACHE_DIR, exist_ok=True)


def cache_tag(prompt):
    text = "_".join(prompt.split()[:6])[:25]
    tag = re.sub(r"[^a-z_A-Z0-9]", "_", text)
    taghash = fnv_hash_fast.fnv1a_32(prompt.encode("utf-8"))
    tag += f"_{taghash:08x}"
    return tag


def get_cached_prompt(prompt, format="json", force=False):
    if force:
        log.info("Forcing cache miss")
        return None

    cache_fn = os.path.join(LLM_CACHE_DIR, f"{cache_tag(f'{format}_{prompt}')}.json")

    if os.path.exists(cache_fn):
        log.info(f"Cache hit {cache_fn}")
        with open(cache_fn, "r") as f:
            return json.load(f)

    log.info("Cache miss")
    return None


def set_cached_prompt(prompt, content, format="json"):
    if format == "json":
        cache_fn = os.path.join(LLM_CACHE_DIR, f"{cache_tag(f'{format}_{prompt}')}.json")
        with open(cache_fn, "w") as f:
            json.dump(content, f)
    elif format == "txt":
        cache_fn = os.path.join(LLM_CACHE_DIR, f"{cache_tag(f'{format}_{prompt}')}.txt")
        with open(cache_fn, "w") as f:
            f.write(content)


def str_prompt_old(prompt, system_prompt="You are a helpful assistant.", force=False):
    log.info(
        "Processing: %s",
        json.dumps({"prompt": prompt, "system_prompt": system_prompt, "force": force}),
    )
    content = None
    if not (content := get_cached_prompt(prompt, format="str", force=force)):
        with tempfile.TemporaryDirectory() as tmpdirname:
            outputfile = os.path.join(tmpdirname, "llm_str_prompt.json")
            # fmt: off
            redis.Redis(host="redis").rpush("gpu_tasks", json.dumps(["llm_str_prompt", prompt, system_prompt, outputfile]))
            # fmt: on
            tools.wait_for(outputfile)

            with open(outputfile, "r") as f:
                content = f.read()

        set_cached_prompt(prompt, content, format="str")

    log.info("LLM responce is: %s", content)
    return content


def text_2_audio(
    chapter: Chapter,
    phrase_xml: Tag,
    character_name: str,
    wavfile: str,
    force: bool=False
) -> str:
    """
    Submit a new ComfyUI api job to generate audio from this text.

    Args:
        chapter (Chapter): The chapter object containing context for the audio generation.
        phrase_xml (Tag): The XML tag containing the text to be converted to audio.
        character_name (str): The name of the character whose voice will be used.
        wavfile (str): The path to the output WAV file.
        force (bool, optional): Whether to force regeneration of the audio file if it already exists. Defaults to False.

    Returns:
        str: The path to the generated WAV file.

    Raises:
        ValueError: If the specified character has no voice defined.
    """
    spoken_text = clean_latex(phrase_xml.get_text())

    character_dict = characters.get_character(chapter, character_name)
    if "voices" not in character_dict:
        log.error(f"Character {character_name} has no voice defined")
        raise ValueError(f"Character {character_name} has no voice defined")

    outputfile = os.path.join(
        const.COMFY_DIRS["artifactserver"]["OUTPUT_DIR"],
        os.path.basename(wavfile)
    )
    
    if os.path.exists(outputfile):
        if force:
            os.remove(outputfile)
        else:
            log.info(f"Audio file already exists at {outputfile}, skipping generation")
            shutil.copy(outputfile, wavfile)
            return wavfile
    
    # magic file used by Misaki to override single word pronunciation
    pronunciation_dict_fn = os.path.join(
        const.COMFY_DIRS["artifactserver"]["INPUT_DIR"],
        "override.json"
    )
    pronunciation = {}
    for word, pron in chapter.get_pronunciation().items():
        log.info(f'Adding pronunciation entry: {word} -> {pron}')
        pronunciation[word] = pron['pronunciation']

    with open(pronunciation_dict_fn, "w") as f:
        json.dump(pronunciation, f)

    template_environment: dict[str, str|int|float] = {}
    template_environment["LANGUAGE"] = _kokoro_language(character_dict)
    # anything inside a template gets the "comfyui" version of the
    # COMFY_DIRS.
    log.info('Including %s layers of voice data', len(character_dict["voices"]))
    for voice_index, voice_dict in enumerate(character_dict["voices"]):
        template_environment[f"SPEAKER_{voice_index:02d}_NAME"] = voice_dict["id"]
        template_environment[f"SPEAKER_{voice_index:02d}_WEIGHT"] = int(voice_dict["strength"]) / 100
    
    # zero out any remaining entries
    for voice_index in range(len(character_dict["voices"]), 10):
        template_environment[f"SPEAKER_{voice_index:02d}_NAME"] = "af_heart"
        template_environment[f"SPEAKER_{voice_index:02d}_WEIGHT"] = 0.0

    template_environment["TEXT"] = spoken_text.strip().replace("\n", " ")
    template_environment["FILENAME_PREFIX"] = os.path.join("audio", os.path.basename(outputfile))

    template_environment["PRONUNCIATION_DIR"] = os.path.join(
        const.COMFY_DIRS['comfyui']['OUTPUT_DIR'],
        "audio"
    )
    template_environment["PRONUNCIATION_FILENAME"] = os.path.basename(outputfile).replace(".wav", ".pronunciation")

    #const.COMFY_DIRS["comfyui"]["OUTPUT_DIR"]
    log.info("Running LLM workflow with template_environment: %s", json.dumps(template_environment, indent=2))
    # The problem is chapter.get_workflow() which is the modern flow, is
    # really more of a chapter.get_image_workflow(); it assumes and expects
    # to be working with image_xml data.

    # the smart move is to add chapter.get_phrase_workflow() and let it assume
    # it is working with single phrase_xml data.

    # TODO: rewrite to use comfy.run_workflow_fn
    # response = comfy.run_workflow_fn(
    #     workflow,
    #     template_environment=template_environment
    # )
    # workflow = comfy.load_workflow_template("api", "t2a", "kokoro")
    # f"phrase.{workflow_name}.*.json",
    workflow_name = "kokoro"

    workflow_filename = chapter.get_phrase_workflow(
        phrase_xml=phrase_xml,
        workflow_name=workflow_name,
        template_environment=template_environment
    )
    
    comfy.run_workflow_fn(workflow_filename)

    # convert flac to wav while we copy it into place.
    glob_filename = os.path.join(
        const.COMFY_DIRS["artifactserver"]["OUTPUT_DIR"],
        "audio",
        os.path.basename(outputfile) + "*.flac"
    )
    
    log.info('Expected output to match glob: %s', glob_filename)
    newest_flac = max(glob.glob(
        glob_filename
    ))

    # artifactserver-1  |   "TEXT": "THE MOUSE, THE CAT, AND THE COCK",
    # artifactserver-1  |   "FILENAME_PREFIX": "audio/ph_0_THE_MOUSE__THE_CAT__AND_T_af306568_d3a9.wav",
    # artifactserver-1  |   "PRONUNCIATION_DIR": "/root/ComfyUI/output/audio",
    # artifactserver-1  |   "PRONUNCIATION_FILENAME": "ph_0_THE_MOUSE__THE_CAT__AND_T_af306568_d3a9.pronunciation"

    #expected: /output/audio/ph_0_THE_MOUSE__THE_CAT__AND_T_af306568_d3a9.wav_*.flac
    #          /output/audio/ph_0_THE_MOUSE__THE_CAT__AND_T_af306568_d3a9.wav.wav_00001.flac
    # make sure the output directory exists
    os.makedirs(os.path.dirname(wavfile), exist_ok=True)

    subprocess.run(
        [
            "ffmpeg",
            "-y", "-i",
            os.path.join(
                const.COMFY_DIRS["artifactserver"]["OUTPUT_DIR"],
                "audio", 
                newest_flac
            ), wavfile
        ],
        check=True
    )
    # /root/ComfyUI/output/audio/ph_0_Fables_6020c8ae_0a40.pronunciation.txt
    #               output/audio/ph_0_Fables_6020c8ae_0a40.wav_00001_.flac
    pronunciation_filename = os.path.basename(newest_flac).split('.')[0] + "pronunciation.txt"
    
    pronunciation_pfn = os.path.join(
        const.COMFY_DIRS["comfyui"]["OUTPUT_DIR"],
        "audio",
        pronunciation_filename
    )

    if os.path.exists(pronunciation_pfn):
        shutil.copy(
            pronunciation_pfn,
            wavfile.replace(".wav", ".pronunciation") + ".txt"
        )

    return wavfile


def str_prompt(prompt: str, system_prompt: str ="You are a helpful assistant.", force:bool=False):
    content = None
    filename = "llm_str.txt"
    if not (content := get_cached_prompt(prompt, format="txt", force=force)):

        workflow_filename = get_prompt_workflow_by_filename(
            workflow_filename="chat.qwen35-json.Qwen 3.5 4b bf16.json",
            prompt=prompt,
            system_prompt=system_prompt,
            outputfile=filename,
        )

        comfy.run_workflow_fn(workflow_filename)

        # and find the output..
        glob_name = os.path.join(
            const.COMFY_DIRS["artifactserver"]["OUTPUT_DIR"], 
            filename + "*"
        )
        
        outputfile = glob.glob(glob_name)

        if outputfile and os.path.exists(outputfile[0]):
            log.info('Reading output file: %s', outputfile[0])
            content = Path(outputfile[0]).read_text()
            os.remove(outputfile[0])
        else:
            log.error('Output file not found: %s', glob_name)
            raise ValueError("Output file not found")

        set_cached_prompt(prompt, content, format="txt")

    log.info("LLM response is: %s", content)
    return content


def str_fast_prompt(prompt, system_prompt="You are a helpful assistant.", force=False):
    """
    Llama 3.2 1B instruct via vllm, this is very quick but not very smart.
    """
    content = None
    workflow = comfy.load_workflow_template(
        "api", "t2t", 
        "vllm.llama-3.2-1b"
    )

    outputfile = os.path.join(
        const.COMFY_DIRS["artifactserver"]["OUTPUT_DIR"], 
        "llm_str.txt"
    )

    if os.path.exists(outputfile):
        os.remove(outputfile)
    
    log.info("Running LLM workflow", prompt=prompt)
        
    # anything inside a template gets the "comfyui" version of the
    # COMFY_DIRS.
    response = comfy.run_workflow(
        workflow,
        template_environment={
            "FREQUENCY_PENALTY": 0.0,
            "PRESENCE_PENALTY": 0.0,
            "TEMPERATURE": 1.0,
            "TOP_P": 1.0,
            "MAX_TOKENS": 56000,
            "SYSTEM_PROMPT": system_prompt,
            "PROMPT": prompt,
            "OUTPUT_FILE_PATH": const.COMFY_DIRS["comfyui"]["OUTPUT_DIR"],
            "FILE_NAME": os.path.basename(outputfile)
        }
    )

    log.info(f"LLM workflow response: {response}")
    content = response

    set_cached_prompt(prompt, content, format="txt")

    log.info("LLM responce is: %s", content)
    return content


def json_prompt(prompt, force=False):
    content = None
    if not (content := get_cached_prompt(prompt, format="json", force=force)):

        workflow = comfy.load_workflow_template("api", "t2json", "qwen35-json_Qwen 3.5 JSON")

        outputfile = os.path.join(const.COMFY_DIRS["artifactserver"]["OUTPUT_DIR"], "llm_json")
        if os.path.exists(outputfile):
            os.remove(outputfile)
        
        log.info("Running LLM workflow", prompt=prompt)
            
        # anything inside a template gets the "comfyui" version of the
        # COMFY_DIRS.
        response = comfy.run_workflow(
            workflow,
            template_environment={
                "PROMPT": prompt,
                "OUTPUT_DIR": const.COMFY_DIRS["comfyui"]["OUTPUT_DIR"],
                "FILE_NAME": os.path.basename(outputfile)
        })

        log.info(f"LLM workflow response: {response}")

        with open(outputfile, "r") as f:
            content = json.load(f)

        set_cached_prompt(prompt, content, format="json")

    log.info("LLM responce is: %s", content)
    return content


def outpainting(prompt, image_fn, mask_fn, output_fn):
    # do not reformat black code formatter
    # comment telling black not to reformat this line
    # fmt: off
    redis.Redis(host="redis").rpush("gpu_tasks", json.dumps(["outpainting", prompt, image_fn, mask_fn, output_fn]))
    # fmt: on
    tools.wait_for(output_fn)
    return output_fn


def trigger_llm_task_file(task_name, output_fn, *args, **kwargs) -> str:
    """
    Trigger a LLM task.
    Returns a filename containing the result.

    Valid task names are listed in gpu_service.py

    The args and kwargs relevant for each LLM task are the tricky bit. Going
    through this generic function won't make it friendly.
    """
    redis.Redis(host="redis").rpush("gpu_tasks", json.dumps([task_name, output_fn, *args, kwargs]))

    tools.wait_for(output_fn)

    return output_fn


def trigger_llm_task_str(task_name, *args, **kwargs) -> str:
    """
    Trigger a LLM task.
    Returns a string that is the response

    Valid task names are listed in gpu_service.py

    The args and kwargs relevant for each LLM task are the tricky bit. Going
    through this generic function won't make it friendly.
    """
    output = ""

    with tempfile.TemporaryDirectory() as tmpdirname:
        output_fn = os.path.join(tmpdirname, "llm_task_response.txt")

        trigger_llm_task_file(task_name, output_fn, *args, **kwargs)

        with open(output_fn, "r") as f:
            output = f.read()

    return output


def generate_image_prompt(text, paragraph_text, meta_prompt="", prompt_fn=""):
    """
    Generate a prompt for the image based on the text and surrounding context.
    """
    return trigger_llm_task_str(
        "text_to_image_prompt",
        text,  # umm, yeah, this may be a dumb choice.
        paragraph_text,
        meta_prompt,
        prompt_fn,
    )
