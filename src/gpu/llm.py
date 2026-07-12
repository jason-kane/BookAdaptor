"""
Helper class for interacting with Phi
"""
import random

from llama_cpp import Llama
#from unsloth import FastLanguageModel
#from unsloth.chat_templates import get_chat_template
import transformers
from typeguard import function_name
print('transformers')
import json
import shutil
import tempfile
import fnv_hash_fast
from transformers import AutoModelForCausalLM, AutoTokenizer
import os
from artifact_editor import tools
import logger
from typing import Union, Dict, List, Type, Any
import re


log = logger.log(__name__)

transformers.logging.set_verbosity_info()

JSON = Union[Dict[str, Any], List[Any], int, str, float, bool, Type[None]]


FIFO_FN = os.path.join(
    os.path.dirname(__file__), 
    '..',
    'drawing.fifo'
)


def get_authors():
    return ["Author 1", "Author 2", "Author 3"]


def get_books(author):
    return [f"{author}'s Book 1", f"{author}'s Book 2"]


def get_chapters(book):
    return [f"{book} Chapter 1", f"{book} Chapter 2"]


def get_paragraphs(chapter):
    return [f"{chapter} Paragraph 1", f"{chapter} Paragraph 2"]


class LLM:
    style = "transformers"
    max_new_tokens = 1024
    model_name = "meta-llama/Llama-3.2-1B-Instruct"

    def __init__(self):
        """
        This just _barely_ fits in my 3060 12GB as long as I keep the context size low.
        """
        # model, tokenizer = FastLanguageModel.from_pretrained(
        #     #model_name="unsloth/phi-4-unsloth-bnb-4bit",
        #     model_name="unsloth/Meta-Llama-3.1-8B-bnb-4bit",
        #     #max_seq_length=256,
        #     dtype=None,  #auto
        #     load_in_4bit=True,
        # )    

        # FastLanguageModel.for_inference(model)

        # self.llm = pipeline(
        #     "text-generation",
        #     # model="unsloth/phi-4-unsloth-bnb-4bit",
        #     model=model,
        #     tokenizer=tokenizer,
        #     device_map="auto"
        # )
        
        # Use a pipeline as a high-level helper
        # from transformers import pipeline

        # Llama 3.1 is gentle on VRAM (about 9.5GB), I'll try increasing chunk size to better utilize resources.
        # holy shit this thing is slow.
        # self.llm = transformers.pipeline(
        #     "text-generation",
        #     model="unsloth/Meta-Llama-3.1-8B-Instruct-bnb-4bit"
        # )

        # model, tokenizer = FastLanguageModel.from_pretrained(
        #     model_name = "unsloth/mistral-7b-instruct-v0.3-bnb-4bit",
        #     max_seq_length = 2048,
        #     dtype = None,
        #     load_in_4bit = True,
        # )
        
        # lora_rank = 16
        # lora_alpha = 16
        # lora_dropout = 0

        # self.model = FastLanguageModel.get_peft_model(
        #     model,
        #     r = lora_rank, # Choose any number > 0 ! Suggested 8, 16, 32, 64, 128
        #     target_modules = ["q_proj", "k_proj", "v_proj", "o_proj",
        #                     "gate_proj", "up_proj", "down_proj",],
        #     lora_alpha = lora_alpha,
        #     lora_dropout = lora_dropout, # Supports any, but = 0 is optimized
        #     bias = "none",    # Supports any, but = "none" is optimized
        #     # [NEW] "unsloth" uses 30% less VRAM, fits 2x larger batch sizes!
        #     use_gradient_checkpointing = "unsloth", # True or "unsloth" for very long context
        #     random_state = 3407,
        #     use_rslora = False,  # We support rank stabilized LoRA
        #     loftq_config = None, # And LoftQ
        # )

        # self.tokenizer = get_chat_template(
        #     tokenizer,
        #     chat_template = "chatml",
        #     map_eos_token = True
        #     # system_message = "Below are some instructions that describe some tasks. Write responses that appropriately complete each request.",
        # )

        # FastLanguageModel.for_inference(self.model)

        # self.tokenizer.pad_token = self.tokenizer.unk_token
        # self.tokenizer.padding_side = "left"
        # self.model = "Qwen/Qwen2.5-3B-Instruct" # "Qwen/Qwen2.5-7B-Instruct-1M" # "mistralai/Mistral-Nemo-Instruct-2407"  # "mistralai/Mistral-7B-Instruct-v0.3"
        
        self.pipeline = transformers.pipeline(
            "text-generation",
            model_name=self.model,
            max_new_tokens=8000,
            device="cuda",
            trust_remote_code=True
        )
        
    def json_prompt(self, prompt: str) -> JSON | None:
        success = False
        while not success:
            try:
                full = self.str_prompt(prompt, max_new_tokens=0)
                response = self.parse_json_llm_response(full)
                success = True
            except json.JSONDecodeError as e:
                log.error(f"JSONDecodeError: {e}")
            
        return response

    def str_tools_prompt(
        self, 
        prompt: str, 
        system_prompt="/no_think\n\nYou are a helpful assistant.", 
        max_new_tokens: int=0
    ) -> str:

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt}
        ]

        log.info(f"messages: {messages}")
        if self.style == "transformers":
            log.error('Tools not supported in transformers style LLM')
            raise NotImplementedError('Tools not supported in transformers style LLM')
        
            text = self.tokenizer.apply_chat_template(
                messages,
                add_generation_prompt=True,
                tokenize=False,
                # return_dict=True
                # return_tensors="pt"            
                enable_thinking=False
            )
            model_inputs = self.tokenizer([text], return_tensors="pt").to(self.model.device)

            generated_ids = self.model.generate(
                **model_inputs,
                max_new_tokens=max_new_tokens or self.max_new_tokens
            )
            generated_ids = [
                output_ids[len(input_ids):] for input_ids, output_ids in zip(model_inputs.input_ids, generated_ids)
            ]

            response = self.tokenizer.batch_decode(generated_ids, skip_special_tokens=True)[0]
            if response.startswith('"'):
                response = response.strip('"')
        
        elif self.style == "llama_cpp":
            available_tools = {
                'get_authors': {
                    "type": "function",
                    "function": {
                        "name": "get_authors",
                        "description": "Get a list of authors in the library.",
                        "parameters": {
                            "type": "object",
                            "properties": {},
                            "required": []
                        }
                    },
                },
                'get_books': {
                    "type": "function",
                    "function": {
                        "name": "get_books",
                        "description": "Get a list of books by a particular author.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "author": {
                                    "type": "string",
                                    "description": "The author to get books for."
                                }
                            },
                            "required": ['author']  
                        }
                    },
                },
                'get_chapters': {
                    "type": "function",
                    "function": {
                        "name": "get_chapters",
                        "description": "Get a list of chapters in a particular book.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "book": {
                                    "type": "string",
                                    "description": "The book to get chapters for."
                                }
                            },
                            "required": ['book']  
                        }
                    },                    
                },
                'get_paragraphs': {
                    "type": "function",
                    "function": {
                        "name": "get_paragraphs",
                        "description": "Get a list of paragraphs in a particular chapter.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "chapter": {
                                    "type": "string",
                                    "description": "The chapter to get paragraphs for."
                                }
                            },
                            "required": ['chapter']  
                        }
                    },                    
                }
            }

            log.info(f"llama_cpp messages: {messages}")
            response = self.model.create_chat_completion(
                messages=messages,
                tools=available_tools,
                tool_choice="auto",
                seed=random.randint(0, 2**32 - 1)
                # max_new_tokens = max_new_tokens or self.max_new_tokens
            )
            log.info(f"llama_cpp response: {response}")
            response_message = response["choices"][0]["message"]
            
            response_content = response_message["content"]
            tool_calls = response_message.get("tool_calls", [])
            
            if tool_calls:
                for tool_call in tool_calls:
                    function_name = tool_call.function.name 
                    function_to_call = available_tools[function_name]
                    function_args = json.loads(tool_call.function.arguments)
                    function_response = function_to_call(**function_args)
                    messages.append(
                        {
                            "tool_call_id": tool_call.id,
                            "role": "tool",
                            "name": function_name,
                            "content": function_response,
                        }
                    )

                response = self.model.create_chat_completion(
                    messages=messages,
                    tools=tools
                )
            if "<think>" in response_content:
                response_content = re.sub(r'<think>[\s\S]*?</think>', '', response_content, flags=re.DOTALL).strip()
            
            elif "</think>" in response_content:
                response = re.sub(r'[\s\S]*?</think>', '', response, flags=re.DOTALL).strip()

        return response        

    def str_prompt(
            self, 
            prompt: str, 
            system_prompt="/no_think\n\nYou are a helpful assistant.", 
            max_new_tokens: int=0
        ) -> str:

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt}
        ]
        log.info(f"messages: {messages}")
        if self.style == "transformers":
            text = self.tokenizer.apply_chat_template(
                messages,
                add_generation_prompt=True,
                tokenize=False,
                # return_dict=True
                # return_tensors="pt"            
                enable_thinking=False
            )
            model_inputs = self.tokenizer([text], return_tensors="pt").to(self.model.device)

            generated_ids = self.model.generate(
                **model_inputs,
                max_new_tokens=max_new_tokens or self.max_new_tokens
            )
            generated_ids = [
                output_ids[len(input_ids):] for input_ids, output_ids in zip(model_inputs.input_ids, generated_ids)
            ]

            response = self.tokenizer.batch_decode(generated_ids, skip_special_tokens=True)[0]
            if response.startswith('"'):
                response = response.strip('"')
        
        elif self.style == "llama_cpp":
            log.info(f"llama_cpp messages: {messages}")
            response = self.model.create_chat_completion(
                messages = messages
                # max_new_tokens = max_new_tokens or self.max_new_tokens
            )
            log.info(f"llama_cpp response: {response}")
            response = response["choices"][0]["message"]["content"]
            if "<think>" in response:
                response = re.sub(r'<think>[\s\S]*?</think>', '', response, flags=re.DOTALL).strip()
            
            elif "</think>" in response:
                response = re.sub(r'[\s\S]*?</think>', '', response, flags=re.DOTALL).strip()

        return response

    def parse_json_llm_response(self, in_str):
        log.info(f"parse_json_llm_response(self, {in_str=}")
        as_json = None

        if "```json" in in_str:
            # strip out any leading text
            in_str = in_str[in_str.find("```json") :]
            # remote the markdown code block wrapper
            in_str = in_str.strip().removeprefix("```json").removesuffix("```")
        
        in_str = in_str.replace("```", "")

        log.info(f"158 {in_str=}")

        if "### Explanation" in in_str:
            # cut off any text after "### Explanation"
            in_str = in_str[0:in_str.index("### Explanation")]

        log.info(f"164 {in_str=}")

        last_brace = in_str.rfind("}")
        last_bracket = in_str.rfind("]")
        cutoff = max(last_brace, last_bracket)
        if cutoff > 0:
            in_str = in_str[:cutoff + 1]

        # we're doing whatever we can to try and find valid json
        log.info(f"Attempting json.loads() on:\n{in_str}")
        if in_str[0] not in ['{', '[']:
            # sometimes it starts with "Response: " or something
            if '{' in in_str and (in_str.find('{') < in_str.find('[') or '[' not in in_str):
                in_str = in_str[in_str.find('{'):]
            else:
                in_str = in_str[in_str.find('['):]

        try:
            as_json = json.loads(in_str)
        except json.JSONDecodeError as e:
            log.info(f"Failed to parse as JSON: {e}")
            # msg: The unformatted error message
            # doc: The JSON document being parsed
            # pos: The start index of doc where parsing failed
            # lineno: The line corresponding to pos
            # colno: The column corresponding to pos
            # https://rich.readthedocs.io/en/stable/logging.html#logging-handler
            out = ""
            for num, line in enumerate(e.doc.splitlines()):
                out += f"[white on blue]{num + 1:03}: {line}\n"
                if num == e.lineno:
                    out += "[bold yellow on blue]" + " " * e.colno + "^\n"
                    out += f"[white on blue]Parse Error at line {e.lineno}, column {e.colno}\n"
                    out += e.msg.rstrip("\n") + "\n"  # always one newline
                    out += "[grey on blue]" + "-" * 40

            log.info(out, extra={"markup": True})
            raise
            # log.info(f"JSONDecodeError: {e.msg} at pos {e.pos} (line {e.lineno}, column {e.colno})")
            # log.info(f"Failed to parse as JSON: {e}")

        return as_json    


