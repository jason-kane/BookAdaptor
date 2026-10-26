import contextlib
from html import escape
import fcntl
import json
import os

from flask import has_request_context, request, url_for
import const
import re
import redis

from filelock import FileLock

import logger
import glob

log = logger.log(__name__)
from artifact_editor.cache import r


def selected_base_style_prompt():
    if has_request_context():
        return request.values.get('base_style_prompt', 'classic')
    return 'classic'

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


def sync_lost_membership(client, style_key):
    """Keep the reserved LOST tag aligned with whether a style has any tags."""
    if client.type(style_key) not in ('hash', b'hash'):
        client.srem('LOST/tags', style_key)
        client.zrem('LOST/index', style_key)
        if client.scard('LOST/tags') == 0:
            client.srem('tags', 'LOST')
            client.lrem('categories', 0, 'LOST')
        return
    if client.sismember('ABYSS/tags', style_key):
        client.srem('LOST/tags', style_key)
        client.zrem('LOST/index', style_key)
        if client.scard('LOST/tags') == 0:
            client.srem('tags', 'LOST')
            client.lrem('categories', 0, 'LOST')
    elif client.scard(style_key + '/tags') == 0:
        client.sadd('LOST/tags', style_key)
        client.zadd('LOST/index', {style_key: 0})
        client.sadd('tags', 'LOST')
    else:
        client.srem('LOST/tags', style_key)
        client.zrem('LOST/index', style_key)
        if client.scard('LOST/tags') == 0:
            client.srem('tags', 'LOST')
            client.lrem('categories', 0, 'LOST')


def clear_abyss_membership(client, style_key):
    client.srem('ABYSS/tags', style_key)
    if client.scard('ABYSS/tags') == 0:
        client.srem('tags', 'ABYSS')
        client.lrem('categories', 0, 'ABYSS')


def sync_all_lost_memberships(client):
    """Repair LOST links for every persisted style before showing tag tabs."""
    for key in client.scan_iter(_type='HASH'):
        style_key = key.decode() if isinstance(key, bytes) else key
        fields = client.hmget(style_key, 'name', 'prompt')
        if all(value is not None for value in fields):
            sync_lost_membership(client, style_key)


def lost_styles_page(client, page, page_size=10):
    """Fetch one sorted page without loading every LOST style object."""
    total = client.scard('LOST/tags')
    index_size = client.zcard('LOST/index')
    if index_size != total:
        client.delete('LOST/index')
        cursor = 0
        while True:
            cursor, members = client.sscan('LOST/tags', cursor=cursor, count=512)
            if members:
                values = {value.decode() if isinstance(value, bytes) else value: 0
                          for value in members}
                client.zadd('LOST/index', values)
            if cursor == 0:
                break
    start = page * page_size
    page_styles = []
    previous_batch = None
    while len(page_styles) < page_size:
        keys = client.zrange('LOST/index', start + len(page_styles), start + page_size - 1)
        members = [value.decode() if isinstance(value, bytes) else value for value in keys]
        if not members or members == previous_batch:
            break
        previous_batch = members
        with client.pipeline(transaction=False) as pipe:
            for member in members:
                pipe.type(as_id(member))
            kinds = pipe.execute()
        for member, kind in zip(members, kinds):
            if kind not in ('hash', b'hash'):
                _discard_missing_lost_style(client, member)
                continue
            try:
                style = Style(member)
            except ValueError:
                # The hash may disappear between TYPE and loading the style.
                _discard_missing_lost_style(client, member)
                continue
            if not client.sismember('LOST/tags', member):
                client.zrem('LOST/index', member)
                continue
            page_styles.append(style)
    return page_styles, client.scard('LOST/tags')


