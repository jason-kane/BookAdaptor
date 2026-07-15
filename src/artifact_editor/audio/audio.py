import contextlib
import eng_to_ipa
import copy
import json
import math
import os
import re
import wave
import decimal
from bs4 import BeautifulSoup

import fnv_hash_fast
from flask import request
import redis

from artifact_editor import llm
import const
import logger
from artifact_editor.characters.characters import name_to_tag
from artifact_editor.audio.pronunciation import pronunciation
from artifact_editor.audio.sound import htmx as sound_htmx
from artifact_editor.tools import (
    get_chapterdir,
    get_paragraph_dir,
    get_tag,
    wait_for,
)

from . import (
    audio_cache,
    htmx,
)

log = logger.log(__name__)


# def correct_fragdex_and_phrase_indexs(mybook):
#     paragraph_index = 0
#     for paragraph in mybook.soup.findAll("paragraph"):

#         if not str(paragraph).strip():
#             continue

#         paragraph.attrs['index'] = paragraph_index

#         fragdex = 0
#         for fragment in paragraph.contents:
#             if not str(fragment).strip():
#                 continue

#             if 'fragdex' in fragment.attrs:
#                 fragdex = int(fragment.attrs['fragdex'])
#             else:
#                 fragment.attrs['fragdex'] = fragdex

#             if fragment.name == "phrase":
#                 phrase_index = fragment.attrs.get('id')
#                 if phrase_index is None:
#                     phrase_index = f"{paragraph_index}_{fragdex}"
#                     fragment.attrs['id'] = phrase_index

#             fragdex += 1

#         paragraph_index += 1

#     return mybook


def phrase_sequence(chapter, page=1):
    """
    Get the phrase sequence for the given chapter directory.
    """
    log.info(f"phrase_sequence({chapter}, {page})")
    if page is None:
        try:
            page = int(request.args.get("page", None))
        except (TypeError, ValueError):
            page = 1
    else:
        page = int(page)

    # is the "errors only" checkbox checked? (it's state should be js-synced with the url parameter)
    errors_only = request.args.get("errors_only", "false").lower() == "true"

    phrase_out = ""

    all_paragraphs = chapter.paragraphs()
    total_paragraphs = len(all_paragraphs)

    if errors_only:
        # in error mode, we only show paragraphs with errors
        # so we want the 'page' numbered paragraph _with_ errors.
        paragraphs_with_errors = []
        # there is probably a clever beautiful soup way to do this lookup more
        # efficiently.
        for paragraph_xml in chapter.paragraphs():
            for phrase_xml in paragraph_xml.findAll("phrase"):
                if "❓" in phrase_xml.attrs.get("pronunciation", ""):
                    paragraphs_with_errors.append(paragraph_xml)
                    break

        paragraph = (
            paragraphs_with_errors[page - 1]
            if page <= len(paragraphs_with_errors)
            else None
        )
        all_paragraphs = paragraphs_with_errors
        total_paragraphs = len(all_paragraphs)
        log.info("Processing paragraph with error: %s", paragraph)
    else:
        # in paragraph mode, page == paragraph
        # use beautiful soup to find the paragraph with index=page-1

        paragraph = chapter.get_paragraph(page - 1)
        # paragraph = paragraphs[page - 1] if page <= len(paragraphs) else None
        log.info("Processing paragraph: %s", paragraph)

    # paragraph metadata
    phrase_out += """<wa-details summary="Paragraph Metadata">"""
    phrase_out += """<div class="label-on-left">"""

    if paragraph:
        for attr in paragraph.attrs:
            phrase_out += f"""<wa-input disabled label="{attr}" value="{paragraph.attrs[attr]}"></wa-input>"""

    phrase_out += "</div>"
    #################

    phrase_out += '<div class="wa-cluster">'
    if paragraph:
        if paragraph.attrs.get("fullscreen", "false") == "true":
            phrase_out += f"""
            <wa-button
                hx-post="paragraph/set_fullscreen"
                hx-target="#phrases"
                hx-swap="innerHTML"
                hx-vals='{{"paragraph_index": "{page - 1}", "fullscreen": "false"}}'
                value="{page - 1}">Disable Fullscreen</wa-button>
            """
        else:
            phrase_out += f"""
            <wa-button
                hx-post="paragraph/set_fullscreen"
                hx-target="#phrases"
                hx-swap="innerHTML"
                hx-vals='{{"paragraph_index": "{page - 1}", "fullscreen": "true"}}'
                value="{page - 1}">Enable Fullscreen</wa-button>
            """
    phrase_out += "</div>"
    phrase_out += "</wa-details>"

    image_index = 0
    frament_count = 0
    for fragment in getattr(paragraph, "contents", []):
        frament_count += 1
        log.info("Processing fragment: %s", fragment)

        if fragment.name == "phrase":
            if fragment.attrs.get("type", "") == "dinkus":
                # dinkus phrases are not editable.
                phrase_out += f"""
                    <form>
                        <div id="phrase-{fragment["index"]}" class="wa-card">
                            {htmx.dinkus_editor(fragment)}
                        </div>
                    </form>
                    <hr/>
                """
                continue
            else:
                if errors_only and "❓" not in fragment.attrs.get("pronunciation", ""):
                    continue

                # un-typed content
                phrase_out += htmx.phrase_editor(chapter, fragment, page)
                phrase_out += "<hr/>"
                continue

        elif fragment.name == "image":
            if errors_only:
                continue

            if "index" not in fragment.attrs:
                fragment.attrs["index"] = image_index
            else:
                # it knows better than we do.
                image_index = int(fragment.attrs["index"])

            # phrase_out += f'<div id="image-{fragment.attrs["index"]}" class="wa-card">'
            phrase_out += htmx.image_placeholder(chapter, fragment)
            phrase_out += "<hr/>"

            image_index += 1

        elif fragment.name == "sound":
            if errors_only:
                continue

            phrase_out += sound_htmx.sound_editor(chapter, fragment, page)
            phrase_out += "<hr/>"

        # [ Correct Pronunciation (microphone) ]
        # https://huggingface.co/neurlang/ipa-whisper-base TODO: web api to
        # record audio from the microphone save it to a wav file use whisper to
        # translate into IPA Show ours on top, your version on the bottom,
        # highlighted differences middle is editable, starts with the "old" IPA
        # and [play] [save] buttons.
        #
        # IE:  Anyone can change how a line is spoken in the characters voice
        # without knowing IPA, and you can spot-correct single words with
        # minimal effort.
    log.info("%s fragments processed", frament_count)

    # paginator at the top and bottom of the page, because I'm lazy.
    out = ""
    out += simple_paginator(
        page=page,
        errors_only=errors_only,
        total_pages=total_paragraphs,
    )

    if errors_only:
        state = " checked"
    else:
        state = ""

    out += (
        '<div class="wa-split">'
        "<div></div>"
        "<div>"
        f"""<wa-checkbox
        hx-get="phrases?errors_only={not errors_only}"
        hx-push-url="?errors_only={not errors_only}"
        hx-target="#phrases"
        {state}
        >Errors Only</wa-checkbox>"""
        "</div>"
        "</div>"
    )

    # the actual phrases
    out += phrase_out

    out += simple_paginator(
        page=page,
        errors_only=errors_only,
        total_pages=total_paragraphs,
    )

    return out


