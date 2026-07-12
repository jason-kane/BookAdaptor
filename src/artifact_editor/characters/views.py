import glob
import os
import random
import shutil
import textwrap

from flask import (
    Blueprint,
    redirect,
    render_template,
    request,
    send_from_directory,
    url_for,
)

import audio_effects
import const
import logger
from artifact_editor import (
    config,
    llm,
    tools,
)
from artifact_editor.author.author import Author
from artifact_editor.chapter.chapter import Chapter
from artifact_editor.tools import (
    get_chapterdir,
    get_chapterurl,
)

from . import (
    characters,
    htmx,
    voices,
)

log = logger.log(__name__)

bp = Blueprint(
    "characters",
    __name__,
    template_folder=os.path.join(os.path.dirname(__file__), "templates"),
)


# POST /L.%20Frank%20Baum/The%20Marvelous%20Land%20of%20Oz/0002/characters/import
@bp.route("import", methods=["POST"])
def import_character(author, title, chapter, language):
    chapterdir = get_chapterdir(author, title, chapter)
    mybook = get_book(chapterdir)
    previous_chapter = mybook.get_previous_chapter()

    log.info(
        f'Importing character for author: "{author}", title: "{title}", chapter: "{chapter}"'
    )

    character_name = request.form.get("character")
    log.info(f'Character to import: "{character_name}"')

    previous_characters = characters.get_all_characters(
        previous_chapter,
        get_chapterdir(
            previous_chapter.author,
            previous_chapter.title,
            previous_chapter.chapter_number,
        ),
    )

    if character_name in previous_characters:
        characters.save_character(
            mybook,
            chapterdir,
            character_tag=character_name,
            character_dict=previous_characters[character_name],
        )

    # Implement the logic for importing a character from the previous chapter
    return "Character imported", 200


@bp.route("/", methods=["GET"])
def characters_base(author, title, chapter_number, language):
    author = Author(author)
    chapter = Chapter(author, title, chapter_number)

    log.info(f'author: "{author}", title: "{title}", chapter: "{chapter.number}"')

    all_characters = characters.get_all_characters(chapter)
    unattached_speakers = set()
    for phrase in chapter.get_xml().find_all("phrase"):
        character_name = phrase.get("speaker")
        if character_name and character_name not in all_characters:
            unattached_speakers.add(character_name)

    # sorted by how often they are visually present in the story
    # use whenever we are presenting a list of characters in the UI.
    # instead of alphabetical.
    all_characters = tools.cast_bestsort(all_characters)

    # we want the characters from the previous chapter.
    previous_chapter = chapter.previous()
    previous_characters = {}
    if previous_chapter:
        previous_characters = characters.get_all_characters(
            previous_chapter,
            get_chapterdir(
                previous_chapter.author.name,
                previous_chapter.title,
                previous_chapter.number,
            ),
        )
        log.info("Previous chapter characters: %s", previous_characters)
    else:
        log.info("No previous chapter found")

    previous_chapter_characters = []
    for character_label in previous_characters:
        pc = previous_characters[character_label]
        previous_chapter_characters.append(
            f'<wa-option value="{character_label}">{pc['name']}</wa-option>'
        )

    import_character_form = ""
    if previous_chapter_characters:
        import_character_form = f"""
    <div id="import_character" class="wa-stack"
        <h2>Import Character</h2>
        <div class="wa-cluster">
            <wa-select name="character" id="character" placeholder="Select one">
                {''.join(previous_chapter_characters)}
            </wa-select>

            <wa-button 
                class="import-character-button"
                hx-post="{url_for('library.book.chapter.characters.import_character', author=author, title=title, chapter_number=chapter_number, language=language)}"
                hx-target="#import_character"
                hx-include="#character"
                hx-on::before-request="beforeRequest(this,event)"
                hx-on::after-request="afterRequest(this,event)"
                hx-swap="outerHTML transition:true">Import Character</wa-button>
        </div>
    </div>
    """

    set_name_url = ""
    # set_name_url = url_for(
    #     'characters.set_name',
    #     **chapter.kwargs,
    #     character_name=speaker
    # )

    rename_select = f"""
    <wa-select
        name="rename_to"
        hx-post="{ set_name_url }"
        hx-target="#character_drift_resolution"
        hx-swap="outerHTML transition:true">
        <wa-option value="">Select Character</wa-option>
    """
    for character in all_characters:
        c = all_characters[character]
        rename_select += f'<wa-option value="{ character }">{ c['name'] }</wa-option>'
    rename_select += "</wa-select>"

    return render_template(
        "characters.html",
        language="english",
        rename_select=rename_select,
        pretty_language="English",
        author=author,
        pretty_author=chapter.config["author"],
        title=title,
        pretty_title=chapter.config["title"],
        chapter=chapter,
        characters=all_characters,
        import_character_form=import_character_form,
        section="characters",
        section_cosmetic="Characters",
        all_character_cards=htmx.all_character_cards(chapter, all_characters),
        unattached_speakers=unattached_speakers,
        drift_resolution_widget=htmx.drift_resolution_widget(chapter),
    )


