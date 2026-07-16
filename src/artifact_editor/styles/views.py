from collections import defaultdict
import math
import shutil
import glob
import os
import random
import json
from bs4 import BeautifulSoup
import shutil

import comfy
import flask

from text_to_image.registry import registry as t2i_registry

import artifact_editor.styles.styles as styles
import artifact_editor.images.images as images
import artifact_editor.chapter.chapter as chapter

from flask import (
    Blueprint,
    render_template,
    request,
    send_from_directory,
    url_for,
)

# , Book
import artifact_editor.styles.htmx as htmx
import const
import logger
from artifact_editor import (
    config,
    llm,
    tools,
)
from .styles import as_id, id_to_category

# from artifact_editor.chapter.chapter import Chapter
# from artifact_editor.chapter import htmx as chapter_htmx
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
@bp.route("/show_sample/<category_name>/<filename>")
def show_sample(category_name, filename):
    sample_pfn = os.path.join(
        os.path.join(const.LIBRARY_DIR, "style_exploration"), filename
    )
    if os.path.exists(sample_pfn):
        return send_from_directory(
            os.path.join(const.LIBRARY_DIR, "style_exploration"), filename
        )

    # generate a new sample image.
    style_tag = filename.removesuffix(".png")

    draw_sample(category_name, style_tag)
    # filename)

    return send_from_directory(
        os.path.join(const.LIBRARY_DIR, "style_exploration"), filename
    )

# a little degas
# ballet outfits do not have enough variety for this to be helpful
# base_prompt = "sublime beautiful ballet dancer practicing"

# this is amusing, and tells you how well the model handles
# potentially "mature" content.
base_prompt = "stunning woman in a flimsy, sheer lace dress.  Detailed background with something rare and exciting happening."

def draw_sample_old(filename):
    sample_pfn = os.path.join(
        os.path.join(const.LIBRARY_DIR, "style_exploration"),
        filename,
    )
    workflow = comfy.load_workflow_template("api", "t2i", "style_sample")
    
    card = Card.from_style_name(os.path.splitext(filename)[0])
    if card is None:
        log.warning("No card found for %s, cannot draw sample", filename)
        return
    
    template_environment = {
        "PROMPT": card.prompt.format(prompt=base_prompt),
        "FILENAME_PREFIX": os.path.join("samples", filename),
    }

    comfy.run_workflow(
        workflow,
        template_environment=template_environment
    )

    # make sure the output directory exists
    os.makedirs(os.path.dirname(sample_pfn), exist_ok=True)

    # move the image into it
    for fn in glob.glob(
        os.path.join(
            const.COMFY_DIRS["artifactserver"]["OUTPUT_DIR"],
            "samples", 
            filename + "*"
        )
    ):
        shutil.move(
            os.path.join(
                const.COMFY_DIRS["artifactserver"]["OUTPUT_DIR"],
                "samples",
                fn
            ),
            sample_pfn
        )
        break

    return card



def draw_sample(category_name, style_tag):
    sample_pfn = os.path.join(
        os.path.join(const.LIBRARY_DIR, "style_exploration"),
        f"{style_tag}.png",
    )
    workflow = comfy.load_workflow_template("api", "t2i", "style_sample")
    
    style = styles.get_style(category_name=category_name, style_tag=style_tag)
    template_environment = {
        "PROMPT": style["prompt"].format(prompt=base_prompt),
        "FILENAME_PREFIX": os.path.join("samples", f"{style_tag}.png"),
    }

    comfy.run_workflow(
        workflow,
        template_environment=template_environment
    )

    # make sure the output directory exists
    os.makedirs(os.path.dirname(sample_pfn), exist_ok=True)

    # move the image into it
    for fn in glob.glob(
        os.path.join(
            const.COMFY_DIRS["artifactserver"]["OUTPUT_DIR"],
            "samples", 
            style_tag + "*"
        )
    ):
        shutil.move(
            os.path.join(
                const.COMFY_DIRS["artifactserver"]["OUTPUT_DIR"],
                "samples",
                fn
            ),
            sample_pfn
        )
        break


@bp.route("/regenerate", methods=["PUT"])
def regenerate_style_image():
    """
    Replace the image for a style with a new one, using the same prompt
    and a random seed.
    """
    category_name = request.form.get("category")
    style_tag = request.form.get("style_tag")

    delete_sample(f"{style_tag}.png")
    draw_sample(category_name, style_tag)

    return style_details()



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


