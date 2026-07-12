import html
import os
import shutil

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

    previous_chapter_metadata = ""
    if chapter.number > 1:
        log.info('Loading previous chapter %s - %s - %s', author, title, chapter.number - 1)
        previous = chapter.previous()
        if previous:
            previous_chapter_config = chapter.previous().config
            previous_chapter_metadata = previous_chapter(previous_chapter_config)

    raw_xml = ""
    try:
        raw_xml = chapter.get_xml().prettify()
    except Exception as e:
        log.error(f"Error loading XML for chapter {chapter.url}: {e}")

    try:
        raw_text = chapter.load_txt()
    except FileNotFoundError:
        raw_text = ""

    log.info('Generating widgets for %s', chapter.config)
    chapter_metadata = "\n".join([
        #chapter_metadata_text_input(chapter.config, "title", "Title"),
        chapter_metadata_text_input(chapter.config, "chapter_title", "Chapter Title"),
        #chapter_metadata_text_input(chapter.config, "subtitle", "Subtitle"),
        #chapter_metadata_text_input(chapter.config, "author", "Author"),
        chapter_metadata_text_input(chapter.config, "translator", "Translator"),
        chapter_metadata_text_input(chapter.config, "youtube", "YouTube URL"),
        chapter_metadata_choices(
            chapter,
            "paragraph_technique",
            "Paragraph Technique",
            ['dialog', 'socratic', 'biblical', 'narrator', 'poetry']),
    ])

    return render_template(
        "text.html",
        language="english",
        pretty_language="English",
        chapter_metadata=chapter_metadata,
        chapter_config=chapter.config,
        previous_chapter=previous_chapter_metadata,
        save_button=htmx.save_book_button(chapter.url),
        convert_to_xml=htmx.convert_to_xml_button(chapter),
        chapterurl=chapter.url,
        author=author,       
        pretty_author=chapter.config.get("author", author),
        title=title,
        pretty_title=chapter.config.get("title", title),
        chapter=chapter,
        book_text=htmx.book_text(raw_text, chapter),
        xml=raw_xml,
        section="text",
        section_cosmetic="Text"
    )

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
