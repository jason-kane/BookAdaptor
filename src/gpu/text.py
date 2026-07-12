# from llama_cpp import Llama, llama_free_model
from transformers import pipeline
from gpu import llm
import os
import logger

log = logger.log(__name__)


def _text_to_image_prompt(prompt_fn, text, paragraph_text, meta_prompt, *args, **kwargs):
    ai = llm.Qwen2_1_5B_Instruct()
    
    if os.path.exists(prompt_fn):
        os.unlink(prompt_fn)

    background = ai.str_prompt(
        prompt=f"Describe the setting for this paragraph of text {paragraph_text}"
    )

    foreground = ai.str_prompt(
        prompt=f"To draw a quality, detailed illustration of this paragraph what must be included: {paragraph_text}"
    )

    if "/" in text:
        text = open(text, "r").read()

    meta_prompt = (
        f"Create a highly detailed image prompt describing an image for an AI to draw based on the phrase: \"{text}\". "
        f"Do not include anything but the prompt."
        f"Include the background: {background} and foreground: {foreground}. "
        f"Make sure to include any other important details from the paragraph context: \"{paragraph_text}\"."
    )

    prompt = ai.str_prompt(meta_prompt, max_new_tokens=256)

    log.info('Writing prompt to %s', prompt_fn)
    with open(prompt_fn, "w") as h:
        h.write(prompt)

    return prompt


def _condense_image_prompt(prompt_fn, text, paragraph_text, meta_prompt, *args, **kwargs):
    ai = llm.Qwen2_1_5B_Instruct()

    # prompt_text = open(text, "r").read()
    full_prompt = ""

    if os.path.exists(prompt_fn):
        full_prompt = open(prompt_fn, "r").read()
        log.info('Existing prompt found, condensing it: %s', full_prompt)
        os.unlink(prompt_fn)

    # condense_prompt = (
    #     f"Right-size the following text to about 256 tokens. We want a concise image prompt detailing a specific moment in time: \"{prompt_text}\". "
    #     f"You may include other important details from the paragraph context: \"{paragraph_text}\".  Do respond with the finished description. Do not include anything else."
    # )

    prompt = ai.str_prompt(
        prompt=f"""
        {paragraph_text}
        ----
        {text}
        ----
        {full_prompt}
        """,
        system_prompt=("You are a helpful assistant for "
        "focusing and improving image prompts while maintaining "
        "it evocative and descriptive. "
        "Use the entire 512 token space. "
        "Use short, descriptive phrases. Focus on 3-5 key visual concepts."
        "Include visually striking and important details.  "
        "I am providing a chunk of text from the story, "
        "followed by '----', "
        "followed by the piece of the story you must focus on, "
        "followed by '---', "
        "followed by the prompt to be compressed."
        "\n\nExample:\n"
        "Jack and Jill went up the hill\n"
        "To fetch a pail of water;\n"
        "Jack fell down and broke his crown\n"
        "And Jill came tumbling after."
        "\n\n----\n\n"
        "Jack fell down and broke his crown\n"
        "\n\n---\n\n"
        "A detailed image prompt describing a man falling down a hill and breaking his crown while a horified girl watches.  The image should evoke a sense of tragedy and misfortune, with a focus on the moment of the fall and the expression of pain on Jack's face."
        "\n\n---\n\n"
        "A really good image prompt describing a specific moment in time as jack falls and breaks his head."
        ),
        max_new_tokens=512
    )

    log.info('Writing condensed prompt to %s', prompt_fn)
    with open(prompt_fn, "w") as h:
        h.write(prompt)

    return prompt