@bp.route("/style_details")
def style_details():
    log.info("style_details()")

    style_tag = request.form.get("style_tag") or request.args.get("style_tag")
    category_name = request.form.get("category") or request.args.get("category")

    favorite_styles = styles.all_favorite_styles()
    style = styles.get_style(
        category_name=category_name,
        style_tag=style_tag
    )

    if style['name'] in favorite_styles:
        # "Choosing" is critical.  It is what determines which of the
        # thousand + styles we want to shortlist.  Everywhere else in
        # the UI we only show these styles.
        card_tag = "favorite"
        favorite_control_button = f"""<wa-button
            hx-delete="choice"
            hx-vals="js:{{style: '{style['name']}'}}"
            variant="warning"
            pill
        >Ignore</wa-button>"""
    else:
        favorite_control_button = f"""<wa-button
            hx-post="choice"
            hx-vals="js:{{style: '{style['name']}'}}"
            variant="success"
            pill
        >Favorite</wa-button>"""
    
    tier_buttons = "<wa-button-group label='Tier'>"
    tooltips = []
    for tier, icon, variant, cosmetic in styles.tier_cosmetics:
            tier_buttons += f"""<wa-button
                hx-post="{category_name}/{style['tag']}/tier"
                hx-vals="js:{{tier: {tier}}}"
                variant="{variant}"
                size="s"
                id="tier_button_{tier}"
                pill"""
            
            if int(style.get("tier", "2")) == tier:
                tier_buttons += f""" class="active_tier" """
            
            tier_buttons += f"""
            ><wa-icon name="{icon}" label="{cosmetic}"></wa-icon></wa-button>"""
            tooltips.append(f"<wa-tooltip for='tier_button_{tier}'>{cosmetic} Tier</wa-tooltip>")

    tier_buttons += "</wa-button-group>"
    tier_buttons += "".join(tooltips)
    
    out = ""
    prefix = ""

    style_prompt = style['prompt']
    style_negative_prompt = style['negative_prompt']
    
    prompt_prefix, prompt_postfix = style_prompt.split("{prompt}")

    clean_prompt = style["prompt"]
    clean_prompt = clean_prompt.removeprefix(prompt_prefix).removesuffix(prompt_postfix)

    # the tax of using this style prompt.
    token_price = (len(prompt_prefix) + len(prompt_postfix)) * CHARS_TO_TOKENS
    prompt_price = len(base_prompt) * CHARS_TO_TOKENS

    out += f"""
    <div class="wa-stack wa-align-items-center">
        {favorite_control_button}
        <div class="wa-cluster">
            {tier_buttons}
        </div>
    </div>
    """
    out += f"""<hr/><h4>Tokens</h4>
        <p>zImage Max: 512, best adherance < 75</p>
        <div class="label-on-left">
            <wa-input label="Style" value="{ token_price }" disabled></wa-input>
            <wa-input label="Prompt" value="{ prompt_price }" disabled></wa-input>
            <wa-input label="TOTAL" value="{ token_price + prompt_price } tokens" disabled></wa-input>
        </div>
    """

    out += f"""
        <p><wa-textarea label="Prefix" value="{prompt_prefix}"></wa-textarea></p>
        <p><wa-textarea label="Prompt" placeholder="{base_prompt}" disabled></wa-textarea></p>
        <p><wa-textarea label="Postfix" value="{prompt_postfix}"></wa-textarea></p>
    """

    out += f"""                    
        <wa-button hx-put="regenerate" variant="brand" pill>Regenerate</wa-button>
        <wa-button 
            href="regenerate_ui?category={category_name}&style_tag={style_tag}"
            hx-get="regenerate_ui?category={category_name}&style_tag={style_tag}"
            target="_blank"
            variant="brand" 
            appearance="accent"
            pill>
            <wa-icon src="/static/images/comfyui.svg"></wa-icon>
        </wa-button>
        <wa-button hx-delete="delete" variant="danger" pill>Delete Style</wa-button>
    """
    # </wa-details>
    # </wa-card>
    # I like these categories, because I think they can speak well to
    # intent and divides the space reasonably well, albiet not always
    # predictably.
    # soup = BeautifulSoup(out, "html.parser")
    return (
        f"""
    <div hx-swap-oob="true" id="style_details_{style_tag}">
        {out}
    </div>
    """,
        200,
    )