def simple_paginator(page, errors_only, total_pages):
    """
    Create a dumb pagination object for the audio page.
    """
    # total_pages = (total_phrases + per_page - 1) // per_page
    # return {
    #     'per_page': per_page,
    #     'page': page,
    #     'total_phrases': total_phrases,
    #     'total_pages': total_pages,
    #     'has_next': page < total_pages,
    #     'has_prev': page > 1
    # }
    text_description = f"Showing phrases in paragraph {page} of {total_pages}"

    # <div class="wa-placeholder"></div>
    #         <wa-divider></wa-divider>

    # little stuff matters
    out = f"""
           <div class="wa-stack">      
            <div class="wa-split">
              <span class="wa-caption-l">{text_description}</span>
              <wa-button-group orientation="horizontal">
                {htmx.left_chevron_button(page, errors_only)} """

    # if there are <= 9 pages, show them all
    if total_pages <= 9:
        log.warning(f"Showing all {total_pages} audio pages in nav")
        # show all pages, highlighting the current page.
        for p in range(1, total_pages + 1):
            if p == page:
                # selected
                out += htmx.page_button(page, p, errors_only=errors_only)
            else:
                out += htmx.page_button(page, p, errors_only=errors_only)

    # there are more pages than what we want to show.
    else:
        buckets = []
        #
        # there are seven slots
        #
        # first gets page 1
        buckets.append(htmx.page_button(page, 1, errors_only=errors_only))

        first_over = True
        first_under = True
        for page_option in range(2, total_pages):
            span = 2
            if page <= 4 or (total_pages - page < 4):
                span = 4 - page

            span = max(2, span)

            if (page - page_option) > span:
                if first_over:
                    # if we are more than two pages away, we show a spacer
                    buckets.append(htmx.spacer_button())
                    first_over = False
                continue

            if (page_option - page) > span:
                if first_under:
                    # if we are more than two pages away, we show a spacer
                    buckets.append(htmx.spacer_button())
                    first_under = False
                continue

            buckets.append(htmx.page_button(page, page_option, errors_only=errors_only))

        buckets.append(htmx.page_button(page, total_pages, errors_only=errors_only))

        out += "\n".join(buckets)

    out += f"""
                {htmx.right_chevron_button(page, errors_only, total_pages)}
              </wa-button-group>
            </div>
          </div>
    """
    return out