# PUT /Malory/LeMorteDArthur_V01_B01/0001/characters/Ulfius/pronunciation_filter
@bp.route("<character_name>/pronunciation_filter", methods=["PUT"])
def htmx_pronunciation_filter(author, title, chapter_number, language, character_name):
    """
    Update the character's pronunciation filter.
    """
    author = Author(author)
    chapter = Chapter(
        author=author, title=title, number=chapter_number, language=language
    )

    character_dict = characters.get_character(chapter, character_name)

    pronunciation_filter = request.form.get("pronunciation_filter", 2000)

    log.info(
        "Setting pronunciation_filter to %s for character %s",
        pronunciation_filter,
        character_name,
    )
    character_dict["pronunciation_filter"] = pronunciation_filter

    characters.save_character(chapter, character_name, character_dict)

    return htmx.character_pronunciation_widget(
        chapter, character_dict["tag"], pronunciation_filter
    )


@bp.route("<character_name>/actions/condense_description", methods=["POST"])
def condense_description(author, title, chapter_number, language, character_name):
    """
    Condense the character's description.
    """
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)

    character_dict = characters.get_character(chapter, character_name)
    existing_description = character_dict.get("description", "")

    if existing_description:
        description = characters.condense_description(existing_description)
        character_dict["description"] = description
        characters.save_character(chapter, character_name, character_dict)
    else:
        description = ""

    return htmx.character_description_response(chapter, character_name, description)


# /L.%20Frank%20Baum/The%20Marvelous%20Land%20of%20Oz/0001/characters/Narrator/actions/generate_description
@bp.route("<character_tag>/actions/generate_description", methods=["POST"])
def generate_description(author, title, chapter_number, language, character_tag):
    """
    Generate a description for the character based on the entire chapter.
    """
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)

    description = characters.generate_description(chapter, character_tag)

    return htmx.character_description_response(
        chapter,
        character_tag,
        description,
    )


@bp.route("<character_tag>/actions/fanciful_description", methods=["POST"])
def fanciful_description(author, title, chapter_number, language, character_tag):
    """
    Generate a fanciful description for the character loosely based on the entire chapter.
    """
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)

    description = characters.fanciful_description(chapter, character_tag)

    return htmx.character_description_response(
        chapter,
        character_tag,
        description,
    )




# remove_voice_weights
@bp.route("<character_name>/actions/remove_voice_weights", methods=["POST"])
def remove_voice_weights(author, title, chapter_number, language, character_name):
    """
    Remove all voice weights for this character.
    """
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)
    chapterdir = get_chapterdir(author, title, chapter_number)
    chapterurl = get_chapterurl(author, title, chapter_number)

    character_dict = characters.get_character(chapter, character_name)
    character_dict["voices"] = []
    characters.save_character(chapter, character_name, character_dict)

    return htmx.voice_blender(
        character_dict,
        character_name,
        chapterurl,
        gender=character_dict.get("voice_gender", "all"),
        language=character_dict.get("voice_language", "all"),
        accent=character_dict.get("voice_accent", "all"),
    )