class Qwen25_7B_Instruct(LLM):
    """
    Uses 10-12GB of vram, takes 5 minutes to process a 3k chunk.
    Poor quality response
    """
    model_name = "Qwen/Qwen2.5-7B-Instruct"
    max_new_tokens = 3000
    def __init__(self):
        self.model = AutoModelForCausalLM.from_pretrained(
            self.model_name,
            torch_dtype="auto",
            device_map="auto"
        )

        self.tokenizer = AutoTokenizer.from_pretrained(self.model_name)

# Our project together is turning english literature into video, with scrolling text, spoken narration and visualizations.

class Qwen3_5_9B_Q5_K_M(LLM):
    """
    Faster, smarter, but less context.
    """
    style = "llama_cpp"

    def __init__(self):
        self.model = Llama.from_pretrained(
            repo_id="bartowski/Qwen_Qwen3.5-9B-GGUF",
            filename="Qwen_Qwen3.5-9B-Q5_K_M.gguf",
            n_threads=8,
            n_gpu_layers=35,  # all
            n_ctx=16 * 1024 
        )


class Qwen3_8B_Q4_K_M(LLM):
    style = "llama_cpp"

    def __init__(self):
        self.model = Llama.from_pretrained(
            repo_id="Qwen/Qwen3-8B-GGUF",
            filename="Qwen3-8B-Q4_K_M.gguf",
            n_gpu_layers=34,
            n_ctx=32 * 1024 # 40960,  # 32768,
        )


