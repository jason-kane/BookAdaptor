import base64
import hashlib
import os
import random
import string
import eng_to_ipa
from artifact_editor.author.author import Author

from bs4 import BeautifulSoup
from flask import (
    Blueprint,
    render_template,
    make_response,
    request,
    send_from_directory,
    url_for,
)

import const
import logger
from artifact_editor import (
    config,
    tools,
)
from artifact_editor.audio import audio, utterances
from artifact_editor.audio import htmx as audio_htmx
from artifact_editor.audio.pronunciation import pronunciation
from artifact_editor.chapter.chapter import Chapter
from . import (
    htmx,
)

log = logger.log(__name__)


bp = Blueprint(
    "pronunciation",
    __name__,
    template_folder=os.path.join(os.path.dirname(__file__), "templates"),
)


@bp.route("/", methods=["GET"])
def base(author, title, chapter_number, language):
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)
    log.info(f"base_pronunciation_guide({author}, {title}, {chapter.number})")

    return render_template(
        "pronunciation_guide.html",
        language=language,
        pretty_language=language.title(),
        author=author,
        pretty_author=chapter.config.get("author", author.pretty_name),
        title=title,
        pretty_title=chapter.config.get("title", title),
        chapter=chapter,
        chapterurl=chapter.url,
        find_unpronouncable_words_button=htmx.find_unpronouncable_words_button(chapter),
        try_cmu_dict_button=htmx.try_cmu_dict_button(chapter),
        global_pronunciation_list=pronunciation.global_pronunciation_list(chapter),
        section="phrase.pronunciation",
        section_cosmetic="Phrase › Pronunciation Guide",
        add_global_pronunciation_url=url_for(
            'library.book.chapter.audio.pronunciation.add_global_pronunciation',
            **chapter.kwargs,
        ),
    )


# /Mark%20Twain/A%20Connecticut%20Yankee%20in%20King%20Arthurs%20Court/0001/audio/actions/edit_global_pronunciation_form?key=guenever
@bp.route("edit", methods=["GET"])
def edit(author, title, chapter_number, language):
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)

    key = request.args.get("key", "").strip()

    return htmx.edit_row(chapter, key=key)


# POST /Mark%20Twain/A%20Connecticut%20Yankee%20in%20King%20Arthurs%20Court/0001/audio/actions/find_unpronouncable_words
@bp.route("actions/find_unpronouncable_words", methods=["POST"])
def find_unpronouncable_words(author, title, chapter_number, language):
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)

    unpronouncable_words = audio.find_unpronouncable_words(chapter)
    log.info("Found new unpronouncable words: %s", unpronouncable_words)

    if unpronouncable_words:
        pass

    return htmx.global_pronunciation_table(chapter)


@bp.route("<phrase_index>", methods=["PUT"])
def save(author, title, chapter_number, language, phrase_index):
    log.info("request.form: %s", request.form)
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)
    pronunciation = request.form.get("pronunciation", None)

    phrase_xml = chapter.get_phrase(phrase_index)
    phrase_xml.attrs["pronunciation"] = pronunciation
    chapter.save_xml()

    return audio_htmx.phrase_editor(
        chapter,
        phrase_xml,
        page=0,
        split=(phrase_xml.get_text().strip(), ""),
    ), 200


# PUT /Malory/LeMorteDArthur_V01_B01/0001/audio/0_1/pronunciation_filter
@bp.route("<phrase_index>/pronunciation_filter", methods=["PUT"])
def save_pronunciation_filter(author, title, chapter_number, language, phrase_index):
    log.info("request.form: %s", request.form)
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)
    
    page = request.args.get("page")
    if page is None:
        page = request.form.get("page")

    if page is None:
        log.error("** No page provided in request args or form data. **")
        page = 1

    pronunciation_filter = request.form.get("pronunciation_filter", None)
    phrase_xml = chapter.get_phrase(phrase_index)

    phrase_xml.attrs["pronunciation_filter"] = pronunciation_filter
    chapter.save_xml()

    return audio_htmx.phrase_editor(chapter, phrase_xml, page)


