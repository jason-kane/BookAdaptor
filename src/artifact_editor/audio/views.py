import glob
import html
import os
import shutil
from pyexpat import model

import numpy as np
import speech_recognition as sr
import torch
from bs4 import BeautifulSoup
from bs4.element import NavigableString
from flask import Blueprint, make_response, render_template, request, send_file
from speech_recognition.audio import AudioData
from transformers import (
    WhisperFeatureExtractor,
    WhisperForConditionalGeneration,
    WhisperProcessor,
)

import const
import logger
from artifact_editor import (
    config,
    llm,
    tools,
)
from artifact_editor.audio.pronunciation import pronunciation
from artifact_editor.audio.pronunciation.views import bp as pronunciation_bp
from artifact_editor.audio.sound.views import bp as sound_bp
from artifact_editor.audio.timeline.views import bp as timeline_bp
from artifact_editor.author.author import Author
from artifact_editor.chapter.chapter import Chapter

from . import (
    audio,
    htmx,
)

log = logger.log(__name__)

bp = Blueprint(
    "audio",
    __name__,
    template_folder=os.path.join(os.path.dirname(__file__), "templates"),
)

bp.register_blueprint(sound_bp, url_prefix="/sound")
bp.register_blueprint(pronunciation_bp, url_prefix="/pronunciation")
bp.register_blueprint(timeline_bp, url_prefix="/timeline")

# @bp gives us "/<author>/<path:title>/<chapter>/audio")
# so all of these are (author, title, chapter) at least.


# POST /Bible/Old%20Testament/0001/audio/actions/try_cmu_dict
@bp.route("/actions/try_cmu_dict", methods=["POST"])
def audio_try_cmu_dict(author, title, chapter):
    bookdir = tools.get_bookdir(author, title)
    audio.try_cmu_dict(bookdir)
    return "", 204


# /Bible/Old%20Testament/0001/audio/actions/add_image_slot/4_0
@bp.route("/actions/add_image_slot/<phrase_index>", methods=["POST"])
def add_image_slot(author, title, chapter_number, language, phrase_index):
    log.info(f"Adding image slot to phrase {phrase_index}")
    # add an image slot prior to the specified phrase
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)

    soup = chapter.get_xml()

    phrase_xml = soup.find("phrase", index=phrase_index)
    paragraph_xml = phrase_xml.find_parent("paragraph")

    # create a new image slot
    new_image_xml = soup.new_tag("image")
    phrase_xml.insert_after(new_image_xml)

    # re-index _all_ images
    image_index = 0
    for image_xml in soup.findAll("image"):
        image_xml.attrs["index"] = str(image_index)
        image_index += 1

    chapter.save_xml()

    return audio.phrase_sequence(chapter, page=None)


# /Grimm/Fairy%20Tales/1/english/audio/actions/add_delay/3
@bp.route("/actions/add_delay/<phrase_id>", methods=["POST"])
def add_delay(author, title, chapter_number, language, phrase_id):
    """
    Add a widget to control delay (audio silence) at the end of a phrase.
    default is zero -- there is some baked in delay in the TTS system.
    """
    log.info(f"Adding delay control to phrase {phrase_id}")
    # add an image slot prior to the specified phrase
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)

    phrase_xml = chapter.get_xml().find("phrase", index=phrase_id)
    if phrase_xml is None:
        log.error(f"Phrase with id {phrase_id} not found in book {chapter}")
        return "Phrase not found", 404

    phrase_xml.attrs["delay"] = "0"

    chapter.save_xml()

    return audio_phrases(
        chapter.author.name, chapter.title, chapter.number, chapter.language
    )