class Qwen3_14B_Q4_K_M(LLM):
    style = "llama_cpp"

    def __init__(self):
        self.model = Llama.from_pretrained(
            repo_id="Triangle104/Qwen3-18B-A3B-Stranger-Thoughts-IPONDER-Abliterated-Uncensored-Q4_K_M-GGUF",
            filename="qwen3-18b-a3b-stranger-thoughts-iponder-abliterated-uncensored-q4_k_m.gguf",
            n_gpu_layers=25,
            n_ctx=40960,  # 32768,
        )


class Qwen3_14B_Instruct(LLM):
    """
    This is so good, but hell is it slow.
    """
    model_name = "Qwen/Qwen3-14B"
    max_new_tokens = 3000
    
    def __init__(self):
        self.model = AutoModelForCausalLM.from_pretrained(
            self.model_name,
            torch_dtype="auto",
            device_map="auto"
        )

        self.tokenizer = AutoTokenizer.from_pretrained(self.model_name)


class Qwen2_1_5B_Instruct(LLM):
    """

    """
    model_name = "Qwen/Qwen2-1.5B-Instruct"
    max_new_tokens = 3000
    def __init__(self):
        self.model = AutoModelForCausalLM.from_pretrained(
            self.model_name,
            torch_dtype="auto",
            device_map="auto"
        )

        self.tokenizer = AutoTokenizer.from_pretrained(self.model_name)



