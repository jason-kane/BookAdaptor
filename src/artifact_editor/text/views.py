import html
import json
import os
import re
import const
import nltk
import spacy
import shutil
from collections.abc import Iterable

from bs4 import BeautifulSoup
from flask import (
    Blueprint,
    make_response,
    redirect,
    render_template,
    request,
)
from titlecase import titlecase

import logger
import roman
from artifact_editor import config
from artifact_editor.author.author import Author
from artifact_editor.chapter.chapter import Chapter
from artifact_editor.characters import characters
from artifact_editor.tools import (
    get_chapterdir,
    get_chapterurl,
)

from . import (
    htmx,
    text,
)

log = logger.log(__name__)

bp = Blueprint(
    'text', 
    __name__,
    template_folder=os.path.join(
        os.path.dirname(__file__),
        "templates"
    )
)


# Boolean toggles available for all books.
TOGGLEFIELDS = []
# # TOGGLEFIELDS = [
#     'HAS_NUMBERED_CHAPTERS',
#     'HAS_CHAPTER_TITLES',
#     'HAS_ROMAN_NUMERAL_CHAPTERS',
#     'HAS_ALLCAPS_BREAKS',
#     'CALL_THEM_BOOKS',
#     'CALL_THEM_CHAPTERS',
#     'BREAK_INTO_CHAPTERS',
# ]

def extract_title(delta, chapter, chapter_xml):
    """
    Extract the title from the delta and add it to the chapter_xml.
    We're stripping the title out of the delta.
    """
    ops = []
    for operation in delta["ops"]:
        if "insert" in operation:
            insert_list = []
            for line in operation["insert"].split("\n"):
                if line.strip().upper() == chapter.title.upper():
                    log.info('Found title page: %s', line)
                    add_title(chapter_xml, chapter)
                    continue
            
                insert_list.append(line)
            operation["insert"] = "\n".join(insert_list)
        ops.append(operation)

    return {"ops": ops}

def extract_author(delta, chapter, chapter_xml):
    """
    Extract the author from the delta and add it to the chapter_xml.
    We're stripping the author out of the delta.
    """
    ops = []
    for operation in delta["ops"]:
        if "insert" in operation:
            insert_list = []
            for line in operation["insert"].split("\n"):
                if line.strip().upper() in [
                    f"BY {chapter.author.name.upper()}",
                    f"{chapter.author.name.upper()}"
                ]:
                    log.info('Found author page: %s', line)
                    add_author(chapter_xml, chapter)
                    continue
            
                insert_list.append(line)
            operation["insert"] = "\n".join(insert_list)
        ops.append(operation)

    return {"ops": ops}


def lists_that_do_not_contain_lists(lists):
    """
    Given a list of lists, return a list of lists that do not contain any
    sublists.  ie: flatten the tree to the leaves.
    """
    for item in lists:
        if isinstance(item, list):
            if all(not isinstance(subitem, list) for subitem in item):
                yield item
            else:
                yield from lists_that_do_not_contain_lists(item)
        else:
            yield item


