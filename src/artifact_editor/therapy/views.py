from email.mime import message
import glob
import markdown_it
from html_to_delta import html_to_delta

import redis
import json
import os
import shutil
import tempfile
from flask import (
    Blueprint,
    make_response,
    redirect,
    render_template,
    request,
    send_file,
    send_from_directory,
    url_for,
)
from PIL import Image
from transformers import T5Tokenizer

import camera
import const
import logger
from artifact_editor import (
    chapter,
    config,
    llm,
    tools,
)
from artifact_editor.characters import characters
from artifact_editor.images import htmx, images, selector
from artifact_editor.styles import styles
from artifact_editor.tools import (
    get_bookdir,
    get_bookurl,
    get_chapterdir,
    get_chapterurl,
    get_surrounding_paragraphs,
    get_text_to_next,
)
from artifact_editor.video import video
from text_to_image.registry import registry as t2i_registry

from . import htmx


md = markdown_it.MarkdownIt('commonmark', {'breaks': True, 'html': True})

FIFO_FN = os.path.join(
    os.path.dirname(__file__), 
    "..", "..",
    "drawing.fifo"
)

log = logger.log(__name__)

bp = Blueprint(
    'therapy', 
    __name__,
    template_folder=os.path.join(
        os.path.dirname(__file__),
        "templates"
    )
)
# children
# bp.register_blueprint(
#     images_selector,
#     url_prefix="/<int:image_index>/selector"
# )

MIN_TOKENS = 128
MAX_TOKENS = 256 


@bp.route("/", methods=["GET"])
def therapy_base():
    out = ""
    if os.path.exists("therapy.json"):
        with open("therapy.json", "r") as f:
            history = [
                json.loads(as_str) for as_str in f.read().split("\n\n") if as_str.strip()
            ]
    else:
        history = []

            # for index, line in enumerate(history):
            #     line['query'] = md.render(line['query'])
            #     # html_to_delta(md.render(line['query']))
            #     line['response'] = md.render(line['response'])
            #     # html_to_delta(md.render(line['response']))               

    return render_template(
        "therapy.html",
        entry_format=entry_format,
        user_comment=htmx.user_comment,
        ai_comment=htmx.ai_comment,
        history=history
    )

# /therapy/update_query/20
@bp.route("/update_<side>/<int:index>", methods=["POST"])
def update_query(side, index):
    if not os.path.exists("therapy.json"):
        return "No history to update", 400

    with open("therapy.json", "r") as f:
        history = [
            json.loads(as_str) for as_str in f.read().split("\n\n") if as_str.strip()
        ]

    if index >= len(history):
        return "Invalid index (%s >= %s)" % (index, len(history)), 400

    log.info(f"{request.form} {request.data}")
    new_query = request.form.get("delta")
    if not new_query:
        return entry_format(index, history[index]), 200

    # new_query is html, I think ideally we want it as markdown.  
    # maybe delta is okay..
    history[index][side] = new_query
    # html_to_delta(new_query)

    with open("therapy.json", "w") as f:
        f.write("\n\n".join(json.dumps(entry) for entry in history))

    return entry_format(index, history[index]), 200

    # return htmx.user_comment(history[index]['query']) + htmx.ai_comment(history[index]['response']), 200


def entry_format(index, entry):
    return f"""
    <div id="bf-{ index }">
            <div class="comment wa-flank">
                <wa-avatar class="user-avatar" slot="media" shape="square" label="Square avatar">
                    <wa-icon slot="icon" src="/static/fontawesome7/svgs/solid/smile.svg"></wa-icon>
                </wa-avatar>

                <div class="wa-stack">
                    <div class="editor" id='editor-query-{ index }'>
                    </div>

                    <div class="button-row wa-cluster">
                        <wa-button 
                            variant="text" 
                            size="small"
                            hx-post="/therapy/compress/query/{ index }"
                            hx-target="#bf-{ index }"
                            hx-swap="outerHTML"
                        >Compress</wa-button>

                        <wa-button 
                            variant="text" 
                            size="small"
                            hx-post="/therapy/rerun/{ index }"
                            hx-target="#bf-{ index }"
                            hx-swap="outerHTML"
                        >Rerun Query</wa-button>
                    </div>
                </div>
            </div>

            <div class="comment wa-flank">
                <wa-icon class="ai-avatar" slot="icon" src="/static/fontawesome7/svgs/solid/brain.svg"></wa-icon>
                <div class="wa-stack">
                    <div class="editor" id='editor-response-{ index }'>
                        { entry['response'] }
                    </div>
                    <div class="button-row wa-cluster">
                        <wa-button 
                            variant="text" 
                            size="small" 
                            hx-post="/therapy/compress/response/{ index }"
                            hx-target="#bf-{ index }"
                            hx-swap="outerHTML"
                        >Compress</wa-button>
                    </div>
                </div>
            </div>
        </div>

        <script>
            add_quill_editor({ index });
        </script>
    """    