# http://localhost:8080/styles/regenerate_ui?category=verbing&style_tag=appraising
@bp.route("/regenerate_ui", methods=["GET"])
def regenerate_ui():
    """
    Create the ComfyUI workflow for regenerating a style image, then redirect to it.
    """
    category_name = request.args.get("category")
    style_tag = request.args.get("style_tag")

    style = styles.get_style(
        category_name=category_name,
        style_tag=style_tag
    )
    
    # api.t2i.style_sample.json
    c = chapter.Chapter(
        author=None,
        title=None,
        number=0,
        language=None,
    )

    image_xml = BeautifulSoup("<book><paragraph><image></image></paragraph></book>", "xml").find("image")
    image_xml.attrs = {
        "index": 0,
        "prompt": base_prompt,
        "seed": str(random.randint(0, 999999999)),
        "style": style_tag,
    }

    workflow = c.get_comfy_workflow(
        image_xml=image_xml,
        interface="ui",
        mode="t2i",
        workflow_template="style_sample",
        template_environment={
            "PROMPT": style["prompt"].format(prompt=base_prompt),
            "FILENAME_PREFIX": os.path.join("samples", f"{style_tag}.png"),
        }
    )

    # this is the pisser that will show up in your workflow list.
    workflow_name = f"style_{style_tag}_sample"
    workflow_fn = os.path.join(const.COMFY_DIRS["comfyui"]["WORKFLOWS_DIR"], workflow_name + ".json")
    with open(workflow_fn, "w") as h:
        json.dump(workflow, h)

    # 2. Redirect to the workflow page for that workflow in comfyui.
    workflow_url = const.COMFY_DIRS["comfyui"]["UI_URL"] + f"?workflow={workflow_name}.json"

    return flask.redirect(workflow_url, code=302)