# /L.%20Frank%20Baum/The%20Marvelous%20Land%20of%20Oz/0001/characters/Narrator/actions/random_voice_weights
@bp.route("<character_name>/actions/random_voice_weights", methods=["POST"])
def randomize_voice_weights(author, title, chapter_number, language, character_name):
    """
    Randomize the voice weights for this character.
    """
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)

    character_dict = characters.get_character(chapter, character_name)
    for voice in character_dict.get("voices", []):
        voice["strength"] = int(random.uniform(0, 100))
    characters.save_character(chapter, character_name, character_dict)

    return htmx.voice_blender(
        chapter,
        character_dict,
        character_name,
    )


# http://localhost:5000/Aesop/Fables/0020/characters/The_Mouse/actions/update_name
@bp.route("<character_tag>/actions/update_name", methods=["POST"])
def set_name(author, title, chapter_number, language, character_tag):
    """
    Update the character's name.
    """
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)

    all_characters = characters.get_all_characters(chapter)
    character_dict = characters.tag_to_character(chapter, character_tag)

    name = request.form.get("name", "").strip()
    # don't allow a blank name
    if name:
        tag = characters.name_to_tag(name)
        if tag not in all_characters:
            # remove the old entry
            del all_characters[character_tag]
            # update the character entry
            character_dict["tag"] = tag
            character_dict["name"] = name

            all_characters[tag] = character_dict
            characters.save_characters(chapter, all_characters)
        elif character_tag == tag:
            # we can do some cleanup, just in case.  This lets users fix things
            # like whitespace in the 'name' that results in the same tag.
            character_dict["name"] = name
            all_characters[tag] = character_dict
            characters.save_characters(chapter, all_characters)

    return htmx.character_data(chapter, character_dict)


# http://localhost:5000/Aesop/Fables/0020/characters/The_Frog/actions/update_description
@bp.route("<character_tag>/actions/update_description", methods=["POST"])
def update_character_description(
    author, title, chapter_number, language, character_tag
):
    """
    Update the character's description.
    """
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)

    character_dict = characters.tag_to_character(chapter, character_tag)

    description = request.form.get("description", "").strip()

    character_dict["description"] = description
    characters.save_character(
        chapter,
        character_tag,
        character_dict,
    )

    return htmx.character_data(chapter, character_dict)


# /H.%20P.%20Lovecraft/Cool%20Air/0001/characters/Mechanic/actions/update_gender
@bp.route("<character_tag>/actions/update_gender", methods=["POST"])
def set_gender(author, title, chapter_number, language, character_tag):
    """
    Update the character's gender.
    """
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)

    character_dict = characters.tag_to_character(chapter, character_tag)

    gender = request.form.get("gender", "").strip()

    if gender:
        character_dict["gender"] = gender
        characters.save_character(
            chapter,
            character_tag,
            character_dict,
        )

    return htmx.character_data(chapter, character_dict)


# http://localhost:5000/Aesop/Fables/0020/characters/The_Frog/actions/update_tts_engine
@bp.route("<character_tag>/actions/update_tts_engine", methods=["POST"])
def update_tts_engine(author, title, chapter_number, language, character_tag):
    """
    Update the character's TTS engine.
    """
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)

    character_dict = characters.tag_to_character(chapter, character_tag)

    tts_engine = request.form.get("tts_engine", "").strip()

    # TODO: make sure it's a real engine.
    if tts_engine:
        character_dict["tts_engine"] = tts_engine
        characters.save_character(chapter, character_tag, character_dict)

    return htmx.character_data(chapter, character_dict)


# http://localhost:5000/H.%20P.%20Lovecraft/Cool%20Air/0001/characters/Esteban_Herrero/actions/update_age
@bp.route("<character_tag>/actions/update_age", methods=["POST"])
def set_age(author, title, chapter_number, language, character_tag):
    """
    Update the character's age.
    """
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)

    character_dict = characters.tag_to_character(chapter, character_tag)

    age = request.form.get("age", "").strip()

    if age:
        character_dict["age"] = age
        characters.save_character(chapter, character_tag, character_dict)

    return htmx.character_data(chapter, character_dict)


