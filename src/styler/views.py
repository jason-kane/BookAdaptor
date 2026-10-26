from collections import defaultdict
import math
import shutil
import unicodedata
import redis
import glob
import os
import random
import json
from bs4 import BeautifulSoup
import shutil

import comfy
import flask

from text_to_image.registry import registry as t2i_registry

import styler.styles as styles
import artifact_editor.images.images as images
import artifact_editor.chapter.chapter as chapter

from flask import (
    Blueprint,
    render_template,
    request,
    send_from_directory,
    url_for,
)

import styler.htmx as htmx
import const
import logger
from artifact_editor import (
    config,
    llm,
    tools,
)
from artifact_editor.chapter.chapter import Chapter
from .styles import as_id, id_to_category

from artifact_editor.author.author import Author
from artifact_editor.cache import cache

log = logger.log(__name__)

bp = Blueprint(
    "styles",
    __name__,
    template_folder=os.path.join(os.path.dirname(__file__), "templates"),
)

base_prompt = """
A 6-year-old child making a silly face. They have slightly messy hair. Their
expression should convey a sense of pure, uninhibited fun. A 20s female is
disgusted by the behavior of the child.  The image should avoid any sense of
creepiness and focus on the child's playful energy and the inappropriateness of
how the woman reacts.
""".replace("\n", " ").strip()

default_tier = 2  # C tier
CHARS_TO_TOKENS = 0.25


def get_groupings_count(item_count):
    # target 30 items per group, max of 11 groups.
    if item_count == 0:
        return 0

    return min(math.ceil(item_count / 30), 11)


# let me get this shit straight
# 1 token is about .75 words, or 4 characters.
# so each character, "costs" 0.25 tokens.  roughly.
# this is so we can show the user how much of the
# context is being used up by the style.
@bp.route("/show_sample/<path:filename>")
def show_sample(filename: str):
    filename = unicodedata.normalize("NFC", filename)
    # solo_krea2_sampler.png

    as_list = filename.split("_")

    engine = as_list.pop(-2)
    style_tag = "_".join(as_list[:-1])

    log.info("show_sample(%s) => engine=%s, style_tag=%s", filename, engine, style_tag)

    filename = f"{style_tag}_{engine}.png"

    sample_dir = os.path.join(const.LIBRARY_DIR, "style_exploration")
    if request.args.get("base_style_prompt") == "kid_friendly":
        sample_dir = os.path.join(sample_dir, "kid_friendly")
    sample_pfn = os.path.join(sample_dir, filename)

    if os.path.exists(sample_pfn):
        return send_from_directory(
            sample_dir, filename
        )

    # Generation belongs to the serial websocket worker, never an image GET.
    return "Sample is not ready", 404


# a little degas
# ballet outfits do not have enough variety for this to be helpful
# base_prompt = "sublime beautiful ballet dancer practicing"

# this is amusing, and tells you how well the model handles
# potentially "mature" content.
BASE_STYLE_PROMPTS = {
    "classic": "stunning erotic woman in a very revealing, flimsy, sheer lace dress",
    "kid_friendly": (
        "A joyful storybook adventure with a girl and her cute, floppy-eared dog, "
        "and a boy befriending a tiny, friendly pet dragon. They share a playful "
        "moment in a sunny garden, laughing together. Warm, wholesome, imaginative, "
        "and child friendly; everyone is fully clothed, with natural hands and "
        "short, realistic fingers."
    ),
}
DEFAULT_BASE_STYLE_PROMPT = "classic"


def get_base_style_prompt():
    selected = (request.args.get("base_style_prompt", DEFAULT_BASE_STYLE_PROMPT)
                if flask.has_request_context() else DEFAULT_BASE_STYLE_PROMPT)
    return BASE_STYLE_PROMPTS.get(selected, BASE_STYLE_PROMPTS[DEFAULT_BASE_STYLE_PROMPT])


base_prompt = BASE_STYLE_PROMPTS[DEFAULT_BASE_STYLE_PROMPT]


def draw_sample(style_tag, engine="zit", base_style_prompt="classic"):
    log.info("draw_sample(%s, engine=%s)", style_tag, engine)

    chapter = Chapter(
        author=Author(""),
        title="",
        number=0,
        language="english",
    )

    # workflow = comfy.load_workflow_template(
    #     "api", "t2i", f"{engine}_style_sample",
    # )
    style = styles.Style(style_tag=style_tag)
    cachebreak = random.randint(0, 999999999)
    if base_style_prompt not in BASE_STYLE_PROMPTS:
        base_style_prompt = DEFAULT_BASE_STYLE_PROMPT
    sample_prompt = style.prompt.format(prompt=BASE_STYLE_PROMPTS[base_style_prompt])

    # this is a UI style workflow
    workflow_fn = chapter.get_workflow(
        image_xml=BeautifulSoup("<chapter></chapter>", "xml").find("chapter"),
        workflow_name=f"{engine}_sampler",
        video_index=None,
        template_environment={
            "HEIGHT": 512,
            "WIDTH": 512,
            "PROMPT": sample_prompt,
            "FILENAME_PREFIX": os.path.join(
                "samples", style_tag + f"_{engine}_{cachebreak}"
            ),
        },
        force_name=f"{engine}_sampler_{style_tag}",
    )

    log.info("running workflow %s", workflow_fn)
    comfy.run_workflow_fn(workflow_fn=workflow_fn)

    # comfy.run_workflow(
    #     workflow,
    #     template_environment=template_environment,
    # )

    sample_dir = os.path.join(const.LIBRARY_DIR, "style_exploration")
    if base_style_prompt == "kid_friendly":
        sample_dir = os.path.join(sample_dir, "kid_friendly")
    sample_pfn = os.path.join(sample_dir, f"{style_tag}_{engine}.png")

    # make sure the output directory exists
    os.makedirs(os.path.dirname(sample_pfn), exist_ok=True)

    # move the image into it, wildcard absorbs the cachebreak
    for fn in glob.glob(
        os.path.join(
            const.COMFY_DIRS["artifactserver"]["OUTPUT_DIR"],
            "samples",
            f"{style_tag}_{engine}_*",
        )
    ):
        origin = os.path.join(
            const.COMFY_DIRS["artifactserver"]["OUTPUT_DIR"],
            "samples",
            os.path.basename(fn),
        )
        log.info("Moving %s to %s", origin, sample_pfn)
        shutil.move(origin, sample_pfn)
        break

    # move the image into it
    for fn in glob.glob(
        os.path.join(
            const.COMFY_DIRS["artifactserver"]["OUTPUT_DIR"],
            "samples",
            f"{style_tag}_{engine}_*",
        )
    ):
        origin = os.path.join(
            const.COMFY_DIRS["artifactserver"]["OUTPUT_DIR"],
            "samples",
            os.path.basename(fn),
        )
        log.info("Moving %s to %s", origin, sample_pfn)
        shutil.move(origin, sample_pfn)
        break

    if os.path.isfile(sample_pfn):
        from styler.jobs import sample_task
        from styler.sample_metadata import write_metadata
        write_metadata(sample_pfn, sample_task(dict(style_tag=style_tag, engine=engine,
                                                   base_style_prompt=base_style_prompt)),
                       prompt=sample_prompt)
    return sample_pfn


@bp.route("/regenerate", methods=["PUT"])
def regenerate_style_image():
    """
    Replace the image for a style with a new one, using the same prompt
    and a random seed.
    """
    style_tag = request.form.get("style_tag")
    engine = request.form.get("engine", "zit")
    base_style_prompt = request.form.get("base_style_prompt", "classic")

    style = styles.Style(
        style_tag=style_tag,
    )
    delete_sample(f"{style.style_tag}_{engine}.png", base_style_prompt)
    # The replacement card requests this missing sample over the page socket.
    return style.as_card(oob=True), 200


@bp.route("/draw_sample", methods=["POST"])
def draw_sample_url():
    image_model = "tsqn.zimageturbo"
    # Implement the logic for drawing a sample image
    prompt = request.form.get("prompt")
    raw_style = request.form.get("style", "")
    lora = request.form.get("lora", "")
    loras = [lora] if lora else []

    image_module = t2i_registry.get(image_model)

    soup = BeautifulSoup("", "xml")

    paragraph_xml = soup.new_tag("paragraph")
    paragraph_xml.attrs["index"] = "0"
    image_xml = soup.new_tag("image")
    paragraph_xml.append(image_xml)

    image_xml.attrs["index"] = 0
    image_xml.attrs["prompt"] = prompt
    image_xml.attrs["loras"] = json.dumps(loras)
    image_xml.attrs["seed"] = str(random.randint(0, 999999999))

    class PseudoChapter:
        def __init__(self):
            self.key = '["author", "title", "0", "english"]'
            self.author = Author("pseudo_author")

        def get_paragraph_dir(self, paragraph_index):
            pdir = os.path.join(const.LIBRARY_DIR, "style_exploration")
            os.makedirs(pdir, exist_ok=True)
            return pdir

    if raw_style:
        log.info(f"{styles=}")
        style_name = styles.as_id(raw_style)
        # wrong
        prompt_filter, negative_prompt = styles.get_style(style_name, from_all=True)

        if prompt_filter:
            styled_prompt = prompt_filter.format(prompt=prompt)
            image_xml.attrs["styled_prompt"] = styled_prompt
            image_xml.attrs["style"] = style_name
            image_xml.attrs["styled"] = True

        if negative_prompt:
            image_xml.attrs["negative_prompt"] = negative_prompt

    img_fn = images.get_image_fn(
        styled_prompt,
        loras=[],
        paragraph_dir=PseudoChapter().get_paragraph_dir(0),
        image_index=0,
        randomized=False,
    )
    full_img_fn = os.path.join(
        const.LIBRARY_DIR, "style_exploration", os.path.basename(img_fn)
    )
    if not os.path.exists(full_img_fn):
        image_module(PseudoChapter()).generate_image("pseudo", image_xml, sample=True)
        # /home/jkane/books/active/style_exploration/img_0_A_colored_pencil_picture__36dfa3e7_35f8.png
        with open(full_img_fn + ".json", "w") as f:
            json.dump(
                {
                    "prompt": prompt,
                    "style": raw_style,
                    "loras": loras,
                },
                f,
                indent=2,
            )

    return "", 200