@bp.route("/actions/add_sound/<phrase_index>", methods=["POST"])
def add_sound(author, title, chapter_number, language, phrase_index):
    """
    Add an empty sound slot after the indicated phrase.
    """
    log.info(f"Adding sound slot to phrase {phrase_index}")
    # add an image slot prior to the specified phrase
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)

    phrase_xml = chapter.get_phrase(phrase_index)
    if phrase_xml is None:
        log.error(f"Phrase with id {phrase_index} not found in book {chapter}")
        return "Phrase not found", 404

    new_sound_xml = chapter.soup.new_tag("sound")
    new_sound_xml.attrs.update(
        {"name": "", "start_offset": 0, "end_offset": None, "blocking": True}
    )
    phrase_xml.insert_after(new_sound_xml)

    chapter.save_xml()

    return audio_phrases(
        chapter.author.name, chapter.title, chapter.number, chapter.language
    )


# http://localhost:5000/L.%20Frank%20Baum/The%20Marvelous%20Land%20of%20Oz/0001/audio/actions/remove_delay/0_1?phrase_id=0_1&speaker=Narrator&pronunciation=%C3%B0%C9%99%20m%CB%88%C9%91%CB%90%C9%B9v%C9%99l%C9%99s%20l%CB%88%C3%A6nd%20%CA%8Cv%20%CB%88%C9%91%CB%90z&delay=0&page=1
@bp.route("/actions/remove_delay/<phrase_index>", methods=["DELETE"])
def remove_delay(author, title, chapter_number, language, phrase_index):
    phrase_index = int(phrase_index)

    log.info(f"Removing delay control from phrase {phrase_index}")
    author = Author(author)
    chapter = Chapter(
        author=author, title=title, number=chapter_number, language=language
    )

    phrase_xml = chapter.get_phrase(phrase_index)
    if phrase_xml is None:
        log.error(f"Phrase with index {phrase_index} not found")
        return "Phrase not found", 404

    if "delay" in phrase_xml.attrs:
        del phrase_xml.attrs["delay"]

    chapter.save_xml()

    return audio_phrases(author, title, chapter_number, language)


# http://localhost:5000/L.%20Frank%20Baum/The%20Marvelous%20Land%20of%20Oz/0001/audio/actions/set_delay/0_1
@bp.route("/actions/set_delay/<phrase_index>", methods=["POST"])
def set_delay(author, title, chapter_number, language, phrase_index):
    phrase_index = int(phrase_index)
    author = Author(author)
    chapter = Chapter(
        author=author, title=title, number=chapter_number, language=language
    )

    delay = float(request.form.get("delay", "0"))
    log.info(f"Setting delay to {delay} seconds for phrase {phrase_index}")

    phrase_xml = chapter.get_phrase(phrase_index)
    if phrase_xml is None:
        log.error(f"Phrase with index {phrase_index} not found")
        return "Phrase not found", 404

    phrase_xml.attrs["delay"] = str(delay)

    chapter.save_xml()

    return audio_phrase(author.name, title, chapter_number, language, phrase_index)


def audio_phrase(author, title, chapter_number, language, phrase_index):
    """
    Return a fully populated single phrase editor widget.
    """
    author = Author(author)
    chapter = Chapter(
        author=author, title=title, number=chapter_number, language=language
    )

    phrase_xml = chapter.get_phrase(phrase_index)

    if phrase_xml is None:
        return "Phrase not found", 404

    page = request.form.get("page", None)
    return htmx.phrase_editor(chapter, phrase_xml, page), 200


@bp.route("/phrases", methods=["GET"])
def audio_phrases(author, title, chapter_number, language, page=None):
    if page is None:
        page = request.args.get("page", None)
    author = Author(author)
    chapter = Chapter(
        author=author, title=title, number=chapter_number, language=language
    )

    response = make_response(audio.phrase_sequence(chapter, page=page))
    response.headers["HX-Trigger-After-Swap"] = "latex_editor_init"
    return response


