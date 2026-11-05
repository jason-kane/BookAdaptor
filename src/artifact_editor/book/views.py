import os
import re
import json
from html import escape
from artifact_editor import loras

from flask import (
    Blueprint,
    redirect,
    render_template,
    request,
    url_for,
)

import styler.htmx as styles_htmx
import const
import logger
import roman
from artifact_editor import (
    config,
    tools,
)
from artifact_editor.author.author import Author
from artifact_editor.chapter import htmx as chapter_htmx
from artifact_editor.chapter.chapter import Chapter
from artifact_editor.characters import characters as character_store
from artifact_editor.characters import htmx as character_htmx

from . import htmx
from .book import Book

log = logger.log(__name__)

bp = Blueprint(
    "book",
    __name__,
    template_folder=os.path.join(os.path.dirname(__file__), "templates"),
)


@bp.route("/", methods=["PUT"])
def set_book_metadata(author, title):
    author = Author(author)
    book = Book(author, title)

    if not book.bookdir:
        return render_template("404.html"), 404

    if 'default_image_template' in request.form:
        from artifact_editor.textoviva.workflow import image_engine_choices
        if request.form['default_image_template'] not in dict(image_engine_choices()):
            return "Invalid image generation engine", 400

    if "default_loras" in request.form:
        try:
            selected = loras.normalize(json.loads(request.form["default_loras"]))
            available = {item["filename"] for item in loras.catalog()}
            if any(value not in available for value in selected):
                raise ValueError("Unknown LoRA")
        except (ValueError, TypeError):
            return "Invalid LoRA selection", 400
        book.config["default_loras"] = selected

    for name, value in request.form.items():
        if name == "default_loras":
            continue
        match name:
            case "style":
                log.info('Setting book config "default_style" to "%s"', value)
                book.config["default_style"] = value
            case _:
                log.info('Setting book config "%s" to "%s"', name, value)
                book.config[name] = value

    book.save_config()

    book_metadata = get_book_metadata(book)
    return book_metadata