@bp.route("/draw_all_styles", methods=["POST"])
def draw_all_styles():
    image_model = "tsqn.zimageturbo"
    # Implement the logic for drawing a sample image
    prompt = request.form.get("prompt")

    image_module = t2i_registry.get(image_model)

    soup = BeautifulSoup("", "xml")

    paragraph_xml = soup.new_tag("paragraph")
    paragraph_xml.attrs["index"] = "0"
    image_xml = soup.new_tag("image")
    paragraph_xml.append(image_xml)

    image_xml.attrs["index"] = 0
    image_xml.attrs["prompt"] = prompt
    image_xml.attrs["seed"] = str(random.randint(0, 999999999))

    class PseudoChapter:
        def __init__(self):
            self.key = '["author", "title", "0", "english"]'
            self.author = Author("pseudo_author")

        def get_paragraph_dir(self, paragraph_index):
            pdir = os.path.join(const.LIBRARY_DIR, "style_exploration")
            os.makedirs(pdir, exist_ok=True)
            return pdir

    all_styles = styles.all_styles(all_means_all=True)

    for style in all_styles:
        log.info("APPLYING STYLE: %s", style)
        style_name = styles.as_id(style["name"])

        prompt_filter = style.get("prompt", "")
        negative_prompt = style.get("negative_prompt", "")

        if prompt_filter:
            styled_prompt = prompt_filter.format(prompt=prompt)
            image_xml.attrs["styled_prompt"] = styled_prompt
            image_xml.attrs["style"] = style_name
            image_xml.attrs["styled"] = True

        if negative_prompt:
            image_xml.attrs["negative_prompt"] = negative_prompt

        img_fn = images.get_image_fn(
            styled_prompt,
            loras=[],
            paragraph_dir=PseudoChapter().get_paragraph_dir(0),
            image_index=0,
            randomized=False,
        )
        full_img_fn = os.path.join(
            const.LIBRARY_DIR, "style_exploration", os.path.basename(img_fn)
        )
        if not os.path.exists(full_img_fn):
            log.warning(
                "Style Exploration image missing: %s, generating a replacement...",
                img_fn,
            )
            image_module(PseudoChapter()).generate_image(
                "pseudo", image_xml, sample=True
            )

            # /home/jkane/books/active/style_exploration/img_0_A_colored_pencil_picture__36dfa3e7_35f8.png
            log.info("Creating %s", full_img_fn + ".json")
            with open(full_img_fn + ".json", "w") as f:
                json.dump(
                    {
                        "prompt": styled_prompt,
                        "style": style_name,
                        "loras": [],
                    },
                    f,
                    indent=2,
                )
        else:
            log.info(
                "Style Exploration image already exists: %s, skipping generation.",
                img_fn,
            )

    return "", 200


@bp.route("/draw_all_lora", methods=["POST"])
def draw_all_lora():
    image_model = "tsqn.zimageturbo"
    # Implement the logic for drawing a sample image
    prompt = request.form.get("prompt")

    image_module = t2i_registry.get(image_model)

    soup = BeautifulSoup("", "xml")

    paragraph_xml = soup.new_tag("paragraph")
    paragraph_xml.attrs["index"] = "0"
    image_xml = soup.new_tag("image")
    paragraph_xml.append(image_xml)

    image_xml.attrs["index"] = 0
    image_xml.attrs["prompt"] = prompt
    image_xml.attrs["seed"] = str(random.randint(0, 999999999))

    class PseudoChapter:
        def __init__(self):
            self.key = '["author", "title", "0", "english"]'
            self.author = Author("pseudo_author")

        def get_paragraph_dir(self, paragraph_index):
            pdir = os.path.join(const.LIBRARY_DIR, "style_exploration")
            os.makedirs(pdir, exist_ok=True)
            return pdir

    for lora_info in styles.all_loras():
        log.info("APPLYING LORA: %s", lora_info["filename"])
        loras = [
            lora_info["filename"],
        ]
        image_xml.attrs["loras"] = json.dumps(loras)

        img_fn = images.get_image_fn(
            prompt,
            loras=loras,
            paragraph_dir=PseudoChapter().get_paragraph_dir(0),
            image_index=0,
            randomized=False,
        )

        full_image_fn = os.path.join(
            const.LIBRARY_DIR, "style_exploration", os.path.basename(img_fn)
        )
        if not os.path.exists(full_image_fn):
            log.warning(
                "Lora Exploration image missing: %s, generating a replacement...",
                img_fn,
            )
            image_module(PseudoChapter()).generate_image(
                "pseudo", image_xml, sample=True
            )

            # /home/jkane/books/active/style_exploration/img_0_A_colored_pencil_picture__36dfa3e7_35f8.png
            log.info("Creating %s", full_image_fn + ".json")
            with open(full_image_fn + ".json", "w") as f:
                json.dump(
                    {
                        "prompt": prompt,
                        "style": "",
                        "loras": loras,
                    },
                    f,
                    indent=2,
                )
        else:
            log.info(
                "Lora Exploration image already exists: %s, skipping generation.",
                img_fn,
            )
    return "", 200


@bp.route("/add_tag", methods=["POST"])
def add_tag():
    style_tag = request.form.get('style_tag')
    tag = request.form.get('tag', '').strip()
    if not style_tag or not tag or len(tag) > 200:
        return 'Style and tag are required', 400
    try:
        style = styles.Style(style_tag)
    except ValueError:
        return 'Style not found', 404
    styles.add_tag_to_style(None, style.style_tag, tag)
    return flask.jsonify(tag=tag, count=styles.r.scard(tag + '/tags'))


@bp.route("/add_style", methods=["POST"])
def add_style():
    # copy a style from styles-all.json to styles.json
    style_name = request.form.get("style")
    styles.enable_style(style_name)
    return "", 200


def tier_to_letter(tier):
    #        0    1    2    3    4    5    6    7
    if tier:
        return ["F", "D", "C", "B", "A", "S", "SS", "SSS"][tier]
    else:
        return "Unranked"


CARD_DETAIL = {}


def card_detail(image_name):
    if image_name in CARD_DETAIL:
        return CARD_DETAIL[image_name]

    full_image_name = os.path.join(
        const.LIBRARY_DIR,
        "style_exploration",
        image_name,
    )

    meta = None

    if os.path.exists(full_image_name + ".json"):
        with open(full_image_name + ".json", "r") as f:
            meta = json.loads(f.read())

        if not meta:
            log.warning("Metadata file for %s is empty", full_image_name + ".json")
    else:
        log.warning(
            "Metadata file missing for %s, expected at %s",
            full_image_name,
            full_image_name + ".json",
        )

    CARD_DETAIL[image_name] = {
        "image_name": image_name,
        "full_image_name": full_image_name,
        "meta": meta,
    }

    return CARD_DETAIL[image_name]


def get_favorite_control_button(style, favorite_styles):
    """
    Returns button suitable for toggling the 'favorite' status of a style.
    """
    log.info(
        "get_favorite_control_button(style, favorite_styles)",
        style_name=style.name,
        favorite_styles=favorite_styles,
    )
    # convert favorite_styles, a [] of {} with attributes including
    # 'name', into a dict indexed by style names
    # that isn't very efficient.
    # hwo about this
    for s in favorite_styles:
        if style.name == s["name"]:
            return f"""<wa-button
                hx-delete="choice"
                hx-vals="js:{{style: '{style.name}'}}"
                variant="warning"
                pill
            >Ignore</wa-button>"""
    else:
        return f"""<wa-button
            hx-post="choice"
            hx-vals="js:{{style: '{style.name}'}}"
            variant="success"
            pill
        >Favorite</wa-button>"""


def style_tier_slider(style, engine):
    from html import escape
    endpoint = escape(url_for('styles.set_style_tier', style_tag=style.style_tag, engine=engine), quote=True)
    engine_name = dict(zit='Z-Image Turbo', krea2='Krea 2', qwen='Qwen Image 2.1')[engine]
    markers = ''.join(
        f'<span slot="reference" title="{cosmetic} Tier" style="left:{tier / 7 * 100:.6f}%">'
        f'<wa-icon name="{icon}" label="{cosmetic} Tier"></wa-icon> {cosmetic}</span>'
        for tier, icon, _, cosmetic in styles.tier_cosmetics
    )
    return (
        f'<wa-slider class="style-tier-slider" data-style-tag="{escape(style.style_tag, quote=True)}" data-engine="{engine}" '
        f'label="{engine_name} tier" name="tier" min="0" max="7" step="1" '
        f'value="{style.get_engine_tier(engine)}" with-markers with-tooltip '
        f'hx-post="{endpoint}" hx-trigger="change" '
        'hx-vals="js:{tier: this.value}" hx-swap="none">'
        f'{markers}</wa-slider>'
    )