# POST /L.%20Frank%20Baum/The%20Marvelous%20Land%20of%20Oz/0001/paragraph/set_fullscreen
@bp.route("/paragraph/set_fullscreen", methods=["POST"])
def set_paragraph_fullscreen(author, title, chapter_number, language):
    paragraph_index = int(request.form.get("paragraph_index", -1))
    fullscreen = request.form.get("fullscreen", "false").lower() == "true"
    author = Author(author)
    chapter = Chapter(
        author=author, title=title, number=chapter_number, language=language
    )

    all_paragraphs = chapter.get_xml().findAll("paragraph")
    paragraph = all_paragraphs[paragraph_index]

    paragraph_tags = tools.tags_to_dict(paragraph.attrs.get("tags", ""))

    if fullscreen:
        paragraph.attrs["fullscreen"] = "true"
        paragraph_tags["has-text"] = "false"
        paragraph_tags["spoken-only"] = "true"
        paragraph.attrs["tags"] = tools.dict_to_tags(paragraph_tags)
    else:
        paragraph.attrs["fullscreen"] = "false"
        paragraph_tags["has-text"] = "true"
        paragraph_tags["spoken-only"] = "false"
        paragraph.attrs["tags"] = tools.dict_to_tags(paragraph_tags)

    chapter.save_xml()

    return audio.phrase_sequence(chapter, page=paragraph_index + 1)


@bp.route("/text_snippet/<phrase_index>.png", methods=["GET"])
def text_snippet(author, title, chapter_number, language, phrase_index):
    author = Author(author)
    chapter = Chapter(
        author=author, title=title, number=chapter_number, language=language
    )
    phrase_xml = chapter.get_phrase(phrase_index)

    fn = chapter.get_highlighted_text_snippet_fn(phrase_xml)

    return send_file(fn, mimetype="image/png")


@bp.route("/", methods=["GET"])
def base(author, title, chapter_number, language):
    author = Author(author)
    chapter = Chapter(
        author=author, title=title, number=chapter_number, language=language
    )
    # get the "page" http parameter
    log.info(f"base_audio({author}, {title}, {chapter.number})")

    try:
        page = int(request.args.get("page", None))
    except (TypeError, ValueError):
        page = 1

    pretty_author = chapter.config.get("author", author.pretty_name)
    pretty_title = chapter.config.get("title", title)

    return render_template(
        "audio.html",
        language="english",
        pretty_language="English",
        author=author,
        pretty_author=pretty_author,
        title=title,
        pretty_title=pretty_title,
        chapter=chapter,
        chapterurl=chapter.url,
        phrase_sequence=audio.phrase_sequence(chapter, page=page),
        regenerate_all_audio_button=htmx.regenerate_all_audio_button(chapter),
        generate_all_missing_audio_button=htmx.generate_all_missing_audio_button(
            chapter
        ),
        repronounce_where_missing_button=htmx.repronounce_where_missing_button(chapter),
        reset_audio_cache_json_button=htmx.reset_audio_cache_json_button(chapter),
        # set_phrase_speaker_button=htmx_set_phrase_speaker_button(chapterurl),
        recalculate_all_phrase_frames_button=htmx.recalculate_all_phrase_frames_button(
            chapter
        ),
        # htmx_single_clip_card=htmx_single_clip_card,
        section="phrase.workshop",
        section_cosmetic="Phrases › Workshop",
    )


@bp.route("/actions/recalculate_all_phrase_frames", methods=["POST"])
def recalculate_all_phrase_frames(author, title, chapter_number, language):
    author = Author(author)
    chapter = Chapter(
        author=author, title=title, number=chapter_number, language=language
    )

    for phrase_xml in chapter.get_xml().find_all("phrase"):
        wavfile = audio.get_wavfile_filename(phrase_xml, chapter)
        if wavfile and os.path.exists(wavfile):
            duration = audio.get_wav_duration(wavfile)
            phrase_xml.attrs["duration"] = str(duration)
            phrase_xml.attrs["frames"] = int(duration * const.FPS)
        else:
            log.warning(
                f"Wav file {wavfile} not found for phrase {phrase_xml['index']}"
            )
    chapter.save_xml()

    audio.recalculate_image_frames(chapter)

    return htmx.recalculate_all_phrase_frames_button(chapter)


