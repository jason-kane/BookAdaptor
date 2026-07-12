import html
import json
import os
import re
import numpy as np

from flask import request, url_for, render_template

import const
import logger
from artifact_editor.characters import characters
from artifact_editor.tools import generic_button
from artifact_editor.images import htmx as images_htmx

log = logger.log(__name__)


def image_placeholder(chapter, image_xml):
    """
    This is a placeholder for an image in the segement stream.
    """
    # paragraph = image_xml.find_parent("paragraph")
    if "src" in image_xml.attrs:
        src = url_for(
            "library.book.chapter.images.show_image_by_index",
            author=chapter.author.name,
            title=chapter.title,
            chapter_number=chapter.number,
            language=chapter.language,
            height=150,
            image_index=image_xml.attrs["index"],
        )
        # src=f"/{chapter.url}/{chapter.language}/images/{ image_xml['index'] }.png"
    else:
        # no src, so we don't know where it is.
        src = "/static/images/placeholder.png"
    
    if image_xml.attrs.get("animation", "false") == "true":
        # animation
        image_or_video = f"""
        <video
            src="{images_htmx.get_animation_url(chapter, int(image_xml.attrs['index']))}"
            controls
        ></video>
        """
    else:
        # image
        image_or_video = f"""
            <img class="image-placeholder" src="{src}"></img>
        """

    return render_template(
        "image.html",
        chapter=chapter,
        image_xml=image_xml,        
        src=src,
        **chapter.kwargs,
        image_or_video=image_or_video,
    )


def dinkus_editor(fragment):
    """
    we're providing a select widget to choose the dinkus style.
    """
    current_style = fragment.attrs.get("style", "fleuron")
    options = [
        "fleuron",
        "simplefleuron",
        "asterism",
        "tightasterism",
        "trueasterism",
        "dinkus",
        "closing",
    ]

    # select widgets are for chumps.
    dropdown = f"""
    <div class="wa-stack">
        <wa-dropdown
            name="dinkus_style"
            value="{current_style}"
            >
        >
            <wa-button slot="trigger" with-caret>{current_style}</wa-button>
    """

    # <wa-icon src="https://shoelace.style/assets/images/shoe.svg" style="font-size: 4rem;"></wa-icon>

    for opt in options:
        dropdown += f"""
            <wa-dropdown-item
                hx-post="actions/set_dinkus_style/{fragment['index']}"
                hx-target="#phrase-{fragment['index']}"
                hx-swap="innerHTML"
                hx-vals='{{"style": "{opt}"}}'
                hx-on::wa-select="htmx.trigger(this, 'submit')"
            >
                <wa-icon slot="icon" src="/static/images/dinkus_{opt}.svg"></wa-icon>{opt.capitalize()}
            </wa-dropdown-item>
        """

    duration = float(fragment.attrs.get("duration", 0.75))
    dropdown += f"""
        </wa-dropdown>

        <wa-slider
            label="Duration (seconds)"
            hx-post="actions/set_dinkus_duration/{fragment['index']}"
            hx-target="#phrase-{fragment['index']}"
            hx-swap="innerHTML"
            hx-trigger="change delay:500ms"
            name="duration"
            min="0"
            max="2"
            step="0.25"
            value="{duration}"
            with-markers
            with-tooltip
        >
            <span slot="reference">0s</span>
            <span slot="reference">0.5s</span>
            <span slot="reference">1s</span>
            <span slot="reference">1.5s</span>
            <span slot="reference">2.0s</span>
        </wa-slider>

        <div>
            <img src="/static/images/dinkus_{current_style}.png" alt="{current_style} dinkus">
        </div>
    </div>
    """

    return dropdown


