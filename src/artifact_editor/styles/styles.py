import contextlib
import fcntl
import json
import os
import const
import re
import redis

# redis.Redis(host="redis").rpush("gpu_tasks", json.dumps(["llm_str_prompt", prompt, system_prompt, outputfile]))

from filelock import FileLock

import logger
import glob

log = logger.log(__name__)
from artifact_editor.cache import r

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
def as_id(category: str):
    c = category.strip().lower()
    c = re.sub(r"[^\-a-z0-9_]", "_", c)
    # c = c.replace(" ", "_").replace(".", "_").replace(",", "_").replace(":", "_").replace(";", "_")
    return c


def add_tag_to_style(category_name, style_tag, new_tag):
    """
    Add a tag to a style in the given category.

    what the fuck is this?  this is shit.
    """
    key = as_id(f"{category_name}/{style_tag}")
    r.sadd(f"{key}/tags", new_tag)


def enable_style(style_tag):
    """
    OBSOLETE
    copy the indicated style from styles-all.json to styles.json
    """
    with open(os.path.join(os.path.dirname(__file__), "styles-all.json"), "r") as f:
        all_styles = json.loads(f.read())
        style_to_enable = None
        for s in all_styles:
            if as_id(s["name"]) == style_tag:
                style_to_enable = s
                break
        if not style_to_enable:
            raise ValueError(f"Style {style_tag} not found in styles-all.json")

    with open(os.path.join(os.path.dirname(__file__), "styles.json"), "r") as f:
        current_styles = json.loads(f.read())

    # check if the style is already enabled
    for s in current_styles:
        if as_id(s["name"]) == style_tag:
            return

    current_styles.append(style_to_enable)

    with open(os.path.join(os.path.dirname(__file__), "styles.json"), "w") as f:
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
    custom_styles_fn = os.path.join(const.STYLES_DIR, "Custom", "custom.json")

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
                lora_info = {"trigger_words": [], "weight": 1.0}

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
            "styles.json" if not from_all else "styles-all.json",
        ),
        "r",
    ) as f:
        all_styles = json.loads(f.read())
        for s in all_styles:
            if as_id(s["name"]) == style_name:
                prompt_filter = s["prompt"]
                negative_prompt = s.get("negative_prompt", b"")
                found = True
                break

    if not found:
        raise ValueError(f"Style {style_name} not found ({from_all=})")
    return prompt_filter, negative_prompt


VERSION = 1.20


def check_upgrade():
    version = r.get("styles_version")
    if version is None:
        log.info("No styles_version found in redis, importing all styles")

    upgrading = r.get("upgrading")
    if upgrading:
        log.info("Styles upgrade in progress, skipping check_upgrade()")
        return

    r.set("upgrading", "1")
    # set the flag to indicate upgrade in progress

    if version is None or float(version) < VERSION:
        log.info("Upgrading styles in redis from version %s to 1.1", version)

        # initial import into redis, or loaded whenever
        # redis is cleared.
        bulk_data = {}

        # wipe it clean
        r.flushdb()

        log.info("Importing styles into redis")
        # each data/ directory is a category
        categories = all_categories()
        log.info("Found %s categories", len(categories))
        for category in categories:
            if category["tag"] in ['nouns',]:
                #log.info('Skipping category "%s"', category["category"])
                continue

            # if category["tag"] not in ["era"]:
            #     # log.info('Skipping category "%s"', category["category"])
            #     continue

            log.info('Scanning category "%s"', category["category"])
            # each json file in the directory is a group
            for group in set(style_groups_in_category(category["category"])):
                log.info(
                    'Importing styles in group "%s" of category "%s"', group, category
                )
                ticker = 100
                bulk_data = {}
                for style in styles_in_group(category["category"], group):
                    style["tag"] = as_id(style.get("cosmetic", style.get("name")))
                    style["group"] = group
                    key = as_id(f"{category['category']}/{style['tag']}")

                    #if ticker % 5000 == 0:
                    if True:
                        log.info(
                            "[%s] Importing style to hset(%s)",
                            ticker,
                            key,
                            style=style,
                        )

                    bulk_data[key] = style

                    # r.hset(key, mapping=style)

                    r.sadd(key + "/tags", category["category"])
                    # log.info("category %s", category)
                    r.sadd(key + "/tags", group)
                    if "tags" in style:
                        for tag in style["tags"]:
                            r.sadd(key + "/tags", tag)

                    ticker += 1

                # massive speedup
                # log.info("Executing bulk import of %s styles into redis", len(bulk_data))
                pipe = r.pipeline(transaction=False)
                for key, style in bulk_data.items():
                    # for fast reverse lookup
                    pipe.rpush(category["category"], key)
                    pipe.rpush(group, key)
                    log.info(
                        "Saving style",
                        key=key,
                        mapping=style
                    )
                    pipe.hset(key, mapping=style)

                pipe.execute()
                # log.info("Bulk import complete")

        if len(bulk_data) == 0:
            log.info("No styles imported")

        r.set("styles_version", VERSION)

    else:
        log.info("Styles version %s found in redis, no import needed", version)

    r.delete("upgrading")  # clear the flag after upgrade is done

