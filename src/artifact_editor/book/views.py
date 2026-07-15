import glob
import os
import shutil
import re

from flask import (
    Blueprint,
    render_template,
    request,
    send_from_directory,
    redirect,
    url_for,
)

from .book import Book

import artifact_editor.styles.htmx as styles_htmx
import const
import logger
import roman
from artifact_editor import (
    config,
    tools,
)
from artifact_editor.author.author import Author

# from artifact_editor.chapter.chapter import Chapter
from artifact_editor.chapter import htmx as chapter_htmx
from artifact_editor.chapter.chapter import Chapter

from . import htmx


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

    for name, value in request.form.items():
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


def get_book_metadata(book):
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

    # book_style_widget = styles_htmx.add_style_widget(
    #     selected_style=style,
    #     url=f"/{bookurl}/actions/save_book_style"
    # )

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
    bookdir = tools.get_bookdir(author.name, title)
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
    log.info("request.form: %s", request.form)
    splitstyle = request.form.get("splitstyle")

    # default values
    book_config["BIBLE_BREAKDOWN"] = False

    if splitstyle == "number":
        book_config["HAS_NUMBERED_CHAPTERS"] = True
        book_config["HAS_ROMAN_NUMERAL_CHAPTERS"] = False
        book_config["HAS_ALLCAPS_BREAKS"] = False
        book_config["HAS_UNPUNCTUATED_SINGLE_LINE"] = False

    elif splitstyle == "roman":
        book_config["HAS_NUMBERED_CHAPTERS"] = False
        book_config["HAS_ROMAN_NUMERAL_CHAPTERS"] = True
        book_config["HAS_ALLCAPS_BREAKS"] = False
        book_config["HAS_UNPUNCTUATED_SINGLE_LINE"] = False

    elif splitstyle == "all_caps_title":
        book_config["HAS_NUMBERED_CHAPTERS"] = False
        book_config["HAS_ROMAN_NUMERAL_CHAPTERS"] = False
        book_config["HAS_ALLCAPS_BREAKS"] = True
        book_config["HAS_UNPUNCTUATED_SINGLE_LINE"] = False

    elif splitstyle == "unpunctuated_single_line":
        book_config["HAS_NUMBERED_CHAPTERS"] = False
        book_config["HAS_ROMAN_NUMERAL_CHAPTERS"] = False
        book_config["HAS_ALLCAPS_BREAKS"] = False
        book_config["HAS_UNPUNCTUATED_SINGLE_LINE"] = True

    elif splitstyle == "allcaps_and_chapter_roman":
        book_config["HAS_NUMBERED_CHAPTERS"] = False
        book_config["HAS_ROMAN_NUMERAL_CHAPTERS"] = True
        book_config["HAS_ALLCAPS_BREAKS"] = True
        book_config["HAS_UNPUNCTUATED_SINGLE_LINE"] = False
        book_config["CALL_THEM_CHAPTERS"] = True

    elif splitstyle == "CHAPTER_roman_title":
        book_config["HAS_NUMBERED_CHAPTERS"] = False
        book_config["HAS_ROMAN_NUMERAL_CHAPTERS"] = True
        book_config["HAS_ALLCAPS_BREAKS"] = False
        book_config["HAS_UNPUNCTUATED_SINGLE_LINE"] = False
        book_config["CALL_THEM_CHAPTERS"] = True
        book_config["CHAPTER_TITLE_ON_SAME_LINE"] = True

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
        for index, line in enumerate(book_lines):
            log.info('Processing line %d: "%s"', index, line)
            if indicates_a_new_chapter(
                book_config,
                index,
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