@bp.route("/import_chapter", methods=["POST"])
def import_chapter(author, title, chapter_number, language):
    """
    New algorithm based on scoring every word based on how good of a phrase
    break it is and how suitable it is as a new image.
    """
    author = Author(author)

    chapter = Chapter(
        author=author,
        title=title,
        number=chapter_number,
        language=language
    )

    chapter_soup = BeautifulSoup("<chapter></chapter>", "xml")

    delta = chapter.load_delta()
    
    # we want to be able to slice-and-dice the plain text and the formatted
    # text in parallel.  It's stupid, but I think it might actually be
    # easiest to leave it in Delta format for a little longer than I had
    # originally intended.

    # if there is a title, add a title page
    delta = extract_title(delta, chapter, chapter_soup)
    delta = extract_author(delta, chapter, chapter_soup)
   
    #nltk.download('punkt_tab')
    spacy.prefer_gpu()
    try:
        nlp = spacy.load("en_core_web_sm")
    except OSError:
        log.info('Downloading spacy model en_core_web_sm...')
        import subprocess
        subprocess.run(["python", "-m", "spacy", "download", "en_core_web_sm", "--break-system-packages"])
        nlp = spacy.load("en_core_web_sm")

    # break all the 'inserts' in delta up into single delta sentences.
    out_ops = []
    for ops in delta["ops"]:
        #for sentence in nltk.sent_tokenize(ops.get("insert", "")):
        log.info('Processing ops: %s', repr(ops))

        sentence_spans = list(nlp(ops.get("insert", "")).sents)
        sentence_count = len(sentence_spans)
        log.info('Operation has %s sentences', sentence_count)
        for sentence_index, sentence_span in enumerate(sentence_spans):
            log.info('sentence_span: %s', repr(sentence_span))
            log.info('sentence_span.text_with_ws: %s', repr(sentence_span.text_with_ws))
            log.info('sentence_span.text: %s', repr(sentence_span.text))
            sentence = sentence_span.text_with_ws

            # ARGG!! spacy still fucks up trailing newlines.
            # we _need_ the trailing whitespace.
            # is this the last sentence?
            if sentence_index == sentence_count - 1:
                log.info('%s == %s', sentence_index, sentence_count - 1)
                whitespace = ops["insert"][len(ops["insert"].rstrip()):]
                log.info('whitespace: %s', repr(whitespace))
                sentence += whitespace
            else:
                log.info('%s != %s', sentence_index, sentence_count)

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

            log.info('sentence: %s', repr(sentence))
            for word in sentence.split(' '):
                log.info('word: %s', repr(word))
                if word.strip():
                    out_ops.append({
                        "insert": word + " ",
                        "attributes": ops.get("attributes", {}),
                        "type": "word",
                        # punctuation that isn't at the end of a sentence is a weak
                        # phrase break, way better than nothing.
                        "phrase_score": 1 if word[-1] in [".", "!", "?", ";", ":", ","] else 0
                    })
                else:
                    out_ops.append({
                        "insert": word,
                        "attributes": ops.get("attributes", {}),
                        "type": "whitespace",
                        "phrase_score": 0
                    })
            
            if out_ops[-1]["insert"].strip():
                try:
                    out_ops[-1]["phrase_score"] += {
                        ".": 5,
                        "!": 5,
                        "?": 5,
                        ";": 3,
                        ":": 3,
                        ",": 1
                    }[out_ops[-1]["insert"].strip()[-1]]
                except KeyError:
                    pass

    index = 0
    attributes = {}
    phrases = []
    while index < len(out_ops):
        # for phrases -- the visual highlighted text, we want each section
        # to be small enough to fit the readers eye.  Less than 10 words,
        # with a strong preference for ending on a sentence break or strong
        # punctuation mark.

        # This is also the unit of TTS.  Its much easier for the TTS to
        # sound good when it gets a complete sentence.

        # we will use a WINDOW_SIZE word evaluation window that resets whenever we
        # choose a phrase break point.
        
        WINDOW_SIZE = 20  # 25 is too big

        break_index = None
        while break_index is None and WINDOW_SIZE < 30:
            highest = 0
            
            for i in range(index, min(index + WINDOW_SIZE, len(out_ops))):
                if out_ops[i].get("phrase_score", 0) > highest:
                    highest = out_ops[i]["phrase_score"]
                    break_index = i
            
            if break_index is None:
                log.info('No phrase break found in window size %d, reducing window size...', WINDOW_SIZE)
                WINDOW_SIZE += 2

        if break_index is None:
            log.info('!FAILURE! No phrase break found!')
            p = out_ops[index:min(index + WINDOW_SIZE, len(out_ops))]
            log.info('p: %s', p)
            log.info('phrase: %s', " ".join([o["insert"] for o in p]))

            raise ValueError("No phrase break found!")

        plain_phrase = "".join([out_ops[i]["insert"] for i in range(index, break_index + 1)])
        # log.info(' Spoken phrase: %s', plain_phrase)

        typeset_phrase = []
        log.info('Processing out_ops[%d:%d] for typesetting...', index, break_index + 1)
        for o in out_ops[index:break_index + 1]:
            if "\t" in o["insert"]:
                log.info('Replacing tab with \hspace*{2em} in phrase: %s', o["insert"])
                o["insert"] = o["insert"].replace("\t", r"\hspace*{2em}")

            # That I have to treat this as a special edge case means my
            # algorithm is shit.  Adding more features is going to be a
            # nightmare until I redesign to handle arbitrary combinations of
            # attributes.
            if (
                o["attributes"].get("italic") and 
                attributes.get("italic") is None and
                o["attributes"].get("bold") and
                attributes.get("bold") is None
            ):
                # bold _and_ italics.
                typeset_phrase.append(r"\textit{\textbf{" + o['insert'])
                attributes["italic"] = True
                attributes["bold"] = True
                continue

            if o["attributes"].get("italic") and attributes.get("italic") is None:
                # we are starting an italic block
                typeset_phrase.append(r"\textit{" + o['insert'])
                attributes["italic"] = True
                continue
            
            if o["attributes"].get("italic") is None and attributes.get("italic"):
                # we are ending an italic block
                if typeset_phrase:
                    # retain the trailing whitespace, _after_ the close tag.
                    stripped = typeset_phrase[-1].rstrip()
                    trailing_whitespace = typeset_phrase[-1][len(stripped):]
                    typeset_phrase[-1] = stripped + "}" + trailing_whitespace
                else:
                    phrases[-1]["latex"] += "}"
                attributes["italic"] = None
            
            if o["attributes"].get("bold") and attributes.get("bold") is None:
                # we are starting a bold block
                typeset_phrase.append(r"\textbf{" + o['insert'])
                attributes["bold"] = True
                continue
            
            if o["attributes"].get("bold") is None and attributes.get("bold"):
                # how many characters long was the previous line 
                # prior to the trailing whitespace?
                if typeset_phrase:
                    stripped = typeset_phrase[-1].rstrip()
                    trailing_whitespace = typeset_phrase[-1][len(stripped):]
                    # tuck the } close brace between the string and its trailing whitespace.
                    typeset_phrase[-1] = stripped + "}" + trailing_whitespace
                    # we aren't bold anymore.
                else:
                    #  we went bold in a previous phrase.
                    stripped = phrases[-1]["latex"].rstrip()
                    trailing_whitespace = phrases[-1]["latex"][len(stripped):]
                    phrases[-1]["latex"] = stripped + "}" + trailing_whitespace

                attributes["bold"] = None

            typeset_phrase.append(o["insert"])

        phrases.append({
            "spoken": plain_phrase,
            "latex": "".join(typeset_phrase)
        })

        index = break_index + 1

    chapter_xml = chapter_soup.find('chapter')

    with open(
        os.path.join(
            const.LIBRARY_DIR,
            chapter.get_chapterdir(),
            chapter.language,
            "chapter.latex"
        ), "w") as h, open(os.path.join(
            const.LIBRARY_DIR,
            chapter.get_chapterdir(),
            chapter.language,
            "chapter.rainbow.latex"
        ), "w") as rainbow_h:
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
\setlength{\droptitle}{-45pt}
\posttitle{\par\end{center}}
\title{%s}
\author{%s}
\date{}
\maketitle
\vspace{-0.5in}
\tolerance=9999
\hyphenpenalty=10000
\exhyphenpenalty=100                    
"""

            latex_block = ""
            latex_rainbow_block = ""
            paragraph = chapter_soup.new_tag("paragraph")
            
            rainbow_series = []
            
            initial_index = len(chapter_soup.find_all('phrase'))

            for index, p in enumerate(phrases, initial_index):
                phrase = chapter_soup.new_tag("phrase")
                phrase.string = p["spoken"]
                phrase.attrs['index'] = index

                # limit to 24 bits, 10 offset to keep us away from pure black
                rainbow_int = (index + 10) % 16777216
                phrase.attrs['latex'] = p["latex"].replace("\n", r"\n")
                
                prior_whitespace = p['latex'][:len(p['latex']) - len(p['latex'].lstrip())]
                post_whitespace = p['latex'][len(p['latex'].rstrip()):]
                p["latex_rainbow"] = f"{prior_whitespace}\\color{{B{rainbow_int:X}}}\\highLight[B{rainbow_int:X}]{{{p['latex'].strip()}}}{post_whitespace}"
                phrase.attrs['latex_rainbow'] = p["latex_rainbow"].replace("\n", r"\n")

                r, g, b = rainbow_int.to_bytes(3, 'big')
                rainbow_series.append(f"\\definecolor{{B{rainbow_int:X}}}{{RGB}}{{{r},{g},{b}}}")
                
                latex_block += p["latex"]
                latex_rainbow_block += p['latex_rainbow']

                # double newline == paragraph break.
                if phrase.attrs['latex'].lstrip(" ").startswith(r"\n\n"):
                    new_paragraph = chapter_soup.new_tag("paragraph")
                    new_paragraph.append(phrase)                   
                    chapter_xml.append(new_paragraph)
                    paragraph = new_paragraph
                elif phrase.attrs['latex'].rstrip(" ").endswith(r"\n\n"):
                    paragraph.append(phrase)
                    new_paragraph = chapter_soup.new_tag("paragraph")
                    chapter_xml.append(new_paragraph)
                    paragraph = new_paragraph                    
                else:
                    log.info('s: %s', p['spoken'])
                    paragraph.append(phrase)

            # chapter_xml.append(paragraph)

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

            h.write(header % ("", title, author.pretty_name))
            rainbow_h.write(header % ("\n".join(rainbow_series), title, author.pretty_name))

            h.write("".join(all_lines))
            rainbow_h.write("".join(all_rainbow_lines))

            h.write(r"""\end{document}""")
            rainbow_h.write(r"""\end{document}""")
            
    delta["ops"] = out_ops
    chapter.soup = chapter_xml
    chapter.save_xml()

    return "", 200


def shatter_delta(delta, target=25, recurse_depth=0):
    # delta is a {'ops': [{'insert': ''}, ...]} sort of object. we want to find
    # the list element in the middle, based on the absolute length of the
    # 'insert' strings.  So.. first we're going to decorate all those inner
    # dicts with the lengths of their insert strings.  Easy.  We'll get a total
    # as long as we're iterating anyway.

    log.info("[%d] Breaking delta (%d ops) into two pieces...", recurse_depth, len(delta["ops"]))
    if len(delta["ops"]) == 2:
        log.info('[%d] Quick Shatter: Two ops, returning them as-is', recurse_depth)
        return [delta["ops"][0], delta["ops"][1]]

    elif len(delta["ops"]) == 1:
        log.info('[%d] Quick Shatter: One op, returning it as-is', recurse_depth)
        return [delta["ops"][0], None]

    total = 0
    if recurse_depth > 0:
        for operation in delta["ops"]:
            total += operation["length"]
    else:
        for operation in delta["ops"]:
            if "insert" in operation:
                operation["length"] = len(operation["insert"].split())
                total += operation["length"]
            else:
                operation["length"] = 0

    middle = total // 2
    log.info('shatter_delta: total %d, middle %d, target %d', total, middle, target)

    first = []
    first_length = 0
    
    second = []
    second_length = 0

    first_operation = True
    for operation in delta["ops"]:
        log.info('Decreasing middle %d by operation length %d', middle, operation["length"])
        middle -= operation["length"]
        if first_operation or middle > 0:
            log.info('Adding operation to first half')
            first.append(operation)
            first_length += operation["length"]
            first_operation = False
        else:
            log.info('Adding operation to second half')
            second.append(operation)
            second_length += operation["length"]
    
    # if first_length == 0 or second_length == 0:
    #     # we aren't splitting anymore.
    #     log.info('fully shattered, returning [%d, %d]', first_length, second_length)
    #     return [first, second]

    if first_length > target:
        log.info('RECURSING: first_length %d > target %d, shattering first', first_length, target)
        first = shatter_delta({"ops": first}, target=target, recurse_depth=recurse_depth+1)
    
    if second_length > target:
        log.info('RECURSING: second_length %d > target %d, shattering second', second_length, target)
        second = shatter_delta({"ops": second}, target=target, recurse_depth=recurse_depth+1)

    # this will be a binary tree of dicts, with 'ops' lists less than or equal to target in size at the leaves.
    # we can walk the tree to build our phrases.
    log.info('Broke %d entries into [%d, %d]', total, first_length, second_length)
    return [first, second]


def add_title(chapter_xml, chapter):
    title_page = chapter_xml.new_tag("paragraph")
    title_page.attrs["tags"] = "has-text=false,spoken-only=true"
    title_page.attrs["fullscreen"] = "true"
    chapter_xml.find('chapter').append(title_page)

    title_image = chapter_xml.new_tag("image")
    if "subtitle" in chapter.kwargs:
        title_prompt = (
            "The front cover of a detailed carved and "
            "painted cover of a masterfully crafted "
            "leather-bound handmade special edition of"
            f" \"{chapter.title} - {chapter.subtitle}\" by \"{chapter.author.name}\"."
        )
    else:
        title_prompt = (
            #f"The front of a record albumn called {title} by {author}"
            "The front cover of a detailed carved and "
            "painted cover of a masterfully crafted "
            "leather-bound handmade special edition of"
            f" \"{chapter.title}\" by \"{chapter.author.name}\"."
        )

    title_image.attrs["prompt"] = title_prompt
    title_page.append(title_image)

    phrase = chapter_xml.new_tag("phrase")
    phrase.string = chapter.title
    phrase.attrs["speaker"] = "Narrator"
    title_page.append(phrase)


def add_author(chapter_xml, chapter):
    author_page = chapter_xml.new_tag("paragraph")
    author_page.attrs["tags"] = "has-text=false,spoken-only=true"
    author_page.attrs["fullscreen"] = "true"
    chapter_xml.find('chapter').append(author_page)

    author_image = chapter_xml.new_tag("image")
    author_prompt = f"Renown author {chapter.author.name} at a desk writing the famous story {chapter.title}"
    author_image.attrs["prompt"] = author_prompt
    author_page.append(author_image)

    phrase = chapter_xml.new_tag("phrase")
    phrase.string = f"By {chapter.author.name}"
    phrase.attrs["speaker"] = "Narrator"
    author_page.append(phrase)


@bp.route("/actions/save_chapter_text", methods=["POST"])
def save_chapter_text(author, title, chapter_number, language):
    """
    Save the book metadata to both chapter config json and straight to the xml file.
    why both?  fuck off.
    """
    author = Author(author)
    chapter = Chapter(
        author=author,
        title=title,
        number=chapter_number,
        language=language
    )

    book_text = request.form.get('book_text', '')
    chapter.save_txt(book_text)
    
    root = chapter.get_xml()
    # persist the Chapter() kwargs items to the top object in the chapter XML.
    for key, value in chapter.kwargs.items():
        root.attrs[key] = value
    
    chapter.save_xml()

    out = htmx.book_text_textarea(book_text)    

    # update the XML textarea (so we see the new kwargs I guess)    
    out += f"""<div
        hx-swap-oob="true"
        id="xml_text"
    >
        <wa-textarea
            label="XML Text",
            name="xml_text",
            rows="10"
            value="{html.escape(chapter.soup.prettify() if chapter.soup else '')}"
            size="medium"
            appearance="outlined"
            resize="vertical"
        ></wa-textarea>
    </div>
    """
  
    return out, 200


@bp.route("/actions/convert_to_xml", methods=["POST"])
def convert_to_xml(author, title, chapter_number, language):

    author = Author(author)
    chapter = Chapter(
        author=author,
        title=title,
        number=chapter_number,
        language=language
    )

    xmlfn = chapter.get_xml_fn()
    if os.path.exists(xmlfn):
        shutil.copyfile(
            xmlfn,
            xmlfn + ".bak"
        )
        os.unlink(xmlfn)

    soup = chapter.get_xml()
    root = chapter.soup.find('book')

    # dump some metadata into the root ("book").
    for key, value in chapter.kwargs.items():
        root.attrs[key] = value

    log.info('pre-save root: %s', root)

    # title page
    title_page = soup.new_tag("paragraph")
    title_page.attrs["tags"] = "has-text=false,spoken-only=true"
    title_page.attrs["fullscreen"] = "true"
    root.append(title_page)
    chapter.save_xml()
    
    title_image = soup.new_tag("image")
    if "subtitle" in root.attrs:
        title_prompt = (
            "The front cover of a detailed carved and "
            "painted cover of a masterfully crafted "
            "leather-bound handmade special edition of"
            f" \"{chapter.title} - {chapter.subtitle}\" by \"{chapter.author.name}\"."
        )
    else:
        title_prompt = (
            #f"The front of a record albumn called {title} by {author}"
            "The front cover of a detailed carved and "
            "painted cover of a masterfully crafted "
            "leather-bound handmade special edition of"
            f" \"{chapter.title}\" by \"{chapter.author.name}\"."
        )

    title_image.attrs["prompt"] = title_prompt
    title_image.attrs["t2i"] = "tsqn.zimageturbo"
    title_page.append(title_image)
    chapter.save_xml()

    title_phrase = soup.new_tag("phrase")
    title_phrase.string = chapter.title
    title_phrase.attrs["speaker"] = "Narrator"
    title_page.append(title_phrase)
    chapter.save_xml()

    # author page
    author_page = soup.new_tag("paragraph")
    author_page.attrs["tags"] = "has-text=false,spoken-only=true"
    author_page.attrs["fullscreen"] = "true"
    author_image = soup.new_tag("image")
    author_prompt = f"Renown author {chapter.author.name} at a desk writing the famous story {chapter.title}"
    author_image.attrs["prompt"] = author_prompt
    author_image.attrs["t2i"] = "tsqn.zimageturbo"
    author_page.append(author_image)
    chapter.save_xml()

    author_phrase = soup.new_tag("phrase")
    author_phrase.string = f"By {chapter.author.name}"
    author_phrase.attrs["speaker"] = "Narrator"
    author_page.append(author_phrase)
    root.append(author_page)
    chapter.save_xml()

    # translator page
    if chapter.translator:
        translator_page = soup.new_tag("paragraph")
        translator_page.attrs["tags"] = "has-text=false,spoken-only=true"
        translator_page.attrs["fullscreen"] = "true"
        
        translator_image = soup.new_tag("image")
        translator_prompt = f"Charming and clever {chapter.translator} making an important but difficult decision while sitting at a desk writing a letter with a fountain pen"
        translator_image.attrs["prompt"] = translator_prompt
        translator_image.attrs["t2i"] = "tsqn.zimageturbo"
        translator_page.append(translator_image)

        translator_phrase = soup.new_tag("phrase")
        translator_phrase.string = f"Translated by {chapter.translator}"
        translator_phrase.attrs["speaker"] = "Narrator"
        translator_page.append(translator_phrase)
        root.append(translator_page)
        chapter.save_xml()

    # illustrator page
    # chapter page
    contents = chapter.load_txt()
    # make sure our linefeeds are not windows format
    if "\r\n\r\n" in contents:
        contents = contents.replace("\r", "")

    # paragraphs are indicated by a blank line
    all_paragraphs = contents.split("\n\n")
    paragraph_count = len(all_paragraphs)
    log.info(f'Iterating over {paragraph_count} paragraphs...')

    first = True
    for paragraph_index in range(paragraph_count):
        log.info('Top of paragraph loop, index: %d', paragraph_index)
        # why do this inside the loop?  because process_paragraph can add
        # new characters, and this is a fairly cheap call.
        all_characters = {}
        if not first:
            all_characters = characters.get_all_characters(chapter)

        # parent element process_paragraph can attach stuff to
        hints = request.form.get('hints', '')

        paragraph_text = all_paragraphs[paragraph_index].strip()
        
        if not paragraph_text.strip():
            log.info('Skipping empty paragraph')
            continue
        
        paragraph = soup.new_tag("paragraph")

        # pre-chapter material, or all upper case paragraphs
        # which are generally meaningful.
        if paragraph_text.upper().strip() in [
            "PREFACE", 
            "A WORD OF EXPLANATION",
        ] or paragraph_text.strip() == paragraph_text.upper().strip():
            # is this a single word paragraph?
            if len(paragraph_text.split()) == 1:
                # is it a roman numeral (maybe with a trailing period)?
                if roman.is_roman_numeral(paragraph_text.strip().replace(".", "")):
                    paragraph.attrs["tags"] = "has-text=false,spoken-only=true"

                    # draw a nice fancy image portraying the roman
                    # numeral is a classic sort of style
                    image = paragraph.new_tag("image")
                    image.attrs["prompt"] = (
                        f"The roman numeral {paragraph_text} carved carefully into a fine marble column in a splendid and pleasing style"
                    )

                    phrase = paragraph.new_tag("phrase")

                    numeral = paragraph_text.replace("\n", " ").replace(".", "")
                    spoken = roman.numeral_to_spoken(numeral)
                    phrase.string = paragraph_text
                    phrase.attrs["helpers"] = f"{paragraph_text}:{spoken}"
                    phrase.attrs["speaker"] = "Narrator"
                    phrase.attrs["type"] = "section_header"  # used for styling
                    # done with this "paragraph"
                    log.info('Adding roman numeral paragraph: %s', paragraph_text)
                    
                    root.append(paragraph)
                    chapter.save_xml()
                    continue

            # is this a dinkus?  everything is either "*"" or " "
            if paragraph_text.replace(" ", "").strip() == len(paragraph_text.replace(" ", "").strip()) * "*":
                phrase = soup.new_tag("phrase")
                paragraph.attrs["tags"] = "has-text=true"
                phrase.attrs["type"] = "dinkus"
                phrase.attrs["duration"] = 0.75 # 3/4 seconds of "pronunciation" time
                phrase.attrs["speaker"] = "Narrator"
                phrase.string = "***"
                paragraph.append(phrase)
                root.append(paragraph)
                chapter.save_xml()
                continue

            # special cases for PREFACE, INTRODUCTION, etc.
            # we will format them like chapter titles on the page
            # but they will be spoken and highlighted like normal text.
            paragraph.attrs["tags"] = "has-text=true,spoken-only=false"

            image = soup.new_tag("image")
            image.attrs["clip_prompt"] = (
                f'{paragraph_text},book page,classic,elegant,ornate,letters'
            )
            
            prompt = (
                f'A classic and elegant book page with the text "{paragraph_text}" presented boldly in large ornate letters'
            )
            image.attrs["prompt"] = prompt
            image.attrs["t2i"] = "tsqn.zimageturbo"

            phrase = soup.new_tag("phrase")
            # titlecase is prone to mistakes.
            phrase.string = titlecase(paragraph_text)
            phrase.attrs["speaker"] = "Narrator"
            phrase.attrs["type"] = "section_header"  # used for latex styling
            
            # done with this "paragraph"
            paragraph.append(image)
            #chapter.save_xml()

            paragraph.append(phrase)
            #chapter.save_xml()
            #chapter.load_xml(force=True)

            log.info('1root: %s', root)
            log.info('1paragraph: %s', paragraph)
            root.append(paragraph)
            log.info('2root: %s', root)

            #chapter.save_xml()
            log.info('Adding section break: %s', paragraph_text)
        
        # we need to choose a technique for identifying which portions of the text
        # are spoken by which characters.
        # sometimes, it's stupid simple.  Lets check for stupid simple.
        if chapter.config.get("paragraph_technique", None) is None:
            log.info('Detecting paragraph technique...')
            chapter.config["paragraph_technique"] = text.detect_paragraph_technique(paragraph_text)
            
            log.info('Detected paragraph technique: %s', chapter.config["paragraph_technique"])
            chapter.save_config()
        else:
            log.info('Using paragraph technique: %s', chapter.config["paragraph_technique"])

        paragraph_technique = chapter.config["paragraph_technique"]
        # just a matter of time before this causes a problem.
        # TODO: improve.
        # * set a relevant type="" on these phrases
        if (
            paragraph_technique != "biblical"
            and (
                paragraph_text.strip().upper() in chapter.config.get("title", []).upper()
                or
                paragraph_text.strip().upper() in chapter.config.get("chapter_title", []).upper()
                or
                # "by author name"
                chapter.config.get("author", "").upper() in paragraph_text.strip().upper()
                or
                # "translated by translator name"
                chapter.config.get("translator", False) and chapter.config.get("translator", "").upper() in paragraph_text.strip().upper()
            )
        ):
            # we're already handing these with their own pre-text, full screen segments
            # we don't to be repetivive so we skip them here.
            log.info('Skipping header material paragraph_text: %s', paragraph_text)
            continue
        
        if hints is None:
            hints = {}
            
        chapter.save_xml()
        if paragraph_technique == "socratic":
            # CHARACTERNAME: Spoken text
            # over one or more lines
            #
            # OTHERCHARACTERNAME: More spoken text
            text.socratic_to_paragraph(
                chapter,
                paragraph_text, 
                paragraph, 
                hints, 
                all_characters
            )
        elif paragraph_technique == "dialog":
            # 'dialog' is a slow, quotation mark based engine to identify portions of text
            # that are 'spoken' and make a best guess as to the speaker.
            log.info('Invoking dialog_to_paragraph...')
            text.dialog_to_paragraph(
                chapter, 
                paragraph_text, 
                paragraph, 
                all_paragraphs, 
                paragraph_index, 
                hints, 
                all_characters
            )
        elif paragraph_technique == "poetry":
            text.poetry_to_paragraph(
                chapter,
                paragraph_text,
                paragraph,
            )            
        elif paragraph_technique == "narrator":
            log.info('Invoking narrator_to_paragraph...')
            text.narrator_to_paragraph(
                chapter,
                paragraph_text,
                paragraph, 
            )
        elif paragraph_technique == "biblical":
            # bible title page
            # book title page
            previous_chapter = 0
            after_header = False
            for verse_string in paragraph_text.split("\n"):
                if verse_string[:3] == "1:1":
                    after_header = True
                
                if not after_header:
                    continue

                chapter_verse, verse_text = verse_string.split(" ", maxsplit=1)
                chapter_number, verse = chapter_verse.split(":")
                if chapter_number != previous_chapter:
                    chapter_xml = soup.new_tag("paragraph")
                    chapter_xml.attrs["chapter"] = chapter_number
                    root.append(chapter_xml)

                phrase = soup.new_tag("phrase")
                phrase.attrs["chapter"] = chapter_number
                phrase.attrs["verse"] = verse
                phrase.attrs["speaker"] = "Narrator"
                phrase.string = verse_text.strip()
                chapter_xml.append(phrase)

                previous_chapter = chapter_number
        else:
            log.error('Unknown paragraph technique: %s', paragraph_technique)

        log.info('paragraph %s', paragraph)
        log.info('root %s', root)       
        
        
        root.append(paragraph)
        chapter.save_xml()

        # I know, write it _every_ paragraph?  This is for debugging, not speed.
        # when we want speed we just bump this section down one indent.  No problem.
        # tree = ET.ElementTree(root)
        # ET.indent(tree)
        # write the prettier version with indentation and newlines
        # book_xml_fn = chapter.get_xml_fn()
        # log.info(f'Saving {book_xml_fn}...')
        # with open(book_xml_fn, "w", encoding="utf-8") as book_xml_file:
        #     book_xml_file.write(soup.prettify())
        # chapter.save_xml()
        #
        # root.save(book_xml_fn)
        # tree.write(
        #     book_xml_fn, 
        #     encoding="unicode"
        # )
        first = False

    # No, no, you're not done yet fawker.  we are NOT finished.

    chapter.save_xml()

    phrase_index = 0
    for index, paragraph in enumerate(root.find_all("paragraph")):
        paragraph.attrs['index'] = index

        # fragdex = 0
        for phrase in paragraph.children:
            if not hasattr(phrase, 'attrs'):
                continue

            phrase.attrs['index'] = phrase_index
            phrase_index += 1
            #phrase.attrs['id'] = f"{index}_{fragdex}"
            #fragdex += 1

    #log.info(f'Saving {book_xml_fn}...')
    #with open(book_xml_fn, "w", encoding="utf-8") as book_xml_file:
    #    book_xml_file.write(soup.prettify())

    chapter.save_xml()

    return htmx.convert_to_xml_button(chapter), 200


def previous_chapter(chapter_config):
    out = '<div class="wa-stack">'
    # 'title',  'subtitle', 'author', 'aspect', 
    for key in [
        'chapter_title',
        'translator', 'youtube', 
        'paragraph_technique'
    ]:
        label = key.replace("_", " ").title()
        value = chapter_config.get(key, "")
        out += f'<wa-input disabled label="{label}" type="text" value="{value}"></wa-input>'
    out += "</div>"
    return out


# http://localhost:5000/Oscar%20Wilde/The%20Picture%20of%20Dorian%20Gray/0002/text/set_chapter_title
@bp.route("/set_<key>", methods=["PUT"])
def set_chapter_metadata(author, title, chapter_number, language, key):
    # Implementation for setting the chapter title
    value = request.form.get(key)

    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)
    chapter.config[key] = value
    chapter.save_config()

    return "", 200


def title_input(chapter_config):
    label = "Title"
    value = chapter_config.get("title", "")
    return f'<wa-input label="{label}" type="text" value="{value}"></wa-input>'


def chapter_metadata_text_input(
        chapter_config,
        name,
        label
    ):
    value = chapter_config.get(name, "")
    return f'''
    <wa-input 
        hx-put="set_{name}"
        hx-include="[name='{name}']"
        hx-trigger="change"
        name="{name}"
        label="{label}" 
        type="text"
        value="{value}">
    </wa-input>'''.replace("\n", "")


def chapter_metadata_choices(
        chapter,
        name,
        label,
        options
):
    log.info('chapter.config: %s', chapter.config)
    value = chapter.config.get(name, "Missing")

    out = f'''<wa-select 
        hx-put="set_{name}"
        hx-include="[name='{name}']"
        hx-trigger="change"
        name="{name}"
        value="{value}"
        label="{label}">'''
    for option in options:
        out += f'<wa-option value="{option}">{option.title()}</wa-option>'
    out += '</wa-select>'
    return out


# def paragraph_technique_input(chapter):
#     label = "Paragraph Technique"
#     hint = "How should we determine who says what?"
#     value = chapter.config.get("paragraph_technique", "")
#     return f'''<wa-select name="paragraph_technique" value="{value}" label="{label}" hint="{hint}" id="paragraph_technique">
#         <wa-option value="dialog">Dialog (conventional novel, quotes around character statements)</wa-option>
#         <wa-option value="socratic">Socratic (CHARACTER: what they say...)</wa-option>
#         <wa-option value="biblical">Biblical (chapter:verse prefix to each paragraph)</wa-option>
#         <wa-option value="narrator">Narrator (everything voiced by one narrator)</wa-option>
#         <wa-option value="poetry">Poetry (single narrator, preserve line breaks and spacing)</wa-option>
#     </wa-select>'''



@bp.route("/", methods=["DELETE"])
def delete_chapter(author, title, chapter_number, language):
    author = Author(author)
    chapter = Chapter(
        author=author,
        title=title,
        number=chapter_number,
        language=language
    )
    chapter.delete()

    response = make_response("", 200)
    response.headers["HX-Redirect"] = f"/{chapter.author.name}/{chapter.title}/"
    return response


@bp.route("/", methods=["GET"])
def text_base(author, title, chapter_number, language):
    log.info(f"Text view called for {author} - {title} - {chapter_number} - {language}")
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)

    return render_template(
        "text.html",
        language=chapter.language,
        pretty_language=chapter.pretty_language,
        import_button=htmx.import_button(chapter),
        chapterurl=chapter.url,
        author=author,       
        pretty_author=chapter.config.get("author", author),
        title=title,
        pretty_title=chapter.config.get("title", title),
        chapter=chapter,
        section="text",
        section_cosmetic="Text"
    )

@bp.route("/delta", methods=["GET"])
def get_delta(author, title, chapter_number, language):
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)

    try:
        raw_text = chapter.load_delta()
    except FileNotFoundError:
        log.error('Delta file not found for chapter %s. Returning empty text.', chapter.get_delta_fn())
        raw_text = {}

    if raw_text in [{}, None]:
        book_txt = os.path.join(
            const.LIBRARY_DIR,
            chapter.get_chapterdir(),
            "book.txt"
        )

        # plain text file, not delta.  No problem.
        if os.path.exists(book_txt):           
            log.info('Importing plain text %s', book_txt)

            with open(book_txt, "r", encoding="utf-8") as f:
                raw_text = f.read()

            delta = {"ops": [{"insert": raw_text}]}
            raw_text = json.dumps(delta, indent=2)

            os.makedirs(os.path.dirname(chapter.get_delta_fn()), exist_ok=True)
            with open(chapter.get_delta_fn(), "w", encoding="utf-8") as f:
                f.write(raw_text)
        else:
            log.info('No book.txt found for chapter %s. Returning empty delta.', chapter.get_chapterdir())
            raw_text = json.dumps({"ops": []}, indent=2)

    return raw_text, 200

@bp.route("/delta", methods=["POST"])
def save_delta(author, title, chapter_number, language):
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)

    new_delta = request.form["delta"]

    with open(chapter.get_delta_fn(), "w", encoding="utf-8") as f:
        as_json = json.loads(new_delta)
        # pretty print it.
        f.write(json.dumps(as_json, indent=2))
    
    return "", 200

@bp.route("/get_text", methods=["GET"])
def get_text(author, title, chapter_number, language):
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)

    try:
        raw_text = chapter.load_txt()
    except FileNotFoundError:
        log.error('Text file not found for chapter %s. Returning empty text.', chapter.get_txt_fn())
        raw_text = ""

    return raw_text, 200

@bp.route("/get_xml", methods=["GET"])
def get_xml(author, title, chapter_number, language):
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)

    try:
        raw_xml = chapter.get_xml().prettify()
    except Exception as e:
        log.error(f"Error loading XML for chapter {chapter.url}: {e}")
        raw_xml = ""

    return raw_xml, 200 


@bp.route("/action/book_text", methods=["POST"])
def book_text_submit(author, title, chapter_number, language):
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)

    new_text = request.form["book_text"]

    with open(chapter.get_txt_fn(), "w", encoding="utf-8") as f:
        f.write(new_text)
    
    return htmx.book_text_textarea(new_text), 200


@bp.route("/action/book_meta", methods=["POST"])
def book_meta_submit(author, title, chapter_number, language):
    new_author = request.form["author"]
    new_title = request.form["title"]
    new_chapter_title = request.form["chapter_title"]
    new_translator = request.form.get("translator", "")
    new_hints = request.form['hints']
    new_paragraph_technique = request.form["paragraph_technique"]

    # we need some rules.
    # Every directory is relative to bookdir.  If we're talking to a third party module that _needs_ an abosolute path,
    # that is fine.  But any output path must be made relative to bookdir.
    #
    # bookdir ends with a /
    # all relative paths do _not_ start with /

    chapterdir = get_chapterdir(author, title, chapter)
    chapterurl = get_chapterurl(author, title, chapter)
    configdict = config.get_config(chapterdir)
    
    configdict["author"] = new_author
    configdict["title"] = new_title
    configdict["chapter_title"] = new_chapter_title
    configdict["hints"] = new_hints
    configdict["translator"] = new_translator
    configdict["paragraph_technique"] = new_paragraph_technique
    config.save_config(chapterdir, configdict)

    # log.info('bookdir: %s', chapterdir)
    # mybook = get_book(chapterdir)
    # mybook.raw = new_text
    # mybook.save_raw()

    # log.info(f'{request.form=}')
    # if request.form["button"] == "extract_characters":
    #     log.info('Initializing CharacterBook')
    #     cb = characters.CharacterBook(chapterdir, new_text)

    #     # this is where the time goes.
    #     log.info('Finding characters')
    #     cb.find_characters()
        
    #     # create characters.json in bookdir
    #     log.info('Saving characters.json')
    #     cb.save(
    #         os.path.join(
    #             const.LIBRARY_DIR,
    #             chapterdir, 
    #             "characters.json"
    #         )
    #     )

    
    # if mybook.soup is None:
    #     raw_xml = ""
    # else:
    #     raw_xml = mybook.soup.prettify()

    return redirect(f"/{chapterurl}/text", code=302)