@bp.route("/image_slot/<image_index>", methods=["DELETE"])
def delete_image_slot(author, title, chapter_number, language, image_index):
    image_index = int(image_index)
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)

    # Remove the image slot from the audio data
    image_xml = chapter.get_image(image_index)

    if image_xml:
        # self destruct the image slot
        image_xml.decompose()

        # Save the book after removing the image slot
        chapter.save_xml()
        chapter.load_xml(force=True)
        return "", 204
    else:
        log.error(f"Image slot at index {image_index} not found.")
        return "", 404


@bp.route("/actions/set_speaker/<phrase_index>", methods=["POST"])
def assign_phrase_speaker(author, title, chapter_number, language, phrase_index):
    phrase_index = int(phrase_index)

    author = Author(author)
    chapter = Chapter(
        author=author, title=title, number=chapter_number, language=language
    )

    speaker = request.form.get("speaker", None)

    audio.save_speaker(chapter, phrase_index, speaker)
    return htmx.speaker_selector(
        chapter,
        chapter.get_phrase(phrase_index),
    )


# http://localhost:5000/Ambrose%20Bierce/An%20Occurrence%20at%20Owl%20Creek%20Bridge/0001/audio/actions/set_second_phrase/3_18
@bp.route("/actions/set_split/<phrase_index>", methods=["POST"])
def set_split(author, title, chapter_number, language, phrase_index):
    """
    Given form values first_phrase and second_phrase, return a nice UI for
    deciding exactly where to split the phrase using the provided split as the
    current division.
    """
    author = Author(author)
    chapter = Chapter(
        author=author, title=title, number=chapter_number, language=language
    )
    phrase_index = int(phrase_index)

    phrase_xml = chapter.get_phrase(phrase_index)

    if phrase_xml is None:
        return "Phrase not found", 404

    whole_source_phrase = phrase_xml.get_text().strip()
    first_phrase = request.form.get("first_phrase", "").strip()
    second_phrase = request.form.get("second_phrase", "").strip()
    # what is the index of the first character that is different between
    # phrase_text and request.form['first_phrase']?
    first_index = next(
        (
            i
            for i, (char1, char2) in enumerate(zip(whole_source_phrase, first_phrase))
            if char1 != char2
        ),
        None,
    )
    second_index = next(
        (
            i
            for i, (char1, char2) in enumerate(zip(whole_source_phrase, second_phrase))
            if char1 != char2
        ),
        None,
    )

    if first_index is not None and second_index is None:
        index = first_index
    elif second_index is not None and first_index is None:
        index = second_index
    else:
        index = len(whole_source_phrase)

    return htmx.split_phrase_text(
        chapter,
        phrase_xml,
        split=(whole_source_phrase[:index], whole_source_phrase[index:]),
    ), 200


# set_linebreak
@bp.route("/actions/set_linebreak/<phrase_index>", methods=["PUT"])
def set_linebreak(author, title, chapter_number, language, phrase_index):
    phrase_index = int(phrase_index)
    author = Author(author)
    chapter = Chapter(
        author=author, title=title, number=chapter_number, language=language
    )

    phrase_xml = chapter.get_phrase(phrase_index)
    if phrase_xml is None:
        return "Phrase not found", 404

    linebreak = request.form.get("no_linebreak", "false")
    phrase_xml.attrs["no_linebreak"] = linebreak
    log.info("Saving no_linebreak=%s for phrase %s", linebreak, phrase_xml["index"])
    chapter.save_xml()

    page = request.form.get("page", None)
    return htmx.phrase_editor(chapter, phrase_xml, page), 200