@bp.route("<character_tag>", methods=["DELETE"])
def delete_character(author, title, chapter_number, language, character_tag):
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)

    log.info(f"{author=}, {title=}, {chapter_number=}, {character_tag=}")

    all_characters = characters.get_all_characters(chapter)

    if character_tag in all_characters:
        del all_characters[character_tag]
        characters.save_characters(chapter, all_characters)
        log.info(
            "Deleted character %s from chapter %s", character_tag, chapter.chapterdir
        )
    else:
        log.error(
            "Character %s not found in chapter %s", character_tag, chapter.chapterdir
        )

    return htmx.all_character_cards(chapter, all_characters)


# POST /Aesop/Fables/0023/characters/Narrator/actions/toggle_global
@bp.route("<character_name>/actions/toggle_global", methods=["POST"])
def toggle_global_character(author, title, chapter_number, language, character_name):
    """
    Toggle the global character setting.
    """
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)

    character_dict = characters.get_character(chapter, character_name)

    if character_dict is None:
        return "Character not found", 404

    # Toggle the global character setting
    character_dict["is_global"] = not character_dict.get("is_global", False)

    characters.save_character(
        chapter,
        character_name,
        character_dict,
    )

    return htmx.character_data(chapter, character_dict)


@bp.route("", methods=["POST"])
def add_character(author, title, chapter_number, language):
    """
    Add a new character to the chapter.
    """
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)

    all_characters = characters.get_all_characters(chapter)

    # get the name of the new character
    character_name = request.form.get("name", "").strip()

    if not character_name:
        return "Character name is required", 400

    # ensure the character name is unique
    if character_name in all_characters:
        return f"Character '{character_name}' already exists", 400

    log.info("Creating new character %s in chapter %s", character_name, chapter)
    character_tag = characters.name_to_tag(character_name)
    all_characters[character_tag] = {
        "name": character_name,
        "description": request.form.get("description", ""),
        "tts_engine": request.form.get("tts_engine", ""),
        "tag": character_tag,
        "voices": [],
    }

    log.info("Saving updated characters for chapter %s", chapter)
    characters.save_characters(chapter, all_characters)

    log.info("Added new character %s to chapter %s", character_name, chapter)

    return redirect(
        url_for(
            "library.book.chapter.characters.characters_base",
            author=chapter.author.name,
            title=chapter.title,
            chapter_number=chapter.number,
            language=chapter.language,
        )
    )


@bp.route("<character_tag>/voice", methods=["POST"])
def add_voice_to_blend(author, title, chapter_number, language, character_tag):
    """
    Add a new voice to the character voice blender.
    """
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)

    log.info("Looking for character %s in chapter %s", character_tag, chapter)
    character_dict = characters.get_character(chapter, character_tag)

    log.info("Character is %s", character_dict)
    # get the voice from the form
    voice_id = request.form.get("voice", "").strip()
    # {character_name}_voice_selector

    if not voice_id:
        return "Voice ID is required", 400

    if voice_id not in voices.KOKORO_VOICES:
        return f"Voice '{voice_id}' not found", 400

    voice = voices.KOKORO_VOICES[voice_id]

    log.info("Adding voice %s to character %s", voice_id, character_tag)
    # add the voice to the character's voices
    character_dict.setdefault("voices", []).append(
        {
            "id": voice["id"],
            "name": voice["name"],
            "strength": 50,
        }
    )
    characters.save_character(chapter, character_tag, character_dict)

    # carry through the filters so the UX is not annoying.
    return htmx.voice_blender(
        chapter,
        character_dict,
        character_tag,
    )


# /Aesop/Fables/0018/characters/The_Woman/add_effect
@bp.route("<character_tag>/add_effect", methods=["POST"])
def htmx_add_effect_to_character(
    author, title, chapter_number, language, character_tag
):
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)

    log.info("Looking for character %s in chapter %s", character_tag, chapter)
    character_dict = characters.get_character(chapter, character_tag)

    log.info("Character is %s", character_dict)

    effect_key = request.form.get("effect", "").strip()
    if not effect_key:
        return "Effect key is required", 400

    if "effects" not in character_dict:
        character_dict["effects"] = {}

    if effect_key not in character_dict["effects"]:
        # default config for any effect == empty dict
        # we rely on the effect to fill in defaults as needed.
        # when we request the config widgets.
        character_dict["effects"][effect_key] = {}

    # defaults to 'all'
    character_dict["voice_gender"] = character_dict.get("voice_gender", "all")
    character_dict["voice_language"] = character_dict.get("voice_language", "all")
    character_dict["voice_accent"] = character_dict.get("voice_accent", "all")

    log.info(f"Saving as {character_dict=}")
    characters.save_character(chapter, character_tag, character_dict)

    return htmx.get_character_effects(chapter.url, character_tag, character_dict)


