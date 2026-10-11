import html
import os

import numpy as np
import speech_recognition as sr
import torch
from bs4 import BeautifulSoup
from bs4.element import NavigableString
from flask import Blueprint, g, make_response, render_template, request, send_file
from speech_recognition.audio import AudioData
from transformers import (
    WhisperFeatureExtractor,
    WhisperForConditionalGeneration,
    WhisperProcessor,
)

import const
import logger
from artifact_editor import (
    llm,
    tools,
)
from artifact_editor.phrases.pronunciation.views import bp as pronunciation_bp
from artifact_editor.phrases.sound.views import bp as sound_bp
from artifact_editor.phrases.timeline.views import bp as timeline_bp
from artifact_editor.author.author import Author
from artifact_editor.chapter.chapter import (
    Chapter,
    chapter_url_defaults_helper,
    chapter_url_value_preprocessor,
)

from . import (
    htmx,
    phrases,
)

log = logger.log(__name__)

bp = Blueprint(
    "phrases",
    __name__,
    template_folder=os.path.join(os.path.dirname(__file__), "templates"),
)


@bp.url_defaults
def my_url_defaults(endpoint, values):
    chapter_url_defaults_helper(endpoint, values)


@bp.url_value_preprocessor
def my_url_value_preprocessor(endpoint, values):
    chapter_url_value_preprocessor(endpoint, values)


bp.register_blueprint(sound_bp, url_prefix="/sound")
bp.register_blueprint(pronunciation_bp, url_prefix="/pronunciation")
bp.register_blueprint(timeline_bp, url_prefix="/timeline")

# @bp gives us "/<author>/<path:title>/<chapter>/audio")
# so all of these are (author, title, chapter) at least.


# POST /Bible/Old%20Testament/0001/audio/actions/try_cmu_dict
@bp.route("/actions/try_cmu_dict", methods=["POST"])
def audio_try_cmu_dict():
    bookdir = tools.get_bookdir(g.author.name, g.title)
    phrases.try_cmu_dict(bookdir)
    return "", 204


# /Bible/Old%20Testament/0001/audio/actions/add_image_slot/4_0
@bp.route("/actions/add_image_slot/<phrase_index>/<after>/", methods=["POST"])
def add_image_slot(phrase_index: str, after: str | bool = True):
    # more or less assume true
    after = after or after.lower() in ["true", "1", "yes"]

    # with clear paths to find false.
    if after.lower() in ["false", "0", "no"]:
        after = False

    log.info(f"Adding image slot to phrase {phrase_index}")
    # add an image slot prior to the specified phrase
    soup = g.chapter.get_xml()

    phrase_xml = soup.find("phrase", index=phrase_index)

    # create a new image slot
    new_image_xml = soup.new_tag("image")
    if after:
        phrase_xml.insert_after(new_image_xml)
    else:
        phrase_xml.insert_before(new_image_xml)

    # re-index _all_ images
    image_index = 0
    for image_xml in soup.findAll("image"):
        image_xml.attrs["index"] = str(image_index)
        image_index += 1

    g.chapter.save_xml()

    return htmx.image_placeholder(g.chapter, new_image_xml), 200


# /Grimm/Fairy%20Tales/1/english/audio/actions/add_delay/3
@bp.route("/actions/add_delay/<int:phrase_id>", methods=["POST"])
def add_delay(phrase_id: int):
    """
    Add a widget to control delay (audio silence) at the end of a phrase.
    default is zero -- there is some baked in delay in the TTS system.
    """
    log.info(f"Adding delay control to phrase {phrase_id}")
    # add an image slot prior to the specified phrase
    phrase_xml = g.chapter.get_xml().find("phrase", index=phrase_id)
    if phrase_xml is None:
        log.error(f"Phrase with id {phrase_id} not found in book {g.chapter}")
        return "Phrase not found", 404

    phrase_xml.attrs["delay"] = "0"

    g.chapter.save_xml()

    return audio_phrases()