@bp.route("actions/add_global_pronunciation", methods=["POST"])
def add_global_pronunciation(author, title, chapter_number, language):
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)

    word = request.form.get("add_word", "").strip()
    pronun = request.form.get("add_pron", "").strip()

    if not word:
        log.error("Word must be provided.")
        log.info(request.form)
        return htmx.global_pronunciation_table(chapter)
    else:
        log.info('Saving global pronunciation: "%s" => "%s"', word, pronun)

    pronunciation_dict = pronunciation.get_global_pronunciations(chapter)
    key = pronunciation.word_to_key(word)
    pronunciation_dict[key] = {"word": word, "pronunciation": pronun, "after": ""}
    pronunciation.save_global_pronunciations(chapter, pronunciation_dict)

    return htmx.global_pronunciation_table(chapter)


@bp.route("set_pronunciation", methods=["POST"])
def set_pronunciation(author, title, chapter_number, language):
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)

    key = request.form.get("key", "").strip()
    pronun = request.form.get("pronunciation", "").strip()

    log.info(f"{request.args=}")
    log.info(f"{request.form=}")

    if not key or not pronun:
        log.error("Key and pronunciation must be provided.")
        log.info(request.form)
        return htmx.global_pronunciation_table(chapter)

    pronunciation_dict = pronunciation.get_global_pronunciations(chapter)

    pronunciation_dict[key].update({"pronunciation": pronun})
    pronunciation.save_global_pronunciations(chapter, pronunciation_dict)

    return htmx.edit_row(chapter, key=key)


@bp.route("<key>", methods=["DELETE"])
def delete_global_pronunciation(author, title, chapter_number, language, key):
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)

    if not key:
        log.error("Key must be provided.")
        log.info(request.form)
        return htmx.global_pronunciation_table(chapter)

    pronunciation_dict = pronunciation.get_global_pronunciations(chapter)
    log.warning('Deleting global pronunciation for key: %s', key)
    del pronunciation_dict[key]
    pronunciation.save_global_pronunciations(chapter, pronunciation_dict)

    return htmx.global_pronunciation_table(chapter)


# kæsəlmeɪn
# POST /Mark%20Twain/A%20Connecticut%20Yankee%20in%20King%20Arthurs%20Court/0001/audio/actions/edit_global_pronunciation HTTP/1.1[0m"
# @bp.route("/<author>/<path:title>/<chapter>/audio/actions/edit_global_pronunciation", methods=["POST"])
# def edit_global_pronunciation(author, title, chapter):
#     chapterdir = tools.get_chapterdir(author, title, chapter)
#     chapterurl = tools.get_chapterurl(author, title, chapter)
#     bookdir = tools.get_bookdir(author, title)

#     word = request.form.get("word", "").strip()
#     pronun = request.form.get(f"pronounce_{word}", "").strip()

#     if not word or not pronun:
#         log.error("Word and pronunciation must be provided.")
#         log.info(request.form)
#         return htmx.global_pronunciation_table(chapterurl, chapterdir)

#     pronunciation_dict = pronunciation.get_global_pronunciations(bookdir)
#     pronunciation_dict[word] = pronun
#     pronunciation.save_global_pronunciations(bookdir, pronunciation_dict)

#     return htmx.global_pronunciation_table(chapterurl, chapterdir)