@bp.route("/style_details")
def style_details(oob=False):
    from html import escape
    tag = request.form.get("tag") or request.args.get("tag")
    style_tag = request.form.get("style_tag") or request.args.get("style_tag")
    base_style_prompt = request.form.get("base_style_prompt") or request.args.get("base_style_prompt", "classic")
    log.info("style_details()", style_tag=style_tag)

    favorite_styles = styles.all_favorite_styles()

    try:
        style = styles.Style(style_tag=style_tag)
    except ValueError as e:
        log.error("Error getting style: %s", e)
        return "", 404

    from styler.image_tags import EVENTS, reconcile_style_on_detail, request_missing_tags
    pending_tag_tasks = request_missing_tags(styles.r, style.style_tag, const.LIBRARY_DIR)
    pending_tag_engines = {task['engine'] for task in pending_tag_tasks}
    status = (None if pending_tag_tasks else
              reconcile_style_on_detail(styles.r, style.style_tag, const.LIBRARY_DIR))
    if status:
        styles.r.publish(EVENTS, json.dumps(status))

    tag_cloud = (f'<div id="tag_cloud_{escape(style.key, quote=True)}" '
                 f'class="wa-cluster tags-removable" data-style-tag="{escape(style.style_tag, quote=True)}">')
    tag_chips = ''

    found = False
    for member_tag in style.tags:
        log.info("member %s", member_tag)
        if member_tag == tag:
            found = True
            tag_chips += f"""
            <wa-tag size="m" with-remove pill variant="danger">{escape(member_tag)}</wa-tag>
            """
        else:
            tag_chips += f"""
            <wa-tag size="m" with-remove pill>{escape(member_tag)}</wa-tag>
            """

    if not found:
        # there is an inconsistency; this style isn't supposed to be a member of
        # this tag.
        style.remove_tag(tag)

    tag_search = f"""
            <label class="style-tag-picker-label">Add Tag to {escape(style.name)}
                <input class="style-tag-picker" type="text" list="styler-tag-options"
                    placeholder="Search tags or type a new tag" autocomplete="off"
                    aria-label="Add Tag: search or create a tag">
            </label>
            """
    tag_cloud += tag_search + tag_chips + '</div>'


    favorite_control_button = get_favorite_control_button(style, favorite_styles)

    engines = (('zit', 'Z-Image'), ('krea2', 'Krea 2'), ('qwen', 'Qwen 2.1'))
    tier_buttons = '<div class="wa-grid styler-tier-controls">' + ''.join(
        '<div class="styler-engine-cell">' + style_tier_slider(style, engine) + '</div>'
        for engine, _ in engines
    ) + '</div>'

    out = ""
    prefix = ""

    style_prompt = style.prompt
    style_negative_prompt = style.negative_prompt

    prompt_prefix, prompt_postfix = style_prompt.split("{prompt}")
    prompt_prefix_attr = escape(prompt_prefix, quote=True)
    prompt_postfix_attr = escape(prompt_postfix, quote=True)

    clean_prompt = style.prompt
    clean_prompt = clean_prompt.removeprefix(prompt_prefix).removesuffix(prompt_postfix)

    # the tax of using this style prompt.
    token_price = (len(prompt_prefix) + len(prompt_postfix)) * CHARS_TO_TOKENS
    prompt_price = len(clean_prompt) * CHARS_TO_TOKENS

    # {favorite_control_button}

    out += f"""
    <div class="wa-grid styler-engine-controls">
        <div class="wa-stack wa-gap-xs styler-engine-control">
            <div class="wa-cluster">
            <wa-button 
                data-regenerate
                hx-put="regenerate" 
                hx-vars="{{engine: 'zit', style_tag: '{style_tag}', base_style_prompt: '{base_style_prompt}'}}"
                variant="brand"
                hx-swap="none"
                pill>Regenerate</wa-button>
            <wa-button 
                href="regenerate_ui?engine=zit&style_tag={style_tag}&base_style_prompt={base_style_prompt}"
                hx-get="regenerate_ui?engine=zit&style_tag={style_tag}&base_style_prompt={base_style_prompt}"
                target="_blank"
                variant="brand" 
                appearance="accent"
                pill>
                <wa-icon src="/static/images/comfyui.svg"></wa-icon>
            </wa-button>
            </div>
            <wa-button data-style-retag="{escape(style_tag, quote=True)}" data-engine="zit" data-base-style-prompt="{escape(base_style_prompt, quote=True)}" pill{' disabled' if 'zit' in pending_tag_engines else ''}>Retag Z-Image</wa-button>
            <span class="retag-status" role="status" aria-live="polite">{'Retag queued' if 'zit' in pending_tag_engines else ''}</span>
        </div>
        <div class="wa-stack wa-gap-xs styler-engine-control">
            <div class="wa-cluster">
            <wa-button 
                data-regenerate
                hx-put="regenerate"
                hx-vars="{{engine: 'krea2', style_tag: '{style_tag}', base_style_prompt: '{base_style_prompt}'}}"
                variant="brand"
                hx-swap="none"
                pill>Regenerate</wa-button>
            <wa-button 
                href="regenerate_ui?engine=krea2&style_tag={style_tag}&base_style_prompt={base_style_prompt}"
                hx-get="regenerate_ui?engine=krea2&style_tag={style_tag}&base_style_prompt={base_style_prompt}"
                target="_blank"
                variant="brand" 
                appearance="accent"
                pill>
                <wa-icon src="/static/images/comfyui.svg"></wa-icon>
            </wa-button>
            </div>
            <wa-button data-style-retag="{escape(style_tag, quote=True)}" data-engine="krea2" data-base-style-prompt="{escape(base_style_prompt, quote=True)}" pill{' disabled' if 'krea2' in pending_tag_engines else ''}>Retag Krea 2</wa-button>
            <span class="retag-status" role="status" aria-live="polite">{'Retag queued' if 'krea2' in pending_tag_engines else ''}</span>
        </div>
        <div class="wa-stack wa-gap-xs styler-engine-control">
            <div class="wa-cluster">
            <wa-button 
                data-regenerate
                hx-put="regenerate"
                hx-vars="{{engine: 'qwen', style_tag: '{style_tag}', base_style_prompt: '{base_style_prompt}'}}"
                variant="brand"
                hx-swap="none"
                pill>Regenerate</wa-button>
            <wa-button 
                href="regenerate_ui?engine=qwen&style_tag={style_tag}&base_style_prompt={base_style_prompt}"
                hx-get="regenerate_ui?engine=qwen&style_tag={style_tag}&base_style_prompt={base_style_prompt}"
                target="_blank"
                variant="brand" 
                appearance="accent"
                pill>
                <wa-icon src="/static/images/comfyui.svg"></wa-icon>
            </wa-button>
            </div>
            <wa-button data-style-retag="{escape(style_tag, quote=True)}" data-engine="qwen" data-base-style-prompt="{escape(base_style_prompt, quote=True)}" pill{' disabled' if 'qwen' in pending_tag_engines else ''}>Retag Qwen 2.1</wa-button>
            <span class="retag-status" role="status" aria-live="polite">{'Retag queued' if 'qwen' in pending_tag_engines else ''}</span>
        </div>
    </div>
    <div 
        id="style_card_{style_tag}"
        class="wa-stack wa-align-items-left" 
        {"hx-swap-oob='true'" if oob else ""}>

        {tier_buttons}
        {favorite_control_button}
        {tag_cloud}
    </div>
    """
    out += f"""<hr/><h4>Tokens</h4>
        <p>zImage Max: 512, best adherance < 75</p>
        <div 
            id="style_card_tokens" 
            class="label-on-left"
            {"hx-swap-oob='true'" if oob else ""}>
            <wa-input label="Style" value="{ token_price }" disabled></wa-input>
            <wa-input label="Prompt" value="{ prompt_price }" disabled></wa-input>
            <wa-input label="TOTAL" value="{ token_price + prompt_price } tokens" disabled></wa-input>
        </div>
    """

    out += f"""
    <div 
        id="style_card_prompt_{style_tag}"
        class="wa-stack"
        {"hx-swap-oob='true'" if oob else ""}>
        <p><wa-textarea label="Prefix" value="{prompt_prefix_attr}"></wa-textarea></p>
        <p><wa-textarea label="Postfix" value="{prompt_postfix_attr}"></wa-textarea></p>
    </div>
    """
    # <p><wa-textarea label="Prompt" placeholder="{clean_prompt}" disabled></wa-textarea></p>

    # </wa-details>
    # </wa-card>
    # I like these categories, because I think they can speak well to
    # intent and divides the space reasonably well, albiet not always
    # predictably.
    # soup = BeautifulSoup(out, "html.parser")
    return (out, 200)


@bp.get('/style_description')
def style_description():
    from html import escape

    style_tag = request.args.get('style_tag', '')
    try:
        from styler.style_description import request as request_description
        result = request_description(styles.r, style_tag)
    except ValueError:
        return '<p class="styler-historical-description">Style not found.</p>', 404
    except Exception:
        log.exception('Unable to queue historical description for style %s', style_tag)
        return '<p class="styler-historical-description">Unable to queue historical context.</p>', 503

    element_id = escape(f"style_history_{result['style_key']}", quote=True)
    style_name = escape(result['style_tag'], quote=True)
    if result['state'] == 'complete':
        description = escape(result['description'])
        return (f'<p id="{element_id}" class="styler-historical-description" '
                f'data-style-history-description data-style-tag="{style_name}">{description}</p>')
    return (f'<p id="{element_id}" class="styler-historical-description" '
            f'data-style-history-description data-style-tag="{style_name}" '
            'role="status" aria-live="polite">Historical description queued…</p>')


@bp.route("/style/name", methods=["PUT"])
def set_style_name():
    style_tag = (request.values.get("style_tag") or "").strip()
    name = (request.values.get("name") or "").strip()
    if not style_tag or not name:
        return "Style and name are required", 400
    if len(name) > 160:
        return "Style name must be 160 characters or fewer", 400
    try:
        style = styles.Style(style_tag=style_tag)
    except ValueError:
        return "Style not found", 404
    style.style["name"] = name
    style.save_to_redis()
    return flask.jsonify(style_tag=style.style_tag, name=style.name)


# http://localhost:8080/styles/regenerate_ui?category=verbing&style_tag=appraising
@bp.route("/regenerate_ui", methods=["GET"])
def regenerate_ui():
    """
    Create the ComfyUI workflow for regenerating a style image, then redirect to it.
    """
    engine = request.args.get("engine", "zit")
    style_tag = request.args.get("style_tag")

    style = styles.Style(style_tag=style_tag)
    log.info("style retrieved", style=style)

    # Chapter stub
    c = chapter.Chapter(
        author=Author(""),
        title="",
        number=0,
        language="english",
    )

    # image_xml = BeautifulSoup(
    #     "<chapter></chapter>", "xml"
    # ).find("image")
    # image_xml.attrs = {
    #     "index": 0,
    #     "prompt": style["prompt"].format(prompt=base_prompt),
    #     "seed": str(random.randint(0, 999999999)),
    #     "style": style_tag,
    # }
    cachebreak = random.randint(0, 999999999)

    workflow_fn = c.get_workflow(
        image_xml=BeautifulSoup("<chapter></chapter>", "xml").find("chapter"),
        workflow_name=f"{engine}_sampler",
        video_index=None,
        template_environment={
            "HEIGHT": 512,
            "WIDTH": 512,
            "PROMPT": style.prompt.format(prompt=get_base_style_prompt()),
            "FILENAME_PREFIX": os.path.join(
                "samples", style_tag + f"_{engine}_{cachebreak}"
            ),
        },
        force_name=f"{engine}_sampler_{style_tag}",
    )

    # workflow = c.get_workflow(
    #     image_xml=BeautifulSoup("<chapter></chapter>", "xml").find("chapter"),

    #     image_xml=image_xml,
    #     interface="ui",
    #     mode="t2i",
    #     workflow_template="style_sample",
    #     template_environment={
    #         "PROMPT": style["prompt"].format(prompt=base_prompt),
    #         "FILENAME_PREFIX": os.path.join("samples", style["name"]),
    #         "STYLE_CATEGORY": category_name,
    #     },
    # )

    # this is the pisser that will show up in your workflow list.
    # workflow_name = f"style_{style_tag}_sample"
    # workflow_fn = os.path.join(
    #     const.COMFY_DIRS["artifactserver"]["WORKFLOWS_DIR"],
    #     workflow_name + ".json",
    # )
    # with open(workflow_fn, "w") as h:
    #     json.dump(workflow, h)

    # 2. Redirect to the workflow page for that workflow in comfyui.
    workflow_url = const.COMFYUI_UI_URL + f"?workflow={workflow_fn}"

    return flask.redirect(workflow_url, code=302)