@bp.route("/actions/add_sound/<int:phrase_index>", methods=["POST"])
def add_sound(phrase_index: int):
    """
    Add an empty sound slot after the indicated phrase.
    """
    log.info(f"Adding sound slot to phrase {phrase_index}")
    # add an image slot prior to the specified phrase

    phrase_xml = g.chapter.get_phrase(phrase_index)
    if phrase_xml is None:
        log.error(f"Phrase with id {phrase_index} not found in book {g.chapter}")
        return "Phrase not found", 404

    new_sound_xml = g.chapter.soup.new_tag("sound")
    new_sound_xml.attrs.update(
        {"name": "", "start_offset": 0, "end_offset": None, "blocking": True}
    )
    phrase_xml.insert_after(new_sound_xml)

    g.chapter.save_xml()

    return audio_phrases()


# http://localhost:5000/L.%20Frank%20Baum/The%20Marvelous%20Land%20of%20Oz/0001/audio/actions/remove_delay/0_1?phrase_id=0_1&speaker=Narrator&pronunciation=%C3%B0%C9%99%20m%CB%88%C9%91%CB%90%C9%B9v%C9%99l%C9%99s%20l%CB%88%C3%A6nd%20%CA%8Cv%20%CB%88%C9%91%CB%90z&delay=0&page=1
@bp.route("/actions/remove_delay/<int:phrase_index>", methods=["DELETE"])
def remove_delay(phrase_index: int):
    phrase_index = int(phrase_index)

    log.info(f"Removing delay control from phrase {phrase_index}")

    phrase_xml = g.chapter.get_phrase(phrase_index)
    if phrase_xml is None:
        log.error(f"Phrase with index {phrase_index} not found")
        return "Phrase not found", 404

    if "delay" in phrase_xml.attrs:
        del phrase_xml.attrs["delay"]

    g.chapter.save_xml()

    return audio_phrases()


# http://localhost:5000/L.%20Frank%20Baum/The%20Marvelous%20Land%20of%20Oz/0001/audio/actions/set_delay/0_1
@bp.route("/actions/set_delay/<int:phrase_index>", methods=["POST"])
def set_delay(phrase_index: int):
    phrase_index = int(phrase_index)

    delay = float(request.form.get("delay", "0"))
    log.info(f"Setting delay to {delay} seconds for phrase {phrase_index}")

    phrase_xml = g.chapter.get_phrase(phrase_index)
    if phrase_xml is None:
        log.error(f"Phrase with index {phrase_index} not found")
        return "Phrase not found", 404

    phrase_xml.attrs["delay"] = str(delay)

    g.chapter.save_xml()

    return audio_phrase(phrase_index)


def audio_phrase(phrase_index: int):
    """
    Return a fully populated single phrase editor widget.
    """
    phrase_xml = g.chapter.get_phrase(phrase_index)

    if phrase_xml is None:
        return "Phrase not found", 404

    page = request.form.get("page", None)
    return htmx.phrase_editor(g.chapter, phrase_xml, page), 200


@bp.route("/phrases", methods=["GET"])
def audio_phrases(page: int | None = None):
    pretty_mode = False

    if page is None:
        page = request.args.get("page", None)

    if pretty_mode:
        payload = BeautifulSoup(
            phrases.phrase_sequence(g.chapter, page=page), "xml"
        ).prettify()
    else:
        payload = phrases.phrase_sequence(g.chapter, page=page)

    response = make_response(payload)
    response.status_code = 200
    response.headers["HX-Trigger-After-Swap"] = "latex_editor_init"

    return response


# POST /L.%20Frank%20Baum/The%20Marvelous%20Land%20of%20Oz/0001/paragraph/set_fullscreen
@bp.route("/paragraph/set_fullscreen", methods=["POST"])
def set_paragraph_fullscreen():
    paragraph_index = int(request.form.get("paragraph_index", -1))
    fullscreen = request.form.get("fullscreen", "false").lower() == "true"

    all_paragraphs = g.chapter.get_xml().findAll("paragraph")
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

    g.chapter.save_xml()

    return phrases.phrase_sequence(g.chapter, page=paragraph_index + 1)