# /therapy/compress/response/3
@bp.route("/compress/<side>/<int:index>", methods=["POST"])
def compress(side, index):
    if not os.path.exists("therapy.json"):
        return "No history to compress", 400

    with open("therapy.json", "r") as f:
        history = [json.loads(as_str) for as_str in f.read().split("\n\n") if as_str.strip()]

    if index >= len(history):
        return "Invalid index", 400

    entry = history[index]
    content_to_compress = entry[side]

    prompt = f"Compress the following text while retaining its meaning and key information:\n\n{content_to_compress}\n\nThe compressed version should be concise and use fewer tokens."

    compressed_content = llm.str_prompt(
        prompt,
        system_prompt="You are a helpful assistant that compresses text while retaining its meaning and key information. Provide a concise version of the input text using fewer tokens."
    )

    # Update the entry with the compressed content
    history[index][side] = compressed_content

    # Write the updated history back to the file
    with open("therapy.json", "w") as f:
        f.write("\n\n".join(json.dumps(entry) for entry in history))

    return htmx.user_comment(history[index]['query']) + htmx.ai_comment(history[index]['response']), 200


# /therapy/rerun/query/8
@bp.route("/rerun/<int:index>", methods=["POST"])
def rerun_query(index):
    if not os.path.exists("therapy.json"):
        return "No history to rerun", 400

    with open("therapy.json", "r") as f:
        history = [json.loads(as_str) for as_str in f.read().split("\n\n") if as_str.strip()]

    if index >= len(history):
        return "Invalid index", 400

    entry = history[index]
    prompt = entry['query']

    response_prompt = "\n".join(
        [f"User: {entry['query']}\nAI: {entry['response']}" for entry in history[:index]]
        + [f"User: {prompt}\nAI:"]
    )

    content = llm.str_prompt(
        response_prompt,
        system_prompt="You are a blunt and cold AI designed to do exactly what you are told with precision.  Your user is an expert, you can be very technical."
    )

    # Update the entry with the new response
    history[index]['response'] = content

    # Write the updated history back to the file
    with open("therapy.json", "w") as f:
        f.write("\n\n".join(json.dumps(entry) for entry in history))

    return htmx.user_comment(history[index]['query']) + htmx.ai_comment(history[index]['response']), 200


# POST /therapy/say
@bp.route("/say", methods=["POST"])
def therapy_say():
    log.info("Received therapy request...")
    MAX_CHARACTERS = 2048  # should be a percentage of context, in tokens?  instead we get this crap.

    record_fn = "therapy.json"

    prompt = request.form.get("text", "")
    if not prompt:
        return "No prompt provided", 400
    
    log.info("Loading the historic record...")
    if os.path.exists(record_fn):
        with open(record_fn, 'r') as record_fh:
            history = [json.loads(as_str) for as_str in record_fh.read().split("\n\n") if as_str.strip()]
    else:
        history = []

    response_prompt = "\n".join(
        [f"User: {entry['query']}\nAI: {entry['response']}" for entry in history]
        + [f"User: {prompt}\nAI:"]
    )
        
    while len(history) > 1 and len(prompt) > MAX_CHARACTERS:
        # Remove the oldest entry until we fit.
        history.pop(0)
        response_prompt = "\n".join(
            [f"User: {entry['query']}\nAI: {entry['response']}" for entry in history]
            + [f"User: {prompt}\nAI:"]
        )
        forgetting = True

    #if forgetting:
        # we have accumulated too much context.  We need to forget.
    
    # tools:
    #   lock - do not forget this
    #   buttons
    #     - compress llm call to try and express the same concept more economically.
    #     - split - break one message into multiple messages based on whatever (so you can apply tools like 'lock' to them separately)
    #     - forget - remove this from context permanently
    #     - long-term memory - store this in a separate file for later retrieval, this doesn't forget, it remembers with a long term memory tag.
    #                          but when we compress it, we can drop to a simple summary and the tag for complete recollection from a tool call.
    #    prompt = "[The AI has forgotten some of the earlier conversation due to memory limits.]\n" + prompt

    
    content = ""
    with tempfile.TemporaryDirectory() as tmpdirname:
        outputfile = os.path.join(
            tmpdirname, "therapy.json"
        )

        if os.path.exists(outputfile):
            os.remove(outputfile)

        # Write the prompt to the FIFO for the TTS engine to read
        try:
            log.info('Writing to Redis for therapy AI processing...')
            # fmt: off
            redis.Redis(host="redis").rpush("gpu_tasks", json.dumps(['therapy', response_prompt, outputfile,]))
            # fmt: on
            
            # with open(FIFO_FN, 'w') as fifo:
            #     fifo.write(
            #         json.dumps(['therapy', response_prompt, outputfile]) + '\n\n'
            #     )
                
        except Exception as e:
            log.error(f"Failed to write to FIFO: {e}")
            return "Internal Server Error", 500
        
        tools.wait_for(outputfile)

        with open(outputfile, 'r') as f:
            content = f.read()

    with open(record_fn, 'a') as record_fh:
        record_fh.write(
            "\n" + 
            json.dumps({"query": prompt, "response": content}) + "\n\n")

    return htmx.user_comment(prompt) + htmx.ai_comment(content), 200
