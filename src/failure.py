
def import_chapter_too_nasty(author, title, chapter_number, language):
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

    chapter_xml = BeautifulSoup("<chapter></chapter>", "xml")

    delta = chapter.load_delta()
    
    # we want to be able to slice-and-dice the plain text and the formatted
    # text in parallel.  It's stupid, but I think it might actually be
    # easiest to leave it in Delta format for a little longer than I had
    # originally intended.

    # if there is a title, add a title page
    delta = extract_title(delta, chapter, chapter_xml)

    delta = extract_author(delta, chapter, chapter_xml)
   
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
        for sentence_obj in nlp(ops.get("insert", "")).sents:
            sentence = sentence_obj.text_with_ws + "\n"
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
            log.info('sentence: %s', sentence)
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
        highest = 0
        WINDOW_SIZE = 25
        break_index = None
        for i in range(index, min(index + WINDOW_SIZE, len(out_ops))):
            if out_ops[i].get("phrase_score", 0) > highest:
                highest = out_ops[i]["phrase_score"]
                break_index = i

        if break_index is None:
            log.info('!FAILURE! No phrase break found!')
            p = out_ops[index:min(index + WINDOW_SIZE, len(out_ops))]
            log.info('p: %s', p)
            log.info('phrase: %s', " ".join([o["insert"] for o in p]))

            raise ValueError("No phrase break found!")

        plain_phrase = "".join([out_ops[i]["insert"] for i in range(index, break_index + 1)])
        # log.info(' Spoken phrase: %s', plain_phrase)

        typeset_phrase = []
        for o in out_ops[index:break_index + 1]:
            if "\t" in o["insert"]:
                log.info('Replacing tab with \hspace{2em} in phrase: %s', o["insert"])
                o["insert"] = o["insert"].replace("\t", r"\hspace{2em}")

            if "\n" in o["insert"]:
                # there are newlines within this insert block.
                new_insert = []
                
                as_list = o["insert"].split("\n")
                log.info('Processing %s', repr(o["insert"]))
                log.info('as_list: %s', as_list)
                for segment in as_list[:-1]:
                    if segment.strip():
                        new_insert.append(segment + r"s\\" + "\n")
                    else:
                        new_insert.append("\n")

                new_insert[-1] += as_list[-1]
                                
                if o["insert"][-1] == "\n" and len(o["insert"].strip()) > 0:
                    if new_insert[-1].endswith(r"\\" + "\n"):
                        new_insert[-1] = new_insert[-1][:-3]

                    new_insert[-1] = new_insert[-1][:-1] + r"\\" + "\n"
                o["insert"] = "".join(new_insert)

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
                # we are ending a bold block
                if typeset_phrase:
                    # retain the trailing whitespace, _after_ the close tag.
                    stripped = typeset_phrase[-1].rstrip()
                    if stripped.endswith(r"\\"):
                        stripped = stripped[:-2]                    
                    trailing_whitespace = typeset_phrase[-1][len(stripped):]
                    typeset_phrase[-1] = stripped + "}" + trailing_whitespace
                else:
                    stripped = phrases[-1]["latex"].rstrip()
                    if stripped.endswith(r"\\"):
                        stripped = stripped[:-2]
                    
                    trailing_whitespace = phrases[-1]["latex"][len(stripped):]
                    phrases[-1]["latex"] = stripped + "}" + trailing_whitespace 

                attributes["bold"] = None

            # no format change
            #if len(o["insert"].strip()) > 0:
            #    o["insert"] = o["insert"].replace("\n", r"\\" + "\n")
            typeset_phrase.append(o["insert"])

        phrases.append({
            "spoken": plain_phrase,
            "latex": "".join(typeset_phrase)
        })

        index = break_index + 1

    
    with open(
        os.path.join(
            const.LIBRARY_DIR,
            chapter.get_chapterdir(),
            chapter.language,
            "chapter.latex"
        ), "w") as f:
            f.write(r"""\documentclass[parskip=full]{scrartcl}
\usepackage[paperheight=48in,paperwidth=2.825in,top=0.5in,bottom=4in,left=0.1in,right=0.1in,heightrounded]{geometry}
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
\vspace{-0.5in}""" % (title, author.pretty_name))

            for p in phrases:
                log.info('s: %s', p['spoken'])
                f.write(p["latex"])
            
            f.write(r"""\end{document}""")
            
    delta["ops"] = out_ops
    # for operation in delta["ops"]:
    #     log.info('operation: %s', operation)

    return "", 200