class Phi_3_1_mini_128K_Instruct(LLM):
    """
    Uses a hair less than 12GB of VRAM and pegs a dozen cpu cores
    """
    model_name = "bartowski/Phi-3.1-mini-128k-instruct-GGUF"   
    max_new_tokens = 1024

    def __init__(self):
        self.llm = Llama.from_pretrained(
            repo_id="bartowski/Phi-3.1-mini-128k-instruct-GGUF",
            filename="Phi-3.1-mini-128k-instruct-IQ2_M.gguf",
            n_gpu_layers=20,
            n_ctx=32768,
            local_files_only=True            
        )
    
    def str_prompt(self, prompt: str) -> str | None:
        messages = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": prompt}
        ]
        response = self.llm.create_chat_completion(
            messages = messages
        )

        return response


class Phi_3_mini_128K_Instruct(LLM):
    model_name = "microsoft/Phi-3-mini-128k-instruct"
    
    # Exceeds 12GB VRAM @ 2048 with 4k context
    # Takes about.. ugg, > 8 minutes
    max_new_tokens = 1024

    def __init__(self):
        self.model = AutoModelForCausalLM.from_pretrained(
            self.model_name,
            torch_dtype="auto",
            device_map="auto"
        )

        self.tokenizer = AutoTokenizer.from_pretrained(self.model_name)