def recalculate_image_frames(chapter):
    """
    Recalculate the display duration of each image in the book.
    """
    last_image = None
    frames = 0
    for paragraph in chapter.get_xml().findAll("paragraph"):
        for fragment in paragraph.contents:
            if fragment.name == "phrase":
                try:
                    frames += int(fragment.attrs.get("frames", "0"))
                except ValueError:
                    log.error("Invalid frame value in phrase %s", fragment)
                    raise

            elif fragment.name in [
                "image",
            ]:
                if last_image is not None:
                    last_image.attrs["frames"] = frames

                last_image = fragment
                frames = 0

    # the final image doesn't have a next image to trigger the duration save
    last_image.attrs["frames"] = frames

    chapter.save_xml()


def recalculate_paragraph_durations(chapter):
    """
    This is half of the scrolling rate calculation.
    """
    for paragraph in chapter.get_xml().find("book").children:
        if not hasattr(paragraph, "name"):
            continue

        if paragraph.name == "paragraph":
            paragraph.attrs["frames"] = 0
            for fragment in paragraph.contents:
                if fragment is None:
                    continue

                if fragment.name == "phrase":
                    paragraph.attrs["frames"] += int(
                        float(fragment.attrs.get("frames", 0))
                    )

    chapter.save_xml()


def get_wavfile_filename(phrase_xml, chapter):
    """
    Gets the absolute path of the wavfile for a given phrase.
    If the phrase doesn't have a src attribute, we generate one.
    """
    paragraph_xml = phrase_xml.find_parent("paragraph")

    src = phrase_xml.attrs.get("src", None)
    if src is None:
        # make a unique content based identifier tag
        spoken_text = phrase_xml.get_text().strip()
        tag = get_tag(spoken_text)

        wavfile = os.path.join(
            const.LIBRARY_DIR,
            chapter.get_paragraph_dir(paragraph_xml.attrs["index"]),
            f"ph_{phrase_xml['index']}_{tag}.wav",
        )
        phrase_xml.attrs["src"] = f"ph_{phrase_xml['index']}_{tag}.wav"
    else:
        phrase_xml.attrs["src"] = os.path.basename(phrase_xml.attrs["src"])

        wavfile = os.path.join(
            const.LIBRARY_DIR,
            chapter.get_paragraph_dir(paragraph_xml.attrs["index"]),
            phrase_xml.attrs.get("src", None),
        )
        log.info(f"wavfile: {wavfile}")

    return wavfile


def get_wav_duration(wavfile):
    try:
        with contextlib.closing(wave.open(wavfile, "rb")) as p:
            frame_count = p.getnframes()
            frame_rate = p.getframerate()
            # how long the talking takes
            log.info("%s frames at %s fps", frame_count, frame_rate)
            duration = decimal.Decimal(frame_count) / decimal.Decimal(frame_rate)
    except EOFError:
        log.error(f"Corrupt or invalid {wavfile}")
        raise
    return duration


def wav_append_delay(wavfile, delay_amount, outfile=None):
    # params = (1, 2, 16000, 0, 'NONE', 'not compressed')

    if os.path.exists(wavfile):
        with contextlib.closing(wave.open(wavfile, "rb")) as input:
            nchannels, sampwidth, framerate, nframes, comptype, compname = (
                input.getparams()
            )
            all_frames = input.readframes(nframes)
            original_duration = decimal.Decimal(nframes) / decimal.Decimal(framerate)

    else:
        all_frames = b""
        nchannels = 1
        sampwidth = 2
        framerate = 16000
        nframes = 0
        comptype = "NONE"
        compname = "not compressed"
        original_duration = 0

    delay_wav_frames = int(framerate * delay_amount)

    log.info(
        f"Duration was {original_duration} seconds ({nframes} frames); adding {delay_amount}s ({delay_wav_frames} frames) of silence AFTER the audio..."
    )

    if outfile is None:
        outfile = wavfile

    log.debug(f"adding {delay_wav_frames} frames of delay")
    with contextlib.closing(wave.open(outfile, "wb")) as out:
        out.setparams((nchannels, sampwidth, framerate, nframes, comptype, compname))
        out.writeframes(all_frames)
        out.writeframes(b"\0" * (sampwidth * delay_wav_frames))