# /Aesop/Fables/0018/characters/The_Woman/effect/pitch_shift
@bp.route("<character_name>/effect/<effect_key>", methods=["PUT"])
def htmx_update_effect_config(
    author, title, chapter_number, language, character_name, effect_key
):
    """
    Generic handler for updating effect configurations.
    """
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)

    log.info("Looking for character %s in chapter %s", character_name, chapter)
    char = characters.get_character(chapter, character_name)

    log.info("Character is %s", char)

    # update the effect config with all form values except effect_key
    effect_cls = audio_effects.registry.get_effect(effect_key)

    effect_config = {}
    for key in effect_cls().get_configuration_keys():
        effect_config[key] = request.form.get(key, effect_config.get(key))

    char["effects"][effect_key] = effect_config

    characters.save_character(chapter, character_name, char)

    return htmx.get_character_effects(chapter.url, character_name, char)


@bp.route("<character_tag>/audio", methods=["POST"])
def create_audio_sample(author, title, chapter_number, language, character_tag):
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)

    log.info("Looking for character %s in chapter %s", character_tag, chapter)

    sample_sentence = request.form.get("sample_sentence", "").strip()
    character = characters.get_character(chapter, character_tag)
    log.info("Character is %s", character)

    filename = characters.generate_sample_audio(
        chapter,
        character_tag,
        sample_sentence,
        effects=character.get("effects", {}),
    )

    log.info("Generated audio for %s: %s", character_tag, filename)

    # log.info('Generated audio for %s: %s', character_name, filename)
    # base_filename = os.path.basename(filename)

    audio_src = url_for(
        "library.book.chapter.characters.sample_audio",
        character_tag=character_tag,
        filename=filename,
        **chapter.kwargs,
    )

    return (
        f"""<source 
            src="{audio_src}"
            type="audio/wav"></source>""",
        200,
    )


# /W.%20W.%20Jacobs/The%20Monkeys%20Paw/0001/characters/Narrator/sample/tmp9we8f2ek.wav
@bp.route("<character_tag>/sample/<filename>")
def sample_audio(author, title, chapter_number, language, character_tag, filename):
    """
    Serve the sample audio file for the character.
    """
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)

    # ensure the filename is safe
    safe_filename = os.path.basename(filename)

    # serve the file from the characters/sample directory
    # W. W. Jacobs/The Monkeys Paw/chapter/0001/audio_temp_cache/tmp9kktq12s.wav
    cachedir = os.path.join(const.LIBRARY_DIR, chapter.chapterdir, "audio_temp_cache")
    log.info(
        "Serving sample audio for %s: %s %s", character_tag, cachedir, safe_filename
    )
    return send_from_directory(
        cachedir, safe_filename, as_attachment=False, mimetype="audio/wav"
    )


# /W.%20W.%20Jacobs/The%20Monkeys%20Paw/0001/characters/Narrator/af_sarah/set_voice_strength
@bp.route("<character_tag>/<voice_id>/set_voice_strength", methods=["PUT"])
def voice_set_strength(
    author, title, chapter_number, language, character_tag, voice_id
):
    """
    Set the strength of a voice in the character's voice blender.
    """
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)

    log.info("Looking for character %s in chapter %s", character_tag, chapter)
    char = characters.get_character(chapter, character_tag)

    log.info("Character is %s", char)

    # get the strengths from the form
    all_voices = char.get("voices", [])
    voices_by_id = {v["id"]: v for v in all_voices}

    strength = request.form[voice_id].strip()

    for key in request.form:
        if key in voices.KOKORO_VOICES:
            # overwrite or create new
            voices_by_id[key] = {
                "id": key,
                "name": voices.KOKORO_VOICES[key]["name"],
                "strength": int(request.form[key].strip()),
            }

    char["voices"] = list(voices_by_id.values())

    # save the updated character
    characters.save_character(chapter, character_tag, char)

    # carry through the filters so the UX is not annoying.
    return htmx.single_voice_mixer(
        character_tag,
        voice_id,
        voices.KOKORO_VOICES[voice_id]["name"],
        strength,
        chapter,
    ), 200


