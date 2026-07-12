import contextlib
import fcntl
import json
import os
import const
import re

from filelock import FileLock

import logger
import glob

log = logger.log(__name__)

# def style_name_to_key(name):
#     """
#     Convert a style name to a key suitable for use in a JSON object.
#     """
#     return (
#         name.lower()
#         .replace(" ", "_")
#         .replace("-", "_")
#         .replace("'", "")
#         .replace('"', "")
#     )

            

def enable_style(style_tag):
    """
    OBSOLETE
    copy the indicated style from styles-all.json to styles.json
    """
    with open(
        os.path.join(
            os.path.dirname(__file__), 
            "styles-all.json"
        ), "r"
    ) as f:
        all_styles = json.loads(f.read())
        style_to_enable = None
        for s in all_styles:
            if as_id(s["name"]) == style_tag:
                style_to_enable = s
                break
        if not style_to_enable:
            raise ValueError(f"Style {style_tag} not found in styles-all.json")
    
    with open(
        os.path.join(
            os.path.dirname(__file__), 
            "styles.json"
        ), "r"
    ) as f:
        current_styles = json.loads(f.read())
    
    # check if the style is already enabled
    for s in current_styles:
        if as_id(s["name"]) == style_tag:
            return
    
    current_styles.append(style_to_enable)

    with open(
        os.path.join(
            os.path.dirname(__file__), 
            "styles.json"
        ), "w"
    ) as f:
        f.write(json.dumps(current_styles, indent=4))


def all_styles():
    """
    All _custom_ styles.  The "styler" app is intended to make
    it easy to find the syles you like and add them to "Custom"
    so you have a short list of favorites to choose from.

    We need to reach into:
        ComfyUI/custom_nodes/ComfyUI_MileHighStyler/data/Custom/custom.json
    
    Which looks like:
    [{
        "name": "Wizard of Oz",
        "prompt": "Wizard of Oz mode, {prompt}, \u00acA whimsical and fantastical page illustration style used in creating illustrations for the Wizard of Oz. The style uses strong black on paper strokes and simple outlines, with watercolor fill of major characters. The style emphasizes magical wonder characters, enchanted landscapes, and the imaginative world of Oz.",
        "negative_prompt": "Dark Fantasy Art, digital design"
    }
    ...
    ]    

    Styles don't come with a good unique, and a list
    of dicts pretty much requires you to walk the 
    friggin' list.  But -- the "Custom" list is small.  
    No biggie.
    """
    custom_styles_fn = os.path.join(
        const.COMFY_DIRS["artifactserver"]["CUSTOM_NODES"],
        "ComfyUI_MileHighStyler",
        "data",
        "Custom",
        "custom.json"
    )

    lock = FileLock(custom_styles_fn + ".lock")
    with lock:
        with open(custom_styles_fn, "r") as f:
            custom_styles = json.loads(f.read())

    return custom_styles


def all_favorite_styles():
    """
    All styles in the "Custom" category.  This is what we show in the UI.
    """
    return styles_in_group("Custom", "custom")


def all_loras():
    for fn in os.listdir(const.LORA_DIR):
        # this is what passes for rocket science around here.
        if fn.endswith(".safetensors"):
            if os.path.exists(os.path.join(const.LORA_DIR, fn + ".json")):
                with open(os.path.join(const.LORA_DIR, fn + ".json"), "r") as f:
                    lora_info = json.loads(f.read())
            else:
                lora_info = {
                    "trigger_words": [],
                    "weight": 1.0
                }

            if not lora_info.get("name"):
                lora_info["name"] = os.path.splitext(fn)[0].title().replace("_", " ")

            lora_info["filename"] = fn
            yield lora_info


def get_style_old(style_name, from_all=False):
    prompt_filter = "{prompt}"
    negative_prompt = ""
    found = False
    with open(
        os.path.join(
            os.path.dirname(__file__), 
            "styles.json" if not from_all else "styles-all.json"
        ), "r"
    ) as f:
        all_styles = json.loads(f.read())
        for s in all_styles:
            if as_id(s["name"]) == style_name:
                prompt_filter = s["prompt"]
                negative_prompt = s.get("negative_prompt", "")
                found = True
                break

    if not found:
        raise ValueError(f"Style {style_name} not found ({from_all=})")
    return prompt_filter, negative_prompt