def speak(chapter, phrase_xml, wavfile, workdir, delay: float = 0):
    """
    wavfile is an absolute path/fn
    """
    log.info(f"speak(... {phrase_xml=} ...):")

    if os.path.exists(wavfile):
        return wavfile, None

    log.info("Cache miss: %s", wavfile)

    pronunciation_fn = wavfile + ".pronunciation"
    if os.path.exists(pronunciation_fn):
        log.info("Removing stale pronunciation file: %s", pronunciation_fn)
        os.unlink(pronunciation_fn)

    llm.text_2_audio(
        chapter,
        phrase_xml.get_text(),
        phrase_xml.attrs.get("speaker", "Narrator"),
        wavfile,
        force=False
    )

    if not os.path.exists(wavfile):
        # for whatever reason, there isn't an error, or a wavfile. this
        # usually means the string is "***" or something similarly
        # unpronouncable.
        log.error(f"Audio file {wavfile} does not exist after generation.")
        return None, None

    try:
        get_wav_duration(wavfile)
    except FileNotFoundError:
        log.error(f"Missing expected file {wavfile}")
        if os.path.exists(wavfile):
            log.error("umm, yeah, wave() is kind of stupid")
        else:
            log.error("For real!  It is legit missing.")
        raise

    except EOFError:
        log.error(f"Corrupt or invalid {wavfile}")
        os.unlink(wavfile)
        return None, None

    if delay > 0:
        wav_append_delay(wavfile, delay)

    # global_pronunciation_map = {v['word']: v for v in pronunciation.global_pronunciation_list(chapter)}

    pronunciation_ipa = None
    # did speak() leave behind a pronunciation treat?
    if os.path.exists(wavfile + ".pronunciation"):
        log.info('Found pronunciation file: %s', wavfile + ".pronunciation")
        # were there any problems?
        with open(wavfile + ".pronunciation", "r") as h:
            pronunciation_ipa = h.read().strip()
            log.info('Raw pronunciation IPA: "%s"', pronunciation_ipa)

            # fix some whitespace problems
            pronunciation_ipa = pronunciation_ipa.replace(" , ", ", ")

            if "❓" in pronunciation_ipa:
                log.warning("Found unpronounceable markers in pronunciation ipa")
                # one or more words could not be pronounced.
                # but.. which word(s)?
                # and Lady Castlemaine, and some other executive heads of that kind;
                # and lˈAdi ❓, and sˌʌm ˈʌðə ɪɡzˈɛkjʊtɪv hˈɛdz ɒv ðˈat kˈInd;
                # can we just split them both?
                #  log.info('soup: %s', repr(soup))
                # soup = BeautifulSoup(str(phrase_xml), "html.parser")
                # phrase_as_words = soup.find("phrase").text
                phrase_as_words = phrase_xml.get_text().strip()

                # ve--ry _good!_ I’ll beat
                phrase_as_words = (
                    phrase_as_words.replace("--", " ")
                    .replace("_", "")
                    .replace("[", "")
                    .replace("]", "")
                    .replace("/", " ")
                )

                log.info(f"Phrase as words: {phrase_as_words.split()}")
                log.info(f"Pronunciation IPA: {pronunciation_ipa.split()}")

                # we want to know exactly which word is the problem.
                for word, pron in zip(
                    phrase_as_words.split(), pronunciation_ipa.split()
                ):
                    # without trailing punctuation
                    pron = pron.strip("_,;.!?:()\"’“”'")
                    word = word.strip("_,;.!?:()\"’“”'").lower()

                    if "❓" in pron:
                        log.info(f'Attempting to pronounce {word} using eng_to_ipa.jonvert()')
                        p = eng_to_ipa.jonvert(word)
                        if "*" in p:
                            log.error(f"Eng-to-IPA also failed for word: {word}")
                            pronunciation.add_word_pronunciation(
                                chapter, word, pronunciation=pron
                            )
                        else:
                            log.info(f"Eng-to-IPA suggests {p} for word: {word}")
                            pronunciation_ipa = pronunciation_ipa.replace("❓", p, 1)
                            log.info(
                                f"Updated pronunciation IPA: {word} -> {pronunciation_ipa}"
                            )
                            pronunciation.add_word_pronunciation(
                                chapter, word, pronunciation=p
                            )

                    else:
                        log.info(f'I think {word} should be pronounced: "{pron}"')
            else:
                log.info("All words pronounced successfully.")

    return wavfile, pronunciation_ipa


def try_cmu_dict(chapter):
    """
    Scan the global pronunciation dictionary and try to fill in any missing
    pronunciations using the CMU Pronouncing Dictionary.
    """
    log.info("try_cmu_dict(%s):", chapter)
    pronunciation_dict = pronunciation.get_global_pronunciations(chapter)

    for key in pronunciation_dict.keys():
        p = pronunciation_dict[key]

        if p["pronunciation"] == "":
            log.info(f'Found missing pronunciation for word: {p["word"]}')
            # Try to get pronunciation from CMU dict
            cmu_pron = eng_to_ipa.jonvert(p["word"])

            if "*" not in cmu_pron:
                log.info(f'Found CMU pronunciation for {p["word"]}: {cmu_pron}')
                pronunciation.add_word_pronunciation(
                    chapter, p["word"], pronunciation=cmu_pron
                )
            else:
                log.info(f'No CMU pronunciation found for {p["word"]}')
        else:
            log.info(f'Using existing pronunciation: {p["word"]}: {p["pronunciation"]}')

    return