# http://localhost:8080/style/renaissance__1300___1600_/tier
@bp.route("/<style_tag>/tier", methods=["POST"])
@bp.route("<category_name>/<style_tag>/tier", methods=["POST"])
def set_style_tier(style_tag, category_name=None):
    try:
        engine = request.args.get('engine') or request.form.get('engine')
        if engine not in ('zit', 'krea2', 'qwen'):
            raise ValueError('Engine required')
        tier = int(request.form['tier'])
        if not 0 <= tier < len(styles.tier_cosmetics):
            raise ValueError('Invalid tier')
    except (KeyError, TypeError, ValueError):
        return "Invalid tier", 400

    log.info(
        "Setting style %s in category %s to tier %s", style_tag, category_name, tier
    )

    style = styles.Style(style_tag)
    style.set_engine_tier(engine, tier)

    return style.engine_tier_badge(engine, oob=True), 200


# # @cache.cached(timeout=60 * 60 * 24)
# def history_cards(min_tier, max_tier, **filters):
#     count = defaultdict(int)
#     fcount = defaultdict(int)
#     card_details = {}
#     history_list = []
#     # we want a two layer list; the number of elements in the second layer is
#     # the fun bit.  What makes sense is going to vary quite a bit based on how
#     # many entries we have.  I'm not sure what the answer is, but it's
#     # reasonably easy to build some prototypes and see what works.

#     # iterating a directory for png/json file pairs.
#     # there is no DB here, it's just showing all the files it has.
#     for fn in sorted(
#         os.listdir(
#             os.path.join(
#                 const.LIBRARY_DIR,
#                 "style_exploration"
#             )
#         )
#     ):
#         if not fn.endswith(".png"):
#             continue

#         # the idea is we're passing through (badly) the filter toggles, then the
#         # card builder can self-reject (empty response handled by the card.strip() below).

#         # there is metadata associated with each card, single_card() extracts the 'tier'
#         # metadata and applies the filters you give it.
#         card, tier = single_card(
#             fn,
#             min_tier=min_tier,
#             max_tier=max_tier,
#             constraint_toggles=filters.get("constraint_toggles"),
#             chosen_styles=filters.get("chosen_styles"),
#         )

#         # do we want the totals on the top of the page to be filtered totals or actual totals?
#         # Exactly.  So glad we can agree on both. (count vs. fcount, fcount is filtered count)
#         count[tier] += 1

#         if card.strip():
#             log.debug("Including card for %s with tier %s to history list", fn, tier)
#             fcount[tier] += 1
#             history_list.append(card)
#             card_details[fn] = card_detail(fn)
#         else:
#             log.debug("Filtering card for %s with filters: %s", fn, filters)

#     # log.info(f'Returning {history_list=}, {count=}')
#     return history_list, card_details, count, fcount


# def get_chosen_styles():
#     chosen_styles = styles.all_styles()
#     return [styles.style_name_to_key(s["name"]) for s in chosen_styles]


# @bp.route("/history")
# def history():
#     min_tier = request.args.get("min_tier", 4)  # default to C or better
#     max_tier = request.args.get("max_tier", 7)  # SSS or worse
#     hide_styles = request.args.get("hide_styles", "off").lower() == "on"
#     hide_loras = request.args.get("hide_loras", "off").lower() == "on"
#     only_chosen = request.args.get("only_chosen", "off").lower() == "on"
#     history_list, card_details, count, fcount = history_cards(
#         min_tier=min_tier,
#         max_tier=max_tier,
#         hide_styles=hide_styles,
#         hide_loras=hide_loras,
#         only_chosen=only_chosen,
#         chosen_styles=get_chosen_styles(),
#     )

#     return "\n".join(history_list), 200


class Card:
    """
    Obsolete.  Use styles.Style instead.

    styles.Style().as_card() is the new way to do this.  This class is only here
    for backwards compatibility and will be removed.

    """

    def __init__(self, style_name: str, **meta):
        if style_name is None:
            raise ValueError("style_name is required")

        # category_name = meta.pop("category_name", None)
        log.info("Card.__init__()", style_name=style_name, meta=meta)
        self.style_name = style_name

        # if category_name is None:
        #     self.category_name = self.find_category(style_name)
        # else:
        #     self.category_name = category_name

        self.tag = as_id(style_name)
        if "tier" in meta:
            self.tier = int(meta["tier"])
        else:
            log.warning(
                "No tier found for style %s, defaulting to %s", style_name, default_tier
            )
            self.tier = default_tier

        self.meta = meta

    # @staticmethod
    # def find_group(style_name):
    #     """
    #     Given a style name, find the first group containing that style.
    #     """
    #     for category in styles.all_categories():
    #         for group in styles.style_groups_in_category(category["category"]):
    #             for style in styles.styles_in_group(category["category"], group):
    #                 if style["name"] == style_name:
    #                     return group

    @staticmethod
    def find_category(style_name):
        """
        Given a style name, find the first group containing that style.
        """
        log.error("Obsolete find_category()")
        for category in styles.all_categories():
            for group in styles.style_groups_in_category(category["category"]):
                for style in styles.styles_in_group(category["category"], group):
                    if style["name"] == style_name:
                        return category["category"]

    @classmethod
    def from_style_name(cls, style_name):
        log.error("Obsolete from_style_name()")
        # this is rough.  Just having a style name isn't enough unless we've ALREADY
        # done an image, in which case we have metadata cached and it's no problem.
        full_metadata_name = os.path.join(
            const.LIBRARY_DIR,
            "style_exploration",
            style_name + ".json",
        )

        if os.path.exists(full_metadata_name):
            with open(full_metadata_name, "r") as f:
                meta = json.loads(f.read())
            return cls(style_name=style_name, group_name=meta.get("group_name"), **meta)
        else:
            # we have not done this style before, and we don't know
            # which category it belongs to.
            category_name = cls.find_category(style_name)
            if category_name:
                log.info(
                    f"Found category {category_name} for style {style_name} in styles.json"
                )
                return cls(style_name=style_name, category_name=category_name)

        log.error(f"No Style Found for {style_name}.")
        return None

    @property
    def image_filename(self):
        full_image_name = os.path.join(
            const.LIBRARY_DIR,
            "style_exploration",
            self.style_name + ".png",
        )
        return full_image_name

    @property
    def image_metadata_filename(self):
        full_metadata_name = os.path.join(
            const.LIBRARY_DIR,
            "style_exploration",
            self.style_name + ".json",
        )
        return full_metadata_name

    @property
    def style(self):
        return self.style_name

    @property
    def prompt(self):
        return self.meta.get("prompt", "{prompt}")

    def set_meta(self, key, value):
        self.meta[key] = value

        with open(self.image_metadata_filename, "w") as f:
            json.dump(self.meta, f, indent=2)

    def as_html(self, oob=False):
        # rank_str = f"<em>{self.tier} Tier</em>"
        style_info = f"<p><b>Style:</b> {self.style}</p>"
        loras_info = ""

        try:
            prompt_prefix, prompt_postfix = self.prompt.split("{prompt}")
        except ValueError:
            log.error('Invalid prompt: does not contain "{prompt}"', prompt=self.prompt)
            prompt_prefix = self.prompt
            prompt_postfix = ""

        _, icon, variant, cosmetic = styles.tier_cosmetics[self.tier]

        badge = f"""
        <wa-badge pill class="tier-badge" variant="{variant}">
            <wa-icon slot="start" name="{icon}"></wa-icon>
            {cosmetic}
        </wa-badge>
        """

        zit_image_url = url_for(
            "styles.show_sample",
            filename=f"{self.style_name}_zit.png",
        )

        return f"""
        <wa-card 
            id="card_{self.tag}"
            {"hx-swap-oob='true'" if oob else ""}
            class="card-overview history-item-{self.tag}"
        >
            <img
                loading="lazy"
                class="image_sample"
                slot="media"
                src="{zit_image_url}"
                alt="Sample Image"
            >
                {badge}
            </img>
            <wa-details
                hx-get="style_details"
                hx-vals='{{"style_tag": "{self.tag}"}}'
                hx-trigger="intersect once"
                hx-target="#style_details_{self.tag}"
                icon-placement="start">
                <span slot="summary">
                    {style_info}{loras_info}
                </span>
                
                <div id="style_details_{self.tag}"></div>
            </wa-details>
        </wa-card>
        """

    # #hx-trigger="wa-show from:#card_{self.tag}"]"

    def details(self):
        return {
            "tag": self.tag,
            "style_name": self.style_name,
            "full_image_name": self.image_filename,
            "meta": self.meta,
        }