@bp.route("/loras/new", methods=["GET", "POST"])
def lora_creation_wizard(author, title):
    from artifact_editor.book import lora_wizard
    from artifact_editor.book.lora_training import training_progress, training_steps

    author_obj = Author(author)
    book = Book(author_obj, title)
    if not book.bookdir:
        return render_template("404.html"), 404
    character_catalog = character_store.get_all_characters(book, is_global=True)
    draft_id = request.args.get("draft", "")
    draft = None
    error = None
    try:
        if draft_id:
            draft = lora_wizard.load(book, draft_id)
        step = int(request.args.get("step", draft.get("step", 1) if draft else 1))
        if step not in range(1, 5) or (not draft and step != 1) or (draft and step > draft['step']):
            return "Complete the previous wizard steps first.", 400
    except (ValueError, FileNotFoundError):
        return "LoRA draft not found.", 404
    if request.method == "POST":
        try:
            from artifact_editor.book import lora_training
            if request.form.get('action') == 'save_source_selection':
                if not draft or step != 2:
                    return "Source image selection is unavailable.", 400
                with lora_training.locked(book, draft_id):
                    draft = lora_wizard.load(book, draft_id)
                    if draft['status'] in (*lora_training.ACTIVE, 'complete'):
                        return "This dataset is locked because training has been submitted.", 409
                    lora_wizard.save_source_selection(book, draft, request.form, character_catalog)
                return {"saved": True}
            if draft and draft['status'] in (*lora_training.ACTIVE, 'complete') and step != 4:
                return "This dataset is locked because training has been submitted.", 409
            if step == 4:
                draft = lora_training.enqueue(book, draft_id)
                from gpu_service import queue_manual_switch
                import os
                import redis
                client = redis.Redis(host=os.environ.get("REDIS_HOST", "redis"), decode_responses=True)
                try:
                    queue_manual_switch(client, "training")
                finally:
                    client.close()
                return redirect(url_for("library.book.lora_creation_wizard", author=author,
                                        title=title, draft=draft['id'], step=4))
            if draft is None:
                draft = lora_wizard.create(book, request.form)
            else:
                with lora_training.locked(book, draft_id):
                    draft = lora_wizard.load(book, draft_id)
                    if step == 3 and request.form.get('remove_image'):
                        if draft['status'] in (*lora_training.ACTIVE, 'complete'):
                            return "This dataset is locked because training has been submitted.", 409
                        draft = lora_wizard.remove_image(book, draft, request.form['remove_image'],
                                                        request.form, character_catalog)
                        return redirect(url_for("library.book.lora_creation_wizard", author=author,
                                                title=title, draft=draft['id'], step=3))
                    if (draft['status'] in (*lora_training.ACTIVE, 'complete')
                            or draft.get('caption_state') in ('queued', 'running')):
                        return "This dataset is locked while captions or training are being generated.", 409
                    draft = lora_wizard.update(book, draft, step, request.form, character_catalog)
            if step == 2:
                from artifact_editor.book import lora_captioning
                lora_captioning.enqueue(book, draft['id'])
            return redirect(url_for("library.book.lora_creation_wizard", author=author,
                                    title=title, draft=draft['id'], step=draft['step']))
        except ValueError as exc:
            error = str(exc)
    from artifact_editor.images.source import source_images
    references = source_images(book) if step == 2 else []
    return render_template("lora_creation_wizard.html", book=book, draft=draft,
                           training_progress=training_progress(lora_wizard.directory(book, draft['id'])) if draft and draft.get('status') == 'training' else {},
                           training_steps=training_steps(len(draft['images'])) if draft else 0,
                           uncaptioned_count=sum(lora_wizard.needs_caption(item, draft['trigger']) for item in draft['images']) if draft else 0,
                           source_images=references, character_catalog=character_catalog,
                           source_characters={item.get('source_path'): item.get('character_tags', []) for item in (draft or {}).get('images', [])},
                           caption_text=lambda item: lora_wizard.caption_text(item, (draft or {}).get('trigger', '')),
                           selected_sources={item.get('source_path') for item in (draft or {}).get('images', [])},
                           author=author_obj, title=title, section="book", step=step,
                           steps=lora_wizard.STEPS, error=error,
                           values=request.form if request.method == "POST" and step == 1 else draft or {})


@bp.route("/loras/drafts/<draft_id>/images/<image_id>", methods=["PATCH"])
def autosave_lora_image(author, title, draft_id, image_id):
    from flask import jsonify
    from artifact_editor.book import lora_wizard, lora_training
    book = Book(Author(author), title)
    if not book.bookdir:
        return jsonify(error="Book not found."), 404
    changes = request.get_json(silent=True)
    if not isinstance(changes, dict) or not changes or set(changes) - {'caption', 'character_tags'}:
        return jsonify(error="Invalid image edit."), 400
    try:
        with lora_training.locked(book, draft_id):
            draft = lora_wizard.load(book, draft_id)
            if (draft['status'] in (*lora_training.ACTIVE, 'complete')
                    or draft.get('caption_state') in ('queued', 'running')):
                return jsonify(error="Image editing is locked while captioning or training is running."), 409
            catalog = character_store.get_all_characters(book, is_global=True)
            item = lora_wizard.save_image_edit(book, draft, image_id, changes, catalog)
        return jsonify(saved=True, image_id=image_id, caption=lora_wizard.caption_text(item))
    except FileNotFoundError:
        return jsonify(error="Image draft not found."), 404
    except ValueError as exc:
        return jsonify(error=str(exc)), 400