def find_unpronouncable_words(chapter):
    """
    Scan the chapter for unpronouncable words and add them to the global
    pronunciation dictionary with empty pronunciations.
    """
    unpronouncable_words = set()

    for paragraph in chapter.get_xml().findAll("paragraph"):
        for phrase_xml in paragraph.findAll("phrase"):
            spoken_text = phrase_xml.get_text().strip()
            log.info(f"Checking phrase: {spoken_text}")

            pronunciation_ipa = phrase_xml.attrs.get("pronunciation", None)

            if pronunciation_ipa is None:
                log.info("Phrase has no pronunciation, need to generate")
                continue

            if "❓" not in pronunciation_ipa:
                log.info(
                    "Phrase has pronunciation and is fully pronounceable, skipping"
                )
                continue

            else:
                log.info(f"Found unpronouncable words in phrase: {spoken_text}")
                phrase_as_words = spoken_text.split()

                for word, pron in zip(phrase_as_words, pronunciation_ipa.split()):
                    pron = pron.strip(",;.!?:()\"'")

                    if pron == "❓":
                        word = word.lower().strip(",;.!?:()\"'")
                        unpronouncable_words.add(word)

    for word in unpronouncable_words:
        pronunciation.add_word_pronunciation(chapter, word, pronunciation="")

    return unpronouncable_words


def repronounce_phrase(chapter, phrase_xml):
    # discard the current value
    phrase_xml.attrs.pop("pronunciation", None)

    wavfile = get_wavfile_filename(phrase_xml, chapter)
    if os.path.exists(wavfile):
        os.remove(wavfile)
        log.info(f"Deleted existing wav file: {wavfile}")

    if os.path.exists(wavfile + ".pronunciation"):
        os.remove(wavfile + ".pronunciation")
        log.info(f"Deleted existing pronunciation file: {wavfile}.pronunciation")

    phrase_xml.attrs["pronunciation_filter"] = request.form.get(
        "pronunciation_filter", "2000"
    )

    # so sloppy audio.speak(), you are embarassing.
    _, pronunciation = speak(
        chapter=chapter,
        phrase_xml=phrase_xml,
        wavfile=wavfile,
        workdir=os.path.join(
            const.LIBRARY_DIR,
            os.path.dirname(wavfile).lstrip(
                "/"
            ),  # because we may not have paragraph_dir available.
        ),
        delay=float(phrase_xml.attrs.get("delay", 0)),
    )

    # in-place pad audio with silence, just enough to be an exact fps mulitple
    log.info("[regenerate_phrase] Padding audio file to frame: %s", wavfile)
    wav_pad_to_frame(wavfile)

    phrase_xml.attrs["src"] = os.path.basename(wavfile)
    duration = get_wav_duration(wavfile)

    log.info("Duration measured as: %s seconds", duration)
    phrase_xml.attrs["duration"] = str(duration)
    phrase_xml.attrs["frames"] = int(duration * const.FPS)

    if pronunciation:
        phrase_xml.attrs["pronunciation"] = pronunciation

    recalculate_image_frames(chapter)
    chapter.save_xml()

    return pronunciation


def repronounce_where_missing(chapter):
    """
    Scan the chapter for words with missing pronunciations and
    attempt to repronounce them.
    """
    for paragraph in chapter.get_xml().findAll("paragraph"):
        for phrase_xml in paragraph.findAll("phrase"):
            spoken_text = phrase_xml.get_text().strip()
            log.info(f"Checking phrase: {spoken_text}")

            pronunciation_ipa = phrase_xml.attrs.get("pronunciation", None)

            if pronunciation_ipa is None:
                log.info("Phrase has no pronunciation, need to generate")
                continue

            if "❓" not in pronunciation_ipa:
                log.info(
                    "Phrase has pronunciation and is fully pronounceable, skipping"
                )
                continue

            else:
                log.info(f"Found unpronouncable words in phrase: {pronunciation_ipa}")
                repronounce_phrase(chapter, phrase_xml)

    chapter.save_xml()
    return


