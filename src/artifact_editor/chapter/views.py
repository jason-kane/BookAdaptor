import glob
import json
import os
import re
import html
import shutil
import roman
import spacy
import subprocess

from flask import (
    Blueprint,
    redirect,
    render_template,
    request,
    send_from_directory,
    url_for,
    g,
)

import const
import logger
from artifact_editor import (
    config,
    tools,
)
from bs4 import BeautifulSoup
from artifact_editor.author.author import Author
from artifact_editor.tools import (
    get_bookdir,
    get_chapterdir,
    get_chapterurl,
)

from . import htmx

from artifact_editor.chapter.chapter import (
    Chapter,
    chapter_url_defaults_helper,
    chapter_url_value_preprocessor,
)

from artifact_editor.styles import htmx as styles_htmx

from ncdeltaprocess import TranslatorQuillJS

from typing import Any

log = logger.log(__name__)

t = TranslatorQuillJS()

bp = Blueprint(
    "chapter",
    __name__,
    template_folder=os.path.join(os.path.dirname(__file__), "templates"),
)

@bp.url_defaults
def my_url_defaults(endpoint, values):
    chapter_url_defaults_helper(endpoint, values)

@bp.url_value_preprocessor
def my_url_value_preprocessor(endpoint, values):
    chapter_url_value_preprocessor(endpoint, values) 


# PUT /Oscar%20Wilde/The%20Picture%20of%20Dorian%20Gray/1/set_chapter_cover
@bp.route("/set_chapter_cover", methods=["PUT"])
def set_chapter_cover():
    filename = request.form.get("filename")
    image_index = int(request.form.get("index"))

    image_xml = g.chapter.get_image(image_index)
    paragraph_xml = image_xml.find_parent("paragraph")

    source = os.path.join(
        tools.const.LIBRARY_DIR,
        g.chapter.get_paragraph_dir(paragraph_xml['index']),
        filename,
    )

    destination = os.path.join(
        const.LIBRARY_DIR,
        g.chapter.chapterdir,
        "cover.png",
    )

    # Set the chapter cover image
    shutil.copyfile(source, destination)

    return redirect(
        url_for(
            "library.book.chapter.chapter_base",
            author=g.author.name,
            title=g.title,
            chapter_number=g.chapter.number,
            language=g.language,
        )
    )


@bp.route("/set_mood", methods=["PUT"])
def set_mood():
    new_mood = request.form.get("mood")
    
    g.chapter.mood = new_mood
    log.info(f"Setting g.chapter.config['mood'] to {new_mood}")
    g.chapter.config["mood"] = new_mood
    g.chapter.save_config()

    return "", 200


@bp.route("/set_theme", methods=["PUT"])
def set_theme():
    new_theme = request.form.get("theme")
    g.chapter.theme = new_theme
    g.chapter.config["theme"] = new_theme
    g.chapter.save_config()

    return "", 200


@bp.route("")
@bp.route("/")
def chapter_base():
    return render_template(
        "text.html",
        chapter_pdf_url = url_for(
            'library.book.chapter.typography.serve_chapter_pdf',
        ),
        language=g.chapter.language,
        pretty_language=g.chapter.pretty_language,
        import_button=htmx.import_button(g.chapter),
        chapterurl=g.chapter.url,
        author=g.author,
        pretty_author=g.chapter.config.get("author", g.author),
        title=g.title,
        pretty_title=g.chapter.config.get("title", g.title),
        chapter_title=g.chapter.chapter_title,
        chapter=g.chapter,
    )