@bp.route("/text_snippet/<phrase_index>.png", methods=["GET"])
def text_snippet(phrase_index: int):
    phrase_xml = g.chapter.get_phrase(phrase_index)

    fn = g.chapter.get_highlighted_text_snippet_fn(phrase_xml)

    return send_file(fn, mimetype="image/png")


@bp.route("/text_snippet/redraw/<phrase_index>.png", methods=["GET"])
def redraw_text_snippet(phrase_index: int):
    phrase_xml = g.chapter.get_phrase(phrase_index)
    # redraw the text snippet
    # force will take care of the redraw
    g.chapter.get_highlighted_text_snippet(phrase_xml, force=True)
    # send back the updated widget
    return htmx.get_phrase_latex_image(g.chapter, phrase_xml), 200


@bp.route("/", methods=["GET"])
def base():
    # get the "page" http parameter
    log.info(f"base_audio({g.author}, {g.title}, {g.chapter.number})")

    try:
        page = int(request.args.get("page", None))
    except (TypeError, ValueError):
        page = 1

    pretty_author = g.chapter.config.get("author", g.author.pretty_name)
    pretty_title = g.chapter.config.get("title", g.title)

    return render_template(
        "phrases.html",
        language="english",
        pretty_language="English",
        author=g.author,
        pretty_author=pretty_author,
        title=g.title,
        pretty_title=pretty_title,
        chapter=g.chapter,
        chapterurl=g.chapter.url,
        phrase_sequence=phrases.phrase_sequence(g.chapter, page=page),
        regenerate_all_audio_button=htmx.regenerate_all_audio_button(g.chapter),
        generate_all_missing_audio_button=htmx.generate_all_missing_audio_button(
            g.chapter
        ),
        repronounce_where_missing_button=htmx.repronounce_where_missing_button(
            g.chapter
        ),
        reset_audio_cache_json_button=htmx.reset_audio_cache_json_button(g.chapter),
        recalculate_all_phrase_frames_button=htmx.recalculate_all_phrase_frames_button(
            g.chapter
        ),
        section="phrase.workshop",
        section_cosmetic="Phrases › Workshop",
    )


@bp.route("/actions/recalculate_all_phrase_frames", methods=["POST"])
def recalculate_all_phrase_frames():
    for phrase_xml in g.chapter.get_xml().find_all("phrase"):
        wavfile = phrases.get_wavfile_filename(phrase_xml, g.chapter)
        if wavfile and os.path.exists(wavfile):
            duration = phrases.get_wav_duration(wavfile)
            phrase_xml.attrs["duration"] = str(duration)
            phrase_xml.attrs["frames"] = int(duration * const.FPS)
        else:
            log.warning(
                f"Wav file {wavfile} not found for phrase {phrase_xml['index']}"
            )
    g.chapter.save_xml()

    phrases.recalculate_image_frames(g.chapter)

    return htmx.recalculate_all_phrase_frames_button(g.chapter)


@bp.route("/image_slot/<int:image_index>", methods=["DELETE"])
def delete_image_slot(image_index: int):
    image_index = int(image_index)

    # Remove the image slot from the audio data
    image_xml = g.chapter.get_image(image_index)

    if image_xml:
        # self destruct the image slot
        image_xml.decompose()

        # Save the book after removing the image slot
        g.chapter.save_xml()
        g.chapter.load_xml(force=True)
        return "", 204
    else:
        log.error(f"Image slot at index {image_index} not found.")
        return "", 404


@bp.route("/actions/set_speaker/<phrase_index>", methods=["POST"])
def assign_phrase_speaker(phrase_index: int):
    phrase_index = int(phrase_index)
    chapter = g.chapter

    speaker = request.form.get("speaker", None)

    phrases.save_speaker(chapter, phrase_index, speaker)
    return htmx.speaker_selector(
        chapter,
        chapter.get_phrase(phrase_index),
    )