def _get_style(category_name, style_tag):
    """
    Get the style dict for the given category and style tag.
    """
    log.info("Looking for style '%s' in category '%s'", style_tag, category_name)
    if style_tag in ["none", ]:
        return None, {
            "name": "None",
            "prompt": "{prompt}",
            "negative_prompt": "",
            "tag": "none"
        }

    category_name = category_name.removeprefix("category_")
    for dirname in os.listdir(const.COMFY_DIRS["artifactserver"]["STYLES_DIR"]):
        if dirname.lower() == category_name.lower():
            category_name = dirname
            break

    for style_file in glob.glob(os.path.join(
        const.COMFY_DIRS["artifactserver"]["STYLES_DIR"],
        category_name,
        "*.json"
    )):
        
        pfn = os.path.join(
            const.COMFY_DIRS["artifactserver"]["STYLES_DIR"],
            category_name,
            style_file
        )

        log.info('Loading styles from %s', pfn)

        lock = FileLock(pfn + ".lock")
        with lock:
            with open(pfn, "r") as f:
                try:
                    styles = json.loads(f.read())
                except json.JSONDecodeError as e:
                    log.error("Error decoding JSON from %s: %s", pfn, e)
                    raise

        change = False
        for style in styles:
            if 'tag' not in style:
                # give tags to any styles that need them.
                style['tag'] = as_id(
                    style.get('cosmetic', style.get('name'))
                )
                change = True
                
            if style['tag'] == style_tag:
                if change:
                    log.info('Saving updated styles to %s', pfn)
                    lock = FileLock(pfn + ".lock")
                    with lock:
                        with open(pfn, "w") as f:
                            json.dump(styles, f, indent=4)

                return style_file, style
                
    return None, None


def get_style(category_name, style_tag):
    style_file, style = _get_style(category_name, style_tag)
    
    # this should exist:
    # os.path.join(
    #     const.COMFY_DIRS["artifactserver"]["STYLES_DIR"],
    #     category_name,
    #     style_file
    # ), "r"
    return style


def style_groups_in_category(category):
    groups = []
    category_dir = os.path.join(
        const.COMFY_DIRS["artifactserver"]["STYLES_DIR"],
        category,
    )
    for fn in os.listdir(category_dir):
        if fn.endswith(".json"):
            groups.append(os.path.splitext(fn)[0])
    return groups


def styles_in_group(category, group, **filters):
    min_tier = int(filters.get("min_tier", 0))
    max_tier = int(filters.get("max_tier", 999))

    fn = os.path.join(
        const.COMFY_DIRS["artifactserver"]["STYLES_DIR"],
        category,
        group + ".json"
    )

    all_styles = []
    if fn.endswith(".json"):
        pfn = os.path.join(
            const.COMFY_DIRS["artifactserver"]["STYLES_DIR"],
            category,
            fn
        )

        lock = FileLock(pfn + ".lock")
        with lock:
            with open(pfn, "r") as f:
                styles = json.loads(f.read())
                for s in styles:
                    s["category"] = category
                    if min_tier <= int(s.get("tier", 2)) <= max_tier: # default tier is 2 ('C' rank)
                        all_styles.append(s)
                    else:
                        log.info('Filtering out style %s due to tier %s not in [%s, %s]', s["name"], s.get("tier", 0), min_tier, max_tier)

    return all_styles


def as_id(category: str):
    c = category.strip().lower()
    c = re.sub(r"[^\-a-z0-9_]", "_", c)
    # c = c.replace(" ", "_").replace(".", "_").replace(",", "_").replace(":", "_").replace(";", "_")
    return c


category_index = None

tier_cosmetics = [
    (0, "face-sad-cry", "danger", "F"), 
    (1, "face-frown", "danger", "D"), 
    (2, "face-meh", "warning", "C"), 
    (3, "face-smile", "warning", "B"), 
    (4, "face-smile-beam", "success", "A"), 
    (5, "face-grin", "success", "S"), 
    (6, "face-grin-squint", "brand", "SS"), 
    (7, "face-grin-stars", "brand", "SSS")
]