def add_author(chapter_xml, chapter):
    """
    Add an author page to the given chapter XML.
    """
    author_page = chapter_xml.new_tag("paragraph")
    author_page.attrs["tags"] = "has-text=false,spoken-only=true"
    author_page.attrs["fullscreen"] = "true"
    chapter_xml.find("chapter").append(author_page)

    author_image = chapter_xml.new_tag("image")
    author_prompt = f"Renown author {chapter.author.name} at a desk writing the famous story {chapter.title}"
    author_image.attrs["prompt"] = author_prompt
    author_page.append(author_image)

    phrase = chapter_xml.new_tag("phrase")
    phrase.string = f"By {chapter.author.name}"
    phrase.attrs["speaker"] = "Narrator"
    author_page.append(phrase)


def extract_title(delta, chapter, chapter_xml):
    """
    Extract the title from the delta and add it to the chapter_xml.
    """
    ops = []
    for operation in delta["ops"]:
        if "insert" in operation:
            insert_list = []
            for line in operation["insert"].split("\n"):
                if line.strip().upper() == chapter.title.upper():
                    log.info("Found title page: %s", line)
                    add_title(chapter_xml, chapter)
                    continue

                insert_list.append(line)

        operation["insert"] = "\n".join(insert_list)
        ops.append(operation)

    return {"ops": ops}


def extract_author(delta, chapter, chapter_xml):
    """
    Extract the author from the delta and add it to the chapter_xml.
    """
    ops = []
    for operation in delta["ops"]:
        if "insert" in operation:
            insert_list = []
            for line in operation["insert"].split("\n"):
                if line.strip().upper() in [
                    f"BY {chapter.author.name.upper()}",
                    f"{chapter.author.name.upper()}",
                ]:
                    log.info("Found author page: %s", line)
                    add_author(chapter_xml, chapter)
                    continue

                insert_list.append(line)
            operation["insert"] = "\n".join(insert_list)
        ops.append(operation)

    return {"ops": ops}


@bp.route("/reflow_paragraphs", methods=["PUT"])
def reflow_paragraphs():
    """
    Reflow the paragraphs in the chapter.
    """
    # we want to remove all "excess" newlines from this chapter
    # this will allow the contents to reflow based on latex rules
    # which are generally best.
    
    # The trouble, or the challenge, is they are in delta format which
    # is not paragraph aware.
    delta = g.chapter.load_delta()
    out_ops = []
    for operation in delta["ops"]:
        if "insert" in operation:
            operation["insert"] = re.sub(r"\n{2,}", "**PARAGRAPH**", operation["insert"])

            # remove all newlines that are not followed by a newline
            # (reducing our \n\n\n to \n\n)
            operation["insert"] = re.sub(r"\n([^\n])", r" \1", operation["insert"])

            operation["insert"] = re.sub(r"\*\*PARAGRAPH\*\*", "\n\n", operation["insert"])
        out_ops.append(operation)

    g.chapter.save_delta({"ops": out_ops})
    return "", 200


def compress_ops(ops: list[dict[str, Any]]) -> list[dict[str, Any]]:
    compressed = []
    attributes = {}
    same = []
    for operation in ops:  # type: dict[str, Any]
        try:
            del operation["attributes"]["phrase"]
        except KeyError:
            pass

        if not same:
            same.append(operation)
        else:
            if operation.get("attributes") == same[-1].get("attributes"):
                same.append(operation)
            else:
                # different attributes, we need to compress the previous same list into a single operation.
                if len(same) > 1:
                    compressed.append(
                        {
                            "insert": "".join([o["insert"] for o in same]),
                            "attributes": same[-1].get("attributes", {}),
                            "type": same[-1].get("type", "word"),
                        }
                    )
                else:
                    compressed.append(same[-1])
                same = [operation]

    # compress any remaining operations in 'same'
    if same:
        if len(same) > 1:
            compressed.append(
                {
                    "insert": "".join([o["insert"] for o in same]),
                    "attributes": same[-1].get("attributes", {}),
                    "type": same[-1].get("type", "word"),
                }
            )
        else:
            compressed.append(same[-1])

        if compressed and operation.get("type") == compressed[-1].get("type") == "word":
            compressed[-1]["insert"] += operation["insert"]
        else:
            compressed.append(operation)
    return compressed