@bp.route("/actions/set_latex/<int:phrase_index>", methods=["PUT"])
def set_latex(phrase_index: int):
    phrase_index = int(phrase_index)

    phrase_xml = g.chapter.get_phrase(phrase_index)
    if phrase_xml is None:
        return "Phrase not found", 404

    new_latex = request.form.get("latex", None)
    if new_latex is not None:
        phrase_xml.attrs["latex"] = new_latex
    else:
        phrase_xml.attrs["latex"] = ""
    g.chapter.save_xml()

    # you can't just re-set the latex and run away.

    # re-draw the full chapter pdf and rainbow latex

    g.chapter.get_highlighted_text_snippet(phrase_xml, force=True)

    # //page = request.form.get("page", None)
    return "", 200
    # //htmx.phrase_editor(g.chapter, phrase_xml, page), 200


# http://localhost:5000/Ambrose%20Bierce/An%20Occurrence%20at%20Owl%20Creek%20Bridge/0001/audio/actions/set_second_phrase/3_18
@bp.route("/actions/set_split/<int:phrase_index>", methods=["POST"])
def set_split(phrase_index: int):
    """
    Given form values first_phrase and second_phrase, return a nice UI for
    deciding exactly where to split the phrase using the provided split as the
    current division.
    """
    phrase_index = int(phrase_index)

    phrase_xml = g.chapter.get_phrase(phrase_index)

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
        g.chapter,
        phrase_xml,
        split=(whole_source_phrase[:index], whole_source_phrase[index:]),
        page=request.form.get("page", None),
    ), 200


# set_linebreak
@bp.route("/actions/set_linebreak/<phrase_index>", methods=["PUT"])
def set_linebreak(
    author: str, title: str, chapter_number: int, language: str, phrase_index: int
):
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


@bp.route("/actions/save_split_phrase/<phrase_index>", methods=["POST"])
def save_split_phrase(phrase_index: int):
    """
    Commit the currently proposed split to the chapter
    XML.
    """
    phrase_index = int(phrase_index)

    phrase_xml = g.chapter.get_phrase(phrase_index)

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

    # ok smartass, how about the "latex" attribute?  we need it split correctly too.
    # usually, second_phrase.strip() will be a substring of phrase_xml.attrs["latex"]
    if "latex" in phrase_xml.attrs:
        latex = phrase_xml.attrs["latex"]
        split_index = latex.find(second_phrase[:8].strip())
        if split_index != -1:
            new_phrase_xml.attrs["latex"] = latex[split_index:]
            phrase_xml.attrs["latex"] = latex[:split_index]

    #
    # <phrase duration="3.2325" fragdex="1" frames="81" id="35_1" pronunciation="“ðə fˈɜɹst mˈæn hæd hɪz θɹˈi wˈɪʃᵻz. jˈɛs,”" speaker="The_Sergeant-Major" src="ph_1__The_first_man_had_his_th_a7786732_8656.wav">
    #   “The first man had his three wishes. Yes,”
    # </phrase>

    # fragdex
    # id
    # speaker, easy
    new_phrase_xml.attrs["speaker"] = phrase_xml.attrs.get("speaker", "Narrator")

    paragraph = phrase_xml.find_parent("paragraph")

    phrase_xml.insert_after(new_phrase_xml)

    phrases.reindex_phrases(g.chapter)
    g.chapter.load_xml(force=True)

    fragdex = 0
    for fragment in paragraph.children:
        if not hasattr(fragment, "attrs"):
            continue
        fragment.attrs["fragdex"] = str(fragdex)

        if fragment.name == "phrase":
            fragment.attrs["id"] = f"{paragraph.attrs['index']}_{fragdex}"

        fragdex += 1

    g.chapter.save_xml()

    page = request.form.get("page", None)
    return phrases.phrase_sequence(g.chapter, page=page)