def wav_pad_to_frame(wavfile):
    """
    Add frames of silence to the end of wavfile so it lines up with a video
    frame boundary at the current framerate.

    Overwrites wavfile
    """
    with contextlib.closing(wave.open(wavfile, "rb")) as input:
        nchannels, sampwidth, framerate, nframes, comptype, compname = input.getparams()
        all_frames = input.readframes(nframes)
        original_duration = nframes / framerate

        # wav_per_frame = framerate / const.FPS
        # round up to the nearest multiple of wav_per_frame
        # desired_duration_frames = int(math.ceil(nframes / wav_per_frame) * wav_per_frame)
        log.info(
            f"We want to know how many audio frames at {framerate}fps of silence we need "
            "to add to make our total number of frames evenly divisible by const.FPS"
        )
        wav_per_frame = decimal.Decimal(framerate) / decimal.Decimal(const.FPS)
        log.info("Each video frame is %s audio frames", wav_per_frame)
        desired_duration_frames = math.ceil(nframes / wav_per_frame) * wav_per_frame

        delay_wav_frames = desired_duration_frames - nframes
        desired_duration_seconds = desired_duration_frames / framerate

    log.info(f"Duration was {original_duration} seconds ({nframes} audio frames);")
    log.info(f"Desired duration is {desired_duration_seconds} seconds.")
    log.info(
        f"Adding {delay_wav_frames} frames of silence ({delay_wav_frames/framerate}s) to align to video frame boundary..."
    )
    with contextlib.closing(wave.open(wavfile, "wb")) as out:
        out.setparams((nchannels, sampwidth, framerate, nframes, comptype, compname))
        log.info("sampwidth: %s", sampwidth)
        out.writeframes(all_frames)
        out.writeframes(b"\0" * (sampwidth * int(delay_wav_frames)))

    expected_duration = (nframes + delay_wav_frames) / framerate
    log.info(f"Expected duration is {expected_duration} seconds.")

    actual_duration = get_wav_duration(wavfile)
    log.info(f"Actual duration is {actual_duration} seconds.")
    assert math.isclose(
        (actual_duration), expected_duration, rel_tol=1e-4
    ), f"Expected {expected_duration}, got {actual_duration}"


def from_xml(soup, chapterdir, wavfile, workdir, fragdex):
    """
    Convert a BeautifulSoup object to an audio file
    """
    # remove any images
    onion = copy.copy(soup)
    # all the 'image' tags harikiri and yank themselves
    # out of the soup.
    [x.extract() for x in onion.findAll("image")]

    # the soup we were called with an <audio>...</audio> xml
    # fragment.
    all_child_text = str(onion.decode_contents().strip())  # .get_text()

    # it isn't just xml, it's xml with some latex bits.  We don't want
    # the latex bits to confuse the TTS, so we strip them out.
    all_child_text = all_child_text.replace(r"\nobreak", "")
    delay = float(soup.attrs.get("delay", 0))

    if all_child_text:
        log.info(f"Speaking: {all_child_text}")

        if not os.path.exists(os.path.join(const.LIBRARY_DIR, wavfile.lstrip("/"))):
            speak(
                chapterdir=chapterdir,
                phrase_xml=onion,  # str(all_child_text),
                wavfile=wavfile,
                workdir=workdir,
                fragdex=fragdex,
                delay=delay,
            )

        if os.path.exists(wavfile):
            wav_append_delay(os.path.join(const.LIBRARY_DIR, wavfile.lstrip("/")), 0.2)

            wav_pad_to_frame(os.path.join(const.LIBRARY_DIR, wavfile.lstrip("/")))

            return wavfile
        else:
            return None

    log.error("Empty audio fragment")
    return None