# hx-post="/{author}/{title}/{chapter}/audio/save_phrase/{phrase['id']}"
@bp.route("/actions/save_split_phrase/<phrase_index>", methods=["POST"])
def save_split_phrase(author, title, chapter_number, language, phrase_index):
    """
    Commit the currently proposed split to the chapter
    XML.
    """
    author = Author(author)
    chapter = Chapter(
        author=author, title=title, number=chapter_number, language=language
    )
    phrase_index = int(phrase_index)

    phrase_xml = chapter.get_phrase(phrase_index)

    if phrase_xml is None:
        return "Phrase not found", 404

    log.info("request.form: %s", request.form)
    first_phrase = request.form.get("first_phrase", None)
    second_phrase = request.form.get("second_phrase", None)

    if first_phrase is None or second_phrase is None:
        return "Missing first or second phrase", 400

    # brass tacks.  we are going to split this phrase into two phrases.
    # first create a new second phrase
    new_phrase_xml = BeautifulSoup("", "xml").new_tag("phrase")

    phrase_xml.string = first_phrase.strip()
    new_phrase_xml.string = second_phrase.strip()
    # <phrase duration="3.2325" fragdex="1" frames="81" id="35_1" pronunciation="“ðə fˈɜɹst mˈæn hæd hɪz θɹˈi wˈɪʃᵻz. jˈɛs,”" speaker="The_Sergeant-Major" src="ph_1__The_first_man_had_his_th_a7786732_8656.wav">
    #   “The first man had his three wishes. Yes,”
    # </phrase>

    # fragdex
    # id
    # speaker, easy
    new_phrase_xml.attrs["speaker"] = phrase_xml.attrs.get("speaker", "Narrator")

    paragraph = phrase_xml.find_parent("paragraph")

    phrase_xml.insert_after(new_phrase_xml)

    fragdex = 0
    for fragment in paragraph.children:
        if not hasattr(fragment, "attrs"):
            continue
        fragment.attrs["fragdex"] = str(fragdex)

        if fragment.name == "phrase":
            fragment.attrs["id"] = f"{paragraph.attrs['index']}_{fragdex}"

        fragdex += 1

    chapter.save_xml()

    # hx-get="phrases?page={page}"
    # return htmx_phrase_editor(chapterurl, chapterdir, phrase_xml), 200
    page = request.form.get("page", None)
    return audio.phrase_sequence(chapter, page=page)


# /Ambrose%20Bierce/An%20Occurrence%20at%20Owl%20Creek%20Bridge/chapter/0001/audio/actions/split_phrase
@bp.route("/actions/split_phrase", methods=["POST"])
def split_phrase(author, title, chapter_number, language):
    """
    Initiate the phrase split UI.
    """
    author = Author(author)
    chapter = Chapter(author=author, title=title, number=chapter_number)

    phrase_index = int(request.form["phrase_index"])
    page = request.form.get("page", None)

    phrase_xml = chapter.get_phrase(phrase_index)

    if phrase_xml is None:
        log.error(f"Phrase with id {phrase_index} not found in book {chapter}")
        return "Phrase not found", 404

    # paragraph = phrase_xml.find_parent("paragraph")

    return htmx.phrase_editor(
        chapter, phrase_xml, page, split=(phrase_xml.get_text().strip(), "")
    ), 200