@bp.route("actions/repronounce_phrase/<phrase_index>", methods=["POST"])
def repronounce_phrase(author, title, chapter_number, language, phrase_index, force=True):
    """
    """
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)

    page = request.form.get("page", 0)
    phrase_xml = chapter.get_phrase(phrase_index)

    if phrase_xml is None:
        log.error("Invalid phrase_index: %s", phrase_index)
        return

    pronunciation = audio.repronounce_phrase(chapter, phrase_xml)
    
    phrase_xml = chapter.get_phrase(phrase_index)
    phrase_xml.attrs["pronunciation"] = pronunciation
    chapter.save_xml()

    log.info(f"Repronounced phrase {phrase_index} with new pronunciation: {phrase_xml.attrs.get('pronunciation', '')}")
    phrase_xml = chapter.get_phrase(phrase_index)
    log.info(f"Same, right? {phrase_xml.attrs.get('pronunciation', '')}")

    return audio_htmx.pronunciation_textarea(chapter, phrase_xml), 200


# /Mark%20Twain/A%20Connecticut%20Yankee%20in%20King%20Arthurs%20Court/0001/audio/pronunciation/bedivere.wav
@bp.route("<key>.wav", methods=["GET"])
def get_pronunciation_audio(author, title, chapter_number, language, key):
    """
    The request for the audio generates it on demand
    """
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)

    pronunciation_dict = pronunciation.get_global_pronunciations(chapter)
    if key not in pronunciation_dict:
        log.error(f"Key '{key}' not found in global pronunciations.")
        return "", 404

    word = pronunciation_dict[key]["word"]
    pronun = pronunciation_dict[key]["pronunciation"]

    pronunciation_dir = os.path.join(
        const.LIBRARY_DIR,
        chapter.bookdir.lstrip("/"),
        "pronunciation",
    )

    os.makedirs(pronunciation_dir, exist_ok=True)

    wavfile = os.path.join(pronunciation_dir, f"{key}.wav")

    if os.path.exists(wavfile):
        os.unlink(wavfile)

    if True:
        log.info(f"Generating pronunciation audio for '{word}' as '{pronun}'")
        # so sloppy audio.speak(), you are embarassing.
        phrase_xml = BeautifulSoup(
            f"""<phrase 
                id=\"0_0\" 
                pronunciation=\"{pronun}\"
                speaker=\"Narrator\"
            >{word}</phrase>""",
            "xml",
        )

        _, pronunciation_str = audio.speak(
            chapter=chapter,
            phrase_xml=phrase_xml,
            wavfile=wavfile,
            workdir=os.path.join(
                pronunciation_dir,
                os.path.dirname(wavfile).lstrip(
                    "/"
                ),  # because we may not have paragraph_dir available.
            ),
            delay=0,
        )
    else:
        log.info(f"Serving existing pronunciation audio for '{word}'")

    # return tools.send_file_secure(wavfile, mimetype="audio/wav")
    log.info(f"Serving {pronunciation_dir} / {wavfile}")
    return send_from_directory(
        pronunciation_dir,
        os.path.basename(wavfile),
        as_attachment=False,
        mimetype="audio/wav",
    )


# POST /Bible/Old%20Testament/0001/audio/pronunciation/soundslike
@bp.route("soundslike", methods=["POST"])
def soundslike(author, title, chapter_number, language):
    # author = Author(author)
    # chapter = Chapter(author, title, chapter_number, language)

    soundslike = request.form.get("soundslike", "").strip()
    key = request.form.get("key", "").strip()

    p = eng_to_ipa.jonvert(soundslike)
    if not "*" in p:
        log.info(f"Eng-to-IPA suggests {p} for word: {soundslike}")

        # the response from jonvert isn't exactly what Kokoro expects, so we
        # need to massage it a bit.  For example, jonvert returns an ascii "g"
        # for the voiced velar plosive, but Kokoro expects "ɡ".
        p = p.replace('g', 'ɡ')  # replace g with the proper IPA character

        response = make_response(
            f"""<wa-textarea
                id="soundslike_{key}" 
                placeholder="International Phonetic Alphabet (IPA)"
                value="{p}">
            </wa-textarea>"""
        )
        response.headers["Content-Type"] = "text/html"
        log.info("Returning response", response_data=response.data)
        return response, 200

    log.info("Eng-to-IPA failed for word", word=soundslike)
    return "", 204