def _discard_missing_lost_style(client, style_key):
    """Drop stale LOST links without failing the entire page request."""
    client.eval('''
        redis.call('SREM', KEYS[1], ARGV[1])
        redis.call('ZREM', KEYS[2], ARGV[1])
        if redis.call('SCARD', KEYS[1]) == 0 then
            redis.call('SREM', KEYS[3], 'LOST')
            redis.call('LREM', KEYS[4], 0, 'LOST')
        end
    ''', 4, 'LOST/tags', 'LOST/index', 'tags', 'categories', style_key)
    log.warning('Skipping missing LOST style %s', style_key)


def add_tag_to_style(category_name, style_tag, new_tag):
    """Add both sides of a style/tag membership, including newly created tags."""
    key = as_id(style_tag)
    with r.pipeline() as pipe:
        pipe.sadd(key + '/tags', new_tag)
        pipe.sadd(new_tag + '/tags', key)
        pipe.sadd('tags', new_tag)
        pipe.sadd('styler:manual-tags:' + key, new_tag)
        pipe.srem('styler:auto-tags:' + key, new_tag)
        pipe.srem('styler:tag-suppressed:' + key, new_tag)
        pipe.execute()
    sync_lost_membership(r, key)
    clear_abyss_membership(r, key)


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


VERSION = 1.37

def check_upgrade(category_name: str | None=None):
    version = r.get("styles_version")
    if version is None:
        log.info("No styles_version found in redis, importing all styles")

    try:
        upgrading = r.get("upgrading")
    except redis.exceptions.ResponseError as e:
        log.warning("Error checking upgrading flag in redis: %s", e)
        upgrading = None

    if upgrading:
        log.info("Styles upgrade in progress, skipping check_upgrade()")
        return

    r.set("upgrading", "1", ex=300)
    # set the flag to indicate upgrade in progress

    if version is None or float(version) < VERSION or category_name is not None:
        log.info("Upgrading styles in redis from version %s to %s", version, VERSION)

        # initial import into redis, or loaded whenever
        # redis is cleared.
        bulk_data = {}

        # wipe it clean
        r.flushdb()

        log.info("Importing styles into redis")

        for directory in os.listdir(const.STYLES_DIR):
            for fn in glob.glob(os.path.join(const.STYLES_DIR, directory, "*.json")):
                log.info("Found style file %s", fn)
                with open(fn, "r") as f:
                    try:
                        styles = json.loads(f.read())
                    except json.JSONDecodeError as e:
                        log.error("Error decoding JSON from %s: %s", fn, e)
                        raise

                    for style in styles:
                        # some reserved words
                        if style['name'] in ['tags', 'categories']:
                            continue

                        key = as_id(style['name'])

                        # add tags
                        dir_tag = os.path.basename(directory).lower()
                        json_tag = os.path.splitext(
                            os.path.basename(fn)
                        )[0].lower()

                        r.sadd(key + "/tags", dir_tag)
                        r.sadd(key + "/tags", json_tag)

                        # add tag indexes
                        r.sadd("tags", dir_tag)
                        r.sadd("tags", json_tag)

                        r.sadd(dir_tag + "/tags", key)
                        r.sadd(json_tag + "/tags", key)

                        # save the style itself
                        try:
                            r.hset(key, mapping={
                                "name": style.get("name", ""),
                                "prompt": style.get("prompt", "{prompt}"),
                                "negative_prompt": style.get("negative_prompt", ""),
                            })
                        except redis.exceptions.ResponseError as e:
                            log.error("Error saving style '%s' to redis: %s", key, e)
                            raise

                        # for tag in style["name"].split("_") + [ group ]:
                        #     r.sadd(key + "/tags", tag)
                        #     r.sadd("tags", tag)  # for reverse lookup of all tags



            # if category["tag"] not in ["era"]:
            #     # log.info('Skipping category "%s"', category["category"])
            #     continue

            #log.info('Scanning category "%s"', category["category"])
            # each json file in the directory is a group
            # for group in set(style_groups_in_category(category["category"])):
            #     log.info(
            #         'Importing styles in group "%s" of category "%s"', group, category
            #     )
            #     ticker = 100
            #     bulk_data = {}
            #     for style in styles_in_group(category["category"], group):
            #         key = as_id(f"{category['category']}/{style['name']}")

            #         #if ticker % 5000 == 0:
            #         if True:
            #             log.info(
            #                 "[%s] Importing style to hset(%s)",
            #                 ticker,
            #                 key,
            #                 style=style,
            #             )

            #         bulk_data[key] = style

            #         # style.setdefault("tags", [])
            #         # for tag in style["name"].split("_") + [ group ]:    
            #         #     if tag not in style["tags"]:
            #         #         style["tags"].append(tag)

            #         # r.hset(key, mapping=style)

            #         r.sadd(key + "/tags", category["category"])
            #         # log.info("category %s", category)
            #         r.sadd(key + "/tags", group)

            #         for tag in style["name"].split("_") + [ group ]:
            #             r.sadd(key + "/tags", tag)
            #             r.sadd("tags", tag)  # for reverse lookup of all tags

            #         ticker += 1

                # massive speedup
                # # log.info("Executing bulk import of %s styles into redis", len(bulk_data))
                # pipe = r.pipeline(transaction=False)
                # for key, style in bulk_data.items():
                #     # for fast reverse lookup
                #     # pipe.rpush(category["category"], key)
                #     # pipe.rpush(group, key)
                #     log.info(
                #         "Saving style",
                #         key=key,
                #         mapping=style
                #     )
                #     pipe.hset(key, mapping=style)

                # pipe.execute()
                # log.info("Bulk import complete")

        # if len(bulk_data) == 0:
        #     log.info("No styles imported")

        # User-created style merges are stored separately from the imported
        # catalog so a Redis rebuild does not bring merged entries back.
        from styler.style_duplicates import apply_saved_merges
        apply_saved_merges(r)
        r.set("styles_version", VERSION)

    else:
        log.info("Styles version %s found in redis, no import needed", version)

    r.delete("upgrading")  # clear the flag after upgrade is done