@bp.route("/actions/regenerate_phrase/<phrase_index>", methods=["POST"])
def regenerate_phrase(
    author, title, chapter_number, language, phrase_index, force=True
):
    """
    TODO: this should redraw the whole clip frame, not just the
    stupid button.
    """
    author = Author(author)
    chapter = Chapter(
        author=author, title=title, number=chapter_number, language=language
    )
    phrase_index = int(phrase_index)

    page = request.form.get("page", None)
    chapter.load_xml()

    phrase_xml = chapter.get_phrase(phrase_index)
    log.warning("Alpha", phrase_xml=phrase_xml, has_soup=chapter.soup is not None)
    if phrase_xml is None:
        return "Phrase not found", 404

    # adds 'src' if it doesn't exist, with a plausible filename.
    wavfile = audio.get_wavfile_filename(phrase_xml, chapter)

    log.warning("Beta", phrase_xml=phrase_xml, has_soup=chapter.soup is not None)
    
    # should do NOTHING.
    phrase_xml = chapter.get_phrase(phrase_index)
    log.warning("Gamma", phrase_xml=phrase_xml, has_soup=chapter.soup is not None)

    if wavfile is None:
        log.error(f"Phrase with index {phrase_index} not found in book {chapter}")
        return "Phrase not found", 404

    if os.path.exists(wavfile):
        if force:
            # _regenerate_ phrase
            log.info(f"Deleted existing wav file: {wavfile}")
            os.remove(wavfile)            
        else:
            log.info(f"Phrase {phrase_index} already has an audio file: {wavfile}")
            return htmx.phrase_editor(chapter, phrase_xml, page), 200

    # regenerate the wav for this phrase
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
    log.info("[regenerate_phrase] Padding audio file to frame: %s", wavfile)
    audio.wav_pad_to_frame(wavfile)

    phrase_xml.attrs["src"] = os.path.basename(wavfile)
    duration = audio.get_wav_duration(wavfile)

    log.info("Duration measured as: %s seconds", duration)
    phrase_xml.attrs["duration"] = str(duration)
    phrase_xml.attrs["frames"] = int(duration * const.FPS)

    if new_pronunciation and not phrase_xml.attrs.get("pronunciation", False):
        log.info(
            f"Updating pronunciation for phrase {phrase_index} to {new_pronunciation}"
        )
        phrase_xml.attrs["pronunciation"] = new_pronunciation

    elif not new_pronunciation:
        log.warning(f"No pronunciation found for phrase {phrase_index} at {wavfile}")

    test_phrase = chapter.get_phrase(phrase_index)
    log.warning(f"Test phrase after regeneration: {test_phrase}")
    
    log.info(f"Before: {phrase_xml.attrs=}")
    chapter.save_xml()
    
    log.info(f"After 1: {phrase_xml.attrs=}")
    phrase_xml = chapter.get_phrase(phrase_index)
    log.info(f"After 2: {phrase_xml.attrs=}")

    audio.recalculate_image_frames(chapter)

    chapter.save_xml()
    return htmx.phrase_editor(chapter, phrase_xml, page), 200


# POST /Bible/Old%20Testament/0001/audio/actions/repronounce_missing_words
@bp.route("/actions/repronounce_missing_words", methods=["POST"])
def repronounce_missing_words(author, title, chapter):
    chapterdir = tools.get_chapterdir(author, title, chapter)
    chapterurl = tools.get_chapterurl(author, title, chapter)

    audio.repronounce_where_missing(chapterdir)

    return htmx.repronounce_where_missing_button(chapterurl)


def recognize_neurolang_ipa_whisper_medium(
    self, audio_data: AudioData, show_dict: bool = False, **kwargs
):
    processor = WhisperProcessor.from_pretrained("neurlang/ipa-whisper-medium")
    model = WhisperForConditionalGeneration.from_pretrained(
        "neurlang/ipa-whisper-medium"
    )
    fe = WhisperFeatureExtractor.from_pretrained("neurlang/ipa-whisper-medium")

    model.config.forced_decoder_ids = None
    model.config.suppress_tokens = []
    model.generation_config.forced_decoder_ids = None
    model.generation_config._from_model_config = True

    # turn AudioData into torch.FloatTensor with shape (batch_size, sequence_length, feature_dim)?
    audio = audio_data.get_raw_data(
        convert_width=2, convert_rate=16000
    )  # PCM 16kHz raw audio data

    # truncate to 30 seconds
    max_length = 30 * 16000 * 2  # 30 seconds * 16000 samples/second * 2 bytes/sample
    if len(audio) > max_length:
        audio = audio[:max_length]
        log.info(f"Audio truncated to 30 seconds (max_length={max_length} bytes)")

    as_tensor = np.frombuffer(audio, np.int16).flatten().astype(np.float32) / 32768.0

    # log.info(f"Audio data length: {len(audio)} bytes")
    log.info(f"Audio duration: {len(audio) / 16000} seconds")
    input_features = fe(
        raw_speech=torch.from_numpy(as_tensor), sampling_rate=16000, return_tensors="pt"
    )
    log.info(f"{input_features.input_features=}")

    predicted_ids = model.generate(
        input_features=input_features.input_features,
        # torch.from_numpy(as_tensor)
    )
    log.info("predicted_ids: %s", predicted_ids)
    transcription = processor.batch_decode(predicted_ids, skip_special_tokens=True)
    log.info(f"Transcription: {transcription}")
    return transcription