def set_style_tier(category_name, style_tag, tier):
    style_file, style = _get_style(category_name, style_tag)
    if not style:
        log.error("Style %s not found in category %s", style_tag, category_name)
        return

    style["tier"] = tier
    fn = os.path.join(
        const.COMFY_DIRS["artifactserver"]["STYLES_DIR"],
        category_name,
        style_file
    )
    
    lock = FileLock(fn + ".lock")
    with lock:
        with open(fn, "r") as h:
            try:
                all_styles = json.loads(h.read())
            except json.JSONDecodeError as e:
                log.error("Error decoding JSON from %s: %s", fn, e)
                raise
        
        for style in all_styles:
            if style['tag'] == style_tag:
                style['tier'] = tier
                break

        with open(fn, "w") as f:
            json.dump(all_styles, f, indent=4)

    log.info("Set style %s in category %s to tier %s", style_tag, category_name, tier)


def id_to_category(tag: str):
    global category_index
    tag = as_id(tag)
    if category_index is None or tag not in category_index:
        category_index = {}

        for category in all_categories():
            if category["tag"] == tag:
                category_index[category["tag"]] = category['category']
            else:
                log.warning("Category tag mismatch: %s != %s", category["tag"], tag)

    if tag in category_index:
        return category_index[tag]
    
    log.error("Category tag not found: %s", tag)
    return None


def all_categories(): 
    categories = []
    for fn in os.listdir(
        os.path.join(
            const.COMFY_DIRS["artifactserver"]["STYLES_DIR"],
        )
    ):
        if os.path.isdir(os.path.join(
            const.COMFY_DIRS["artifactserver"]["STYLES_DIR"],
            fn,
        )):
            categories.append({
                "tag": as_id(fn),
                "category": fn,
                "cosmetic": fn.title().replace("_", " ")
            })
    return categories

# styles.mark_style_as_chosen(style_tag, category)
def mark_style_as_chosen(category_name, style_tag, chosen=True):
    style_file, style = _get_style(category_name, style_tag)
    if not style:
        log.error("Style %s not found in category %s", style_tag, category_name)
        return

    style["chosen"] = chosen
    fn = os.path.join(
        const.COMFY_DIRS["artifactserver"]["STYLES_DIR"],
        category_name,
        style_file
    )
    
    lock = FileLock(fn + ".lock")
    with lock:
        with open(fn, "r") as h:
            try:
                all_styles = json.loads(h.read())
            except json.JSONDecodeError as e:
                log.error("Error decoding JSON from %s: %s", fn, e)
                raise
        
        for s in all_styles:
            if s['tag'] == style_tag:
                s['chosen'] = chosen
                break

        with open(fn, "w") as f:
            json.dump(all_styles, f, indent=4)

    # and copy it into the "Custom" category if chosen, or remove it if not chosen
    custom_category = "Custom"
    if chosen:
        # copy the style into the Custom category
        custom_fn = os.path.join(
            const.COMFY_DIRS["artifactserver"]["STYLES_DIR"],
            custom_category,
            "custom.json"
        )
        lock = FileLock(custom_fn + ".lock")
        with lock:
            if not os.path.exists(custom_fn):
                with open(custom_fn, "w") as f:
                    json.dump([style], f, indent=4)
            else:
                with open(custom_fn, "r") as f:
                    custom_styles = json.loads(f.read())

                # check if the style is already in the custom styles
                if not any(s['tag'] == style_tag for s in custom_styles):
                    custom_styles.append(style)
                    with open(custom_fn, "w") as f:
                        json.dump(custom_styles, f, indent=4)

    else:
        # remove this style from the Custom category
        custom_fn = os.path.join(
            const.COMFY_DIRS["artifactserver"]["STYLES_DIR"],
            custom_category,
            "custom.json"
        )
        lock = FileLock(custom_fn + ".lock")
        with lock:
            if os.path.exists(custom_fn):
                with open(custom_fn, "r") as f:
                    custom_styles = json.loads(f.read())

                # filter out the selected style 
                custom_styles = [s for s in custom_styles if s['tag'] != style_tag]

                with open(custom_fn, "w") as f:
                    json.dump(custom_styles, f, indent=4)

    log.info("Marked style %s in category %s as chosen=%s", style_tag, category_name, chosen)