def generate_audio(chapter):
    """
    Creates a new audio_cache.json if it does not exist.
    """

    cache = audio_cache.get_audio_cache(chapter.chapterdir)

    # audio_cache doesn't exist until the whole book has been processed
    if len(cache) == 0:
        paragraphs = []
        paragraph_index = 0
        for paragraph in chapter.get_xml().findAll("paragraph"):
            audio_segments = []
            if not str(paragraph).strip():
                continue

            # # Make sure paragraph_dir is set and exists
            # if paragraph.attrs.get("dir", None):
            #     paragraph_dir = paragraph.attrs["dir"].lstrip("/")
            # else:
            #     paragraph_dir = os.path.join(chapterdir, "paragraphs", f"{paragraph_index:06}")
            #     paragraph.attrs["dir"] = paragraph_dir.lstrip("/")

            # log.info(f"Creating {const.LIBRARY_DIR=} + {paragraph_dir=}")

            paragraph_dir = chapter.get_paragraph_dir(paragraph.attrs["index"])

            paragraph_dir_full = os.path.join(
                const.LIBRARY_DIR,
                paragraph_dir,
            )

            log.info(f"Creating {paragraph_dir_full=}")
            os.makedirs(paragraph_dir_full, exist_ok=True)

            # drop a fragment.xml for diagnostics
            with open(os.path.join(paragraph_dir_full, "fragment.xml"), "w") as h:
                h.write(paragraph.prettify())

            fragdex = 1
            if paragraph.name == "paragraph":
                for fragment in paragraph.contents:
                    if not str(fragment).strip():
                        continue

                    if fragment.name == "phrase":
                        src = fragment.attrs.get("src")
                        spoken_text = fragment.get_text().strip()
                        speaker = fragment.attrs.get("speaker", "Narrator")

                        # speakers = audio.soup_to_set_of_characters(
                        #    fragment
                        # )
                        # log.info('This segment of text has speakers: %s', speakers)

                        # if len(speakers) == 0:
                        #    speaker = fragment.attrs.get(
                        #        'speaker', 'Narrator'
                        #    )

                        # if len(speakers) == 1:
                        #     speaker = speakers.pop()
                        #     fragment['speaker'] = speaker

                        if src is None:
                            tag = get_tag(spoken_text)

                            audio_fn = os.path.join(
                                paragraph_dir, f"ph_{fragdex}_{tag}.wav"
                            )

                            if not os.path.exists(audio_fn):
                                log.info("Creating %s for %s", audio_fn, spoken_text)
                                from_xml(
                                    soup=fragment,
                                    chapterdir=chapter.chapterdir,
                                    wavfile=audio_fn,
                                    workdir=os.path.join(
                                        paragraph_dir_full.lstrip("/")
                                    ),
                                    fragdex=fragdex,
                                )

                            if os.path.exists(
                                os.path.join(const.LIBRARY_DIR, audio_fn)
                            ):
                                duration = get_wav_duration(
                                    os.path.join(const.LIBRARY_DIR, audio_fn)
                                )

                                fragment.attrs["duration"] = duration
                                # audio.from_xml() has already padded the wav file to fit
                                # exactly in an int() number of video frames and account
                                # for the 0.2s of padding that is added between phrases by the assembler.
                                # we need this to be accurate or we won't have enough/too many frames.
                                fragment.attrs["frames"] = int(duration * const.FPS)

                                # src = os.path.join("/", audio_fn)
                                fragment.attrs["src"] = audio_fn
                                fragment.attrs["fragdex"] = fragdex
                            else:
                                continue

                        # this madness is over.
                        # phrase_id = fragment.attrs.get('id')
                        # if phrase_id is None:
                        #     phrase_id = f"{paragraph_index}_{fragdex}"
                        #     fragment.attrs['id'] = phrase_id

                        # audio_cache
                        audio_segments.append(
                            {
                                "src": src,
                                "text": spoken_text,
                                "speaker": speaker,
                                "fragdex": fragdex,
                                "id": fragment.attrs.get("id", ""),
                                "delay": fragment.attrs.get("delay", 0),
                            }
                        )

                        fragdex += 1

            if audio_segments:
                if "id" not in paragraph.attrs:
                    # Assign a unique ID if not present
                    text = " ".join(
                        [audio_segments[i]["text"] for i in range(len(audio_segments))]
                    )
                    tag = re.sub(r"[^a-z_A-Z0-9]", "_", text)

                    paragraph.attrs["id"] = "%s_%s_%s" % (
                        paragraph_index,
                        tag[:80],
                        fnv_hash_fast.fnv1a_32(tag.encode("utf-8", errors="ignore")),
                    )

                paragraphs.append(
                    {
                        "id": paragraph.attrs.get("id"),
                        "dir": paragraph_dir,
                        "text_fragment": audio_segments[0]["text"][:40],
                        "audio_segments": audio_segments,
                    }
                )
            elif "id" not in paragraph.attrs:
                # no audio segments
                paragraph.attrs["id"] = "%s" % paragraph_index

            paragraph_index += 1

        chapter.save_xml()

        # Save the audio cache after processing all chapters
        audio_cache.save_audio_cache(chapter.chapterdir, paragraphs)

        cache = audio_cache.get_audio_cache(chapter.chapterdir)

    return cache