def import_chapter_better_but_still_wrong(author, title, chapter_number, language):
    author = Author(author)

    chapter = Chapter(
        author=author,
        title=title,
        number=chapter_number,
        language=language
    )

    chapter_xml = BeautifulSoup("<chapter></chapter>", "xml")

    delta = chapter.load_delta()
    
    # we want to be able to slice-and-dice the plain text and the formatted
    # text in parallel.  It's stupid, but I think it might actually be
    # easiest to leave it in Delta format for a little longer than I had
    # originally intended.

    # if there is a title, add a title page
    delta = extract_title(delta, chapter, chapter_xml)

    delta = extract_author(delta, chapter, chapter_xml)
    
    nltk.download('punkt_tab')

    # break all the 'inserts' in delta up into single delta sentences.
    out_ops = []
    for ops in delta["ops"]:
        for sentence in nltk.sent_tokenize(ops.get("insert", "")):
            log.info('sentence: %s', sentence)
            out_ops.append({
                "insert": sentence,  # does this need a trailing space?
                "attributes": ops.get("attributes", {}),
                "type": "sentence"
            })
    delta["ops"] = out_ops

    # now.. for the messy part.
    first, second = shatter_delta(delta)

    # flatten out the tree

    flat = lists_that_do_not_contain_lists([first, second])
    flat = list(flat)

    for f in flat:
        tl = 0
        for k in f:
            if k:
                tl += k.get("length", 0)
 
        # this will be where we insert <image> tags
        log.info(f'{f} (total length: {tl})')

        # phrases will come from gluing together ops that were split
        # because the attribute changed.  We can use the "type": "sentence"
        # to differentiate.

        # the attribute-aware part is translated into the phrase "latex" attribute as
        # formatting instructions.

    #log.info('shatter results', first=first, second=second)
    # log.info('delta after title and author extraction: %s', delta)
    return "", 200



def import_chapter_moron_approach(author, title, chapter_number, language):
    """
    Take the current Delta formatted text and convert it to XML.
    
    The get_text() is the spoken text The "latex" attribute holds the visible
    text The "pronunciation" attribute holds the IPA pronunciation of the text

    With a typical reading speed of 120-180 words per minute and a need for
    image markers every 8 seconds or less, we want to place image markers every
    25 words.  We want them to align with sentence breaks whenever possible.
    """
    author = Author(author)

    chapter = Chapter(
        author=author,
        title=title,
        number=chapter_number,
        language=language
    )

    chapter_xml = BeautifulSoup("<chapter></chapter>", "xml")
    title_page = None
    author_page = None

    paragraph = chapter_xml.new_tag("paragraph")
    skips = 0
    accumulation = ""

    delta = chapter.load_delta()
    for operation in delta["ops"]:
        # operations are blocks with common formatting attributes.
        for line in re.split('(\n\n+)|([,;.]+)', operation.get("insert", "")):
            if line is None:
                continue
            # Paragraphs are delimited by \n\n within paragraphs, _phrases_ are
            # sentences or significant sentence fragments. we will use nltk to
            # split the paragraph in sentences, it doesn't get fooled as easily
            # by things like "Dr. Bob"
            if title_page is None and (line.strip().upper() == chapter.title.upper()):
                log.info('Found title page: %s', line)
                title_page = True
                add_title(chapter_xml, chapter)
                continue

            if author_page is None and (
                line.strip().upper() in [
                    f"BY {chapter.author.name.upper()}",
                    f"{chapter.author.name.upper()}"
                ]
            ):
                log.info('Found author page: %s', line)
                author_page = True
                add_author(chapter_xml, chapter)
                continue

            if line:
                if skips:
                    line = "\n" * skips + line
                    accumulation += line
                
                    # end of paragraph
                    phrase = chapter_xml.new_tag("phrase")
                    phrase.string = accumulation.strip()
                    paragraph.append(phrase)

                    chapter_xml.append(paragraph)

                    paragraph = chapter_xml.new_tag("paragraph")
                    accumulation = ""
                    skips = 0
                    continue
                else:
                    accumulation += line
            else:
                skips += 1

            # have we reached a good place to end a phrase?
            # ie: end of a sentence, strong punctuation mark, paragraph break
            # or the text we've accumulated is just too long.
            if len(accumulation.split()) > 25:
                log.info('Processing long phrase: %s', accumulation)
                phrase = chapter_xml.new_tag("phrase")
                phrase.string = accumulation.strip()
                paragraph.append(phrase)
                accumulation = ""
                continue
            
            if len(accumulation.split()) > 20 and line.strip().endswith((".", "!", "?", ";", ":")):
                log.info('Processing punctuated phrase: %s', accumulation)
                phrase = chapter_xml.new_tag("phrase")
                phrase.string = accumulation.strip()
                paragraph.append(phrase)
                accumulation = ""
                continue

    # log.info('accumulation: %s', accumulation)
    # final paragraph and phrase.
    if accumulation.strip():
        phrase = chapter_xml.new_tag("phrase")
        phrase.string = accumulation.strip()
        paragraph.append(phrase)
        chapter_xml.append(paragraph)

    log.info('chapter_xml: %s', chapter_xml.prettify())
    return "", 200