class Grouper:
    def __init__(self, style_history, card_details, count, fcount):
        self.style_history = style_history
        self.card_details = card_details
        card_details_list = list(card_details.values())
        self.count = count
        self.fcount = fcount

        # we have 'count' total entries.  With the current filters applied, we
        # have fcount entries. we will do two passes, and cache the first.  The
        # first pass is looking for logical categories for objects like these.
        # We're going to AI the categorization itself.

        # card_details is a dict keyed on filename of: {
        #   "image_name": image_name,
        #   "full_image_name": full_image_name,
        #   "meta": { <contents of the json file next to this image>
        #     {
        #       "prompt": "2D Game Art mode, A 6-year-old child making a silly face. They have slightly messy hair. Their expression should convey a sense of pure, uninhibited fun. A 20s female is disgusted by the behavior of the child.  The image should avoid any sense of creepiness and focus on the child's playful energy and the inappropriateness of how the woman reacts., \u00ac2D Game Art is a style that focuses on creating pixelated environments, characters, and objects for two-dimensional video games. It highlights pixel art, where the visibility of individual pixels adds to the visual charm. The style uses flat colors and clean lines to craft striking and immersive worlds, often seen in platformers, side-scrollers, and retro games. The result is a blend of functionality with nostalgic aesthetics.",
        #       "style": "2d_game_art",
        # "loras": []
        #     }
        # },

        # great stuff, but we only care about "image_name" because that is
        # guaranteed unique and will be used for the sort order later. now, we
        # have an uneven distribution of objects when sorted alphabetically, but
        # if we're willing to be ridiculous with the ranges it's not a problem.
        # so lets try that.
        categories = {}

        categories[1] = {
            0: {"cosmetic": "All", "category_index": 0, "contents": card_details_list}
        }

        for N in range(2, 12):
            # we want to split card_details into N _EVEN_ groups, based on the image_name field.
            # it's a string, and we want to provide pretty "Aa..De" style labels.
            # for index in range(N):
            #    card_details[]
            categories[N] = {}

            fn_list = card_details.keys()
            total_cards = len(fn_list)

            if total_cards == 0:
                continue

            previous_end = ""
            cards_per_category = total_cards / N
            for category_index in range(N):
                categories[N][category_index] = {}

                start_index = int(category_index * cards_per_category)
                end_index = int((category_index + 1) * cards_per_category)
                start_word = card_details_list[start_index]["meta"]["style"]
                log.info(
                    "Category %s: start_index=%s, end_index=%s, start_word=%s",
                    category_index,
                    start_index,
                    end_index,
                    start_word,
                )
                log.debug(
                    "card_details_list[end_index-1]=%s",
                    card_details_list[end_index - 1],
                )
                end_word = card_details_list[end_index - 1]["meta"]["style"]

                # ok, now how many letters do we need to differentiate between start_word and end_word?
                # length = len(os.path.commonprefix([start_word, end_word])) + 1
                # still not good enough.  These must be hard breaks.  start needs to be = length with
                # previous end
                length = 6

                categories[N][category_index]["cosmetic"] = (
                    f"{start_word[:length]}..{end_word[:length]}"
                )
                categories[N][category_index]["category_index"] = category_index
                categories[N][category_index]["contents"] = card_details_list[
                    start_index:end_index
                ]
                previous_end = end_word

            # categories[N]["contents"] = [ card_details[fn] for fn in fn_list ]
            # {{ grouping.contents|safe }}
        self.categories = categories

        # how many categories do we want?  I like N, at least for N < 12 becuase
        # after that the number of tabs becomes an interface problem.  N can be
        # fun.  We can pre-generate them, just go through the whole thing 2..N
        # # times.  It's storage-cheap.
        # categories = {}
        # for N in range(2, 12):
        #     system_prompt = ("""Group these %s items into %s categories based on the style
        #     and content of the included metadata.  The categories should be
        #     non-overlapping and collectively exhaustive.  The grouping should
        #     be based on the metadata of the images, such as the style, loras,
        #     and prompt.  The grouping should not be based on the image name,
        #     but rather the content and style of the image as described by the
        #     metadata.  The output should be a list of groups, where each group is
        #     a lis t of image names that belong to that group.""" % (len(card_details), N)).replace("\n", " ")

        #     # this is going to be too big, right?  of course it is, this is totally insane.
        #     categories[N] = llm.str_prompt(prompt = card_details, system_prompt = system_prompt)

        #     log.info('Added N=%s  categories=%s', N, categories[N])

    def members(self, count, category_index: int):
        log.info("members(%s, %s)", count, category_index)
        out = []
        log.debug(
            "Delivering members for %s", self.categories[count].get(category_index)
        )
        # category_index = int(category_index.removeprefix("group_"))

        try:
            category = self.categories[count][category_index]
        except KeyError:
            log.error(
                f"Category index {category_index} not found in categories[{count}]."
            )
            log.error(
                f"Available categories for count {count}: {self.categories[count].keys()}"
            )
            return []

        # very noisy
        # log.debug("Category %s: %s", category_index, category)

        # out.append(self.categories[count][category]["contents"])
        for image_metadata in category["contents"]:
            log.debug("image_metadata=%s", image_metadata)
            out.append(Card.from_style_name(image_metadata["style_name"]))

        return out

    def groupings(self, count):
        out = []
        log.debug("Delivering self.categories[%s]", count)
        for category_index in self.categories[count]:
            # category_index = int(category_index)
            out.append(
                {
                    "tag": category_index,
                    "category_index": self.categories[count][category_index][
                        "category_index"
                    ],
                    "cosmetic": self.categories[count][category_index]["cosmetic"],
                }
            )
        return out

    def indexof(self, count, category_index):
        log.info("indexof(%r, %r)", count, category_index)
        try:
            category_index = int(category_index.removeprefix("group_"))
        except AttributeError:
            log.error(f"Invalid category_index format: {category_index}")
            return None

        try:
            value = self.categories[count][category_index]["category_index"]
            log.info("indexof(%s, %s) = %s", count, category_index, value)
            return value
        except KeyError:
            log.error(
                f"self.categories[{count}][{category_index}]['category_index'] requested."
            )
            log.error(
                f"self.categories[{count}].keys(): {self.categories[count].keys()}"
            )
            log.error(
                f"self.categories[{count}][{category_index}]: %s",
                self.categories[count].get(str(category_index)),
            )
            # log.error("self.categories: %s", json.dumps(self.categories, indent=2))

            return None