class Style:
    def __init__(self, category_name, style_tag):
        self.category_name = category_name
        self.style_tag = style_tag
        self.key = as_id(f"{category_name}/{style_tag}")

        self.style = {
            "name": style_tag,
            "tag": style_tag,
            "category_name": category_name,
            "group": "",
            "prompt": "{prompt}",
            "negative_prompt": "",
            "tier": "2",
            "chosen": "False",
            "cosmetic": style_tag,
        }
        self.vcard = None

        self.load_from_redis()        
        self.load_tags()

    def load_from_redis(self):
        redis_data = r.hgetall(self.key)
        for k in list(redis_data.keys()):
            v = redis_data.pop(k)
            if isinstance(k, bytes):
                k = k.decode("utf-8")
            if isinstance(v, bytes):
                v = v.decode("utf-8")
            redis_data[k] = v
        log.info('Merging in redis data', redis_data=redis_data)
        self.style.update(redis_data)

    def load_tags(self):
        try:
            self.vcard = r.scard(self.key + "/tags")
        except redis.exceptions.ResponseError:
            self.vcard = 0

        tags = []
        if self.vcard:
            vrange = r.smembers(self.key + "/tags")
            for tag_element in vrange:
                tag = (
                    tag_element.decode("utf-8")
                    if isinstance(tag_element, bytes)
                    else tag_element
                )
                tags.append(tag)
            self.style["tags"] = tags

    @property
    def name(self):
        return self.style.get("name", self.style_tag)

    @property
    def prompt(self):
        return self.style.get("prompt", "{prompt}")

    @property
    def negative_prompt(self):
        return self.style.get("negative_prompt", "")

    @classmethod
    def from_image(cls, chapter, image_xml):
        """
        Construct a Style object from an image XML element, using the chapter's
        style settings.
        """
        style_tag = chapter.get_image_style(image_xml)
        style_category = image_xml.get("style_category", "Custom")

        return cls(
            category_name=style_category,
            style_tag=style_tag
        )


# def get_style(category_name, style_tag):
#     """
#     Get the style dict for the given category and style tag.
#     """
#     log.info("Looking for style '%s' in category '%s'", style_tag, category_name)
#     check_upgrade()

#     key = as_id(f"{category_name}/{style_tag}")
#     log.info('Looking for style in redis with key "%s"', key)

#     style = {
#         "name": style_tag,
#         "tag": style_tag,
#         "category_name": category_name,
#         "group": "",
#         "prompt": "{prompt}",
#         "negative_prompt": "",
#         "tier": "2",
#         "chosen": "False",
#         "cosmetic": style_tag,
#     }
#     redis_data = r.hgetall(key)
#     if redis_data:
#         log.info("Found style %s in redis", key, redis_data=redis_data)
#     else:
#         log.warning("Style %s not found in redis, checking for upgrade", key)
#         check_upgrade()
#         redis_data = r.hgetall(key)
        
