import glob
import json
import os
import re
import html
import shutil

import flask
from artifact_editor.chapter import chapter
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

@bp.route("/update_phrase", methods=["PUT"])
def update_phrase():
    phrase_id = request.args.get("phrase_id")
    new_text = request.form.get("text")

    phrase = g.chapter.get_phrase(phrase_id)
    phrase.text = new_text
    g.chapter.save_phrases()

    return "", 200

@bp.route("")
@bp.route("/")
def chapter_base():
    from artifact_editor.phrases import htmx as phrases_htmx

    phrases = g.chapter.phrases()

    return render_template(
        "text.html",
        chapter_pdf_url = url_for(
            'library.book.chapter.typography.serve_chapter_pdf',
        ),
        phrases=phrases,
        speakers=g.chapter.speakers(),
        previous_phrase_index=-1,
        language=g.chapter.language,
        pretty_language=g.chapter.pretty_language,
        import_button=htmx.import_button(g.chapter),
        generate_missing_audio_button=phrases_htmx.generate_all_missing_audio_button(
            g.chapter, label="Generate Missing Audio"
        ),
        chapterurl=g.chapter.url,
        author=g.author,
        pretty_author=g.chapter.config.get("author", g.author),
        title=g.title,
        pretty_title=g.chapter.config.get("title", g.title),
        chapter_title=g.chapter.chapter_title,
        chapter=g.chapter,
        is_chapter=True,
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
            # when same is empty, we add our operation to it.  done.
            same.append(operation)
        else:
            # same is not empty
            if operation.get("attributes") == same[-1].get("attributes"):
                # if the attributes are the same, we can merge them together.
                same.append(operation)
            else:
                # The attributes are different.
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

    return compressed


@bp.route("/phrase_editor/<phrase_index>/", methods=["GET"])
def phrase_editor(phrase_index: int):
    """
    Replace the chosen phrase with the phrase editor
    Revert the previous phrase to the plain phrase viewer
    """
    phrase_index = int(phrase_index)
    from artifact_editor.phrases import htmx as phrases_htmx

    phrase_xml = g.chapter.get_phrase(phrase_index)
    phrase = chapter.Phrase(chapter=g.chapter, phrase_xml=phrase_xml)
    previous_phrase_index = int(request.args.get("previous_phrase_index", -1))
    
    previous_phrase_xml = g.chapter.get_phrase(previous_phrase_index)
    if previous_phrase_xml:
        previous_phrase = chapter.Phrase(chapter=g.chapter, phrase_xml=previous_phrase_xml)
        previous_delta = previous_phrase.delta
    else:
        previous_phrase = None
        previous_delta = "[]"
       
    # just a place for quilljs to attach itself.
    out = f"""
<div 
    id="phrase_{phrase.index}_ta" class="phrase-editor"
    hx-swap-oob="true"
>
<h1>Spot This</h1>
</div>
    """

    # replace the previous quilljs-enhanced editor with the 
    # original vanilla, so we only have one quill running instead of N
    # _and_ it makes it clear which one is "hot", and we can add other widgets to the 'hot' phrase.
    
    if previous_phrase:
        # we are not the users first choice, they picked previous_phrase_index before they
        # picked us.  So we fix previous_phrase and put it back in its inert state, but with
        # any edits reflected.

        # value = "[\n"
        # for op in json.loads(previous_delta):
        #     value += "    " + json.dumps(op, separators=(", ", ": ")) + ",\n"
        # value += "]"

        # value = json.dumps(previous_delta)

        out += f"""
<wa-textarea 
    id="phrase_{ previous_phrase_index }_ta" 
    class="phrase-editor"
    hx-get="{ url_for('library.book.chapter.phrase_editor', phrase_index=previous_phrase.index) }"
    hx-vals='js:{{"previous_phrase_index": window.previous_phrase_index}}'
    hx-trigger="click"
    hx-swap="none"
    hx-swap-oob="true"
    value='{ previous_phrase.simple_text() }'
></wa-textarea>
"""

    #phrase_delta = phrase.delta or "[]"
    #previous_simple_text = phrase.simple_text()
    #previous_phrase.attrs.get("simple_text", "")

    out += f"""
<div id="js-executor" hx-swap-oob="true">
    <script>
        document.querySelectorAll('#phrase-stack .phrase-container.selected').forEach(
            container => container.classList.remove('selected')
        );
        document.getElementById('phrase_{phrase.index}')?.classList.add('selected');

        // first things first.. remove every child of #phrase-stack that has a role="toolbar"
        clearToolbars('phrase-stack');
"""
    if previous_phrase:
        out += """
        // restore the value of the previously active editor
        fixPreviousEditor(`""" + previous_phrase.simple_text().replace("`", "\\`") + "`);"
        
    out += f"""
        // the new previous.
        window.previous_phrase_index = '{phrase_index}';
        phraseEditor = new Quill('#phrase_{phrase.index}_ta',
        {{
            modules: {{
                syntax: false, 
                toolbar: {{
                    container: [
                        [
                            {{ 'font': fontFamilyArr }}, 
                            {{ 'size': fontSizeArr }}
                        ], ['bold', 'italic'], [
                            {{ align: ''}}, 
                            {{ align: 'center' }}, 
                            {{ align: 'justify' }}
                        ],
                    ]
                }}
            }}, 
            theme: 'snow'
        }}); 
        
        //wish I was kidding
        // 1. Get a reference to Quill's internal tooltip input element
        //const tooltipInput = text_editor_title.theme.tooltip.root.querySelector('input[data-link]');
        //const tooltipInput = null;

        // 2. Overwrite the default strings with whatever you want!
        //if (tooltipInput) 
        //    tooltipInput.dataset.link = 'https://localhost/tooltips';
        //    tooltipInput.dataset.video = ''; // 'Paste video URL here...';
        //    tooltipInput.dataset.formula = 'e = mc^2';
        //
        var delta = {phrase.delta} || [];
        phraseEditor.setContents(delta);
        phraseEditor.on('text-change', function(delta, oldDelta, source)
        {{
            htmx.ajax('POST', 'phrase_delta/' + delta.index + '/',
            {{
                headers: {{
                    'Content-Type': 'application/x-www-form-urlencoded'
                }}, values: {{
                    delta: JSON.stringify(phraseEditor.getContents()),
                }}, swap: 'none', onError: function (err) {{
                    console.error('Error updating:', err);
                }}
            }});
        }});
    </script>
</div>
"""

    # Only populate controls for the active phrase. Clear the prior phrase's
    # controls as its rich editor is returned to the lightweight state.
    if previous_phrase and previous_phrase_index != phrase_index:
        out += f"""
<div id="chapter-audio-controls-{previous_phrase_index}"
    class="wa-stack wa-gap-xs" hx-swap-oob="true"></div>
"""
    out += phrases_htmx.chapter_audio_controls(g.chapter, phrase_xml, oob=True)

    return out, 200



@bp.route("/import_chapter_small_phrase", methods=["POST"])
def import_chapter_small_phrase():
    """
    Archiving a bit, this is tuned for many small phrases, but that isn't
    the design anymore, and it's baked into the algorithm.

    New algorithm based on scoring every word based on how good of a phrase
    break it is and how suitable it is as a new image.
    """
    # clean slate.
    chapter_soup = BeautifulSoup("<chapter></chapter>", "xml")

    # load_delta knows to fallback to chapter.txt with a thin wrapper if there
    # is no delta file.
    delta = g.chapter.load_delta()

    # The title and author at the beginning of the chapter are a pain in the
    # ass. They have to be special, because if we're rendering a single chapter
    # as a video we want to include and speak both the book and author.  If
    # we're chapter 4 of 20 in a six hour video, we don't want to waste a moment
    # with tedious repetition of the title and author.
    
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
            # ie: paragraph.
            

            # # Regex pattern to match anything between double quotes or smart quotes
            # quote_pattern = re.compile(r'(["“][^"”]*["”])')

            # # Find matches and convert them to spaCy Spans
            # for match in quote_pattern.finditer(doc.text):
            #     start, end = match.span()
            #     # Align the character indices with spaCy token boundaries
            #     span = doc.char_span(start, end)
            #     if span is not None:
            #         print(f"Found quote: {span.text}")
            # Pattern to capture text between standard quotes, smart quotes, or single quotes
            quote_pattern = re.compile(r'["“\'‘]([^"”\'’]+)["”\'’]')

            # Find all matches and extract their start and end character positions
            broken = []
            for match in quote_pattern.finditer(major_break):
                start_idx = match.start()
                end_idx = match.end()
                broken.append((start_idx, end_idx))

            log.info('major_break: %s', repr(major_break))
            log.info('broken: %s', repr(broken))

            if not broken:
                broken = [(0, len(major_break))]
            
            for start_idx, end_idx in broken:
                log.info("Broken span: start=%s, end=%s", start_idx, end_idx)
                doc = nlp(major_break[start_idx:end_idx])

                sentence_spans = list(doc.sents)
                sentence_count = len(sentence_spans)
                log.info("Operation has %s sentences", sentence_count)
                for sentence_index, sentence_span in enumerate(sentence_spans, index_start):
                    log.info("[%s] sentence_span: %s", sentence_index, repr(sentence_span))
                    log.info("[%s] sentence_span.text_with_ws: %s", sentence_index, repr(sentence_span.text_with_ws))
                    log.info("[%s] sentence_span.text: %s", sentence_index, repr(sentence_span.text))
                    sentence = sentence_span.text_with_ws

                    if sentence[0] in [",", ".", "!", "?", ";", ":"]:
                        # the sentence begins with punctuation.
                        # we can't move it to the end of the previous because it's here because
                        # the formatting has changed.  Not a problem exactly, we just
                        # have to fix the trailing whitespace on the previous opt_ops.
                        
                        # if the previous operation ended with a space, remove it
                        # we . dont want , this .
                        if out_ops[-1]["insert"][-1] == " ":
                            out_ops[-1]["insert"] = out_ops[-1]["insert"][:-1]

                    log.info("sentence: %s", repr(sentence))
                    for word in re.split(r"([ \n])", sentence):
                        # tape the whitespace dribble to the previous
                        if out_ops and word in [" ", "\n"]:
                            out_ops[-1]["insert"] += word
                            continue

                        log.info('word: %s', repr(word))
                        # is this the sort of word we would consider a better
                        # than random choice for a place to split the audio?
                        if word.strip():
                            out_ops.append(
                                {
                                    "insert": word,
                                    "attributes": ops.get("attributes", {}),
                                    "type": "word",
                                    # punctuation that isn't at the end of a
                                    # sentence is a weak phrase break, way
                                    # better than nothing.  we're establishing
                                    # an initial 0/1 score for each word, if it
                                    # ends the right kind of punctuation.
                                    "phrase_score": 1
                                    if word[-1] in [".", "!", "?", ";", ":", ","]
                                    else 0,
                                }
                            )
                        elif word:
                            # whatever it is, it doesn't survive a strip()
                            out_ops.append(
                                {
                                    "insert": word,
                                    "attributes": ops.get("attributes", {}),
                                    "type": "whitespace",
                                    "phrase_score": 0,
                                }
                            )

                    if out_ops[-1]["insert"].strip():
                        # a phrase ending with punctuation?
                        # different score based on which punctuation.
                        try:
                            out_ops[-1]["phrase_score"] += {
                                ".": 5,
                                "!": 5,
                                "?": 5,
                                ";": 3,
                                ":": 3,
                                ",": 1,
                                '"': 5,
                            }[out_ops[-1]["insert"].strip()[-1]]
                        except KeyError:
                            pass

    index = index_start
    phrases = []
    log.info('Iterating index from %s to %s', index, len(out_ops))
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

        # we've identified one "phrase" in the source material.
        # we typeset each phrase independently.  this is messy.
        log.info("Processing out_ops[%d:%d] for typesetting...", index, break_index + 1)
        our_ops = out_ops[index : break_index + 1]

        doc = t.ops_to_internal_representation(our_ops)
        as_latex = doc.render_tree(mode='latex')

        # render_tree gets a lot of things right.  It also
        # makes some mistakes.  Lets fix those.
        as_latex = as_latex.replace("\\end{center}", "\\end{center}\n\\vspace{-3.5em}")

        # clear any \emph{} that is empty, it adds vertical space.
        as_latex = as_latex.replace(r"\emph{}", "")
        # ditto for empty textbf{} blocks.
        as_latex = as_latex.replace(r"\textbf{}", "")

        # how wide should a tab be? 2em looks about right to me.
        as_latex = as_latex.replace("\t", r"\hspace*{2em}")

        # this one is very puzzling to me, using '' for all " is wrong
        # latex wants `` for opening quotes and '' for closing quotes.
        # we can either use " and let luaquotes do the work, or do it ourselves.
        
        # these should be right in most cases, but there are likely others I haven't considered.
        as_latex = as_latex.replace(" ''", " ``")
        as_latex = as_latex.replace("\n''", "\n``")
        as_latex = as_latex.replace("(''", "(``")

        if as_latex.startswith("''"):
            as_latex = "``" + as_latex[2:]

        # this section is buggy
        # grab a copy of our end-of-phrase trailing whitespace
        trailing_whitespace = our_ops[-1]["insert"][len(our_ops[-1]["insert"].rstrip()) :]
       
        # add a clean copy of our trailing whitespace to the end of our phrase latex
        as_latex = as_latex.strip() + trailing_whitespace

        # then glue our_ops together to get plain_phrase (what is spoken)
        plain_phrase = "".join([o["insert"] for o in our_ops])
       
        phrases.append({"spoken": plain_phrase, "latex": as_latex, 'delta': our_ops})

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

        # header block
        title_paragraph = chapter_soup.new_tag("paragraph")
        title_paragraph.attrs["tags"] = "has-text=false,spoken-only=true"

        title_image = chapter_soup.new_tag("image")
        title_image.attrs["prompt"] = f"Elegant book cover for {g.chapter.title} by {g.chapter.author.name} in an environment that represents the themes of the book."
        title_image.attrs["workflow_image_template"] = const.DEFAULT_IMAGE_TEMPLATE

        title_phrase = chapter_soup.new_tag("phrase")
        title_phrase.string = f"{g.chapter.title}\n\nby {g.chapter.author.name}"
        title_phrase.attrs["speaker"] = "Narrator"
        title_phrase.attrs["index"] = "0"
        title_phrase.attrs["delta"] = json.dumps(compress_ops(g.chapter.load_title_delta()["ops"]))

        title_paragraph.append(title_image)
        title_paragraph.append(title_phrase)

        chapter_xml.append(title_paragraph)

        paragraph = chapter_soup.new_tag("paragraph")

        rainbow_series = []

        initial_index = len(chapter_soup.find_all("phrase"))
        first = True
        for index, p in enumerate(phrases, initial_index):
            phrase = chapter_soup.new_tag("phrase")
            phrase.string = p["spoken"]
            phrase.attrs["index"] = index
            phrase.attrs["delta"] = json.dumps(compress_ops(p["delta"]))

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
            if first or phrase.attrs["latex"].lstrip(" ").startswith(r"\n\n"):
                new_paragraph = chapter_soup.new_tag("paragraph")
                new_paragraph.append(phrase)
                chapter_xml.append(new_paragraph)
                paragraph = new_paragraph
                first = False
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

            # # escapes
            # if "$" in line:
            #     line = line.replace("$", r"\$")

            all_lines.append(line)

        all_rainbow_lines = []
        for line in latex_rainbow_block.splitlines(keepends=True):
            if line.strip() and all_rainbow_lines and all_rainbow_lines[-1].strip():
                # there is a non-blank line after a non-blank line.  We need to add a \\
                all_rainbow_lines[-1] = all_rainbow_lines[-1].rstrip() + r"\\" + "\n"

            # escapes
            # if "$" in line:
            #     line = line.replace("$", r"\$")

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
        rainbow_h.write(as_latex)

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




@bp.route("/import_chapter", methods=["POST"])
def import_chapter():
    """
    New algorithm based on scoring every word based on how good of a phrase
    break it is and how suitable it is as a new image.
    """
    # clean slate.
    chapter_soup = BeautifulSoup("<chapter></chapter>", "xml")

    # load_delta knows to fallback to chapter.txt with a thin wrapper if there
    # is no delta file.
    delta = g.chapter.load_delta()

    # The title and author at the beginning of the chapter are a pain in the
    # ass. They have to be special, because if we're rendering a single chapter
    # as a video we want to include and speak both the book and author.  If
    # we're chapter 4 of 20 in a six hour video, we don't want to waste a moment
    # with tedious repetition of the title and author.
    
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
            # ie: paragraph.
            # # Regex pattern to match anything between double quotes or smart quotes
            # quote_pattern = re.compile(r'(["“][^"”]*["”])')

            # # Find matches and convert them to spaCy Spans
            # for match in quote_pattern.finditer(doc.text):
            #     start, end = match.span()
            #     # Align the character indices with spaCy token boundaries
            #     span = doc.char_span(start, end)
            #     if span is not None:
            #         print(f"Found quote: {span.text}")
            # Pattern to capture text between standard quotes, smart quotes, or single quotes
            # (moron.. not nearly good enough. "that's showing them".  doofus.)

            double_quote_pattern = re.compile(r'["“]([^"”]+)["”]')
            # single_quote_pattern = re.compile(r'[\'‘]([^\'’]+)[\'’]')

            # Find all matches and extract their start and end character positions
            broken = []
            float_index = 0
            for match in double_quote_pattern.finditer(major_break):
                start_idx = match.start()
                end_idx = match.end()
                if start_idx > float_index:
                    broken.append((float_index, start_idx))

                broken.append((start_idx, end_idx))
                float_index = end_idx

            if float_index < len(major_break):
                broken.append((float_index, len(major_break)))

            log.info('major_break: %s', repr(major_break))
            log.info('broken: %s', repr(broken))

            if not broken:
                broken = [(0, len(major_break))]
            
            for start_idx, end_idx in broken:
                log.info("Broken span: start=%s, end=%s", start_idx, end_idx)
                doc = nlp(major_break[start_idx:end_idx])

                sentence_spans = list(doc.sents)
                sentence_count = len(sentence_spans)
                log.info("Operation has %s sentences", sentence_count)
                for sentence_index, sentence_span in enumerate(sentence_spans, index_start):
                    log.info("[%s] sentence_span: %s", sentence_index, repr(sentence_span))
                    log.info("[%s] sentence_span.text_with_ws: %s", sentence_index, repr(sentence_span.text_with_ws))
                    log.info("[%s] sentence_span.text: %s", sentence_index, repr(sentence_span.text))
                    sentence = sentence_span.text_with_ws

                    if sentence[0] in [",", ".", "!", "?", ";", ":"]:
                        # the sentence begins with punctuation.
                        # we can't move it to the end of the previous because it's here because
                        # the formatting has changed.  Not a problem exactly, we just
                        # have to fix the trailing whitespace on the previous opt_ops.
                        
                        # if the previous operation ended with a space, remove it
                        # we . dont want , this .
                        if out_ops[-1]["insert"][-1] == " ":
                            out_ops[-1]["insert"] = out_ops[-1]["insert"][:-1]

                    log.info("sentence: %s", repr(sentence))
                    for word in re.split(r"([ \n])", sentence):
                        # tape the whitespace dribble to the previous
                        if out_ops and word in [" ", "\n"]:
                            out_ops[-1]["insert"] += word
                            continue

                        log.info('word: %s', repr(word))
                        # is this the sort of word we would consider a better
                        # than random choice for a place to split the audio?
                        if word.strip():
                            out_ops.append(
                                {
                                    "insert": word,
                                    "attributes": ops.get("attributes", {}),
                                    "type": "word",
                                    # punctuation that isn't at the end of a
                                    # sentence is a weak phrase break, way
                                    # better than nothing.  we're establishing
                                    # an initial 0/1 score for each word, if it
                                    # ends the right kind of punctuation.
                                    "phrase_score": 1
                                    if word[-1] in [".", "!", "?", ";", ":", ","]
                                    else 0,
                                }
                            )
                        elif word:
                            # whatever it is, it doesn't survive a strip()
                            out_ops.append(
                                {
                                    "insert": word,
                                    "attributes": ops.get("attributes", {}),
                                    "type": "whitespace",
                                    "phrase_score": 0,
                                }
                            )

                    if out_ops[-1]["insert"].strip():
                        # a phrase ending with punctuation?
                        # different score based on which punctuation.
                        try:
                            out_ops[-1]["phrase_score"] += {
                                ".": 5,
                                "!": 5,
                                "?": 5,
                                ";": 3,
                                ":": 3,
                                ",": 1,
                                '"': 5,
                                '”': 5,
                                '\n': 8
                            }[out_ops[-1]["insert"][-1]]
                        except KeyError:
                            pass

    index = index_start
    phrases = []
    log.info('Iterating index from %s to %s', index, len(out_ops))
    while index < len(out_ops):
        # for NEW phrases -- we want the segment of the text that the TTS engine
        # will be able to effectively read.  We are entirely disconnected from 
        # highlighting, pure vocal quality.

        # we will use a WINDOW_SIZE word evaluation window that resets whenever we
        # choose a phrase break point.

        window_size = 8  # _words_ not characters, this how many words away is the bare minimum.

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
                    log.debug("Reached end of out_ops without finding a phrase break.")
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

        # we've identified one "phrase" in the source material.
        # we typeset each phrase independently.  this is messy.
        log.info("Processing out_ops[%d:%d] for typesetting...", index, break_index + 1)
        our_ops = out_ops[index : break_index + 1]

        doc = t.ops_to_internal_representation(our_ops)
        as_latex = doc.render_tree(mode='latex')

        # render_tree gets a lot of things right.  It also
        # makes some mistakes.  Lets fix those.
        as_latex = as_latex.replace("\\end{center}", "\\end{center}\n\\vspace{-3.5em}")

        # clear any \emph{} that is empty, it adds vertical space.
        as_latex = as_latex.replace(r"\emph{}", "")
        # ditto for empty textbf{} blocks.
        as_latex = as_latex.replace(r"\textbf{}", "")

        # how wide should a tab be? 2em looks about right to me.
        as_latex = as_latex.replace("\t", r"\hspace*{2em}")

        # this one is very puzzling to me, using '' for all " is wrong
        # latex wants `` for opening quotes and '' for closing quotes.
        # we can either use " and let luaquotes do the work, or do it ourselves.
        
        # these should be right in most cases, but there are likely others I haven't considered.
        as_latex = as_latex.replace(" ''", " ``")
        as_latex = as_latex.replace("\n''", "\n``")
        as_latex = as_latex.replace("(''", "(``")

        if as_latex.startswith("''"):
            as_latex = "``" + as_latex[2:]

        # this section is buggy
        # grab a copy of our end-of-phrase trailing whitespace
        trailing_whitespace = our_ops[-1]["insert"][len(our_ops[-1]["insert"].rstrip()) :]
       
        # add a clean copy of our trailing whitespace to the end of our phrase latex
        as_latex = as_latex.strip() + trailing_whitespace

        # then glue our_ops together to get plain_phrase (what is spoken)
        plain_phrase = "".join([o["insert"] for o in our_ops])
       
        phrases.append({"spoken": plain_phrase, "latex": as_latex, 'delta': our_ops})

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

        # header block
        title_paragraph = chapter_soup.new_tag("paragraph")
        title_paragraph.attrs["tags"] = "has-text=false,spoken-only=true"

        title_image = chapter_soup.new_tag("image")
        title_image.attrs["prompt"] = f"Elegant book cover for {g.chapter.title} by {g.chapter.author.name} in an environment that represents the themes of the book."
        title_image.attrs["workflow_image_template"] = const.DEFAULT_IMAGE_TEMPLATE

        # title_phrase = chapter_soup.new_tag("phrase")
        # title_phrase.string = f"{g.chapter.title}\n\nby {g.chapter.author.name}"
        # title_phrase.attrs["speaker"] = "Narrator"
        # title_phrase.attrs["index"] = "0"
        # title_phrase.attrs["delta"] = json.dumps(compress_ops(g.chapter.load_title_delta()["ops"]))

        # title_paragraph.append(title_image)
        # title_paragraph.append(title_phrase)

        chapter_xml.append(title_paragraph)

        paragraph = chapter_soup.new_tag("paragraph")

        rainbow_series = []

        initial_index = len(chapter_soup.find_all("phrase"))
        first = True
        for index, p in enumerate(phrases, initial_index):
            phrase = chapter_soup.new_tag("phrase")
            phrase.string = p["spoken"]
            phrase.attrs["index"] = index
            phrase.attrs["delta"] = json.dumps(compress_ops(p["delta"]))

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
            if first or phrase.attrs["latex"].lstrip(" ").startswith(r"\n\n"):
                new_paragraph = chapter_soup.new_tag("paragraph")
                new_paragraph.append(phrase)
                chapter_xml.append(new_paragraph)
                paragraph = new_paragraph
                first = False
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

            # # escapes
            # if "$" in line:
            #     line = line.replace("$", r"\$")

            all_lines.append(line)

        all_rainbow_lines = []
        for line in latex_rainbow_block.splitlines(keepends=True):
            if line.strip() and all_rainbow_lines and all_rainbow_lines[-1].strip():
                # there is a non-blank line after a non-blank line.  We need to add a \\
                all_rainbow_lines[-1] = all_rainbow_lines[-1].rstrip() + r"\\" + "\n"

            # escapes
            # if "$" in line:
            #     line = line.replace("$", r"\$")

            all_rainbow_lines.append(line)

        h.write(header % (""))
        rainbow_h.write(
            header % ("\n".join(rainbow_series))
        )

        # now write the header, in latex format.
        title_delta = g.chapter.load_title_delta()
        # t = TranslatorQuillJS()
        doc = t.ops_to_internal_representation([])
        # title_delta["ops"])
        as_latex = doc.render_tree(mode='latex')
        # yeah, cause this isn't fragile as shit.
        as_latex = as_latex.replace("\\end{center}", "\\end{center}\n\\vspace{-3.5em}")
        as_latex += "\n\\vspace{3.5em}"

        h.write(as_latex)
        rainbow_h.write(as_latex)

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