@bp.route("/loras/drafts/<draft_id>/images/<image_id>/caption", methods=["GET", "POST"])
def generate_one_lora_caption(author, title, draft_id, image_id):
    from flask import jsonify
    from artifact_editor.book import lora_wizard, lora_captioning
    book = Book(Author(author), title)
    if not book.bookdir:
        return jsonify(error="Book not found."), 404
    try:
        draft = (lora_captioning.enqueue(book, draft_id, image_id=image_id)
                 if request.method == "POST" else lora_wizard.load(book, draft_id))
        item = next((item for item in draft['images'] if item['id'] == image_id), None)
        if item is None:
            return jsonify(error="Image not found."), 404
        return jsonify(state=draft.get('caption_state', 'draft'),
                       ready=bool(item.get('caption_generated')), caption=item['caption'],
                       error=draft.get('caption_error'))
    except (ValueError, FileNotFoundError) as exc:
        return jsonify(error=str(exc)), 400


@bp.route("/loras/drafts/<draft_id>/captions", methods=["POST"])
def generate_lora_captions(author, title, draft_id):
    from artifact_editor.book import lora_captioning
    book = Book(Author(author), title)
    if not book.bookdir:
        return render_template("404.html"), 404
    try:
        replace_all = request.form.get('replace_all') == 'true'
        lora_captioning.enqueue(book, draft_id, replace_all=replace_all)
    except (ValueError, FileNotFoundError) as exc:
        return str(exc), 400
    return redirect(url_for("library.book.lora_creation_wizard", author=author,
                            title=title, draft=draft_id, step=3))


@bp.route("/loras/characters", methods=["POST"])
def add_lora_character(author, title):
    from flask import jsonify
    book = Book(Author(author), title)
    if not book.bookdir:
        return jsonify(error="Book not found."), 404
    name = request.form.get('name', '').strip()
    if not name or len(name) > 160:
        return jsonify(error="Enter a character name of 1–160 characters."), 400
    tag = character_store.name_to_tag(name)
    if not tag:
        return jsonify(error="Enter a character name containing letters or numbers."), 400
    catalog = character_store.get_all_characters(book, is_global=True)
    for existing_tag, character in catalog.items():
        if existing_tag == tag or character.get('name', '').casefold() == name.casefold():
            return jsonify(tag=existing_tag, name=character.get('name', name))
    catalog[tag] = {'name': name, 'tag': tag, 'description': '',
                    'tts_engine': 'kokoro', 'voices': [], 'alias': [], 'is_global': True}
    character_store.save_characters(book, catalog, is_global=True)
    return jsonify(tag=tag, name=name), 201


@bp.route("/loras/source")
def lora_book_source_image(author, title):
    from artifact_editor.images.source import source_image_path
    from flask import send_file
    book = Book(Author(author), title)
    if not book.bookdir:
        return render_template("404.html"), 404
    try:
        path = source_image_path(book, request.args.get('file', ''))
    except (ValueError, FileNotFoundError):
        return "Source image not found.", 404
    return send_file(path)


@bp.route("/loras/drafts/<draft_id>/images/<image_id>")
def lora_reference_image(author, title, draft_id, image_id):
    from artifact_editor.book import lora_wizard
    from flask import send_file
    book = Book(Author(author), title)
    if not book.bookdir:
        return render_template("404.html"), 404
    try:
        draft = lora_wizard.load(book, draft_id)
        item = next(item for item in draft['images'] if item['id'] == image_id)
    except (ValueError, FileNotFoundError, StopIteration):
        return "Reference image not found.", 404
    return send_file(lora_wizard.directory(book, draft_id) / 'dataset' / item['filename'])


@bp.route("/loras/drafts/<draft_id>/log")
def lora_training_log(author, title, draft_id):
    from artifact_editor.book import lora_wizard
    from flask import Response
    book = Book(Author(author), title)
    if not book.bookdir:
        return render_template("404.html"), 404
    try:
        lora_wizard.load(book, draft_id)
        path = lora_wizard.directory(book, draft_id) / 'training.log'
        if path.exists():
            with path.open('rb') as stream:
                stream.seek(max(0, path.stat().st_size - 65536))
                content = stream.read().decode('utf-8', errors='replace')
        else:
            content = "Waiting for the local training worker."
    except (ValueError, FileNotFoundError):
        return "LoRA draft not found.", 404
    return Response(content, mimetype='text/plain')