#         if not redis_data:
#             log.error("Style %s still not found in redis after upgrade", key)
#             raise ValueError(f"Style {style_tag} not found in category {category_name}")
    
#     for k in list(redis_data.keys()):
#         v = redis_data.pop(k)
#         if isinstance(k, bytes):
#             k = k.decode("utf-8")
#         if isinstance(v, bytes):
#             v = v.decode("utf-8")
#         redis_data[k] = v

#     style.update(redis_data)

#     try:
#         vcard = r.scard(key + "/tags")
#     except redis.exceptions.ResponseError:
#         vcard = 0

#     tags = []
#     if vcard:
#         vrange = r.smembers(key + "/tags")
#         for tag_element in vrange:
#             tag = (
#                 tag_element.decode("utf-8")
#                 if isinstance(tag_element, bytes)
#                 else tag_element
#             )
#             tags.append(tag)
#         style["tags"] = tags

#     if "prompt" not in style:
#         raise ValueError(f"Style {style_tag} import error: missing 'prompt'!")

#     return style

#     # # rpush("gpu_tasks", json.dumps(["llm_str_prompt", prompt, system_prompt, outputfile]))

#     #     if "tags" not in style:
#     #     style["tags"] = []

#     # log.info("Looking for style '%s' in category '%s'", style_tag, category_name)
#     # if style_tag in ["none", ]:
#     #     return (None, {
#     #         "name": "None",
#     #         "prompt": "{prompt}",
#     #         "negative_prompt": "",
#     #         "tag": "none"
#     #     })

#     # category_name = category_name.removeprefix("category_")
#     # for dirname in os.listdir(const.STYLES_DIR):
#     #     if dirname.lower() == category_name.lower():
#     #         category_name = dirname
#     #         break

#     # for style_file in glob.glob(os.path.join(
#     #     const.STYLES_DIR,
#     #     category_name,
#     #     "*.json"
#     # )):

#     #     pfn = os.path.join(
#     #         const.STYLES_DIR,
#     #         category_name,
#     #         style_file
#     #     )

#     #     log.info('Loading styles from %s', pfn)

#     #     lock = FileLock(pfn + ".lock")
#     #     with lock:
#     #         with open(pfn, "r") as f:
#     #             try:
#     #                 styles = json.loads(f.read())
#     #             except json.JSONDecodeError as e:
#     #                 log.error("Error decoding JSON from %s: %s", pfn, e)
#     #                 raise

#     #     change = False
#     #     for style in styles:
#     #         if 'tag' not in style:
#     #             # give tags to any styles that need them.
#     #             style['tag'] = as_id(
#     #                 style.get('cosmetic', style.get('name'))
#     #             )
#     #             change = True

#     #         if style['tag'] == style_tag:
#     #             if change:
#     #                 log.info('Saving updated styles to %s', pfn)
#     #                 lock = FileLock(pfn + ".lock")
#     #                 with lock:
#     #                     with open(pfn, "w") as f:
#     #                         json.dump(styles, f, indent=4)

#     #             return (style_file, style)

#     # return (None, None)