def _text_to_fanciful_image_prompt(prompt_fn, text, paragraph_text, meta_prompt, *args, **kwargs):
    # ai = llm.Qwen3_14B_Instruct()
    # ai = llm.Qwen3_14B_Q4_K_M() 
    ai = llm.Qwen3_8B_Q4_K_M()
    
    if os.path.exists(prompt_fn):
        os.unlink(prompt_fn)

    fanciful_prompt = (
        f"Create a fanciful, imaginative, and whimsical image prompt for an AI to draw based on the phrase: \"{text}\". "
        f"The image should evoke a sense of wonder and fantasy. "
        f"Make sure to include any other important details from the paragraph context: \"{paragraph_text}\".  Do not include anything but the finished description."
    )

    prompt = ai.str_prompt(fanciful_prompt, max_new_tokens=256)

    log.info('final fanciful prompt: %s', prompt)
    # a = {'id': 'chatcmpl-c4d009f5-9864-493b-b7fb-17277a44e2fa', 
    #      'object': 'chat.completion', 
    #      'created': 1767595867, 
    #      'model': '/home/jkane/.cache/huggingface/hub/models--Triangle104--Qwen3-18B-A3B-Stranger-Thoughts-IPONDER-Abliterated-Uncensored-Q4_K_M-GGUF/snapshots/f8c186d0ba49d35f6b1b0f088e846be4c19e2fca/./qwen3-18b-a3b-stranger-thoughts-iponder-abliterated-uncensored-q4_k_m.gguf', 
    #      'choices': [
    #          {
    #              'index': 0,
    #              'message': {
    #                  'role': 'assistant', 
    #                  'content': 'Okay, let\'s start with the prompt. The user wants an imaginative image based on '
    #                     'Oscar Wilde\'s quote from "The Picture of Dorian Gray." The paragraph is about art\'s uselessness'
    #             },
    #             'logprobs': None, 
    #             'finish_reason': 'length'
    #         }
    #     ],
    #     'usage': {'prompt_tokens': 474, 'completion_tokens': 38, 'total_tokens': 512}
    # }

    log.info('Writing fanciful prompt to %s', prompt_fn)
    with open(prompt_fn, "w") as h:
        h.write(prompt)
        # ["choices"][0]["message"]["content"])

    return prompt


class RetryableError(Exception):
    pass


def _text_to_image_clip_prompt(text, paragraph_text, t5_prompt, prompt_fn):
    ai = llm.Qwen2_1_5B_Instruct()
    
    background = ai.str_prompt(
        prompt=f"Describe the setting for this paragraph of text {paragraph_text}"
    )

    foreground = ai.str_prompt(
        prompt=f"To draw a quality, detailed illustration of this paragraph what must be included: {paragraph_text}"
    )

    meta_prompt = (
        f"Create a CLIP sequence of comma separated keywords to help instruct another AI to draw a detailed image based on the phrase: \"{text}\". "
        f"The detailed prompt for this image is: {t5_prompt}. "
        f"Respond with a JSON list of keywords."
        f"Consider both the background: {background} and foreground: {foreground}. "
        f"Make sure to consider important details from the paragraph context: \"{paragraph_text}\".\n"
        "Response must be a valid JSON list."
    )

    log.info('submitting prompt: %s', meta_prompt)
    clip_prompt = ai.json_prompt(meta_prompt)
    log.info('raw clip_prompt: %s', clip_prompt)

    # dedupe but don't change the order
    seen = set()
    for x in clip_prompt:
        # sometimes we get a nested list, just flatten it.
        if isinstance(x, list):
            if isinstance(x[0], str):
                x = ','.join(x)
        elif isinstance(x, dict):
            # there are plenty of ways this could go..
            if 'Keyword' in x.keys():
                x = x['Keyword']
            elif 'background' in x.keys() or 'foreground' in x.keys():
                x = x.get('background', []) + x.get('foreground', [])
            else:
                # this will often be acceptable
                try:
                    x = str(" ".join(x.values()))
                except TypeError as e:
                    log.error('Error processing clip_prompt item %s: %s', x, e)
                    raise RetryableError

        x = x.strip()
        if not x or x in seen:
            continue
        seen.add(x)

    clip_prompt = ','.join(seen)
    
    # snip off any leading or trailing commas and whitespace.
    #clip_prompt = clip_prompt.strip(',').strip()

    with open(prompt_fn, "w") as h:
        h.write(clip_prompt)

    log.info('final clip_prompt: %s', clip_prompt)
    return clip_prompt