@bp.route("/loras/status")
def lora_status(author, title):
    book = Book(Author(author), title)
    if not book.bookdir:
        return render_template("404.html"), 404
    return lora_training_controls(book)


def lora_training_controls(book):
    from artifact_editor.book import lora_training
    entries = lora_training.queue_entries(const.LIBRARY_DIR, bookdir=book.bookdir)
    return render_template(
        "book_lora_training.html", book=book, entries=entries,
        training_active=any(entry['state'] in ('queued', 'running') for entry in entries),
    )


def lora_field(book):
    selected = loras.normalize(book.config.get("default_loras", []))
    options = "".join(
        f'<wa-option value="{escape(item["filename"], quote=True)}" {"selected" if item["filename"] in selected else ""}>{escape(item["name"])}</wa-option>'
        for item in loras.catalog()
    )
    return f"""<div class="wa-cluster" style="align-items: center">
        <wa-select label="Z-Image Turbo LoRAs" multiple clearable
        placeholder="Choose LoRAs" value="{escape(' '.join(selected), quote=True)}"
        hint="Default LoRAs for new Z-Image Turbo images in this book."
        hx-put="{book.set_book_metadata_url}" hx-trigger="change"
        hx-target="#book_metadata" hx-swap="outerHTML"
        hx-vals='js:{{default_loras: JSON.stringify(this.value)}}'>
        {options}</wa-select>
        {lora_training_controls(book)}
        </div>"""



def get_book_metadata(book):
    from artifact_editor.textoviva.workflow import default_image_workflow, image_engine_choices
    book_metadata = "".join(
        [
            book.input_field(
                "Title:", "pretty_title", book.config.get("pretty_title", book.title)
            ),
            book.input_field(
                "Subtitle:", "subtitle", book.config.get("subtitle", book.subtitle)
            ),
            book.choice_field(
                "Style:",
                "default_style",
                book.config.get("default_style"),
                styles_htmx.get_style_choices(),
            ),
            book.choice_field(
                "T2I engine:", "default_image_template",
                default_image_workflow(book, const.DEFAULT_IMAGE_TEMPLATE), image_engine_choices(),
            ),
            lora_field(book),
            book.choice_field(
                "Aspect:",
                "aspect_ratio",
                book.config.get("aspect_ratio", ""),
                htmx.get_aspect_ratio_choices(),
            ),
        ]
    )
    return f"""<div id="book_metadata">
    {book_metadata}
    </div>"""


@bp.route("/")
def table_of_contents(author, title):
    """Display the table of contents for the selected layer."""
    author = Author(author)
    book = Book(author, title)

    if not book.bookdir:
        return render_template("404.html"), 404

    style = book.config.get("default_style", "")

    book_metadata = get_book_metadata(book)

    return render_template(
        "book.html",
        section="book",
        book_metadata=book_metadata,
        language="english",
        pretty_language="English",
        bookurl=book.bookurl,
        author=author,
        pretty_author=book.config.get("author", author),
        title=title,
        subtitle=book.config.get("subtitle", ""),
        pretty_title=book.config.get("pretty_title", title),
        bookdir=book.bookdir,
        chapter_list=chapter_htmx.chapter_list(author=author, title=title),
    )


@bp.route("/characters", methods=["GET"])
def characters_panel(author, title, message=""):
    author_obj = Author(author)
    chapter = Chapter(author_obj, title, 1, language="english")
    catalog = character_store.get_all_characters(chapter, is_global=True)
    character_cards = {
        tag: character_htmx.character_data(chapter, character, True)
        for tag, character in catalog.items()
    }
    return render_template(
        "book_characters_panel.html",
        author=author_obj,
        title=title,
        chapter=chapter,
        characters=catalog,
        character_cards=character_cards,
        message=message,
    )