@bp.route("/import_chapter", methods=["POST"])
def import_chapter():
    """
    New algorithm based on scoring every word based on how good of a phrase
    break it is and how suitable it is as a new image.
    """
    chapter_soup = BeautifulSoup("<chapter></chapter>", "xml")

    # load_delta knows to fallback to chapter.txt with a thin wrapper if there
    # is no delta file.
    delta = g.chapter.load_delta()

    # The title and author at the beginning of the chapter are a pain in the
    # ass. They have to be special, because if we're rendering a single chapter
    # as a video we want to include and speak both the book and author.  If
    # we're chapter 4 of 20 in a six hour video, we don't want to waste a moment
    # with tedious repetition of the title and author.
    
    
    # These confuse the phrase breaking algorithm, so we pop them out.
    #delta = extract_title(delta, g.chapter, chapter_soup)
    #delta = extract_author(delta, g.chapter, chapter_soup)

    # nltk.download('punkt_tab')
    spacy.prefer_gpu()
    try:
        nlp = spacy.load("en_core_web_sm")
    except OSError:
        log.info("Downloading spacy model en_core_web_sm...")
        subprocess.run(
            [
                "python",
                "-m",
                "spacy",
                "download",
                "en_core_web_sm",
                "--break-system-packages",
            ]
        )
        nlp = spacy.load("en_core_web_sm")

    # break all the 'inserts' in delta up into single delta sentences.
    out_ops = []
    index_start = 0

    delta["ops"] = compress_ops(delta["ops"])

    for ops in delta["ops"]:
        log.info("Processing ops: %s", repr(ops))

        for major_break in re.split(r"(\n\n+)", ops.get("insert", "")):
            log.info('major_break: %s', repr(major_break))
            sentence_spans = list(nlp(major_break).sents)
            sentence_count = len(sentence_spans)
            log.info("Operation has %s sentences", sentence_count)
            for sentence_index, sentence_span in enumerate(sentence_spans, index_start):
                log.info("[%s] sentence_span: %s", sentence_index, repr(sentence_span))
                log.info("[%s] sentence_span.text_with_ws: %s", sentence_index, repr(sentence_span.text_with_ws))
                log.info("[%s] sentence_span.text: %s", sentence_index, repr(sentence_span.text))
                sentence = sentence_span.text_with_ws

                # ARGG!! spacy still fucks up trailing newlines.
                # we _need_ the trailing whitespace.
                # is this the last sentence?
                # if sentence_index == sentence_count - 1:
                #     log.info("%s == %s", sentence_index, sentence_count - 1)
                #     whitespace = ops["insert"][len(ops["insert"].rstrip()) :]
                #     log.info("whitespace: %s", repr(whitespace))
                #     sentence += whitespace
                # else:
                #     log.info("%s != %s", sentence_index, sentence_count)

                # the problem with nltk.sent_tokenize is that it destroys whitespace.
                # some of that whitespace is critical for correct typesetting.
                # example:
                #  input: " wrote in machine code.\n\nNot FORTRAN.  \n\tNot RATFOR."
                #  output: ["wrote in machine code.", "Not FORTRAN.", "Not RATFOR."]

                # the trailing whatever, don't care, but the leading tabs and extra
                # newlines must be preserved.  I'll try spacy...

                if sentence[0] in [",", ".", "!", "?", ";", ":"]:
                    # the sentence begins with punctuation.
                    # we can't move it to the end of the previous because it's here because
                    # the formatting has changed.  Not a problem exactly, we just
                    # have to fix the trailing whitespace on the previous opt_ops.
                    if out_ops[-1]["insert"][-1] == " ":
                        out_ops[-1]["insert"] = out_ops[-1]["insert"][:-1]

                log.info("sentence: %s", repr(sentence))
                for word in re.split(r"([ \n])", sentence):
                    log.info('word: %s', repr(word))
                    if out_ops and word in [" ", "\n"]:
                        out_ops[-1]["insert"] += word
                        continue

                    if word.strip():
                        out_ops.append(
                            {
                                "insert": word,
                                "attributes": ops.get("attributes", {}),
                                "type": "word",
                                # punctuation that isn't at the end of a sentence is a weak
                                # phrase break, way better than nothing.
                                "phrase_score": 1
                                if word[-1] in [".", "!", "?", ";", ":", ","]
                                else 0,
                            }
                        )
                    elif word:
                        out_ops.append(
                            {
                                "insert": word,
                                "attributes": ops.get("attributes", {}),
                                "type": "whitespace",
                                "phrase_score": 0,
                            }
                        )

                if out_ops[-1]["insert"].strip():
                    try:
                        out_ops[-1]["phrase_score"] += {
                            ".": 5,
                            "!": 5,
                            "?": 5,
                            ";": 3,
                            ":": 3,
                            ",": 1,
                        }[out_ops[-1]["insert"].strip()[-1]]
                    except KeyError:
                        pass

    index = index_start
    attributes = {}
    phrases = []
    # then out_ops to latex.
    while index < len(out_ops):
        # for phrases -- the visual highlighted text, we want each section
        # to be small enough to fit the readers eye.  Less than 10 words,
        # with a strong preference for ending on a sentence break or strong
        # punctuation mark.

        # This is also the unit of TTS.  Its much easier for the TTS to
        # sound good when it gets a complete sentence.

        # we will use a WINDOW_SIZE word evaluation window that resets whenever we
        # choose a phrase break point.

        window_size = 18  # 25 is too big

        # we are starting at out_ops[index], and we want to know where we need
        # to put out_ops[break_index] to split at the best possible phrase break
        # point.  We're going to evaluate and score each word in the window,
        # then pick the highest scoring word as the break point.  If we don't
        # find a break point, ie: nothing scores higher than a threshold -- we
        # will increase the window size and try again.
        break_index = None
        while break_index is None and window_size < 60:
            highest = 0

            for i in range(index, min(index + window_size, len(out_ops))):
                if out_ops[i]["type"] == "whitespace":
                    continue

                if out_ops[i].get("phrase_score", 0) > highest:
                    highest = out_ops[i]["phrase_score"]
                    break_index = i

            if break_index is None:
                if index + window_size >= len(out_ops):
                    break_index = len(out_ops)

            if break_index is None:
                log.info(
                    "No phrase break found in window size %d, increasing window size...",
                    window_size,
                )
                window_size += 2

        if break_index is None:
            log.info("!FAILURE! No phrase break found!")
            p = out_ops[index : min(index + window_size, len(out_ops))]
            log.info("p: %s", p)
            log.info("phrase: %s", " ".join([o["insert"] for o in p]))

            raise ValueError("No phrase break found!")

        log.info("Processing out_ops[%d:%d] for typesetting...", index, break_index + 1)
        typeset_phrase = []

        our_ops = out_ops[index : break_index + 1]

        doc = t.ops_to_internal_representation(our_ops)
        as_latex = doc.render_tree(mode='latex')
        as_latex = as_latex.replace("\\end{center}", "\\end{center}\n\\vspace{-3.5em}")

        plain_phrase = "".join([o["insert"] for o in our_ops])

        # for orig_index in range(index, break_index + 2):
        #     if orig_index >= len(out_ops):
        #         close_only = True
        #         orig = {"insert": "", "attributes": {}}
        #     else:
        #         orig = out_ops[orig_index]
        #         close_only = False

        #     o = orig.copy()
        #     if not close_only:
        #         # replacements with latex symbols
        #         if "—" in o["insert"]:
        #             log.info("Replacing em dash with -- in phrase: %s", o["insert"])
        #             o["insert"] = o["insert"].replace("—", "--")

        #         if "œ" in o["insert"]:
        #             log.info("Replacing œ with oe in phrase: %s", o["insert"])
        #             o["insert"] = o["insert"].replace("œ", r"\oe")

        #         # simple escapes
        #         if "&" in o["insert"]:
        #             log.info("Replacing & with \& in phrase: %s", o["insert"])
        #             o["insert"] = o["insert"].replace("&", r"\&")

        #         if "\t" in o["insert"]:
        #             log.info("Replacing tab with \hspace*{2em} in phrase: %s", o["insert"])
        #             o["insert"] = o["insert"].replace("\t", r"\hspace*{2em}")

        #         if o.get('attributes', {}).get('align') == 'center':
        #             # the previous line (this is a \n) should be
        #             # centered.
        #             typeset_phrase[-1] = r"\begin{center}" + typeset_phrase[-1] + r"\end{center}"

        #         # That I have to treat this as a special edge case means my
        #         # algorithm is shit.  Adding more features is going to be a
        #         # nightmare until I redesign to handle arbitrary combinations of
        #         # attributes.
        #         if (
        #             o["attributes"].get("italic")
        #             and attributes.get("italic") is None
        #             and o["attributes"].get("bold")
        #             and attributes.get("bold") is None
        #         ):
        #             # bold _and_ italics.
        #             typeset_phrase.append(r"\textit{\textbf{" + o["insert"])
        #             attributes["italic"] = True
        #             attributes["bold"] = True
        #             continue

        #         if o["attributes"].get("italic") and attributes.get("italic") is None:
        #             # we are starting an italic block
        #             typeset_phrase.append(r"\textit{" + o["insert"])
        #             attributes["italic"] = True
        #             continue

        #         if o["attributes"].get("bold") and attributes.get("bold") is None:
        #             # we are starting a bold block
        #             typeset_phrase.append(r"\textbf{" + o["insert"])
        #             attributes["bold"] = True
        #             continue

        #     if o["attributes"].get("italic") is None and attributes.get("italic"):
        #         # we are ending an italic block
        #         if typeset_phrase:
        #             # retain the trailing whitespace, _after_ the close tag.
        #             stripped = typeset_phrase[-1].rstrip()
        #             trailing_whitespace = typeset_phrase[-1][len(stripped) :]
        #             typeset_phrase[-1] = stripped + "}" + trailing_whitespace
        #         else:
        #             stripped = phrases[-1]["latex"].rstrip()
        #             trailing_whitespace = phrases[-1]["latex"][len(stripped) :]
        #             phrases[-1]["latex"] = stripped + "}" + trailing_whitespace

        #         attributes["italic"] = None

        #     if o["attributes"].get("bold") is None and attributes.get("bold"):
        #         # how many characters long was the previous line
        #         # prior to the trailing whitespace?
        #         if typeset_phrase:
        #             stripped = typeset_phrase[-1].rstrip()
        #             trailing_whitespace = typeset_phrase[-1][len(stripped) :]
        #             # tuck the } close brace between the string and its trailing whitespace.
        #             typeset_phrase[-1] = stripped + "}" + trailing_whitespace
        #             # we aren't bold anymore.
        #         else:
        #             #  we went bold in a previous phrase.
        #             stripped = phrases[-1]["latex"].rstrip()
        #             trailing_whitespace = phrases[-1]["latex"][len(stripped) :]
        #             phrases[-1]["latex"] = stripped + "}" + trailing_whitespace

        #         attributes["bold"] = None

        #     if not close_only:
        #         typeset_phrase.append(o["insert"])

        # plain_phrase = ""
        # for i in range(index, break_index):
        #     # close.. but we aren't setting the phrase id
        #     # for the title or author correctly.
        #     # we know which phrase each word is in.
        #     # out_ops[i]["attributes"]["phrase"] = 1 + len(phrases)
        #     plain_phrase += out_ops[i]["insert"]
       
        phrases.append({"spoken": plain_phrase, "latex": as_latex})

        index = break_index + 1

    # clear any leading newlines from the first phrase.
    out_ops[0]["insert"] = out_ops[0]["insert"].lstrip("\n")
    # out_ops = [
    #     {
    #         "attributes": {
    #             "bold": True,
    #             "phrase": 0
    #         },
    #         "insert": "The Story of Mel\n\n"
    #     }, {
    #         "insert": "by Ed Nather\n\n",
    #         "attributes": {
    #             "bold": False,
    #             "phrase": 1
    #         },
    #     },        
    # ] + out_ops

    g.chapter.save_delta({"ops": out_ops})

    chapter_xml = chapter_soup.find("chapter")

    with open(
        os.path.join(
            const.LIBRARY_DIR,
            g.chapter.get_chapterdir(),
            g.chapter.language,
            "chapter.latex",
        ),
        "w",
    ) as h, open(
        os.path.join(
            const.LIBRARY_DIR,
            g.chapter.get_chapterdir(),
            g.chapter.language,
            "chapter.rainbow.latex",
        ),
        "w",
    ) as rainbow_h:
        header = r"""\documentclass[parskip=full]{scrartcl}
\usepackage[paperheight=200in,paperwidth=2.825in,top=0.5in,bottom=4in,left=0.1in,right=0.1in,heightrounded]{geometry}
\addtokomafont{title}{\centering}
\addtokomafont{author}{\centering}
\usepackage[english]{babel}
\usepackage[utf8]{inputenc}
\usepackage[T1]{fontenc}
\usepackage{froufrou}

\usepackage{luaquotes}
\usepackage{luacolor}
\usepackage{lua-ul}
\usepackage{titling}

\usepackage[osf]{libertinus-otf}

%s

\pagenumbering{gobble}
\widowpenalties 1 10000
\raggedbottom
\setlength{\leftmargini}{0.125em}

\begin{document}
\tolerance=9999
\hyphenpenalty=10000
\exhyphenpenalty=100                    
"""
# \setlength{\droptitle}{-45pt}
# \posttitle{\par\end{center}}
# \title{%s}
# \author{%s}
# \date{}
# \maketitle
# \vspace{-0.5in}

        latex_block = ""
        latex_rainbow_block = ""
        paragraph = chapter_soup.new_tag("paragraph")

        rainbow_series = []

        initial_index = len(chapter_soup.find_all("phrase"))

        for index, p in enumerate(phrases, initial_index):
            phrase = chapter_soup.new_tag("phrase")
            phrase.string = p["spoken"]
            phrase.attrs["index"] = index

            # limit to 24 bits, 10 offset to keep us away from pure black
            rainbow_int = (index + 10) % 16777216
            phrase.attrs["latex"] = html.escape(p["latex"].replace("\n", r"\n"))

            prior_whitespace = p["latex"][: len(p["latex"]) - len(p["latex"].lstrip())]
            post_whitespace = p["latex"][len(p["latex"].rstrip()) :]
            p["latex_rainbow"] = (
                f"{prior_whitespace}\\color{{B{rainbow_int:X}}}\\highLight[B{rainbow_int:X}]{{{p['latex'].strip()}}}{post_whitespace}"
            )
            phrase.attrs["latex_rainbow"] = p["latex_rainbow"].replace("\n", r"\n")

            _r, _g, _b = rainbow_int.to_bytes(3, "big")
            rainbow_series.append(
                f"\\definecolor{{B{rainbow_int:X}}}{{RGB}}{{{_r},{_g},{_b}}}"
            )

            latex_block += p["latex"]
            latex_rainbow_block += p["latex_rainbow"]

            # double newline == paragraph break.
            if phrase.attrs["latex"].lstrip(" ").startswith(r"\n\n"):
                new_paragraph = chapter_soup.new_tag("paragraph")
                new_paragraph.append(phrase)
                chapter_xml.append(new_paragraph)
                paragraph = new_paragraph
            elif phrase.attrs["latex"].rstrip(" ").endswith(r"\n\n"):
                paragraph.append(phrase)
                new_paragraph = chapter_soup.new_tag("paragraph")
                chapter_xml.append(new_paragraph)
                paragraph = new_paragraph
            else:
                log.info("s: %s", p["spoken"])
                paragraph.append(phrase)

        chapter_xml.append(paragraph)

        all_lines = []
        for line in latex_block.splitlines(keepends=True):
            if line.strip() and all_lines and all_lines[-1].strip():
                # there is a non-blank line after a non-blank line.  We need to add a \\
                all_lines[-1] = all_lines[-1].rstrip() + r"\\" + "\n"

            # escapes
            if "$" in line:
                line = line.replace("$", r"\$")

            all_lines.append(line)

        all_rainbow_lines = []
        for line in latex_rainbow_block.splitlines(keepends=True):
            if line.strip() and all_rainbow_lines and all_rainbow_lines[-1].strip():
                # there is a non-blank line after a non-blank line.  We need to add a \\
                all_rainbow_lines[-1] = all_rainbow_lines[-1].rstrip() + r"\\" + "\n"

            # escapes
            if "$" in line:
                line = line.replace("$", r"\$")

            all_rainbow_lines.append(line)

        h.write(header % (""))
        rainbow_h.write(
            header % ("\n".join(rainbow_series))
        )

        # now write the header, in latex format.
        title_delta = g.chapter.load_title_delta()
        # t = TranslatorQuillJS()
        doc = t.ops_to_internal_representation(title_delta["ops"])
        as_latex = doc.render_tree(mode='latex')
        as_latex = as_latex.replace("\\end{center}", "\\end{center}\n\\vspace{-3.5em}")
        as_latex += "\n\\vspace{3.5em}"
        h.write(as_latex)

        h.write("".join(all_lines))
        rainbow_h.write("".join(all_rainbow_lines))

        h.write(r"""\end{document}""")
        rainbow_h.write(r"""\end{document}""")

    #delta["ops"] = out_ops
    g.chapter.soup = chapter_xml
    g.chapter.save_xml()

    cwd = os.path.join(
        const.LIBRARY_DIR,
        g.chapter.get_chapterdir(),
        g.chapter.language,
    )

    # sorry assholes.
    subprocess.run(["lualatex", "./chapter.latex"], cwd=cwd)
    subprocess.run(
        [
            "gs",
            "-dNOPAUSE",
            "-sDEVICE=pngalpha",
            "-r600",
            "-dTextAlphaBits=4",
            "-dGraphicsAlphaBits=4",
            "-sOutputFile=chapter-%d.png",
            "chapter.pdf",
            "-c",
            "quit",
        ],
        cwd=cwd,
    )

    subprocess.run(["lualatex", "./chapter.rainbow.latex"], cwd=cwd)

    subprocess.run(
        [
            "gs",
            "-dNOPAUSE",
            "-sDEVICE=pngalpha",
            "-r600",
            "-dTextAlphaBits=4",
            "-dGraphicsAlphaBits=4",
            "-sOutputFile=chapter.rainbow-%d.png",
            "chapter.rainbow.pdf",
            "-c",
            "quit",
        ],
        cwd=cwd,
    )

    return "", 200