@bp.route("/actions/record_audio/<phrase_index>", methods=["POST"])
def record_audio(author, title, chapter_number, language, phrase_index):
    """
    ** THIS IS GOING TO LISTEN ON YOUR MICROPHONE. **
    """
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)

    # this is localhost, we're going to record audio of the user pronouncing one
    # word. we are going to pass that through a wav -> ipa llm engine and return
    # that it where the user can use it to use (or parts of it) as the correct
    # pronunciation for the word.
    phrase_xml = chapter.get_phrase(phrase_index)
    if phrase_xml is None:
        return "Phrase not found", 404

    word = request.form["word"]
    sr.Recognizer.recognize_neurolang_ipa_whisper_medium = (
        recognize_neurolang_ipa_whisper_medium
    )
    r = sr.Recognizer()
    pron = None
    try:
        with sr.Microphone() as source:
            log.info(f"{source=}")
            log.info(f"{dir(source)=}")
            log.info(f"{source.list_working_microphones()=}")
            r.adjust_for_ambient_noise(source)
            log.info("Listening for audio input...")
            audio = r.listen(source, stream=False)
            log.info("Done Listening for audio input...")
    except Exception as e:
        log.error(f"Error recording audio: {e}")
        return "Error recording audio", 500

    # transcribe to IPA
    pron = r.recognize_neurolang_ipa_whisper_medium(audio)
    log.info(f"Transcribed pronunciation: {pron}")

    pron = pron[0] if isinstance(pron, list) and pron else pron

    # ok, so in theory... pron is the IPA pronunciation of "word" according to the user.
    pron = html.escape(pron) if pron else ""

    log.info("Returning pronunciation: %s", pron)

    return (
        f"""<wa-input 
        id="ipa_37_0"
        type="text"
        size="m"
        value="{pron}"
        appearance="outlined"></wa-input>""",
        200,
    )

    # # write audio to a WAV file
    # with open("microphone-results.wav", "wb") as f:
    #     f.write(audio.get_wav_data())


@bp.route("/actions/merge_with_previous", methods=["POST"])
def merge_with_previous(author, title, chapter_number, language):
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)

    phrase_index = int(request.form.get("phrase_index", None))
    page = request.form.get("page", None)

    phrase_xml = chapter.get_phrase(phrase_index)

    log.info(f"Merging phrase {phrase_index} with previous phrase.")

    # find the previous phrase, this is the phrase we are augmenting.
    previous_phrase = phrase_xml.find_previous_sibling("phrase")
    if previous_phrase is None:
        log.error(f"No previous phrase found for phrase {phrase_index}.")
        return "No previous phrase found", 404

    log.info(f"Previous phrase found: {previous_phrase['index']}")

    # append our text to the previous phrase
    previous_phrase_text = previous_phrase.get_text().strip()
    merging_phrase_text = phrase_xml.get_text().strip()
    new_phrase_text = f"{previous_phrase_text} {merging_phrase_text}"

    previous_phrase.clear()
    new_phrase = NavigableString(new_phrase_text)
    previous_phrase.append(new_phrase)

    # remove our src, so the audio will be regenerated
    previous_phrase.attrs.pop("src", None)

    # remove our pronunciation, so the audio will be regenerated
    previous_phrase.attrs.pop("pronunciation", None)

    if "src" in phrase_xml.attrs:
        wavfile = os.path.join(const.LIBRARY_DIR, phrase_xml.attrs["src"].lstrip("/"))

        if os.path.exists(wavfile):
            log.info(f"Removing audio file {wavfile} for phrase {phrase_index}.")
            os.remove(wavfile)

    # remove ourselves from the book
    phrase_xml.decompose()
    log.info(f"Removed phrase {phrase_index} from book.")

    # save the book xml
    chapter.save_xml()

    return htmx.phrase_editor(chapter, previous_phrase, page), 200