@bp.route("/characters", methods=["POST"])
def add_book_character(author, title):
    author_obj = Author(author)
    chapter = Chapter(author_obj, title, 1, language="english")
    name = request.form.get("name", "").strip()
    if not name:
        return "Enter a character name.", 400
    tag = character_store.name_to_tag(name)
    catalog = character_store.get_all_characters(chapter, is_global=True)
    if tag in catalog:
        return "That character already exists in this book.", 409
    catalog[tag] = {
        "name": name,
        "tag": tag,
        "description": request.form.get("description", "").strip(),
        "tts_engine": "kokoro",
        "voices": [],
        "alias": [],
        "is_global": True,
    }
    character_store.save_characters(chapter, catalog, is_global=True)
    return characters_panel(author, title)


def ireplace(old, new, text):
    """
    replace only the _first_ instance of `old` in `text` with `new`.
    """
    index_l = text.lower().index(old.lower())
    out = text[:index_l] + new + text[index_l + len(old) :]
    log.info(f"{index_l} {text} -> {out}")
    return out


def indicates_a_new_chapter(book_config, index, book_lines):
    # line, previous_line, next_line):
    """
    Given config settings, does this line indicate we are starting a new chapter?
    """
    line = book_lines[index]
    previous_line = book_lines[index - 1] if index - 1 >= 0 else ""
    next_line = book_lines[index + 1] if index + 1 < len(book_lines) else ""

    clean_line = line.replace("*", "").strip().strip("_")
    as_list = clean_line.split()
    if not clean_line:
        return False

    if book_config.get("BIBLE_BREAKDOWN", False):
        # Each "book" of the bible is a chapter within
        # the system.  In our text file there are multiple
        # ways to differentiate them, I'm going simple.

        # two or more blank lines before
        # chapter title
        # two or more blank lines after

        # for the purpose of this function, we just need
        # to respond 'True' or 'False', we cam make it easy:
        if index < 2:
            # skip the first two lines, they are title/subtitle
            return False

        # the two previous lines are not blank
        if [l.strip() for l in book_lines[index - 2 : index]] != ["", ""]:
            return False

        # the two next lines are not blank
        if [l.strip() for l in book_lines[index + 1 : index + 3]] != ["", ""]:
            return False

        return True

    if book_config["HAS_NUMBERED_CHAPTERS"]:
        # 2
        try:
            int(as_list[0])
            return True
        except ValueError:
            return False
        except IndexError:
            # empty line
            return False

    elif book_config["HAS_ROMAN_NUMERAL_CHAPTERS"]:
        # the whole line is a valid roman numeral
        if book_config.get("CALL_THEM_CHAPTERS", False):
            if "CHAPTER" in clean_line.upper():
                clean_line = ireplace("CHAPTER", "", clean_line)
                if "." in clean_line and book_config.get(
                    "CHAPTER_TITLE_ON_SAME_LINE", False
                ):
                    # strip everything after the period
                    clean_line = clean_line[: clean_line.find(".")]
        elif book_config.get("CALL_THEM_CHAPTERS_OR_LETTERS", False):
            if "CHAPTER" in clean_line.upper():
                clean_line = ireplace("CHAPTER", "", clean_line)
                if "." in clean_line and book_config.get(
                    "CHAPTER_TITLE_ON_SAME_LINE", False
                ):
                    # strip everything after the period
                    clean_line = clean_line[: clean_line.find(".")]
            elif "LETTER" in clean_line.upper():
                clean_line = ireplace("LETTER", "", clean_line)
                if "." in clean_line and book_config.get(
                    "CHAPTER_TITLE_ON_SAME_LINE", False
                ):
                    # strip everything after the period
                    clean_line = clean_line[: clean_line.find(".")]
        elif book_config.get("CALL_THEM_BOOKS", False):
            if "BOOK" in clean_line.upper():
                clean_line = ireplace("BOOK", "", clean_line)

        clean_line = clean_line.strip(".").strip()
        if clean_line:
            return roman.is_roman_numeral(clean_line)
        else:
            # log.info(f"NOT roman numeral: {clean_line}")
            return False

    elif book_config["HAS_ALLCAPS_BREAKS"]:
        if clean_line:
            return clean_line.upper() == clean_line
        return False

    elif book_config["HAS_UNPUNCTUATED_SINGLE_LINE"]:
        if (
            (
                previous_line.strip() == ""  # preceded by a blank line
            )
            and (
                next_line.strip() == ""  # followed by a blank line
            )
            and (line[0] not in ["“"])
            and (
                line[0] == line[0].upper()  # starts with a capital letter
            )
            and (
                line[-1]
                not in ["]", ".", "!", "?", ";", ":"]  # does not end with punctuation
            )
        ):
            return True

        return False

    elif book_config["CALL_THEM_CHAPTERS_OR_LETTERS"]:
        if "CHAPTER" in clean_line.upper():
            remainder = clean_line.upper().replace("CHAPTER", "").strip().strip(".").strip()
            try:
                if int(remainder):
                    return True
            except ValueError:
                return False
            
            return False
        elif "LETTER" in clean_line.upper():
            remainder = clean_line.upper().replace("LETTER", "").strip().strip(".").strip()
            try:
                if int(remainder):
                    return True
            except ValueError:
                return False
            return False
        else:
            return False

    log.error('Unknown chapter break method in config: "%s"', book_config)
    return False