# http://localhost:8080/style/renaissance__1300___1600_/tier
@bp.route("<category_name>/<style_tag>/tier", methods=["POST"])
def set_style_tier(category_name, style_tag):
    tier = int(request.form.get("tier", 2))

    log.info("Setting style %s in category %s to tier %s", style_tag, category_name, tier)

    style_file, _ = styles._get_style(category_name, style_tag)
    style_pfn = os.path.join(
        const.STYLES_DIR,
        category_name,
        style_file
    )

    with open(style_pfn, "r") as f:
        json_styles = json.loads(f.read())
    
    for s in json_styles:
        if s['tag'] == style_tag:
            s['tier'] = tier
            break
    
    with open(style_pfn, "w") as f:
        json.dump(json_styles, f, indent=4)
        
    return "", 200


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
    def __init__(self, style_name, category_name, **meta):
        self.style_name = style_name
    
        if category_name is None:
            self.category_name = self.find_category(style_name)
        else:
            self.category_name = category_name

        self.tag = as_id(style_name)
        if "tier" in meta:
            self.tier = int(meta["tier"])
        else:
            log.warning('No tier found for style %s, defaulting to %s', style_name, default_tier)
            self.tier = default_tier

        self.meta = meta

    @staticmethod
    def find_group(style_name):
        """
        Given a style name, find the first group containing that style.
        """
        for category in styles.all_categories():
            for group in styles.style_groups_in_category(category["category"]):
                for style in styles.styles_in_group(category["category"], group):
                    if style["name"] == style_name:
                        return group

    @staticmethod
    def find_category(style_name):
        """
        Given a style name, find the first group containing that style.
        """
        for category in styles.all_categories():
            for group in styles.style_groups_in_category(category["category"]):
                for style in styles.styles_in_group(category["category"], group):
                    if style["name"] == style_name:
                        return category["category"]

    @classmethod
    def from_style_name(cls, style_name):
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
                log.info(f"Found category {category_name} for style {style_name} in styles.json")
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
        return self.meta.get("prompt", "")

    def set_meta(self, key, value):
        self.meta[key] = value

        with open(self.image_metadata_filename, "w") as f:
            json.dump(self.meta, f, indent=2)

    def as_html(self):
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

        return f"""
        <wa-card 
            id="card_{self.tag}"
            class="card-overview history-item-{self.tag}"
        >
            <img
                loading="lazy"
                class="image_sample"
                slot="media"
                src="{url_for('styles.show_sample', category_name=self.category_name, filename=self.style_name + '.png')}"
                alt="Sample Image"
            >
                {badge}
            </img>
            <wa-details
                hx-get="style_details"
                hx-vals='{{"category": "{self.category_name}", "style_tag": "{self.tag}"}}'
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


@bp.route("/choose_category", methods=["PUT"])
def choose_category():
    # TODO: support more filters
    log.info(f"choose_category with args: {request.args}")
    log.info(f"choose_category with form: {request.form}")
    min_rating, max_rating = request.form.getlist("rating_filter")

    filters = {
        "min_tier": min_rating,
        "max_tier": max_rating,
    }

    # category is actually the directory name containing json files
    # which in turn are lists of dictionaries, each of which is a style
    # definition.
    category = id_to_category(request.form.get("group", None).removeprefix("category_"))
    if category is None:
        log.error("Invalid category provided: %s", request.form.get("group", None))
        return "Invalid category", 400
    
    log.info("Delivering category %s with filters %s", category, filters)

    response = (
        f"""<div class="wa-stack" hx-swap-oob="true" id="group_{ as_id(category) }">"""
    )
    # TODO: apply filters
    for group in styles.style_groups_in_category(category):
        response += f"""<h3>{ group }</h3>"""
        all_styles = styles.styles_in_group(category, group, **filters)

        #response += "<div class='wa-grid' style='grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));'>"
        response += "<div class='wa-cluster wa-align-items-start wa-gap-3xs'>"
        for style_index, style in enumerate(all_styles):
            log.info("style: %s", style)

            response += Card(
                style_name=style.get("cosmetic", style.get("name")),
                category_name=category,
                prompt=style.get("prompt", ""),
                name=style.get("name", ""),
                negative_prompt=style.get("negative_prompt", ""),
                category=category,
                tier=style.get("tier", default_tier)
            ).as_html()
        response += "</div>"

    response += """</div>"""
    return response, 200




# @bp.route("/grouping", methods=["PUT"])
# def grouping():
#     group = request.form.get("group", None)

#     if group == "None":
#         group = None

#     log.info("Delivering grouping for group=%s", group)
#     # in theory this will be mostly cached
#     min_tier = request.form.get("min_tier", 2)  # default to C or better
#     max_tier = request.form.get("max_tier", 7)  # SSS or worse

#     filters = {
#         "min_tier": min_tier,
#         "max_tier": max_tier,
#         "hide_styles": request.form.get("hide_styles", "off").lower() == "on",
#         "hide_loras": request.form.get("hide_loras", "off").lower() == "on",
#         "only_chosen": request.form.get("only_chosen", "off").lower() == "on",
#         "constraint_toggles": json.loads(request.form.get("constraints", "[]")),
#         "chosen_styles": styles.all_styles(),
#     }
#     log.info("Filters for grouping: %s", filters)
#     style_history, card_details, count, fcount = history_cards(**filters)
#     log.info(
#         "len(card_details)=%s, len(style_history)=%s",
#         len(card_details),
#         len(style_history),
#     )

#     grouper = Grouper(style_history, card_details, count, fcount)
#     log.info(f"{fcount=}")
#     total_filtered_results = sum([fcount[g] for g in fcount])
#     if total_filtered_results:
#         groupings_count = get_groupings_count(total_filtered_results)
#         groupings = grouper.groupings(groupings_count)
#     else:
#         groupings_count = 0
#         groupings = []

#     log.info("groupings_count=%s", groupings_count)
#     log.info("groupings=%s", groupings)

#     if not group:
#         if groupings:
#             group = "group_0"
#         else:
#             # _no_ results with these filters.  Reset to a clean slate.
#             return (
#                 """
#                 <div hx-swap-oob="true" id="groupings">
#                     <wa-tab-group
#                         id="grouping-tag-group"
#                         hx-put="grouping"
#                         hx-swap="none"
#                     >
#                     </wa-tab-group>
#                 </div>
#             """,
#                 200,
#             )

#     log.info("invoking grouper.indexof(%r, %r)", groupings_count, group)
#     category_index = grouper.indexof(groupings_count, group)
#     log.info("group %s => category_index=%r", group, category_index)

#     # update the "group_{category_index}" part by including all the cards
#     group_panel_list = []
#     for card in grouper.members(groupings_count, category_index=category_index):
#         log.debug("Checking card %s : group %s", card, group)
#         as_html = card.as_html()
#         if as_html:
#             group_panel_list.append(as_html)
#     group_panel = (
#         f'<div class="wa-cluster" id="group_{category_index}">'
#         + "\n".join(group_panel_list)
#         + "</div>"
#     )

#     # update the tab group to show the groups for this filtering.
#     grouping_html = [
#         '<div id="groupings" hx-swap-oob="true">',
#         "<wa-tab-group",
#         'id="grouping-tag-group"',
#         f'active="{ group }"' if group else "",
#         ">",
#     ]