def style_groups_in_category(category_key, force=True):
    r = redis.Redis(host="redis")

    # which groups are in this category?
    # ie: which json style files were in this directory?
    key = category_key + "/groups"
    groups = None
    if not force:
        groups = r.smembers(key)
        log.info("Found %s groups in category %s", len(groups), category_key)

    if groups is None:
        log.info("[Cache Miss] Rebuilding whole directory tree to catch %s...", key)
        for category_name in os.listdir(const.STYLES_DIR):
            log.info('Scanning category "%s"', category_name)
            category_dir = os.path.join(const.STYLES_DIR, category_name)
            if not os.path.isdir(category_dir):
                log.warning("Skipping non-directory %s in styles dir", category_dir)
                continue

            for fn in os.listdir(category_dir):
                # log.debug("Considering file %s in category %s", fn, category_name)
                if fn.endswith(".json"):
                    group_name = os.path.splitext(fn)[0]
                    scanned_category_key = as_id(category_name)
                    try:
                        r.sadd(scanned_category_key + "/groups", group_name)
                    except redis.exceptions.ResponseError as e:
                        log.warning(
                            "Error adding group %s to category %s in redis: %s",
                            group_name,
                            scanned_category_key,
                            e,
                        )
                        r.delete(scanned_category_key + "/groups")
                        r.sadd(scanned_category_key + "/groups", group_name)

        # try again
        groups = r.smembers(key)
        if not groups:
            log.warning("No groups found in category %s", key)
            return []
    else:
        log.info("[Cache Hit] Found %s groups in category %s", len(groups), key)

    return [g.decode("utf-8") if isinstance(g, bytes) else g for g in groups]

    # category_dir = os.path.join(
    #     const.STYLES_DIR,
    #     category,
    # )
    # for fn in os.listdir(category_dir):
    #     if fn.endswith(".json"):
    #         groups.append(os.path.splitext(fn)[0])
    # return groups


def styles_in_group(category_key: str, group: str, **filters):
    min_tier = int(filters.get("min_tier", 0))
    max_tier = int(filters.get("max_tier", 999))

    all_styles = []
    category_dir = None
    
    for dir in os.listdir(const.STYLES_DIR):
        dir_id = as_id(dir)
        if dir_id == category_key or dir == category_key:
            category_dir = dir
            break

    if category_dir is None:
        log.error("Category '%s' not found in styles dir", category_key)
        return []

    pfn = os.path.join(
        const.STYLES_DIR,
        category_dir,
        group + ".json",
    )

    try:
        with open(pfn, "r") as f:
            styles = json.loads(f.read())
            for s in styles:
                s["category"] = category_dir
                if (
                    min_tier <= int(s.get("tier", "2")) <= max_tier
                ):  # default tier is 2 ('C' rank)
                    all_styles.append(s)
                else:
                    log.info(
                        "Filtering out style %s due to tier %s not in [%s, %s]",
                        s["name"],
                        s.get("tier", "2"),
                        min_tier,
                        max_tier,
                    )

                # one time type change
                if s.get("chosen", "False") is True:
                    s["chosen"] = "True"
                elif s.get("chosen", "False") is False:
                    s["chosen"] = "False"
    except FileNotFoundError:
        log.warning("Style group file not found: %s", pfn)
        # check_upgrade()
        raise

    return all_styles


category_index = None

tier_cosmetics = [
    (0, "face-sad-cry", "danger", "F"),
    (1, "face-frown", "danger", "D"),
    (2, "face-meh", "warning", "C"),
    (3, "face-smile", "warning", "B"),
    (4, "face-smile-beam", "success", "A"),
    (5, "face-grin", "success", "S"),
    (6, "face-grin-squint", "brand", "SS"),
    (7, "face-grin-stars", "brand", "SSS"),
]


def set_style_tier(category_name, style_tag, tier):
    style_file, style = get_style(category_name, style_tag)
    if not style:
        log.error("Style %s not found in category %s", style_tag, category_name)
        return

    style["tier"] = tier
    fn = os.path.join(const.STYLES_DIR, category_name, style_file)

    lock = FileLock(fn + ".lock")
    with lock:
        with open(fn, "r") as h:
            try:
                all_styles = json.loads(h.read())
            except json.JSONDecodeError as e:
                log.error("Error decoding JSON from %s: %s", fn, e)
                raise

        for style in all_styles:
            if style["tag"] == style_tag:
                style["tier"] = tier
                break

        with open(fn, "w") as f:
            json.dump(all_styles, f, indent=4)

    log.info("Set style %s in category %s to tier %s", style_tag, category_name, tier)