def stockpile_entry(tag, page=0):
    from stockpile import tagged_images
    images = tagged_images(const.LIBRARY_DIR, tag)
    page = max(0, min(page, max(0, (len(images) - 1) // 3)))
    start = page * 3
    return render_template('stockpile-entry.html', tag=tag, images=images[start:start + 3],
                           page=page, total=len(images), start=start,
                           has_previous=page > 0, has_next=start + 3 < len(images))


@bp.route('/stockpile')
def stockpile_page():
    return stockpile_entry(request.args.get('tag', ''), request.args.get('page', 0, type=int))


def stockpile_category_counts(style_tags):
    from stockpile import tag_counts
    aliases = {' '.join(tag.casefold().split()): tag for tag in style_tags}
    return {aliases.get(tag, tag): count for tag, count in tag_counts(const.LIBRARY_DIR).items()}


@bp.route('/stockpile/tags')
def stockpile_tags():
    style_tags = [tag.decode() if isinstance(tag, bytes) else tag for tag in styles.r.smembers('tags')]
    counts = stockpile_category_counts(style_tags)
    with styles.r.pipeline(transaction=False) as pipe:
        for tag in counts:
            pipe.scard(tag + '/tags')
        style_counts = pipe.execute()
    return flask.jsonify(categories=[dict(tag=tag, count=count, stockpile_count=counts[tag])
                                      for tag, count in zip(counts, style_counts)])


@bp.route('/stockpile/image/<path:filename>')
def stockpile_image(filename):
    from pathlib import Path
    from stockpile import EXTENSIONS
    root = (Path(const.LIBRARY_DIR) / 'stockpile').resolve()
    path = (root / filename).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        flask.abort(404)
    if path.suffix.lower() not in EXTENSIONS:
        flask.abort(404)
    return send_from_directory(root, filename)


def _lost_page(page, page_size=5):
    page = max(0, page)
    while True:
        members, total = styles.lost_styles_page(styles.r, page, page_size)
        last_page = max(0, (total - 1) // page_size)
        if page <= last_page:
            return page, members, total
        page = last_page


STYLE_TAG_PAGINATION_THRESHOLD = 100
STYLE_TAG_PAGE_SIZE = 25


def _style_tag_page(tag, page, page_size=STYLE_TAG_PAGE_SIZE):
    """Return one stable, validated page of styles belonging to a tag."""
    key = tag + '/tags'
    members = sorted((value.decode() if isinstance(value, bytes) else value
                      for value in styles.r.smembers(key)), key=str.casefold)
    valid = []
    for offset in range(0, len(members), 128):
        batch = members[offset:offset + 128]
        with styles.r.pipeline(transaction=False) as pipe:
            for member in batch:
                pipe.type(styles.as_id(member))
            kinds = pipe.execute()
        for member, kind in zip(batch, kinds):
            if kind in ('hash', b'hash'):
                valid.append(member)
            else:
                styles.r.srem(key, member)
    total = len(valid)
    last_page = max(0, (total - 1) // page_size)
    page = min(max(0, page), last_page)
    page_styles = []
    for member in valid[page * page_size:(page + 1) * page_size]:
        try:
            page_styles.append(styles.Style(member))
        except ValueError:
            styles.r.srem(key, member)
    return page, page_styles, total


@bp.get('/style_page')
def style_page():
    """Return current page membership and HTML only for cards the viewer lacks."""
    from html import escape
    tag = request.args.get('tag', '')
    page_number = request.args.get('page', 0, type=int) or 0
    if tag == 'LOST':
        page_size = 5
        page, members, total = _lost_page(page_number, page_size)
    else:
        if not styles.r.sismember('tags', tag):
            flask.abort(400, 'Unknown paginated style list')
        page_size = STYLE_TAG_PAGE_SIZE
        page, members, total = _style_tag_page(tag, page_number, page_size)
    present = set(request.args.getlist('present'))
    entries = {}
    for style in members:
        if style.key not in present:
            entries[style.key] = (
                f'<div class="wa-cluster wa-align-items-start wa-gap-3xs" data-style-entry '
                f'data-style-key="{escape(style.key, quote=True)}">'
                + style.as_card(tag=tag, oob=False) + '</div>')
    return flask.jsonify(tag=tag, page=page, page_size=page_size, total=total,
                         keys=[style.key for style in members], entries=entries)


@bp.route("/choose_tag", methods=["PUT"])
def choose_tag():
    from html import escape
    import math

    tag = request.form.get("tag", "")
    requested_page = request.form.get('page', None, type=int)
    from styler.backfill import start, stop
    if tag in ('LOST', 'ABYSS'):
        stop(styles.r)
    elif requested_page is None:
        start(styles.r, tag)
    parts = [f'<div class="wa-stack" hx-swap-oob="true" id="tag_{escape(tag, quote=True)}">']
    if tag in ('LOST', 'ABYSS'):
        parts.append('<div class="wa-stack card-overview"><h2 class="styler-style-name">Stockpile</h2>'
                     '<p>No stockpile images for this system tag.</p></div>')
    else:
        stockpile_url = escape(url_for('styles.stockpile_page'), quote=True)
        parts.append(f'<div data-stockpile-slot data-stockpile-tag="{escape(tag, quote=True)}" '
                     f'data-stockpile-url="{stockpile_url}"><span>Loading stockpile images…</span></div>')
    page_number = max(0, requested_page or 0)
    if tag == 'LOST':
        page_size = 5
        page_number, members, total = _lost_page(page_number, page_size)
        total_pages = max(1, math.ceil(total / page_size))
        parts[0] = (f'<div class="wa-stack" hx-swap-oob="true" id="tag_{escape(tag, quote=True)}" '
                    f'data-style-page data-page-category="{escape(tag, quote=True)}" '
                    f'data-page="{page_number}" data-page-size="{page_size}" data-page-total="{total}" '
                         f'data-page-url="{url_for("styles.style_page", base_style_prompt=request.args.get("base_style_prompt", "classic"))}">')
        from styler.backfill import restrict_lost_queue
        restrict_lost_queue(styles.r, [style.key for style in members])
        start = page_number * page_size
        page_members = members
        pager = ['<div class="wa-cluster wa-align-items-center" style="margin:0.75rem 0">']
        pager.append(f'<span data-lost-summary data-style-page-summary data-page="{page_number}" data-page-size="{page_size}">'
                     f'Showing {start + 1 if total else 0}–{min(start + page_size, total)} '
                     f'of {total} LOST styles</span>')
        for label, target_page, disabled in (
                ('Previous', page_number - 1, page_number == 0),
                ('Next', page_number + 1, page_number + 1 >= total_pages)):
            values = escape(json.dumps({'tag': 'LOST', 'page': target_page}), quote=True)
            disabled_attr = ' disabled' if disabled else ''
            pager.append(f'<wa-button hx-put="choose_tag" hx-vals="{values}" '
                         f'data-style-page-nav="{label.lower()}" hx-swap="none"{disabled_attr}>{label}</wa-button>')
        pager.append('</div>')
        parts.append(''.join(pager))
    else:
        total = styles.r.scard(tag + '/tags')
        if total > STYLE_TAG_PAGINATION_THRESHOLD:
            page_size = STYLE_TAG_PAGE_SIZE
            page_number, page_members, total = _style_tag_page(tag, page_number, page_size)
            parts[0] = (f'<div class="wa-stack" hx-swap-oob="true" id="tag_{escape(tag, quote=True)}" '
                        f'data-style-page data-page-category="{escape(tag, quote=True)}" '
                        f'data-page="{page_number}" data-page-size="{page_size}" data-page-total="{total}" '
                        f'data-page-url="{url_for("styles.style_page", base_style_prompt=request.values.get("base_style_prompt", "classic"))}">')
            start = page_number * page_size
            pager = ['<div class="wa-cluster wa-align-items-center" style="margin:0.75rem 0">']
            pager.append(f'<span data-style-page-summary data-page="{page_number}" data-page-size="{page_size}">'
                         f'Showing {start + 1 if total else 0}–{min(start + page_size, total)} '
                         f'of {total} {escape(tag)} styles</span>')
            total_pages = max(1, math.ceil(total / page_size))
            for label, target_page, disabled in (
                    ('Previous', page_number - 1, page_number == 0),
                    ('Next', page_number + 1, page_number + 1 >= total_pages)):
                values = escape(json.dumps({'tag': tag, 'page': target_page}), quote=True)
                disabled_attr = ' disabled' if disabled else ''
                pager.append(f'<wa-button hx-put="choose_tag" hx-vals="{values}" '
                             f'data-style-page-nav="{label.lower()}" hx-swap="none"{disabled_attr}>{label}</wa-button>')
            pager.append('</div>')
            parts.append(''.join(pager))
        else:
            page_members = styles.StyleLibrary().with_tag(tag)

    for style in page_members:
        parts.append(f"<div class='wa-cluster wa-align-items-start wa-gap-3xs' data-style-entry data-style-key='{escape(getattr(style, 'key', style.style_tag), quote=True)}'>")
        card = style.as_card(tag=tag, oob=False)
        parts.append(card)
        parts.append('</div>')
    parts.append('</div>')
    return ''.join(parts), 200


def style_category_tree(style):
    """Return the complete topic tree and every path touched by this style's samples."""
    from .topic_taxonomy import cached_tree
    tree = cached_tree(styles.r)
    category_tags = {value.decode() if isinstance(value, bytes) else value for value in style.tags}
    # Redis style-tag membership can be missing for older or manually edited
    # styles. Recover the original category/group memberships from source files.
    if not category_tags:
        for parent_name in os.listdir(const.STYLES_DIR):
            parent_dir = os.path.join(const.STYLES_DIR, parent_name)
            if not os.path.isdir(parent_dir):
                continue
            for filename in os.listdir(parent_dir):
                if not filename.lower().endswith('.json'):
                    continue
                path = os.path.join(parent_dir, filename)
                try:
                    with open(path, encoding='utf-8') as source:
                        entries = json.load(source)
                except (OSError, ValueError):
                    continue
                if not isinstance(entries, list):
                    continue
                if any(isinstance(entry, dict)
                       and styles.as_id(entry.get('name', '')) == style.key
                       for entry in entries):
                    category_tags.update((parent_name.lower(), os.path.splitext(filename)[0].lower()))
    from .sample_metadata import image_tags
    sample_tags = set()
    for base_style_prompt in ('classic', 'kid_friendly'):
        for engine in ('zit', 'krea2', 'qwen'):
            path = style.get_image_path(engine, base_style_prompt)
            if os.path.isfile(path):
                sample_tags.update(image_tags(path))

    def find_locations(tags):
        found = []
        for parent in (tree or {}).get('topics', []):
            for category in parent.get('categories', []):
                for substyle in category.get('tags', []):
                    if substyle in tags:
                        found.append(dict(parent=parent['name'], category=category['name'],
                                          substyle=substyle, stylename=style.key))
        return found

    locations = find_locations(category_tags)
    if not locations:
        locations = find_locations(sample_tags)
    return tree, sorted(category_tags | sample_tags), locations


@bp.get('/style_categories')
def style_categories():
    style_tag = request.args.get('style_tag', '')
    try:
        style = styles.Style(style_tag)
    except ValueError:
        return flask.jsonify(error='Style not found'), 404
    _, _, locations = style_category_tree(style)
    paths = [[location['parent'], location['category'], location['substyle'], style.key]
             for location in locations]
    return flask.jsonify(style_tag=style.style_tag, paths=paths)


@cache.memoize(timeout=60 * 60)
def _styler_style_search_index():
    """Build a compact searchable name index without loading style cards."""
    entries = {}
    for category in styles.all_categories():
        for group in styles.style_groups_in_category(category['category']):
            for style in styles.styles_in_group(category['category'], group):
                name = str(style.get('name', '')).strip()
                if not name:
                    continue
                key = styles.as_id(name)
                entries.setdefault(key, {'kind': 'style', 'name': name, 'style_tag': key})
    return list(entries.values())


@bp.get('/search_suggestions')
def styler_search_suggestions():
    """Suggest topics, categories, tags, and styles for the Styler picker."""
    query = request.args.get('q', '').strip()[:80]
    if len(query) < 2:
        return flask.jsonify(results=[])
    from .topic_taxonomy import cached_tree
    tree = cached_tree(styles.r) or {'topics': []}
    results = []
    for topic in tree.get('topics', []):
        results.append({'kind': 'topic', 'name': topic['name'], 'parent': topic['name']})
        for category in topic.get('categories', []):
            results.append({'kind': 'category', 'name': category['name'],
                            'parent': topic['name'], 'category': category['name']})
            for tag in category.get('tags', []):
                results.append({'kind': 'tag', 'name': tag,
                                'parent': topic['name'], 'category': category['name']})
    results.extend(_styler_style_search_index())
    needle = query.casefold()

    def rank(item):
        name = item['name'].casefold()
        if name == needle:
            relevance = 0
        elif name.startswith(needle):
            relevance = 1
        else:
            relevance = 2
        return relevance, len(item['name']), item['name'].casefold(), item['kind']

    matches = [item for item in results if needle in item['name'].casefold()]
    matches.sort(key=rank)
    return flask.jsonify(results=matches[:16])


@bp.get('/find_target')
def find_target():
    """Resolve a style's tag through the topic tree and open its containing page."""
    style_tag = request.args.get('style', '').strip()
    if not style_tag:
        flask.abort(400, 'Missing style parameter')
    try:
        style = styles.Style(style_tag)
    except ValueError:
        flask.abort(404, 'Style not found')

    tree, _, locations = style_category_tree(style)
    page = url_for('styles.styles_base',
                   target_engine=request.args.get('engine', ''),
                   target_action=request.args.get('action', ''),
                   base_style_prompt=request.args.get('base_style_prompt', 'classic'))
    if not locations:
        # A style can exist in the library before its tags have been assigned
        # to the topic tree. Let the picker render it as a standalone result.
        from urllib.parse import urlencode
        member_tags = [tag.decode() if isinstance(tag, bytes) else tag for tag in style.tags]
        query = urlencode({'target_style': style.key,
                           'target_tag': member_tags[0] if member_tags else style.key})
        return flask.redirect(f'{page}&{query}' if '?' in page else f'{page}?{query}')

    target = locations[0]
    from urllib.parse import quote
    fragment = ','.join(quote(value, safe='') for value in (
        target['parent'], target['category'], target['substyle'], style.key))
    return flask.redirect(page + '#' + fragment)


@bp.get('/target_card')
def target_card():
    """Render a style card for a target path even when its category is empty."""
    from html import escape
    style_tag = request.args.get('style', '').strip()
    tag = request.args.get('tag', '').strip()
    if not style_tag or not tag:
        flask.abort(400)
    try:
        style = styles.Style(style_tag)
    except ValueError:
        flask.abort(404, 'Style not found')
    return (f'<div class="wa-cluster wa-align-items-start wa-gap-3xs" data-style-entry '
            f'data-style-key="{escape(style.key, quote=True)}">'
            + style.as_card(tag=tag, oob=False) + '</div>')


@bp.get('/topics')
def styler_topics():
    """Return parent topics from the last complete background build."""
    from .topic_taxonomy import cached_tree
    tree = cached_tree(styles.r)
    pending = styles.r.get('styler:topic-tree:pending')
    if isinstance(pending, bytes):
        pending = pending.decode()
    if not tree:
        return flask.jsonify(topics=[], ready=False, building=bool(pending),
                             tag_count=0)
    return flask.jsonify(topics=[{'name': topic['name'],
                                 'description': topic.get('description', '')}
                                for topic in tree['topics']],
                         ready=True, building=bool(pending),
                         built_at=tree.get('built_at'), tag_count=tree.get('tag_count', 0),
                         tag_digest=tree.get('tag_digest'))


@bp.post('/topics/rebuild')
def styler_topics_rebuild():
    """Ask the idle worker to rebuild the entire category tree."""
    from .topic_jobs import enqueue_taxonomy_rebuild
    try:
        result = enqueue_taxonomy_rebuild(styles.r, reason='manual')
        code = 202 if result.get('state') in ('queued', 'already_queued') else 503
        return flask.jsonify(result), code
    except Exception as exc:
        log.error('Unable to queue Styler topic tree rebuild: %s', exc)
        return flask.jsonify(error='Unable to queue topic tree rebuild'), 503


@bp.get('/topics/<path:topic>/tags')
def styler_topic_tags(topic):
    """Return raw tags stored under one parent in the last complete tree."""
    from .topic_taxonomy import cached_tree
    tree = cached_tree(styles.r)
    parent = next((item for item in (tree or {}).get('topics', []) if item['name'] == topic), None)
    if parent is None:
        return flask.jsonify(error='Unknown or not-yet-built topic'), 404
    tags = [tag for category in parent['categories'] for tag in category['tags']]
    return flask.jsonify(topic=topic, tags=tags)


@bp.get('/topics/<path:topic>/subtopics')
def styler_subtopics(topic):
    """Return cached categories for the selected parent topic."""
    from .topic_taxonomy import cached_tree
    tree = cached_tree(styles.r)
    parent = next((item for item in (tree or {}).get('topics', []) if item['name'] == topic), None)
    if parent is None:
        return flask.jsonify(error='Unknown or not-yet-built topic'), 404
    return flask.jsonify(topic=topic, topics=[{'name': category['name'],
                                              'description': category.get('description', '')}
                                             for category in parent['categories']])


@bp.get('/topics/<path:topic>/subtopics/<path:subtopic>/tags')
def styler_subtopic_tags(topic, subtopic):
    """Return tags stored in the selected category in the last complete tree."""
    from .topic_taxonomy import cached_tree
    tree = cached_tree(styles.r)
    parent = next((item for item in (tree or {}).get('topics', []) if item['name'] == topic), None)
    category = next((item for item in (parent or {}).get('categories', [])
                     if item['name'] == subtopic), None)
    if category is None:
        return flask.jsonify(error='Unknown or not-yet-built category'), 404
    if flask.request.headers.get('HX-Request') == 'true':
        from html import escape
        import json

        group = [f'<wa-tab-group data-category-tab-group data-base-style-prompt="{escape(flask.request.args.get("base_style_prompt", "classic"), quote=True)}" '
                 f'aria-label="Tags in {escape(subtopic, quote=True)}">']
        for tag in category['tags']:
            safe_tag = escape(tag, quote=True)
            group.append(
                f'<wa-tab data-style-category="{safe_tag}" panel="tag_{safe_tag}" '
                f'hx-put="/styles/choose_tag" hx-trigger="styler-category-load" '
                f'hx-vals="{escape(json.dumps({"tag": tag, "base_style_prompt": flask.request.args.get("base_style_prompt", "classic")}), quote=True)}" '
                f'hx-swap="none">{escape(tag)}</wa-tab>'
                f'<wa-tab-panel name="tag_{safe_tag}"><div id="tag_{safe_tag}"></div></wa-tab-panel>'
            )
        group.append('</wa-tab-group>')
        return ''.join(group)
    return flask.jsonify(topic=topic, subtopic=subtopic, tags=category['tags'])


@cache.cached(timeout=60 * 60 * 24)
def get_categories_and_counts(style_dir, default_tier):
    log.error("Obsolete get_categories_and_counts()")
    all_categories = []
    count = defaultdict(int)

    for category in styles.all_categories():
        log.info('Iterating category "%s"', category["category"])
        all_categories.append(category["category"])
        for group in styles.style_groups_in_category(category["category"]):
            log.info(
                'Iterating styles in group "%s" of category "%s"',
                group,
                category["category"],
            )
            for style in styles.styles_in_group(category["category"], group):
                tier = style.get("tier", default_tier)
                count[tier] += 1

    return all_categories, count


@bp.route("/")
def styles_base():
    from styler.backfill import stop
    stop(styles.r)
    from styler.jobs import clear_queued_samples
    cleared = clear_queued_samples(styles.r)
    log.info('Cleared %s queued style samples on page load', cleared)
    tag = request.args.get("tag", "custom")

    count = defaultdict(int)
    # Topic and child-tag navigation loads on demand; avoid rendering the old
    # flat vocabulary of tabs into every page response.
    all_tags = []
    style_tag_counts = {}
    stockpile_counts = {}
    tag_counts = {}

    fcount = {}

    # Iterate all the files in comfyui /output/samples
    for fn in glob.glob(
        os.path.join(
            const.COMFY_DIRS["artifactserver"]["OUTPUT_DIR"], "samples", "*.png"
        )
    ):
        origin = os.path.join(
            const.COMFY_DIRS["artifactserver"]["OUTPUT_DIR"],
            "samples",
            os.path.basename(fn),
        )

        # none_krea2_874246583_00001_.png
        target_fn = os.path.basename(fn).replace("_00001_", "")
        # none_krea2_874246583.png
        target_fn = "_".join(os.path.splitext(target_fn)[0].split("_")[:-1]) + ".png"
        # none_krea2.png

        target = os.path.join(
            const.LIBRARY_DIR,
            "style_exploration",
            target_fn,
            # none_krea2.png
        )
        log.info("Moving sample image to library", origin=origin, target=target)
        shutil.move(origin, target)
        break

    # log.info("groupings=%s", groupings)
    response = flask.make_response(
        render_template(
            "styles-tabgroups.html",
            active_tag=tag,
            all_tags=all_tags,
            tag_counts=tag_counts,
            style_tag_counts=style_tag_counts,
            stockpile_counts=stockpile_counts,
            # groupings=groupings,
            base_prompt=get_base_style_prompt(),
            base_style_prompt=request.args.get("base_style_prompt", DEFAULT_BASE_STYLE_PROMPT),
            base_style_prompt_options=BASE_STYLE_PROMPTS,
            count=count,
            fcount=fcount,
            lora_selector=htmx.lora_selector(),
            all_styles=styles.all_styles(),
            chosen_styles=styles.all_styles(),
            as_id=styles.as_id,
            # hide_styles=request.args.get("hide_styles", "on").lower() == "on",
            # hide_loras=request.args.get("hide_loras", "on").lower() == "on",
            # only_chosen=request.args.get("only_chosen", "on").lower() == "on",
        )
    )

    return response


# "DELETE /styles/tag?tag=subject_count_options_reformatted_50_corrected&style_tag=quad
@bp.route("/tag", methods=["DELETE"])
def delete_tag():
    tag = request.args.get("tag", None)
    style_tag = request.args.get("style_tag", None)

    if not tag or not style_tag:
        return "Missing 'tag' or 'style_tag' parameter", 400

    log.info("Deleting tag %s from style %s", tag, style_tag)

    style = styles.Style(style_tag=style_tag)
    if not style:
        return f"Style {style_tag} not found", 404

    style.remove_tag(tag)

    count = styles.r.eval("""
        local count = redis.call('SCARD', KEYS[1])
        if count == 0 then
            redis.call('SREM', 'tags', ARGV[1])
            redis.call('LREM', 'categories', 0, ARGV[1])
        end
        return count
    """, 1, tag + '/tags', tag)
    return flask.jsonify(tag=tag, count=count)


@bp.route("/style", methods=["DELETE"])
def delete_style():
    """Remove a style and all of its reverse tag memberships."""
    style_tag = request.values.get("style_tag", "").strip()
    if not style_tag:
        return "Missing 'style_tag' parameter", 400
    key = as_id(style_tag)

    styles.r.eval('''
        for _, tag in ipairs(redis.call('SMEMBERS', KEYS[1])) do
            redis.call('SREM', tag .. '/tags', ARGV[1])
            if redis.call('SCARD', tag .. '/tags') == 0 then
                redis.call('SREM', 'tags', tag)
                redis.call('LREM', 'categories', 0, tag)
            end
        end
        redis.call('DEL', KEYS[1], KEYS[2], KEYS[3], KEYS[4], KEYS[5],
                   KEYS[6], KEYS[7], KEYS[8])
        redis.call('SREM', 'LOST/tags', ARGV[1])
        redis.call('ZREM', 'LOST/index', ARGV[1])
        redis.call('SREM', 'ABYSS/tags', ARGV[1])
        if redis.call('SCARD', 'LOST/tags') == 0 then
            redis.call('SREM', 'tags', 'LOST')
            redis.call('LREM', 'categories', 0, 'LOST')
        end
        if redis.call('SCARD', 'ABYSS/tags') == 0 then
            redis.call('SREM', 'tags', 'ABYSS')
            redis.call('LREM', 'categories', 0, 'ABYSS')
        end
        return 1
    ''', 8, key + '/tags', key, 'styler:metadata:v1:' + key,
        'styler:auto-tags:' + key, 'styler:manual-tags:' + key,
        'styler:tag-suppressed:' + key, 'styler:consensus:' + key,
        'styler:auto-retag:' + key, key)
    return flask.jsonify(state='style_deleted', style_tag=key,
                         lost_count=styles.r.scard('LOST/tags'))


@bp.route("/tag/all", methods=["DELETE"])
def delete_tag_everywhere():
    tag = request.args.get("tag", "").strip()
    if not tag:
        return "Missing 'tag' parameter", 400
    if tag.casefold() in ('lost', 'abyss'):
        return f"{tag.upper()} is a reserved tag", 409
    from styler.tag_culling import delete_tag as cull_tag
    result = cull_tag(styles.r, const.LIBRARY_DIR, tag)
    return flask.jsonify(result)


@bp.route("/<image_name>", methods=["DELETE"])
def delete_sample(image_name, base_style_prompt=None):
    base_style_prompt = base_style_prompt or request.args.get('base_style_prompt', 'classic')
    sample_dir = os.path.join(const.LIBRARY_DIR, "style_exploration")
    if base_style_prompt == 'kid_friendly':
        sample_dir = os.path.join(sample_dir, 'kid_friendly')
    img_fn = os.path.join(sample_dir, image_name)
    if os.path.exists(img_fn):
        os.unlink(img_fn)

    stem = os.path.splitext(image_name)[0]
    if '_' in stem:
        style_tag, engine = stem.rsplit('_', 1)
        if engine in ('zit', 'krea2', 'qwen'):
            from styler.tag_consensus import reconcile, style_key
            from styler.jobs import sample_task, EVENTS
            if styles.r.exists('styler:consensus:' + style_key(style_tag)):
                agreement = reconcile(styles.r, style_tag, const.LIBRARY_DIR)
                event = sample_task(dict(style_tag=style_tag, engine=engine,
                                         base_style_prompt=base_style_prompt))
                event.update(state='prompt_tagged', **agreement)
                styles.r.publish(EVENTS, json.dumps(event))

    json_fn = img_fn + ".json"
    if os.path.exists(json_fn):
        os.unlink(json_fn)
    from pathlib import Path
    Path(img_fn).with_suffix('.json').unlink(missing_ok=True)
    Path(img_fn).with_suffix('.tags.json').unlink(missing_ok=True)
    return "", 200


def page_button(current_index, page_number):
    if int(current_index) == int(page_number):
        return (
            f'<wa-button appearance="accent" variant="brand">{page_number}</wa-button>'
        )
    else:
        log.info(f"{current_index=} != {page_number=}")
        return f'<wa-button href="styles_{int(page_number)}.html" appearance="outlined">{page_number}</wa-button>'


def build_pagination(all_styles, current_page, per_page):
    log.info(
        f"Building pagination for page {current_page} with {len(all_styles)} total styles and {per_page} styles per page"
    )

    pagination = ['<wa-button-group orientation="horizontal">']

    # draw the left chevron, disabled if we are on the first page.
    if int(current_page) > 1:
        pagination += [
            f'<wa-button href="styles_{int(current_page) - 1}.html" appearance="outlined">',
            '    <wa-icon name="chevron-left"></wa-icon>',
            "</wa-button>",
        ]
    else:
        # disabled
        pagination += [
            '<wa-button appearance="outlined" disabled>',
            '    <wa-icon name="chevron-left"></wa-icon>',
            "</wa-button>",
        ]

    total_pages = (len(all_styles) + per_page - 1) // per_page
    page_number = 1
    pad = 3

    prev = 1

    while page_number < total_pages:
        # current_page: the page we are generating
        # page_number: the page we are drawing a button for
        if page_number == int(current_page):
            # use accent to highlight the current page.
            pagination.append(
                f'<wa-button appearance="accent" variant="brand">{page_number}</wa-button>'
            )
            prev = page_number

        elif abs(page_number - int(current_page)) <= pad:
            pagination.append(
                f'<wa-button href="styles_{int(page_number)}.html" appearance="outlined">{page_number}</wa-button>'
            )
            prev = page_number

        elif page_number < pad:
            pagination.append(
                f'<wa-button href="styles_{int(page_number)}.html" appearance="outlined">{page_number}</wa-button>'
            )
            prev = page_number

        elif page_number > total_pages - pad:
            pagination.append(
                f'<wa-button href="styles_{int(page_number)}.html" appearance="outlined">{page_number}</wa-button>'
            )
            prev = page_number

        elif page_number + 1 > prev:
            # whenever we skip numbers, add ellipses
            pagination.append(
                """<wa-button appearance="outlined" disabled>...</wa-button>"""
            )
            prev = total_pages

        page_number += 1

    if int(current_page) < total_pages:
        pagination.append(
            f'<wa-button href="styles_{int(current_page) + 1}.html" appearance="outlined">'
        )
        pagination.append('    <wa-icon name="chevron-right"></wa-icon>')
        pagination.append("</wa-button>")
    else:
        pagination.append('<wa-button appearance="outlined" disabled>')
        pagination.append('    <wa-icon name="chevron-right"></wa-icon>')
        pagination.append("</wa-button>")

    return "\n".join(pagination)

    #  < (1) 2 3 4 ... 16 >
    # current page, +/- pad pages, first and last page, ellipses as needed.
    pad = 4

    pagination.append(page_button(current_page, 1))

    if current_page < pad:
        # show the first few pages, then ellipses, then last page.
        for page_number in range(2, min(total_pages + 1, pad + 1)):
            pagination.append(page_button(current_page, page_number))
    else:
        pagination.append(
            """<wa-button appearance="outlined" disabled>...</wa-button>"""
        )

    for page_number in range(min(total_pages + 1, pad + 1), current_page + pad):
        pagination.append(page_button(current_page, page_number))

    if int(current_page) < total_pages:
        pagination.append(
            f'<wa-button href="styles_{int(current_page) + 1}.html" appearance="outlined">'
        )
        pagination.append('    <wa-icon name="chevron-right"></wa-icon>')
        pagination.append("</wa-button>")
    else:
        pagination.append('<wa-button appearance="outlined" disabled>')
        pagination.append('    <wa-icon name="chevron-right"></wa-icon>')
        pagination.append("</wa-button>")
    pagination.append("</wa-button-group>")

    return "\n".join(pagination)
    # first the left chevron
    # show first page
    # then ellipses
    # then a few pages before the current page
    # then the current page
    # then a few pages after the current page
    # then the last page
    # then the right chevron

    for page_number in range(first_page, total_pages):
        first_page = max(1, int(page_number) - 4)
        last_page = min(int(page_number) + 4, total_pages)

        # if first_page > 1:

        pagination.append(
            """<wa-button appearance="outlined" disabled>...</wa-button>"""
        )

        if last_page < total_pages:
            pagination.append(
                """<wa-button appearance="outlined" disabled>...</wa-button>"""
            )
            pagination.append(
                f'<wa-button href="styles_{total_pages}.html" appearance="outlined">{total_pages}</wa-button>'
            )

        pagination = "\n".join(pagination)

        pagination_html = '<div class="wa-pagination">'
        if current_index > 0:
            pagination_html += f'<a href="{first_page}?page={current_index}" class="wa-button wa-button--outline"><wa-icon name="chevron-left"></wa-icon> Previous</a>'
        else:
            pagination_html += f'<span class="wa-button wa-button--outline wa-disabled"><wa-icon name="chevron-left"></wa-icon> Previous</span>'

        pagination_html += f"<span>Page {current_index + 1} of {total_pages}</span>"

        if current_index < total_pages - 1:
            pagination_html += f'<a href="{first_page}?page={current_index + 2}" class="wa-button wa-button--outline">Next <wa-icon name="chevron-right"></wa-icon></a>'
        else:
            pagination_html += f'<span class="wa-button wa-button--outline wa-disabled">Next <wa-icon name="chevron-right"></wa-icon></span>'

        pagination_html += "</div>"
        return pagination_html


@bp.route("/export_styles", methods=["POST"])
def export_styles():
    # Implement the logic for exporting styles as pages
    styles_dir = os.path.join(const.PAGES_DIR, "styles")
    os.makedirs(styles_dir, exist_ok=True)

    per_page = 100
    # by key, much easier.
    all_styles = {
        styles.as_id(d["name"]): d for d in styles.all_styles(all_means_all=True)
    }

    for image in os.listdir(os.path.join(const.LIBRARY_DIR, "style_exploration")):
        if image.endswith(".png"):
            meta_fn = os.path.join(
                const.LIBRARY_DIR, "style_exploration", image + ".json"
            )
            if os.path.exists(meta_fn):
                with open(meta_fn, "r") as f:
                    meta = json.loads(f.read())
                    style_name = meta.get("style")
                    if style_name in all_styles:
                        all_styles[style_name]["image"] = image
                        all_styles[style_name]["ranking"] = meta.get(
                            "ranking", default_tier
                        )

                        if not os.path.exists(
                            os.path.join(styles_dir, "images", image)
                        ):
                            os.makedirs(
                                os.path.join(styles_dir, "images"), exist_ok=True
                            )
                            shutil.copyfile(
                                os.path.join(
                                    const.LIBRARY_DIR, "style_exploration", image
                                ),
                                os.path.join(styles_dir, "images", image),
                            )
                    else:
                        log.warning(
                            'No style found for image %s with style name "%s"',
                            image,
                            style_name,
                        )

    log.info("Exporting %d styles to %s", len(all_styles), styles_dir)
    total_pages = (len(all_styles) + per_page - 1) // per_page

    page_index = 0
    for i in range(0, len(all_styles), per_page):
        page_index += 1

        page_styles = list(all_styles.values())[i : i + per_page]
        page_content = render_template(
            "styles_page.html",
            base_prompt=get_base_style_prompt(),
            styles=page_styles,
            per_page=per_page,
            page_index=page_index,
            total_pages=total_pages,
            total_results=len(all_styles),
            tier_to_letter=tier_to_letter,
            pagination=build_pagination(all_styles, page_index, per_page=per_page),
        )
        page_fn = os.path.join(styles_dir, f"styles_{page_index}.html")
        with open(page_fn, "w") as f:
            f.write(page_content)

    return "", 200


# http://localhost:8080/styles/choice
@bp.route("/choice", methods=["POST"])
def choose_this_style():
    style_cosmetic = request.form.get("style")
    category = request.form.get("category")
    style_tag = request.form.get("style_tag")

    if not style_cosmetic:
        return "No style name provided", 400

    style = Card.from_style_name(style_cosmetic)
    if not style:
        return f"No style found for {style_cosmetic}", 404

    # Mark the style as chosen
    styles.mark_style_as_chosen(category, style_tag, True)

    from styler.image_tags import prioritize_style_tags
    prioritize_style_tags(styles.r, style_tag, const.LIBRARY_DIR)

    favorite_styles = styles.get_favorite_styles()
    return get_favorite_control_button(style_tag, favorite_styles), 200