def pretty_duration(phrase_xml):
    total_prior = 0
    for phrase in phrase_xml.find_parent("book").findAll("phrase"):
        if phrase is phrase_xml:
            # we are done
            break
        try:
            duration = float(phrase.get("duration", 0))
        except (ValueError, TypeError):
            duration = 0

        total_prior += duration

    # this phrase
    start_second = total_prior
    end_second = start_second + float(phrase_xml.get("duration", 0))

    start_minute = int(start_second // 60)
    start_second = start_second % 60
    end_minute = int(end_second // 60)
    end_second = end_second % 60

    # <wa-icon src="/static/fontawesome7/svgs/solid/chevron-left.svg"></wa-icon>
    def as_icons(in_float, digits=3):
        log.info(f"as_icons: in_float={in_float}, digits={digits}")
        out = []
        if digits == 3:
            in_float = round(in_float, 3)
            for character in f"{in_float:2.3f}":
                if character == ".":
                    out.append(".")
                else:
                    out.append(
                        f'<wa-icon auto-width src="/static/fontawesome7/svgs/solid/{character}.svg"></wa-icon>'
                    )
        elif digits == 0:
            in_float = round(in_float, 0)
            for character in f"{in_float:02d}":
                out.append(
                    f'<wa-icon auto-width src="/static/fontawesome7/svgs/solid/{character}.svg"></wa-icon>'
                )
        return "".join(out)

    start_minute = as_icons(start_minute, 0)
    start_second = as_icons(start_second)

    end_minute = as_icons(end_minute, 0)
    end_second = as_icons(end_second)

    return f"{start_minute}:{start_second} to {end_minute}:{end_second}"


def soundslike(phrase_xml, soundslike_url):
    out = f"""
    <div class="wa-cluster">
        <wa-input
            class="soundslike-input"
            label="SoundsLike"
            hint="Use an english word to help find the right IPA symbols."
            hx-post="{soundslike_url}"
                hx-target="#soundslike_{phrase_xml['index']}"
                hx-swap="outerHTML"
                name="soundslike"
                hx-include="#pronounce_{phrase_xml['index']}"
                value=""
                hx-trigger="change delay:125ms">
            </wa-input>

            <wa-textarea 
                class="soundslike-output"
                id="soundslike_{phrase_xml['index']}"
                placeholder="International Phonetic Alphabet (IPA)"
                resize="auto"
                rows="1"
                disabled>
            </wa-textarea>
        </div>"""
    return out


def pronunciation_helper(chapter, phrase_xml):
    soundslike_url = url_for(
        "library.book.chapter.audio.pronunciation.soundslike",
        author=chapter.author.name,
        title=chapter.title,
        chapter_number=chapter.number,
        language=chapter.language,
    )
        
    if not "❓" in phrase_xml.attrs.get("pronunciation", ""):
        return ""

    out = ""
    # The assumptions here require balls of steel.
    word_index = -1
    for word, word_pronunciation in zip(
        phrase_xml.get_text().strip().split(), # the "raw" words 
        phrase_xml.attrs.get("pronunciation", "").split() # the prounounced words
    ):
        word_index += 1

        if not "❓" in word_pronunciation:
            continue

        microphone_url = url_for(
            "library.book.chapter.audio.record_audio",
            **chapter.kwargs,
            phrase_index=phrase_xml['index'],
        )
        microphone_button = f"""
        <wa-button
            pill
            variant="danger"
            appearance="outlined"
            class="idle"
            hx-on::before-request="beforeMicrophoneRequest(this,event)"
            hx-on::after-request="afterMicrophoneRequest(this,event)"
            hx-vals='{{"word": {json.dumps(word)}}}'
            hx-post="{microphone_url}"><wa-icon 
                auto-width 
                src="/static/fontawesome7/svgs/solid/microphone-slash.svg">
            </wa-icon>
        </wa-button>"""
        
        # we don't pronounce quote marks
        word = word.replace('"', '')

        # this word is the one that needs help.
        out += f"""
        <div class="wa-cluster">
            <wa-input value="{html.escape(word)}" disabled></wa-input>{microphone_button} => IPA: 
            <wa-input id="ipa_{phrase_xml['index']}_{word_index}"></wa-input>
            <wa-button pill>Save</wa-button>
        </div>
        {soundslike(phrase_xml, soundslike_url)}
        """
        
    return out


def audio_player(chapter, phrase_xml):
    if "src" not in phrase_xml.attrs or phrase_xml["src"].strip() == "":
        return ""

    audio_src = url_for(
        'static.get_binary_file',
        author=chapter.author.name,
        title=chapter.title,
        chapter_number=chapter.number,
        language=chapter.language,
        paragraph=f"{int(phrase_xml.find_parent('paragraph')['index']):06}",
        filename=phrase_xml['src']
    )

    return f"""<audio controls>
                <source src="{audio_src}" type="audio/wav">
            </audio>"""

def generate_audio_button(chapter, phrase_xml):
    generate_audio_url = url_for(
        "library.book.chapter.audio.regenerate_phrase",
        author=chapter.author.name,
        title=chapter.title,
        chapter_number=chapter.number,
        language=chapter.language,
        phrase_index=phrase_xml['index'],
    )

    return f"""<wa-button
        hx-post="{generate_audio_url}"
        hx-vals='{{"page": "{request.args.get("page", 1)}" }}'
        hx-target="#phrase-{phrase_xml['index']}"
        hx-swap="outerHTML"
        hx-trigger="click">(Re)generate Audio</wa-button>"""


def pronunciation_textarea(chapter, phrase_xml):
    return f"""
    <wa-textarea
        id="pronounce_{phrase_xml['index']}"
        class="pronunciation-input"
        label="Pronunciation (IPA)"
        name="pronunciation"
        hx-put="{url_for('library.book.chapter.audio.pronunciation.save', author=chapter.author.name, title=chapter.title, chapter_number=chapter.number, language=chapter.language, phrase_index=phrase_xml['index'])}"
        hx-trigger="change delay:500ms"
        placeholder="IPA Pronunciation" 
        resize="auto"
        rows="1"
        value="{phrase_xml.get('pronunciation', '')}"
    ></wa-textarea>
    """


def base_text_phrase(chapter, phrase_xml):
    escaped = html.escape(phrase_xml.get_text().strip())
    phrase = f"""
        <wa-textarea
            class="phrase-text"
            disabled
            label="Phrase Text"
            name="text"
            resize="auto"
            rows="1"
            value="{escaped}">
        </wa-textarea>
    """

    if (
        chapter.config['paragraph_technique'] == "poetry"
        and phrase_xml.find_parent('paragraph').attrs.get('fullscreen', 'false') != 'true'
    ):
        # we need more control over when there are and aren't line breaks
        # in poetry mode.  Wrap ^ in a cluster and add a checkbox.
        value = "checked" if phrase_xml.attrs.get("no_linebreak") == "true" else ""

        phrase = f"""
        <div class="wa-cluster">
            {phrase}
            <wa-checkbox
                hx-put="{url_for('library.book.chapter.audio.set_linebreak', author=chapter.author.name, title=chapter.title, chapter_number=chapter.number, language=chapter.language, phrase_index=phrase_xml['index'])}"
                hx-vals='{{"no_linebreak": { "false" if value else "true" }}}'
                hx-swap="outerHTML"
                hx-target="#phrase-{phrase_xml['index']}"
                {value}>No break</wa-checkbox>
        </div>
        """

    return phrase


def split_phrase_button(chapter, phrase_xml):
    split_phrase_url = url_for(
        "library.book.chapter.audio.split_phrase",
        author=chapter.author.name,
        title=chapter.title,
        chapter_number=chapter.number,
        language=chapter.language
    )
    return f"""<wa-button
        hx-post="{split_phrase_url}"
        hx-target="#phrase-{phrase_xml['index']}"
        hx-swap="innerHTML"
        hx-trigger="click">Split Phrase</wa-button>"""


def add_edit_delay(chapter, phrase_xml, page):
    if "sound" not in phrase_xml.attrs:
        # don't have any sounds?  option to add a sound.
        return (f"""<wa-button
            hx-post="actions/add_sound/{phrase_xml['index']}"
            hx-vals='{{"page": "{page}"}}'
            hx-target="#phrases"
            hx-swap="innerHTML"
            variant="brand"
            hx-trigger="click">&#x2193; Add Sound Slot &#x2193;</wa-button>""", "")
    else:
        # have sounds?  widgets to edit/remove the sounds.
        return ("", f"""
        <div class="wa-cluster" id="delay-widget-{phrase_xml['index']}">
            <wa-slider
                label="After-phrase Delay (seconds)"
                name="delay"
                style="width: 50%;"
                value="{phrase_xml['delay']}"
                min="0"
                max="2"
                step="0.1"
                with-markers
                with-tooltip
                hx-post="actions/set_delay/{phrase_xml['index']}"
                hx-target="#phrase-{phrase_xml['index']}"
                hx-swap="innerHTML"
                hx-trigger="change delay:500ms"></wa-slider>
            <wa-button
                hx-delete="actions/remove_delay/{phrase_xml['index']}"
                hx-vals='{{"page": "{page}"}}'
                hx-target="#phrases"
                hx-swap="innerHTML"
                variant="danger"
                hx-trigger="click">Remove Delay</wa-button>
        </div>""")

def add_image_slot_button(chapter, phrase_xml, page):
    next_sibling = phrase_xml.find_next_sibling()

    if next_sibling and next_sibling.name == "image":
        # this phrase already has an image slot immediately following it, so we shouldn't offer to add another one.
        return ""
    
    add_image_slot_url = url_for(
        "library.book.chapter.audio.add_image_slot",
        author=chapter.author.name,
        title=chapter.title,
        chapter_number=chapter.number,
        language=chapter.language,
        phrase_index=phrase_xml["index"],
    )
    return f"""<wa-button
            hx-post="{add_image_slot_url}"
            hx-vals='{{"page": "{page}"}}'
            hx-target="#phrases"
            hx-swap="afterend"
            variant="brand"
            hx-trigger="click">&#x2193; Add Image Slot &#x2193;</wa-button>"""

def repronounce_button(chapter, phrase_xml):
    return f"""<wa-button
        hx-post="{ url_for('library.book.chapter.audio.pronunciation.repronounce_phrase', author=chapter.author.name, title=chapter.title, chapter_number=chapter.number, language=chapter.language, phrase_index=phrase_xml['index']) }"
        hx-target="#pronounce_{phrase_xml['index']}"
        hx-swap="outerHTML"
        hx-trigger="click">Re-pronounce</wa-button>"""

# hx-on::after-request="htmx.remove( htmx.find('#phrase-{phrase_xml['index']}'))"

def merge_with_previous_button(chapter, phrase_xml, previous_phrase_xml, page):
    if previous_phrase_xml:
        return f"""<wa-button
                hx-post="{ url_for('library.book.chapter.audio.merge_with_previous', author=chapter.author.name, title=chapter.title, chapter_number=chapter.number, language=chapter.language) }"
                hx-target="#phrase-{previous_phrase_xml['index']}"
                hx-vals='{{"page": "{page}"}}'
                hx-swap="innerHTML"
                hx-on::after-request="htmx.remove( htmx.find('#phrase-{phrase_xml['index']}'))"
                hx-trigger="click">Merge with previous</wa-button>"""
    else:
        return ""


def phrase_typography_src(chapter, phrase_xml):
    """
    Return the src of an image of the page right around phrase_xml.  So we
    render the page, use the highlight geometry to locate it, then crop to page
    width and the phrase dimensions with some padding above and below.

    Smart move is to generate this from redis contents instead of filesystem.
    If the typography doesn't exist, we generate it.  It isn't scary.
    """
    # find redis contents related to this phrase (how?) assemble image (could be
    # pasting multiple images togehter) determine phrase dimensions (same
    # pasting of rainbow, then crop after filtering and bbox-ing rainbow?
    
    # write (to disk?), create suitable URL.
    # TODO: new function to serve these images

    return chapter.get_highlighted_text_snippet(phrase_xml, force=False)

    # img = page_segment.from_offset(
    #     chapter=chapter,
    #     phrase_xml=phrase_xml,
    #     top_index=0,
    #     force=False,
    #     no_background=False
    # )
    
    # # close. find the yellow region, make a bounding box, crop to the bbox plus
    # # some padding, save that.
    
    # # using numpy to filter the non-yellow to transparent.
    # np_img = np.array(img)
    # yellow_mask = (np_img[:,:,0] > 200) & (np_img[:,:,1] > 200) & (np_img[:,:,2] < 100)

    # coordinates = np.argwhere(yellow_mask)
    # if coordinates.size > 0:
    #     y_min, x_min = coordinates.min(axis=0)
    #     y_max, x_max = coordinates.max(axis=0)        

    #     # crop 'img' to the bounding box with some padding
    #     # but -- stay inside the image.
    #     padding = 75
    #     x_min = 0
    #     x_max = np_img.shape[1]

    #     y_min = max(y_min - padding, 0)
    #     y_max = min(y_max + padding, np_img.shape[0])

    #     img = img.crop((x_min, y_min, x_max, y_max))

    #     img.save(
    #         os.path.join(
    #             const.LIBRARY_DIR,
    #             paragraph_dir,
    #             f"phrase_{phrase_xml['index']}.png"
    #         )
    #     )
    # else:
    #     log.error('No highlight region found.')

    # phrase_text_image_url = url_for(
    #     'library.book.chapter.images.'
    # )

    # return f"phrase_{phrase_xml['index']}.png"


def underscore_to_italics(phrase_text):
    if "_" in phrase_text:
        log.debug("Adding italics...")
        phrase_text = phrase_text.replace(r"&", r"\&")
        phrase_text = re.sub(
            # r"_([-\' \’\?a-zA-Z \.!]+)_", 
            r"_([^_]+)_", 
            r"\\textit{\g<1>}", 
            phrase_text
        )
    return phrase_text


def hyphen_fixer(phrase_text):
    if "—" in phrase_text:
        log.debug("Replacing — with --...")
        phrase_text = phrase_text.replace("—", "--")
    return phrase_text


def title_spread(title):
    """
    If the title is too long, we need to split it into two lines.
    """
    if len(title) > 29:
        # find the space nearest but not exceeding the halfway point
        middle = len(title) // 2
        found = title[middle]
        
        while found != " " and middle > 0:
            middle -= 1
            found = title[middle]
        
        if found != " ":
            raise ValueError(f'Failed to find a good place to break title: {title}')

        left = title[:middle].strip()
        right = title[middle:].strip()
        return [left, right]
    else:
        return [title, ]


def format_section_header(phrase_text, phrase_xml):
    if phrase_xml.attrs.get("type", "") == "section_header":
        log.info('Typesetting section header...')
        out = "\n" + r"\vspace{0.5cm}" + "\n"
        for segment in title_spread(phrase_text):
            out += (r"\centerline{\textbf{\normalsize %s}}" % segment) + "\n"
        out += r"\vspace{0.25cm}" + "\n"
        return out
    else:
        return phrase_text


def phrase_latex(chapter, phrase_xml):
    # fullscreen == no typesetting
    if phrase_xml.find_parent('paragraph').attrs.get('fullscreen', 'false') == 'true':
        return ""
    
    phrase = phrase_xml.get_text(
        separator=" ",
        strip=True
    )
    # TODO: dinkus
    # TODO: chapter title
    phrase = format_section_header(phrase, phrase_xml)
    phrase = underscore_to_italics(phrase)
    phrase = hyphen_fixer(phrase)
    # TODO: poetry lines end in \nobreak\\

    if "latex" not in phrase_xml.attrs:
        phrase_xml.attrs["latex"] = f"{phrase}\n"

    return f"""
    <wa-textarea
        class="phrase-latex"
        label="LaTeX"
        resize="auto"
        rainbow="false"
        index="{phrase_xml['index']}"
        value="{phrase_xml.attrs.get('latex', '')}">
    </wa-textarea>
    """


def phrase_latex_rainbow(chapter, phrase_xml):
    if phrase_xml.find_parent('paragraph').attrs.get('fullscreen', 'false') == 'true':
        return ""

    phrase = phrase_xml.get_text(
        separator=" ",
        strip=True
    )

    phrase = format_section_header(phrase, phrase_xml)
    phrase = underscore_to_italics(phrase)
    phrase = hyphen_fixer(phrase)
    
    # limit to 24 bits
    rainbow_int = (int(phrase_xml['index']) + 10) % 16777216
    if "latex_rainbow" not in phrase_xml.attrs:
        phrase_xml.attrs['latex_rainbow'] = f"\\color{{B{rainbow_int:X}}}\\highLight[B{rainbow_int:X}]{{{phrase}}}\n"

    return f"""
    <wa-textarea
        class="phrase-latex"
        label="LaTeX Highlight Rainbow"
        resize="auto"
        rainbow="true"
        index="{phrase_xml['index']}"
        rows="1"        
        value="{phrase_xml.attrs.get('latex_rainbow', '')}">
    </wa-textarea>
    """


def phrase_editor(chapter, phrase_xml, page, split=None):
    """
    we are the <div id="phrase-{phrase['index']}" class="wa-card"> block
    for a specific phrase.
    """
    previous_phrase = phrase_xml.find_previous_sibling("phrase")
    phrase_speaker_value = phrase_xml.attrs.get("speaker", "Narrator")

    if split:
        # if we are engaged in a split operation, we want the split editor to
        # consume the space normally reserved for the phrase itself.
        text_phrase = split_phrase_text(chapter, phrase_xml, split, page)
    else:
        text_phrase = base_text_phrase(chapter, phrase_xml)
    
    paragraph = phrase_xml.find_parent("paragraph")
    if "has-text=false" in paragraph.attrs.get("tags", "").split(","):
        phrase_latex_image = ""
    else:
        # no visible text == no latex typography
        phrase_latex_image = f"""
                <div class="wa-cluster wa-align-items-start">
                    <img 
                        class="phrase-typography" 
                        src=" {chapter.get_highlighted_text_snippet(phrase_xml, force=False)}"></img>
                    {phrase_latex(chapter, phrase_xml)}
                </div>
        """

    return render_template(
        "phrase.html",
        page=page,
        phrase_xml=phrase_xml,
        chapter=chapter,
        pretty_duration=pretty_duration,
        speaker_selector=speaker_selector,
        text_phrase=text_phrase,
        phrase_latex_image=phrase_latex_image,
        pronunciation_textarea=pronunciation_textarea,
        pronunciation_helper=pronunciation_helper,
        audio_player=audio_player,
        generate_audio_button=generate_audio_button,
        repronounce_button=repronounce_button,
        merge_with_previous_button=merge_with_previous_button,
        add_image_slot_button=add_image_slot_button,
        add_edit_delay=add_edit_delay,
        split_phrase_button=split_phrase_button,
    )
    

    # {phrase_latex_rainbow(chapter, phrase_xml)}
    return out


def split_phrase_text(chapter, phrase_xml, split, page):
    raw_text = phrase_xml.get_text().strip()
    first_phrase, second_phrase = split

    if raw_text.strip() != f"{first_phrase.strip()} {second_phrase.strip()}".strip():
        log.error("Split phrases do not exactly match original phrase text.")
        log.error("raw_text: '%s'", raw_text)
        log.error("first_phrase: '%s'", first_phrase)
        log.error("second_phrase: '%s'", second_phrase)
        raise ValueError("Split phrases do not exactly match original phrase text.")

    
    set_split_url = url_for(
        "library.book.chapter.audio.set_split",
        **chapter.kwargs,
        phrase_index=phrase_xml['index'],
    )

    save_split_url = url_for(
        "library.book.chapter.audio.save_split_phrase",
        **chapter.kwargs,
        phrase_index=phrase_xml['index'],
    )

    # cancel_split_url = url_for(
    
    first_phrase = html.escape(first_phrase)
    second_phrase = html.escape(second_phrase)

    phrase_text = f"""
    <div class="wa-cluster" id="phrase-split-{phrase_xml['index']}">
        <div class="wa-stack">
            <wa-input
                class="phrase_split"
                name="first_phrase"
                value="{first_phrase}"
                hx-post="{set_split_url}"
                hx-target="#phrase-split-{phrase_xml['index']}"
                hx-swap="outerHTML"
                hx-trigger="change delay:100ms"></wa-input>
            
            <wa-input
                class="phrase_split"
                name="second_phrase"
                value="{second_phrase}"
                hx-post="{set_split_url}"
                hx-target="#phrase-split-{phrase_xml['index']}"
                hx-swap="outerHTML"
                hx-trigger="change delay:100ms"></wa-input>
        </div>

        <div class="wa-stack">
            <wa-button
                hx-post="{save_split_url}"
                hx-target="#phrases"
                hx-swap="innerHTML"
                hx-include="closest form"
                hx-vals='{{"page": "{page}" }}'
                hx-trigger="click">Apply Split</wa-button>
        </div>
    </div>"""

            # <wa-button
            #     hx-post="/{chapterurl}/audio/actions/cancel_split_phrase/{phrase['index']}"
            #     hx-target="#phrase-{phrase['index']}"
            #     hx-vals='{{"page": "{request.args.get("page", 1)}" }}'
            #     hx-swap="innerHTML"
            #     hx-trigger="click">Cancel Split</wa-button>

    return phrase_text


def speaker_selector(chapter, phrase_xml):
    all_characters = characters.get_all_characters(chapter)
    speakers = all_characters.keys()

    speaker_options = ""
    # log.info("Speakers: %s", speakers)
    for speaker_id in sorted(speakers):
        speaker_tag = all_characters[speaker_id]["tag"]
        speaker_name = all_characters[speaker_id]["name"]

        speaker_options += f"""
            <wa-option value="{speaker_tag}">{speaker_name}</wa-option>"""

    speaker = phrase_xml.get("speaker")
    if speaker not in speakers:
        log.error("Speaker %s not found in speakers list!!", speaker)

    assign_speaker_url = url_for(
        "library.book.chapter.audio.assign_phrase_speaker",
        **chapter.kwargs,
        phrase_index=phrase_xml['index'],
    )

    out = f"""
        <div class="wa-cluster">
            <wa-icon></wa-icon>  
            <wa-select
                label="Speaker"
                hx-post="{assign_speaker_url}"
                hx-trigger="change"
                hx-target="#speaker-choice-{phrase_xml['index']}"
                name="speaker" 
                value="{speaker}">
    {speaker_options}
            </wa-select>
        </div>"""

    return out


def left_chevron_button(page, errors_only):
    if page == 1:
        return """<wa-button appearance="outlined" disabled>
                    <wa-icon src="/static/fontawesome7/svgs/solid/chevron-left.svg"></wa-icon>
                </wa-button>"""
    else:
        return f"""<wa-button appearance="outlined"
                hx-get="phrases?page={page - 1}&errors_only={errors_only}" 
                hx-trigger="click" 
                hx-target="#phrases" 
                hx-push-url="?page={page - 1}&errors_only={errors_only}"
                hx-on::after-request="latex_editor_init()"
                hx-swap="innerHTML">
                    <wa-icon src="/static/fontawesome7/svgs/solid/chevron-left.svg"></wa-icon>
                </wa-button>"""


def right_chevron_button(page, errors_only, total_pages):
    if page == total_pages:
        return """<wa-button appearance="outlined" disabled>
                    <wa-icon src="/static/fontawesome7/svgs/solid/chevron-right.svg"></wa-icon>
                </wa-button>"""
    else:
        return f"""<wa-button appearance="outlined"
                hx-get="phrases?page={page + 1}&errors_only={errors_only}" 
                hx-target="#phrases" 
                hx-push-url="?page={page + 1}&errors_only={errors_only}"
                hx-swap="innerHTML">
                    <wa-icon src="/static/fontawesome7/svgs/solid/chevron-right.svg"></wa-icon>
                </wa-button>"""


def spacer_button():
    return """<wa-button appearance="outlined" disabled>...</wa-button>"""


def chosen_page_button(page):
    return f"""<wa-button 
        appearance="accent" 
        variant="brand">{page}
    </wa-button>"""


def clickable_page_button(page, errors_only=False):
    return f"""<wa-button 
        hx-get="phrases?page={page}&errors_only={errors_only}" 
        hx-trigger="click" 
        hx-target="#phrases" 
        hx-swap="innerHTML" 
        hx-push-url="?page={page}&errors_only={errors_only}"
        hx-on::after-request="latex_editor_init()"
        appearance="outlined">{page}
    </wa-button>"""


def page_button(chosen_page, button_page, errors_only=False):
    if chosen_page == button_page:
        return chosen_page_button(button_page)
    else:
        return clickable_page_button(button_page, errors_only=errors_only)


def generate_all_missing_audio_button(chapter):
    log.info(f"Generating all missing audio for chapter kwargs: {chapter.kwargs}")
    # {'author': 'L. Frank Baum', 'title': 'The Marvelous Land of Oz', 'chapter_number': 2, 'language': 'english'}

    return f"""<wa-button 
    hx-post="{ url_for('library.book.chapter.audio.generate_all_missing_audio', **(chapter.kwargs)) }"
    hx-on::before-request="beforeRequest(this,event)"
    hx-on::after-request="afterRequest(this,event)"
    hx-swap="outerHTML"
    variant="neutral"
    appearance="accent"
    size="m"
    class="">Generate All Missing Audio</wa-button>
    """


def regenerate_all_audio_button(chapter):
    log.info(f"Generating all audio for chapter kwargs: {chapter.kwargs}")
    # {'author': 'L. Frank Baum', 'title': 'The Marvelous Land of Oz', 'chapter_number': 2, 'language': 'english'}

    return f"""<wa-button 
    hx-post="{ url_for('library.book.chapter.audio.regenerate_all_audio', **(chapter.kwargs)) }"
    hx-on::before-request="beforeRequest(this,event)"
    hx-on::after-request="afterRequest(this,event)"
    hx-swap="outerHTML"
    variant="neutral"
    appearance="accent"
    size="m"
    class="">Generate All Audio</wa-button>
    """


def repronounce_where_missing_button(chapter):
    return generic_button(
        chapter.url,
        category="audio",
        tag="repronounce_missing_words",
        cosmetic="Repronounce Missing Words",
    )


def reset_audio_cache_json_button(chapter):
    return generic_button(
        chapter.url,
        category="audio",
        tag="reset_audio_cache_json",
        cosmetic="Reset Audio Cache",
    )


def recalculate_all_phrase_frames_button(chapter):
    recalculate_frames_url = url_for(
        "library.book.chapter.audio.recalculate_all_phrase_frames",
        **chapter.kwargs,
    )

    return f"""
    <wa-button
        hx-post="{recalculate_frames_url}" 
        hx-on::before-request="beforeRequest(this,event)"
        hx-on::after-request="afterRequest(this,event)" 
        hx-swap="outerHTML" 
        variant="neutral" 
        appearance="accent" 
        size="m" 
        class="">Recalculate All Phrase Frames</wa-button>"""