@bp.route("/actions/generate_all_missing_audio", methods=["POST"])
def generate_all_missing_audio(author, title, chapter_number, language):
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)

    audio.generate_all_audio(chapter, force=False)

    return htmx.generate_all_missing_audio_button(chapter)


@bp.route("/actions/regenerate_all_audio", methods=["POST"])
def regenerate_all_audio(author, title, chapter_number, language):
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)

    audio.generate_all_audio(chapter, force=True)
    return htmx.regenerate_all_audio_button(chapter)


@bp.route("/actions/reset_audio_cache_json", methods=["POST"])
def reset_audio_cache_json(author, title, chapter_number, language):
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)

    for phrase_xml in chapter.get_xml().find_all("phrase"):
        phrase_xml.attrs.pop("duration", None)
        phrase_xml.attrs.pop("frames", None)

    chapter.save_xml()

    # audio_cache.delete_audio_cache(chapterdir)
    # generate_audio(chapterdir)

    # create a new audo_cache.json
    # generate_audio(chapterdir)
    # recalculate_image_frames(chapterdir)

    return htmx.reset_audio_cache_json_button(chapter)


# http://localhost:5000/Aesop/Fables/0026/audio/actions/set_dinkus_style/8_0
@bp.route("/actions/set_dinkus_style/<phrase_id>", methods=["POST"])
def set_dinkus_style(author, title, chapter_number, language, phrase_id):
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)

    phrase_xml = chapter.get_xml().find("phrase", id=phrase_id)

    if phrase_xml is None:
        log.error(f"Phrase with id {phrase_id} not found in book {chapter}")
        return "Phrase not found", 404

    # Get the new dinkus style from the form data
    new_dinkus_style = request.form.get("style", None)
    if new_dinkus_style:
        phrase_xml.attrs["style"] = new_dinkus_style

    chapter.save_xml()

    phrase_out = f'<div id="phrase-{phrase_id}" class="wa-card">'
    phrase_out += htmx.dinkus_editor(phrase_xml)
    phrase_out += "</div>"
    return phrase_out, 200


# /Aesop/Fables/chapter/0026/audio/actions/set_dinkus_duration/8_0
@bp.route("/actions/set_dinkus_duration/<phrase_id>", methods=["POST"])
def set_dinkus_duration(author, title, chapter_number, language, phrase_id):
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)

    phrase_xml = chapter.get_xml().find("phrase", id=phrase_id)

    if phrase_xml is None:
        log.error(f"Phrase with id {phrase_id} not found in book {chapter}")
        return "Phrase not found", 404

    # Get the new dinkus duration from the form data
    new_dinkus_duration = request.form.get("duration", None)
    if new_dinkus_duration:
        phrase_xml.attrs["duration"] = new_dinkus_duration

    chapter.save_xml()

    phrase_out = f'<div id="phrase-{phrase_id}" class="wa-card">'
    phrase_out += htmx.dinkus_editor(phrase_xml)
    phrase_out += "</div>"
    return phrase_out, 200