def generate_all_audio(chapter, force=True):
    log.info("[generate_all_audio] (%s, force=%s)", chapter, force)

    # we're essentially clicking 'regenerate' on everything
    # that doesn't have a wav file.
    for phrase_xml in chapter.get_xml().findAll("phrase"):
        if phrase_xml.attrs.get("type", "") == "dinkus":
            # dinkus phrases are not spoken.
            paragraph_xml = phrase_xml.find_parent("paragraph")
            duration = float(phrase_xml.attrs.get("duration", "0.75"))
            wavfile = f"ph_{phrase_xml['index']}_{duration:.2f}.wav"

            phrase_xml.attrs["src"] = wavfile
            abs_wavfile = os.path.join(
                const.LIBRARY_DIR, paragraph_xml.attrs["dir"].lstrip("/"), wavfile
            )

            # wipe any existing silence wav, faster than opening it up
            # and verifying the duration.
            if os.path.exists(abs_wavfile):
                os.unlink(abs_wavfile)

            # create a wav file with the given duration of silence
            wav_append_delay(abs_wavfile, duration, outfile=None)
            continue
        elif (
            force
            or "src" not in phrase_xml.attrs
            or not os.path.exists(phrase_xml.attrs["src"])
        ):
            phrase_index = phrase_xml.attrs["index"]
            log.info(
                f"[generate_all_audio] calling get_wavfile_filename({phrase_xml}, {chapter.chapterdir})"
            )
            wavfile = get_wavfile_filename(phrase_xml, chapter)

            if wavfile is None:
                log.error(
                    f"Phrase with id {phrase_index} not found in {chapter.chapterdir}"
                )
                return "Phrase not found", 404

            phrase_xml = chapter.get_phrase(phrase_index)
            if os.path.exists(wavfile):
                if force:
                    # the simple way to avoid deeper caching
                    log.info(f"Force enabled -- Deleting previous wav file: {wavfile}")
                    os.remove(wavfile)
                else:
                    log.info(
                        f"Phrase {phrase_index} already has an audio file: {wavfile}"
                    )
                    continue

            # _, pronunciation = speak(
            #     chapter=chapter,
            #     phrase_xml=phrase_xml,
            #     wavfile=wavfile,
            #     workdir=os.path.join(os.path.dirname(wavfile).lstrip("/")),
            #     delay=float(phrase_xml.attrs.get("delay", 0)),
            # )
            llm.text_2_audio(
                chapter,
                phrase_xml.get_text(),
                phrase_xml.attrs.get("speaker", "Narrator"),
                wavfile,
                force=False
            )

            # do we already have a pronunciation file for this phrase?
            if os.path.exists(wavfile.replace(".wav", ".pronunciation.txt")):
                # good, this is the right way.
                with open(wavfile.replace(".wav", ".pronunciation.txt"), "r") as f:
                    new_pronunciation = f.read().strip()
            else:
                # look in the comfy output directory
                try:
                    # /root/ComfyUI/output/audio/ph_0_Fables_6020c8ae_0a40.pronunciation.txt
                    pronunciation_filename = os.path.join(
                        const.COMFY_DIRS["artifactserver"]['OUTPUT_DIR'],
                        "audio",
                        os.path.basename(wavfile).replace(".wav", ".pronunciation.txt"),
                    )

                    with open(pronunciation_filename, 'r') as f:
                        # wavfile.replace(".wav", ".pronunciation") + ".txt", "r") as f:
                        new_pronunciation = f.read().strip()
                        log.info(f"Found pronunciation file: {pronunciation_filename}")
                        log.info(f"New pronunciation: {new_pronunciation}")

                except FileNotFoundError:
                    new_pronunciation = None

            # in-place pad audio with silence, just enough to be an exact fps mulitple
            log.info("[generate_all_audio] Padding audio file to frame")
            wav_pad_to_frame(wavfile)

            phrase_xml.attrs["src"] = os.path.basename(wavfile)
            duration = get_wav_duration(wavfile)

            log.info("Duration measured as: %s seconds", duration)
            phrase_xml.attrs["duration"] = str(duration)
            phrase_xml.attrs["frames"] = int(duration * const.FPS)

            if new_pronunciation:
                phrase_xml.attrs["pronunciation"] = new_pronunciation

            chapter.save_xml()

    generate_audio(chapter)
    recalculate_image_frames(chapter)
    chapter.save_xml()


def save_speaker(chapter, phrase_index: int, speaker):
    phrase_xml = chapter.get_phrase(phrase_index)
    phrase_xml.attrs["speaker"] = name_to_tag(speaker)
    chapter.save_xml()
    return True


def get_first_file(file_list):
    """
    return the first file in file_list that actually exists.
    """
    for fn in file_list:
        if os.path.exists(fn):
            return fn

    return None


def assemble(audio_tracks, outfile):
    """
    Combine multiple audio tracks into a single audio track.
    We are not otherwise modifying them in any way.
    """
    # open the first audio track to get the parameters; they must be consistent
    # across the wav files.
    log.info(f"Retrieving audio parameters from {audio_tracks[0]}...")

    first_file = get_first_file(audio_tracks)
    if first_file is None:
        log.error("No audio files exist, cannot assemble them.")
        return

    with contextlib.closing(wave.open(first_file, "rb")) as input:
        params = input.getparams()

    nchannels, sampwidth, framerate, nframes, comptype, compname = params

    # open the outfile wave file for writing
    with contextlib.closing(wave.open(outfile, "wb")) as out:
        # use the parameters from first audio track
        with contextlib.closing(wave.open(first_file, "rb")) as input:
            out.setparams(
                (nchannels, sampwidth, framerate, nframes, comptype, compname)
            )

        # write the frames from each audio track to the outfile
        for track in audio_tracks:
            if not os.path.exists(track):
                log.warning("Skipping audio file %s that does not exist", track)
                continue

            with contextlib.closing(wave.open(track, "rb")) as input:
                all_frames = input.readframes(input.getnframes())
                out.writeframes(all_frames)