# /Ambrose%20Bierce/An%20Occurrence%20at%20Owl%20Creek%20Bridge/chapter/0001/audio/actions/split_phrase
@bp.route("/actions/split_phrase", methods=["POST"])
def split_phrase():
    """
    Initiate the phrase split UI.
    """
    phrase_index = int(request.form["phrase_index"])
    page = request.form.get("page", None)

    phrase_xml = g.chapter.get_phrase(phrase_index)

    if phrase_xml is None:
        log.error(f"Phrase with id {phrase_index} not found in book {g.chapter}")
        return "Phrase not found", 404

    return htmx.phrase_editor(
        g.chapter, phrase_xml, page, split=(phrase_xml.get_text().strip(), "")
    ), 200


@bp.route("/actions/regenerate_phrase/<int:phrase_index>", methods=["POST"])
def regenerate_phrase(phrase_index: int, force=True):
    """
    TODO: this should redraw the whole clip frame, not just the
    stupid button.
    """
    phrase_index = int(phrase_index)

    page = request.form.get("page", None)
    chapter_audio_target = (
        request.headers.get("HX-Target")
        == f"chapter-audio-controls-{phrase_index}"
    )
    g.chapter.load_xml()

    phrase_xml = g.chapter.get_phrase(phrase_index)
    log.warning("Alpha", phrase_xml=phrase_xml, has_soup=g.chapter.soup is not None)
    if phrase_xml is None:
        return "Phrase not found", 404

    # adds 'src' if it doesn't exist, with a plausible filename.
    wavfile = phrases.get_wavfile_filename(phrase_xml, g.chapter)

    log.warning("Beta", phrase_xml=phrase_xml, has_soup=g.chapter.soup is not None)

    # should do NOTHING.
    phrase_xml = g.chapter.get_phrase(phrase_index)
    log.warning("Gamma", phrase_xml=phrase_xml, has_soup=g.chapter.soup is not None)

    if wavfile is None:
        log.error(f"Phrase with index {phrase_index} not found in book {g.chapter}")
        return "Phrase not found", 404

    if os.path.exists(wavfile):
        if force:
            # _regenerate_ phrase
            log.info(f"Deleted existing wav file: {wavfile}")
            os.remove(wavfile)
        else:
            log.info(f"Phrase {phrase_index} already has an audio file: {wavfile}")
            if chapter_audio_target:
                return htmx.chapter_audio_controls(g.chapter, phrase_xml), 200
            return htmx.phrase_editor(g.chapter, phrase_xml, page), 200

    # text_to_speak = phrases.clean_latex(phrase_xml.get_text())
    # log.info(f"Submitting {phrase_xml.get_text()} to TTS for phrase {phrase_index}")

    # regenerate the wav for this phrase
    llm.text_2_audio(
        g.chapter,
        phrase_xml,
        phrase_xml.attrs.get("speaker", "Narrator"),
        wavfile,
        force=False,
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
                const.COMFY_DIRS["artifactserver"]["OUTPUT_DIR"],
                "audio",
                os.path.basename(wavfile).replace(".wav", ".pronunciation.txt"),
            )

            with open(pronunciation_filename, "r") as f:
                # wavfile.replace(".wav", ".pronunciation") + ".txt", "r") as f:
                new_pronunciation = f.read().strip()
                log.info(f"Found pronunciation file: {pronunciation_filename}")
                log.info(f"New pronunciation: {new_pronunciation}")

        except FileNotFoundError:
            new_pronunciation = None

    # in-place pad audio with silence, just enough to be an exact fps mulitple
    log.info("[regenerate_phrase] Padding audio file to frame: %s", wavfile)
    phrases.wav_pad_to_frame(wavfile)

    phrase_xml.attrs["src"] = os.path.basename(wavfile)
    duration = phrases.get_wav_duration(wavfile)

    log.info("Duration measured as: %s seconds", duration)
    phrase_xml.attrs["duration"] = str(duration)
    phrase_xml.attrs["frames"] = int(duration * const.FPS)

    if new_pronunciation:
        log.info(
            f"Updating pronunciation for phrase {phrase_index} to {new_pronunciation}"
        )
        phrase_xml.attrs["pronunciation"] = new_pronunciation

    elif not new_pronunciation:
        log.warning(f"No pronunciation found for phrase {phrase_index} at {wavfile}")

    test_phrase = g.chapter.get_phrase(phrase_index)
    log.warning(f"Test phrase after regeneration: {test_phrase}")

    log.info(f"Before: {phrase_xml.attrs=}")
    g.chapter.save_xml()

    log.info(f"After 1: {phrase_xml.attrs=}")
    phrase_xml = g.chapter.get_phrase(phrase_index)
    log.info(f"After 2: {phrase_xml.attrs=}")

    phrases.recalculate_image_frames(g.chapter)

    g.chapter.save_xml()
    if chapter_audio_target:
        return htmx.chapter_audio_controls(g.chapter, phrase_xml), 200
    return htmx.phrase_editor(g.chapter, phrase_xml, page), 200