def id_to_category(style_tag: str):
    log.warning("Obsolete - this is not efficient.")

    r = redis.Redis(host="redis")
    style = None

    wildkey = f"*/{as_id(style_tag)}"
    log.info("Scanning redis for %s", wildkey)
    _, matches = r.scan(match=wildkey, count=1)
    log.info("matches=%s", matches)
    for m in matches:
        style = r.hgetall(m)

    if style:
        log.info("Found key %s", m, style=style)
        return style.get("category_name", None)
    else:
        log.error("No redis entry found", search=f"*/{style_tag}")
        check_upgrade(r)
        return None

    # global category_index
    # tag = as_id(tag)
    # if category_index is None or tag not in category_index:
    #     category_index = {}

    #     for category in all_categories():
    #         if category["tag"] == tag:
    #             category_index[category["tag"]] = category['category']
    #         else:
    #             log.warning("Category tag mismatch: %s != %s", category["tag"], tag)

    # if tag in category_index:
    #     return category_index[tag]

    # log.error("Category tag not found: %s", tag)
    # return None


def all_categories():
    categories = []
    for fn in os.listdir(
        os.path.join(
            const.STYLES_DIR,
        )
    ):
        if os.path.isdir(
            os.path.join(
                const.STYLES_DIR,
                fn,
            )
        ):
            categories.append(
                {
                    "tag": as_id(fn),
                    "category": fn,
                    "cosmetic": fn.title().replace("_", " "),
                }
            )
    return categories


# styles.mark_style_as_chosen(style_tag, category)
def mark_style_as_chosen(category_name, style_tag, chosen=True):
    style_file, style = get_style(category_name, style_tag)
    if not style:
        log.error("Style %s not found in category %s", style_tag, category_name)
        return

    style["chosen"] = "True" if chosen else "False"
    fn = os.path.join(const.STYLES_DIR, category_name, style_file)

    lock = FileLock(fn + ".lock")
    with lock:
        with open(fn, "r") as h:
            try:
                all_styles = json.loads(h.read())
            except json.JSONDecodeError as e:
                log.error("Error decoding JSON from %s: %s", fn, e)
                raise

        for s in all_styles:
            if s["tag"] == style_tag:
                s["chosen"] = "True" if chosen else "False"
                break

        with open(fn, "w") as f:
            json.dump(all_styles, f, indent=4)

    # and copy it into the "Custom" category if chosen, or remove it if not chosen
    custom_category = "Custom"
    if chosen:
        # copy the style into the Custom category
        custom_fn = os.path.join(const.STYLES_DIR, custom_category, "custom.json")
        lock = FileLock(custom_fn + ".lock")
        with lock:
            if not os.path.exists(custom_fn):
                with open(custom_fn, "w") as f:
                    json.dump([style], f, indent=4)
            else:
                with open(custom_fn, "r") as f:
                    custom_styles = json.loads(f.read())

                # check if the style is already in the custom styles
                if not any(s["tag"] == style_tag for s in custom_styles):
                    custom_styles.append(style)
                    with open(custom_fn, "w") as f:
                        json.dump(custom_styles, f, indent=4)

    else:
        # remove this style from the Custom category
        custom_fn = os.path.join(const.STYLES_DIR, custom_category, "custom.json")
        lock = FileLock(custom_fn + ".lock")
        with lock:
            if os.path.exists(custom_fn):
                with open(custom_fn, "r") as f:
                    custom_styles = json.loads(f.read())

                # filter out the selected style
                custom_styles = [s for s in custom_styles if s["tag"] != style_tag]

                with open(custom_fn, "w") as f:
                    json.dump(custom_styles, f, indent=4)

    log.info(
        "Marked style %s in category %s as chosen=%s", style_tag, category_name, chosen
    )