def _text_to_image_prompt_gemma(text, paragraph_text, meta_prompt, prompt_fn):
    """
    Lets try throwing this at Gemma
    given:
        the line being read (text), 
        the surrounding paragraphs (paragraph_text),
        and an optional overriding directive (meta_prompt) 
    
    we want a good descriptive image prompt within the input limits of our image
    generating AI.

    our output goes in the prompt_fn.  We have the full GPU and we're blocking
    everyone else until we are done.
    """
    import torch
    from transformers import AutoModelForSeq2SeqLM, AutoTokenizer, pipeline

    if os.path.exists(prompt_fn):
        os.unlink(prompt_fn)

    device = "cuda" if torch.cuda.is_available() else "cpu"

    pipe = pipeline(
        "image-text-to-text",
        model="google/gemma-3-4b-pt",
        device=device,
        torch_dtype=torch.bfloat16,
        return_full_text=False,
        max_new_tokens=1024
    )

    text = " ".join(text.replace("\n", " ").split())
    paragraph_text = paragraph_text.replace("\n", " ")
    
    PARAGRAPH_LIMIT = len(text) * 3

    while len(paragraph_text) > PARAGRAPH_LIMIT:
        paragraph_text = paragraph_text[1:-1]
        
    # gemma is shit at this.
    prompt = f"You are an image prompt writer.  Write a highly detailed FLUX image prompt based on this phrase:\n\n{text}\n\nwithin the context of this text:\n\n{paragraph_text}"

    if meta_prompt:
        # the intent is to make meta prompt the real focus while keeping all the
        # flavor text of the book.  I want putting in meta_prompt material to really dominate.
        prompt = f"Help me create a highly detailed image prompt for an AI to draw. {meta_prompt}. The image is based on the phrase {text} within the context of {paragraph_text}"

    answer = pipe(prompt)
    log.info('answer: %s', answer)
    final_answer = answer[0]['generated_text']
    print(final_answer)
    
    os.makedirs(os.path.dirname(prompt_fn), exist_ok=True)
    with open(prompt_fn, "w") as h:
        h.write(final_answer)

    return prompt_fn


def _text_to_image_prompt_old(text, paragraph_text, meta_prompt, prompt_fn):
    """
    given:
        the line being read (text), 
        the surrounding paragraphs (paragraph_text),
        and an optional overriding directive (meta_prompt) 
    
    we want a good descriptive image prompt within the input limits of our image
    generating AI.

    our output goes in the prompt_fn.  We have the full GPU and we're blocking
    everyone else until we are done.
    """
    import torch
    from transformers import AutoModelForSeq2SeqLM, AutoTokenizer, pipeline

    if os.path.exists(prompt_fn):
        os.unlink(prompt_fn)

    device = "cuda" if torch.cuda.is_available() else "cpu"

    # Model checkpoint this model takes a string and spits out an image
    # description.  It is _not_ a good solution.  We're set for 256 tokens of
    # output.
    model_checkpoint = "gokaygokay/Flux-Prompt-Enhance"

    # Tokenizer
    tokenizer = AutoTokenizer.from_pretrained(model_checkpoint)

    # Model
    model = AutoModelForSeq2SeqLM.from_pretrained(model_checkpoint)

    enhancer = pipeline('text2text-generation',
                        model=model,
                        tokenizer=tokenizer,
                        repetition_penalty= 1.4,
                        device=device)

    max_target_length = 256
    prefix = "enhance prompt: "

    if meta_prompt:
        # the intent is to make meta prompt the real focus while keeping all the
        # flavor text of the book.  I want putting in meta_prompt material to really dominate.
        short_prompt = f"Knowing {paragraph_text} in order to illustrate {text} draw this image: {meta_prompt}"
    else:
        short_prompt = f"In the context of:\n{paragraph_text}\n\nDraw:\n{text}"

    answer = enhancer(prefix + short_prompt, max_length=max_target_length)
    final_answer = answer[0]['generated_text']
    print(final_answer)
    
    os.makedirs(os.path.dirname(prompt_fn), exist_ok=True)
    with open(prompt_fn, "w") as h:
        h.write(final_answer)

    return prompt_fn


def _llm_str_prompt(prompt, system_prompt, prompt_fn):
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": prompt},
    ]
    pipe = pipeline(
        "text-generation",
        model="Qwen/Qwen2.5-0.5B-Instruct",
        device_map="auto"
    )
    try:
        out = pipe(messages, max_new_tokens=256)
    except Exception:
        log.error('Error with prompt: %s', messages)
        raise
    
    log.info(f"LLM {out=}")
    
    response = out[0]["generated_text"][-1]["content"]
    with open(prompt_fn, "w") as h:
        h.write(response)

    return response