#     for group_dict in groupings:
#         grouping_html.append(f"""
#             <wa-tab 
#                 hx-put="grouping"
#                 hx-trigger="click"
#                 hx-vals='{{"group": "group_{group_dict["tag"]}"}}'
#                 hx-swap="none"
#                 panel="group_{as_id(group_dict["tag"])}">{group_dict["cosmetic"]}</wa-tab>""")

#     for group_dict in groupings:
#         if int(group_dict["tag"]) == category_index:
#             grouping_html.append(group_panel)
#         else:
#             log.debug(
#                 f'{group_dict["tag"]} != {category_index}, delivering empty panel'
#             )
#             grouping_html += [
#                 f'<wa-tab-panel name="group_{as_id(group_dict["tag"])}">',
#                 f'<div id="group_{as_id(group_dict["tag"])}" class="skeleton-paragraphs">',
#                 '<wa-skeleton class="square" count="2"></wa-skeleton>',
#                 "</div>",
#                 "</wa-tab-panel>",
#             ]
#     grouping_html.append("</div>")

#     out = "\n".join(grouping_html)
#     response = flask.make_response(out, 200)
#     response.headers["HX-Push-Url"] = url_for(
#         "styles.styles_base",
#         **{
#             "max_tier": max_tier,
#             "min_tier": min_tier,
#             "hide_styles": request.form.get("hide_styles", "off").lower() == "on",
#             "hide_loras": request.form.get("hide_loras", "off").lower() == "on",
#             "only_chosen": request.form.get("only_chosen", "off").lower() == "on",
#             "group": group,
#         },
#     )
#     return response


@cache.cached(timeout=60 * 60 * 24)
def get_categories_and_counts(style_dir, default_tier):
    all_categories = []
    count = defaultdict(int)

    for category in styles.all_categories():
        all_categories.append(category["category"])
        for group in styles.style_groups_in_category(category["category"]):
            for style in styles.styles_in_group(category["category"], group):
                tier = style.get("tier", default_tier)
                count[tier] += 1

    return all_categories, count


@bp.route("/")
def styles_base():
    min_tier = request.args.get("min_tier", 2)  # default to C or better
    max_tier = request.args.get("max_tier", 7)  # SSS or worse
    group = request.args.get("group", None)

    filters = {
        "min_tier": min_tier,
        "max_tier": max_tier,
        "hide_styles": request.args.get("hide_styles", "on").lower() == "on",
        "hide_loras": request.args.get("hide_loras", "on").lower() == "on",
        "only_chosen": request.args.get("only_chosen", "on").lower() == "on",
        "constraint_toggles": json.loads(request.args.get("constraints", "[]")),
        "chosen_styles": styles.all_styles(),
    }

    all_categories = []
    style_dir = const.STYLES_DIR
    all_categories, count = get_categories_and_counts(style_dir, default_tier)                          

    # why isn't the value from get_categories_and_counts() being used?  
    # Because it is wrong.
    all_categories = styles.all_categories()
    category = None

    fcount = {}

    # log.info("groupings=%s", groupings)
    response = flask.make_response(
        render_template(
            "styles-tabgroups.html",
            active_grouping=group,
            active_category=category,
            all_categories=all_categories,
            #groupings=groupings,
            base_prompt=base_prompt,
            count=count,
            fcount=fcount,
            lora_selector=htmx.lora_selector(),
            all_styles=styles.all_styles(),
            chosen_styles=styles.all_styles(),
            as_id=styles.as_id,
            min_tier=min_tier,
            max_tier=max_tier,
            # hide_styles=request.args.get("hide_styles", "on").lower() == "on",
            # hide_loras=request.args.get("hide_loras", "on").lower() == "on",
            # only_chosen=request.args.get("only_chosen", "on").lower() == "on",
        )
    )

    return response


@bp.route("/<image_name>", methods=["DELETE"])
def delete_sample(image_name):
    img_fn = os.path.join(const.LIBRARY_DIR, "style_exploration", image_name)
    if os.path.exists(img_fn):
        os.unlink(img_fn)

    json_fn = img_fn + ".json"
    if os.path.exists(json_fn):
        os.unlink(json_fn)
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
        styles.as_id(d["name"]): d
        for d in styles.all_styles(all_means_all=True)
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
            base_prompt=base_prompt,
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

    #return redirect(url_for("styles.styles_base"), code=302)
    return "", 200