#  /update_title
@bp.route("/update_title", methods=["PUT"])
def update_title():
    new_title = request.form.get("chapter_title")
    g.chapter.set_chapter_title(new_title)
    return "", 200


@bp.route("/delta", methods=["GET"])
def get_delta():
    try:
        raw_text = g.chapter.load_delta()
    except FileNotFoundError:
        log.error(
            "Delta file not found for chapter %s. Returning empty text.",
            g.chapter.get_delta_fn(),
        )
        raw_text = {}

    if raw_text in [{}, None]:
        raw_text = g.chapter.load_txt()
        delta = {"ops": [{"insert": raw_text}]}        
        g.chapter.save_delta(delta)
        raw_text = json.dumps(delta, indent=2)

    if raw_text in [{}, None]:
        book_txt = os.path.join(
            const.LIBRARY_DIR, g.chapter.get_chapterdir(), "book.txt"
        )

        # plain text file, not delta.  No problem.
        if os.path.exists(book_txt):
            log.info("Importing plain text %s", book_txt)

            with open(book_txt, "r", encoding="utf-8") as f:
                raw_text = f.read()

            delta = {"ops": [{"insert": raw_text}]}
            raw_text = json.dumps(delta, indent=2)

            os.makedirs(os.path.dirname(g.chapter.get_delta_fn()), exist_ok=True)
            with open(g.chapter.get_delta_fn(), "w", encoding="utf-8") as f:
                f.write(raw_text)
        else:
            log.info(
                "No book.txt found for chapter %s. Returning empty delta.",
                g.chapter.get_chapterdir(),
            )
            raw_text = json.dumps({"ops": []}, indent=2)

    return raw_text, 200



    # chapter_xml = g.chapter.get_xml()
    # image_list = []
    # for image_index, image in enumerate(chapter_xml.find_all("image")):
    #     log.info("Adding image..")
    #     if "src" in image.attrs:
    #         image_list.append(
    #             {
    #                 "url": url_for(
    #                     "library.book.chapter.images.show_image_by_index",
    #                     author=g.author.name,
    #                     title=g.title,
    #                     chapter_number=g.chapter.number,
    #                     language="english",
    #                     height=0,
    #                     image_index=image_index,
    #                 ),
    #                 "alt": image.get("alt", ""),
    #                 "index": image_index,
    #                 "filename": os.path.basename(image.attrs["src"]),
    #             }
    #         )
    #     else:
    #         log.info("No src, no image")
    #         log.info(image)

    # chapter_style_selector = styles_htmx.get_chapter_style_selector(g.chapter)

    # return render_template(
    #     "chapter.html",
    #     chapter_style_selector=chapter_style_selector,
    #     chapter_mood=htmx.chapter_mood_widget(g.chapter),
    #     chapter_theme=htmx.chapter_theme_widget(g.chapter),
    #     language="english",
    #     pretty_language="English",
    #     chapterurl=g.chapter.url,
    #     author=g.author,
    #     pretty_author=g.chapter.config.get("author", g.author),
    #     title=g.title,
    #     pretty_title=g.chapter.config.get("pretty_title", g.title),
    #     chapter=g.chapter,
    #     chapter_image_url=url_for(
    #         'library.chapter_cover',
    #         author=g.author.name,
    #         title=g.title,
    #         chapter_number=g.chapter.number,
    #     ),
    #     image_list=image_list,
    # )


@bp.route("/delta", methods=["POST"])
def save_delta():
    new_delta = request.form["delta"]

    with open(g.chapter.get_delta_fn(), "w", encoding="utf-8") as f:
        as_json = json.loads(new_delta)
        # pretty print it.
        f.write(json.dumps(as_json, indent=2))

    return "", 200

@bp.route("/title_delta", methods=["POST"])
def save_title_delta():
    new_delta = json.loads(request.form["delta"])
    g.chapter.save_title_delta(new_delta)
    return "", 200

@bp.route("/title_delta", methods=["GET"])
def get_title_delta():
    delta = g.chapter.load_title_delta()
    return delta, 200

# PUT /library/Aesop/Fables/31/english/set_chapter_style
@bp.route("/set_chapter_style", methods=["PUT"])
def set_chapter_style():
    new_style = request.form.get("chapter_style")
    g.chapter.set_chapter_style(new_style)

    return styles_htmx.get_chapter_style_selector(g.chapter)