# DEFAULT_MODEL = Phi_3_1_mini_128K_Instruct
# DEFAULT_MODEL = Qwen3_8B_Q4_K_M
DEFAULT_MODEL = Qwen3_5_9B_Q5_K_M  # should be good for 16k context
# DEFAULT_MODEL = Qwen2_1_5B_Instruct


# # obsolete
# class LLM3:
#     def __init__(self, model="phi4"):
#         self.model = model
#         if model == "phi3":
#             self.llm = Llama.from_pretrained(
#                 repo_id="bartowski/Phi-3.1-mini-128k-instruct-GGUF",
#                 filename="Phi-3.1-mini-128k-instruct-Q5_K_L.gguf",
#                 n_gpu_layers=10,
#                 n_ctx=32768,
#                 local_files_only=True
#             )
#         elif model == "phi4":
#             self.llm = pipeline(
#                 "text-generation",
#                 model="unsloth/phi-4-unsloth-bnb-4bit"
#             )


#     def json_prompt(self, prompt, tmp_fn=None):
#         """
#         Run this prompt through.  When you are done, create a file containing
#         the response. We're doing this because we're running in our own process
#         getting one-way instructions from the main application.  That makes the
#         application side very easy to impliment, it's all fire-and-forget.  When
#         blocking needs to happen (as it often does), the application can just
#         wait for the output file to exist then read the contents.

#         Our guarantee is that the prompt response will be valid json or None.
#         """
#         out = self.llm.create_chat_completion(
#             messages = [
#             {
#                 "role": "user",
#                 "content": prompt
#             }
#         ])
#         log.info(f"{out=}")

#         if self.model == "phi":
#             full = out["choices"][0]["message"]["content"]

#         response = self.parse_json_llm_response(full)
#         if tmp_fn:
#             log.info('json_prompt is saving {response=} to {tmp_fn=}')
#             with open(tmp_fn, 'w') as f:
#                 json.dump(response, f)       
#         return response

#     def str_prompt(self, prompt):
#         out = self.llm.create_chat_completion(
#             messages = [
#             {
#                 "role": "user",
#                 "content": prompt
#             }
#         ])
#         log.info(f"{out=}")

#         if self.model == "phi":
#             full = out["choices"][0]["message"]["content"]

#         return full

#     def parse_json_llm_response(self, in_str):
#         log.info(f"\n\n{in_str=}")
#         as_json = None

#         if "```json" in in_str:
#             # strip out any leading text
#             in_str = in_str[in_str.find("```json") :]
#             # remote the markdown code block wrapper
#             in_str = in_str.strip().removeprefix("```json").removesuffix("```")
        
#         in_str = in_str.replace("```", "")
            
