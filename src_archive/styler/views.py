"""Archived definitions from src/styler/views.py.

Historical source fragments: decorators and bodies retain their original module
globals. This file is not a runtime module and must never be imported by src.
See src_archive/manifest.json for reachability evidence.
"""

CARD_DETAIL = {}


def get_groupings_count(item_count):
    # target 30 items per group, max of 11 groups.
    log.warning('Obsolete code <ST0001>')
    if item_count == 0:
        return 0

    return min(math.ceil(item_count / 30), 11)


@bp.route("/draw_all_styles", methods=["POST"])
def draw_all_styles():
    log.warning('Obsolete code <ST0005>')
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
    log.warning('Obsolete code <ST0006>')
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
    log.warning('Obsolete code <ST0007>')
    style_name = request.form.get("style")
    styles.enable_style(style_name)
    return "", 200


def tier_to_letter(tier):
    #        0    1    2    3    4    5    6    7
    log.warning('Obsolete code <ST0012>')
    if tier:
        return ["F", "D", "C", "B", "A", "S", "SS", "SSS"][tier]
    else:
        return "Unranked"


def card_detail(image_name):
    log.warning('Obsolete code <ST0002>')
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


class Grouper:
    def __init__(self, style_history, card_details, count, fcount):
        log.warning('Obsolete code <ST0003>')
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


@cache.cached(timeout=60 * 60 * 24)
def get_categories_and_counts(style_dir, default_tier):
    log.warning('Obsolete code <ST0011>')
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


def page_button(current_index, page_number):
    log.warning('Obsolete code <ST0009>')
    if int(current_index) == int(page_number):
        return (
            f'<wa-button appearance="accent" variant="brand">{page_number}</wa-button>'
        )
    else:
        log.info(f"{current_index=} != {page_number=}")
        return f'<wa-button href="styles_{int(page_number)}.html" appearance="outlined">{page_number}</wa-button>'


def build_pagination(all_styles, current_page, per_page):
    log.warning('Obsolete code <ST0010>')
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
    log.warning('Obsolete code <ST0008>')
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



# Historical commented-out history UI
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