def resolve_style_key(client, style_tag):
    """Resolve merged style aliases while keeping saved style IDs usable."""
    key = as_id(style_tag)
    seen = set()
    while key not in seen and client.type(key) in ('hash', b'hash'):
        seen.add(key)
        target = client.hget(key, 'merged_into')
        if not target:
            break
        key = as_id(target.decode() if isinstance(target, bytes) else target)
    return key


class Style:
    def __init__(self, style_tag):
        self.key = resolve_style_key(r, style_tag)
        self.style_tag = self.key

        self.style: dict[str, str] = {
            "name": style_tag,
            "tag": style_tag,
            "prompt": "{prompt}",
            "negative_prompt": "",
            "tier": "2",
            "chosen": "False",
            "cosmetic": style_tag,
        }
        self.tag_count = 0
        self.load_from_redis()        
        self.load_tags()

    def load_from_redis(self):
        # Cache the assembled metadata across requests and Gunicorn workers.
        # Reading the source and filling the cache is atomic with invalidation
        # in save_to_redis, so an old read cannot repopulate it after an edit.
        script = '''
            local cached = redis.call('GET', KEYS[2])
            if cached then return cached end
            local fields = redis.call('HGETALL', KEYS[1])
            if #fields == 0 then return false end
            local merged = cjson.decode(ARGV[1])
            for i = 1, #fields, 2 do
                merged[fields[i]] = fields[i + 1]
            end
            local result = cjson.encode(merged)
            redis.call('SET', KEYS[2], result, 'EX', 86400)
            return result
        '''
        def read():
            return r.eval(script, 2, self.key, f'styler:metadata:v1:{self.key}',
                          json.dumps(self.style))

        cached = read()
        if not cached:
            log.warning("Style %s not found in redis, checking for upgrade", self.key)
            check_upgrade()
            cached = read()

            if not cached:
                log.error("Style %s still not found in redis after upgrade", self.key)
                raise ValueError(f"Style {self.style_tag} not found.")

        self.style = json.loads(cached)

    def save_to_redis(self):
        """
        In Redis:

        Hash Key [self.key] to a mapping of the style object with all string values.
        Set Key [self.key + "/tags"] to a set of tags for this style, the tags are strings.
        """
        log.info('Saving style to redis', key=self.key, mapping=self.style)

        # we can't include tags in the hash because
        # it is a list, unsupported unless we serialize.
        # but that is fine, we want them in a set anyway.
        if 'tags' in self.style:
            del self.style['tags']

        #r.delete(self.key + "/tags")
        #r.sadd(self.key + "/tags", *self.tags)
        #r.sadd("tags", *self.tags)
        
        with r.pipeline() as pipe:
            pipe.hset(self.key, mapping=self.style)
            pipe.delete(f'styler:metadata:v1:{self.key}')
            pipe.execute()

    def load_tags(self) -> list[str]:
        try:
            # get the number of tags for this style from Redis
            self.tag_count = r.scard(self.key + "/tags")
        except redis.exceptions.ResponseError:
            self.tag_count = 0

        self._tags = r.smembers(self.key + "/tags")
        sync_lost_membership(r, self.key)
        return self._tags
    
    @property
    def tier(self):
        return int(self.style.get("tier", "2"))

    @property
    def name(self):
        return self.style.get("name", self.style_tag)

    @property
    def prompt(self):
        return self.style.get("prompt", "{prompt}")

    @property
    def negative_prompt(self):
        return self.style.get("negative_prompt", "")

    @property
    def tags(self):
        return self.load_tags()

    def remove_tag(self, tag: str):
        """
        Remove a tag from this style.
        """
        r.sadd('styler:tag-suppressed:' + self.key, tag)
        r.srem('styler:auto-tags:' + self.key, tag)

        # remove the tag from the style
        r.srem(self.key + "/tags", tag)

        # and remove the style from the tag
        r.srem(tag + "/tags", self.key)
        sync_lost_membership(r, self.key)
        
        self.load_tags()
    
    @classmethod
    def from_image(cls, chapter, image_xml):
        """
        Construct a Style object from an image XML element, using the chapter's
        style settings.
        """
        style_tag = chapter.get_image_style(image_xml)

        return cls(
            style_tag=style_tag
        )

    def get_engine_tier(self, engine):
        return int(self.style.get(f'tier_{engine}', self.tier))

    def set_engine_tier(self, engine, tier, *, source='manual'):
        if engine not in ('zit', 'krea2', 'qwen') or not 0 <= tier <= 7:
            raise ValueError('Invalid engine tier')
        field = f'tier_{engine}'
        source_field = f'tier_source_{engine}'
        # Update only this engine, preserving ratings saved from other tabs.
        with r.pipeline() as pipe:
            pipe.hset(self.key, mapping={field: tier, source_field: source})
            pipe.delete(f'styler:metadata:v1:{self.key}')
            pipe.execute()
        self.style[field] = tier
        self.style[source_field] = source

    def engine_tier_badge(self, engine, oob=False):
        _, icon, variant, cosmetic = tier_cosmetics[self.get_engine_tier(engine)]
        return (
            f'<wa-badge id="tier_{escape(self.key, quote=True)}_{engine}" pill '
            f'data-style-tag="{escape(self.style_tag, quote=True)}" data-engine="{engine}" '
            f'class="tier-badge" variant="{variant}"'
            + (' hx-swap-oob="outerHTML"' if oob else '')
            + f'><wa-icon slot="start" name="{icon}"></wa-icon>{cosmetic}</wa-badge>'
        )

    def set_tier(self, tier: int):
        self.style["tier"] = tier
        self.save_to_redis()

        # and save the change to the file system.
        if "filename" in self.style and self.style["filename"]:
            fn = os.path.join(
                const.STYLES_DIR, 
                self.category_name, 
                self.style["filename"],
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
                    if "tag" not in s:
                        log.warning("Style in file %s missing 'tag': %s", fn, s)

                    if s.get("tag", "") == self.style_tag:
                        s["tier"] = tier
                        break

                with open(fn, "w") as f:
                    json.dump(all_styles, f, indent=4)

    def as_card(self, tag: str="", oob:bool=True):
        base_style_prompt = selected_base_style_prompt()
        # what shows up on the un-expanded card:
        style_info = f'''<div class="styler-style-heading">
            <div class="styler-style-name-view" data-style-name-view>
                <h2 class="styler-style-name" data-style-name-text>{escape(self.name)}</h2>
                <wa-button type="button" size="small" appearance="plain"
                    data-style-name-edit aria-label="Edit style name" title="Edit style name">
                    <wa-icon name="pen-to-square"></wa-icon>
                </wa-button>
            </div>
            <form class="styler-name-editor wa-cluster" data-style-name-form hidden
                hx-put="style/name" hx-swap="none">
                <input type="hidden" name="style_tag" value="{escape(self.style_tag, quote=True)}">
                <input type="text" name="name" value="{escape(self.name, quote=True)}"
                    aria-label="Style name" required maxlength="160" autocomplete="off">
                <wa-button type="submit" variant="brand" size="small">Save</wa-button>
                <span data-style-name-status role="status" aria-live="polite"></span>
            </form>
        </div>'''

        zit_image_url = url_for(
            "styles.show_sample",
            filename=f"{self.style_tag}_zit_sampler.png",
            base_style_prompt=base_style_prompt,
        )

        krea_image_url = url_for(
            "styles.show_sample",
            filename=f"{self.style_tag}_krea2_sampler.png",
            base_style_prompt=base_style_prompt,
        )
        qwen_image_url = url_for(
            "styles.show_sample",
            filename=f"{self.style_tag}_qwen_sampler.png",
            base_style_prompt=base_style_prompt,
        )
        style_tags = set(self.tags)
        def sample_attributes(engine, url):
            return (
                f'data-style-sample="{escape(self.style_tag, quote=True)}" '
                f'data-base-style-prompt="{escape(base_style_prompt, quote=True)}" '
                f'data-style-name="{escape(self.name, quote=True)}" '
                f'data-engine="{engine}" data-sample-url="{escape(url, quote=True)}"'
            )

        def sample_panel(engine, url):
            from styler.sample_metadata import character_placement as image_character_placement, image_tags
            engine_name = {'zit': 'Z-Image Turbo', 'krea2': 'Krea 2', 'qwen': 'Qwen Image 2.1'}[engine]
            placement = image_character_placement(self.get_image_path(engine))
            placement_label = {'in_image': 'Character in image', 'outside_image': 'Character outside image',
                               'not_visible': 'No character visible', 'uncertain': 'Character placement unclear'}.get(placement, '')
            tags = ''.join('<wa-tag size="s" role="button" tabindex="0" aria-pressed="'
                           + ('true' if tag in style_tags else 'false') + '"'
                           + (' variant="success"' if tag in style_tags else '')
                           + f'>{escape(tag)}</wa-tag>'
                           for tag in image_tags(self.get_image_path(engine)))
            return f'''<div class="wa-stack wa-gap-s styler-engine-cell" data-sample-panel>
                <div class="styler-engine-heading">{engine_name}</div>
                <div class="styler-character-placement" data-character-placement data-style-tag="{escape(self.style_tag, quote=True)}" data-engine="{engine}" data-base-style-prompt="{escape(base_style_prompt, quote=True)}">{escape(placement_label)}</div>
                <div class="wa-frame:square stylesample">
                    <img loading="lazy" class="image_sample" {sample_attributes(engine, url)} alt="Sample Image">
                    {self.engine_tier_badge(engine)}
                </div>
                <div class="styler-image-tag-wall" data-image-tags
                    data-style-tag="{escape(self.style_tag, quote=True)}" data-engine="{engine}"
                    data-base-style-prompt="{escape(base_style_prompt, quote=True)}"
                    aria-label="Image tags" aria-live="polite">{tags}</div>
            </div>'''

        saved_description = self.style.get('historical_description', '')
        if saved_description:
            from styler.style_description import _source
            _, current_signature = _source(self)
            if self.style.get('historical_description_signature') != current_signature:
                saved_description = ''
        description_text = escape(saved_description) if saved_description else 'Historical description will appear here.'

        return f"""
        <div 
            id="card_{self.style_tag}"
            data-style-tags="{escape(json.dumps(sorted(self.tags)), quote=True)}"
            {"hx-swap-oob='true'" if oob else ""}
            class="wa-stack card-overview history-item-{self.style_tag}"
        >
            <wa-button class="delete-style-button" size="small" appearance="plain" variant="danger"
                hx-delete="style" hx-vals='{{"style_tag": "{escape(self.style_tag, quote=True)}"}}'
                hx-target="#card_{escape(self.style_tag, quote=True)}" hx-swap="delete"
                aria-label="Delete {escape(self.name, quote=True)}" title="Delete style">
                <wa-icon name="trash"></wa-icon>
            </wa-button>
            <wa-details
                hx-get="style_details"
                hx-vals='{{"style_tag": "{self.style_tag}", "tag": "{tag}", "base_style_prompt": "{base_style_prompt}"}}'
                hx-trigger="wa-show once"
                hx-target="#style_details_{self.style_tag}"
                icon-placement="start">
                <div slot="summary">
                    {style_info}
                    <p id="style_history_{escape(self.key, quote=True)}"
                       class="styler-historical-description" data-style-history-description
                       data-style-tag="{escape(self.style_tag, quote=True)}"
                       hx-get="{escape(url_for('styles.style_description', style_tag=self.style_tag), quote=True)}"
                       hx-trigger="wa-show from:closest wa-details once" hx-target="this" hx-swap="outerHTML"
                       role="status" aria-live="polite">
                        {description_text}
                    </p>
                    <div class="wa-grid styler-engine-grid">
                        {sample_panel('zit', zit_image_url)}
                        {sample_panel('krea2', krea_image_url)}
                        {sample_panel('qwen', qwen_image_url)}
                    </div>
                </div>
                <div id="style_details_{self.style_tag}"></div>
            </wa-details>
        </div>
        """

    def __repr__(self):
        return f"<Style {self.style_tag}>"

    def get_image_path(self, sampler: str, base_style_prompt: str | None = None):
        """
        Get the path to the sample image for this style and sampler.
        """
        filename = f"{self.style_tag}_{sampler}.png"

        base_style_prompt = base_style_prompt or selected_base_style_prompt()
        sample_dir = os.path.join(const.LIBRARY_DIR, "style_exploration")
        if base_style_prompt == 'kid_friendly':
            sample_dir = os.path.join(sample_dir, 'kid_friendly')
        sample_pfn = os.path.join(sample_dir, filename)

        return sample_pfn

class StyleLibrary:
    def __init__(self):
        self.styles: dict[str, Style] = {}

    def get_ranked_tags(self, force=True, **filters):
        check_upgrade()
        none = True
        if not force:
            for element in r.lrange("categories", 0, -1):
                none = False
                if isinstance(element, bytes):
                    element = element.decode("utf-8")
                yield element

        if none:
            ranking = []
            for tag in r.smembers("tags"):
                none = False

                try:
                    tag_member_count = r.scard(tag + "/tags")
                    if tag_member_count == 0:
                        log.warning(
                            "Tag %s has no associated styles, removing from redis", tag
                        )
                        r.srem("tags", tag)

                    ranking.append((tag_member_count, tag))

                except redis.exceptions.ResponseError:
                    if not r.exists(tag + "/tags"):
                        log.warning(
                            "Tag %s has no associated styles, removing from redis", tag
                        )
                        r.srem("tags", tag)

            if none:
                # iterate every hash in redis
                all_tags = set()
                for style_key in r.scan_iter(_type="HASH"):
                    tag = style_key.decode("utf-8")
                    for e in tag.split("_"):
                        all_tags.add(e)
                        r.sadd("tags", e)
                        r.sadd(e + "/tags", tag)
                    
                for tag_bytename in r.smembers("tags"):
                    tag = tag_bytename.decode("utf-8")
                    ranking.append((r.scard(tag + "/tags"), tag))
                    log.info("Counting tags: %s", ranking[-1])

            sorted_ranking = [
                tag for _, tag in sorted(
                    ranking,
                    key=lambda x: x[0],
                    reverse=True
                )
            ]
            log.info(f"{sorted_ranking=}")

            if sorted_ranking:
                r.lpush("categories", *sorted_ranking)

            for tag in sorted_ranking:
                yield tag
            
    def get_style(self, style_tag):
        key = as_id(style_tag)
        if key in self.styles:
            return self.styles[key]
        else:
            style = Style(style_tag)
            self.styles[key] = style
            return style

    def with_tag(self, tag):
        """
        Generator for Style objects that have the given tag.
        """
        check_upgrade()
        log.info('Retrieving set "%s/tags" from redis', tag)

        members = [member.decode("utf-8") if isinstance(member, bytes) else member
                   for member in r.smembers(tag + "/tags")]

        def discard_missing(member):
            # Recheck atomically: an importer may have restored the style
            # since the membership/type snapshot was read.
            r.eval('''
                if redis.call('TYPE', KEYS[1]).ok ~= 'hash' then
                    redis.call('SREM', KEYS[2], ARGV[1])
                    redis.call('DEL', KEYS[3])
                end
            ''', 3, as_id(member), tag + '/tags',
                'styler:metadata:v1:' + as_id(member), member)
            log.warning('Skipping missing style %s in tag %s', member, tag)

        for offset in range(0, len(members), 128):
            batch = members[offset:offset + 128]
            with r.pipeline(transaction=False) as pipe:
                for member in batch:
                    pipe.type(as_id(member))
                kinds = pipe.execute()
            for member, kind in zip(batch, kinds):
                if isinstance(member, bytes):
                    member = member.decode('utf-8')
                if kind not in ('hash', b'hash'):
                    discard_missing(member)
                    continue
                try:
                    style = Style(member)
                except ValueError:
                    discard_missing(member)
                    continue
                yield style

    def delete_tag(self, style_tag):
        """
        Delete a tag from the library and redis.
        """
        key = as_id(style_tag)
        log.info("Deleting tag %s", key)

        # remove the tag from all associated styles
        for style_key in r.smembers(key + "/tags"):
            r.srem(style_key + "/tags", key)

        r.srem("tags", key)  # remove the tag from the global tags set

        # delete the tag hash
        r.delete(key)
        r.delete(f'styler:metadata:v1:{key}')

        # delete the tags set
        r.delete(key + "/tags")



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


def style_groups_in_category(category_key, force=False):
    log.info('Looking for style groups in category "%s"', category_key)
    r = redis.Redis(host="redis")

    # which groups are in this category?
    # ie: which json style files were in this directory?
    key = as_id(category_key) + "/groups"
    groups = None
    if not force:
        log.info('Gathering members of set "%s" from redis', key)
        groups = r.smembers(key)
        log.info("Found %s groups in category %s", len(groups), key)

    if groups is None or len(groups) == 0:
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
                        log.info('Adding group "%s" to set "%s/groups" in redis', group_name, scanned_category_key)
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
                s["filename"] = group + ".json"
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
    """
    Obsolete, use Style.set_tier() instead.
    """
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