#         log.info(f"Attempting json.loads() on:\n{in_str}")
#         try:
#             as_json = json.loads(in_str)
#         except json.JSONDecodeError as e:
#             log.info(f"Failed to parse as JSON: {e}")

#         return as_json


def _str_prompt(prompt, system_prompt, tmp_fn):
    local_llm = DEFAULT_MODEL()
    response = local_llm.str_prompt(prompt, system_prompt)
    
    if tmp_fn:
        with open(tmp_fn, 'w') as h:
            h.write(response)
    return response


def _json_prompt(prompt, tmp_fn):
    local_llm = DEFAULT_MODEL()

    retries = 3
    response = None
    # Retry logic to handle potential JSONDecodeError
    # it gets the formatting wrong about 1 in 4 times.
    # a few retries makes it slow but way more reliable.
    while response is None and retries > 0:
        retries -= 1
        try:
            response = local_llm.json_prompt(prompt)
        except json.JSONDecodeError as e:
            log.error(f"JSONDecodeError: {e}")
            if retries == 0:
                raise e
            log.info("Retrying...")
    
    if tmp_fn:
        os.makedirs(os.path.dirname(tmp_fn), exist_ok=True)
        log.info(f'json_prompt is saving response to {tmp_fn=}')
        with open(tmp_fn, 'w') as h:
            h.write(json.dumps(response))
    return response


def str_prompt(prompt):
    """
    OBSOLETE
    """
    log.warning('str_prompt is deprecated')
    content = None
    if not (content := get_cached_prompt(prompt, format='str')):
        with tempfile.TemporaryDirectory() as tmpdirname:

            outputfile = os.path.join(
                tmpdirname, "llm_str_prompt.json"
            )
            with open(FIFO_FN, 'a') as fifo:
                fifo.write(
                    json.dumps(['llm_str', prompt, outputfile]) + "\n\n"
                )

            tools.wait_for(outputfile)

            with open(outputfile, 'r') as f:
                content = f.read()

        set_cached_prompt(prompt, content, format='str')

    log.info('LLM responce is: %s', content)
    return content


LLM_CACHE_DIR = os.path.join(
    os.path.dirname(__file__), 
    "..", "..",
    "LLM_CACHE"
)
os.makedirs(LLM_CACHE_DIR, exist_ok=True)


def cache_tag(prompt):
    text = "_".join(prompt.split()[:6])[:25]
    tag = re.sub(r"[^a-z_A-Z0-9]", "_", text)
    hash = fnv_hash_fast.fnv1a_32(
        prompt.encode('utf-8')
    )
    tag += f"_{hash:08x}"    
    return tag


def get_cached_prompt(prompt, format='json'):
    cache_fn = os.path.join(
        LLM_CACHE_DIR,
        f"{cache_tag(f'{format}_{prompt}')}.json"
    )

    if os.path.exists(cache_fn):
        log.info(f'Cache hit {cache_fn}')
        with open(cache_fn, 'r') as f:
            return json.load(f)
    
    log.info('Cache miss')
    return None
        

def set_cached_prompt(prompt, content, format='json'):
    cache_fn = os.path.join(
        LLM_CACHE_DIR,
        f"{cache_tag(f'{format}_{prompt}')}.json"
    )
    with open(cache_fn, 'w') as f:
        json.dump(content, f)


def json_prompt(prompt):
    out = get_cached_prompt(prompt)

    if out is None:
        tmpdirname = tempfile.mkdtemp()

        outputfile = os.path.join(
            tmpdirname, "llm_json_prompt.json"
        )
        with open(FIFO_FN, 'a') as fifo:
            fifo.write(
                json.dumps(['llm_json', prompt, outputfile]) + "\n\n"
            )

        tools.wait_for(outputfile)

        log.info(f'Reading from {outputfile=}')
        with open(outputfile, 'r') as f:
            out = json.load(f)

        shutil.rmtree(tmpdirname)

        log.info('Saving to cache')
        set_cached_prompt(prompt, out)

    return out