@bp.route("<character_name>/drift_resolution", methods=["POST"])
def drift_resolution(author, title, chapter_number, language, character_name=""):
    """
    A change was made to the value of this field.  As a result, we need to
    rebuild the language, accent and voices selection fields to take
    the new value of this field into account.
    """
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)

    rename_to = request.form.get("rename_to", "").strip()

    character = characters.get_character(chapter, rename_to)

    if character:
        for phrase in chapter.phrases():
            if phrase.get("speaker") == character_name:
                phrase["speaker"] = character["tag"]
        chapter.save_xml()

    all_characters = characters.get_all_characters(mybook, chapterdir)

    for c in all_characters:
        all_characters[c]["tag"] = characters.name_to_tag(c)

    unattached_speakers = set()
    for phrase in mybook.soup.find_all("phrase"):
        character_name = phrase.get("speaker")
        if character_name and character_name not in all_characters:
            unattached_speakers.add(character_name)

    return render_template(
        "character_drift_resolution.html",
        unattached_speakers=unattached_speakers,
        chapterurl=chapterurl,
        characters=all_characters,
    )


@bp.route("<character_tag>/voice/set_gender", methods=["POST"])
def voice_set_gender(author, title, chapter_number, language, character_tag=""):
    """
    A change was made to the value of this field.  As a result, we need to
    rebuild the language, accent and voices selection fields to take
    the new value of this field into account.
    """
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)

    character = characters.get_character(chapter, character_tag)
    character["voice_gender"] = request.form.get("gender", "all")
    characters.save_character(chapter, character_tag, character)

    return htmx.voice_blender(
        chapter,
        character,
        character_tag,
    )


@bp.route("<character_tag>/actions/language_filter", methods=["POST"])
def voice_set_language(author, title, chapter_number, language, character_tag=""):
    """
    A change was made to the value of this field.  As a result, we need to
    rebuild the language, accent and voices selection fields to take
    the new value of this field into account.
    """
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)

    character = characters.get_character(chapter, character_tag)
    character["voice_language"] = request.form.get("language", "all")
    characters.save_character(chapter, character_tag, character)

    # the version of htmx.voice_blender() that only includes the selected language
    return htmx.voice_blender(
        chapter,
        character,
        character_tag,
    )


@bp.route("<character_tag>/actions/accent_filter", methods=["POST"])
def voice_set_accent(author, title, chapter_number, language, character_tag=""):
    """
    A change was made to the value of this field.  As a result, we need to
    rebuild the language, accent and voices selection fields to take
    the new value of this field into account.
    """
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)

    character = characters.get_character(chapter, character_tag)
    character["voice_accent"] = request.form.get("accent", "all")
    characters.save_character(chapter, character_tag, character)

    # the version of htmx.voice_blender() that only includes the selected gender
    return htmx.voice_blender(
        chapter,
        character,
        character_tag,
    )


@bp.route("<character_tag>/regenerate")
def regenerate_headshot(author, title, chapter_number, language, character_tag):
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)

    characters.draw_headshot(chapter, character_tag, force=True)

    all_characters = characters.get_all_characters(chapter)
    character_dict = all_characters[character_tag]

    character_dict["headshot"] = (
        f"/{chapter.url}/characters/{character_tag}/headshot.png"
    )

    characters.save_character(chapter, character_tag, character_dict)

    return htmx.headshot(character_dict, chapter, character_tag), 200


# R.%20M.%20Kane/Nessie%20vs%20Navy/characters/Nessie vs Navy/characters/Jim.png
@bp.route("<character_tag>/headshot.png")
def headshot(author, title, chapter_number, language, character_tag):
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)

    return send_from_directory(
        os.path.join(const.LIBRARY_DIR, chapter.chapterdir, "characters"),
        f"{character_tag}.png",
    )