def bible_cleanup(chapter_text_list):
    """
            Input looks like:

    ---
    6:6 And it repented the LORD that he had made man on the earth, and it
    grieved him at his heart.



    6:7 And the LORD said, I will destroy man whom I have created from the
    face of the earth; both man, and beast, and the creeping thing, and
    the fowls of the air; for it repenteth me that I have made them.
    ----
            We want each verse on one line.
    """
    # print(chapter_text_list)

    verse = ""
    verse_text = ""

    as_list = []
    in_header = True

    for line in chapter_text_list:
        if in_header:
            if line[:4] == "1:1 ":
                in_header = False
            else:
                as_list.append(line)
                continue

        if line.strip() == "":
            continue

        # is this a new verse?
        m = re.match(r"(\d+:\d+)( ?.*)", line)
        if m:
            if verse and verse_text:
                # getting a new verse means the previous verse is complete.
                as_list.append(f"{verse} {verse_text}")

            verse = m.group(1)
            verse_text = str(m.group(2)).strip()
        else:
            # not a new verse == more text for the current verse.
            verse_text += " " + line.strip()

    if verse and verse_text:
        as_list.append(f"{verse} {verse_text}")

    # print(f"{as_list=}")
    # raise Exception("debug bible cleanup")
    return as_list


@bp.route("breakdown_chapters", methods=["POST"])
def breakdown_chapters(author, title):
    """
    Break down the book into chapters and return a list of chapters.

    the form POST body should include "raw" with the raw text of the book.
    """
    language = "english"
    author = Author(author)
    log.info("Author: %s, Title: %s", author, title)

    bookdir = os.path.join(author.name, title)
    book_config = config.get_config(bookdir)

    # we have:
    # mybook.config['HAS_NUMBERED_CHAPTERS'] = True
    #
    #                1.
    #
    # or
    #
    #             Chapter 1.
    #

    # mybook.config['HAS_ROMAN_NUMERAL_CHAPTERS'] = True
    #
    #                I.
    #
    # or
    #
    #             Chapter I.
    #

    # mybook.config['HAS_ALLCAPS_BREAKS'] = True
    #
    #             PART TWO
    #

    # mybook.config['HAS_CHAPTER_TITLE'] = True
    #
    #
    #             Chapter I.
    #             The Beginning
    # or
    #             Chapter I. The Beginning
    # log.info("request.form: %s", request.form)
    splitstyle = request.form.get("splitstyle")

    # default values
    book_config["BIBLE_BREAKDOWN"] = False

    if splitstyle == "number":
        book_config["HAS_NUMBERED_CHAPTERS"] = True
        book_config["HAS_ROMAN_NUMERAL_CHAPTERS"] = False
        book_config["HAS_ALLCAPS_BREAKS"] = False
        book_config["HAS_UNPUNCTUATED_SINGLE_LINE"] = False
        book_config["CALL_THEM_CHAPTERS_OR_LETTERS"] = False

    elif splitstyle == "roman":
        book_config["HAS_NUMBERED_CHAPTERS"] = False
        book_config["HAS_ROMAN_NUMERAL_CHAPTERS"] = True
        book_config["HAS_ALLCAPS_BREAKS"] = False
        book_config["HAS_UNPUNCTUATED_SINGLE_LINE"] = False
        book_config["CALL_THEM_CHAPTERS_OR_LETTERS"] = False

    elif splitstyle == "all_caps_title":
        book_config["HAS_NUMBERED_CHAPTERS"] = False
        book_config["HAS_ROMAN_NUMERAL_CHAPTERS"] = False
        book_config["HAS_ALLCAPS_BREAKS"] = True
        book_config["HAS_UNPUNCTUATED_SINGLE_LINE"] = False
        book_config["CALL_THEM_CHAPTERS_OR_LETTERS"] = False

    elif splitstyle == "unpunctuated_single_line":
        book_config["HAS_NUMBERED_CHAPTERS"] = False
        book_config["HAS_ROMAN_NUMERAL_CHAPTERS"] = False
        book_config["HAS_ALLCAPS_BREAKS"] = False
        book_config["HAS_UNPUNCTUATED_SINGLE_LINE"] = True
        book_config["CALL_THEM_CHAPTERS_OR_LETTERS"] = False

    elif splitstyle == "allcaps_and_chapter_roman":
        book_config["HAS_NUMBERED_CHAPTERS"] = False
        book_config["HAS_ROMAN_NUMERAL_CHAPTERS"] = True
        book_config["HAS_ALLCAPS_BREAKS"] = True
        book_config["HAS_UNPUNCTUATED_SINGLE_LINE"] = False
        book_config["CALL_THEM_CHAPTERS"] = True
        book_config["CALL_THEM_CHAPTERS_OR_LETTERS"] = False

    elif splitstyle == "CHAPTER_roman_title":
        book_config["HAS_NUMBERED_CHAPTERS"] = False
        book_config["HAS_ROMAN_NUMERAL_CHAPTERS"] = True
        book_config["HAS_ALLCAPS_BREAKS"] = False
        book_config["HAS_UNPUNCTUATED_SINGLE_LINE"] = False
        book_config["CALL_THEM_CHAPTERS"] = True
        book_config["CALL_THEM_CHAPTERS_OR_LETTERS"] = False
        book_config["CHAPTER_TITLE_ON_SAME_LINE"] = True

    elif splitstyle == "letter_or_chapter_arabic":
        book_config["HAS_NUMBERED_CHAPTERS"] = False
        book_config["HAS_ROMAN_NUMERAL_CHAPTERS"] = False
        book_config["HAS_ALLCAPS_BREAKS"] = False
        book_config["HAS_UNPUNCTUATED_SINGLE_LINE"] = False
        book_config["CALL_THEM_CHAPTERS"] = False
        book_config["CALL_THEM_CHAPTERS_OR_LETTERS"] = True

    elif splitstyle == "bible_breakdown":
        # based on the king james .txt I have
        book_config["BIBLE_BREAKDOWN"] = True

    elif splitstyle == "single_chapter":
        book_config["SINGLE_CHAPTER"] = True
    else:
        log.error("Unknown splitstyle: %s", splitstyle)

    config.save_config(bookdir, book_config)

    log.info("config: %s", book_config)
    index = 0
    chapter_text_list = []
    chapter_list = []

    raw = request.form.get("raw", "")

    if book_config.get("SINGLE_CHAPTER", False):
        log.info("Applying single chapter mode")
        chapter = Chapter(
            author=author,
            title=title,
            number=1,
            language=language,
        )
        os.makedirs(os.path.join(const.LIBRARY_DIR, chapter.languagedir), exist_ok=True)
        chapter.save_txt(raw)
    else:
        previous_line = ""
        log.info("Breaking book into chapters (%s characters)", len(raw))

        book_lines = raw.splitlines()
        log.info("Found %d lines of text in book", len(book_lines))
        first = True
        for lineindex, line in enumerate(book_lines):
            log.info('Processing line %d: "%s"', lineindex, line)
            if indicates_a_new_chapter(
                book_config,
                lineindex,
                book_lines,
            ):
                log.info('New Chapter: "%s"', line)

                # start of a new (or first) chapter.
                # if the chapter title is on that same line this will
                # catch it.
                if chapter_text_list and not first:
                    log.info("Recording chapter %d", index + 1)
                    # we have a previous chapter, so we save it.
                    if book_config["BIBLE_BREAKDOWN"]:
                        chapter_text_list = bible_cleanup(chapter_text_list)

                    chapter = Chapter(
                        author=author,
                        title=title,
                        number=index + 1,
                        language=language,
                    )

                    os.makedirs(chapter.languagedir, exist_ok=True)

                    chapter.save_txt("\n".join(chapter_text_list))

                    index = len(chapter_list) + 1
                    chapter_list.append(
                        {
                            "index": index,
                            "slug": f"{index:04}",
                        }
                    )
                    chapter_text_list = [line]
                elif first:
                    log.info("Starting first chapter")
                    first = False
                    chapter_text_list.append(line)
                else:
                    chapter_text_list = [line]
            else:
                if book_config["BIBLE_BREAKDOWN"]:
                    # some cleanup, want verses separated by newlines
                    # most of them already are, but there are many mistakes.
                    matching = True
                    while matching:
                        m = re.match(r"(.*[^[0-9])([0-9]+:[0-9]+)( ?.*)", line)

                        if m:
                            # there is a verse indicator inside the paragraph.
                            prefix = m.group(1)
                            verse = m.group(2)
                            rest = m.group(3).strip()
                            log.info(f"Splitting {verse}")

                            # record prefix
                            chapter_text_list.append(prefix)
                            # include everything else in the next line to evaluate
                            line = f"{verse} {rest}"

                        else:
                            # we are done
                            log.info('No more verses in line: "%s"', line)
                            chapter_text_list.append(line)
                            matching = False
                else:
                    chapter_text_list.append(line)

            previous_line = line

        # the leftover chapter
        if chapter_text_list:
            log.info("Recording final chapter %d", len(chapter_list) + 1)
            if book_config["BIBLE_BREAKDOWN"]:
                chapter_text_list = bible_cleanup(chapter_text_list)

            chapter = Chapter(
                author=author,
                title=title,
                number=index + 1,
                language=language,
            )

            os.makedirs(chapter.languagedir, exist_ok=True)

            chapter.save_txt("\n".join(chapter_text_list))

            chapter_list.append(
                {
                    "index": index + 1,
                    "slug": f"chapter_{index + 1}",
                }
            )

    # return htmx_chapter_list(bookurl, author, title, chapter_list)
    book_url = url_for(
        "library.book.table_of_contents",
        author=author.name,
        title=title,
    )
    return redirect(book_url, code=302)


@bp.route("/characters/<character_tag>/merge", methods=["POST"])
def merge_book_character(author, title, character_tag):
    chapter = Chapter(Author(author), title, 1, language="english")
    try:
        character_store.merge_characters(
            chapter, character_tag, request.form.get("target_tag", ""), book_scope=True
        )
        message = "Characters merged. Names retained as aliases and references updated."
    except ValueError as exc:
        message = str(exc)
    return characters_panel(author, title, message=message)