# POST /Bible/Old%20Testament/0001/audio/actions/repronounce_missing_words
@bp.route("/actions/repronounce_missing_words", methods=["POST"])
def repronounce_missing_words(author: str, title: str, chapter_number: int):
    chapterdir = tools.get_chapterdir(author, title, chapter_number)
    chapterurl = tools.get_chapterurl(author, title, chapter_number)

    phrases.repronounce_where_missing(chapterdir)

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


@bp.route("/actions/record_audio/<int:phrase_index>", methods=["POST"])
def record_audio(
    author: str, title: str, chapter_number: int, language: str, phrase_index: int
):
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
    #     f.write(phrases.get_wav_data())


@bp.route("/actions/merge_with_previous", methods=["POST"])
def merge_with_previous(author: str, title: str, chapter_number: int, language: str):
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

    if chapter.config.get("TEXT_STRUCTURE", "") == "verse":
        # we want to retain line breaks in verse mode
        previous_phrase_text = previous_phrase.get_text()
        merging_phrase_text = phrase_xml.get_text()
        new_phrase_text = f"{previous_phrase_text} {merging_phrase_text}"

        previous_phrase.clear()
        new_phrase = NavigableString(new_phrase_text)
        previous_phrase.append(new_phrase)
    else:
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
def generate_all_missing_audio():
    phrases.generate_all_audio(g.chapter, force=False)

    return htmx.generate_all_missing_audio_button(g.chapter)


@bp.route("/actions/regenerate_all_audio", methods=["POST"])
def regenerate_all_audio(author: str, title: str, chapter_number: int, language: str):
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)

    phrases.generate_all_audio(chapter, force=True)
    return htmx.regenerate_all_audio_button(chapter)


@bp.route("/actions/reset_audio_cache_json", methods=["POST"])
def reset_audio_cache_json(author: str, title: str, chapter_number: int, language: str):
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)

    for phrase_xml in chapter.get_xml().find_all("phrase"):
        phrase_xml.attrs.pop("duration", None)
        phrase_xml.attrs.pop("frames", None)

    chapter.save_xml()

    return htmx.reset_audio_cache_json_button(chapter)


# http://localhost:5000/Aesop/Fables/0026/audio/actions/set_dinkus_style/8_0
@bp.route("/actions/set_dinkus_style/<phrase_id>", methods=["POST"])
def set_dinkus_style(
    author: str, title: str, chapter_number: int, language: str, phrase_id: str
):
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
@bp.route("/actions/set_dinkus_duration/<int:phrase_id>", methods=["POST"])
def set_dinkus_duration(
    author: str, title: str, chapter_number: int, language: str, phrase_id: int
):
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


# goal: level of assistance needed to generate 15 minutes of content per week.
# That is one typical book chapter.

# daily effort required is
# two minutes of good quality content.

# the automation need to be strong enough
# to create two minutes in one sitting.

# about 10 paragraphs.

# how long does it take to generate 2 minutes right now?
# TODO:
#   need to be able to defer generation to on overnight build.

# one chapter per week == about 15 minutes of content.
# I want two minutes of output in a one hour session.

# 30 minutes to generate 1 minute
# 10 minutes to make 20 seconds
# 5 minutes per 10 second clip

# I'm not even close to that, more like
# 30 minutes per 5 second clip
# 90 hours for 15 minutes
# call it two months per chapter.
